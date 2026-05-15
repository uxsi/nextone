# VS Code GUI 进程找不到 next-edit-server

严重度：高

## 现象

Extension 启动后状态栏没有 NextOne 指示器，服务端子进程未启动。终端中 `next-edit-server --help` 正常，但 VS Code 中无效。

## 根因

VS Code 以 GUI 方式启动时不继承终端的 shell profile。`next-edit-server` 通过 pyenv 安装在 `~/.pyenv/versions/3.10.11/bin/` 下，该路径由 `~/.zshrc` 中的 `eval "$(pyenv init -)"` 注册到 PATH，但 GUI 进程不经过 shell profile，看不到这个路径。

LanguageClient 调用 `child_process.spawn("next-edit-server", ...)` 失败后静默退出，没有错误提示。

## 修复

在 `editors/vscode/src/extension.ts` 中新增 `resolveCommand()` 函数和 Output Channel 日志。

修复前，`activate()` 直接使用配置中的命令名：

```typescript
const serverPath = config.get<string>("serverPath", "next-edit-server");

const serverOptions: ServerOptions = {
    command: serverPath,  // "next-edit-server" — GUI 进程找不到
    args: serverArgs,
    transport: TransportKind.stdio,
};
```

修复后，通过用户登录 shell 解析绝对路径：

```typescript
const configuredPath = config.get<string>("serverPath", "next-edit-server");
const serverPath = resolveCommand(configuredPath);

const outputChannel = vscode.window.createOutputChannel("NextOne");
outputChannel.appendLine(`Server path: ${configuredPath} → ${serverPath}`);

const serverOptions: ServerOptions = {
    command: serverPath,  // "/Users/.../.pyenv/versions/3.10.11/bin/next-edit-server"
    args: serverArgs,
    transport: TransportKind.stdio,
};
```

`resolveCommand()` 实现：

```typescript
function resolveCommand(command: string): string {
    if (command.startsWith("/")) {
        return command;
    }
    const shell = os.userInfo().shell || "/bin/zsh";
    try {
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
        // which failed
    }
    return command;
}
```

`$SHELL -l -c "which next-edit-server"` 启动一个登录 shell（`-l` 会 source profile），在其中执行 `which`，返回解析后的绝对路径。
