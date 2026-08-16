# OpenCompete Agent 执行控制优化 - 架构设计

> 状态：已实现并通过离线门禁  
> 日期：2026-07-18  
> 作者：Codex  
> 关联需求：真实竞品研究执行优化、OpenCompete Bench v2.2 闭环  
> 前置基线：O01 run `021ca3fa-c327-40e6-8615-446f104b9a66`

## 1. 背景与目标

### 1.1 问题

O01 的真实 Checkpoint 显示，单个 Researcher 实际按 `search -> think -> search -> think -> compress` 运行。`tool_call_iterations` 统计模型轮次，而研究 Prompt 把额度描述为搜索调用；最后一次反思已经识别关键证据缺口，系统却因模型轮次达到上限直接压缩，反思不能转化为下一次搜索。

O02 报告声称把 `max_researcher_iterations/max_react_tool_calls` 提高到 `3/6`，但该 run 的 Manifest 与 Checkpoint 仍为 `2/4`。因此 O02 只能视为实验身份无效的同配置复跑，不能证明增加预算有效或有害。

### 1.2 成功标准

- [x] 搜索、反思和模型轮次分别计数，`think_tool` 不消耗研究工具额度。
- [x] `search -> think -> search -> think` 后，只要仍有研究工具额度且未达到模型硬上限，Researcher 可以继续执行下一轮搜索。
- [x] 每个 Researcher 以稳定枚举记录终止原因，包括发送前全局预算拒绝。
- [x] 旧 Checkpoint 缺少新字段时仍可恢复，并从现有消息和旧计数推导安全初值。
- [x] 正式运行继续使用同一个 `deep_researcher_builder`、Runner、Checkpointer、materialize、validate 和 Run Repository。
- [x] 配置声明、RunnableConfig、Manifest 与测试预注册值一致。
- [x] 默认离线测试不调用真实模型或 Tavily。

### 1.3 非目标

- 本迭代不建立第二套 Supervisor、Researcher 或研究 Graph。
- 本迭代不实现 Coverage Ledger、Gap Verifier、`Send` 动态调度或直接 URL 提取工具。
- 本迭代不增加领域包，不写入 Benchmark case ID、产品名、价格、Reference Claim 或预设答案。
- 本迭代不运行昂贵真实模型测试；真实 Pilot 需单独执行。

## 2. 约束与上下文

| 类型 | 内容 |
|------|------|
| 技术栈 | Python、LangGraph 1.2.9、Pydantic、pytest |
| 部署形态 | 个人本地、单用户 CLI |
| 规模假设 | 单任务少量并行 Researcher，无分布式调度 |
| 安全 | 不读取、输出或持久化 `.env` 密钥；网页仍是不可信数据 |
| 上游兼容 | 保留官方 Supervisor/Researcher 职责和 `deep_researcher_builder` |
| 持久化 | 父 Graph 的 SQLite Checkpointer 继续负责嵌入子图恢复 |

## 3. 可测试需求

### REQ-A01：分离执行计数

系统 SHALL 分别维护模型轮次、已执行研究工具调用数和反思工具调用数。

#### Scenario：反思不占搜索额度

- GIVEN Researcher 已执行两次研究工具和两次 `think_tool`
- WHEN 研究工具额度为三且模型硬上限尚未达到
- THEN 下一轮仍可执行一次研究工具调用

### REQ-A02：稳定终止语义

系统 SHALL 在无工具调用、主动完成、研究工具额度耗尽、模型硬上限和发送前全局预算拒绝五种情况下记录不同的稳定终止原因。

#### Scenario：模型硬上限兜底

- GIVEN 模型持续调用反思工具且没有完成
- WHEN 模型轮次达到硬上限
- THEN Researcher 进入压缩且终止原因为 `model_turn_limit`

### REQ-A03：研究工具额度不可超发

系统 SHALL 在并行工具调用前按剩余额度截断研究工具调用，并为因 Researcher 本地额度未执行的调用返回不含供应商正文的稳定 ToolMessage。已准入且实际进入工具边界的普通 Provider 失败计为一次研究尝试；BudgetGuard 在发送前拒绝的调用不得计入，并 SHALL 从 Researcher 上推到共享 Supervisor。Supervisor 必须停止新增研究、保留同批成功结果；后续压缩与最终报告使用已有消息生成确定性部分结果，不得新增外部调用，最终由 durable 预算快照使正式 Runner 发布 `partial`。

#### Scenario：并行调用超过剩余额度

- GIVEN 当前只剩一个研究工具额度
- WHEN 模型同时请求两个搜索调用
- THEN 只执行一个，另一个标记为本地额度拒绝

#### Scenario：全局预算在发送前拒绝

- GIVEN Researcher 本地仍有研究工具额度，但全局 BudgetGuard 已无可用额度
- WHEN 新研究工具调用尝试预留预算
- THEN 外部调用不发送、Researcher 记录 `budget_exhausted` 且不增加研究调用计数、共享 Supervisor 停止新增研究并保留同批成功结果、压缩与报告节点不再调用模型，正式 Runner 按预算耗尽语义生成 `partial`

### REQ-A04：恢复兼容

系统 SHALL 恢复缺少新计数字段的旧 Researcher Checkpoint，不得因字段缺失失败或把历史调用全部清零。

#### Scenario：旧状态继续运行

- GIVEN 旧状态只有 `tool_call_iterations` 和历史消息
- WHEN 新代码恢复该状态
- THEN 模型轮次使用旧计数，研究与反思计数从历史消息推导

### REQ-A05：实验身份可审计

系统 SHALL 在创建 run 时把本次 Agent 额度写入 Manifest，并由离线测试证明其与 RunnableConfig 使用同一个 Settings 实例。

#### Scenario：配置不一致

- GIVEN 预注册参数与 Manifest 不一致
- WHEN 汇总优化结论
- THEN 该 run 标记为实验身份无效，不用于因果归因

### REQ-A06：反硬编码

系统 SHALL 只依据工具类型、通用配置和运行状态决定路由。

#### Scenario：跨案例通用

- GIVEN 任意实体、层级和维度组合
- WHEN 执行研究循环
- THEN 源码不包含案例 ID、产品名、价格或 Reference ID 分支

## 4. 方案对比

| 方案 | 思路 | 优点 | 代价 | 适用 |
|------|------|------|------|------|
| A 执行控制修复 | 分离研究工具额度、反思和模型硬上限，增加稳定终止原因 | 直接对应已验证根因，改动最小 | 不保证所有 Scope Cell 闭合 | 第一迭代 |
| B Typed Coverage Ledger | 加入 requirement/task/result 状态和完成门禁 | 可做缺口驱动调度 | 需要扩展上游输入与 Supervisor 契约 | A 的 Pilot 仍不足时 |
| C `Send` + Verifier + 定向工具 | 动态扇出、缺口验证和 URL 定向提取 | 能力最完整 | 当前规模过重，扩大恢复和幂等风险 | 明确证据证明 B 不足时 |

### 4.1 推荐

- **采用：** A，作为独立、可证伪的第一迭代。
- **条件采用：** B 只有在 A 的真实 Pilot 仍出现关键缺口未闭合时进入。
- **否决：** C 当前没有已确认需求支撑，删除它不违反本迭代任何需求。

### 4.2 调研摘要

| 候选 | 来源 | 结论 | 原因 |
|------|------|------|------|
| LangGraph StateGraph/Command/Checkpoint | 锁定版本本地源码与官方 Graph/Persistence 文档 | 采用 | 现有 Graph 已使用，无需新框架 |
| LangGraph `Send` | 锁定版本 `langgraph.types.Send` | 延后 | 当前 `asyncio.gather` 足以完成第一迭代 |
| LangGraph `RetryPolicy` | 锁定版本 `RetryPolicy` | 当前否决 | 套在批量节点会重放成功兄弟并叠加 ModelRuntime 重试 |
| Deep Agents TODO/Verify | 仓库既有调研台账 | 仅作为 B 的模式参考 | 不引入其框架，不复制第二套 Graph |
| GPT Researcher follow-up | 仓库既有调研台账 | 仅作为 B 的模式参考 | A 尚未验证前不增加递归调度 |

外网在本轮未获准独立复核上述外部项目最新源码；已验证事实来自锁定 LangGraph、本地 ODR 源码、Checkpoint 和仓库测试。

自研最小边界：三个通用计数、一个终止原因、一个研究工具额度配置和局部路由判断。重新评估触发条件是 A 的真实 Pilot 仍不能关闭关键缺口，或失败注入证明现有字符串错误无法支持恢复。

## 5. 逻辑架构

### 5.1 组件与职责

| 组件 | 职责 | 不负责 | 依赖 |
|------|------|--------|------|
| `ResearcherState` | 保存模型、研究工具、反思计数和终止原因 | 竞品领域定义 | LangGraph State |
| `researcher` | 调用同一研究模型并增加模型轮次 | 证据门禁 | 现有 ModelRuntime |
| `researcher_tools` | 执行额度内工具、更新计数并决定继续或压缩 | 生成报告 | 现有 Tool/Command |
| `Configuration`/Settings | 提供研究工具额度和模型硬上限 | 动态预算调度 | Pydantic |
| Application Runner | 生成同一 RunnableConfig 和 Manifest 快照 | 改写 Agent 路由 | 现有 Settings |

### 5.2 依赖规则

- 允许 `opencompete` 把非秘密配置传入 `open_deep_research`。
- 禁止 `open_deep_research` 导入 OpenCompete 领域模型或 Benchmark 数据。
- 禁止新增旁路 Graph、旁路 Runner 或测试专用研究实现。

### 5.3 数据流

```text
冻结 Scope
-> 正式 prepare_research_input
-> 同一 deep_researcher_builder
-> Researcher 模型轮次
-> researcher_tools 计算剩余额度
-> 继续 Researcher 或 compress_research
-> 正式 final_report_generation
-> OpenCompete materialize
-> validate
-> 原 Run Repository
```

### 5.4 关键状态与失败语义

| 状态 | 真相源 | 失败语义 |
|------|--------|----------|
| `model_turns` | Researcher Checkpoint；旧状态回退到 `tool_call_iterations` | 达上限为 `model_turn_limit` |
| `research_tool_calls` | 已实际执行的非控制类工具数 | 达上限为 `research_tool_limit` |
| `reflection_tool_calls` | 已实际执行的 `think_tool` 数 | 不单独触发额度耗尽 |
| `termination_reason` | Researcher Checkpoint | `no_tool_calls/research_complete/research_tool_limit/model_turn_limit` |
| Provider 预算 | 现有 BudgetGuard ledger | 耗尽仍由正式 Runner 发布 `partial` |

OpenAI/Anthropic 原生 Web Search 在模型响应返回前已经由供应商执行，无法使用本地工具的执行前截断；系统从响应元数据记录实际请求数，并在响应后终止后续研究调用。正式 OpenCompete 仍固定使用 Tavily，因此产品主路径具备执行前额度控制。

## 6. 接口与契约

### 6.1 对内状态草图

```python
class ResearcherState(TypedDict):
    tool_call_iterations: int  # 旧字段，继续镜像模型轮次
    model_turns: int
    research_tool_calls: int
    reflection_tool_calls: int
    termination_reason: ResearchTerminationReason | None
```

配置新增 `max_research_tool_calls`；现有 `max_react_tool_calls` 保持模型工具循环硬上限的兼容语义。

### 6.2 ID、幂等与重试

- 本迭代不新增业务 ID。
- 超额工具调用在执行前拒绝，因此不产生 Provider 调用或预算预留。
- ModelRuntime 的单调用重试与主备降级保持不变。
- 成功并行 Researcher 不因失败兄弟重放。

## 7. 横切关注点

| 主题 | 决策 |
|------|------|
| 配置与密钥 | 新字段为非秘密整数；密钥仍只在环境/进程内 |
| 观测 | 终止原因进入 Checkpoint，不记录原始异常或网页正文 |
| 错误与降级 | 本地额度拒绝使用稳定文本；Provider 失败语义本迭代不扩张 |
| 安全 | 工具结果仍按不可信数据处理 |
| 性能预算 | 不因反思自动增加 Provider 预算；研究工具调用仍受 BudgetGuard 双重约束 |

## 8. 测试与完成定义

### 8.1 测试策略

- 单元：脚本消息驱动路由、并行额度截断、终止原因和旧状态回退。
- 集成：Settings -> RunnableConfig -> Manifest 一致性；BudgetExceeded 仍为 `partial`。
- 回归：失败 Researcher 不重放成功兄弟；Graph composition、Prompt injection、持久化和全量离线套件。
- 真实 Pilot：不在默认测试运行；后续使用固定 Scope 正式入口单独执行。

### 8.2 完成门禁

- [ ] `uv run pytest -q tests/opencompete/test_upstream_agent_control.py`
- [ ] `uv run pytest -q tests/opencompete/test_upstream_failure_isolation.py tests/opencompete/test_application.py`
- [ ] `uv run ruff check .`
- [ ] `uv run mypy src/opencompete`
- [ ] `uv run pytest -q`
- [ ] `git diff --check`
- [ ] `git diff upstream-b764481f -- src/open_deep_research` 已单独审计
- [ ] 源码扫描无 Benchmark case ID、产品名、价格和 Reference ID

## 9. 风险、未决与否决记录

### 9.1 风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| 配置关系不合理 | 模型硬上限仍可能先于研究工具额度触发 | OpenCompete Settings 校验硬上限至少覆盖搜索/反思交替 |
| 并行一次请求多个研究工具 | 单轮可能超额 | 执行前按剩余额度截断 |
| 旧 Checkpoint 无新字段 | 恢复时计数不完整 | 从旧计数和历史消息推导 |
| A 只修路由不保证覆盖 | Bench 指标可能仍不足 | 以真实 Pilot 决定是否进入 B |

### 9.2 未决问题

| 问题 | 影响面 | 默认假设 | 关闭条件 |
|------|--------|----------|----------|
| A 是否足以把 critical coverage 提升到 80% | 是否进入 B | 不预判 | 固定 Scope 正式 Pilot |
| 定向 URL 提取是否必要 | 工具面 | 当前不新增 | 多案例持续出现指定来源无法命中 |

### 9.3 已否决方案

| 方案 | 否决原因 | 重评条件 |
|------|----------|----------|
| 新 Planner/Verifier/Writer Agent | 第二套 Graph 风险和当前规模不匹配 | B 经多案例验证仍不足 |
| 报告后 LLM 自我改写 | 不能补回缺失证据，增加幻觉和成本 | 无 |
| 长期向量记忆/跨任务共享 | 当前无多用户或跨任务需求 | 出现明确复用需求 |
| 针对案例 006 的搜索模板 | 评测过拟合和正式产品污染 | 永不采用 |

## 10. 变更面预估

| 区域 | 预计改动 | 备注 |
|------|----------|------|
| `src/open_deep_research/state.py` | 修改 | 通用状态与终止类型 |
| `src/open_deep_research/deep_researcher.py` | 修改 | 同一 Researcher 路由与额度执行 |
| `src/open_deep_research/configuration.py` | 修改 | 通用研究工具额度 |
| `src/opencompete/settings.py` | 修改 | 正式配置传递与关系校验 |
| `opencompete*.toml` | 修改 | 中文配置说明与显式值 |
| `tests/opencompete/` | 新增/修改 | 离线特征和集成测试 |
| materialize/validate/persistence | 不修改 | 保持正式主路径 |

## 11. 审计摘要

1. **确认的需求：** REQ-A01/A02；删除分离计数和终止原因会继续让最后一次反思无法行动。  
   **成熟能力：** LangGraph State/Command；**选择：** 在现有节点内最小扩展；**自研：** 三个计数和路由判断。
2. **确认的需求：** REQ-A03；删除执行前截断会让并行调用突破本地额度。  
   **成熟能力：** 现有 ToolMessage/Command；**选择：** 标准列表切片，不引入调度器。
3. **确认的需求：** REQ-A04；删除兼容逻辑会破坏正在恢复的旧任务。  
   **成熟能力：** 父 Graph Checkpointer 已继承到子图；**选择：** 缺字段时从持久消息推导。
4. **确认的需求：** REQ-A05；删除配置核对会重复 O02 的不可审计归因。  
   **成熟能力：** 现有 Settings 和 Manifest；**选择：** 复用同一对象并加测试，不新建实验数据库。
5. **适配层为何不足：** 外层 Graph 无法拦截已编译 `supervisor_subgraph` 内部的 Researcher 路由和终止判断；只改输入 Prompt 不能修复计数或继续边。因此本迭代必须对同一上游内核做上述最小通用修改。
