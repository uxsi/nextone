# pipeline.initialize() 在 LSP 握手前执行

严重度：中

## 现象

LanguageClient 可能丢弃 pre-initialize notification 或产生协议错误。实际表现不确定，取决于 LanguageClient 实现的容错程度。

## 根因

`run()` 方法在进入消息循环前直接调用 `pipeline.initialize()`，后者会发送 `loading_model` → `ready` 两条 status notification。此时 LanguageClient 还没发 `initialize` 请求，服务端就已经主动推送了消息。

LSP 规范明确要求："Before the initialize request, the server must not send any request or notification to the client."

```python
# 修复前
def run(self, stdin=None, stdout=None) -> None:
    self._stdin = stdin or sys.stdin.buffer
    self._stdout = stdout or sys.stdout.buffer
    self._running = True

    self._pipeline = Pipeline(
        document_store=self.document_store,
        send_notification=self.send_notification,
        model_path=self._model_path,
    )
    self._pipeline.initialize()  # ← 此时 initialize 握手还没开始，就发了 notification

    while self._running:
        msg = self._read_message()
        ...
```

## 修复

将 `pipeline.initialize()` 延迟到 `_handle_initialize()` 返回 response 之后：

```python
# 修复后
def run(self, stdin=None, stdout=None) -> None:
    self._stdin = stdin or sys.stdin.buffer
    self._stdout = stdout or sys.stdout.buffer
    self._running = True

    # Create the pipeline but do NOT initialize yet.
    # pipeline.initialize() sends status notifications, which are
    # forbidden before the LSP initialize handshake completes.
    self._pipeline = Pipeline(
        document_store=self.document_store,
        send_notification=self.send_notification,
        model_path=self._model_path,
    )
    # 不调用 initialize()

    while self._running:
        msg = self._read_message()
        ...

def _handle_initialize(self, msg: JsonRpcMessage) -> None:
    # ... 返回 InitializeResult response ...
    self._write_message({"jsonrpc": "2.0", "id": msg.id, "result": result})

    # Now that the handshake is done, initialize the pipeline.
    # This sends loading_model → ready status notifications to the client.
    if self._pipeline:
        self._pipeline.initialize()
```
