# composite rename 检测缺少中间编辑一致性校验

严重度：低

前序 issue：[009-charwise-input-rename-detection](./009-charwise-input-rename-detection.md)

## 现象

用户在函数定义行上连续做几次不同修改（非 rename 意图），可能收到错误的 rename suggestion。

## 根因

009 引入的 `_try_rename_from_history()` 只校验历史窗口首尾：

```python
# 009 的实现
first = edit_history[0]
last = edit_history[-1]
if first.start_line != last.start_line:
    return None
if len(first.old_lines) != 1 or len(last.new_lines) != 1:
    return None
```

没有验证中间的 edit 是否与首尾属于同一次编辑序列。几个薄弱点：

- 没有验证所有 edit 都在同一个 `uri`。如果用户快速在两个文件间切换编辑，不同文件的 edit 可能被拼在一起。
- 没有验证中间 edit 也在同一 `start_line`。
- 没有验证中间 edit 也是单行变更。

后续的 `detect_rename()` + `find_references()` 两道验证能过滤掉大部分误报，加上窗口大小只有 3，实际误报率不高。但缺少基本的结构一致性校验属于实现遗漏。

## 修复

遍历全部 edit 验证结构一致性——同 uri、同 start_line、全部单行：

```python
# 修复后
first = edit_history[0]
last = edit_history[-1]

for edit in edit_history:
    if edit.uri != first.uri:
        return None
    if edit.start_line != first.start_line:
        return None
    if len(edit.old_lines) != 1 or len(edit.new_lines) != 1:
        return None
```

没有添加严格的逐条连续性校验（`edit[i].new_lines == edit[i+1].old_lines`），因为滑动窗口大小为 3，用户输入超过窗口容量时中间编辑会被淘汰，保留的记录之间天然不连续。

新增测试 `test_engine_composite_rename_rejects_different_uri` 和 `test_engine_composite_rename_rejects_different_lines` 验证。
