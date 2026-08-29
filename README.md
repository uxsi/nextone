# NextOne

本地运行的 Next Edit Prediction 系统。开发者完成一处编辑后，自动预测下一处需要修改的位置和内容，无需自然语言指令。

## 项目结构

```
nextone/
├── server/                             # next-edit-server（Python）
│   ├── pyproject.toml
│   ├── scripts/
│   │   ├── verify_server.py            # 端到端验证脚本（同文件 + 跨文件）
│   │   └── verify_lsp_path.py          # LSP 路径验证脚本
│   ├── src/
│   │   └── next_edit_server/
│   │       ├── __main__.py             # CLI 入口
│   │       ├── server.py               # JSON-RPC over stdio 主循环
│   │       ├── protocol.py             # 协议消息类型定义
│   │       ├── document_store.py       # 文档版本管理
│   │       ├── edit_history.py         # 编辑历史滑动窗口
│   │       ├── pipeline.py             # 端到端流水线 + 指标采集
│   │       ├── file_reader.py          # 磁盘文件读取 + LRU 缓存
│   │       ├── location/
│   │       │   ├── engine.py           # 规则引擎调度（同文件 + 跨文件）
│   │       │   ├── rename.py           # 符号重命名传播
│   │       │   ├── signature.py        # 函数签名变更传播
│   │       │   └── pattern.py          # 重复模式检测
│   │       ├── generation/
│   │       │   ├── generator.py        # NES diff 生成
│   │       │   └── prompt.py           # prompt 模板 + diff 解析
│   │       ├── inference/
│   │       │   └── backend.py          # 推理后端（llama.cpp / Dummy）
│   │       └── project_index/
│   │           ├── __init__.py         # ProjectIndex 门面类
│   │           ├── indexer.py          # 后台索引线程
│   │           ├── symbol_table.py     # 线程安全符号表
│   │           ├── file_scanner.py     # git ls-files 文件枚举
│   │           └── queries.py          # tree-sitter 符号提取
│   └── tests/
│       ├── test_protocol.py
│       ├── test_document_store.py
│       ├── test_location.py
│       ├── test_generation.py
│       ├── test_pipeline.py
│       ├── test_file_scanner.py
│       ├── test_symbol_table.py
│       ├── test_project_index.py
│       ├── test_cross_file_location.py
│       └── test_cross_file_pipeline.py
├── editors/
│   └── vscode/                         # VS Code Extension
│       ├── .vscode/
│       │   ├── launch.json             # F5 调试配置（含跨文件验证）
│       │   └── tasks.json              # 编译任务
│       ├── package.json
│       ├── tsconfig.json
│       └── src/
│           └── extension.ts            # 插件入口
├── playground/                         # 手动验证场景
│   ├── cross-file-rename/              # 跨文件 rename 验证
│   │   ├── api.py
│   │   ├── test_api.py
│   │   └── cli.py
│   └── cross-file-signature/           # 跨文件 signature 验证
│       ├── utils.py
│       ├── server.py
│       └── client.py
├── docs/                               # 设计文档
├── issues/                             # 问题记录
└── talks/                              # 评估报告
```

## 环境要求

| 依赖 | 版本 | 用途 |
|------|------|------|
| Python | >= 3.10 | 服务端运行时 |
| Node.js | >= 18 | VS Code Extension 编译 |
| npm | >= 9 | Extension 依赖管理 |
| pip | 最新稳定版 | Python 包安装 |

可选：

| 依赖 | 用途 |
|------|------|
| GGUF 模型文件 | 接入真实 LLM 推理（不提供则使用 DummyBackend） |

## 安装

### 1. 安装服务端

两种安装方式：

```bash
cd server

# 基础安装：只安装运行时依赖（tree-sitter、llama-cpp-python）
pip install -e .

# 开发安装：额外安装测试依赖（pytest、pytest-asyncio）
pip install -e ".[dev]"
```

- `pip install -e .`：安装 `next-edit-server` 及其运行时依赖。适用于纯使用场景（只需要启动服务端）和 CI 生产部署。
- `pip install -e ".[dev]"`：在运行时依赖基础上，额外安装 `pyproject.toml` 中 `[project.optional-dependencies].dev` 定义的测试工具（pytest >= 8.0、pytest-asyncio >= 0.24）。日常开发和运行测试时使用此命令。

`-e`（editable mode）表示以开发模式安装——Python 直接引用源码目录，修改代码后无需重新安装即可生效。

安装完成后，`next-edit-server` 命令会注册到当前 Python 环境的 PATH 中。验证安装：

```bash
next-edit-server --help
```

预期输出：

```
usage: next-edit-server [-h] [--stdio] [--log-level {DEBUG,INFO,WARNING,ERROR}]
                        [--log-file LOG_FILE] [--model-path MODEL_PATH]
                        [--workspace WORKSPACE]

NextOne: local next edit prediction server

options:
  -h, --help            show this help message and exit
  --stdio               Use stdio for JSON-RPC communication (default)
  --log-level {DEBUG,INFO,WARNING,ERROR}
                        Logging level (default: INFO)
  --log-file LOG_FILE   Log to file instead of stderr
  --model-path MODEL_PATH
                        Path to GGUF model file. If not provided, uses a dummy
                        backend for testing.
  --workspace WORKSPACE
                        Workspace root directory for cross-file indexing. If
                        not provided, uses rootUri from LSP initialize or
                        infers from git root.
```

### 2. 安装 VS Code Extension 依赖

```bash
cd editors/vscode
npm install
```

### 3. 编译 Extension

```bash
cd editors/vscode
npm run compile
```

编译产物输出到 `editors/vscode/out/extension.js`。

## 运行

### 方式一：VS Code 调试模式

前提：服务端已安装（`cd server && pip install -e .`），Extension 依赖已安装（`cd editors/vscode && npm install`）。

用 VS Code 打开 `editors/vscode/` 目录。项目已配置好 `.vscode/launch.json` 和 `.vscode/tasks.json`，启动时会自动编译 TypeScript。

有两种 launch 配置：

| 配置名 | 用途 |
|-------|------|
| `Run Extension` | 启动空白 Extension Development Host |
| `Run Extension (playground)` | 打开 `playground/` 作为 workspace，包含所有验证场景 |

启动方式：

**方式 A：F5**

按 `F5`，VS Code 弹出配置选择（或使用上次选择的配置），自动编译并打开 Extension Development Host。

**方式 B：命令面板**

`Cmd+Shift+P` → 输入 `Debug: Start Debugging` → 回车。在弹出的配置列表中选择目标配置。

**方式 C：命令行**

```bash
cd editors/vscode
npm run compile
code --extensionDevelopmentPath="$(pwd)" ../../playground
```

`--extensionDevelopmentPath` 后的位置参数指定 Extension Development Host 打开的目录。

---

**测试步骤（同文件 rename）**

NextOne 通过检测**编辑动作**来预测下一处修改。它需要看到"从 A 改成 B"这个过程，不是看文件当前的静态内容。测试方法：

1. 使用 `Run Extension` 配置启动
2. 新建一个 `.py` 文件，**先输入以下完整内容并保存**：

```python
def hello(name):
    return name

hello("world")
result = hello("test")
```

3. 用光标选中第 1 行的 `hello`（第 4-9 字符），替换输入为 `hi`
4. 停顿 1-2 秒，等待服务端分析
5. 预期效果：光标行尾部出现灰色提示 `Cmd+; Rename hello → hi (2 more)`
6. 按 `Cmd+;` 接受建议，或 `Esc` 拒绝

**测试步骤（跨文件 rename）**

1. 使用 `Run Extension (playground)` 配置启动，Extension Development Host 会打开 `playground/` 目录
2. 打开 `cross-file-rename/api.py`（**不需要打开** `test_api.py` 或 `cli.py`）
3. 等待状态栏出现 `✓ NextOne`（服务端就绪 + 索引完成）
4. 选中第 1 行的 `hello`，替换输入为 `goodbye`
5. 停顿 1-2 秒
6. 预期效果：光标行尾部出现灰色提示 `Cmd+; Rename hello → goodbye in cli.py` 或 `in test_api.py`
7. 按 `Cmd+;` 接受——目标文件在侧边打开并自动完成修改，焦点保留在 `api.py`

**排障**

如果状态栏没有出现 `NextOne` 指示器，说明 Extension 没有成功启动服务端。

常见原因是 VS Code GUI 进程不继承终端的 shell profile（pyenv、nvm 等工具注册的 PATH 对 GUI 进程不可见）。Extension 会自动尝试通过用户登录 shell 解析命令路径（`$SHELL -l -c "which next-edit-server"`），但如果解析失败，需要手动配置绝对路径。

查看 Extension 的路径解析结果：`Cmd+Shift+P` → `Output: Show Output Channel` → 选择 `NextOne`，第一行会显示 `Server path: next-edit-server → /解析后的绝对路径`。

如果解析失败（路径没有变成绝对路径），在 VS Code `settings.json` 中手动指定：

```jsonc
{
    "nextone.serverPath": "/Users/<username>/.pyenv/versions/3.10.11/bin/next-edit-server"
}
```

获取绝对路径的命令：

```bash
pyenv which next-edit-server
# 或
which next-edit-server
```

### 方式二：运行验证脚本（推荐的首次验证方式）

`server/scripts/verify_server.py` 模拟 VS Code 客户端的完整行为：spawn 子进程、发送帧格式的 JSON-RPC 消息、读取并验证服务端响应。不需要 VS Code，不需要模型文件。

```bash
cd server
python scripts/verify_server.py
```

预期输出：

```
============================================================
NextOne Server 端到端验证
============================================================

[启动] next-edit-server --stdio --workspace .../playground/cross-file-rename

[握手] 发送 LSP initialize...
       收到 3 条初始化消息：
       ← initialize response (id=1)
       ← nextEdit/status (state=loading_model)
       ← nextEdit/status (state=ready)

------------------------------------------------------------
场景 1：同文件 rename 传播
------------------------------------------------------------

  [1] 发送 didOpen（包含 hello 函数的 Python 文件）...
      → nextEdit/didOpen (v1)

  [2] 发送 didChange（hello → goodbye）...
      → nextEdit/didChange (v2, hello→goodbye)

  [3] 等待服务端响应（最多 3 秒）...
      收到 2 条消息：
    ← nextEdit/suggest
      id:          suggest-xxxxxxxx
      uri:         file:///tmp/test.py
      baseUri:     file:///tmp/test.py
      baseVersion: 2
      description: Rename `hello` → `goodbye` (2 more)
      deletedLines: [{"num": 4, "text": "hello(\"world\")"}]
      addedLines:   [{"num": 4, "text": "goodbye(\"world\")"}]
    ← nextEdit/status (state=ready)

  ✓ PASS: 同文件 rename 传播正常

------------------------------------------------------------
场景 2：跨文件 rename 传播
------------------------------------------------------------

  [1] 等待项目索引就绪...
      索引应已就绪（3 个文件）

  [2] 发送 didOpen（file:///.../playground/cross-file-rename/api.py）...
      → nextEdit/didOpen (v1)

  [3] 发送 didChange（hello → goodbye）...
      → nextEdit/didChange (v2, hello→goodbye)

  [4] 等待服务端响应（最多 5 秒）...
      收到 3 条消息：
    ← nextEdit/status (state=inferring)
    ← nextEdit/suggest
      id:          suggest-xxxxxxxx
      uri:         file:///.../playground/cross-file-rename/cli.py
      baseUri:     file:///.../playground/cross-file-rename/api.py
      baseVersion: 2
      description: Rename `hello` → `goodbye`
      deletedLines: [{"num": 5, "text": "    msg = api.hello(\"CLI user\")"}]
      addedLines:   [{"num": 5, "text": "    msg = api.goodbye(\"CLI user\")"}]
    ← nextEdit/status (state=ready)

  ✓ PASS: 跨文件 rename 传播正常（suggest 指向其他文件）

============================================================
验证结果汇总
============================================================
  场景 1 (同文件 rename):  PASS ✓
  场景 2 (跨文件 rename):  PASS ✓
============================================================

全部通过。
```

脚本验证两个场景：

1. **同文件 rename**：在一个包含 `hello` 函数定义和调用的文件中重命名，验证服务端返回同文件内的修改建议
2. **跨文件 rename**：在 `playground/cross-file-rename/api.py` 中重命名 `hello`，验证服务端返回指向 `cli.py` 或 `test_api.py` 的跨文件建议（`uri ≠ baseUri`）

### 方式三：手动发送 JSON-RPC 消息（协议调试）

直接在终端与服务端交互。服务端使用 LSP base protocol 帧格式（`Content-Length` 头 + `\r\n\r\n` + JSON 正文），手动通过 stdin 输入不现实，需要用脚本构造帧格式消息。

启动服务端并将日志输出到文件（stdout 是 JSON-RPC 通道，日志必须走 stderr 或文件）：

```bash
next-edit-server --stdio --log-level DEBUG --log-file /tmp/next-edit.log
```

用 Python 向 stdin 发送消息的方式：

```python
import json, sys

def send(msg):
    body = json.dumps(msg).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    sys.stdout.buffer.write(header + body)
    sys.stdout.buffer.flush()

send({
    "jsonrpc": "2.0",
    "method": "nextEdit/didOpen",
    "params": {
        "uri": "file:///tmp/test.py",
        "languageId": "python",
        "version": 1,
        "text": "def hello(name):\n    return name\n"
    }
})
```

通过管道将脚本输出接入服务端 stdin：

```bash
python3 send_messages.py | next-edit-server --stdio --log-level DEBUG 2>/tmp/next-edit.log
```

服务端的响应会输出到 stdout，同样是 `Content-Length` 帧格式。

### 方式四：接入真实模型

下载 Qwen2.5-Coder-7B 的 GGUF 量化版本：

```bash
# 以 Q4_K_M 量化为例（约 4.4 GB）
huggingface-cli download Qwen/Qwen2.5-Coder-7B-Instruct-GGUF \
    qwen2.5-coder-7b-instruct-q4_k_m.gguf \
    --local-dir ~/models/
```

启动服务端时指定模型路径：

```bash
next-edit-server --stdio --model-path ~/models/qwen2.5-coder-7b-instruct-q4_k_m.gguf
```

在 VS Code 中配置（`settings.json`）：

```jsonc
{
    "nextone.modelPath": "/Users/<username>/models/qwen2.5-coder-7b-instruct-q4_k_m.gguf"
}
```

## VS Code 配置项

在 VS Code 的 `settings.json` 中可配置以下选项：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `nextone.serverPath` | string | `"next-edit-server"` | 服务端可执行文件路径。如果 `next-edit-server` 不在 PATH 中，填写绝对路径 |
| `nextone.modelPath` | string | `""` | GGUF 模型文件的绝对路径。留空则使用 DummyBackend |
| `nextone.logLevel` | string | `"INFO"` | 服务端日志级别：`DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `nextone.crossFile.enabled` | boolean | `true` | 启用跨文件编辑建议 |

## 快捷键

| 快捷键 | 作用 | 触发条件 |
|--------|------|---------|
| `Cmd+;` | 接受当前建议 | 编辑器聚焦 + 有建议显示 |
| `Esc` | 拒绝当前建议 | 编辑器聚焦 + 有建议显示 |

建议也会在以下情况自动消失（无需手动操作）：

- 继续编辑文档（文档版本号超过建议的 `baseVersion`）
- 切换到其他文件
- 执行撤销操作
- 建议显示超过 30 秒

## 测试

### 服务端单元测试

```bash
cd server
python -m pytest tests/ -v
```

当前测试覆盖 65 项用例：

| 测试文件 | 覆盖范围 | 用例数 |
|---------|---------|--------|
| `test_protocol.py` | 消息类型序列化、camelCase 转换、notification 构建 | 5 |
| `test_document_store.py` | 文档打开/关闭、增量编辑、全量同步、版本检查 | 7 |
| `test_location.py` | rename 检测与引用查找、signature 检测与调用点查找、pattern 检测、引擎集成 | 11 |
| `test_generation.py` | prompt 构建、NES diff 解析、DummyBackend 集成 | 7 |
| `test_pipeline.py` | 端到端 rename 流程、无匹配不触发、指标采集 | 5 |
| `test_file_scanner.py` | git root 发现、gitignore 过滤、extension 匹配、大文件跳过 | 7 |
| `test_symbol_table.py` | 增删查、exclude 过滤、增量更新、并发安全 | 10 |
| `test_project_index.py` | 索引启动→就绪→查询、就绪前降级、增量重索引 | 3 |
| `test_cross_file_location.py` | 跨文件 rename 检测、无索引降级、无匹配返空、同文件优先 | 4 |
| `test_cross_file_pipeline.py` | 跨文件端到端 suggest、同文件优先级 | 2 |

### VS Code Extension 类型检查

```bash
cd editors/vscode
npx tsc --noEmit
```

## 当前状态

Phase 2，支持的能力：

| 能力 | 状态 |
|------|------|
| VS Code Extension | 已实现 |
| 符号重命名传播 | 已实现（同文件 + 跨文件，tree-sitter / regex） |
| 函数签名变更传播 | 已实现（同文件 + 跨文件） |
| 重复模式检测 | 已实现（同文件，self./this. 属性） |
| 跨文件项目索引 | 已实现（后台线程，git ls-files + tree-sitter） |
| NES diff 生成 | 已实现（DummyBackend + llama.cpp 后端） |
| 协议版本化 + 失效机制 | 已实现 |
| 指标采集 | 已实现（JSON Lines 日志） |
| import 补全 | 未实现（Phase 2 后续） |
| 多编辑器（Neovim / Vim） | 未实现（Phase 2 后续） |
| 训练型 Location Module | 未实现（Phase 3） |
| SFT + DAPO 专用模型 | 未实现（Phase 3） |

## 设计文档

详见 [docs/](./docs/) 目录。

## 问题记录

详见 [issues/](./issues/) 目录。

| 编号 | 标题 | 严重度 |
|------|------|--------|
| [001](./issues/001-missing-vscode-debug-config.md) | 缺少 VS Code 调试配置 | 高 |
| [002](./issues/002-gui-process-path-resolution.md) | VS Code GUI 进程找不到 next-edit-server | 高 |
| [003](./issues/003-lsp-initialize-not-handled.md) | 服务端不处理 LSP initialize 请求 | 高 |
| [004](./issues/004-pipeline-init-before-handshake.md) | pipeline.initialize() 在 LSP 握手前执行 | 中 |
| [005](./issues/005-accept-suggestion-overlapping-replace.md) | acceptSuggestion 对同一行做两次 editBuilder.replace | 高 |
| [006](./issues/006-lsp-didchange-timestamp-zero.md) | LSP 路径下 didChange 的 timestamp 固定为 0 | 低 |
| [007](./issues/007-multi-content-changes-silent-drop.md) | 多个 contentChanges 时只处理第一个且无日志 | 低 |
| [008](./issues/008-new-lines-extraction-wrong.md) | _handle_did_change 中 new_lines 提取错误 | 高 |
| [009](./issues/009-charwise-input-rename-detection.md) | 逐字输入新名称时无法检测 rename | 高 |
| [010](./issues/010-parse-nes-diff-strips-indentation.md) | parse_nes_diff 中 strip() 去掉了代码缩进 | 中 |

## 技术参考

| 资料 | 链接 |
|------|------|
| Augment 博客 | [The AI Research Behind Next Edit](https://www.augmentcode.com/blog/the-ai-research-behind-next-edit) |
| NES 框架论文 | [arxiv 2508.02473](https://arxiv.org/abs/2508.02473) |
| NEP benchmark | [arxiv 2508.10074](https://arxiv.org/abs/2508.10074) |
