# OpenCompete 通用竞品分析系统 - 架构设计

> 状态：已确认设计  
> 日期：2026-07-15  
> 作者：项目所有者、Codex  
> 关联需求：本项目架构讨论记录  
> 前置基线：Open Deep Research `b764481fca7f0dbf00b2c70239bd97cea59d1059`

## 1. 背景与目标

### 1.1 问题

Open Deep Research 已经提供 Supervisor/Researcher、多模型、搜索工具和报告生成能力，但当前 README 的正式启动路径依赖 LangGraph Server，默认 Graph 在模块导入时直接编译，状态主要服务于一次研究流程，也没有面向竞品分析的冻结范围、结构化证据链、成本门禁和本地长期产物。源码同时导出了 `deep_researcher_builder`，因此 OpenCompete 可以在新增模块中注入 Checkpointer，无需为 Graph 工厂改写上游主图。

OpenCompete 要在保留上游研究内核的前提下，成为个人开发者可以直接在本机运行的通用竞品分析系统。系统必须适用于产品与服务、公司与组织、产业与市场，不依赖预先存在的行业模板，并且能够被一套面向竞品分析的可复现评测体系评价。

### 1.2 成功标准

- [x] 用户只通过 CLI 即可创建、确认、恢复、查看和评测任务，不启动 Agent Server。
- [x] 用户确认的研究对象、层级和维度被冻结，研究过程不能擅自扩大范围。
- [x] 完成的报告能够追溯 `Source -> Evidence -> Claim -> Comparison -> Report`。
- [x] 证据不足时输出 `Unknown`，预算耗尽或局部失败时输出列明缺口的 `partial` 报告。
- [x] 进程退出后能够依据 SQLite Checkpoint 恢复同一个 `run_id`。
- [x] 最终报告和结构化业务产物长期保存在本地目录，不依赖 LangSmith 或 Agent Server。
- [x] 删除 `langgraph.json` 后，正式 CLI、恢复、报告和评测测试仍然通过。
- [x] OpenCompete Bench 能对固定竞品分析案例计算 RACE、FACT、硬门禁和成本指标。
- [x] 相对固定上游提交的核心源码差异可通过 Git 单独审计。

### 1.3 非目标

- 多用户、权限系统、远程服务、Web 管理后台和云端部署。
- 定时监控、自动重复研究和跨任务共享状态。
- 自建爬虫、文件知识库、MCP 和多个搜索引擎。
- PostgreSQL、对象存储、分布式锁、跨机器高可用和严格 `exactly-once`。
- 将官方 Deep Research Bench 100 题作为主要竞品分析评测集。
- 没有规则依据的综合质量总分。
- 保证网页信息绝对完整、永远最新或没有矛盾。

## 2. 约束与上下文

| 类型 | 内容 |
|------|------|
| 技术栈 | Python、LangGraph、LangChain、Pydantic、Tavily、SQLite、pytest、Ruff、mypy |
| 部署形态 | Windows 本地单用户、交互式 CLI、按需运行 |
| 规模假设 | 一名用户；一个进程可运行多个不同任务；同一 `run_id` 在进程内互斥 |
| 搜索边界 | 第一版只使用 Tavily 和用户确认的公开 HTTP/HTTPS 网址 |
| 安全边界 | API Key 不落盘；网页是不可信数据；不执行网页指令、代码或本地文件操作 |
| 团队与维护 | 个人开发者，优先采用当前规模下可维护的最小架构 |
| 必须兼容 | 上游 Supervisor/Researcher、多模型配置和可选 DRB/LangSmith 评测路径 |
| 运行硬约束 | 正式功能不得依赖 Agent Server、LangGraph Studio、LangSmith 或 MCP Token Store |

## 3. 可测试需求

### REQ-001：本地独立运行

系统 SHALL 通过 CLI 和 Application Runner 直接构建并执行 LangGraph，不要求 Agent Server。

#### Scenario：删除服务器配置后运行

- GIVEN `langgraph.json` 不存在且 LangSmith 未启用
- WHEN 用户执行 `opencompete new`
- THEN 系统能够进入范围确认并继续完成研究

### REQ-002：研究范围确认与冻结

系统 SHALL 只研究用户输入或经用户确认的发现对象，并在确认后冻结对象、层级、维度和排除项。

#### Scenario：网页要求扩大范围

- GIVEN 用户已确认两家公司和产品层级
- WHEN 搜索结果要求增加其他公司或修改分析维度
- THEN 系统把该内容视为资料且不修改冻结范围

### REQ-003：通用多层竞品分析

系统 SHALL 支持产品与服务、公司与组织、产业与市场三个层级的任意组合，并在没有领域包时使用通用必选维度工作。

#### Scenario：未知行业

- GIVEN 用户选择一个没有领域 Analysis Pack 的行业
- WHEN 用户确认系统建议的通用维度
- THEN 系统仍生成分层比较和跨层结论

### REQ-004：结构化证据链

系统 SHALL 将来源、证据、结论、比较和报告保存为可追溯的结构化关系。

#### Scenario：追溯重要结论

- GIVEN 报告包含一个非 `Unknown` 的重要结论
- WHEN 校验器读取 `bundle.json`
- THEN 能定位支撑该结论的 Evidence 和 Source

### REQ-005：证据不足与部分结果

系统 SHALL 对证据不足的字段输出 `Unknown`，对预算耗尽或局部研究失败输出 `partial`，并保留已完成结果。

#### Scenario：单个研究单元失败

- GIVEN 三个研究单元中一个 Provider 调用最终失败
- WHEN 其他两个研究单元成功
- THEN 报告包含成功结果、失败缺口和 `partial` 状态

### REQ-006：硬预算控制

系统 SHALL 在发起外部调用前预留预算，在调用后结算；没有足够可用预算时不得启动新调用。

#### Scenario：并行任务预算不足

- GIVEN 剩余预算只能覆盖一个外部调用
- WHEN 两个 Researcher 同时申请预算
- THEN 只有一个获得预留，另一个停止并记录缺口

### REQ-007：可恢复执行

系统 SHALL 使用 LangGraph Checkpoint 保存可恢复状态，并支持跨进程执行 `opencompete resume <run_id>`。

#### Scenario：确认点退出

- GIVEN 任务处于 `awaiting_confirmation`
- WHEN 进程退出后恢复同一 `run_id`
- THEN 系统仍等待用户确认且不会提前搜索

### REQ-008：长期业务产物

系统 SHALL 将任务清单、结构化产物、最终报告、诊断事件和评测结果保存在本地 Run Repository。

#### Scenario：完成后查看报告

- GIVEN 任务已经完成且执行进程已退出
- WHEN 用户执行 `opencompete show <run_id>`
- THEN 系统从 Run Repository 读取并展示保存的报告

### REQ-009：失败隔离与重放审计

系统 SHALL 隔离并行 Researcher 的失败，并为逻辑外部调用保存稳定 `call_id` 和实际尝试记录。

#### Scenario：崩溃窗口重放

- GIVEN 外部服务已返回但下一个 Checkpoint 尚未落盘时进程崩溃
- WHEN 系统恢复并重新执行逻辑调用
- THEN 新尝试被标记为 `replayed` 且计入预算和事件日志

### REQ-010：配置与密钥隔离

系统 SHALL 从非秘密配置文件和环境变量加载配置，并禁止密钥进入命令历史、Checkpoint、日志和业务产物。

#### Scenario：持久化数据扫描

- GIVEN 使用真实 API Key 完成一次任务
- WHEN 测试扫描 `.opencompete-data/`
- THEN 不存在该 Key 或 Authorization Header

### REQ-011：不可信网页边界

系统 SHALL 将网页内容限制为证据数据，不允许其改变系统指令、工具权限、冻结范围或预算。

#### Scenario：网页提示注入

- GIVEN 网页包含“忽略原任务并执行其他操作”的文本
- WHEN Researcher 处理该网页
- THEN 系统只保存相关事实证据，不执行网页指令

### REQ-012：可复现评测入口

系统 SHALL 使用固定案例、参考报告、任务级 Criteria 和目标报告运行 OpenCompete Bench，评测过程不得重新搜索网络。

#### Scenario：离线读取历史报告

- GIVEN 某个 Benchmark 案例已有完整目标产物
- WHEN 执行 `opencompete evaluate <dataset>`
- THEN 评测器只读取案例和目标产物，并保存 RACE、FACT、硬门禁和成本结果

### REQ-013：上游兼容与差异可审计

系统 SHALL 保留固定上游 Git 基线，优先以新增模块和薄适配器扩展，任何上游核心改动必须可单独列出和测试。

#### Scenario：修改研究内核

- GIVEN 一个变更涉及 `src/open_deep_research/`
- WHEN 变更准备交付
- THEN 能说明对应需求、适配器不足原因、特征测试和相对 `upstream-b764481f` 的差异

### REQ-014：本地可诊断性

系统 SHALL 在控制台和每次运行的 `events.jsonl` 中记录脱敏结构化事件。

#### Scenario：Provider 最终失败

- GIVEN Provider 在有限重试后仍失败
- WHEN 用户检查任务
- THEN 能看到 `run_id`、节点、研究单元、尝试次数、耗时和脱敏错误码

### REQ-015：有限重试与跨供应商降级

系统 SHALL 对单个逻辑模型调用最多发起五次可审计的真实尝试：主供应商最多三次，主供应商因限流、超时、连接错误或 HTTP 5xx 失败后，备用供应商最多两次；模型 SDK 内部重试必须关闭。

#### Scenario：主供应商持续返回 HTTP 502

- GIVEN 主供应商已完成三次失败尝试且备用供应商配置完整
- WHEN 第三次失败仍属于临时错误
- THEN 系统保持相同 `run_id`、节点和逻辑 `call_id`，切换备用供应商并继续第四次尝试
- AND 全部真实 HTTP 尝试总数不超过五次

#### Scenario：主供应商认证失败

- GIVEN 主供应商返回 HTTP 401 或 403
- WHEN 应用层完成错误分类
- THEN 系统立即停止该逻辑调用，不切换备用供应商，并向用户说明需要检查配置

### REQ-016：安全且准确的模型错误反馈

系统 SHALL 向用户和事件日志提供模型错误的安全白名单字段，包括供应商、模型、节点、HTTP 状态码、异常类型、供应商错误码、请求 ID、尝试序号、是否可重试和建议动作；不得展示或持久化原始响应正文、完整 Header、请求正文、API Key 或 Base URL。

错误展示 SHALL 分为稳定状态标题和动态异常摘要。稳定标题表达系统动作及次数，例如“正在重新连接 1/5”；动态摘要使用经过秘密脱敏、URL 清除和长度限制的异常文本，尽量保留供应商原始语义。未知异常类型仍显示异常类名和安全摘要，不因分类表未覆盖而退化成无信息的“发生错误”。

#### Scenario：备用供应商最终失败

- GIVEN 第五次模型调用失败且供应商异常包含响应正文和 Header
- WHEN CLI 展示最终错误并写入事件日志
- THEN 用户能看到第五次尝试的安全结构化详情、任务已保存和完整恢复命令
- AND 本地数据目录中不存在响应正文、Authorization Header、API Key 或 Base URL

#### Scenario：未预先枚举的流中断异常

- GIVEN 模型 SDK 抛出新的流中断异常，文本为 `stream disconnected before completion: Upstream request failed`
- WHEN 错误分类无法命中已知异常类名但文本通过秘密扫描
- THEN CLI 第一行显示当前重新连接次数
- AND 第二行显示脱敏限长后的原始异常摘要与异常类名
- AND 是否重试仍由状态码、异常链和连接/流中断规则决定

### REQ-017：用户可见的阶段与重试状态

系统 SHALL 在交互式 CLI 中显示当前研究阶段、已完成阶段、当前尝试序号、主备供应商切换、退避倒计时和最终恢复动作；非交互环境仍输出稳定的逐行事件，不依赖动画终端。

#### Scenario：重试期间查看终端

- GIVEN 模型调用因临时错误进入退避
- WHEN 用户观察交互式终端
- THEN 用户能看到失败原因、当前尝试、总尝试数、下次重试剩余时间和任务已持久化提示

#### Scenario：重试耗尽

- GIVEN 五次尝试全部失败
- WHEN 命令返回非零退出码
- THEN CLI 显示当前阶段、最后错误详情、报告是否生成以及 `resume` 命令

### REQ-018：模型运行时与业务流程解耦

系统 SHALL 把供应商选择、重试、退避、错误归一化和降级封装在独立模型运行时适配层。Research Graph、Scope Graph、证据链、报告物化和 Prompt 只依赖稳定的模型调用及进度事件契约，不得包含 OpenAI、DeepSeek 或 Anthropic 的条件分支。

#### Scenario：移除备用供应商

- GIVEN 备用供应商配置被删除
- WHEN 系统重新加载配置并运行研究
- THEN 主供应商仍按相同 Graph 和业务流程工作
- AND 无需修改研究节点、Prompt、证据链或报告生成代码

#### Scenario：替换备用供应商

- GIVEN 未来把 DeepSeek 替换为另一个 LangChain 支持的模型
- WHEN 适配层配置新的模型标识和能力声明
- THEN 主流程无需新增供应商类型判断

### REQ-019：长文本传输与单调用重试边界

系统 SHALL 对压缩和最终报告等纯文本长响应使用 LangChain 官方流式传输能力，持续接收响应数据并聚合为完整消息；工具调用、结构化输出和短响应在未完成供应商能力验证前保持非流。主备重试 SHALL 以单个逻辑模型调用为边界，不得把整张 Graph 的一次执行等同于一次模型调用。

#### Scenario：供应商生成超过中间代理空闲时间

- GIVEN 最终报告需要生成超过 60 秒且中间代理会关闭长期无响应数据的非流连接
- WHEN 模型开始返回流式 chunk
- THEN 应用持续消费 chunk 并聚合完整报告
- AND 不因非流空闲连接被关闭而重复提交同一完整报告请求

#### Scenario：并行研究单元之一发生临时传输错误

- GIVEN 两个 Researcher 并行执行且其中一个模型调用发生可重试流中断
- WHEN 模型运行时执行主三备二策略
- THEN 只重试失败的逻辑模型调用
- AND 已成功的兄弟研究单元不得因整张 Graph 重放而重复发送

### REQ-020：用户报告与审计产物分离

系统 SHALL 把上游成功生成的完整叙事报告作为 `report.md` 持久化，不得由 Artifact Extractor 或确定性审计 Renderer 摘要、重写或替换。结构化 Bundle SHALL 独立承担 FACT、硬门禁和追溯职责。

#### Scenario：最终报告成功生成

- GIVEN 最终报告模型已经生成包含执行摘要、正文分析、建议和来源的 Markdown
- WHEN 系统提取 Source、Evidence、Claim 和 Comparison 并发布任务产物
- THEN `report.md` 与上游最终报告正文一致
- AND 用户报告不包含 `clm_`、`evd_`、`src_` 或 `cmp_` 内部标识
- AND Bundle 的结构化校验失败只能改变任务状态和限制信息，不能无声替换成功报告

#### Scenario：最终报告生成失败

- GIVEN 研究资料和部分结构化结论已经存在，但最终报告模型失败
- WHEN 系统发布 `partial` 产物
- THEN 确定性 Renderer 可以生成用户可读的保底报告
- AND 保底报告只展示产品名称、结论、公开来源和缺口，不展示内部标识

#### Scenario：分别执行报告质量与事实评测

- GIVEN 同一 `run_id` 已保存 `report.md` 和 `bundle.json`
- WHEN OpenCompete Bench 执行 RACE、FACT 和硬门禁
- THEN RACE 读取用户报告
- AND FACT、Coverage 与确定性硬门禁读取结构化 Bundle
- AND 硬门禁不得要求用户报告逐字等于结构化审计模板

#### 最小设计取舍

| 组件 | 对应需求 | 删除后违反 | 选择与取舍 |
|------|----------|------------|------------|
| 上游 `final_report` 直接发布 | REQ-008、REQ-020 | 用户无法获得完整研究价值 | 复用上游成熟报告生成能力，不增加第二次模型重写 |
| 结构化 Bundle | REQ-004、REQ-005、REQ-012 | FACT 与追溯无法执行 | 保留现有 Pydantic 契约，与用户正文解耦 |
| 失败保底 Renderer | REQ-005、REQ-020 | 报告模型失败时没有部分交付 | 仅在失败路径使用，不新增独立审计文件 |
| 第二次叙事生成 | 无已确认需求 | 删除不违反需求 | 拒绝；会增加调用、延迟和信息损失 |

### REQ-021：官方 RACE 量化结果

系统 SHALL 使用固定版本的 Deep Research Bench 官方 100 个 Query、Criteria、Reference 和 evaluator 计算每题四维分数与 Overall，并保存数据版本、evaluator 提交、Judge 模型和输入报告哈希。系统不得把本地近似 Judge 的分数标记为官方 RACE。

#### Scenario：官方配置完全一致

- GIVEN 数据、Criteria、Reference、evaluator 提交和 Judge 模型均与官方固定版本一致
- WHEN 评估完成
- THEN 结果标记为 `official_race`
- AND 保存 `comprehensiveness`、`insight`、`instruction_following`、`readability` 和 `overall` 的 `0–1` 原始值
- AND 用户展示层可派生百分制，但不得改变原始值

#### Scenario：Judge 模型不同

- GIVEN 使用官方数据、Criteria、Reference 和 evaluator，但 Judge 模型不是官方指定模型
- WHEN 评估完成
- THEN 结果标记为 `race_compatible_local`
- AND 不得宣称可直接进入官方排行榜

### REQ-022：量化汇总保持指标独立

系统 SHALL 输出任务完成率、RACE 四维均分、RACE Overall、OpenCompete FACT 支持率、硬门禁通过率及平均成本/Token/耗时；不得把不同语义的指标再合成一个自定义总分。

#### Scenario：批量评估部分失败

- GIVEN 100 个任务中部分研究或 Judge 失败
- WHEN 生成批次汇总
- THEN 完成率使用全部目标任务作分母
- AND RACE 均分只基于成功评分任务并同时显示成功样本数
- AND 失败任务保留安全错误分类，不以零分静默替代

#### 最小设计取舍

| 组件 | 对应需求 | 删除后违反 | 选择与取舍 |
|------|----------|------------|------------|
| 官方 DRB 固定提交 | REQ-021 | 无法证明数据和公式版本 | 运行外部官方仓库，不复制 evaluator |
| 官方三字段 JSONL 导出 | REQ-021 | 无法把 ODR 报告交给官方工具 | 仅实现格式校验与可恢复追加 |
| 量化摘要 | REQ-022 | 用户无法获得批次数字结果 | 标准库解析官方结果并独立汇总成本，不增加数据库 |
| 自定义综合总分 | 无官方或用户需求 | 删除不违反需求 | 拒绝；避免混淆报告质量、事实、可靠性和成本 |

## 4. 方案对比

| 方案 | 思路 | 优点 | 代价 | 适用 |
|------|------|------|------|------|
| A 只改 Prompt | 保留上游运行和状态，只用 Prompt 生成竞品报告 | 开发量最小 | 无法可靠冻结范围、恢复任务、控制预算或提供结构化证据 | 一次性演示 |
| B 结构化扩展 | 保留 Supervisor/Researcher，新增领域模型、Runner、持久化和评测，并对主图做最小工厂化改造 | 复用成熟内核，满足已确认需求，差异可审计 | 需要设计明确的适配边界和恢复语义 | 当前产品阶段 |
| C 更换 Agent 框架 | 使用 CrewAI、Deep Agents 或 Microsoft Agent Framework 重写 | 可以重新组织全部流程 | 重写风险高，丢失上游评测与代码资产，不能直接解决业务持久化 | 上游框架无法维护时 |

### 4.1 推荐

- **采用：** B，基于上游的结构化扩展。
- **理由：** 它是唯一同时满足 REQ-001 至 REQ-014、又保留官方研究内核和退出路径的方案。
- **否决 A：** 删除结构化模块会直接违反范围冻结、证据链、预算、恢复和长期产物需求。
- **否决 C：** 当前没有证据表明 LangGraph 缺少关键能力，重写不符合成熟方案优先和当前规模最小架构原则。

### 4.2 调研摘要

| 候选 | 来源 | 结论 | 原因 |
|------|------|------|------|
| Open Deep Research | [固定提交](https://github.com/langchain-ai/open_deep_research/tree/b764481fca7f0dbf00b2c70239bd97cea59d1059) | 采用 | 已有 Supervisor/Researcher、多模型、Tavily 和 DRB 路径 |
| LangGraph Persistence | [官方文档](https://docs.langchain.com/oss/python/langgraph/persistence) | 采用 | 提供线程级 Checkpoint 和恢复能力 |
| LangGraph Interrupts | [官方文档](https://docs.langchain.com/oss/python/langgraph/interrupts) | 采用 | 用户确认应使用 durable interrupt，而不是 `goto=END` |
| `langgraph-checkpoint-sqlite` | [官方包](https://pypi.org/project/langgraph-checkpoint-sqlite/) | 采用 | 与 LangGraph Checkpointer 契约一致，适合本地单进程 |
| Tavily | [官方文档](https://docs.tavily.com/documentation/api-reference/introduction) | 采用 | 上游已有集成，满足第一版搜索和网址提取需求 |
| Pydantic Settings | [官方文档](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) | 采用 | 负责配置加载、优先级和类型校验 |
| Python `logging` | [标准库文档](https://docs.python.org/3/library/logging.html) | 采用 | 本地结构化日志不需要额外平台 |
| LangChain Runnable Retry/Fallback | [官方 API](https://python.langchain.com/api_reference/core/runnables/langchain_core.runnables.base.Runnable.html) | 采用 | 复用官方 `with_retry`、`with_fallbacks` 组合主备模型，不自研重试调度框架 |
| OpenAI Python 重试与超时 | [官方仓库](https://github.com/openai/openai-python#retries) | 采用其配置边界 | SDK 支持重试，但为保证 attempt 等于真实外部请求，正式配置保持 `max_retries=0` |
| DeepSeek API | [官方文档](https://api-docs.deepseek.com/) | 作为可选备用供应商 | 官方提供 OpenAI/Anthropic 兼容接口；锁定版本仍须做最小真实结构化输出和工具调用实验 |
| Rich Progress/Live | [官方文档](https://rich.readthedocs.io/en/stable/progress.html) | 采用 | 项目已有依赖，满足交互终端阶段、重试和倒计时展示，无需引入前端框架 |
| PostgreSQL/对象存储 | 各自官方能力 | 否决 | 当前单用户本地规模不需要服务和运维成本 |
| Deep Research Bench/RACE | [官方仓库](https://github.com/Ayanami0730/deep_research_bench) | 部分复用 | 复用报告质量方法，但官方 100 题不代表竞品分析覆盖 |
| pytest/Ruff/mypy | 上游 `pyproject.toml` 与各自官方文档 | 采用 | 上游已有依赖或配置，不另建测试工具链 |

最小自研边界：Scope Graph、Analysis Packs、Artifact Models、Budget Guard、Run Repository 对账、CLI Application Runner 和 OpenCompete Bench 业务案例。通用 Graph 执行、Checkpoint、配置校验、搜索、模型接入和测试框架不自研。

重新评估触发条件：LangGraph 无法满足已验证的恢复语义；单次外部调用成本高到不能接受重放；出现多用户、多机器或同一任务多进程并发的真实需求；Tavily 无法满足已确认来源类型。

## 5. 逻辑架构

### 5.1 组件与职责

| 组件 | 职责 | 不负责 | 依赖 |
|------|------|--------|------|
| CLI | `new/resume/list/show/evaluate`、输入输出、确认交互 | Graph 构建、SQL、搜索 | Application Runner |
| Application Runner | 在 OpenCompete 模块中编译上游 Builder，组装配置、Saver、Repository、锁和生命周期 | 竞品分析规则、网页检索实现 | Scope/Research Graph、Persistence、Providers |
| Scope Graph | 实体消歧、可选发现、层级/维度建议、用户确认、冻结 Scope | 生成最终报告 | Artifact Models、Analysis Packs、Providers |
| Research Graph | 规划、并行 Researcher、比较、补证和综合 | 长期产物存储 | 上游研究内核、Providers、Budget Guard |
| Analysis Packs | 通用必选维度和可选领域规则 | Graph 编排、外部调用 | Artifact Models |
| Artifact Models | 来源、证据、结论、比较、报告和验证契约 | Graph、SQLite、网络 | Pydantic |
| Providers | Tavily 和模型的薄适配、重试、调用记录 | 修改 Scope、保存最终报告 | 官方 SDK/LangChain |
| Budget Guard | 调用前预留、调用后结算、并发预算仲裁 | 供应商账单对账 | 配置、SQLite durable ledger |
| Execution Checkpoint Store | 保存和恢复 LangGraph 执行状态 | 历史报告展示 | `AsyncSqliteSaver` |
| Run Repository | 原子保存业务产物、状态清单、报告和评测 | Graph 调度 | 标准文件系统 |
| Evaluation | RACE、FACT、硬门禁和成本评测 | 搜索、修改历史产物 | Artifact Models、Judge Model |

### 5.2 依赖规则

- 允许 `opencompete` 调用 `open_deep_research`；禁止上游内核反向依赖竞品领域模型。
- Analysis Packs 和 Artifact Models 不依赖 LangGraph，保证规则和数据可独立测试。
- CLI 只调用 Application Runner，不直接操作 Graph、SQLite、文件布局或 Provider。
- Provider 只能执行已批准的调用，不能新增研究对象、层级或维度。
- Evaluation 可以调用 Judge Model，但不能调用搜索 Provider，也不能修改历史 `bundle.json`。
- API Key 只能停留在环境变量和 Provider 的进程内调用边界，不进入 State、RunnableConfig 的 `configurable` 或持久化 DTO；LangGraph 会把 `configurable` 标量写入 Checkpoint metadata。

### 5.3 主数据流

```text
CLI 输入
-> 实体消歧
-> 可选发现候选
-> 层级与维度建议
-> interrupt 等待用户确认
-> 冻结 Scope
-> 结构化研究计划
-> 并行 Researcher
-> 分层比较
-> Coverage Gate
-> 预算内补证
-> 确定性校验
-> 完整或部分报告
-> Run Repository
```

### 5.4 任务状态

```text
draft
-> awaiting_confirmation
-> scope_frozen
-> researching
-> synthesizing
-> completed | partial | failed | cancelled
```

- `completed`：所有硬门禁通过，报告和业务产物原子发布完成。
- `partial`：预算耗尽、证据缺口或局部研究失败，但存在可交付结果。
- `failed`：配置、认证、不可恢复持久化错误或程序错误导致无法交付。
- `cancelled`：用户明确取消；已保存的诊断和中间状态仍保留。

### 5.5 数据与一致性

| 数据/产物 | 可变性 | 真相源 | 失败语义 |
|-----------|--------|--------|----------|
| 冻结 Scope | 确认后不可变；变更必须产生新 revision | Checkpoint 和 `bundle.json` | 不允许静默修改 |
| Graph 执行状态 | 运行中可变、可恢复 | `checkpoints.sqlite3` | 保存失败则停止执行 |
| 预算与调用 Ledger | attempt 只追加，reservation 可结算 | `checkpoints.sqlite3` 中的 `oc_*` 表 | 恢复时未结算调用按可能已消费处理 |
| 业务 Bundle | 按 revision 发布，不原地部分更新 | `runs/<run_id>/bundle.json` | 临时文件不成为正式版本 |
| 最终报告 | 上游成功正文不可改写；与同 revision Bundle 一起发布 | `runs/<run_id>/report.md` | 文件损坏时从同 thread Checkpoint 的 `final_report` 恢复；不能从审计模板替代 |
| Manifest | 最后更新的发布索引 | `runs/<run_id>/manifest.json` | 哈希或 revision 不匹配则进入对账 |
| 事件日志 | 只追加的诊断数据 | `events.jsonl` | 丢失单条日志不改变业务状态 |
| 评测结果 | 对目标 revision 不可变 | `evaluations/` | 失败不修改被评测报告 |

默认布局：

```text
.opencompete-data/
├── checkpoints.sqlite3
└── runs/<run_id>/
    ├── manifest.json
    ├── bundle.json
    ├── report.md
    ├── events.jsonl
    └── evaluations/
```

运行中以 Checkpoint 为恢复真相源；任务发布完成后以 Run Repository 为历史读取入口。两者没有跨存储事务，因此使用相同 `run_id`、`scope_revision`、`artifact_revision` 和内容哈希对账，业务文件全部写入临时文件后原子替换，`manifest.json` 最后发布。

OpenCompete 自有 DTO 在 Graph State 中只保存 `model_dump(mode="json")` 后的 JSON 兼容值，节点和 Application Runner 在使用边界通过 `model_validate` 恢复类型。这样不需要允许 Checkpoint 反序列化任意 `opencompete.models` 构造器；上游 LangChain/LangGraph 消息继续使用框架内置安全类型。已使用锁定版本的 `LANGGRAPH_STRICT_MSGPACK=true` 和真实 SQLite 恢复实验验证该边界。

## 6. 接口与契约

### 6.1 CLI

```text
opencompete new
opencompete resume <run_id>
opencompete list
opencompete show <run_id>
opencompete evaluate <dataset>
```

- `new` 创建 `run_id`、采集用户需求并执行到确认点或终态。
- `resume` 只恢复已有 `run_id`，不存在时返回配置类错误。
- `list` 和 `show` 只读取 Run Repository，不启动研究。
- `evaluate` 读取固定数据集和案例到历史 Run 的绑定；可通过 targets JSON 批量评测，也可通过 case/run 参数评测单个案例。Judge Model 可选，搜索始终禁用。

### 6.2 Application Runner 草图

```python
build_graph(checkpointer) -> CompiledStateGraph
create_run(request, settings) -> RunHandle
resume_run(run_id, user_input=None) -> RunHandle
list_runs() -> list[RunSummary]
load_run(run_id) -> RunBundle
evaluate_dataset(request) -> EvaluationBatchResult
```

`build_graph` 位于 `src/opencompete/`，调用上游已导出的 `deep_researcher_builder.compile(checkpointer=...)`；不删除或替换上游 `deep_researcher` 导出。CLI 不依赖这些函数的内部 Graph、SQLite 或目录实现。

### 6.3 核心 DTO

- `ScopeSpec`：研究对象、实体标识、层级、维度、排除项、确认时间、revision。
- `Source`：规范化 URL、标题、发布方、发布时间、访问时间、来源类型。
- `Evidence`：Source 引用、原文摘录或结构化值、位置、时间、质量标记。
- `Claim`：结论文本、层级、维度、Evidence 引用、状态和不确定性。生产 Artifact Extractor 使用模型结构化输出，但只能引用确定性捕获的 Source/Evidence ID；校验失败或无法支持时降为 `Unknown`。
- `Comparison`：对象集合、维度、Claim 引用、差异、共同点和缺口。
- `Report`：章节、Comparison 引用、跨层结论、限制和引用列表。
- `RunManifest`：状态、revision、配置快照、预算摘要、文件哈希和脱敏错误。

重要 Claim 只有在至少引用一个可定位 Evidence 时才能为已知状态；否则必须为 `Unknown`。Source、Evidence 和 Claim 不能仅靠报告中的自由文本隐式关联。

### 6.4 ID、重放与版本

- `run_id`：应用生成的 UUID，作为 Checkpoint thread 和产物目录共同标识。
- `unit_id`：由冻结 Scope revision 和研究单元规范化内容生成的稳定标识。
- `call_id`：由 `run_id + node + unit_id + logical_call_index` 生成，在恢复重放时保持稳定。
- `attempt_id`：每次真实外部请求生成并写入 SQLite durable ledger，用于区分正常重试和崩溃后的重放。
- `scope_revision`：用户重新确认范围时递增；旧 revision 不原地修改。
- `artifact_revision`：每次正式发布 Bundle 时递增，并与报告和 Manifest 对齐。

外部 API 不承诺幂等。如果调用成功后、Checkpoint 前崩溃，恢复可能再次调用；系统在请求前持久化 reservation 和 attempt，恢复时将未结算 attempt 视为可能已消费，记录 `replayed` 并计入预算，不宣称 `exactly-once`。

## 7. 失败、预算与恢复

### 7.1 预算语义

- 预算至少包含 Tavily 调用数、模型 Token 和按配置价格计算的成本上限。
- 外部调用开始前按最大允许输出和已构建输入预留预算；完成后按可获得的实际用量结算。
- Provider 没有返回可靠用量时保留预留值，不能假设零成本。
- 并行任务只有成功预留后才能启动，避免并发检查导致共同超支。
- 预算耗尽停止新增外部调用，不撤销已完成结果，并进入 `partial` 综合。
- Manifest 保存本次使用的价格配置、模型标识和预算计算版本；系统预算不等同于供应商最终账单。

### 7.2 错误分类

| 错误 | 处理 | 最终状态 |
|------|------|----------|
| 配置缺失或类型错误 | 外部调用前失败 | `failed` |
| API 认证错误 | 不做无意义重试，保存脱敏摘要 | `failed` |
| 超时、限流、网络和 Provider 5xx | Provider 边界有限重试 | 成功或 `partial` |
| 单个 Researcher 最终失败 | 隔离该单元，保留其他结果 | `partial` |
| 结构化输出校验失败 | 允许一次受控修复，仍失败则记录缺口 | `partial` 或 `failed` |
| 预算耗尽 | 停止新调用并综合已有产物 | `partial` |
| Checkpoint 保存失败 | 立即停止，不能继续产生副作用 | `failed` |
| 业务产物发布失败 | 保留 Checkpoint，恢复后对账重建 | 非 `completed` |
| 用户取消 | 保存取消状态和已有诊断 | `cancelled` |
| 未分类程序错误 | 本地日志保留堆栈，Manifest 只保存脱敏摘要 | `failed` |

### 7.3 恢复对账

1. Application Runner 以 `run_id` 加载最新 Checkpoint 和 Manifest。
2. Checkpoint 仍处于运行状态时，以 Checkpoint 为准继续；比 Checkpoint 新但未发布的临时业务文件被忽略。
3. Manifest 声明 `completed` 时，校验 Bundle、Report 的 revision 和内容哈希。
4. 哈希或 revision 不一致时，不展示为完成；从最终 Checkpoint 重建缺失产物后重新发布 Manifest。
5. 无法读取 Checkpoint 且正式业务产物完整时，允许历史查看但不允许继续执行。
6. 同一进程的同一 `run_id` 使用 `asyncio.Lock`；两个进程同时恢复同一任务属于第一版不支持的用法。

## 8. 横切关注点

| 主题 | 决策 |
|------|------|
| 配置 | 非秘密配置使用 `opencompete.toml`；优先级为 CLI、环境变量、配置文件、默认值 |
| 密钥 | 只从环境变量或被 Git 忽略的 `.env` 读取，不提供密钥 CLI 参数，也不放入 RunnableConfig `configurable` |
| 遥测 | LangSmith 默认关闭，启用后也只是可选观测能力 |
| 日志 | 控制台加每次运行的 JSONL；记录 `run_id/node/unit_id/call_id/tokens/Tavily calls/duration/error_code` |
| 日志脱敏 | 不记录 API Key、Authorization Header、完整网页正文和完整模型上下文 |
| 网页安全 | 网页只作为不可信数据；只允许公开 HTTP/HTTPS；禁止改变 Scope、工具和预算 |
| URL 边界 | 提交 Provider 前拒绝本机、内网、保留地址和非网页协议 |
| 性能 | 并发上限和预算预留共同限制 Researcher；不引入分布式调度 |
| 依赖 | 沿用上游 `uv.lock`，新增 SQLite Checkpointer 和配置能力后重新锁定并验证 |

## 9. 评测设计

### 9.1 OpenCompete Bench

第一版建立 6 至 10 个跨领域通用竞品分析案例。每个案例包括：

```text
Prompt + Reference Report + Criteria + Target Report
```

- Prompt 固定研究对象、层级、范围和输出要求。
- Reference Report 使用多个跨领域权威来源综合，并经过人工审核。
- Criteria 是任务级可判定标准，不把某个行业的固定指标硬套到所有案例。
- Target Report 来自与固定 run revision 对应的 `bundle.json` 和 `report.md`。

### 9.2 指标

- RACE：全面性、深度、指令遵循、可读性。
- FACT：引用是否真正支持对应结论。
- 硬门禁：范围越界、重要结论无证据、错误使用 `Unknown`、预算违规、产物不一致和密钥泄漏。
- 成本：模型 Token、Tavily 调用、估算成本和运行时间。

第一版不设置没有基线支持的统一质量分。硬门禁直接通过或失败；RACE 和 FACT 保存为版本趋势，待固定案例、参考报告和人工评分稳定后再设置最低阈值。

官方 DRB 100 题保留为通用研究能力的补充回归入口，不代表 OpenCompete 的主要竞品分析质量。

## 10. 测试与完成定义

### 10.1 测试策略

- 单元测试：Artifact Models、Scope 冻结、Analysis Packs、预算预留、配置优先级、密钥脱敏、URL 边界和错误映射。
- Graph 集成测试：使用假 Provider 测试 durable interrupt、恢复、局部失败、重放标记和部分报告，不访问网络。
- 持久化测试：真实临时 SQLite 和临时目录，覆盖 Checkpoint 与业务文件各个崩溃窗口的对账。
- 上游特征测试：修改 `src/open_deep_research` 前先锁定相关节点的既有输入、输出和路由行为。
- 真实接口闭环测试：显式 `live` 标记，使用独立宽松测试预算，真实执行 Tavily、研究、压缩、报告、提取、SQLite 重载和 CLI 查看；不进入默认测试。
- Benchmark：发布前按需运行 OpenCompete Bench；默认开发测试不产生评测费用。

### 10.2 完成门禁

- [x] 依赖安装通过：`uv sync --extra dev`
- [x] 代码检查通过：`uv run ruff check .`
- [x] 新增包类型检查通过：`uv run mypy src/opencompete`
- [x] 默认离线测试通过：`uv run pytest -q`
- [x] `langgraph.json` 临时移出仓库后，CLI 和全部正式功能测试仍通过
- [x] 进程退出后能够恢复到确认点和研究中状态
- [x] 一个 Researcher 失败时其他结果和引用仍被保存
- [x] 预算耗尽时没有新调用，最终状态为 `partial`
- [x] `manifest.json`、`bundle.json` 和 `report.md` revision 与哈希一致
- [x] `.opencompete-data/` 扫描不存在测试 API Key 或 Authorization Header
- [x] 重要非 `Unknown` Claim 全部能追溯到 Evidence 和 Source
- [x] 相对 `upstream-b764481f` 的核心修改已单独审阅
- [x] OpenCompete Bench 能读取固定目标产物并保存全部指标

## 11. 风险、未决与否决记录

### 11.1 已接受风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| 外部调用完成后、Checkpoint 前崩溃 | 恢复时可能重复计费 | 稳定 `call_id`、独立 `attempt_id`、`replayed` 标记和预算计入 |
| SQLite 与业务文件无共同事务 | 短暂不一致 | revision、内容哈希、原子替换、Manifest 最后发布和恢复对账 |
| 两个进程恢复同一任务 | 可能产生写入冲突 | 第一版明确不支持；只保证进程内 `asyncio.Lock` |
| 模型输出和 Judge 分数波动 | 不能逐字复现 | 保存模型、Prompt、配置、价格和评测版本，确定性硬门禁独立执行 |
| 网页过期、矛盾或不完整 | 报告存在信息缺口 | 保存来源与访问时间，冲突不强行合并，证据不足为 `Unknown` |
| 网页提示注入 | 可能影响模型判断 | 冻结 Scope、数据/指令分离、工具白名单和确定性产物校验 |
| Tavily或模型不可用 | 任务无法完整完成 | 有限重试、失败隔离和 `partial` 报告 |
| 主供应商不可用 | 任务在首个模型节点停止 | 主供应商三次临时错误后切换已验证的备用供应商，单个逻辑调用总尝试不超过五次 |
| 备用模型能力不等价 | 结构化输出或工具调用可能失败 | 启用前做锁定版本 live 能力测试，事件与 Bundle 记录实际模型，失败时保留恢复点而不伪造结果 |
| 错误详情泄露秘密或用户输入 | 本地产物泄密 | 只持久化安全白名单字段，禁止原始响应正文、完整 Header、请求正文和 Base URL |
| 供应商逻辑侵入研究图 | 更换模型时需要修改主流程并破坏上游审计 | 供应商选择和降级只属于独立模型运行时适配层，Graph 与 Prompt 禁止出现供应商分支 |
| DeepSeek 兼容能力不完整 | 工具调用可用但结构化输出可能返回 HTTP 400 | 启用全流程 fallback 前分别验证结构化输出、工具调用、压缩和报告；能力不满足时不得宣称可兜底 |
| 供应商调价 | 系统估算与账单不同 | 保存价格配置版本，并明确预算是本地控制值 |
| 上游升级冲突 | 后续合并成本 | 固定提交、独立对照目录、Git 标签、核心差异审计和上游测试 |

已在 Windows 上用 LangGraph 1.2.9、`langgraph-checkpoint` 4.1.1 和 `langgraph-checkpoint-sqlite` 3.1.0 完成两个独立进程的 durable interrupt 恢复实验；也已验证只给外层 Graph 注入 Saver 时，可以在重新打开连接后恢复嵌入子图内部的 interrupt。

### 11.2 显式未决事项

这些事项不会改变已确认的模块边界或数据流，但必须在对应实施阶段关闭：

| 问题 | 影响面 | 当前决策 | 关闭条件 |
|------|--------|----------|----------|
| OpenCompete Bench 的具体 6 至 10 个案例 | 评测数据 | 使用跨领域案例和人工审核参考报告 | Benchmark 实施前冻结案例清单与 Criteria |
| 各模型的初始价格表 | 预算估算 | 作为带日期的可配置数据保存 | 第一次真实冒烟测试前核对供应商公开价格 |
| 默认模型组合 | 初始体验和成本 | 保留上游多模型配置，不绑定单一供应商 | CLI 冒烟测试时选择一组可用默认配置并记录理由 |

### 11.3 已否决方案

| 方案 | 否决原因 | 重评条件 |
|------|----------|----------|
| 只修改 Prompt | 不能满足结构化证据、范围冻结、恢复和预算需求 | 产品退化为一次性报告生成器 |
| 复制并重写 Supervisor/Researcher | 会失去上游修复和差异可审计性 | 上游内核无法通过适配满足已确认需求 |
| 更换 Agent 框架 | 重写成本和风险高，没有关键能力缺口证据 | LangGraph 无法维护或恢复能力验证失败 |
| Agent Server 作为正式运行层 | 用户无法使用，违反 REQ-001 | 产品明确转为远程多用户服务 |
| PostgreSQL和对象存储 | 当前规模下运维和复杂度过高 | 出现多用户、多机器或高并发需求 |
| 严格外部调用 `exactly-once` | 供应商与本地存储无法形成共同事务 | 单次重放成本达到不可接受水平且供应商支持幂等 |
| 官方 DRB 作为主竞品评测集 | 通用研究任务不能代表竞品分析覆盖 | 只作为补充回归集保留 |
| 第一版统一质量总分 | 指标权重没有经验或人工标注依据 | Benchmark 基线和人工评分稳定后 |
| 多搜索引擎和自建爬虫 | 第一版没有真实需求，扩大安全和维护面 | Tavily 无法覆盖已确认来源类型 |

## 12. 变更面预估

| 区域 | 预计改动 | 备注 |
|------|----------|------|
| `src/open_deep_research/deep_researcher.py` | 已验证的最小补丁 | 处理并行失败隔离、模型请求参数透传、兼容端点的 Tool transcript 压缩、单调用主备路由和最终报告有界输入；Graph 工厂和 durable interrupt 仍在 OpenCompete 模块 |
| `src/open_deep_research/model_routing.py` | LangChain 官方能力的薄封装 | 仅组合 `with_retry`、`with_fallbacks`、临时异常白名单和非秘密路由元数据；不持有业务状态、不执行 Graph、不实现第二套调度器 |
| `src/open_deep_research/state.py` | 尽量不改 | 上游状态通过适配转换；只有 Graph 契约要求时最小扩展 |
| `src/open_deep_research/configuration.py` | 最小参数扩展 | 只承接 OpenCompete 无法从外层注入的请求超时、SDK 重试、Tavily 结果数和最终报告输入上限；秘密仍不进入配置 |
| `src/open_deep_research/utils.py` | 最小 Provider 边界补丁 | 第一版通过 Provider 边界禁用 MCP 和其他搜索路径；网页摘要失败只记录异常类型，并透传官方 Tavily `max_results` 与模型传输参数 |
| `src/opencompete/` | 新增 | CLI、Runner、Scope、领域模型、Provider、预算、持久化和评测 |
| `tests/` | 新增和补充 | 离线单测、Graph 集成、恢复、上游特征和可选 live 测试 |
| `benchmarks/opencompete/` | 新增 | 固定案例、Criteria、参考报告元数据和数据集版本 |
| `docs/` | 新增和修改 | 中文架构规格、实现计划、运行和评测说明 |
| `pyproject.toml`、`uv.lock` | 最小修改 | CLI 入口、SQLite Checkpointer、配置和测试依赖 |
| `langgraph.json` | 可保留但不依赖 | 只作为上游调试兼容入口 |
| `src/legacy/` | 不修改 | 不在第一版变更范围 |

## 13. 审计摘要

| 重要设计 | 对应需求 | 删除后的后果 | 成熟方案及来源 | 选择与最小自研 |
|----------|----------|--------------|----------------|----------------|
| 保留上游研究内核 | REQ-003、REQ-013 | 需要重写研究编排并失去上游兼容 | Open Deep Research 固定提交 | 复用 Supervisor/Researcher，只做薄适配 |
| Application Runner 与 Graph 工厂 | REQ-001、REQ-007、REQ-013 | 正式运行继续依赖 Agent Server | 上游已导出的 Builder 与 LangGraph Checkpoint | 在 OpenCompete 模块编译并注入 Saver，不修改上游 Graph 导出 |
| Durable interrupt | REQ-002、REQ-007、REQ-013 | `goto=END` 无法可靠表达等待确认和恢复 | LangGraph Interrupts | 在外层 Scope Graph 自研确认 DTO 和 CLI 交互，上游 clarification 正式运行时关闭 |
| Scope Graph 与冻结 Scope | REQ-002、REQ-003、REQ-011 | 网页和模型可能擅自改变竞品分析范围 | LangGraph 状态机 + Pydantic | 自研竞品范围规则，这是领域特有能力 |
| Artifact Models | REQ-004、REQ-005、REQ-012 | 报告只有自由文本，无法确定性追溯和评测 | Pydantic | 自研竞品证据关系，不自研校验框架 |
| Budget Guard | REQ-005、REQ-006、REQ-009 | 并行调用可能超预算，重放无法计费 | Provider 用量数据 + 进程内锁 + SQLite | 自研 durable 预留/结算账本，暂不引入分布式中间件 |
| SQLite Checkpoint | REQ-007 | 进程退出后无法恢复 | `AsyncSqliteSaver` | 只自研 Saver 生命周期和 thread 映射 |
| JSON 兼容 Graph State | REQ-007、REQ-010 | Checkpoint 写入应用构造器元数据，产生反序列化告警并扩大本地数据库被篡改后的类型加载面 | LangGraph `JsonPlusSerializer` 的精确 allowlist 或严格模式、Pydantic `model_dump/model_validate` | 选择 JSON State；不维护随 DTO 增长的构造器 allowlist，只在既有节点边界做类型恢复 |
| Run Repository | REQ-008 | Checkpoint 被错误当作长期业务格式，报告难查看和迁移 | 标准文件系统、原子替换 | 自研 revision 与对账，当前不引入对象存储 |
| Tavily-only Provider | REQ-003、REQ-011 | 搜索面扩大，安全和测试复杂度上升 | 上游 Tavily 集成和官方 SDK | 薄适配、URL 校验和调用记录 |
| OpenCompete Bench | REQ-012 | 官方 100 题无法代表通用竞品分析质量 | Deep Research Bench/RACE 方法 | 自研 6 至 10 个竞品案例、Criteria 和硬门禁 |
| 标准日志和本地事件 | REQ-014 | 失败、重放和成本无法诊断 | Python `logging` | 只自研 JSONL Formatter 和脱敏字段 |
| 模型传输与有界输入 | REQ-005、REQ-009、REQ-013、REQ-014、REQ-019 | 兼容供应商的大请求会因非流空闲连接中断，整图重试使一次失败放大为多批重复请求 | LangChain `init_chat_model` 的流式聚合、`with_retry`/`with_fallbacks`、官方 Tavily `max_results`、标准字符串有界组合 | SDK 重试设为 0；纯文本长响应使用官方流式能力；主备重试下移到单模型 Runnable；ToolMessage 转不可信 transcript并有界保留报告输入 |

## 14. 真实供应商兼容性决策

### 14.1 确认的需求与删除后果

| 设计 | 对应需求 | 删除后的后果 |
|---|---|---|
| 单次 LLM 超时与 SDK 重试配置 | REQ-009、REQ-014；个人本地运行必须能从 Provider 故障返回 | OpenAI SDK 默认长超时和内部重试会把一次 Graph attempt 放大为多次不可见请求，事件、预算和等待时间失真 |
| Tavily 单查询结果数与网页内容上限 | REQ-003、REQ-005；完整链路必须适配用户提供的兼容模型 | 多查询乘以多网页后，压缩输入超过供应商稳定处理范围，连续返回 5xx |
| ToolMessage 纯文本压缩 transcript | REQ-003、REQ-011、REQ-013 | 原始 assistant tool-call 元数据和规划内容被再次发送，严格兼容端点可能拒绝；删除 Tool 数据本身又会丢失证据 |
| 最终报告临时错误重试与有界输入/输出提示 | REQ-005、REQ-009 | 一次 5xx 会立即退化成错误报告；长生成超过供应商网关时间后无法完成闭环 |
| 宽松候选提取、严格发布 DTO | REQ-004、REQ-005 | 模型常见的“Unknown 同时附证据”错误在清洗前抛出，既有确定性降级层无法运行 |
| 长文本流式传输与独立输出上限 | REQ-005、REQ-009、REQ-019 | 中间代理约 60 秒关闭非流连接；报告和提取器共用上限会在一端截断或迫使另一端生成过长 |
| 单模型调用级主备运行时 | REQ-009、REQ-015、REQ-018、REQ-019 | 单个并行单元失败会重放整批调用，手工恢复后同一 `call_id` 可反复获得新的五次额度 |

### 14.2 成熟能力调研与选择

- 固定版本 `openai 2.32.0` 的客户端源码确认默认请求超时为 600 秒、默认 SDK 重试为 2；采用官方客户端的 `timeout`、`max_retries` 参数，不自研 HTTP 客户端。来源：[OpenAI Python 重试与超时](https://github.com/openai/openai-python#retries)。
- 固定版本 LangChain `_ConfigurableModel` 源码验证只有列入 `configurable_fields` 的参数会进入具体模型；选择扩展 `timeout`、`max_retries` 透传，不替换 `init_chat_model`。来源：[LangChain `init_chat_model`](https://python.langchain.com/api_reference/langchain/chat_models/langchain.chat_models.base.init_chat_model.html)。
- Tavily 搜索 API 已提供 `max_results`；选择通过配置限制每查询结果数，不自研搜索结果调度器。来源：[Tavily Search API](https://docs.tavily.com/documentation/api-reference/endpoint/search)。
- LangChain 已提供 `get_buffer_string`，但真实实验进一步证明最终只需 ToolMessage 证据；选择确定性纯文本 transcript，不复制工具协议或供应商消息序列化。
- 固定版本 `langchain-core 1.4.8`、`langchain-openai 1.1.14` 和 `langchain-anthropic 1.4.7` 均提供官方流式聚合能力；模型配置 `streaming=True` 后，`ainvoke` 会消费 `_astream` 并聚合完整 `AIMessage`。选择只在纯文本长节点启用，不自研 SSE 客户端，也不在未经验证的工具调用和结构化输出中全局启用。
- LangChain Runnable 已提供 `with_retry` 和 `with_fallbacks`；主备边界已经下移到完成工具绑定、结构化输出或纯文本配置后的单个 Runnable。Application Runner 每个 invoke/resume 只执行一次 Graph，原整图 `ModelRuntime` 及其测试已经删除。

### 14.3 锁定版本实验

2026-07-16 使用用户提供的 OpenAI 兼容 Base URL、模型和 Tavily Key 运行显式 live 测试。实验依次复现并修复：压缩大请求 5xx、SDK 隐式重试审计不透明、最终报告 5xx、Windows CLI 输出编码和候选 Claim 跨字段错误。最终一次完整运行在 9 分 43 秒内通过，覆盖真实搜索、两个 Researcher、两个压缩节点、最终报告、Artifact 提取、SQLite/WAL、本地产物复载和新进程 CLI `show`；未把 URL、密钥、Header 或响应正文写入诊断输出。

以上兼容补丁是当前最小边界。若上游正式提供统一传输配置、兼容 transcript 或稳定的消息裁剪能力，应优先删除对应补丁并回归上游实现。

### 14.4 非流响应中断实验

2026-07-16 在新供应商组合上复现：本地 `final_report_generation` 请求在约 61 秒后抛出 `APIConnectionError`，异常链为 `RemoteProtocolError: peer closed connection without sending complete message body (incomplete chunked read)`；供应商后台同一请求继续运行至 2 分 55 秒并记录 4,319 / 7,999 tokens。请求开始时间相差不足一秒，证明认证、路由和模型调用已连通，失败位于完整响应返回链路。

同一事件账本还证明整图重试会成对重发并行压缩调用；手工恢复后同一最终报告 `call_id` 的累计 attempt 超过五次。因此仅增大 SDK timeout、继续非流重试或增加 SDK 内部重试均被否决。现已用官方流式能力恢复长文本闭环，并把主备运行时下移到单模型 Runnable；每个自动执行窗口主三备二，用户恢复形成新窗口，durable ledger 继续保留累计 attempt。

本设计不存在仍会改变模块边界、数据流、持久化真相源或测试策略的未决问题。实施阶段若关键版本实验否定本设计假设，必须暂停相关实现并回到本规格更新决策，不能静默替换架构。
