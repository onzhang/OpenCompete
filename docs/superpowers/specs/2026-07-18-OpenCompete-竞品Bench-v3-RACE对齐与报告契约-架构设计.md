# OpenCompete 竞品 Bench v3：RACE 对齐与报告契约 — 架构设计

> 状态：已批准并实现（TDD goal）  
> 日期：2026-07-18  
> 作者：接续 Codex 会话 `019f747c-82ac-7760-a5a9-78e20ceace40`  
> 前置基线：  
> - `docs/superpowers/specs/2026-07-15-opencompete-通用竞品分析系统-架构设计.md`  
> - `docs/superpowers/specs/2026-07-17-OpenCompete竞品研究Bench-v2-架构设计.md`  
> - 官方 Deep Research Bench 固定提交 `469cce54ea7f6a63c163d3d9fec879cf289ec484`

## 1. 背景与目标

### 1.1 问题

1. **RACE 主分偏离官方骨架**：通用架构规格写明 RACE 四维为全面性、深度/洞察、指令遵循、可读性；v1 路径仍保留四维（但本地字段名用 `depth`）；competitive v2 却另起五维 `decision_relevance / comparison_rigor / causal_synthesis / evidence_grounding / recommendation_quality` 作为质量主分，导致 **Readability 从主分中消失**。
2. **内部研究契约泄漏进用户报告**：`prepare_research_input` 把冻结 Scope JSON、英文枚举、逐格覆盖清单、`Unknown` 规则整包塞进上游 `messages`；最终报告节点原样消费，Materializer 保留原文，门禁只看结构化元数据，于是发布出审计日志式报告。
3. **评测结果不可诚实标注**：自定义数据 + 本地 Judge 却缺少与官方方法基线的可审计绑定；研究成本与评测（Judge）成本未严格分离。

### 1.2 成功标准

- [ ] competitive 主分恢复官方 RACE 四维：`comprehensiveness`、`insight`、`instruction_following`、`readability`；Overall 与官方加权公式数值一致（parity fixture）。
- [ ] 竞品领域能力进入任务级 Criteria 与独立 FACT/硬门禁，**不再**作为替换 RACE 的五维主分。
- [ ] 结果 `result_kind = opencompete_local_race`，绑定官方仓库/commit/prompt 哈希；不得称为 `official_race`。
- [ ] `ResearchContract` 与 `UserReportBrief` 分离；最终报告上下文无内部枚举、裸 `Unknown`、内部 ID。
- [ ] `ReportPublicationGate` 对 Markdown 正文做确定性检查；失败可一次修复，再失败则 `partial` + 用户可读 fallback。
- [ ] 新建 `benchmarks/opencompete/v3/`；v2 只读可读取、CLI 不再生成新 v2 结果。
- [ ] 现有离线评测测试更新后通过，并新增 v3 覆盖；至少 `ocb-006` 完成端到端回归样例。

### 1.3 非目标

- 不复制或重写上游 `deep_researcher_builder` / ODR 子图内部实现。
- 不把外部官方脚本目录当作 OpenCompete 生产评测入口。
- 不把 FACT、硬门禁、稳定性、成本合成万能总分。
- 不把 reverse 顺序分并入官方公式主分。
- 不在未完成真实用户校准前拍脑袋固定 Readability 最低发布阈值。
- 不将 v2 历史 JSON 就地改写为 v3。
- 不在本变更中扩充生产垂直领域包。

## 2. 约束与上下文

| 类型 | 内容 |
|------|------|
| 技术栈 | Python 3.12、Pydantic FrozenModel、LangGraph、现有 OpenCompete Graph / RunRepository |
| 部署形态 | 本地 CLI；正式 Graph 直达 `freeze_scope` 后继路径 |
| 规模假设 | 8 个竞品案例；Judge 双顺序；离线伪 Judge + 必要 live 闭环 |
| 安全/合规 | 禁止密钥入产物；公开 HTTP URL 校验保持 |
| 必须兼容 | v1 RACE/FACT 路径可读；v2 历史结果只读；`AGENTS.md` 对 `opencompete_local_race` 的标注要求 |
| 官方锚点 | DRB commit `469cce54...`：四维、任务级 Criteria、成对评分、`target/(target+ref)` 维分与 Overall |

## 3. 可测试需求

### REQ-001：官方四维主分

系统 SHALL 仅以 `comprehensiveness`、`insight`、`instruction_following`、`readability` 作为 competitive v3 质量主分维度；字段名使用 `insight` 而非本地旧名 `depth`。

#### Scenario：维度集合

- GIVEN 任意 v3 评测结果
- WHEN 读取 `race.dimensions` 键集合
- THEN 恰好等于上述四维，且不存在 v2 五维键名

### REQ-002：官方 Overall 公式

系统 SHALL 按官方方式计算：

```text
dimension_score = target_dim / (target_dim + reference_dim)
overall = target_weighted_total / (target_weighted_total + reference_weighted_total)
```

维度权重和为 1；维内 Criteria 权重和为 1。Target-first 为规范顺序并产出 `race.overall`。

#### Scenario：parity fixture

- GIVEN 固定 target/reference 原始分与权重样例
- WHEN 调用 v3 聚合函数
- THEN 与官方 `calculate_weighted_scores`（固定提交）逐位一致

### REQ-003：双顺序仅作稳定性诊断

系统 SHALL 额外运行 Reference-first，仅写入 `stability_diagnostic`（含 `order_averaged_diagnostic`、`dimension_order_gaps`、`overall_order_gap`）；reverse 结果不参与 `race.overall`。

#### Scenario：不稳定标记

- GIVEN 两顺序 Overall 差超过阈值
- WHEN 写出结果
- THEN `race.overall` 仍为 Target-first 官方分，且 `eligibility.eligible_for_trend = false`

### REQ-004：指标分离

系统 SHALL 分别持久化 `race`、`fact`、`hard_gates`、`research_cost`、`evaluation_cost`、`eligibility`，禁止合成跨类万能总分。

#### Scenario：Judge 成本

- GIVEN 完成含 Judge 的 v3 评测
- WHEN 读取结果
- THEN Judge token/费用出现在 `evaluation_cost`，不得仅出现在历史研究成本字段

### REQ-005：结果诚实标注

系统 SHALL 将 v3 结果标记为 `result_kind = opencompete_local_race`，并记录 `official_method_base`（repository、commit、criteria_prompt_sha256、scoring_prompt_sha256）。

#### Scenario：禁止冒充官方

- GIVEN OpenCompete 自定义案例与本地 Judge
- WHEN 写出评测 JSON
- THEN `result_kind` 不得为 `official_race`

### REQ-006：v2 只读兼容

系统 SHALL 继续读取历史 v2 评测 JSON；CLI/服务默认路径不再创建新的 v2 五维主分结果。

#### Scenario：历史读取

- GIVEN 已有 v2 `evaluations/*.json`
- WHEN 汇总或展示历史
- THEN 可解析五维结构且不触发写入

### REQ-007：ResearchContract 与 UserReportBrief 分离

系统 SHALL 将内部冻结范围、覆盖单元、排除项、结构化 Unknown 要求放入 `ResearchContract`；将目标读者、决策问题、自然语言比较问题、输出语言与可读结构要求放入 `UserReportBrief`。

#### Scenario：消息边界

- GIVEN 已冻结 Scope 的正式 Run
- WHEN 执行 `prepare_research_input`
- THEN 进入上游根 `messages` / 最终报告消费路径的内容仅为 `UserReportBrief` 自然语言，不得包含 Scope 原始 JSON、内部枚举名或逐格覆盖审计表

### REQ-008：内部契约隔离范围

`ResearchContract` SHALL 仅供 Supervisor（若启用可选 `research_instructions`）、Researcher 侧约束、Extractor 与 hard gates 使用；禁止进入最终报告 `<Messages>`。

#### Scenario：可选 research_instructions

- GIVEN 实验证明缺少内部研究约束导致覆盖显著下降
- WHEN 增加可选 State 字段 `research_instructions`
- THEN 该字段只进入 Supervisor system context，不进入最终报告消息

### REQ-009：ReportPublicationGate

系统 SHALL 在发布 `report.md` 前对 Markdown 正文做确定性门禁（不信任 `ReportArtifact.claim_ids` 填满即合格）。

门禁至少包括：

- 无内部枚举与内部 ID（如 `product_service`、`capabilities`、`user_experience`、`ClaimStatus`、`src_`/`evd_`/`clm_`/`cmp_`、`冻结单元`）
- 无裸 `Unknown`
- 报告语言与用户语言一致（品牌名与必要术语除外）
- 决策型任务前 1,500 字出现结论摘要
- 数字引用可解析到来源；存在可点击来源
- 无重复同义章节；标题层级合法
- 重要价格/成本单位明确
- **不再**要求“至少 5000 字符”

#### Scenario：差报告回归

- GIVEN 当前已知差报告固定样例
- WHEN 运行 ReportPublicationGate
- THEN 至少命中：`internal_schema_leak`、`literal_unknown_leak`、`missing_resolvable_sources`、`decision_summary_missing`、`language_consistency_failed`、`excessive_repetition`

### REQ-010：发布失败语义

系统 SHALL：

1. 首次候选不直接发布；
2. 将门禁错误码反馈给同一 Report Writer，允许一次无搜索修复；
3. 修复仍失败则 Run 状态 `partial`；
4. 使用用户可读 fallback Renderer 作为对外报告，不合格候选不写入正式 `report.md`；
5. 原候选仅保留在 Checkpoint/诊断范围。

#### Scenario：一次修复后仍失败

- GIVEN 候选报告两次均未过门禁
- WHEN materialize/validate 结束
- THEN 状态为 `partial`，`report.md` 为 fallback，不合格全文不在发布路径

### REQ-011：v3 数据集迁移

系统 SHALL 在 `benchmarks/opencompete/v3/` 新建案例；每个案例重新完成：用户向 `prompt.md`、官方四维 `criteria.json`、竞品映射 Criteria、Readability 独立复核、用户视角 Reference 重写、哈希重算、Review 状态重置（不继承 v2 审核结论）。

#### Scenario：首迁 ocb-006

- GIVEN v2 `ocb-006`
- WHEN 完成 v3 迁移门禁
- THEN 案例可加载、Criteria 四维完整、Reference 无审计模板口吻、review 为新状态

### REQ-012：秘密安全

系统 SHALL 不把 API Key、认证 Header 或秘密值写入 Benchmark、报告、日志和评测结果（继承 v2 REQ-009）。

## 4. 方案对比

| 方案 | 思路 | 优点 | 代价 | 结论 |
|------|------|------|------|------|
| A：五维加 Readability 变六维 | 最小改模型字段 | 改动面小 | 仍偏离 RACE；自定义总分继续存在 | **否决** |
| B：官方四维顶层 + 竞品 Criteria + 独立 FACT/硬门禁 + 报告契约分离 | RACE 方法不变，领域能力下沉，修泄漏根因 | 与架构规格和官方方法对齐；可审计 | 需 v3 数据迁移与 DTO/服务改造 | **采用** |
| C：外部官方脚本直接跑 OpenCompete 案例 | 临时改官方数据目录 | 看似“更官方” | 自定义数据仍非官方结果；耦合重；无法修报告生成根因 | **否决** |

### 4.1 推荐

- **采用：** B  
- **理由：** 满足“官方方法骨架 + 竞品定制”的明确决策；同时关闭报告契约泄漏，否则 Readability 只会稳定打出低分。  
- **否决 A/C：** 见上表。

### 4.2 调研摘要

| 候选 | 来源 | 结论 | 原因 |
|------|------|------|------|
| 官方 RACE 四维与公式 | DRB `469cce54...` / `deepresearch_bench_race.py` | 采用为方法基线 | 不可变骨架 |
| 官方 Criteria/Scoring Prompt | 同提交 `prompt/` | 采用为生成与哈希锚点 | 可复算 |
| 现有 v1 `RaceDimension`（含 `depth`） | `evaluation/models.py` | 部分复用，字段对齐时改为 `insight` 或并行 v3 DTO | 本地旧名偏离官方 |
| v2 五维主分 | `competitive_models.py` | 停止生成；历史只读 | 与官方骨架冲突 |
| 现有 FACT / hard_gates | `competitive_service` / `hard_gates.py` | 保留并独立 | 事实与安全不可并入文风分 |
| `reporting.render_validated_report` | `reporting.py` | 改良为用户可读 fallback | 已有不暴露内部 ID 的方向 |
| 重写 ODR builder | 上游包 | 否决 | 边界过大；优先薄适配 |

自研最小边界：v3 DTO、官方公式 parity、报告门禁规则、`UserReportBrief` 适配、v3 数据集与加载器。重评触发：官方 DRB 更新提交或 OpenCompete 改变主分语义时。

## 5. 逻辑架构

### 5.1 组件与职责

| 组件 | 职责 | 不负责 | 依赖 |
|------|------|--------|------|
| `UserReportBrief` 构造 | 自然语言用户任务与可读结构要求 | 内部覆盖审计 | ResearchRequest、读者/语言配置 |
| `ResearchContract` | 冻结 Scope、覆盖、来源、排除、预算、Unknown 结构要求 | 最终报告措辞 | ScopeSpec、Request |
| `prepare_research_input`（改造） | 只把 Brief 写入根 messages；Contract 走确定性路径 | ODR 内部调度 | Graph、上述两 DTO |
| Extractor / hard gates | 用 Contract 校验结构化覆盖与安全 | 用户文风 | Bundle |
| `ReportPublicationGate` | Markdown 正文确定性检查 + 一次修复触发 | Judge 主观分 | 报告候选文本 |
| Fallback Renderer | 用户可读 partial 报告 | 伪装完整研究 | 已校验 Claims/Sources |
| `CompetitiveEvaluationService` v3 | Target-first RACE + reverse 诊断 + FACT + gates + 双成本 | 修改业务 Bundle | v3 数据集、Judge |
| v3 Dataset Loader | 加载/校验 `benchmarks/opencompete/v3` | 改写 v2 | Pydantic 契约 |
| v2 Reader | 只读历史五维结果 | 新写入 | 旧 DTO |

### 5.2 依赖规则

- 允许：Benchmark → 领域模型；ApplicationRunner → Graph/Repository；评测服务只读已发布 Bundle。
- 禁止：评测服务回写业务产物；最终报告节点读取 ResearchContract 原始 JSON；v3 聚合依赖 reverse 顺序。

### 5.3 数据流（主路径）

```text
Bench v3 Case
  prompt.md (用户问题) + criteria.json (四维) + reference + claims
          │
          ▼
ApplicationRunner.create_fixed_scope_run
          │
          ▼
prepare_research_input
  ├─ messages[] ← UserReportBrief only
  └─ ResearchContract → Extractor / hard gates / (optional supervisor system)
          │
          ▼
ODR research → final report candidate
          │
          ▼
ReportPublicationGate
  ├─ pass → materialize 发布 report.md
  └─ fail → 一次修复 → 仍 fail → partial + fallback
          │
          ▼
RunRepository / RunBundle
          │
          ▼
CompetitiveEvaluationService v3
  hard_gates → FACT → Target-first RACE → reverse stability → costs
          │
          ▼
evaluations/<id>.json  (result_kind=opencompete_local_race)
```

### 5.4 关键状态与一致性

- Bundle / report 发布路径：不可变 revision；不合格候选不进 `report.md`。
- 评测 JSON：按 evaluation_id 追加；绑定 `case_content_sha256`、`task_input_sha256`、`run_id`、`artifact_revision`。
- v2 与 v3 结果目录可共存；读取方按 schema/version 分支。

## 6. 数据与契约

### 6.1 RACE 主分与 v2 映射

| RACE 维度 | OpenCompete 任务级定制要点 |
|-----------|---------------------------|
| Comprehensiveness | 对象、价格、套餐、用户类型、核心能力、设备生态、迁移、锁定、TCO 覆盖 |
| Insight | 价格/权益/历史资产/音频链路/迁移成本如何共同形成用户价值；条件化建议与翻转因素 |
| Instruction Following | 地区、日期、用户场景、冻结对象、排除项；直接回答选择问题 |
| Readability | 目标用户、结论前置、语言一致、术语可理解、结构清晰、不重复、表格同口径、来源可点 |

| v2 指标 | v3 去向 |
|---------|---------|
| `decision_relevance` | `instruction_following` |
| `comparison_rigor` | `comprehensiveness` + `insight`；事实部分 → FACT |
| `causal_synthesis` | `insight` |
| `evidence_grounding` | 主要 → FACT；覆盖完整性 → `comprehensiveness` |
| `recommendation_quality` | `insight` + `instruction_following` |
| （缺失）可读性 | 完整 `readability` Criteria |

维度权重：任务级动态生成（官方方法），不在设计中预固定 15%/20%。八案真实用户校准后再讨论最低发布阈值。

### 6.2 Readability Criteria 最低覆盖（每案）

1. 目标用户适应性  
2. 结论前置（“什么人适合谁”）  
3. 语言一致性（中文报告无内部英文枚举）  
4. 术语解释（ALAC/FLAC/DAC 等用户意义）  
5. 信息架构（价格/推荐/迁移结论不跨章重复）  
6. 信息密度（研究限制不淹没决策）  
7. 数据呈现（同口径表）  
8. 可执行性（普通消费者可完成的核实动作）  
9. 来源可访问性（关键规则可点击官方链接）  
10. 内部契约隔离（禁止内部 schema/ID/裸 Unknown）

### 6.3 结果契约草图

```text
CompetitiveEvaluationResultV3
├── evaluation_contract_version
├── result_kind = opencompete_local_race
├── official_method_base
│   ├── repository
│   ├── commit
│   ├── criteria_prompt_sha256
│   └── scoring_prompt_sha256
├── case_content_sha256
├── task_input_sha256
├── target { run_id, artifact_revision }
├── race
│   ├── dimension_weights
│   ├── comprehensiveness / insight / instruction_following / readability
│   ├── target_weighted_total / reference_weighted_total
│   ├── overall                    # Target-first 官方公式
│   ├── canonical_output
│   └── stability_diagnostic       # reverse 与 gap；不入 overall
├── fact
├── hard_gates
├── research_cost
├── evaluation_cost
└── eligibility { eligible_for_trend, reasons[] }
```

### 6.4 两个输入契约草图

```text
ResearchContract
├── frozen_scope
├── internal_dimension_ids
├── coverage_units
├── allowed_sources
├── exclusions
├── budget_and_permissions
└── structured_unknown_requirements

UserReportBrief
├── audience
├── decision_to_resolve
├── as_of_region_and_date
├── natural_language_comparison_question
├── user_scenarios
├── output_language
└── readability_structure_requirements
```

## 7. 横切与安全

- 秘密扫描与公开 URL 校验保持。  
- 评测与研究成本分账，避免“只记研究、不记 Judge”。  
- 日志不得打印完整候选报告中的密钥形态字符串。  
- 稳定性阈值沿用可校准策略：显式写入结果，不静默平均进主分。

## 8. 测试与完成定义

### 8.1 契约测试

- 四维完整且仅官方四维；使用 `insight`。  
- 维权与维内权均 = 1。  
- 与官方 `calculate_weighted_scores` parity。  
- Overall 与官方公式逐位一致。

### 8.2 报告失败回归

固定差报告样例必须命中 REQ-009 所列错误码集合。

### 8.3 用户报告验收

- 30 秒内可见选择建议。  
- 无内部字段/裸 Unknown。  
- 关键价格与迁移规则可点击。  
- 同口径价格/TCO 表。  
- Bundle 仍保留全部 Unknown 与审计关系。

### 8.4 Benchmark 验收

- v3 八案加载与审核门禁（可分阶段：先 ocb-006，后其余七案）。  
- 同 Judge、同 Criteria 评新旧报告；Readability 单独输出。  
- 至少一次 fresh live：研究 → 发布 → v3 RACE → FACT → 成本闭环。  
- 现有约 69 个离线评测测试更新后通过，并新增 v3 覆盖。

## 9. 风险、未决与否决记录

| 项 | 说明 | 处理 |
|----|------|------|
| 去掉 messages 内覆盖清单后覆盖率下降 | 可能 | 先 Brief-only；必要时可选 `research_instructions` 仅进 Supervisor system |
| v1 字段 `depth` vs 官方 `insight` | 命名漂移 | v3 与官方对齐 `insight`；v1 读取兼容层单独处理 |
| 动态维度权重跨案不可比 | 官方方法固有 | 趋势榜用 eligibility；不把自定义固定权冒充官方 |
| Reference 重写工作量大 | 八案 | 先 ocb-006 打通，再批量 |
| 一次修复仍失败 | 体验 | partial + fallback，禁止冒充正式报告 |

### 9.1 设计审计表

| 设计 | 对应需求 | 删除后后果 | 成熟基础 | 最小自研 |
|------|----------|------------|----------|----------|
| 官方四维与公式 | REQ-001/002/005 | 再失 Readability 与官方基线 | 官方 RACE | DTO、适配、parity |
| 竞品任务级 Criteria | REQ-001/011 | 通用 Criteria 盖不住领域 | 官方动态 Criteria | 八案冻结 Criteria |
| FACT 独立 | REQ-004 | 可读但错误报告高分 | 现有 FACT | 保留改名/接入 v3 |
| ReportPublicationGate | REQ-009/010 | 审计式报告再发布 | Markdown 确定性检查 | 产品级规则集 |
| User/Contract 分离 | REQ-007/008 | Prompt 继续污染正文 | State/System 边界 | 两 DTO + 薄适配 |
| 官方实现作数值 Oracle | REQ-002 | 自研聚合再漂 | 固定提交 | parity fixture |

## 10. 实施顺序（摘要）

1. v3 RACE DTO + 官方公式 parity tests  
2. 移除五维主分生成路径；保留 v2 历史 DTO 读取  
3. 分离 ResearchContract / UserReportBrief；改造 `prepare_research_input`  
4. ReportPublicationGate + 一次修复  
5. 改良 fallback Renderer  
6. 迁移 `ocb-006` + 差报告回归  
7. 一次 fresh live 研究与 v3 评测  
8. 迁移其余七案  
9. 更新 README、主架构规格交叉引用、正式命令  
10. 全套离线测试 + 人工验收  

## 11. 批准门槛

批准本规格即冻结：

1. **边界**：B 方案；v3 并行数据集；不重写 ODR builder  
2. **变更面**：`graph.py` 输入构造、`reporting`/materialize 发布门禁、`evaluation/competitive_*` v3 路径、`benchmarks/opencompete/v3`、相关 CLI/测试  
3. **关键决策**：官方四维主分 + Criteria 定制；FACT/门禁/成本独立；Target-first 主分 + reverse 诊断；`opencompete_local_race`  
4. **未决**：动态权重校准后的发布阈值；是否启用 `research_instructions`（仅数据证明覆盖下降后）

批准后产出/更新实现计划：  
`docs/superpowers/plans/2026-07-18-OpenCompete-竞品Bench-v3-RACE对齐与报告契约.md`  

**在规格明确批准前，不修改业务代码。**

