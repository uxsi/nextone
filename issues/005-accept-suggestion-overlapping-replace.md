# acceptSuggestion 对同一行做两次 editBuilder.replace

严重度：高

## 现象

用户按 Cmd+; 接受建议时，编辑不生效。VS Code 内部抛 "Overlapping ranges are not allowed!" 异常。

## 根因

`acceptSuggestion` 函数中有两个 for 循环对同一行的 range 各做一次 `editBuilder.replace`。VS Code 的 `TextEditorEdit` 禁止对重叠 range 做多次操作。

```typescript
// 修复前
editor.edit((editBuilder) => {
    // 第一个循环：用旧文本替换（无意义操作）
    for (const dl of suggestion.deletedLines) {
        const lineIdx = dl.num - 1;
        if (lineIdx >= 0 && lineIdx < editor.document.lineCount) {
            const line = editor.document.lineAt(lineIdx);
            editBuilder.replace(line.range, dl.text);  // ← 第一次 replace
        }
    }

    // 第二个循环：用新文本替换（真正的操作）
    for (let i = 0; i < suggestion.deletedLines.length; i++) {
        const dl = suggestion.deletedLines[i];
        const al = suggestion.addedLines[i];
        if (al) {
            const lineIdx = dl.num - 1;
            if (lineIdx >= 0 && lineIdx < editor.document.lineCount) {
                const line = editor.document.lineAt(lineIdx);
                editBuilder.replace(line.range, al.text);  // ← 第二次 replace，同一行
            }
        }
    }
});
```

两个循环对同一行（如第 4 行）各调了一次 `editBuilder.replace(line.range, ...)`，VS Code 拒绝执行。

## 修复

删掉第一个循环，用 Map 按行号去重，确保每行只做一次 replace：

```typescript
// 修复后
editor.edit((editBuilder) => {
    // Build a map: line number → new text
    const replacements = new Map<number, string>();
    for (let i = 0; i < suggestion.deletedLines.length; i++) {
        const dl = suggestion.deletedLines[i];
        const al = suggestion.addedLines[i];
        if (al) {
            replacements.set(dl.num, al.text);
        }
    }

    // Apply each replacement exactly once per line
    for (const [lineNum, newText] of replacements) {
        const lineIdx = lineNum - 1;
        if (lineIdx >= 0 && lineIdx < editor.document.lineCount) {
            const line = editor.document.lineAt(lineIdx);
            editBuilder.replace(line.range, newText);
        }
    }
});
```
