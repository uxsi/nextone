# LSP 路径下 didChange 的 timestamp 固定为 0

严重度：低

## 现象

指标日志中 edit 事件的时间戳为 0，无法用于延迟分析。

## 根因

标准 LSP `textDocument/didChange` 没有 timestamp 字段。`_translate_lsp_params` 翻译时硬编码了 `timestamp: 0`：

```python
# 修复前
if method == "textDocument/didChange":
    return {
        "uri": text_doc.get("uri", ""),
        "version": text_doc.get("version", 0),
        "changes": params.get("contentChanges", []),
        "timestamp": 0,  # ← 硬编码 0
    }
```

## 修复

用服务端当前时间补充：

```python
# 修复后
if method == "textDocument/didChange":
    return {
        "uri": text_doc.get("uri", ""),
        "version": text_doc.get("version", 0),
        "changes": params.get("contentChanges", []),
        "timestamp": int(time.time() * 1000),  # ← 毫秒时间戳
    }
```

服务端收到消息的时间与编辑器实际编辑时间之间有几毫秒的传输延迟，对于 p50/p95 级别的延迟分析影响可忽略。
