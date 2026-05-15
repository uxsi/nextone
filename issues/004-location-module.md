# Location Module 设计

## 职责

接收当前代码状态 `C_T` 和历史编辑轨迹 `H_T`，输出预测的下一个编辑行号 `L_{T+1}`。

## Phase 1：tree-sitter AST 规则引擎

不训练任何模型，用 AST 分析覆盖最高频的 next edit 场景。覆盖率取决于项目代码风格和语言特性，需通过 Phase 1 实测验证。延迟在 10ms 级别。

**能力边界**：tree-sitter 是语法解析器，提供 CST/AST，不做符号解析、作用域分析、类型推断。以下规则仅覆盖语法层面可判定的场景，遇到作用域遮蔽、别名导入、动态引用、方法重载等语义层面的情况会产生误判或遗漏。

### 场景 1：符号重命名传播

用户把 `function hello()` 改名为 `goodbye()`，tree-sitter 扫描 AST，找到所有引用 `hello` 的 `call_expression` 节点，按文件顺序返回下一个需要修改的位置。

**局限性**：仅覆盖同文件内的简单引用。不处理作用域遮蔽（内层作用域定义了同名变量）、别名导入（`import { hello as h }`）、动态引用（`obj[funcName]()`）。

```python
import tree_sitter_languages as tsl

def find_rename_propagation(
    old_name: str,
    new_name: str,
    tree,
    source_bytes: bytes,
    language: str
) -> list[int]:
    """在 AST 中查找所有引用旧名称的位置，返回行号列表"""
    query = tsl.get_language(language).query(f"""
        (call_expression function: (identifier) @fn (#eq? @fn "{old_name}"))
    """)
    captures = query.captures(tree.root_node, source_bytes)
    return sorted(set(node.start_point[0] for node, _ in captures))
```

### 场景 2：重复模式检测

用户在 struct 中添加了一个字段 `session_id`，然后在 `new()` 方法中初始化了它。检测到"添加字段 → 初始化字段"的模式后，扫描其他方法（`serialize`、`validate`），预测它们也需要处理 `session_id`。

### 场景 3：import 补全（Phase 2 目标）

用户在代码中引用了一个新符号，检测到未解析的引用，预测文件头部需要添加 import 语句。

**Phase 1 不实现此场景**。import 补全本质依赖符号解析和项目索引——需要知道哪些符号在项目中可用、从哪个模块导出，单靠 tree-sitter 的语法树无法可靠完成。Phase 2 引入项目索引或复用 language server 结果后再实现。

### 场景 4：接口变更传播

修改了函数签名（添加/删除参数），tree-sitter 查找同文件内的所有调用点，按文件顺序返回需要修改的位置。

**局限性**：Phase 1 限定在当前文件内。跨文件的调用点查找依赖项目级索引，属于 Phase 2 范围。

## Phase 2：fine-tuned retriever 模型

在 UniXcoder 或 CodeBERT 基础上 fine-tune，输入编辑历史 embedding，输出行号概率分布。

训练数据从本地 git history 合成（详见 issue #008）。

NES 论文的数据：
- SFT 前位置准确率：10.1%
- SFT 后位置准确率：62.8%
- SFT + DAPO 后：77.7%（-do）/ 85.0%（-keep）

**-keep 能力的重要性**：Location Module 必须学会"什么时候不建议"。Phase 1 通过高置信度阈值（只在明确模式匹配时触发）缓解误建议问题。Phase 2 通过训练数据中的 -keep 样本显式建模这个能力。

## 历史窗口设计

NES 论文实验表明历史窗口长度为 3 时效果最佳（72.6% -do 平均准确率）。服务端维护一个滑动窗口，保留最近 3 次编辑的 diff 序列。

## 跨文件定位

- Phase 1：限定在当前文件内
- Phase 2：语言服务结果 / 项目索引辅助的有限符号传播（复用现有 language server 的符号引用结果，而非自建索引）
- Phase 3：学习型 retriever 作为补充，覆盖规则和索引无法触及的模式
