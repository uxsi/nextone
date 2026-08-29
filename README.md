# NextOne

本地运行的 Next Edit Prediction 系统。开发者完成一处编辑后，自动预测下一处需要修改的位置和内容，无需自然语言指令。

## 项目结构

```
nextone/
├── server/                             # next-edit-server（Python）
│   ├── pyproject.toml
│   ├── scripts/
│   │   ├── verify_server.py            # 端到端验证脚本（同文件 + 跨文件）
│   │   ├── verify_lsp_path.py          # LSP 路径验证脚本
│   │   └── verify_import_filter.py     # 跨文件 import 关系过滤验证
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
