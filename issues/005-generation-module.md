# Generation Module 设计

## 职责

确定编辑位置后，基于 `(C_T, H_T, L_gt)` 三元组生成具体的代码修改，输出 NES diff 格式。

## NES Diff 编码格式

每一行（无论增删改）都带绝对行号前缀，位置信息自包含，无需从 `@@` 块头做偏移计算。

标准 difflib 格式 vs NES diff 格式对比：

```diff
# 标准 difflib——用 @@ 块头定位，相对偏移
@@ -1,3 +1,3 @@
-def Hello()
+def GoodBye()
 print("Say")
-print("Hello")
+print("GoodBye")
```

```
# NES diff——每行带绝对行号，位置信息无歧义
1-| def Hello()
1+| def GoodBye()
2 | print("Say")
3-| print("Hello")
3+| print("GoodBye")
```

格式规则：
- 删除行：`{line_num}-| {content}`
- 新增行：`{line_num}+| {content}`
- 上下文行：`{line_num} | {content}`
- 只输出变更区域 + 上下各 2 行上下文

NES diff 的优势：
- 绝对行号消除了偏移计算的错误传播
- token 数比标准 diff 更少，降低生成延迟
- 模型学习难度低——每行都是独立的位置 + 操作 + 内容三元组

## Prompt 模板

```python
SYSTEM_PROMPT = """You are a code edit prediction model. Given the current code state
and recent edit history, generate the next edit in NES diff format.

NES diff format rules:
- Every line has an absolute line number prefix
- Deleted lines: {line_num}-| {content}
- Added lines: {line_num}+| {content}
- Unchanged context lines: {line_num} | {content}
- Only output the changed region with 2 lines of context above and below
"""

def build_generation_prompt(
    current_code: str,
    edit_history: list[dict],  # 最近 3 次编辑的 NES diff
    target_location: int,      # Location Module 预测的行号
    context_window: int = 5    # 目标行上下各取 5 行作为上下文
) -> str:
    lines = current_code.splitlines()
    start = max(0, target_location - context_window)
    end = min(len(lines), target_location + context_window + 1)

    # 带行号的代码片段
    code_context = "\n".join(
        f"{i+1} | {lines[i]}" for i in range(start, end)
    )

    # 编辑历史（最近 3 次）
    history_text = "\n---\n".join(
        f"Edit {i+1} ({h['file']}):\n{h['diff']}"
        for i, h in enumerate(edit_history[-3:])
    )

    return f"""{SYSTEM_PROMPT}

<edit_history>
{history_text}
</edit_history>

<current_code>
{code_context}
</current_code>

<next_edit>
"""
```

## 模型选型

| 阶段 | 模型 | 方式 | 硬件要求 |
|------|------|------|---------|
| Phase 1 | Qwen2.5-Coder-7B | few-shot prompting | Mac M-series 或 8GB GPU |
| Phase 3 | Qwen2.5-Coder-7B | SFT + DAPO | 训练需 A100/H100 |
| 降级方案 | Qwen3-4B | few-shot / SFT | CPU 可用（~15 tok/s） |

NES 论文数据：Qwen3-4B + SFT + DAPO 达到 27.7% Exact Match、91.36% Edit Similarity，在 4B 参数量下表现优于 7B。

## 推理引擎

| 硬件 | 推理引擎 | 7B 模型速度 | 4B 模型速度 |
|------|---------|-----------|-----------|
| Mac M-series | MLX | ~30 tok/s | ~60 tok/s |
| NVIDIA GPU ≥8GB | llama.cpp + CUDA | ~80 tok/s | ~120 tok/s |
| 纯 CPU | llama.cpp CPU | ~8 tok/s | ~15 tok/s |

next edit 输出通常很短（1-5 行 diff，约 50-100 tokens）。各阶段延迟估算：

| 阶段 | Mac M-series（MLX, 7B） | 说明 |
|------|------------------------|------|
| 事件去抖 | 300-500ms | 等待用户停止输入 |
| AST 增量解析 + 规则匹配 | 5-20ms | tree-sitter 增量解析极快 |
| prompt 组装 | < 5ms | 字符串拼接 |
| 首 token 延迟（TTFT） | 200-500ms | 取决于 prompt 长度和模型大小 |
| token 生成 | 1.5-3.3s | 50-100 tokens ÷ 30 tok/s |
| 渲染 + 旧建议清理 | < 50ms | 编辑器侧 |
| **端到端 p50** | **约 2.5s** | |
| **端到端 p95** | **约 4s** | |

延迟可接受性取决于触发方式。用户停顿后出现的主动建议，2-3s 是可接受的等待时间（类似搜索建议的弹出延迟）。但如果在用户连续输入过程中弹出，超过 1s 就会打断编辑节奏。Phase 1 通过高置信度触发条件（仅在明确的规则匹配场景触发）回避后一种情况。

## 延迟优化（Phase 3）

| 技术 | 原理 | 效果 |
|------|------|------|
| Speculative Decoding | 轻量级 n-gram 模型预生成候选 token，大模型做验证 | 提升最显著 |
| Prefix Caching | 缓存历史请求的 KV Cache，复用重复前缀的中间计算 | 减少重复计算 |
| vLLM (Paged Attention + Dynamic Batching) | 优化显存分配和请求批处理 | 提升吞吐量 |

NES 论文最终达到 8500 input tokens/s 吞吐，平均推理延迟 450ms。
