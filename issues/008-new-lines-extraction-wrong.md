# _handle_did_change 中 new_lines 提取错误

严重度：高

## 现象

`detect_rename` 始终返回 None，rename 场景从不触发建议。服务端日志显示 `old_lines` 和 `new_lines` 完全不对称。

## 根因

`new_lines_snapshot` 直接用 `change.text`（替换片段）做 `split("\n")`。

编辑器发送的 `change.text` 只是替换范围内的文本片段。将 `def hello(name):` 中的 `hello`（第 4-9 字符）替换为 `goodbye` 时，`change.text = "goodbye"`，不是完整行 `"def goodbye(name):"`。

```python
# 修复前
new_lines_snapshot: list[str] = []
if p.changes:
    first_change = p.changes[0]
    new_text = first_change.text        # "goodbye" — 只是替换片段
    new_lines_snapshot = new_text.split("\n") if new_text else []
    # new_lines_snapshot = ["goodbye"]
```

`detect_rename` 拿到 `old_lines=["def hello(name):"]` 和 `new_lines=["goodbye"]`，两者结构不对称（一个是完整行，一个只是片段），无法检测到 rename。

## 修复

在 `apply_changes` 之后从更新后的文档中按行读取：

```python
# 修复后
doc = self.document_store.apply_changes(p.uri, p.version, p.changes)
# ...

new_lines_snapshot: list[str] = []
if p.changes:
    first_change = p.changes[0]
    new_line_count = first_change.text.count("\n") + 1
    new_end = change_start + new_line_count
    new_lines_snapshot = doc.get_range(change_start, new_end)
    # new_lines_snapshot = ["def goodbye(name):"]
```

`doc.get_range(change_start, new_end)` 从已应用变更的文档中读取对应行，得到完整的行内容（保留了行的其余部分），与 `old_lines_snapshot` 结构对称。
