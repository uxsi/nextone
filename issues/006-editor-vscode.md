# 编辑器插件适配：VS Code

## 概述

VS Code Extension 是最成熟的插件形态，Extension API 功能最丰富，是 Phase 1 MVP 的首选平台。

## 核心能力映射

| 需求 | VS Code API |
|------|------------|
| 监听编辑事件 | `workspace.onDidChangeTextDocument` |
| 渲染 inline diff（删除行） | `TextEditorDecorationType` 红色背景 |
| 渲染 inline diff（新增行） | `TextEditorDecorationType` 绿色背景 + `DecorationRenderOptions` |
| 快捷键绑定 | `keybindings.json` 中注册 `Cmd+;` |
| 状态栏指示器 | `StatusBarItem` |
| 服务进程通信 | `LanguageClient`（vscode-languageclient） |

## 实现骨架

```typescript
import * as vscode from 'vscode';
import { LanguageClient, TransportKind } from 'vscode-languageclient/node';

let client: LanguageClient;

export function activate(context: vscode.ExtensionContext) {
    // 1. 启动 next-edit-server
    const serverOptions = {
        command: 'next-edit-server',
        args: ['--stdio'],
        transport: TransportKind.stdio,
    };
    client = new LanguageClient('nextEdit', 'NextOne', serverOptions, {});

    // 2. 监听编辑事件，转发给服务
    vscode.workspace.onDidChangeTextDocument((event) => {
        const changes = event.contentChanges.map(c => ({
            range: { start: c.range.start, end: c.range.end },
            text: c.text,
        }));
        client.sendNotification('nextEdit/didChange', {
            uri: event.document.uri.toString(),
            changes,
            timestamp: Date.now(),
        });
    });

    // 3. 接收建议，渲染 inline diff
    client.onNotification('nextEdit/suggest', (params) => {
        renderSuggestion(params);
    });

    // 4. 注册 accept / reject 命令
    context.subscriptions.push(
        vscode.commands.registerCommand('nextEdit.accept', () => {
            acceptSuggestion();
        }),
        vscode.commands.registerCommand('nextEdit.reject', () => {
            clearSuggestion();
        }),
    );

    client.start();
}

// inline diff 渲染
const deletionDecor = vscode.window.createTextEditorDecorationType({
    backgroundColor: 'rgba(255, 0, 0, 0.15)',
    isWholeLine: true,
});
const additionDecor = vscode.window.createTextEditorDecorationType({
    backgroundColor: 'rgba(0, 255, 0, 0.15)',
    isWholeLine: true,
});

let currentSuggestion: any = null;

function renderSuggestion(params: any) {
    clearSuggestion();
    currentSuggestion = params;

    const editor = vscode.window.activeTextEditor;
    if (!editor) return;

    // 删除行标记
    const deletionRanges = params.deleted_lines.map((l: any) =>
        new vscode.Range(l.num - 1, 0, l.num - 1, Number.MAX_SAFE_INTEGER)
    );
    editor.setDecorations(deletionDecor, deletionRanges);

    // 新增行标记（通过 after decoration 在目标行下方渲染）
    const additionRanges = params.added_lines.map((l: any) =>
        new vscode.Range(l.num - 1, 0, l.num - 1, Number.MAX_SAFE_INTEGER)
    );
    editor.setDecorations(additionDecor, additionRanges);

    // 设置 context key 供 when clause 使用
    vscode.commands.executeCommand(
        'setContext', 'nextEdit.hasSuggestion', true
    );
}

function clearSuggestion() {
    const editor = vscode.window.activeTextEditor;
    if (editor) {
        editor.setDecorations(deletionDecor, []);
        editor.setDecorations(additionDecor, []);
    }
    currentSuggestion = null;
    vscode.commands.executeCommand(
        'setContext', 'nextEdit.hasSuggestion', false
    );
}

function acceptSuggestion() {
    if (!currentSuggestion) return;
    // 将 diff 应用到文档
    applyDiff(currentSuggestion);
    // 通知服务端
    client.sendNotification('nextEdit/resolve', {
        id: currentSuggestion.id,
        accepted: true,
    });
    clearSuggestion();
}
```

## 快捷键配置

```jsonc
// package.json 中的 contributes.keybindings
{
  "contributes": {
    "keybindings": [
      {
        "key": "cmd+;",
        "command": "nextEdit.accept",
        "when": "editorTextFocus && nextEdit.hasSuggestion"
      },
      {
        "key": "escape",
        "command": "nextEdit.reject",
        "when": "editorTextFocus && nextEdit.hasSuggestion"
      }
    ]
  }
}
```
