# 分阶段实施路线与风险评估

## 实施路线

### Phase 1：MVP（1-2 周）

**目标**：验证"从编辑历史预测下一处修改"这个交互模式在日常使用中是否有价值。

| 模块 | 方案 |
|------|------|
| Location | tree-sitter AST 规则引擎（重命名传播、接口变更、import 补全） |
| Generation | Qwen2.5-Coder-7B few-shot prompting（NES diff format） |
| 编辑器 | VS Code Extension |
| 快捷键 | Cmd+; 接受 |
| 硬件 | Mac M-series 或 8GB GPU |
| 跨文件 | 不支持，限定当前文件内 |

交付物：
- `next-edit-server` CLI（Python，启动方式 `next-edit-server --stdio`）
- VS Code Extension（TypeScript）
- 基于 MLX / llama.cpp 的本地推理

### Phase 2：多编辑器 + 编辑历史建模（2-4 周）

**目标**：扩展编辑器支持，引入学习型 Location Module。

| 模块 | 方案 |
|------|------|
| Location | AST 规则 + CodeBERT retriever（混合模式） |
| Generation | 同 Phase 1 + git history 数据增强 |
| 编辑器 | + Neovim (Lua) + Vim 8.0+ (VimScript) |
| 快捷键 | GUI: Cmd+;，终端: CSI u 转义序列 + Ctrl+; fallback |
| 跨文件 | tree-sitter 符号引用分析 |

新增交付物：
- Neovim plugin（Lua）
- Vim plugin（VimScript）
- 终端快捷键配置指南（iTerm2 / kitty / Alacritty / WezTerm）
- git history → 训练数据提取工具

### Phase 3：模型训练 + 延迟优化（4-8 周）

**目标**：训练专用模型，达到生产级质量和延迟。

| 模块 | 方案 |
|------|------|
| Location | fine-tuned retriever (UniXcoder SFT + DAPO) |
| Generation | Qwen2.5-Coder-7B SFT + DAPO |
| 延迟优化 | Speculative Decoding + Prefix Caching + vLLM |
| 跨文件 | full codebase retriever |
| 编辑器 | + Zed (WASM Extension) |
| 硬件 | 训练需 A100/H100，推理同 Phase 1 |

新增交付物：
- 训练数据构建流水线（三阶段）
- fine-tuned Location Model
- fine-tuned Generation Model
- Zed Extension
- benchmark 评估套件

## 风险评估

### 高风险

**误建议率**
- 描述：Phase 1 没有 -keep 判断能力，模型不知道什么时候该保持沉默
- 影响：频繁的错误建议会让用户关掉功能
- 缓解：设置高置信度阈值——AST 规则只在明确的模式匹配（重命名、接口变更）时触发，不做模糊预测
- 根治：Phase 3 训练 -keep 样本，显式建模"不建议"能力

**产品化挑战**
- 描述：Augment Code 下线 Next Edit 本身说明这个交互模式在产品化上有难度
- 影响：可能投入大量工程后发现用户接受度不高
- 缓解：Phase 1 MVP 用 1-2 周快速验证，个人使用场景优先，不急于追求通用性

### 中风险

**训练数据质量**
- 描述：git history 合成的数据不等于真实编辑序列（commit 是最终结果，丢失了中间过程）
- 影响：模型学到的是 commit-level 的编辑模式，而非 keystroke-level 的
- 缓解：NES 论文的相关性过滤阶段用 LLM 做二分类，显著提升数据质量

**跨文件定位**
- 描述：Phase 1 限定当前文件内，跨文件需要全仓索引
- 影响：丢失了 Next Edit 最有价值的场景之一
- 缓解：Phase 2 用 tree-sitter 符号引用分析做有限的跨文件跳转

### 低风险

**硬件门槛**
- 描述：7B 模型在纯 CPU 上推理延迟约 3-5 秒
- 影响：没有 GPU 的开发者体验较差
- 缓解：提供 4B 模型降级方案（Qwen3-4B 在 NES benchmark 上 4B 表现优于 7B）

**Vim 渲染能力**
- 描述：Vim 没有 `virt_lines`，popup 浮窗的渲染效果不如 Neovim 的行间插入
- 影响：Vim 用户的视觉体验弱于 Neovim 用户
- 缓解：功能一致，视觉差异可接受

## 技术参考

| 资料 | 链接 | 用途 |
|------|------|------|
| Augment 博客 | [The AI Research Behind Next Edit](https://www.augmentcode.com/blog/the-ai-research-behind-next-edit) | 产品设计理念 |
| NES 框架论文 | [arxiv 2508.02473](https://arxiv.org/abs/2508.02473) | 双模型架构 + NES diff 格式 + DAPO |
| NEP benchmark | [arxiv 2508.10074](https://arxiv.org/abs/2508.10074) | 评估基准 + 训练数据构建方法 |
| tree-sitter | [tree-sitter.github.io](https://tree-sitter.github.io/) | AST 分析引擎 |
| MLX | [github.com/ml-explore/mlx](https://github.com/ml-explore/mlx) | Mac M-series 推理框架 |
| llama.cpp | [github.com/ggerganov/llama.cpp](https://github.com/ggerganov/llama.cpp) | 跨平台推理引擎 |
| Qwen2.5-Coder | [huggingface.co/Qwen/Qwen2.5-Coder-7B](https://huggingface.co/Qwen/Qwen2.5-Coder-7B) | 基座模型 |
