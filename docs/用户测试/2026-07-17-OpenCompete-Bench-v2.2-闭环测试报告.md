# OpenCompete Bench v2.2 闭环测试报告

日期：2026-07-17  
数据集：`opencompete-bench-v2 2.2.0-beta.4`  
Judge 契约：`opencompete-competitive-judge-v2.2.3`

## 一、结论

OpenCompete 已形成一套面向真实竞品研究执行的本地 Bench：使用固定 `ResearchRequest` 和固定冻结 `Scope`，直达正式 OpenCompete Graph 的研究阶段，并继续复用正式 ODR、Prompt 构造、`materialize`、`validate`、`ApplicationRunner` 和 `RunRepository`。系统没有创建第二套 Supervisor、Researcher 或研究 Graph。

八个案例已经达到“AI 专家双审、可审计的 provisional Reference”标准，可以进入 beta 回归；不能表述为“经过人类专家认证的完整行业金标准”。001、002、004、005、006 是正向质量锚点，003 是 mixed 诊断案例，007、008 是证据稀缺 challenge 案例。

案例 006 的第二次真实研究成功完成，正式硬门禁从第一次运行的 27 个错误降为 0。正式模型 Judge 的短请求可以连通，但完整长载荷仍分别出现 Provider 暂时不可用或无效 JSON，系统正确保存为 `judge_failed`，没有伪造五维分或 FACT 分。作为当前缺少人类评审的替代通道，独立 AI 竞品分析师给出了 provisional 量化评分；该评分与正式 Judge 结果严格分开。

## 二、确认需求、删除后果与最小边界

| 确认需求 | 删除该设计的后果 | 采用方式 |
|---|---|---|
| 固定输入绕过候选发现和确认 UX | 无法稳定复现实验，DRB 优化效果会被发现阶段随机性污染 | `ApplicationRunner.create_fixed_scope_run` 注入固定冻结 Scope |
| 必须运行正式研究内核 | 测试可能优化旁路，而非真实产品 | 复用正式 `deep_researcher_builder`、materialize、validate 和持久化 |
| 竞品研究专用质量构念 | 官方四维只能测通用报告质量，不能充分发现错口径、因果外推和无条件建议 | 五个竞品研究维度与案例级 Criteria |
| 证据、Unknown 和范围门禁 | 模型可通过少写、隐藏缺口或写漂亮文本取得高分 | 独立 requirement coverage、Claim-Evidence 和硬门禁 |
| Judge 偏差可检测 | 单顺序或单次评分可能把位置偏差和随机漂移当成版本效果 | 匿名双顺序、至少两次独立重复、稳定性门槛 |
| Reference 可审计 | 无法判断所谓 Gold 是否真实成熟 | AI 专家作者、独立复核、来源定位、摘录哈希和内容哈希 |

最小自研边界仅包含固定案例契约、匿名候选投影、本地聚合、FACT/Unknown/硬门禁和校准器。研究生命周期、搜索、Supervisor/Researcher、持久化和正式报告发布全部复用现有系统。

## 三、官方 DRB/RACE 机制

官方 evaluator 固定对照提交为 `469cce54ea7f6a63c163d3d9fec879cf289ec484`。机制为：

```text
每题动态 Criteria + 两层隐藏权重
→ Target 固定为 article_1，Reference 固定为 article_2
→ Judge 逐 Criterion 对两篇文章给 0–10 分
→ 本地按维内权重和维度权重聚合
→ Overall = target / (target + reference)
```

官方四维为 comprehensiveness、insight、instruction_following、readability。官方没有正式 Scope 门禁、Claim-Evidence 支持、关键 Unknown、双顺序稳定性或结构化产物校验；失败任务只从成功任务均值中消失，因此必须同时报告完成率。

OpenCompete 保留案例级 Criteria、Judge 隐藏权重、成对评分和本地确定性聚合，增加竞品研究专用层。

## 四、专用 Criteria 与权重

| 维度 | 权重 |
|---|---:|
| decision_relevance | 0.15 |
| comparison_rigor | 0.25 |
| causal_synthesis | 0.20 |
| evidence_grounding | 0.25 |
| recommendation_quality | 0.15 |

每案定义 10–15 条 Criterion，每维 2–3 条，包含 2–4 条关键 Criterion，并使用案例化的 `0/4/6/8/10` 锚点。一级权重统一，维内权重按案例决策风险配置。质量分、requirement coverage、硬门禁、完成率和成本分别报告，不合成不可解释的单一总分。

初始资格门槛：

```text
critical requirement coverage >= 0.80
major requirement coverage >= 0.60
important target claim support >= 0.90
claim-evidence relationship support >= 0.90
critical contradiction count == 0
unsupported important target claims == 0
critical Unknown preservation == 1.00
```

## 五、v2.2 Judge 与校准修正

v2.2 完成以下修正：

1. Target 和 Reference 在进入质量 Judge 前投影为完全相同的最小 schema。
2. 原始 Claim、Evidence、Source ID 及报告中的引用 ID 被替换为调用内 opaque ID。
3. Source 标题、发布者、URL 等可能暴露 Reference 身份的侧信道不进入质量 Judge。
4. Locator 使用 `kind + opaque identifier` 联合校验，返回后在本地恢复真实定位。
5. FACT coverage 以案例 `fact criteria` 声明的 requirements 为真相源，analysis 和 recommendation 不再被静默遗漏。
6. `critical` FACT Criterion 会实际提升其 requirements 的门禁等级。
7. `NONFACTUAL_ANALYSIS` 不能为重要 Target Claim 绕过证据支持门禁。
8. Claim 级 `evidence_ids` 必须完整且不重复地覆盖 Bundle 声明的 Evidence。
9. 校准变体使用唯一报告锚点替换并同步替换指定 Reference Claim，不再追加自相矛盾的句子。
10. 五类语义缺陷必须 5/5 通过；style-only 同时限制 Overall、维度和关键 Criterion 漂移。
11. 每个 baseline 和 variant 至少两次独立双顺序评分，跨调用漂移超过 0.25 即失败。
12. 校准 `input_order_id` 使用 opaque SHA-256 标识，不泄露缺陷类型、baseline 或候选位置。

最终独立代码复审结果：P0 = 0、P1 = 0、P2 = 0。

## 六、八案 Reference 专家审核

| 案例 | 模式 | 专家结论 |
|---|---|---|
| 001 AWS Lambda vs Azure Functions | positive | 强锚点；仍需注意四类工作负载矩阵覆盖 |
| 002 Microsoft 365 vs Google Workspace | positive | 成熟采购框架；实际 entitlement 尽调仍需企业数据 |
| 003 Visa vs Mastercard | mixed | 适合监管证据不足下的 RFP 诊断，不是事实完备金标准 |
| 004 UPS vs FedEx | positive | 八案中最接近专业采购报告的案例之一 |
| 005 Kindle vs Kobo | mixed/positive | 研究设计成熟，但未实际执行固定书单体验测试 |
| 006 Spotify vs Apple Music | positive | 价格、TCO、音频链路、推荐和迁移实验设计成熟 |
| 007 Supercharger vs Ionity | challenge | 只测证据稀缺时不编造并继续推进决策 |
| 008 云基础设施竞争 | challenge | 适合企业云 RFP/退出演练，不是三云事实完备报告 |

所有案例均为 AI 专家作者和独立 AI 专家复核，`unresolved_material_issues = 0` 只表示当前 Rubric 内问题已关闭，不代表人类行业专家认证。

## 七、真实研究闭环

### 第一次运行

```text
run_id: e109c339-860f-4c23-945a-b2b5694e5f2b
status: completed
sources: 22
evidence: 24
claims: 48
comparisons: 11
supported claims: 29
unknown claims: 19
tavily calls: 16
cost_usd: 0.931620
```

硬门禁发现 27 个 `important_claim_not_in_report`。根因是自动生成的 Comparison 覆盖了实体，却没有覆盖同一范围单元内的全部重要 Claim。这证明门禁能够发现“报告看似完成、结构化重要结论未发布”的真实产品缺陷。

### 修复后第二次运行

```text
run_id: 821d9414-36f8-4120-a018-8c57738e74f5
status: completed
sources: 20
evidence: 22
claims: 25
comparisons: 12
supported claims: 16
unknown claims: 9
contradicted claims: 0
input_tokens: 131,861
output_tokens: 40,430
tavily_calls: 19
cost_usd: 0.967162
hard-gate violations: 0
```

第二次运行证明固定 Scope 可以通过正式 Graph、共享 ODR、materialize、validate 和 RunRepository 完成发布；修复后 `important_claim_not_in_report = 0`，`scope_cell_without_claim_or_unknown = 0`。

相对第一次运行，成本增加约 3.8%，仍低于领域包候选门槛允许的 20%；但两次运行的 Claim 数量和搜索结果不同，不能据此宣称质量版本提升，正式版本比较仍需每案至少三次独立运行。

## 八、正式 Judge 真实结果

对第二次运行执行了三类连通与真实评测：

1. `anthropic:deepseek-v4-flash`，8k 输出：返回内容无法通过严格质量 JSON 契约。
2. 同模型，16k 输出：仍返回无效质量 JSON，排除单纯输出上限不足。
3. `openai:gpt-5.6-sol`：16-token 最小请求成功返回 `CONNECTED`；一次完整长载荷仍出现“模型 Provider 暂时不可用”。

正式持久化结果示例：

前几次 v2.2/v2.2.2 结果分别因 Provider 暂时不可用、JSON 引号、locator kind 和字段类型错误而失败；这些失败均未伪造分数。v2.2.3 已增加合法 locator 枚举、修复反馈重试、数组/数字类型示例、安全错误摘要，以及统一的 excerpt SHA256。

### v2.2.3 成功正式 Judge

同一真实 Bundle 使用 `anthropic:deepseek-v4-flash`、16k 最大输出和三次结构修复重试后，质量与 FACT Judge 均成功完成：

```text
evaluation: eval-v2-8c687e61-b511-42bb-b454-e6b426caf62d.json
status: ineligible
hard gates: passed, 0 violations
quality: completed
target overall: 6.66625
reference overall: 8.89875
relative to reference: 0.4282846
quality stability: unstable
FACT: completed, eligible=false
critical requirement coverage: 0.3823529
important target claim support: 0.7368421
claim-evidence relationship support: 0.9259259
critical Unknown preservation: 0.0
critical contradiction count: 1
unsupported important target claims: 5
```

该结果的 `ineligible` 是正确的业务评测结论，不是 Judge 运行失败。主要原因是双顺序在决策相关性、比较严谨性、因果综合和建议质量上不稳定；FACT 发现关键 Requirement 覆盖不足、一个关键矛盾和五个重要 Target Claim 不受支持。硬门禁仍为零违规，说明结构化范围和发布完整性修复有效，但内容研究质量尚未达到 Reference 门槛。

独立诊断任务进一步确认：一次 16k 隔离请求正常 `end_turn`，输入约 27,036 tokens、输出 9,322 tokens，首个错误是 JSON 字符串中的未转义中文双引号；随后还发现模型自创 `report_section`、`claim_id`、`source_id` locator kind。该证据排除了单纯截断。诊断脚本不保存请求正文、Header、Base URL 或秘密，位于 [diagnose_competitive_judge_response.py](D:/web-code/OpenCompete/opencompete/benchmarks/diagnose_competitive_judge_response.py)。

## 九、AI 专家 provisional 量化

作为正式 Judge 之外的独立交叉检查，由独立专业竞品分析 Agent 只读审核案例 006。该结果不能写入正式 evaluation，也不能冒充人类审核。

首轮专家给出 Target 5.531；第二名专家独立复核后发现首轮把 Target Evidence schema 错当成 Reference 摘录哈希 schema，造成证据维度过度扣分。最终采用双审后的 provisional 数字：

| 维度 | Target | Reference |
|---|---:|---:|
| decision_relevance | 5.250 | 9.000 |
| comparison_rigor | 5.650 | 9.325 |
| causal_synthesis | 6.750 | 9.250 |
| evidence_grounding | 5.750 | 9.850 |
| recommendation_quality | 5.975 | 9.675 |
| Overall | 5.884 | 9.445 |

相对 Reference 为 `5.88375 / (5.88375 + 9.445) = 0.3838`。

关键 requirement：完整覆盖 4/10、部分覆盖 5/10、未覆盖 1/10；部分支持按 0.5 计时为 65%。主要缺口是 Spotify/Apple 完整价格、全套餐两年 TCO、迁移验证指标和可逆退出计划。因果克制与关键 Unknown 保留表现相对较好。

第二名专家确认 22/22 Target Evidence 均有非空 `excerpt`、`location` 和 `source_id`，不能表述为“没有定位”；但定位均为粗粒度 `Tavily SUMMARY`。复核还发现 Target Evidence 业务契约没有 Reference 构建阶段使用的 `quote_sha256` 字段。v2.2 随后在匿名 Judge 投影层对两侧 excerpt 统一计算 `excerpt_sha256`，既保持正式业务契约不变，也消除了 Reference 独占字段造成的不公平。12/12 Comparison 的 `commonalities` 和 `differences` 均为空，其中 5 条只有补偿性 Unknown gap，说明结构覆盖已经通过，但成熟比较综合仍明显不足。

上述数字是两名 AI 专家的 provisional 双审结果，不是正式 Judge 分，也不是人类专家认证。

## 十、优化闭环：评测 → 定方案 → 实施 → 复测

### 基线诊断

基线 `821d9414-36f8-4120-a018-8c57738e74f5` 的硬门禁已经通过，但正式 Judge 发现内容执行瓶颈：Comparison 的 `differences/commonalities` 全空、关键覆盖 38.2%、重要 Claim 支持 73.7%、1 个关键矛盾、5 个重要 Claim 不受支持，且双顺序不稳定。根因审查定位到研究输入没有逐范围覆盖清单、Extractor 一次性自由抽取，以及 materialize 在 `_sanitize_comparisons` 中无条件清空模型生成的比较文本。

### O01：覆盖驱动 Prompt + 结构化 Comparison 修复

O01 不注入任何 Spotify、Apple、价格或预设答案，只做通用优化：

- 在正式研究输入中生成逐 `entity × level × dimension` 覆盖清单。
- 要求每个单元交付证据结论或显式 Unknown，并要求逐比较维度输出差异、共同点和缺口。
- Extractor 接收同一覆盖清单和 Comparison 契约。
- materialize 保留已通过候选契约的 `differences/commonalities/gaps`，不再静默清空。

同一固定案例、相同数据集和正式 Graph 的 O01 结果：

| 指标 | 基线 | O01 | 变化 |
|---|---:|---:|---:|
| Target Overall | 6.666 | 7.519 | +0.853 |
| relative to Reference | 0.4283 | 0.4797 | +0.0514 |
| critical coverage | 38.2% | 58.8% | +20.6pp |
| important Claim support | 73.7% | 100.0% | +26.3pp |
| relationship support | 92.6% | 100.0% | +7.4pp |
| unsupported important Claims | 5 | 0 | -5 |
| differences 非空 | 0/12 | 11/11 | 候选 100% |
| commonalities 非空 | 0/12 | 9/11 | 候选 81.8% |
| 成本 | $0.9672 | $0.8221 | -15.0% |
| Tavily calls | 19 | 16 | -15.8% |

O01 证明优化方向有效，但仍未通过资格：Unknown preservation 为 0、关键矛盾为 1、Judge 不稳定。因此 O01 是“有效改善但未达标”，不能写成系统已通过。这里基线和候选的 Comparison 数量分别为 12 和 11，非空率使用各自实际 Comparison 数作分母。

### O02：声明为预算实验，但运行身份无效

O02 原计划只提高通用 `max_researcher_iterations=3` 和 `max_react_tool_calls=6`，不添加领域答案。但事后核对发现，run `01e7eb87-3ca2-4c15-943d-b7203c028217` 的 Manifest 与 Checkpoint 实际仍记录 `max_researcher_iterations=2`、`max_react_tool_calls=4`；它与 O01 同为 2 个 Research Unit、8 次 Researcher 模型轮次和 16 次 Tavily 调用。因此以下数字只能作为同配置独立复跑的观测，不能归因于预算上限变化：

| 指标 | O01 | O02 | 变化 |
|---|---:|---:|---:|
| Target Overall | 7.519 | 7.672 | +0.153 |
| critical coverage | 58.8% | 61.8% | +3.0pp |
| important Claim support | 100.0% | 86.7% | -13.3pp |
| relationship support | 100.0% | 96.3% | -3.7pp |
| contradictions | 1 | 0 | -1 |
| unsupported important Claims | 0 | 0 | 0 |
| Judge stability | unstable | unstable | 未改善 |
| 成本 | $0.8221 | $0.8439 | +2.7% |

该复跑未通过资格，且重要 Claim 支持跌破 90%。由于声明参数与实际运行参数不一致，O02 不具备实验身份，必须从预算策略的因果比较中排除。它既不能证明增加预算有效，也不能证明增加预算有害。后续实验必须在运行前预注册 Agent 额度，并在汇总结论前核对 Manifest、RunnableConfig 与 Checkpoint；不能以更高 Overall 掩盖事实支持和稳定性退化。两次真实运行都只是单案例 pilot，不能据此宣称全 Bench 统计显著提升。

### O03：Agent 执行控制修复正式 Pilot

O03 不增加案例事实、竞品名称、价格、Reference Claim 或领域答案。它只修改共享 Researcher 的通用执行控制：分离模型轮次、研究工具和反思计数，允许反思后继续搜索，增加稳定终止原因、并行额度截断和预算耗尽零调用短路。正式固定 Scope run 为 `a7a265e6-8013-43dc-8231-efcb1e020bf1`，评测为 `eval-v2-0f24d29f-5bbb-4c44-b6e8-68bb9ee73c25`。

实验身份核对通过：Manifest 与 34 条 Checkpoint、4 个 namespace 均记录 `max_researcher_iterations=3`、`max_react_tool_calls=6`、`max_research_tool_calls=3`、`research_model=openai:gpt-5.6-sol`。入口为 `fixed_scope_after_freeze_scope`，案例绑定为 `ocb-006-spotify-vs-apple-music`。研究经正式 Runner、共享 `deep_researcher_builder`、SQLite Checkpoint、materialize、validate 和 RunRepository 完成发布。

Judge 沿用 O01 的 `anthropic:deepseek-v4-flash`、16k 最大输出和 `opencompete-competitive-judge-v2.2.3` 契约。首次未配置 Judge 的 `eval-v2-dc02b03a-cdb9-479f-9520-70f2fc13f439` 仅为 `judge_failed/not_run` 审计记录，不进入比较。

| 指标 | O01 | O03 | 变化 | 门槛 |
|---|---:|---:|---:|---:|
| Target Overall | 7.519 | 6.942 | -0.577 | 相对 O01 至少 +0.5 |
| relative to Reference | 0.4797 | 0.4346 | -0.0451 | 仅作配对辅助 |
| critical coverage | 58.8% | 61.8% | +3.0pp | 至少 80% |
| important Claim support | 100.0% | 100.0% | 0 | 至少 90% |
| relationship support | 100.0% | 100.0% | 0 | 至少 90% |
| critical Unknown preservation | 0 | 66.7% | +66.7pp | 100% |
| contradictions | 1 | 0 | -1 | 0 |
| unsupported important Claims | 0 | 0 | 0 | 0 |
| hard gates | passed | passed | 无回归 | 必须通过 |
| Judge stability | unstable | unstable | 未改善 | 必须稳定 |
| 研究成本 | $0.8221 | $1.2141 | +47.7% | 增幅不超过 20% |
| Tavily calls | 16 | 30 | +87.5% | 单独展示 |

O03 的研究输入为 181,848 tokens，输出为 31,295 tokens；CLI 观察耗时约 986 秒。研究产物包含 33 个 Source、37 个 Evidence、22 个 Claim 和 11 个 Comparison。上述成本不包含 Judge 调用费用，不能把评测开销冒充研究成本。

FACT 显示改进集中在诚实保留未知和避免矛盾：推荐质量、迁移质量两个关键 Unknown 已保留，关键矛盾降为零。仍缺失或仅部分支持的关键内容包括 Apple Music 三类套餐确切价格、Spotify Student/Duo/Family 确切价格、完整双方两年 TCO 与价差、Apple 迁移替代版本审核、迁移地区限制，以及固定 200 首曲目和设备链路的显式 Unknown。质量 Judge 的最低项是价格证据 `eg-price-quotes=4.25` 和价格比较 `cr-price=5.5`。

O03 结论为 **ineligible**：执行控制修复是必要的正确性修复，但单纯增加可执行搜索机会没有形成缺口定向闭环，不能称为质量优化成功。它以 47.7% 的成本增幅只换得 3.0pp 关键覆盖提升，同时 Overall 下降 0.577，失败于质量、稳定性和成本三类门槛。该结果仍是单案例单次 Pilot，不支持统计显著性声明。

方案 B 的进入条件已经满足：`critical coverage < 80%`，且 Researcher 在增加工具额度后仍未把已知关键缺口转化为定向任务。下一轮若继续，应设计通用 Typed Coverage Ledger，让 Supervisor 按冻结 Scope 的未闭合 requirement 调度，不增加第二套 Agent/Graph，不扩充生产领域包，也不写入案例答案。

### 优化决策

当前保留 O01 的通用覆盖与结构化 Comparison 修复，O02 因实验身份无效而不进入优化决策。Checkpoint 进一步显示 Researcher 按 `search -> think -> search -> think -> compress` 运行，最后一次反思识别缺口后因模型轮次上限直接压缩；下一轮先修复搜索额度、反思次数和模型硬上限的执行语义，再评估是否需要 Coverage Ledger。仍须处理通用 Unknown 语义、维度别名定义和 Judge 稳定性；不允许向代码注入案例事实或硬编码答案。`hard gates passed` 只表示结构化产物可评测，不等于 `quality eligible` 或优化通过。正式通过标准仍为：critical coverage ≥80%、important support ≥90%、Unknown preservation=1、contradiction=0、hard gates 不下降、Overall 至少提升 0.5 且需要多案例重复运行支持。

## 十一、领域包结论

当前不扩充正式运行时领域包。八案已经证明“通用层级 + 案例自定义维度”可以承载云函数、协作套件、支付网络、物流、阅读生态、音乐、充电网络和企业云。

当前应扩充的是 Benchmark 元数据，而不是正式研究 Prompt：

- `archetype`：B2B 采购、受监管网络、消费订阅、基础设施等。
- 必答决策单元。
- 最低双边证据覆盖。
- 领域特有硬门禁。
- positive、mixed、challenge 分层聚合。

只有至少三个同领域案例、每案至少三次独立运行持续出现同类漏检，且新增领域包满足以下条件，才进入正式系统：

```text
Overall 配对提升 >= 0.5
critical requirement coverage 提升 >= 10pp
硬门禁通过率不下降
成本增幅 <= 20%
bootstrap 95% CI 不跨 0
```

## 十二、验证结果

```text
ruff check .: passed
mypy src/opencompete: 35 source files passed
pytest tests/opencompete: 295 passed, 1 deselected
git diff --check: passed
独立最终代码复审: P0=0, P1=0, P2=0
```

## 十三、完成边界

已经完成：正式固定 Scope 直达路径、八案 Reference、专用 Criteria/权重、匿名双顺序 Judge、requirement/证据/Unknown 门禁、结构化缺陷变体、独立重复稳定性、真实研究闭环、成本与产物指标、专家 provisional 审阅和领域包取舍。

仍属 beta 的外部限制：v2.2.3 已成功完成一个真实案例的正式 Judge，但该案例因双顺序不稳定和 FACT 资格失败而 `ineligible`；尚未对八案全部完成真实 Judge，也没有每案三次独立运行和 bootstrap 95% CI。因此不能宣称某个 DRB 优化版本已在全 Bench 上显著优于基线。
