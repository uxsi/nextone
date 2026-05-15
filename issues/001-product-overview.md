# NextOne: 本地 Next Edit Prediction 产品

## 产品定位

NextOne 是一个本地运行的 proactive edit prediction 系统，核心能力是：开发者完成一处编辑后，自动预测下一处需要修改的位置和内容，无需任何自然语言指令。

与传统代码补全（FIM / Fill-in-the-Middle）的本质区别：FIM 只在光标处补全，NextOne 可以跨文件、跨位置主动导航并建议修改。

## 技术背景

Augment Code 的 Next Edit 是这个方向上最早的商业化产品，已下线。其核心技术公开在两篇官方博客和学术界的后续复现工作中：

- Augment 官方博客：[The AI Research Behind Next Edit](https://www.augmentcode.com/blog/the-ai-research-behind-next-edit)
- NES 框架论文：[arxiv 2508.02473](https://arxiv.org/abs/2508.02473)——双模型架构 + NES diff 格式 + DAPO 训练
- NEP benchmark 论文：[arxiv 2508.10074](https://arxiv.org/abs/2508.10074)——首个 Next Edit Prediction 评估基准

目前开源社区没有 Next Edit 的可用实现（continue.dev、Tabby、Aider 均不做 proactive edit prediction），是一个真实的技术空白。

## 支持的编辑器

| 编辑器 | 优先级 | 插件形态 |
|--------|--------|---------|
| VS Code | P0 | Extension |
| Neovim | P0 | Lua plugin |
| Vim 8.0+ | P1 | VimScript plugin |
| Zed | P2 | WASM Extension |

## 核心交互

1. 开发者正常编辑代码
2. NextOne 后台分析编辑历史，预测下一处需要修改的位置和内容
3. 在编辑器中以 inline diff 形式呈现建议（红色标记删除行、绿色标记新增行）
4. 开发者按 `Cmd+;` 接受建议，`Esc` 拒绝

## 与 Cursor Tab / Copilot 的差异

| 维度 | Cursor Tab / Copilot | NextOne |
|------|---------------------|---------|
| 预测范围 | 光标位置补全 | 主动导航到光标之外的位置 |
| 定位机制 | 依赖光标上下文 | 独立的 Location Module 做位置预测 |
| 呈现方式 | Ghost text（灰色虚影） | Inline diff（增删行对比） |
| 意图来源 | 当前编辑上下文 | 显式的编辑历史序列建模 |
| 部署方式 | 云端 | 完全本地 |
