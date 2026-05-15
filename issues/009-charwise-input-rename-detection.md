# 逐字输入新名称时无法检测 rename

严重度：高

## 现象

用户选中 `hello` 删除后逐字输入 `good`，等待后无建议出现。服务端日志显示 pipeline 执行了但 "no location prediction"。

## 根因

真实编辑行为是"删除旧名 + 逐字输入新名"的多步操作。用户在 VS Code 中选中 `hello` 后输入 `good`，VS Code 发送 N+1 个 didChange 事件：

```
didChange v=3: range=(0,4)-(0,9) text=''      → old=["def hello(name):"] new=["def (name):"]
didChange v=4: range=(0,4)-(0,4) text='g'     → old=["def (name):"]      new=["def g(name):"]
didChange v=5: range=(0,5)-(0,5) text='o'     → old=["def g(name):"]     new=["def go(name):"]
didChange v=6: range=(0,6)-(0,6) text='o'     → old=["def go(name):"]    new=["def goo(name):"]
didChange v=7: range=(0,7)-(0,7) text='d'     → old=["def goo(name):"]   new=["def good(name):"]
```

debounce 300ms 后进入 pipeline 的是最后一个 edit（v=7）。`detect_rename` 对比 `["def goo(name):"]` 和 `["def good(name):"]`：

```python
old_ids = {def, goo, name}
new_ids = {def, good, name}
removed = {goo}
added = {good}
```

`goo` 不是一个在文件其他位置出现的标识符，`find_references("...", "python", "goo")` 返回空列表，没有需要修改的调用点。

## 修复

在 `location/engine.py` 的 `LocationEngine` 中新增 `_try_rename_from_history()` 方法，将编辑历史窗口中第一条 edit 的 `old_lines` 与最后一条的 `new_lines` 做合成对比：

```python
def predict(self, edit, source_code, language, edit_history=None):
    predictions = []

    # Rule 1: 单次 edit 的 rename 检测（原有逻辑）
    rename_pred = self._try_rename(edit, source_code, language)
    if rename_pred:
        predictions.append(rename_pred)

    # Rule 1b: 跨历史窗口的合成 rename 检测（新增）
    if not rename_pred and edit_history and len(edit_history) >= 2:
        composite_pred = self._try_rename_from_history(
            edit_history, source_code, language
        )
        if composite_pred:
            predictions.append(composite_pred)

    # ... 后续规则 ...
```

`_try_rename_from_history` 的核心逻辑：

```python
def _try_rename_from_history(self, edit_history, source_code, language):
    first = edit_history[0]
    last = edit_history[-1]

    # 所有 edit 必须在同一行
    if first.start_line != last.start_line:
        return None
    if len(first.old_lines) != 1 or len(last.new_lines) != 1:
        return None

    # 合成一个"虚拟 edit"：第一条的旧行 vs 最后一条的新行
    composite = EditRecord(
        uri=last.uri,
        version=last.version,
        timestamp=last.timestamp,
        old_lines=first.old_lines,   # ["def hello(name):"]
        new_lines=last.new_lines,    # ["def good(name):"]
        start_line=first.start_line,
        end_line=first.end_line,
    )

    detection = detect_rename(composite)
    if detection is None:
        return None

    # detection.old_name = "hello", detection.new_name = "good"
    refs = find_references(source_code, language, detection.old_name, ...)
    if not refs:
        return None

    return LocationPrediction(
        line=refs[0].line,
        rule=RuleType.RENAME,
        confidence=0.85,
        context={"old_name": detection.old_name, "new_name": detection.new_name, ...},
        ...
    )
```

同时更新 `pipeline.py`，在调用 `predict()` 时传入编辑历史：

```python
# 修复前
prediction = self._location_engine.predict(
    edit=latest_edit,
    source_code=doc.text,
    language=doc.language_id,
)

# 修复后
prediction = self._location_engine.predict(
    edit=latest_edit,
    source_code=doc.text,
    language=doc.language_id,
    edit_history=self._edit_history.get(uri),
)
```
