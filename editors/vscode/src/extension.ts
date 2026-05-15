/**
 * NextOne VS Code Extension
 *
 * Thin plugin layer that:
 * 1. Spawns next-edit-server as a child process (JSON-RPC over stdio)
 * 2. Forwards edit events (didOpen, didChange, didClose) to the server
 * 3. Renders inline diff suggestions from the server
 * 4. Handles accept/reject interaction
 */

import * as vscode from "vscode";
import * as cp from "child_process";
import * as os from "os";
import {
  LanguageClient,
  LanguageClientOptions,
  ServerOptions,
  TransportKind,
} from "vscode-languageclient/node";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface LineDiff {
  num: number;
  text: string;
}

interface SuggestParams {
  id: string;
  uri: string;
  baseUri: string;
  baseVersion: number;
  location: { line: number; character: number };
  diff: string;
  description: string;
  deletedLines: LineDiff[];
  addedLines: LineDiff[];
}

interface CancelSuggestionParams {
  id: string;
  reason: string;
}

interface StatusParams {
  state: "ready" | "loading_model" | "inferring" | "error";
  message: string;
}

// ---------------------------------------------------------------------------
// Extension state
// ---------------------------------------------------------------------------

let client: LanguageClient;
let statusBarItem: vscode.StatusBarItem;
let currentSuggestion: SuggestParams | null = null;

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

export function activate(context: vscode.ExtensionContext): void {
  const config = vscode.workspace.getConfiguration("nextone");
  const configuredPath = config.get<string>("serverPath", "next-edit-server");
  const modelPath = config.get<string>("modelPath", "");
  const logLevel = config.get<string>("logLevel", "INFO");

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
  const serverArgs = ["--stdio", "--log-level", "DEBUG", "--log-file", logFile];
  if (modelPath) {
    serverArgs.push("--model-path", modelPath);
  }
  outputChannel.appendLine(`Server log file: ${logFile}`);

  // 1. Start next-edit-server
  const serverOptions: ServerOptions = {
    command: serverPath,
    args: serverArgs,
    transport: TransportKind.stdio,
  };

  const clientOptions: LanguageClientOptions = {
    // We handle document sync ourselves via custom methods
    documentSelector: [{ scheme: "file" }],
  };

  client = new LanguageClient(
    "nextEdit",
    "NextOne",
    serverOptions,
    clientOptions,
  );

  // 2. Status bar
  statusBarItem = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Right,
    100,
  );
  statusBarItem.text = "$(loading~spin) NextOne";
  statusBarItem.show();
  context.subscriptions.push(statusBarItem);

  // 3. Register custom notification handlers
  client.onNotification("nextEdit/suggest", (params: SuggestParams) => {
    handleSuggestion(params);
  });

  client.onNotification(
    "nextEdit/cancelSuggestion",
    (params: CancelSuggestionParams) => {
      if (currentSuggestion && currentSuggestion.id === params.id) {
        clearSuggestion();
      }
    },
  );

  client.onNotification("nextEdit/status", (params: StatusParams) => {
    updateStatusBar(params);
  });

  // 4. Auto-dismiss stale suggestions on document change
  context.subscriptions.push(
    vscode.workspace.onDidChangeTextDocument((event) => {
      if (
        currentSuggestion &&
        currentSuggestion.baseUri === event.document.uri.toString() &&
        event.document.version > currentSuggestion.baseVersion
      ) {
        clearSuggestion();
      }
    }),
  );

  // 5. Register accept/reject commands
  context.subscriptions.push(
    vscode.commands.registerCommand("nextEdit.accept", () => {
      acceptSuggestion();
    }),
    vscode.commands.registerCommand("nextEdit.reject", () => {
      rejectSuggestion();
    }),
  );

  // 6. Start client
  // LanguageClient automatically sends textDocument/didOpen, didChange,
  // didSave, didClose for files matching documentSelector. The server
  // translates these standard LSP messages to our custom format.
  client.start();
}

export function deactivate(): Thenable<void> | undefined {
  if (!client) {
    return undefined;
  }
  return client.stop();
}

// ---------------------------------------------------------------------------
// Suggestion rendering
// ---------------------------------------------------------------------------

function handleSuggestion(params: SuggestParams): void {
  // Check if suggestion is already stale
  const doc = vscode.workspace.textDocuments.find(
    (d) => d.uri.toString() === params.baseUri,
  );
  if (doc && doc.version > params.baseVersion) {
    return; // Stale
  }

  clearSuggestion();
  currentSuggestion = params;

  const editor = vscode.window.activeTextEditor;
  if (!editor || editor.document.uri.toString() !== params.uri) {
    return;
  }

  // Render deleted lines (red background)
  const deletionRanges = params.deletedLines.map(
    (l) =>
      new vscode.Range(
        l.num - 1,
        0,
        l.num - 1,
        Number.MAX_SAFE_INTEGER,
      ),
  );
  editor.setDecorations(deletionDecorType, deletionRanges);

  // Render added lines (green background)
  // For added lines we highlight the same line numbers
  const additionRanges = params.addedLines.map(
    (l) =>
      new vscode.Range(
        l.num - 1,
        0,
        l.num - 1,
        Number.MAX_SAFE_INTEGER,
      ),
  );
  editor.setDecorations(additionDecorType, additionRanges);

  // Scroll to the suggestion location
  const targetPos = new vscode.Position(params.location.line, 0);
  editor.revealRange(
    new vscode.Range(targetPos, targetPos),
    vscode.TextEditorRevealType.InCenterIfOutsideViewport,
  );

  // Set context key for when-clause
  vscode.commands.executeCommand("setContext", "nextEdit.hasSuggestion", true);

  // Show description in status bar
  statusBarItem.text = `$(lightbulb) ${params.description}`;
}

function clearSuggestion(): void {
  const editor = vscode.window.activeTextEditor;
  if (editor) {
    editor.setDecorations(deletionDecorType, []);
    editor.setDecorations(additionDecorType, []);
  }
  currentSuggestion = null;
  vscode.commands.executeCommand("setContext", "nextEdit.hasSuggestion", false);
}

// ---------------------------------------------------------------------------
// Accept / Reject
// ---------------------------------------------------------------------------

function acceptSuggestion(): void {
  if (!currentSuggestion) {
    return;
  }

  const editor = vscode.window.activeTextEditor;
  if (!editor) {
    return;
  }

  const suggestion = currentSuggestion;

  // Apply the diff: replace each deleted line with its corresponding added line.
  // Phase 1 scenarios (rename, signature) produce 1:1 deleted→added mappings
  // with matching line numbers.
  editor
    .edit((editBuilder) => {
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

function rejectSuggestion(): void {
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

function updateStatusBar(params: StatusParams): void {
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
function resolveCommand(command: string): string {
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
  } catch {
    // which failed — command not found in shell environment
  }

  return command;
}
