# 多个 contentChanges 时只处理第一个且无日志

严重度：低

## 现象

格式化、批量替换等操作在一次 `textDocument/didChange` 中发送多个 `contentChanges`，服务端只处理第一个，其余被静默忽略。开发者无从得知有数据丢失。

## 根因

`_handle_did_change` 只取 `p.changes[0]`，没有日志提示：

```python
# 修复前
if doc and p.changes:
    first_change = p.changes[0]
    change_start = first_change.range.start.line
    # ... 只处理 first_change
```

## 修复

添加 debug 级别日志：

```python
# 修复后
if p.changes:
    if len(p.changes) > 1:
        logger.debug(
            "didChange has %d changes, only processing the first one",
            len(p.changes),
        )
    first_change = p.changes[0]
    # ... 只处理 first_change
```

Phase 1 只处理第一个 change 是有意的简化（绝大多数编辑事件只有一个 change），但日志确保这个行为是可观测的，不被误认为"正常处理了所有 changes"。
