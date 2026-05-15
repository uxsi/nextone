# 通信协议设计

## 为什么不直接用 LSP

LSP 的 `textDocument/completion` 是 client-initiated 的请求-响应模式：客户端请求补全，服务端返回结果。Next Edit 的核心交互是 **服务端主动推送建议**，LSP 虽然有 server→client 的 notification 机制，但 completion 相关的协议都是 client-initiated 的。

## 方案：复用 LSP 传输层 + 自定义 method

采用 JSON-RPC over stdio（与 LSP 相同的传输层），自定义 method names。好处：

- VS Code 的 `LanguageClient`、Neovim 的 `vim.lsp.start`、Vim 的 `channel` 都原生支持 stdio JSON-RPC
- 编辑器插件只需在标准通信流程上注册几个自定义 handler
- 后续可以升级为标准 LSP 扩展提案

## 设计原则

参考 LSP 的状态同步机制，协议必须满足以下约束：

1. **版本化**：每个文档变更携带递增版本号，服务端基于特定版本生成建议，客户端据此判断建议是否过期。
2. **生命周期完整**：覆盖文档打开、变更、保存、关闭的完整生命周期，服务端能准确维护每个文档的最新状态。
3. **失效可控**：定义建议的失效条件和取消机制，避免过期建议残留在编辑器中。
4. **可恢复**：提供全量同步机制，当增量同步出现漂移时能通过全量快照修复。

## 协议定义

### nextEdit/didOpen（Editor → Server）

编辑器打开文档时发送，传递文档全文内容和初始版本号。服务端据此建立文档状态。

```jsonc
{
  "jsonrpc": "2.0",
  "method": "nextEdit/didOpen",
  "params": {
    "uri": "file:///path/to/file.ts",
    "languageId": "typescript",
    "version": 1,
    "text": "function hello(name: string) {\n  console.log(name)\n}\n"
  }
}
```

### nextEdit/didChange（Editor → Server）

编辑器报告编辑事件。`version` 每次编辑递增，服务端据此判断自身状态是否最新。

```jsonc
{
  "jsonrpc": "2.0",
  "method": "nextEdit/didChange",
  "params": {
    "uri": "file:///path/to/file.ts",
    "version": 5,
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

### nextEdit/didSave（Editor → Server）

文档保存时发送。服务端可以用此时机做全量校验。

```jsonc
{
  "jsonrpc": "2.0",
  "method": "nextEdit/didSave",
  "params": {
    "uri": "file:///path/to/file.ts",
    "version": 5
  }
}
```

### nextEdit/didClose（Editor → Server）

文档关闭时发送。服务端释放该文档的状态和编辑历史。

```jsonc
{
  "jsonrpc": "2.0",
  "method": "nextEdit/didClose",
  "params": {
    "uri": "file:///path/to/file.ts"
  }
}
```

### nextEdit/fullSync（Editor → Server）

全量同步文档内容。用于两个场景：（1）插件启动时对已打开文档做初始同步；（2）检测到增量漂移时做修复。

```jsonc
{
  "jsonrpc": "2.0",
  "method": "nextEdit/fullSync",
  "params": {
    "uri": "file:///path/to/file.ts",
    "version": 12,
    "text": "... 完整文件内容 ..."
  }
}
```

### nextEdit/suggest（Server → Editor）

服务端主动推送编辑建议（notification，无需客户端请求）。`baseUri` 和 `baseVersion` 标识该建议基于哪个文档的哪个版本生成——客户端收到后，如果当前文档版本已超过 `baseVersion`，直接丢弃。

```jsonc
{
  "jsonrpc": "2.0",
  "method": "nextEdit/suggest",
  "params": {
    "id": "suggest-001",
    "uri": "file:///path/to/file.ts",
    "baseUri": "file:///path/to/file.ts",
    "baseVersion": 5,
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

### nextEdit/cancelSuggestion（Server → Editor）

服务端主动取消已推送的建议。触发条件：新的 `didChange` 到达后旧建议自动作废，或服务端检测到建议已过期。

```jsonc
{
  "jsonrpc": "2.0",
  "method": "nextEdit/cancelSuggestion",
  "params": {
    "id": "suggest-001",
    "reason": "document_changed"
  }
}
```

`reason` 枚举值：`document_changed` | `superseded` | `timeout`

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
4. 编辑器为每个已打开文档发送 `nextEdit/didOpen`（含全文内容）
5. 编辑器持续发送 `nextEdit/didChange` 事件（每次携带递增 version）
6. 服务端在检测到可预测的编辑模式后发送 `nextEdit/suggest`
7. 编辑器关闭文档时发送 `nextEdit/didClose`
8. 编辑器退出时 kill 子进程

## 建议失效规则

客户端收到 `nextEdit/suggest` 后，在以下任一条件满足时自动丢弃该建议，无需等待服务端 `cancelSuggestion`：

1. 当前文档的 version 已超过建议的 `baseVersion`（用户继续编辑了）
2. 用户切换到了其他文件
3. 用户执行了撤销操作（undo）
4. 建议渲染超过 30 秒未被响应

服务端在以下条件下主动发送 `cancelSuggestion`：

1. 收到新的 `didChange`，旧的推理结果作废
2. 新的建议生成完毕，旧建议被取代（`reason: "superseded"`）

## 消息总览

| Method | 方向 | 类型 | 用途 |
|--------|------|------|------|
| `nextEdit/didOpen` | Editor → Server | notification | 文档打开，传全文 + 版本号 |
| `nextEdit/didChange` | Editor → Server | notification | 增量编辑事件 |
| `nextEdit/didSave` | Editor → Server | notification | 文档保存 |
| `nextEdit/didClose` | Editor → Server | notification | 文档关闭 |
| `nextEdit/fullSync` | Editor → Server | notification | 全量同步（初始化/漂移修复） |
| `nextEdit/suggest` | Server → Editor | notification | 推送编辑建议 |
| `nextEdit/cancelSuggestion` | Server → Editor | notification | 取消已推送的建议 |
| `nextEdit/resolve` | Editor → Server | notification | 用户接受/拒绝建议 |
| `nextEdit/status` | Server → Editor | notification | 服务端状态变更 |
