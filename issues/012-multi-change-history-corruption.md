# 多 contentChanges 事件导致 edit history 失真

严重度：高

前序 issue：[007-multi-content-changes-silent-drop](./007-multi-content-changes-silent-drop.md)

## 现象

多光标编辑、批量替换、格式化、snippet 展开等操作在一次 `didChange` 中产生多个 `contentChanges`。之后 suggestion 可能给出与实际编辑无关的建议，或把不相关的修改误判为 rename。

## 根因

007 发现了"只处理第一个 change"的问题并加了日志，但修复不彻底。实际的数据不一致是：

- `document_store.apply_changes()` 应用了全部 changes，文档状态正确。
- edit history 只记录了第一个 change 的 `old_lines/new_lines/start_line/end_line`。

后续 location engine 和 generation 基于这份失真的 history 工作。例如用户做了多光标替换，history 只记录了第一个光标的变更，pipeline 可能把它当成 rename 来处理。

```
文档状态:  所有 changes 都已应用 ✓
edit history: 只有 changes[0] 的快照 ✗
pipeline 输入: 基于 changes[0] 的快照做预测 → 可能产生错误 suggestion
```

## 修复

将 007 的"加日志"升级为完整的 short-circuit。检测到 `len(p.changes) != 1` 时，只同步文档状态，跳过 pipeline：

```python
# 修复前（007 的状态）
if p.changes:
    if len(p.changes) > 1:
        logger.debug(
            "didChange has %d changes, only processing the first one",
            len(p.changes),
        )
    first_change = p.changes[0]
    # ... 记录快照，触发 pipeline

doc = self.document_store.apply_changes(p.uri, p.version, p.changes)

# 修复后
if len(p.changes) != 1:
    logger.info(
        "didChange has %d changes, syncing document only (skipping pipeline)",
        len(p.changes),
    )
    self.document_store.apply_changes(p.uri, p.version, p.changes)
    return

first_change = p.changes[0]
# ... 记录快照，触发 pipeline（只在单 change 时执行）
```

同时简化了后续代码，去掉不再需要的 `if p.changes:` 守卫，因为走到该分支时一定有 exactly 1 change。

新增测试 `test_server_multi_change_skips_pipeline` 和 `test_server_single_change_triggers_pipeline` 验证行为。
