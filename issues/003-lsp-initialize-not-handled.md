# 服务端不处理 LSP initialize 请求

严重度：高

## 现象

Extension 启动后状态栏一直显示转圈图标（`$(loading~spin) NextOne`），LanguageClient 卡在握手阶段，不发送任何 `textDocument/` 消息。

## 根因

`vscode-languageclient` 的 LanguageClient 连接服务端后，第一步发送标准 LSP `initialize` request（带 `id`，期待 response）。收到 response 后才发送 `initialized` notification，之后才进入正常工作状态。

服务端的 `_dispatch` 只注册了自定义 `nextEdit/` method handler，将 `initialize` 当作未知 method 丢弃：

```python
# 修复前
def _dispatch(self, msg: JsonRpcMessage) -> None:
    handlers: dict[str, Callable[[dict[str, Any]], None]] = {
        Methods.DID_OPEN: self._handle_did_open,
        Methods.DID_CHANGE: self._handle_did_change,
        # ... 只有 nextEdit/ 前缀的 method
    }

    if method and method in handlers:
        handlers[method](msg.params or {})
    elif method:
        logger.warning("Unknown method: %s", method)  # initialize 走到这里被丢弃
```

LanguageClient 永远收不到 `initialize` response，一直阻塞。

## 修复

涉及服务端和 Extension 两端的改动。

### 服务端（server.py）

在 `_dispatch` 开头新增 LSP 生命周期消息处理：

```python
# 修复后
def _dispatch(self, msg: JsonRpcMessage) -> None:
    if msg.method == "initialize" and msg.is_request:
        self._handle_initialize(msg)
        return
    if msg.method == "initialized":
        logger.info("Client initialized")
        return
    if msg.method == "shutdown" and msg.is_request:
        self._handle_shutdown(msg)
        return
    if msg.method == "exit":
        self._running = False
        return
    # ... 后续 handler 路由
```

`_handle_initialize` 返回 `InitializeResult`，声明 `textDocumentSync` 能力：

```python
def _handle_initialize(self, msg: JsonRpcMessage) -> None:
    result = {
        "capabilities": {
            "textDocumentSync": {
                "openClose": True,
                "change": 2,  # Incremental
                "save": {"includeText": False},
            },
        },
    }
    self._write_message({
        "jsonrpc": "2.0",
        "id": msg.id,
        "result": result,
    })
```

新增标准 LSP `textDocument/` 消息到自定义格式的参数翻译层：

```python
lsp_to_custom: dict[str, str] = {
    "textDocument/didOpen": Methods.DID_OPEN,
    "textDocument/didChange": Methods.DID_CHANGE,
    "textDocument/didSave": Methods.DID_SAVE,
    "textDocument/didClose": Methods.DID_CLOSE,
}

method = msg.method
if method and method in lsp_to_custom:
    params = self._translate_lsp_params(method, msg.params or {})
    handlers[lsp_to_custom[method]](params)
    return
```

`_translate_lsp_params` 将 LSP 嵌套结构展平为自定义格式：

```python
@staticmethod
def _translate_lsp_params(method: str, params: dict[str, Any]) -> dict[str, Any]:
    text_doc = params.get("textDocument", {})

    if method == "textDocument/didOpen":
        return {
            "uri": text_doc.get("uri", ""),
            "languageId": text_doc.get("languageId", ""),
            "version": text_doc.get("version", 1),
            "text": text_doc.get("text", ""),
        }

    if method == "textDocument/didChange":
        return {
            "uri": text_doc.get("uri", ""),
            "version": text_doc.get("version", 0),
            "changes": params.get("contentChanges", []),
            "timestamp": int(time.time() * 1000),
        }
    # ... didSave, didClose 类似
```

### Extension（extension.ts）

移除手动发送自定义 notification 的代码。修复前 Extension 手动监听 VS Code 事件并发送 `nextEdit/didOpen` 等消息：

```typescript
// 修复前 — 手动转发每个事件
vscode.workspace.onDidOpenTextDocument((doc) => {
    client.sendNotification("nextEdit/didOpen", {
        uri: doc.uri.toString(),
        languageId: doc.languageId,
        version: doc.version,
        text: doc.getText(),
    });
});

vscode.workspace.onDidChangeTextDocument((event) => {
    client.sendNotification("nextEdit/didChange", { ... });
});

// didSave, didClose 类似...

// 启动后还要为已打开文档补发 didOpen
for (const doc of vscode.workspace.textDocuments) {
    client.sendNotification("nextEdit/didOpen", { ... });
}
```

修复后全部删除，由 LanguageClient 根据 `documentSelector` 和服务端声明的 `textDocumentSync` 能力自动处理：

```typescript
// 修复后 — LanguageClient 自动同步，不需要手动转发
client.start();
// LanguageClient automatically sends textDocument/didOpen, didChange,
// didSave, didClose for files matching documentSelector.
```
