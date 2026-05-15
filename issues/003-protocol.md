# 通信协议设计

## 为什么不直接用 LSP

LSP 的 `textDocument/completion` 是 client-initiated 的请求-响应模式：客户端请求补全，服务端返回结果。Next Edit 的核心交互是 **服务端主动推送建议**，LSP 虽然有 server→client 的 notification 机制，但 completion 相关的协议都是 client-initiated 的。

## 方案：复用 LSP 传输层 + 自定义 method

采用 JSON-RPC over stdio（与 LSP 相同的传输层），自定义 method names。好处：

- VS Code 的 `LanguageClient`、Neovim 的 `vim.lsp.start`、Vim 的 `channel` 都原生支持 stdio JSON-RPC
- 编辑器插件只需在标准通信流程上注册几个自定义 handler
- 后续可以升级为标准 LSP 扩展提案

## 协议定义

### nextEdit/didChange（Editor → Server）

编辑器报告编辑事件。

```jsonc
{
  "jsonrpc": "2.0",
  "method": "nextEdit/didChange",
  "params": {
    "uri": "file:///path/to/file.ts",
    "changes": [
      {
        "range": {
          "start": { "line": 10, "character": 0 },
          "end": { "line": 10, "character": 25 }
        },
        "text": "function goodbye(name: string) {"
      }
    ],
    "timestamp": 1715760000000
  }
}
```

### nextEdit/suggest（Server → Editor）

服务端主动推送编辑建议（notification，无需客户端请求）。

```jsonc
{
  "jsonrpc": "2.0",
  "method": "nextEdit/suggest",
  "params": {
    "id": "suggest-001",
    "uri": "file:///path/to/file.ts",
    "location": { "line": 35, "character": 0 },
    "diff": "35-| hello(user.name)\n35+| goodbye(user.name)",
    "description": "Update function call to match renamed function",
    "deleted_lines": [
      { "num": 35, "text": "  hello(user.name)" }
    ],
    "added_lines": [
      { "num": 35, "text": "  goodbye(user.name)" }
    ]
  }
}
```

### nextEdit/resolve（Editor → Server）

用户接受或拒绝建议。

```jsonc
{
  "jsonrpc": "2.0",
  "method": "nextEdit/resolve",
  "params": {
    "id": "suggest-001",
    "accepted": true
  }
}
```

### nextEdit/status（Server → Editor）

服务端状态通知（模型加载中、推理中、就绪等）。

```jsonc
{
  "jsonrpc": "2.0",
  "method": "nextEdit/status",
  "params": {
    "state": "inferring",
    "message": "Analyzing edit history..."
  }
}
```

`state` 枚举值：`ready` | `loading_model` | `inferring` | `error`

## 连接生命周期

1. 编辑器启动时 spawn `next-edit-server --stdio` 子进程
2. 服务端发送 `nextEdit/status { state: "loading_model" }`
3. 模型加载完毕后发送 `nextEdit/status { state: "ready" }`
4. 编辑器持续发送 `nextEdit/didChange` 事件
5. 服务端在检测到可预测的编辑模式后发送 `nextEdit/suggest`
6. 编辑器关闭时 kill 子进程
