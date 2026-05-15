# NextOne 实施方案可行性评估

## 结论

整体方案**有可行性**，但当前文档更接近“研究型原型方案”，还不能直接作为低风险执行蓝图。核心产品方向成立：独立本地服务 + 薄编辑器插件层的架构合理，Phase 1 以 AST 规则验证交互价值也是正确的收敛方式。

真正的问题不在“能不能做出来”，而在“能不能在足够低的误建议率和足够稳定的编辑器体验下做出可持续可用的版本”。按现有设计，Phase 1 做出 demo 没问题，但要达到稳定可用，至少还需要补上协议一致性、编辑器渲染能力边界、建议触发与撤销机制、评估指标与数据闭环这几块。

## 可行部分

### 1. 总体架构方向正确

[issues/002-architecture.md](/Users/xushengni/project/github/nextone/issues/002-architecture.md) 里的“独立服务进程 + 薄插件层”是合理选择。原因很直接：

- 模型推理资源集中管理，避免每个编辑器重复维护推理逻辑。
- 协议层统一后，VS Code、Neovim、Vim、Zed 只需要做事件采集、渲染和交互。
- 后续无论从本地推理切到远程推理，还是做混合推理，服务层都更容易演进。

这个方向在工程上是可落地的，风险主要不在架构本身，而在协议和状态同步细节。

### 2. Phase 1 的产品验证路径合理

[issues/010-roadmap-and-risks.md](/Users/xushengni/project/github/nextone/issues/010-roadmap-and-risks.md) 里把 MVP 限定为 VS Code、当前文件内、AST 规则 + 本地生成模型，这个收敛方式是对的。

- 先验证“主动建议下一处编辑”是否真有价值，比一开始追求多编辑器覆盖更现实。
- 将跨文件问题延后，能明显降低索引、同步、定位错误等系统复杂度。
- 用规则引擎替代训练型 Location Module，是最快拿到高精度初始样本的方法。

如果目标是 1 个可用原型而不是 1 个完整产品，这条路线可行。

### 3. Location / Generation 双模块拆分成立

[issues/004-location-module.md](/Users/xushengni/project/github/nextone/issues/004-location-module.md) 和 [issues/005-generation-module.md](/Users/xushengni/project/github/nextone/issues/005-generation-module.md) 采用“先定位，再生成”的拆分，这符合 Next Edit 问题本质。相比单模型直接输出编辑，分模块方案更容易：

- 分别优化误命中率和编辑质量。
- 在 Phase 1 用规则高精度兜底。
- 后续替换任一模块时不必重做整套系统。

这部分方案在技术上是自洽的。

## 主要 Issues

### 1. 协议定义不足以支撑真实编辑器状态同步

这是当前文档里最需要先补的点，问题在 [issues/003-protocol.md](/Users/xushengni/project/github/nextone/issues/003-protocol.md)。

现有协议只有 `didChange`、`suggest`、`resolve`、`status`，缺少真实可运行系统必需的上下文控制字段：

- 没有文档版本号。编辑器持续输入时，服务端很容易基于旧版本代码生成建议，客户端无法判断 suggestion 是否过期。
- 没有打开/关闭文档、切换活动编辑器、全文快照同步机制。只传增量 change 不足以保证服务端状态永远正确。
- 没有建议失效机制。用户继续输入、撤销、保存、格式化后，旧 suggestion 何时自动撤销，协议里没有定义。
- 没有并发语义。多个文件同时改动时，当前 `id` 只是 suggestion id，不足以表达“基于哪个版本的哪个文档生成”。

这不是小问题。没有版本化协议，Phase 1 即使 demo 看起来能跑，实际使用时也会频繁出现建议错位。

### 2. “插件极薄”这个判断对 Vim 系不成立

[issues/002-architecture.md](/Users/xushengni/project/github/nextone/issues/002-architecture.md) 认为插件层可以保持“几百行代码”，对 VS Code 可能成立，但对 Vim / Neovim 方案明显低估了复杂度，参考 [issues/007-editor-vim-neovim.md](/Users/xushengni/project/github/nextone/issues/007-editor-vim-neovim.md) 和 [issues/008-keybinding.md](/Users/xushengni/project/github/nextone/issues/008-keybinding.md)。

主要原因：

- Vim 和 Neovim 的通信能力、渲染能力、按键模型差异很大，实际上需要两套插件设计，不是“同一层薄适配”。
- 终端下 `Cmd+;` 依赖外部终端配置和 CSI u 转义，部署成本高，失败路径多。
- Vim 的 popup/textprop 渲染不是 inline diff 的等价实现，交互体验会显著弱于 VS Code / Neovim。

所以多编辑器扩展是可行的，但 Phase 2 预计周期偏乐观，而且 Vim 适配不应与 Neovim 绑定估时。

### 3. Phase 1 的生成链路延迟预算偏乐观

[issues/005-generation-module.md](/Users/xushengni/project/github/nextone/issues/005-generation-module.md) 里提到 Mac M-series 上可以做到 500ms 以内，这个判断只考虑了 token 生成速度，没有完整计入：

- 事件去抖与聚合。
- 规则匹配 / AST 增量解析。
- prompt 组装。
- 模型首 token 延迟。
- suggestion 渲染与旧 suggestion 清理。

如果不做强约束触发，实际交互延迟更可能落在 700ms 到 2s 之间。对于“主动冒出来的建议”，2s 左右已经很容易打断用户。

因此 Phase 1 不是不能做，而是必须把触发条件收得非常窄，例如只在明确 rename propagation / signature propagation 场景触发，而不是每次编辑后都尝试推理。

### 4. Location Module 对 tree-sitter 能力的预期过高

[issues/004-location-module.md](/Users/xushengni/project/github/nextone/issues/004-location-module.md) 把 rename propagation、接口变更传播、import 补全都放进 AST 规则引擎，这里面只有一部分是 tree-sitter 单独能稳定完成的。

具体问题：

- tree-sitter 提供的是语法树，不是语义引用解析。单靠 AST 很难可靠地区分同名符号、作用域遮蔽、动态导入、重载、别名导入等情况。
- import 补全本质依赖符号解析和项目索引，不只是“检测到未解析引用”这么简单。
- 跨文件“查找定义 → 查找所有引用”更接近 language server / indexer 能力，不是简单 tree-sitter query 就能替代。

结论不是这块不可做，而是 Phase 1/2 里应该把表述从“tree-sitter 符号引用分析”改成“基于语言服务或索引器的有限符号分析”，否则实施时会遇到能力断层。

### 5. 训练数据路线可研究，但短期内不构成稳妥产品路径

[issues/009-training-data.md](/Users/xushengni/project/github/nextone/issues/009-training-data.md) 的三阶段数据构建方法在研究上成立，但作为近期产品路线存在两个现实问题：

- commit history 和真实交互式编辑序列差距很大，尤其缺失“停下来不建议”的上下文信号。
- 方案高度依赖 LLM relevance filtering，数据成本、标注一致性、复现实验成本都不低。

这意味着 Phase 3 不是自然延伸，而是一个新的研究项目。当前 roadmap 把它写成 4-8 周，更像理想时间，不像实际可承诺时间。

### 6. 缺少明确评估指标，无法判断 MVP 是否成功

文档里谈了 benchmark 和论文指标，但缺少产品执行必须的在线评估定义，尤其是：

- suggestion trigger rate：每小时触发多少次。
- acceptance rate：触发后被接受的比例。
- stale rate：建议生成时已过期或渲染时已失效的比例。
- annoyance rate：用户主动关闭功能、连续拒绝、快速 dismiss 的比例。
- latency p50 / p95：从最后一次编辑到建议可见的延迟。

没有这些指标，Phase 1 即使做完，也只能得到“感觉还行/不太行”的主观反馈，无法支撑 Phase 2 是否继续投入。

## 可行性判断

### 短期可行

以下目标是现实可落地的：

- 只做 VS Code。
- 只做当前文件内建议。
- 只覆盖少数高置信场景：重命名传播、函数签名改动后的局部调用点修复。
- 用 AST / 文本规则定位，用模型只负责小范围 diff 生成，或者干脆模板化生成。
- 接受率评估优先于覆盖率扩张。

这个范围内，做出可用 MVP 是可行的。

### 中期有条件可行

以下目标不是不能做，但需要额外前提：

- Neovim 支持可行，但应晚于 VS Code 稳定后再接入。
- 跨文件支持可行，但前提是先引入项目级索引或复用现有 language server 结果。
- 训练型 Location Module 可研究，但前提是先跑通评估体系并证明规则方案天花板已到。

### 当前阶段不宜承诺

以下目标现在不适合当作确定性交付承诺：

- Vim 8/9 的产品级体验一致性。
- Phase 3 在 4-8 周内完成专用模型训练并达到生产级质量。
- 仅依赖 tree-sitter 实现稳定的跨文件符号传播。

## 建议的修订方向

### 1. 重写 Phase 1 范围

建议把 MVP 明确改成：

- 单编辑器：仅 VS Code。
- 单文件：不做跨文件。
- 单触发器：rename propagation、signature propagation 两类。
- 单交互：展示、接受、拒绝、自动失效。
- 单目标：验证接受率和干扰率，而不是覆盖更多场景。

### 2. 先补协议，再写插件

协议至少补齐以下字段或消息：

- 文档版本号。
- `didOpen` / `didClose` / `didSave` 或等价生命周期事件。
- suggestion 所基于的 `uri + version`。
- `cancelSuggestion` 或客户端本地自动失效规则。
- 全量同步兜底机制，避免增量漂移。

### 3. 把 Location 的实现表述降级为“规则 + 索引”

不要把 tree-sitter 说成完整语义分析方案。更稳妥的定义应该是：

- Phase 1：语法规则 + 局部文本分析。
- Phase 2：语言服务结果 / 项目索引辅助的符号传播。
- Phase 3：学习型 retriever 作为补充，而不是替代一切。

### 4. 增加 MVP 成功门槛

建议在 roadmap 中加硬性阈值，例如：

- p95 延迟 < 1.2s。
- 接受率 > 25%。
- 连续拒绝后自动降频。
- 用户可一键关闭 proactive suggestions。

这些门槛比论文指标更接近产品真实成败。

## 最终判断

这套方案**方向正确、原型可做、产品化难度高于文档当前估计**。

如果目标是“尽快验证 Next Edit 交互是否值得继续投入”，方案是可行的，但必须收窄到一个更严格的 MVP。若按当前文档直接推进多编辑器、跨文件、训练数据和专用模型，实施风险会明显偏高，且阶段目标之间耦合过强，容易在 Phase 2 前后陷入工程和研究同时失控的状态。

更准确的执行策略应当是：**先把 VS Code 单文件高置信建议做准，再决定是否值得扩展到 Vim/Neovim 和训练路线。**
