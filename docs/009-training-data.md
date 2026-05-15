# 训练数据构建

## 概述

从本地 git history 合成 Next Edit 训练样本，无需公开数据集。遵循 NES 论文的三阶段流水线设计。

## 三阶段流水线

### Stage 1：增量差异检测

从 git commits 提取文件级 diff，将每个 commit 的变更拆分为独立的 hunk（连续变更块）。合并算法将重叠的零散编辑聚合为连贯的编辑单元，避免碎片化。

过滤条件（参考 NEP benchmark 论文）：
- 每个 commit 至少 2 个 hunk（单 hunk 无法构成序列）
- 每个 hunk 不超过 5 行变更（过大的变更通常是重构，不是 next edit 场景）
- 整个 commit 的变更跨度不超过 80 行
- 只保留 additive edits（排除纯删除）
- 排除 merge commits

### Stage 2：序列化实例构建

对于时间步 T 到 T+1 的每次转换，构建训练元组：

- `C_T`：编辑前的代码状态（从 parent commit 取文件内容）
- `H_T`：历史编辑轨迹（前 N-1 个 hunk 的 NES diff）
- `L_gt`：真实编辑位置（第 N 个 hunk 的起始行号）
- `E_gt`：真实编辑内容（第 N 个 hunk 的 NES diff）

```python
import subprocess

def extract_edit_sequences_from_git(
    repo_path: str,
    max_commits: int = 5000
) -> list[dict]:
    """从 git history 提取编辑序列训练样本"""

    log = subprocess.run(
        ["git", "-C", repo_path, "log", f"--max-count={max_commits}",
         "--format=%H", "--diff-filter=M", "--no-merges"],
        capture_output=True, text=True
    )
    commits = log.stdout.strip().splitlines()

    samples = []
    for i in range(len(commits) - 1):
        current, parent = commits[i], commits[i + 1]

        diff = subprocess.run(
            ["git", "-C", repo_path, "diff", "--unified=3", parent, current],
            capture_output=True, text=True
        )

        hunks = parse_unified_diff(diff.stdout)

        # 过滤：只保留 2-5 个 hunk 的 commit
        if not (2 <= len(hunks) <= 5):
            continue
        # 过滤：每个 hunk 不超过 5 行变更
        if any(h['changed_lines'] > 5 for h in hunks):
            continue

        # 按 hunk 顺序构建序列
        for target_idx in range(1, len(hunks)):
            history = hunks[:target_idx]
            target = hunks[target_idx]

            samples.append({
                "commit": current,
                "file": target["file"],
                "history": [format_nes_diff(h) for h in history],
                "target_location": target["start_line"],
                "target_edit": format_nes_diff(target),
            })

    return samples
```

### Stage 3：相关性过滤

用 LLM 评估历史编辑和当前编辑之间的因果关系，产出两类样本：

- **-do 样本**：历史编辑为当前预测提供了有效依据（模型应该建议）
- **-keep 样本**：当前编辑与历史无关（模型应该保持沉默）

NEP benchmark 数据：经 GPT-4o mini 过滤后，72.8% 的样本被标记为不相关（filtered out）。

```python
def filter_by_relevance(
    samples: list[dict],
    llm_client,
    model: str = "gpt-4o-mini"
) -> tuple[list[dict], list[dict]]:
    """用 LLM 评估编辑历史与目标编辑的因果相关性"""

    do_samples = []
    keep_samples = []

    prompt_template = """Given the following code edit history and a target edit,
determine if the target edit is causally related to the history.

Edit History:
{history}

Target Edit:
{target}

Is the target edit a logical continuation of the edit history?
Answer YES or NO with a brief explanation."""

    for sample in samples:
        history_text = "\n---\n".join(sample["history"])
        prompt = prompt_template.format(
            history=history_text,
            target=sample["target_edit"]
        )

        response = llm_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=100,
        )

        answer = response.choices[0].message.content.strip()
        if answer.upper().startswith("YES"):
            sample["label"] = "do"
            do_samples.append(sample)
        else:
            sample["label"] = "keep"
            keep_samples.append(sample)

    return do_samples, keep_samples
```

## 数据规模估算

| 来源 | commits | 预期样本量 |
|------|---------|----------|
| 单个中等仓库（10K commits） | 10,000 | 3K-10K |
| 5 个仓库汇聚 | 50,000 | 15K-50K |
| NES 论文参考值 | — | ~200K (Edit), ~60K (Location) |

## 训练方案

### Location Model

- Base model: UniXcoder 或 CodeBERT
- 训练方式: SFT on -do 和 -keep 样本
- 输入: `(C_T, H_T)` 编码为 token sequence
- 输出: 行号分类（-do 样本输出具体行号，-keep 样本输出"不建议"标记）
- 数据量: ~60K 样本

### Generation Model

- Base model: Qwen2.5-Coder-7B
- 训练方式: SFT + DAPO
- 输入: `(C_T, H_T, L_gt)` 格式化为 prompt
- 输出: NES diff 格式的编辑内容
- SFT 数据量: ~200K 样本
- DAPO reward: 1.0 (exact match), 0.5 × ES (similarity > 0.5), -1.0 (otherwise)
