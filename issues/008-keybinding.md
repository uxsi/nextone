# Cmd+; 快捷键适配

## 核心问题

终端模拟器（iTerm2、Alacritty、kitty 等）和终端内运行的 Vim/Neovim 之间只传输 ANSI 转义序列。`Cmd` 是 macOS 窗口系统层的修饰键，不在 ANSI 序列中编码。**终端 Vim/Neovim 根本收不到 `<D-;>`。**

必须区分 GUI 和终端两种运行环境，分别处理。

## 各编辑器的配置方案

### VS Code

直接在 `keybindings.json` 中配置，无终端问题：

```jsonc
[
    {
        "key": "cmd+;",
        "command": "nextEdit.accept",
        "when": "editorTextFocus && nextEdit.hasSuggestion"
    },
    {
        "key": "cmd+shift+;",
        "command": "nextEdit.reject",
        "when": "editorTextFocus && nextEdit.hasSuggestion"
    }
]
```

### Neovim GUI 客户端（Neovide / VimR）

直接映射 `<D-;>`：

```lua
vim.keymap.set({ "n", "i" }, "<D-;>", function()
    if current_suggestion then
        apply_suggestion(current_suggestion)
        vim.lsp.buf_notify(0, "nextEdit/resolve", {
            id = current_suggestion.id, accepted = true,
        })
        clear_suggestion()
    end
end, { desc = "Accept Next Edit suggestion" })

vim.keymap.set({ "n", "i" }, "<D-S-;>", function()
    clear_suggestion()
end, { desc = "Reject Next Edit suggestion" })
```

### Neovim 终端模式

需要终端模拟器配合，把 Cmd+; 转换为自定义 CSI u 转义序列 `\x1b[59;9u`（59 = semicolon ASCII, 9 = Super modifier），Neovim 再解析这个序列。

**步骤 1：Neovim 注册转义序列**

```lua
vim.keymap.set({ "n", "i" }, "<CSI>59;9u", function()
    if current_suggestion then
        apply_suggestion(current_suggestion)
        vim.lsp.buf_notify(0, "nextEdit/resolve", {
            id = current_suggestion.id, accepted = true,
        })
        clear_suggestion()
    end
end, { desc = "Accept Next Edit suggestion (terminal Cmd+;)" })
```

**步骤 2：配置终端模拟器发送转义序列**

iTerm2：
```
Preferences → Keys → Key Bindings → 添加：
  Keyboard Shortcut: ⌘;
  Action: Send Escape Sequence
  Esc+: [59;9u
```

kitty（~/.config/kitty/kitty.conf）：
```
map cmd+semicolon send_text all \x1b[59;9u
```

Alacritty（~/.config/alacritty/alacritty.toml）：
```toml
[[keyboard.bindings]]
key = "Semicolon"
mods = "Command"
chars = "[59;9u"
```

WezTerm（~/.config/wezterm/wezterm.lua）：
```lua
config.keys = {
    { key = ";", mods = "CMD", action = wezterm.action.SendString("\x1b[59;9u") },
}
```

### Vim GUI（MacVim / gVim）

直接映射 `<D-;>`：

```vim
nnoremap <D-;> :call <SID>AcceptSuggestion()<CR>
inoremap <D-;> <C-o>:call <SID>AcceptSuggestion()<CR>
nnoremap <D-S-;> :call <SID>ClearSuggestion()<CR>
inoremap <D-S-;> <C-o>:call <SID>ClearSuggestion()<CR>
```

### Vim 终端模式

将终端转发的转义序列映射到功能键码（Vim 没有 `<CSI>` 映射语法，借用不常用的 `<F37>`）：

```vim
set <F37>=[59;9u
nnoremap <F37> :call <SID>AcceptSuggestion()<CR>
inoremap <F37> <C-o>:call <SID>AcceptSuggestion()<CR>
```

终端模拟器的配置与 Neovim 终端模式相同。

## 插件自动检测逻辑

插件初始化时自动检测 GUI vs 终端环境，注册对应的 keymap：

**Neovim：**

```lua
local function setup_keybindings()
    if vim.g.neovide or vim.fn.has("gui_running") == 1 then
        -- GUI 客户端：直接绑定 <D-;>
        vim.keymap.set({ "n", "i" }, "<D-;>", accept_suggestion)
    else
        -- 终端：绑定 CSI u 转义序列（需终端配合）
        vim.keymap.set({ "n", "i" }, "<CSI>59;9u", accept_suggestion)
        -- 纯终端 fallback（不依赖终端配置）
        vim.keymap.set({ "n", "i" }, "<C-;>", accept_suggestion)
    end
end
```

**Vim：**

```vim
if has('gui_running')
    nnoremap <D-;> :call <SID>AcceptSuggestion()<CR>
    inoremap <D-;> <C-o>:call <SID>AcceptSuggestion()<CR>
else
    set <F37>=[59;9u
    nnoremap <F37> :call <SID>AcceptSuggestion()<CR>
    inoremap <F37> <C-o>:call <SID>AcceptSuggestion()<CR>
    " fallback
    nnoremap <C-;> :call <SID>AcceptSuggestion()<CR>
    inoremap <C-;> <C-o>:call <SID>AcceptSuggestion()<CR>
endif
```

## Fallback 策略

`<C-;>`（Ctrl+;）作为终端环境的 fallback——大部分现代终端模拟器（kitty、WezTerm）在 CSI u 模式下支持 Ctrl+;，无需额外配置。传统终端（macOS Terminal.app）不支持，只能依赖上述转义序列映射方案。

## Zed

Zed 的快捷键通过 `~/.config/zed/keymap.json` 配置：

```jsonc
[
    {
        "context": "Editor && next_edit_suggestion",
        "bindings": {
            "cmd-;": "next_edit::Accept",
            "cmd-shift-;": "next_edit::Reject"
        }
    }
]
```

Zed 是 GUI 应用，没有终端 Cmd 键传递的问题。
