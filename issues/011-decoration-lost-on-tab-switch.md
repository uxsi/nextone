# 切换标签页后 suggestion 高亮丢失

严重度：中

## 现象

在文件 A 上触发 suggestion（红绿高亮出现），切换到文件 B 的标签页，再切回文件 A 时高亮消失。suggestion 在服务端仍然存活（没有 cancel 或 reject 事件），但用户看不到它，无法 accept 或 reject。

## 根因

VS Code 对不可见标签页的 `TextEditor` 实例会回收。切换到文件 B 后，文件 A 的 `TextEditor` 实例被销毁，其上的 decoration 随之丢失。切回文件 A 时 VS Code 创建新的 `TextEditor` 实例，但之前通过 `setDecorations` 设置的高亮不会自动恢复到新实例上。

扩展没有监听 `onDidChangeActiveTextEditor` 事件，无法在用户切回时重新渲染 decoration。

服务端日志可以确认这一点——suggestion trigger 之后，直到文件关闭前没有任何 cancel/reject 事件：

```
11:15:57,066 [metrics] {"event":"trigger","id":"suggest-a3d23a94",...}
11:16:00,522 Opened file:///Users/xushengni/tmp/test2.py          ← 切到了另一个文件
                                                                    ← 中间无 cancel/reject
11:22:50,678 [metrics] {"event":"cancel","id":"suggest-a3d23a94",...}  ← 关闭文件时才 cancel
```

## 修复

三处改动：

1. 抽取 `renderSuggestionDecorations()` 函数，将 decoration 渲染逻辑从 `handleSuggestion()` 中独立出来：

```typescript
function renderSuggestionDecorations(
  editor: vscode.TextEditor,
  params: SuggestParams,
): void {
  const deletionRanges = params.deletedLines.map(
    (l) => new vscode.Range(l.num - 1, 0, l.num - 1, Number.MAX_SAFE_INTEGER),
  );
  editor.setDecorations(deletionDecorType, deletionRanges);

  const additionRanges = params.addedLines.map(
    (l) => new vscode.Range(l.num - 1, 0, l.num - 1, Number.MAX_SAFE_INTEGER),
  );
  editor.setDecorations(additionDecorType, additionRanges);
}
```

2. 注册 `onDidChangeActiveTextEditor` 监听，切回 suggestion 所在文件时重新渲染：

```typescript
vscode.window.onDidChangeActiveTextEditor((editor) => {
  if (
    editor &&
    currentSuggestion &&
    editor.document.uri.toString() === currentSuggestion.uri
  ) {
    suggestionEditor = editor;
    renderSuggestionDecorations(editor, currentSuggestion);
  }
});
```

3. 新增 `suggestionEditor` 变量追踪渲染目标 editor，`clearSuggestion()` 和 `acceptSuggestion()` 使用该引用而非 `activeTextEditor`，并在 accept 时校验 uri 一致性。
