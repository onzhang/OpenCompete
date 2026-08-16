# OpenCompete v3 ocb-006 真实 Live 评测：量化结果与优化方案

日期：2026-07-18  
案例：`ocb-006-spotify-vs-apple-music`  
数据集：`opencompete-bench-v3 3.0.0-alpha.1`  
评测契约：`opencompete-competitive-evaluation-v3.0.0`  
`result_kind`：`opencompete_local_race`（**不是** `official_race`）  
官方方法基线 commit：`469cce54ea7f6a63c163d3d9fec879cf289ec484`

## 一、本轮 Live 执行摘要

| 项 | 值 |
|---|---|
| `run_id` | `125b644a-6766-4cac-9e6b-1105c8ed419c` |
| 研究入口 | `ApplicationRunner.create_fixed_scope_run` + v3 案例固定 Scope |
| 研究状态 | **partial** |
| 评测 `evaluation_id` | `eval-v3-8ac646ce-78ca-4dab-b37d-1bf187405052` |
| 评测状态 | **partial_diagnostic**（因 Bundle partial，不进趋势榜） |
| 研究模型 | `openai:gpt-5.6-sol` |
| Judge 模型 | `openai:gpt-5.6-sol`（首轮 `anthropic:deepseek-v4-flash` 认证失败后改用同供应商） |
| 发布门禁 | **通过**（`ok=true`，无硬错误码） |

产物路径：

- 运行目录：`.opencompete-data/runs/125b644a-6766-4cac-9e6b-1105c8ed419c/`
- 报告：`report.md`（约 13 345 字符）
- 评测：`evaluations/eval-v3-8ac646ce-78ca-4dab-b37d-1bf187405052.json`

## 二、量化结果（分项，禁止合成万能总分）

### 2.1 研究产物与成本

| 指标 | Live 本轮 | 备注 |
|---|---:|---|
| sources | 30 | |
| evidence | 33 | |
| claims | 22 | supported 18 / unknown 4 |
| comparisons | 11 | |
| tavily_calls | 27 | |
| input_tokens（研究） | 295 882 | |
| output_tokens（研究） | 59 993 | |
| research `cost_usd` | **1.612** | 本地估价账本 |
| 发布门禁 | 通过 | 结论前置 + 可点击来源 |
| hard_gates | **passed / 0 violations** | 结构化范围与发布完整性 |

partial 的直接原因（`report.limitations`）：

1. 有 **1 个研究单元失败**（其余成功结果已保留）
2. Supervisor 因 **`delegation_limit`** 终止（结构化覆盖已闭合）
3. Researcher 因 **`research_tool_limit`** 强制终止（结构化覆盖已闭合）
4. 提取器 limitations 含英文证据边界说明 + 价格未完全冻结等诚实缺口

### 2.2 RACE（官方四维，Target-first 主分）

Overall 公式：`target_weighted_total / (target_weighted_total + reference_weighted_total)`  

| 维度 | 标准化分 (0–1) | 维度权重 |
|---|---:|---:|
| comprehensiveness | **0.570** | 0.30 |
| insight | **0.514** | 0.25 |
| instruction_following | **0.513** | 0.20 |
| readability | **0.648** | 0.25 |
| **overall** | **0.558** | — |

加权原始合计：`target_weighted_total=9.725`，`reference_weighted_total=7.690`。

稳定性诊断（reverse 不入主分）：

| 项 | 值 |
|---|---:|
| reverse_overall | 0.540 |
| overall_order_gap | 0.019 |
| order_averaged_diagnostic | 0.549 |
| RACE `eligible_for_trend`（稳定性子字段） | true |
| 最大维 gap | readability 0.041 |

### 2.3 FACT（独立，不并入 RACE）

| 指标 | 值 |
|---|---:|
| status | completed |
| eligible | **true** |
| critical_claim_coverage | **0.90** |
| major_claim_coverage | 0.90 |
| critical_unknown_preservation_rate | 1.0 |
| critical_contradiction_count | 0 |
| unsupported_important_target_claim_count | 0 |
| Reference Claim 条数（本 v3 案 FACT 子集） | 5 |

### 2.4 评测成本（独立于研究成本）

| 指标 | 值 |
|---|---:|
| evaluation input_tokens | 32 224 |
| evaluation output_tokens | 6 687 |
| evaluation `cost_usd`（账本字段） | 0.0（尚未把 Judge token 映射进 USD 估价；token 已记录） |

### 2.5 资格

| 项 | 值 |
|---|---|
| `eligible_for_trend` | **false** |
| reasons | `partial_bundle` |

硬门禁通过 + FACT eligible + RACE 稳定，但 **Bundle partial 仍禁止进趋势比较**——这是正确的资格语义。

## 三、与基线对比：优化是否有效？

### 3.1 可比性声明（必须先读）

| 维度 | 基线（2026-07-17 v2.2 报告） | 本轮 Live |
|---|---|---|
| 数据集 | opencompete-bench-v2 2.2.0-beta.4 | opencompete-bench-v3 3.0.0-alpha.1 |
| 质量主分 | **五维** competitive（0–10 绝对 + 相对） | **官方 RACE 四维**（0–1 相对） |
| Reference / Criteria | v2 五维 + 完整 Claim 集 | v3 四维 Criteria + 精简 FACT Claim 子集 |
| Judge | v2 结构化双顺序 Judge（deepseek 成功例） | v3 新 Judge（gpt-5.6-sol） |
| 研究输入契约 | 覆盖清单注入 messages | **UserReportBrief 自然语言** + 内部 ResearchContract 分离 |

因此：

- **禁止**把 v2 `target_overall=6.67/10` 与 v3 `overall=0.558` 直接做算术升降。
- **可以**比较：硬门禁、partial 原因、发布可读性门禁、FACT 覆盖方向、成本量级、稳定性 gap。
- v3 FACT Claim 仅 5 条关键子集，覆盖率 0.90 **不可**直接等同 v2 全量 requirement 覆盖 0.38。

### 3.2 基线关键数字（摘自 v2.2 闭环报告，二次修复 run `821d9414-...`）

| 指标 | 基线 |
|---|---:|
| status | completed |
| hard-gate violations | 0 |
| sources / evidence / claims | 20 / 22 / 25 |
| cost_usd | ≈0.97 |
| tavily_calls | 19 |
| 正式 Judge quality relative | 0.428 |
| FACT critical coverage | 0.382 |
| FACT eligible | false |
| quality stability | unstable |

另有 AI 专家 provisional Overall 相对分 ≈0.384（非正式 Judge）。

### 3.3 有效性判断

**有效（有证据）：**

1. **发布契约 / 可读性路径**  
   - 本轮报告通过确定性 `ReportPublicationGate`，开篇即「结论前置 + 分用户建议 + 可点击官方链接」。  
   - RACE **readability=0.648** 为四维最高，与「审计日志式报告」历史痛点相反。  
   - 这与 v3 设计目标（UserReportBrief 分离、门禁、fallback）一致。

2. **结构化硬门禁保持绿色**  
   - Live hard_gates **0 violations**，与 v2.2 第二次修复后的能力持平，说明 brief 分离后 Extractor/hard gates 仍能从 Scope state 保证覆盖，未把内部契约重新泄漏进用户消息。

3. **FACT 诚实性与支持方向改善（方向性，非严格可比）**  
   - 本轮 FACT eligible=true、critical coverage=0.90、矛盾=0。  
   - 基线正式 Judge FACT 明显更差；即便 v3 Claim 子集更小，至少说明当前产物在「关键事实子集」上不再系统性不合格。

4. **双顺序稳定性**  
   - overall_order_gap≈0.019，远好于基线 `unstable`。  
   - 主分仍严格 Target-first，reverse 只作诊断——符合官方公式不篡改原则。

**部分有效 / 有代价：**

5. **完成态回退为 partial**  
   - 基线第二次运行是 `completed`；本轮因 **delegation_limit / research_tool_limit / 1 研究单元失败** 落 partial。  
   - 覆盖“已闭合”但控制环提前收束 → 资格被 `partial_bundle` 挡住，无法进趋势榜。  
   - 成本上升到 ≈1.61 USD（+66% vs 基线 ≈0.97），token 与 tavily 都更高，却未换来 completed。

6. **Insight / Instruction Following 仍贴近 0.5**  
   - 说明相对 Reference，机制洞察与指令遵循没有拉开优势；可读性改善并未自动转化为更深的决策综合。

7. **Judge 工程仍脆弱**  
   - 首轮 Anthropic 路由认证失败；FACT 契约缺 `reason` 字段曾整次评测失败（已修）。  
   - evaluation_cost 的 USD 未估价，观测不完整。

**总体结论（带 caveat）：**

> 在「用户报告契约 + 硬门禁 + RACE 可读性」维度，本轮优化**看起来有效**；在「研究控制环完成率 / 成本效率 / 洞察深度」维度，**尚未证明有效**，甚至因 partial 与更高成本出现局部回退。  
> 因 v2/v3 主分 schema 不可互换，**不能**宣称“Overall 提升了 X%”。

## 四、按成熟 Agent 研究系统视角的优先优化方案

以下仅为方案，不在本轮实现。每条绑定本轮实测弱点。

### P0 — 必须先修（直接挡住资格与复现）

| ID | 方案 | 绑定证据 | 预期收益 | 风险 |
|---|---|---|---|---|
| A1 | **完成态策略与预算解耦**：`delegation_limit`/`research_tool_limit` 在「覆盖已闭合」时允许 `completed`；仅在覆盖未闭合或关键单元失败时 partial | limitations 明确“覆盖已闭合但仍 partial” | 趋势榜可进；减少假 partial | 过宽会掩盖未闭合洞 |
| A2 | **Supervisor 控制环配额自适应**：按未闭合 coverage 单元动态分配剩余委托，而不是固定 `max_supervisor_model_turns=7` 硬切 | `delegation_limit` + 1 失败单元 | 同样预算换更高闭合率 | 成本上升需上限 |
| A3 | **失败研究单元自动补跑 1 次**（同 brief、收紧 query） | “1 个研究单元失败” | 降低偶发 partial | 延迟与费用 +1 单元 |

### P1 — 研究质量（成熟 agent 核心）

| ID | 方案 | 绑定证据 | 预期收益 |
|---|---|---|---|
| B1 | **Planner 显式“决策表”子目标**：价格矩阵 / TCO / 迁移 / 音频链路 四个必填表，未填则 Researcher 不得退出 | insight/IF ≈0.51；limitations 仍报 Apple 价与 Family 价未冻结 | 提升 insight 与 FACT 关键 claim 命中 |
| B2 | **工具环：Extract 优先于宽搜** — 对 allowed_urls 与命中官方域先 extract 再扩展 | tavily 27 次仍价格缺口 | 同调用数更高证据密度 |
| B3 | **上下文压缩策略改为“按 coverage 单元摘要”**，禁止把长英文 limitations 模板塞进 Extractor 候选 | 多条英文 extractor limitation 噪声 | 降低 token、提高中文报告一致性 |
| B4 | **比较综合二次 pass**：专门节点只填 differences/commonalities/gaps，输入为 claims 索引而非全报告 | 历史 v2 痛点 + 本轮 insight 仍低 | 可比性与因果段质量 |

### P1 — 报告与契约

| ID | 方案 | 绑定证据 |
|---|---|---|
| C1 | 发布门禁增加「关键价格数字可解析到来源」软→硬升级（校准后再硬） | 报告过门禁但价格仍有未确认项 |
| C2 | Fallback/Extractor 中英模板统一为用户语言 | 中文报告夹带英文 limitation |
| C3 | ResearchContract 覆盖进度写入 checkpoint 侧车，供 UI/评测读取，不进 messages | brief 分离已做；缺可观测进度 |

### P1 — 评测与 Judge 可靠性

| ID | 方案 | 绑定证据 |
|---|---|---|
| D1 | Judge 默认与 research 同供应商；启动前做 16-token 连通探针 | deepseek 路由认证失败 |
| D2 | RACE 与 FACT 分账失败隔离（已部分修复）+ evaluation_cost USD 估价 | 首次 FACT 契约失败抹掉 RACE；cost_usd=0 |
| D3 | v3 FACT Claim 集扩回“关键全量”并与 v2 要求可映射，否则覆盖率不可跨版本解释 | 本轮仅 5 条 FACT claim |
| D4 | 稳定性：保留 reverse 诊断，但对 readability 单独报位置敏感度 | readability gap 最大 |

### P2 — 成本 / 延迟

| ID | 方案 | 绑定证据 |
|---|---|---|
| E1 | 分阶段预算：探测 30% → 补洞 50% → 报告 20%；探测阶段禁止 final_report | 成本 1.61 > 基线 0.97 且 partial |
| E2 | 并行 research units 在失败后串行补洞，避免全并行放大尾延迟 | max_concurrent=2 + 单元失败 |
| E3 | 报告 max_tokens 与输入截断按“决策表是否齐全”门控，齐全才写长文 | 13k 字报告 + 高 output tokens |

## 五、建议的下一轮实验矩阵（仍不实现）

1. **同 v3 案、同 Judge、只改 A1/A2**：看 partial→completed 率与成本。  
2. **同控制环、只加 B1 决策表**：看 insight 与 FACT 覆盖。  
3. **三跑独立重复**：报告 median overall、order_gap、completed 率（成熟 agent 评估标准）。  
4. **禁止跨 schema 宣称提升**：v2 五维与 v3 四维永远分表展示。

## 六、证据索引

| 文件 | 用途 |
|---|---|
| `.opencompete-data/live-v3-ocb006-last.json` | live run_id 指针 |
| `.opencompete-data/runs/125b644a-.../manifest.json` | 预算与 entry_mode |
| `.opencompete-data/runs/125b644a-.../report.md` | 用户报告 |
| `.opencompete-data/runs/125b644a-.../evaluations/eval-v3-8ac646ce-....json` | 完整分项评测 |
| `docs/用户测试/2026-07-17-OpenCompete-Bench-v2.2-闭环测试报告.md` | 基线 |

## 七、一句话收束

本轮真实 live 证明：**报告契约与硬门禁方向的优化成立，RACE 可读性与 FACT 子集资格看起来健康**；但 **研究控制环仍在覆盖闭合后因委托/工具上限掉 partial，成本上升且 insight 未拉开**——下一步应按成熟 agent 系统优先修「完成态语义 + 自适应控制环 + 决策表子目标」，而不是继续堆报告文风。
