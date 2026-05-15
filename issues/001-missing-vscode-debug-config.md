# 缺少 VS Code 调试配置

严重度：高

## 现象

用 VS Code 打开 `editors/vscode/` 目录后按 F5，无法启动 Extension Development Host。

## 根因

项目没有 `.vscode/launch.json` 和 `.vscode/tasks.json`。VS Code 不知道如何启动 Extension，也不知道启动前需要先编译 TypeScript。

## 修复

新增 `editors/vscode/.vscode/launch.json`：

```jsonc
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Run Extension",
      "type": "extensionHost",
      "request": "launch",
      "args": [
        "--extensionDevelopmentPath=${workspaceFolder}"
      ],
      "outFiles": [
        "${workspaceFolder}/out/**/*.js"
      ],
      "preLaunchTask": "npm: compile"
    }
  ]
}
```

新增 `editors/vscode/.vscode/tasks.json`：

```jsonc
{
  "version": "2.0.0",
  "tasks": [
    {
      "type": "npm",
      "script": "compile",
      "problemMatcher": "$tsc",
      "group": "build",
      "label": "npm: compile"
    },
    {
      "type": "npm",
      "script": "watch",
      "problemMatcher": "$tsc-watch",
      "isBackground": true,
      "group": "build",
      "label": "npm: watch"
    }
  ]
}
```

`preLaunchTask: "npm: compile"` 确保 F5 启动前自动编译 TypeScript。
