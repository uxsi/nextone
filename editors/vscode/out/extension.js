"use strict";
/**
 * NextOne VS Code Extension
 *
 * Thin plugin layer that:
 * 1. Spawns next-edit-server as a child process (JSON-RPC over stdio)
 * 2. Forwards edit events (didOpen, didChange, didClose) to the server
 * 3. Renders inline hint suggestions (after pseudo-element at cursor line end)
 * 4. Handles accept/reject interaction — including cross-file suggestions
 */
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.activate = activate;
exports.deactivate = deactivate;
const vscode = __importStar(require("vscode"));
const cp = __importStar(require("child_process"));
const os = __importStar(require("os"));
const path = __importStar(require("path"));
const node_1 = require("vscode-languageclient/node");
// ---------------------------------------------------------------------------
// Extension state
// ---------------------------------------------------------------------------
let client;
let statusBarItem;
let currentSuggestion = null;
let suggestionEditor = null;
// Decoration type for inline hint at cursor line end.
// Uses `after` pseudo-element — zero impact on layout: no inserted characters,
// no extra lines, no shifted content. Pure render-layer overlay.
const hintDecorType = vscode.window.createTextEditorDecorationType({
    rangeBehavior: vscode.DecorationRangeBehavior.ClosedClosed,
    // No backgroundColor, isWholeLine, or before — zero layout side-effects
});
// ---------------------------------------------------------------------------
// Activation
// ---------------------------------------------------------------------------
function activate(context) {
    const config = vscode.workspace.getConfiguration("nextone");
    const configuredPath = config.get("serverPath", "next-edit-server");
    const modelPath = config.get("modelPath", "");
    const logLevel = config.get("logLevel", "INFO");
    // Resolve the server executable path.
    // VS Code GUI processes don't inherit shell profile (pyenv, nvm, etc.),
    // so a bare command name like "next-edit-server" may not be in PATH.
    // We resolve it through the user's login shell.
    const serverPath = resolveCommand(configuredPath);
    const outputChannel = vscode.window.createOutputChannel("NextOne");
    outputChannel.appendLine(`Server path: ${configuredPath} → ${serverPath}`);
    context.subscriptions.push(outputChannel);
    // Build server args — always log to file for diagnostics
    const logFile = "/tmp/next-edit-server.log";
    const serverArgs = ["--stdio", "--log-level", logLevel, "--log-file", logFile];
    if (modelPath) {
        serverArgs.push("--model-path", modelPath);
    }
    outputChannel.appendLine(`Server log file: ${logFile}`);
    // 1. Start next-edit-server
    const serverOptions = {
        command: serverPath,
        args: serverArgs,
        transport: node_1.TransportKind.stdio,
    };
    const clientOptions = {
        // We handle document sync ourselves via custom methods
        documentSelector: [{ scheme: "file" }],
    };
    client = new node_1.LanguageClient("nextEdit", "NextOne", serverOptions, clientOptions);
    // 2. Status bar
    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    statusBarItem.text = "$(loading~spin) NextOne";
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);
    // 3. Register custom notification handlers
    client.onNotification("nextEdit/suggest", (params) => {
        handleSuggestion(params);
    });
    client.onNotification("nextEdit/cancelSuggestion", (params) => {
        if (currentSuggestion && currentSuggestion.id === params.id) {
            clearSuggestion();
        }
    });
    client.onNotification("nextEdit/status", (params) => {
        updateStatusBar(params);
    });
    // 4. Auto-dismiss stale suggestions on document change
    context.subscriptions.push(vscode.workspace.onDidChangeTextDocument((event) => {
        if (!currentSuggestion) {
            return;
        }
        const changedUri = event.document.uri.toString();
        // Source file changed → baseVersion is stale
        if (changedUri === currentSuggestion.baseUri &&
            event.document.version > currentSuggestion.baseVersion) {
            clearSuggestion();
            return;
        }
        // Target file changed (cross-file) → suggestion no longer valid
        if (changedUri === currentSuggestion.uri && changedUri !== currentSuggestion.baseUri) {
            clearSuggestion();
        }
    }));
    // 4b. Re-render hint when switching back to the suggestion's source editor.
    context.subscriptions.push(vscode.window.onDidChangeActiveTextEditor((editor) => {
        if (editor &&
            currentSuggestion &&
            editor.document.uri.toString() === currentSuggestion.baseUri) {
            suggestionEditor = editor;
            renderSuggestionHint(editor, currentSuggestion);
        }
    }));
    // 5. Register accept/reject commands
    context.subscriptions.push(vscode.commands.registerCommand("nextEdit.accept", () => {
        acceptSuggestion();
    }), vscode.commands.registerCommand("nextEdit.reject", () => {
        rejectSuggestion();
    }));
    // 6. Start client
    // LanguageClient automatically sends textDocument/didOpen, didChange,
    // didSave, didClose for files matching documentSelector. The server
    // translates these standard LSP messages to our custom format.
    client.start();
}
function deactivate() {
    if (!client) {
        return undefined;
    }
    return client.stop();
}
// ---------------------------------------------------------------------------
// Suggestion rendering — unified for same-file and cross-file
// ---------------------------------------------------------------------------
function handleSuggestion(params) {
    // Check if suggestion is already stale
    const doc = vscode.workspace.textDocuments.find((d) => d.uri.toString() === params.baseUri);
    if (doc && doc.version > params.baseVersion) {
        return; // Stale
    }
    clearSuggestion();
    currentSuggestion = params;
    // Always render the hint in the current editor (source file)
    const editor = vscode.window.activeTextEditor;
    if (!editor || editor.document.uri.toString() !== params.baseUri) {
        return;
    }
    suggestionEditor = editor;
    renderSuggestionHint(editor, params);
    // Set context key for when-clause
    vscode.commands.executeCommand("setContext", "nextEdit.hasSuggestion", true);
    // Show description in status bar
    const isCrossFile = params.uri !== params.baseUri;
    const fileLabel = isCrossFile
        ? ` [${path.basename(vscode.Uri.parse(params.uri).fsPath)}]`
        : "";
    statusBarItem.text = `$(lightbulb) ${params.description}${fileLabel}`;
}
/**
 * Render the suggestion as an `after` pseudo-element at the cursor line end.
 *
 * Zero layout impact: no inserted characters, no extra lines, no shifted content.
 * The hint text is rendered as a purely visual overlay after the last character
 * on the cursor line.
 */
function renderSuggestionHint(editor, params) {
    const cursorLine = editor.selection.active.line;
    const isCrossFile = params.uri !== params.baseUri;
    const fileName = isCrossFile
        ? ` in ${path.basename(vscode.Uri.parse(params.uri).fsPath)}`
        : "";
    const hintText = `Cmd+; ${params.description}${fileName}`;
    // Range anchored at the very end of the line — does not cover any existing text
    const lineEnd = editor.document.lineAt(cursorLine).range.end;
    const range = new vscode.Range(lineEnd, lineEnd);
    editor.setDecorations(hintDecorType, [
        {
            range,
            renderOptions: {
                after: {
                    contentText: hintText,
                    color: new vscode.ThemeColor("editorCodeLens.foreground"),
                    fontStyle: "italic",
                    margin: "0 0 0 2em",
                },
            },
        },
    ]);
}
function clearSuggestion() {
    // Clear hint decoration on the editor where it was rendered
    const editor = suggestionEditor ?? vscode.window.activeTextEditor;
    if (editor) {
        editor.setDecorations(hintDecorType, []);
    }
    suggestionEditor = null;
    currentSuggestion = null;
    vscode.commands.executeCommand("setContext", "nextEdit.hasSuggestion", false);
}
// ---------------------------------------------------------------------------
// Accept / Reject
// ---------------------------------------------------------------------------
async function acceptSuggestion() {
    if (!currentSuggestion) {
        return;
    }
    const suggestion = currentSuggestion;
    const isCrossFile = suggestion.uri !== suggestion.baseUri;
    if (isCrossFile) {
        await acceptCrossFileSuggestion(suggestion);
    }
    else {
        await acceptSameFileSuggestion(suggestion);
    }
    client.sendNotification("nextEdit/resolve", {
        id: suggestion.id,
        accepted: true,
    });
    clearSuggestion();
}
/**
 * Apply the suggestion to the current (same) file.
 */
async function acceptSameFileSuggestion(suggestion) {
    const editor = suggestionEditor ?? vscode.window.activeTextEditor;
    if (!editor) {
        return;
    }
    // Safety: verify the editor's document matches the suggestion target
    if (editor.document.uri.toString() !== suggestion.uri) {
        return;
    }
    await applyDiffToEditor(editor, suggestion);
}
/**
 * Apply the suggestion to a different file — silently open, apply, save.
 * The user's focus stays in the current file.
 */
async function acceptCrossFileSuggestion(suggestion) {
    const targetUri = vscode.Uri.parse(suggestion.uri);
    try {
        const doc = await vscode.workspace.openTextDocument(targetUri);
        // showTextDocument with preserveFocus: true keeps user in current file
        const editor = await vscode.window.showTextDocument(doc, {
            viewColumn: vscode.ViewColumn.Beside,
            preserveFocus: true,
            preview: true,
        });
        await applyDiffToEditor(editor, suggestion);
        await doc.save();
    }
    catch (err) {
        // File may have been deleted or become unreadable
        vscode.window.showWarningMessage(`NextOne: Could not apply cross-file suggestion to ${path.basename(targetUri.fsPath)}`);
    }
}
/**
 * Apply a NES diff suggestion to an editor's document.
 */
async function applyDiffToEditor(editor, suggestion) {
    await editor.edit((editBuilder) => {
        // Build a map: line number → new text
        const replacements = new Map();
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
}
function rejectSuggestion() {
    if (!currentSuggestion) {
        return;
    }
    client.sendNotification("nextEdit/resolve", {
        id: currentSuggestion.id,
        accepted: false,
    });
    clearSuggestion();
}
// ---------------------------------------------------------------------------
// Status bar
// ---------------------------------------------------------------------------
function updateStatusBar(params) {
    switch (params.state) {
        case "ready":
            statusBarItem.text = "$(check) NextOne";
            break;
        case "loading_model":
            statusBarItem.text = "$(loading~spin) NextOne: Loading...";
            break;
        case "inferring":
            statusBarItem.text = "$(loading~spin) NextOne: Analyzing...";
            break;
        case "error":
            statusBarItem.text = `$(error) NextOne: ${params.message}`;
            break;
    }
}
// ---------------------------------------------------------------------------
// Shell path resolution
// ---------------------------------------------------------------------------
/**
 * Resolve a command name to its absolute path through the user's login shell.
 *
 * VS Code GUI processes on macOS don't inherit the terminal's shell profile,
 * so tools installed via pyenv, nvm, pipx, etc. are not in PATH. This function
 * spawns the user's login shell to run `which <command>` and returns the
 * resolved absolute path. If resolution fails, returns the original input
 * unchanged (the LanguageClient will attempt to find it directly).
 */
function resolveCommand(command) {
    // Already an absolute path — no resolution needed
    if (command.startsWith("/")) {
        return command;
    }
    const shell = os.userInfo().shell || "/bin/zsh";
    try {
        // -l: login shell (sources profile), -c: execute command
        const resolved = cp
            .execSync(`${shell} -l -c "which ${command}"`, {
            encoding: "utf-8",
            timeout: 5000,
            stdio: ["pipe", "pipe", "pipe"],
        })
            .trim();
        if (resolved && resolved.startsWith("/")) {
            return resolved;
        }
    }
    catch {
        // which failed — command not found in shell environment
    }
    return command;
}
//# sourceMappingURL=extension.js.map