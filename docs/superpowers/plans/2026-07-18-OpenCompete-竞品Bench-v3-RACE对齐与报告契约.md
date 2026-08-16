# OpenCompete 竞品 Bench v3：RACE 对齐与报告契约 Implementation Plan

> **For agentic workers:** 按任务逐步执行；步骤使用 checkbox（`- [ ]`）跟踪。未完成前一任务的验证，不开始下一任务。  
> **状态：** 待设计规格批准后执行（禁止抢跑业务代码）  
> **Design Spec:** `docs/superpowers/specs/2026-07-18-OpenCompete-竞品Bench-v3-RACE对齐与报告契约-架构设计.md`

**Goal:** 将 competitive 主分恢复为官方 RACE 四维（含 Readability），竞品能力下沉 Criteria/FACT/硬门禁；切断内部研究契约对用户报告的污染，并以 v3 数据集与可审计结果契约落地。

**Architecture:** 并行 `benchmarks/opencompete/v3` + v3 评测 DTO/服务；Graph 侧拆分 `UserReportBrief`/`ResearchContract`；发布路径增加 `ReportPublicationGate` 与一次修复；v2 五维仅只读。数值以 DRB 固定提交 `469cce54...` 为 Oracle。

**Tech Stack:** Python 3.12、Pydantic FrozenModel、现有 LangGraph OpenCompete Graph、pytest 离线套件、本地 CLI。

## Global Constraints

- 规格未批准前不改业务代码
- 禁止把 reverse 顺序并入 `race.overall`
- 禁止 `result_kind=official_race`（自定义数据 + 本地 Judge）
- 禁止合成 race+fact+gates+cost 万能总分
- 禁止重写 `deep_researcher_builder` / ODR 内部
- 禁止就地改写 v2 案例或历史 evaluation JSON
- 禁止新增未批准第三方依赖
- 测试命令在 `opencompete/` 目录下执行（以仓库实际 pytest 入口为准）

## File Map

| 文件 | 职责 |
|------|------|
| `src/opencompete/evaluation/competitive_models.py` | 保留 v2 DTO；新增或并列 v3 RACE DTO |
| `src/opencompete/evaluation/competitive_service.py` | v3 评测编排；停止默认写 v2 五维主分 |
| `src/opencompete/evaluation/competitive_judge.py` | 四维 Criterion Judge 输入/输出适配 |
| `src/opencompete/evaluation/competitive_dataset.py` | v3 加载与契约校验；v2 只读 |
| `src/opencompete/evaluation/official_drb.py` | 复用/抽取官方公式 parity 与 method_base 元数据 |
| `src/opencompete/evaluation/hard_gates.py` | 保持独立；接入 v3 结果 |
| `src/opencompete/graph.py` | `prepare_research_input` 只注入 UserReportBrief |
| `src/opencompete/models.py` 或新建 `research_contracts.py` | ResearchContract / UserReportBrief |
| `src/opencompete/reporting.py` | 用户可读 fallback 改良 |
| `src/opencompete/materialize.py` | 接入发布门禁与候选保留语义 |
| 新建 `src/opencompete/report_publication_gate.py` | Markdown 确定性门禁 |
| `src/opencompete/application.py` / `cli.py` | v3 评测与运行入口 |
| `benchmarks/opencompete/v3/**` | 新数据集 |
| `tests/opencompete/evaluation/test_competitive_v3_*.py` | v3 契约、parity、门禁回归 |
| `tests/opencompete/evaluation/test_competitive_v2.py` | 改为历史读取/不再生成断言（按需） |
| `docs/superpowers/specs/2026-07-15-...架构设计.md` | 交叉引用 v3；澄清 depth→insight |

---

### Task 1: v3 RACE DTO 与官方公式 parity

**Files:**

- Create: `src/opencompete/evaluation/competitive_v3_models.py`（若希望与 v2 文件隔离；否则在 `competitive_models.py` 增补并清晰分区）
- Modify: `src/opencompete/evaluation/official_drb.py`（导出/对齐聚合纯函数）
- Test: `tests/opencompete/evaluation/test_competitive_v3_parity.py`

**Interfaces:**

- Consumes: 官方固定样例原始维分、维度权重、维内权重
- Produces: `aggregate_race_v3(...)` → dimension scores + overall；`OfficialMethodBase` 元数据

**Steps:**

- [ ] **Step 1: 写失败测试** — 四维键集合、`insight` 存在、`depth` 不作为 v3 主分键、overall 与 Oracle 一致
- [ ] **Step 2: 运行测试确认失败**

```bash
pytest tests/opencompete/evaluation/test_competitive_v3_parity.py -q
```

- [ ] **Step 3: 最小实现** — 纯函数聚合 + method_base 常量（commit/prompt sha）
- [ ] **Step 4: 测试通过**
- [ ] **Step 5: 完成记录**

**Done when:**

- [ ] REQ-001/002/005 的数值与标注部分可单测证明

---

### Task 2: 停止 v2 五维主分生成，保留历史读取

**Files:**

- Modify: `competitive_service.py`、`cli.py`、`application.py`
- Modify: `tests/opencompete/evaluation/test_competitive_v2.py`
- Test: 新增 `test_competitive_v2_readonly.py`（可选）

**Interfaces:**

- Consumes: 历史 v2 evaluation JSON
- Produces: 默认评测路径走 v3；v2 loader `load_legacy=True`

**Steps:**

- [ ] **Step 1: 测试** — CLI/服务默认不再写出 `decision_relevance` 主分块；旧文件仍可 parse
- [ ] **Step 2: 实现开关与默认 v3**
- [ ] **Step 3: 回归 v2 读取测试**

**Done when:**

- [ ] REQ-006

---

### Task 3: ResearchContract / UserReportBrief + prepare_research_input

**Files:**

- Create: `src/opencompete/research_contracts.py`（推荐）
- Modify: `src/opencompete/graph.py`
- Test: `tests/opencompete/test_prepare_research_input_brief.py`

**Interfaces:**

- Consumes: `ResearchRequest`、`ScopeSpec`
- Produces: `messages` 仅 Brief；Contract 可供后续节点/extractor 使用的 state 字段（若需，明确字段名与 reducer）

**Steps:**

- [ ] **Step 1: 失败测试** — messages 文本不得匹配 Scope JSON 关键键、`product_service`、逐格 `Unknown` 强制句式等
- [ ] **Step 2: 实现两 DTO 与构造函数**
- [ ] **Step 3: 改造 `prepare_research_input`**
- [ ] **Step 4: 确认 hard gates / extractor 仍能从 scope state 取冻结范围（不依赖 messages）**

**Done when:**

- [ ] REQ-007/008（不含可选 research_instructions，除非覆盖实验已证明需要）

---

### Task 4: ReportPublicationGate + 一次修复

**Files:**

- Create: `src/opencompete/report_publication_gate.py`
- Modify: `materialize.py` 与/或 graph 最终报告衔接点
- Test: `tests/opencompete/test_report_publication_gate.py`
- Fixture: 当前差报告样例（固定文件）

**Interfaces:**

- Consumes: markdown:str, output_language, task_kind
- Produces: `GateResult(ok, error_codes[], hints[])`

**Steps:**

- [ ] **Step 1: 用差报告 fixture 写必须命中的错误码测试**
- [ ] **Step 2: 实现确定性规则（正则/结构解析，禁止再调 LLM 做门禁判定）**
- [ ] **Step 3: 接入发布路径：fail → 反馈 Writer 一次 → 再 fail → partial**
- [ ] **Step 4: 断言不合格全文不写 `report.md`**

**Done when:**

- [ ] REQ-009/010

---

### Task 5: Fallback Renderer 改良

**Files:**

- Modify: `src/opencompete/reporting.py`
- Test: `tests/opencompete/test_reporting_fallback.py`

**Interfaces:**

- Consumes: 已校验 claims/comparisons/sources
- Produces: 结论前置、无内部 ID、可点击来源的 Markdown

**Steps:**

- [ ] **Step 1: 测试** — 输出无 `product_service`/`clm_`；含简短建议区
- [ ] **Step 2: 调整章节顺序与措辞**
- [ ] **Step 3: 与 Task 4 集成测试**

**Done when:**

- [ ] partial 路径用户可读

---

### Task 6: v3 评测服务编排

**Files:**

- Modify: `competitive_service.py`、`competitive_judge.py`、`competitive_dataset.py`
- Test: `tests/opencompete/evaluation/test_competitive_v3_service_offline.py`

**Interfaces:**

- Consumes: RunBundle + v3 case
- Produces: `CompetitiveEvaluationResultV3` JSON

**Steps:**

- [ ] **Step 1: 离线伪 Judge 固定输出 → 完整结果契约形状测试**
- [ ] **Step 2: Target-first overall + reverse 仅诊断**
- [ ] **Step 3: FACT/hard_gates/evaluation_cost 字段填充**
- [ ] **Step 4: eligibility.eligible_for_trend 规则**

**Done when:**

- [ ] REQ-003/004/005

---

### Task 7: 迁移 ocb-006 至 v3

**Files:**

- Create: `benchmarks/opencompete/v3/cases/ocb-006-spotify-vs-apple-music/**`
- Create: v3 manifest
- Test: dataset 加载与 criteria 归一化测试

**Steps:**

- [ ] **Step 1: prompt.md 改为真实用户问题（无评测器指令）**
- [ ] **Step 2: criteria.json 官方四维 Schema + 竞品映射 + Readability 十条覆盖**
- [ ] **Step 3: Reference 按用户报告重写；Claims/Sources 重绑**
- [ ] **Step 4: 重算哈希；review 状态重置**
- [ ] **Step 5: 用旧差报告 vs 新 Reference 跑离线 v3 评测形状测试**

**Done when:**

- [ ] REQ-011（ocb-006）

---

### Task 8: Fresh live 闭环（人工触发）

**Files:** 无强制代码；运行记录写入 `docs/用户测试/`

**Steps:**

- [ ] 固定 Scope 跑 ocb-006 正式 Graph
- [ ] 确认发布报告过门禁或合法 partial
- [ ] 跑 v3 评测：RACE 四维 + FACT + 双成本
- [ ] 记录 run_id、evaluation_id、关键分数与门禁结果

**Done when:**

- [ ] 规格 8.4 中 live 条款对 ocb-006 成立

---

### Task 9: 迁移其余七案 + 文档/命令

**Files:**

- `benchmarks/opencompete/v3/cases/ocb-00{1-5,7,8}-*/**`
- README / 主架构规格交叉引用
- CLI help 文案

**Steps:**

- [ ] 逐案迁移（可并行但每案独立校验）
- [ ] 更新正式命令与文档中 v2→v3 说明
- [ ] 全量离线评测：

```bash
pytest tests/opencompete/evaluation -q
```

**Done when:**

- [ ] 八案可加载；离线套件绿；文档无“五维主分”误导表述

---

### Task 10: 最终验收清单

- [ ] parity 与四维契约测试通过
- [ ] 差报告门禁回归通过
- [ ] messages 无内部契约泄漏测试通过
- [ ] v2 历史只读、v3 默认写入
- [ ] ocb-006 live 闭环记录存在
- [ ] 规格成功标准 checklist 全部勾选或显式延期项已记录

---

## 建议执行切片

| 切片 | 任务 | 可独立交付 |
|------|------|------------|
| S1 评测正确性 | Task 1–2, 6 | 是（不改研究报告内容也能先正名主分） |
| S2 报告契约 | Task 3–5 | 是（即使仍用 v2 案例也能止血泄漏） |
| S3 数据与闭环 | Task 7–10 | 依赖 S1+S2 |

推荐顺序：**S1 → S2 → S3**。若只做一个切片，优先 **S2**（用户直接痛点）与 **S1 Task1**（公式 parity）并行。
