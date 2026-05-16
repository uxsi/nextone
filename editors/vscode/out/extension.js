"use strict";
/**
 * NextOne VS Code Extension
 *
 * Thin plugin layer that:
 * 1. Spawns next-edit-server as a child process (JSON-RPC over stdio)
 * 2. Forwards edit events (didOpen, didChange, didClose) to the server
 * 3. Renders inline diff suggestions from the server
 * 4. Handles accept/reject interaction
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
const node_1 = require("vscode-languageclient/node");
// ---------------------------------------------------------------------------
// Extension state
// ---------------------------------------------------------------------------
let client;
let statusBarItem;
let currentSuggestion = null;
let suggestionEditor = null;
// Decoration types for inline diff rendering
const deletionDecorType = vscode.window.createTextEditorDecorationType({
    backgroundColor: "rgba(255, 0, 0, 0.15)",
    isWholeLine: true,
    overviewRulerColor: "rgba(255, 0, 0, 0.5)",
    overviewRulerLane: vscode.OverviewRulerLane.Left,
});
const additionDecorType = vscode.window.createTextEditorDecorationType({
    backgroundColor: "rgba(0, 255, 0, 0.15)",
    isWholeLine: true,
    overviewRulerColor: "rgba(0, 255, 0, 0.5)",
    overviewRulerLane: vscode.OverviewRulerLane.Left,
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
        documentSelector: [{ scheme: "file" }],
        // Prevent LanguageClient from closing documents when the user switches tabs.
        // By default, LanguageClient sends didClose when a tab becomes non-active
        // and didOpen when it becomes active again. This means the server's
        // DocumentStore only ever contains one file, breaking cross-file prediction.
        //
        // We suppress didClose here. When the tab is re-activated, LanguageClient
        // sends didOpen again which is fine (server.document_store.open() overwrites).
        // Documents are truly closed only when the workspace event fires.
        middleware: {
            didClose: async (_document, _next) => {
                // Intentionally not forwarding — keep the document alive in the server
            },
        },
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
        if (currentSuggestion &&
            currentSuggestion.baseUri === event.document.uri.toString() &&
            event.document.version > currentSuggestion.baseVersion) {
            clearSuggestion();
        }
    }));
    // 4b. Re-render decorations when switching back to the suggestion's editor.
    // VS Code may recycle TextEditor instances for non-visible tabs, losing
    // decorations. When the user switches back, we re-apply them.
    context.subscriptions.push(vscode.window.onDidChangeActiveTextEditor((editor) => {
        if (editor &&
            currentSuggestion &&
            editor.document.uri.toString() === currentSuggestion.uri) {
            suggestionEditor = editor;
            renderSuggestionDecorations(editor, currentSuggestion);
        }
    }));
    // 4c. Send didClose only when a document is truly removed from the workspace.
    // The middleware above suppresses LanguageClient's automatic didClose on tab
    // switch. We must handle real closes ourselves.
    context.subscriptions.push(vscode.workspace.onDidCloseTextDocument((document) => {
        if (client && document.uri.scheme === "file") {
            client.sendNotification("textDocument/didClose", {
                textDocument: { uri: document.uri.toString() },
            });
        }
    }));
    // 5. Register accept/reject commands
    context.subscriptions.push(vscode.commands.registerCommand("nextEdit.accept", () => {
        acceptSuggestion();
    }), vscode.commands.registerCommand("nextEdit.reject", () => {
        rejectSuggestion();
    }));
    // 6. Start client and sync all open documents.
    // LanguageClient's default document sync only tracks the active editor.
    // For cross-file prediction, the server needs all open files in its
    // DocumentStore. After the client is ready, we manually send didOpen
    // for every file-scheme document already open in the workspace.
    client.start().then(() => {
        for (const doc of vscode.workspace.textDocuments) {
            if (doc.uri.scheme === "file" && !doc.isClosed) {
                client.sendNotification("textDocument/didOpen", {
                    textDocument: {
                        uri: doc.uri.toString(),
                        languageId: doc.languageId,
                        version: doc.version,
                        text: doc.getText(),
                    },
                });
            }
        }
    });
    // Also send didOpen for any file opened after client startup.
    // This covers files the user opens after the extension activates,
    // including files that LanguageClient would not auto-open because
    // another tab already has focus.
    context.subscriptions.push(vscode.workspace.onDidOpenTextDocument((doc) => {
        if (client && doc.uri.scheme === "file") {
            client.sendNotification("textDocument/didOpen", {
                textDocument: {
                    uri: doc.uri.toString(),
                    languageId: doc.languageId,
                    version: doc.version,
                    text: doc.getText(),
                },
            });
        }
    }));
}
function deactivate() {
    if (!client) {
        return undefined;
    }
    return client.stop();
}
// ---------------------------------------------------------------------------
// Suggestion rendering
// ---------------------------------------------------------------------------
function handleSuggestion(params) {
    // Check if suggestion is already stale
    const doc = vscode.workspace.textDocuments.find((d) => d.uri.toString() === params.baseUri);
    if (doc && doc.version > params.baseVersion) {
        return; // Stale
    }
    clearSuggestion();
    currentSuggestion = params;
    const editor = vscode.window.activeTextEditor;
    if (!editor || editor.document.uri.toString() !== params.uri) {
        return;
    }
    suggestionEditor = editor;
    renderSuggestionDecorations(editor, params);
    // Scroll to the suggestion location
    const targetPos = new vscode.Position(params.location.line, 0);
    editor.revealRange(new vscode.Range(targetPos, targetPos), vscode.TextEditorRevealType.InCenterIfOutsideViewport);
    // Set context key for when-clause
    vscode.commands.executeCommand("setContext", "nextEdit.hasSuggestion", true);
    // Show description in status bar
    statusBarItem.text = `$(lightbulb) ${params.description}`;
}
/**
 * Apply deletion/addition decorations to an editor for the given suggestion.
 * Called both on initial render and when switching back to the suggestion's tab.
 */
function renderSuggestionDecorations(editor, params) {
    const deletionRanges = params.deletedLines.map((l) => new vscode.Range(l.num - 1, 0, l.num - 1, Number.MAX_SAFE_INTEGER));
    editor.setDecorations(deletionDecorType, deletionRanges);
    const additionRanges = params.addedLines.map((l) => new vscode.Range(l.num - 1, 0, l.num - 1, Number.MAX_SAFE_INTEGER));
    editor.setDecorations(additionDecorType, additionRanges);
}
function clearSuggestion() {
    // Clear decorations on the editor where the suggestion was rendered,
    // not necessarily the current activeTextEditor (user may have switched tabs).
    const editor = suggestionEditor ?? vscode.window.activeTextEditor;
    if (editor) {
        editor.setDecorations(deletionDecorType, []);
        editor.setDecorations(additionDecorType, []);
    }
    suggestionEditor = null;
    currentSuggestion = null;
    vscode.commands.executeCommand("setContext", "nextEdit.hasSuggestion", false);
}
// ---------------------------------------------------------------------------
// Accept / Reject
// ---------------------------------------------------------------------------
function acceptSuggestion() {
    if (!currentSuggestion) {
        return;
    }
    // Use the editor where the suggestion was rendered, not activeTextEditor
    // (user may have switched tabs since the suggestion appeared).
    const editor = suggestionEditor ?? vscode.window.activeTextEditor;
    if (!editor) {
        return;
    }
    // Safety: verify the editor's document matches the suggestion target
    if (editor.document.uri.toString() !== currentSuggestion.uri) {
        clearSuggestion();
        return;
    }
    const suggestion = currentSuggestion;
    // Apply the diff: replace each deleted line with its corresponding added line.
    // Phase 1 scenarios (rename, signature) produce 1:1 deleted→added mappings
    // with matching line numbers.
    editor
        .edit((editBuilder) => {
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
    })
        .then((success) => {
        if (success) {
            // Notify server
            client.sendNotification("nextEdit/resolve", {
                id: suggestion.id,
                accepted: true,
            });
        }
    });
    clearSuggestion();
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