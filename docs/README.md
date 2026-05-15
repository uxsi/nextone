# NextOne Issues

本目录包含 NextOne 项目的设计文档和技术方案。

## 文档索引

| 编号 | 标题 | 内容概述 |
|------|------|---------|
| [001](./001-product-overview.md) | 产品定位 | NextOne 是什么、支持的编辑器、核心交互、与 Cursor Tab / Copilot 的差异 |
| [002](./002-architecture.md) | 整体架构设计 | 独立服务 + 薄插件层、模块划分、各组件职责 |
| [003](./003-protocol.md) | 通信协议设计 | JSON-RPC over stdio 自定义协议、消息格式定义、连接生命周期 |
| [004](./004-location-module.md) | Location Module | Phase 1 AST 规则引擎、Phase 2 retriever 模型、历史窗口设计 |
| [005](./005-generation-module.md) | Generation Module | NES diff 编码格式、prompt 模板、模型选型、推理引擎、延迟优化 |
| [006](./006-editor-vscode.md) | 编辑器适配：VS Code | Extension API 映射、实现骨架、快捷键配置 |
| [007](./007-editor-vim-neovim.md) | 编辑器适配：Vim / Neovim | Vim 与 Neovim 能力差异、分别的实现骨架、渲染方案对比 |
| [008](./008-keybinding.md) | Cmd+; 快捷键适配 | GUI vs 终端环境检测、CSI u 转义序列、各终端配置、fallback 策略 |
| [009](./009-training-data.md) | 训练数据构建 | git history 三阶段流水线、相关性过滤、数据规模估算、训练方案 |
| [010](./010-roadmap-and-risks.md) | 实施路线与风险评估 | Phase 1/2/3 里程碑、风险矩阵、技术参考链接 |
