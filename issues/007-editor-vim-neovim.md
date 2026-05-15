# 编辑器插件适配：Vim / Neovim

## Vim 与 Neovim 的能力差异

| 能力 | Neovim | Vim 8.0+ / Vim 9.0+ |
|------|--------|---------------------|
| 外部进程通信 | `vim.lsp.start`（原生 LSP 客户端） | `channel` + `job`（JSON 通信） |
| Inline diff 渲染 | `nvim_buf_set_extmark` + `virt_lines` | `textprop`(9.0+) 或 `popup_create`(8.2+) |
| 高亮标记 | extmark namespace，生命周期自动管理 | `prop_type_add` + `prop_add`，需手动清理 |
| Cmd 键绑定 | GUI 客户端支持 `<D-;>` | GUI 客户端支持 `<D-;>` |

渲染效果差异：Neovim 的 `virt_lines` 是真正的行间插入（新增行与原始代码无缝衔接），Vim 的 `popup_create` 是浮动窗口覆盖在编辑器上方（视觉上有图层分离感）。

## Neovim 插件实现

```lua
local ns = vim.api.nvim_create_namespace("next-edit")
local current_suggestion = nil

-- 1. 启动 next-edit-server
local client_id = vim.lsp.start({
    name = "next-edit",
    cmd = { "next-edit-server", "--stdio" },
    handlers = {
        -- 2. 接收建议
        ["nextEdit/suggest"] = function(err, result, ctx)
            clear_suggestion()
            current_suggestion = result

            local bufnr = vim.uri_to_bufnr(result.uri)

            -- 删除行：红色背景 extmark
            for _, line_info in ipairs(result.deleted_lines) do
                vim.api.nvim_buf_set_extmark(bufnr, ns, line_info.num - 1, 0, {
                    line_hl_group = "NextEditDeletion",
                })
            end

            -- 新增行：virt_lines（直接嵌入编辑器行间）
            local virt = {}
            for _, line_info in ipairs(result.added_lines) do
                table.insert(virt, { { line_info.text, "NextEditAddition" } })
            end

            local anchor_line = result.location.line - 1
            vim.api.nvim_buf_set_extmark(bufnr, ns, anchor_line, 0, {
                virt_lines = virt,
                virt_lines_above = false,
            })
        end,
    },
})

-- accept / reject 逻辑
local function accept_suggestion()
    if not current_suggestion then return end
    apply_suggestion(current_suggestion)
    vim.lsp.buf_notify(0, "nextEdit/resolve", {
        id = current_suggestion.id, accepted = true,
    })
    clear_suggestion()
end

function clear_suggestion()
    local bufnr = vim.api.nvim_get_current_buf()
    vim.api.nvim_buf_clear_namespace(bufnr, ns, 0, -1)
    current_suggestion = nil
end
```

## Vim 插件实现

```vim
let s:job = v:null
let s:channel = v:null
let s:current_suggestion = v:null
let s:popup_id = 0

" ---- 初始化 ----

" 定义高亮组
highlight NextEditDeletion guibg=#3c1f1f ctermbg=52
highlight NextEditAddition guibg=#1f3c1f ctermbg=22

" 定义 textprop 类型
if has('textprop')
    call prop_type_add('NextEditDeletion', {'highlight': 'NextEditDeletion'})
endif

" 1. 启动 next-edit-server 进程
function! s:Start() abort
    let s:job = job_start(['next-edit-server', '--stdio'], {
        \ 'in_mode': 'lsp',
        \ 'out_mode': 'lsp',
        \ 'out_cb': function('s:OnServerMessage'),
        \ 'err_cb': function('s:OnServerError'),
        \ })
    let s:channel = job_getchannel(s:job)
endfunction

" 2. 监听编辑事件
autocmd TextChanged,TextChangedI * call s:SendDidChange()

function! s:SendDidChange() abort
    if s:channel is v:null | return | endif
    let l:payload = {
        \ 'jsonrpc': '2.0',
        \ 'method': 'nextEdit/didChange',
        \ 'params': {
        \     'uri': 'file://' . expand('%:p'),
        \     'timestamp': localtime() * 1000,
        \     'changes': s:GetBufferChanges(),
        \ }
        \ }
    call ch_sendexpr(s:channel, l:payload)
endfunction

" 3. 接收建议
function! s:OnServerMessage(channel, msg) abort
    let l:data = json_decode(a:msg)
    if l:data.method ==# 'nextEdit/suggest'
        call s:RenderSuggestion(l:data.params)
    endif
endfunction

function! s:OnServerError(channel, msg) abort
    echohl ErrorMsg | echom '[NextOne] ' . a:msg | echohl None
endfunction

" 4. 渲染建议
function! s:RenderSuggestion(params) abort
    call s:ClearSuggestion()
    let s:current_suggestion = a:params

    " 删除行：textprop 红色标记
    for line_info in a:params.deleted_lines
        if has('textprop')
            call prop_add(line_info.num, 1, {
                \ 'type': 'NextEditDeletion',
                \ 'end_lnum': line_info.num,
                \ 'end_col': col([line_info.num, '$']),
                \ })
        endif
    endfor

    " 新增行：popup 浮窗（Vim 没有 virt_lines）
    let l:added_text = map(copy(a:params.added_lines), 'v:val.text')
    let l:target_line = a:params.location.line
    let s:popup_id = popup_create(l:added_text, {
        \ 'line': l:target_line + 1,
        \ 'col': 1,
        \ 'pos': 'botleft',
        \ 'highlight': 'NextEditAddition',
        \ 'wrap': v:false,
        \ 'zindex': 50,
        \ })
endfunction

" 5. 清除建议
function! s:ClearSuggestion() abort
    if has('textprop')
        call prop_remove({'type': 'NextEditDeletion', 'all': v:true})
    endif
    if s:popup_id > 0
        call popup_close(s:popup_id)
        let s:popup_id = 0
    endif
    let s:current_suggestion = v:null
endfunction

" 6. 接受建议
function! s:AcceptSuggestion() abort
    if s:current_suggestion is v:null | return | endif
    call s:ApplyDiff(s:current_suggestion)
    let l:payload = {
        \ 'jsonrpc': '2.0',
        \ 'method': 'nextEdit/resolve',
        \ 'params': { 'id': s:current_suggestion.id, 'accepted': v:true }
        \ }
    call ch_sendexpr(s:channel, l:payload)
    call s:ClearSuggestion()
endfunction
```

## 快捷键配置

详见 [issue #008: Cmd+; 快捷键适配](./008-keybinding.md)。
