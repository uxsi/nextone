# parse_nes_diff 中 strip() 去掉了代码缩进

严重度：中

## 现象

用户接受 rename 建议后，替换后的代码行失去了原有的缩进。例如 `    hello("world")` 变成 `hello("world")`。

## 根因

NES diff 格式中每行的结构是 `{line_num}-| {content}`，`|` 和 content 之间有一个空格作为分隔符。`parse_nes_diff` 用 `split("-|", 1)` 拆分后，对 `parts[1]` 调用 `strip()` 去掉前后空白。

当代码本身有缩进时，`parts[1]` 是 `" {indent}{code}"`（1 个分隔空格 + N 个缩进空格 + 代码）。`strip()` 把所有前导空格一起去掉了。

```python
# 修复前
def parse_nes_diff(diff_text: str):
    deleted = []
    added = []

    for line in diff_text.strip().splitlines():
        line = line.strip()
        if not line:
            continue

        if "-|" in line:
            parts = line.split("-|", 1)
            try:
                num = int(parts[0].strip())
                text = parts[1].strip() if len(parts) > 1 else ""  # ← strip() 去掉了缩进
                deleted.append({"num": num, "text": text})
            except ValueError:
                continue
        elif "+|" in line:
            parts = line.split("+|", 1)
            try:
                num = int(parts[0].strip())
                text = parts[1].strip() if len(parts) > 1 else ""  # ← 同上
                added.append({"num": num, "text": text})
            except ValueError:
                continue

    return deleted, added
```

具体数据流：

```
输入:  "4-|     hello(\"world\")"
split("-|", 1) → ["4", "     hello(\"world\")"]
parts[1] = "     hello(\"world\")"   # 1个分隔空格 + 4个缩进空格 + 代码
parts[1].strip() = "hello(\"world\")"  # 缩进全丢
```

## 修复

新增 `_remove_separator_space()` 函数，只去掉 NES 格式中 `|` 后的 1 个分隔空格，保留代码缩进：

```python
# 修复后
def _remove_separator_space(s: str) -> str:
    """Remove exactly one leading space (the NES format separator after '|')."""
    if s.startswith(" "):
        return s[1:]
    return s


def parse_nes_diff(diff_text: str):
    # ...
    for line in diff_text.strip().splitlines():
        line = line.strip()
        if not line:
            continue

        if "-|" in line:
            parts = line.split("-|", 1)
            try:
                num = int(parts[0].strip())
                text = _remove_separator_space(parts[1]) if len(parts) > 1 else ""
                deleted.append({"num": num, "text": text})
            except ValueError:
                continue
        elif "+|" in line:
            parts = line.split("+|", 1)
            try:
                num = int(parts[0].strip())
                text = _remove_separator_space(parts[1]) if len(parts) > 1 else ""
                added.append({"num": num, "text": text})
            except ValueError:
                continue
    # ...
```

修复后的数据流：

```
输入:  "4-|     hello(\"world\")"
split("-|", 1) → ["4", "     hello(\"world\")"]
parts[1] = "     hello(\"world\")"         # 1个分隔空格 + 4个缩进空格 + 代码
_remove_separator_space → "    hello(\"world\")"  # 只去掉1个分隔空格，保留4个缩进空格
```

新增测试用例 `test_parse_nes_diff_preserves_indentation` 验证：

```python
def test_parse_nes_diff_preserves_indentation():
    diff_text = """4-|     hello("world")
4+|     goodbye("world")"""

    deleted, added = parse_nes_diff(diff_text)
    assert deleted[0]["text"] == '    hello("world")'
    assert added[0]["text"] == '    goodbye("world")'
```
