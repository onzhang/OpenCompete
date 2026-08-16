# OpenCompete 竞品研究 Bench v2 架构设计

## 1. 背景与成功标准

官方 Deep Research Bench 能评价通用研究报告质量，但不能充分识别竞品研究中特有的错误，例如跨地区价格比较、不同财务期间排名、产品型号错配、把宣布能力当成已运营能力，以及在证据不足时输出确定性选型建议。

本设计建立一套只服务于 OpenCompete 真实竞品研究执行的本地 Benchmark。它必须使用正式 OpenCompete Graph、共享 Open Deep Research 子图、正式 Prompt 构造、`materialize`、`validate`、`ApplicationRunner` 和本地持久化，不复制 Supervisor、Researcher 或研究 Graph。

成功标准：

1. 八个案例具有固定 `ResearchRequest`、固定 Scope 模板、成熟 Reference、原子 Claim、来源定位、案例级 Criteria 和可审计的双 AI 专家复核记录。
2. 本地 CLI 能跳过竞品发现和人工确认，从正式 Graph 的 `freeze_scope` 后继路径运行研究。
3. 评测同时输出竞品分析五维质量、FACT、硬门禁、完成状态和成本，禁止合成万能总分。
4. Judge 使用双文章顺序评分，并显式标记顺序不稳定。
5. 所有结果绑定固定案例哈希、`run_id` 和 `artifact_revision`，写入原有任务目录。

## 2. 范围与非目标

范围：

- 新建 `benchmarks/opencompete/v2/`，不覆盖 v1。
- 新建 v2 数据契约、加载器、专用 Judge、汇总器和确定性校验。
- 增加固定 Scope 的正式运行入口和 CLI。
- 完成八个 Reference 的领域作者与独立 Reviewer 闭环。
- 使用离线伪 Judge、缺陷变体和必要的真实模型运行进行量化校准。

非目标：

- 不创建第二套研究 Graph、Supervisor、Researcher 或搜索系统。
- 不依赖 Agent Server、LangSmith、MCP 或远程评测平台。
- 不把 AI 审核冒充真实人工审核。
- 不因单个 Benchmark 案例立即扩充生产运行时领域包。
- 不把 DRB 通用报告质量重复纳入 OpenCompete 专用总分。

## 3. 可测试需求

### REQ-001：正式 Graph 直达

系统 SHALL 接收固定 `ResearchRequest` 和已确认 Scope，在正式 Graph 中以 `freeze_scope` 为最后完成节点继续执行。

删除后果：只能重新经过候选发现和人工确认，无法稳定复现相同研究输入。

#### Scenario：固定案例运行

- GIVEN 一个通过 v2 契约校验的案例
- WHEN 用户执行固定案例运行命令
- THEN 系统依次经过 `prepare_research_input → odr_research → materialize → validate`，并发布正式 `RunBundle`

### REQ-002：研究路径唯一

系统 SHALL 复用 `src/opencompete/graph.py` 的编译产物和正式依赖注入。

删除后果：测试可能形成与产品行为不同的旁路，无法证明共享 DRB 优化对真实产品有效。

### REQ-003：成熟 Reference

每个案例 SHALL 包含决策场景、可比口径、事实矩阵、竞争机制、条件化建议、Unknown 和补证计划。

删除后果：Reference 退化为资料摘要，无法代替成熟竞品分析审核基准。

### REQ-004：原子 Claim 审计

每个 Reference SHALL 将事实、分析、建议和 Unknown 拆成稳定 Claim，并关联固定来源和定位。

删除后果：无法判断 Target 遗漏、反驳或无证据补齐了哪些关键结论。

### REQ-005：竞品分析五维评分

系统 SHALL 按决策相关性、可比性、竞争机制、证据与不确定性、场景化建议五维逐 Criterion 评分。

删除后果：跨口径比较和无条件推荐可能被通用文风分掩盖。

### REQ-006：双顺序 Judge

系统 SHALL 分别以 Target/Reference 和 Reference/Target 顺序评分，反向映射后平均并计算差值。

删除后果：文章位置偏差无法被检测。

### REQ-007：指标分离

系统 SHALL 分别保存竞品质量、FACT、硬门禁和执行成本。

删除后果：高文风或洞察分可能掩盖事实、安全、范围或成本失败。

### REQ-008：诚实的专家复核

系统 SHALL 区分 `expert_ai_authored`、`expert_ai_dual_reviewed` 和 `human_approved`。

删除后果：AI 审核可能被错误表述为真实人工背书。

### REQ-009：秘密安全

系统 SHALL 不把任何 API Key、认证 Header 或秘密值写入 Benchmark、报告、日志和评测结果。

删除后果：本地长期产物可能泄漏认证信息。

## 4. 方案比较

| 方案 | 思路 | 优点 | 代价 | 结论 |
|---|---|---|---|---|
| A：扩展 v1 | 在现有 RACE/FACT 上增加字段 | 变更文件少 | v1 语义混杂，Reference Claim 和专用维度难以表达 | 否决 |
| B：并行 v2 | 独立 v2 契约与服务，共用正式运行和持久化 | 边界清晰、可回退、不会破坏 DRB | 增加少量模型和加载代码 | 采用 |
| C：外部评测平台 | 使用远程数据集与实验追踪 | 功能完整 | 对个人本地 CLI 过重，并增加供应商依赖 | 否决 |

成熟方案来源：

- 官方 Deep Research Bench 固定提交 `469cce54ea7f6a63c163d3d9fec879cf289ec484` 的任务级 Criteria、隐藏权重、成对评分和确定性汇总。
- LangGraph 当前锁定版本的 `aupdate_state(..., as_node="freeze_scope")`，已通过本地最小实验验证可从正式节点边界继续执行。
- Pydantic 严格模型、现有 `RunRepository` 原子持久化和秘密扫描能力。

## 5. 逻辑架构

```text
Bench v2 Case
  ├─ fixed ResearchRequest
  ├─ expected Scope template
  ├─ Reference / Claims / Sources
  └─ Competitive Criteria / Weights
          │
          ▼
ApplicationRunner.create_fixed_scope_run
          │ aupdate_state(as_node="freeze_scope")
          ▼
正式 OpenCompete Graph
  prepare_research_input → ODR → materialize → validate
          │
          ▼
RunRepository / RunBundle revision
          │
          ▼
CompetitiveEvaluationService
  hard gates ─ FACT ─ dual-order Judge ─ deterministic aggregation
          │
          ▼
runs/<run_id>/evaluations/<evaluation_id>.json
```

依赖方向：Benchmark 数据依赖领域模型；ApplicationRunner 依赖 Graph 和 Repository；评测服务只读取已发布 Bundle，不反向修改业务产物。

## 6. 五维与权重

| 维度 | 默认权重 | 唯一职责 |
|---|---:|---|
| `decision_relevance` | 0.15 | 决策者、场景、期限、边界和关键问题 |
| `comparison_rigor` | 0.25 | 时间、地区、单位、型号、SKU、状态和指标定义归一化 |
| `causal_synthesis` | 0.20 | 差异成因、替代解释、能力约束、竞争反应和跨层影响 |
| `evidence_grounding` | 0.25 | 来源角色、事实/推断分离、Unknown、反例和证据不对称 |
| `recommendation_quality` | 0.15 | 条件化选择、不推荐场景、风险、退出条件和下一步行动 |

五个主维度使用跨案例统一权重，避免相同 Graph 优化在不同案例中因主权重漂移而失真；维内 Criterion 仍按案例冻结。每维 2–3 条、全案 10–15 条，关键 Criterion 2–4 条。Judge 不接收任何权重，只返回 Criterion 原始分。

质量 Judge 使用匿名、对称的 `candidate_A/candidate_B` 输入。两边只暴露相同结构的报告、Claim、支持卡片和来源，不发送 `Target`、`Reference` 或实现身份标签。Judge 先独立按锚点评价两篇，再做一致性检查；Reference 不是预设满分答案。

## 7. 评分与失败语义

绝对主分：

```text
competitive_analysis_overall = Σ(dimension_score × dimension_weight)
```

辅助相对分：

```text
relative_to_reference = target / (target + reference)
```

分母为零时返回 `null`。相对分只表示参考水平距离，不表示质量百分比。

状态：

- `completed`：门禁通过且 Judge 稳定。
- `ineligible`：硬门禁或 FACT 资格门禁失败；可保留诊断分但不得排名。
- `judge_failed`：任一顺序调用失败或输出不完整。
- `judge_unstable`：关键 Criterion、两个维度或 Overall 超过顺序差阈值。
- `reference_invalid`：Reference 审核、哈希、来源或 Claim 契约失效。
- `partial_diagnostic`：业务 Run 为 partial，仅用于诊断，不进入正式排行榜。

Unknown 是合法研究结果，不是失败。隐藏 Unknown、伪造支持或在 Unknown 上确定性排名才是失败。

状态优先级固定为：

```text
reference_invalid
> partial_diagnostic
> ineligible
> judge_failed
> judge_unstable
> completed
```

FACT 不并入五维 Overall，但参与排名资格。初始门槛为：关键事实加权覆盖率至少 0.80、主要事实至少 0.60、重要 Target Claim 整体支持率至少 0.90、已声明 Claim–Evidence 关系支持率至少 0.90、关键矛盾为零、无不支持的重要 Claim，关键 Unknown 必须被诚实保留。`supported=1`、`partially_supported=0.5`、其余为零；首轮缺陷变体校准后才能修改阈值。

## 8. 双 AI 专家流程

1. 领域 Author 只依据固定来源撰写 Reference、Claims 和 Criteria。
2. 独立 Reviewer 不读取作者推理过程，逐 Claim 返回 `accept/revise/delete/unknown/source_missing`。
3. Author 修订所有重大问题。
4. Reviewer 二次检查；存在未关闭重大问题则保持 `expert_ai_authored`。
5. 全部关闭后写入 `expert_ai_dual_reviewed`，并明确 `human_reviewed=false`。

## 9. 领域包决策

当前不扩充生产垂直领域包。八案先分为四个事实丰富正向案、两个混合证据案和两个证据稀缺挑战案。案例专用维度保留在固定 Scope。只有同一横向能力在至少三个案例、每案至少三次独立运行中重复缺失，且 Pack-on 相对 Pack-off 的五维质量平均提升至少 0.5、关键 Claim 覆盖提升至少 10 个百分点、95% 置信区间不跨零、无门禁回归且成本增幅不超过约 20%，才形成可选领域包。

## 10. 验收门禁

数据集：

- 八个案例均通过跨文件契约。
- 所有维度和维内权重分别精确归一化。
- 关键事实 100% 具有来源定位；主要事实支持率至少 95%。
- 八个案例均为 `expert_ai_dual_reviewed`，未关闭重大问题为零。
- 正向案每案至少 15 个原子 Claim 和 8 个事实；混合案至少 15 个 Claim 和 5 个事实；挑战案至少 12 个 Claim、2 个事实和 3 个 Unknown。
- 关键和主要事实的直接来源必须保存实际证据摘录及可复算 SHA256；动态事实必须明确快照、归档或未冻结边界。
- Author 与 Reviewer 的模型身份、Prompt 哈希和最终复核轮次必须非空。

执行入口：

- 测试证明没有调用发现和确认节点。
- 测试证明调用正式 `prepare_research_input`、研究子图、`materialize` 和 `validate`。
- 产物由原 `RunRepository` 发布并可通过哈希校验读取。

Judge：

- 每个 Criterion 两种顺序均完整返回。
- Criterion 顺序差大于 2.0、维度差大于 1.0、Overall 差大于 0.75 时显式不稳定。
- 缺陷变体主要降低目标维度，不能只改变无关维度。
- 每案至少包含口径错配、来源错位、因果跳跃、无条件建议、遗漏关键场景五种单缺陷变体和一种纯文风变体；Reference 胜过缺陷变体至少 95%，缺陷落入预期维度至少 85%，纯文风变化 Overall 不超过 0.20。

优化判定的初始门槛：

- 无新增硬门禁失败。
- 至少 5/8 案例取得正向 paired delta。
- 平均五维 Overall 至少提升 0.30/10。
- 任一主维度平均退化不超过 0.20。
- 关键 Claim 覆盖不得下降。
- 成本和完成率独立报告。

这些阈值在首轮 Judge 校准后可以提升数据集小版本，但不得针对待测报告临时修改。
