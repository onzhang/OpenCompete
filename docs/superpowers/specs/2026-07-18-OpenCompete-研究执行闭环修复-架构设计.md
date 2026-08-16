# OpenCompete 研究执行闭环修复 - 架构设计

> 状态：已批准实施  
> 日期：2026-07-18  
> 依据：O03 根因审计、独立反证审核、Tavily/Jina 官方能力调研

## 1. 目标与非目标

本轮修复正式 OpenCompete 研究执行中的确定性缺陷：Supervisor 不得静默丢弃已经生成的研究委托；搜索额度必须按真实 query 约束；固定 Benchmark 的研究输入与 Judge 任务定义必须一致；对已确认 URL 应通过正式研究工具获得可核验的精确证据。所有能力继续复用同一个 `deep_researcher_builder`、OpenCompete Graph、Runner、Checkpointer、materialize、validate 和 Run Repository。

本轮不创建第二套 Supervisor、Researcher 或研究 Graph；不添加案例 ID、产品名、价格、Reference Claim 或领域答案；不扩充生产领域包；不默认启用 Jina；不实现完整 Typed Coverage Ledger；不运行真实付费模型或搜索。

## 2. 可测试需求

### REQ-R01：已生成工具调用不得静默丢失

Supervisor SHALL 分离模型轮次、反思次数和研究委托次数。当前模型已经生成的合法 `ConductResearch` 必须执行，或产生可恢复、可观测的明确拒绝记录。

删除后果：O03 已复现的最后一次定向补证任务会继续在工具执行前被丢弃。

### REQ-R02：强制终止与发布状态一致

系统 SHALL 将 Supervisor/Researcher 的强制终止原因和未执行工具传播到正式 OpenCompete 状态。只有强制终止且仍存在未闭合关键覆盖时发布 `partial`；正常完成或额度耗尽但覆盖已闭合时仍可发布 `completed`。

删除后果：结构完整会继续掩盖研究未完成，用户无法区分完成报告与被截断报告。

### REQ-R03：按真实 query 执行前限额

单次 `tavily_search` SHALL 在发送前限制 query 数，并按实际允许发送的 query 数预留、结算和记录预算。输入十条而上限为三条时，最多发送三次真实搜索请求。

删除后果：研究工具调用上限仍可被任意长度的 `queries` 列表放大。

### REQ-R04：定向 URL 证据获取

正式 Researcher SHALL 能对用户确认或搜索发现后去重的公开 HTTP/HTTPS URL 调用 Tavily 官方 Extract。结果必须逐 URL 保留成功或失败、来源 query、短原文摘录和可验证定位；不得把整页正文写入日志、事件或最终业务产物。

删除后果：目标事实位于页面后段或动态区域时，系统只能反复宽搜并继续产生粗粒度 `Tavily SUMMARY`。

### REQ-R05：Benchmark 任务输入同源

固定 Scope 生成路径和 Judge SHALL 消费同一个规范化任务文本，并把其哈希写入可审计运行身份。不得让 Judge 依据目标系统从未收到的要求扣分。

删除后果：绝对分数的构念有效性继续受输入不一致污染，优化差值无法可靠归因。

### REQ-R06：反硬编码与离线验证

实现 SHALL 只依据通用工具类型、URL、运行状态、冻结 Scope 和配置决策；默认测试不得读取 `.env`、调用真实模型、Tavily 或 Jina。

删除后果：系统可能通过记忆 Benchmark 答案获得虚假提升，并产生不可控费用。

## 3. 方案对比与选择

| 方案 | 内容 | 优点 | 代价 | 结论 |
|---|---|---|---|---|
| A 只增加预算/Prompt | 继续提高循环次数并提示模型补证 | 改动小 | 已证明会扩大 query 和成本，不能修复静默丢任务 | 否决 |
| B 控制语义 + 查询硬预算 + Tavily Extract | 修正式 Graph 内的执行语义；使用现有官方 SDK 定向提取 | 直接对应已证实根因；不新增 Provider 和 Graph | 需扩展工具契约、状态与 Evidence 定位 | 采用 |
| C B + Jina 自动回退 + Typed Ledger | 引入第二读取 Provider和持久化缺口账本 | 能覆盖更多动态页和调度场景 | 成本未知、接入与恢复复杂度高，当前证据不足 | 延后 |

成熟能力选择：默认使用仓库已锁定的 Tavily SDK `Search` 与 `Extract`；普通页面使用 basic，只有 basic 失败或明确动态/表格页面时才允许 advanced。Jina Reader 仅保留为未来显式启用候选，重新评估条件是受控小样本证明 Tavily Extract 在目标页面失败且 Jina 在预算内稳定成功。

最小自研边界：Supervisor 三类计数和终止传播；query 列表截断；Tavily Extract Tool 的薄适配；精确摘录定位；Benchmark 规范化任务文本。SDK 已解决的 HTTP 客户端、批量 Extract、失败列表和 usage 不重复实现。

## 4. 正式数据流与失败语义

```text
固定 ResearchRequest + 冻结 Scope + 规范化任务文本
-> 正式 prepare_research_input
-> 同一 deep_researcher_builder
-> Supervisor 规划/反思/研究委托分别计数
-> Researcher Tavily Search（query 硬预算）
-> 对已确认或发现 URL 调用 Tavily Extract
-> 同一 final_report
-> 同一 materialize（精确 Evidence 定位）
-> 同一 validate
-> 同一 Runner / Checkpointer / Run Repository
```

- Supervisor 达到模型硬上限但当前消息含合法工具调用：先执行额度内调用；超额调用写明确拒绝 ToolMessage。
- Tavily Search 超过单次 query 上限：执行确定性截断，未发送部分不计费，并记录截断数量，不保存 query 正文到诊断事件。
- Tavily Extract 最多二十个 URL 一批；`failed_results` 为逐 URL 软失败，同批成功项必须保留。
- Extract 内容只在进程内用于定位和摘要；业务 Evidence 保存短摘录、URL 关联、字符区间或等价稳定定位，不保存整页正文。
- 强制终止但没有材料性未闭合项时不误标 partial；存在未执行委托、预算拒绝或关键覆盖未闭合时必须 partial。

## 5. 验收标准

1. Supervisor 脚本产生 `think -> ConductResearch -> think -> ConductResearch` 时，最后一次委托必须执行或留下明确拒绝记录。
2. 十条 query、单次上限三条时，mock Tavily 只收到三条；预算、事件和调用计数均为三。
3. 固定网页关键哨兵位于第 2,000 字符后：Search-only 不可见，定向 Extract 可见并生成可验证定位。
4. 二十一个 URL 与混合失败响应按 `20 + 1` 分批；成功项保留，失败项可追踪，不泄露密钥或整页正文。
5. 固定案例创建 run 时，正式研究输入的规范化任务文本及哈希与 Judge 使用值一致。
6. 默认离线单元、集成、Ruff、mypy 和全量 pytest 通过；源码扫描不包含 Benchmark case ID、产品名、价格或 Reference ID 分支。
7. 本轮只证明执行正确性和评测有效性；真实质量提升必须在后续 A、Extract 分开做单变量、多案例、至少三次重复 Pilot 后才可宣称。

## 6. 领域包与后续触发条件

当前不扩充生产领域包。只有至少三个同领域案例、每案至少三次复跑，在本轮通用控制与证据修复后仍出现同型领域缺口，并且领域包带来 critical coverage 至少提升十个百分点、成本增幅不超过 20%，才单独立项。

Typed Coverage Ledger 也不在本轮实现。若本轮修复后仍出现“模型未识别关键缺口或已识别但没有形成任务”，再以最小 `requirement_id/status/evidence_ids/attempts/gap_reason` 契约重新设计。
