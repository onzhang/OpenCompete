# OpenCompete Web 动态研究范围与安全恢复 - 架构设计

> 状态：已确认实施  
> 日期：2026-08-15  
> 作者：项目所有者、Codex  
> 关联需求：Web 研究委托动态范围、确认后状态一致性、显式恢复  
> 前置基线：`upstream-b764481f`

## 1. 背景与目标

### 1.1 问题

Web 控制台目前将固定的三层、十五项分析维度预填到新建页；OpenCode 最近的修改只是把旧英文 slug 替换成固定中文目录。它既不能因任务而变化，也会在用户删除后由后端重新补回。研究简报又没有携带最终确认的层级、维度和排除项，导致上游研究与后处理覆盖审计使用两份不同的范围。

另一个独立缺陷发生在范围确认后：LangGraph Checkpoint 已推进到 `scope_frozen` 或 `researching`，但 Manifest 直到整次研究结束才更新。FastAPI 与 Vue 因而继续展示旧的 `awaiting_confirmation`，重复提交变成含糊的 429；OpenCode 为切断 GET 自动恢复而完全断开安全的 Checkpoint 只读投影，服务重启后确认载荷又无法恢复。

本变更只修复 Web 研究委托路径的动态范围和安全恢复闭环。它不重写上游 Supervisor/Researcher，不迁移历史 Run，也不以固定中文目录替代固定英文目录。

### 1.2 成功标准

- [ ] Web 创建页只提交用户委托；每次运行由独立 Planner 生成任务相关、可编辑的中文 Scope 草案。
- [ ] 确认页中用户最终保留、删除或新增的维度原样冻结；系统绝不补回固定维度。
- [ ] 冻结对象、层级、动态维度、排除项和指定网址以自然语言进入上游研究 brief；内部审计契约仍不泄漏给用户消息。
- [ ] `GET`、列表、SSE 订阅和服务启动都不会执行 `ainvoke()`、恢复研究或消耗预算；中断研究只能由 Web 的显式恢复动作继续。
- [ ] 范围接受返回 202 前，Checkpoint 与 Manifest 至少都已反映 `scope_frozen`；页面立即切至直播态，不会被旧 Manifest 覆盖为确认页。
- [ ] 同一个运行正在执行返回明确 409；另一个运行占用单进程容量返回明确 429。
- [ ] 离线 FastAPI 假运行时端到端测试、相关 Python 测试、前端构建均通过；不调用真实模型或 Tavily。

### 1.3 非目标

- 不为历史 Bundle、已有 Checkpoint 或 Benchmark 迁移维度文本。
- 不移除 CLI、`create_fixed_scope_run()` 或固定 Benchmark Scope 路径。
- 不修改 `src/open_deep_research/` 的 Supervisor、Researcher、Prompt 或模型 SDK 路由。
- 不引入 Redis、消息队列、第二套 Graph、分布式锁、后台任务框架或新数据库。
- 不把候选发现、Scope 规划塞进上游 Supervisor 或 `DiscoveryProvider`。
- 不承诺本变更会真实提高任一模型的研究质量；本变更只保证范围契约与运行状态正确贯通。

## 2. 已确认约束与审查结论

| 主题 | 已确认事实与约束 |
|------|------------------|
| 稳定枚举 | 只有 `AnalysisLevel` 的 `product_service`、`company_organization`、`industry_market` 是稳定枚举。 |
| 动态维度 | `ScopeSpec.dimensions`、`Claim.dimension`、`Comparison.dimension` 是单次 Run 内按字符串相等性关联的自由字符串；新 Web 路径必须使用任务相关的中文自然语言。 |
| 用户编辑 | 用户可在确认页添加、删除、修改层级/维度/排除项；显式空值必须被校验拒绝，不能被静默回填。 |
| 运行真相 | 运行中以 LangGraph Checkpoint 为执行真相；Manifest 是历史索引；`ResearchTaskRegistry` 是当前进程占用；Vue Pinia 是页面投影。 |
| 读取边界 | `GET`、启动与 SSE 只做检查和投影，不得调用 `resume_run(..., None)` 或 `graph.ainvoke()`。 |
| 恢复边界 | 仅显式 `POST /api/runs/{run_id}/resume` 可以继续非确认点的中断研究；确认点必须先提交对应确认操作。 |
| 兼容边界 | 历史 Run、旧 slug 的保底渲染、CLI 既有 `ResearchRequest`、固定 Benchmark Scope 均保持兼容。 |

### 2.1 对最近 OpenCode 修改的复审

| 改动 | 结论 | 原因 |
|------|------|------|
| 将固定英文维度换成固定中文 `DEFAULT_LEVEL_DIMENSIONS` | 不采用 | 只是表层中文化，仍让无关任务得到相同十五项维度，仍会覆盖用户删除。 |
| `GET /runs/{id}` 不再调用 `resume_run()` | 保留其正确部分 | 切断了刷新页面静默续跑和花费预算的风险。 |
| `GET /runs/{id}` 也不再调用安全 `project_confirmation()` | 修正 | 服务重启后确认缓存为空，候选/范围确认载荷丢失，错误确认还可能把 Run 写成 failed。 |
| 未新增显式恢复、未同步中间 Manifest、未区分 409/429 | 补齐 | 原始“确认页不切换、按钮一直提交、关掉其他任务仍报错”链路仍存在。 |

## 3. 可测试需求

### REQ-001：动态中文范围草案

Web 系统 SHALL 根据每次用户委托生成包含任务相关中文维度的 Scope 草案，而不是使用任何固定维度目录、slug 翻译表或 `id/label` 双轨模型。

#### Scenario：两个不同问题

- GIVEN 用户分别委托“调研 Trae 的 AI IDE 竞品”和“调研连锁咖啡品牌选址竞争”
- WHEN ScopePlanner 生成草案
- THEN 两份维度来自各自规划输出，且不会因为缺省输入套入同一固定矩阵

### REQ-002：确认内容原样冻结

系统 SHALL 只冻结用户最终提交的 Scope；显式删除维度或层级后不得回填默认项。

#### Scenario：删除全部维度

- GIVEN 用户删除某个仍被选中层级下的全部维度
- WHEN 提交范围确认
- THEN 系统返回明确校验错误，保持确认点可编辑，不重新填入任何默认维度

### REQ-003：冻结范围约束研究

系统 SHALL 将最终对象、中文层级、动态维度、排除项与指定网址以自然语言传入上游研究 brief，并将相同 `ScopeSpec` 用于 Extractor、Coverage、Gate。

#### Scenario：用户新增关注维度与排除项

- GIVEN 用户在确认页加入“代码库上下文连续性”并排除“未公开路线图”
- WHEN 冻结后进入研究
- THEN 上游消息包含这两项用户可读约束，`ResearchContract.exclusions` 也等于冻结 Scope 的排除项

### REQ-004：候选选择不重新规划范围

系统 SHALL 在候选确认后只更新对象集合，不改变已规划的层级、维度、排除项或 Scope revision。

#### Scenario：加入 Cursor

- GIVEN Planner 已为 Trae 生成范围草案
- WHEN 用户在候选确认中加入 Cursor
- THEN 草案的动态维度与排除项字节等价地保留，仅对象集合增加 Cursor

### REQ-005：安全只读检查

系统 SHALL 通过 LangGraph `aget_state()` 投影 Checkpoint 状态；任何读取请求不得恢复或推进图。

#### Scenario：服务重启后的候选确认

- GIVEN API 进程重启且内存确认缓存为空
- WHEN 浏览器 `GET /api/runs/{id}`
- THEN 响应恢复候选/范围确认载荷，且 `ainvoke()`、`resume_run()`、Manifest、事件账本与预算账本均未被读取动作修改

### REQ-006：确认接受的耐久交接

系统 SHALL 在后台研究启动前耐久地冻结 Scope 并同步 Manifest，随后再将执行注册为后台任务。

#### Scenario：用户接受范围

- GIVEN Run 位于范围确认 interrupt
- WHEN 用户提交 `accepted=true`
- THEN 202 响应前 Checkpoint 与 Manifest 至少为 `scope_frozen`，响应携带可展示的执行投影，Vue 不会回到确认页

### REQ-007：显式恢复与容量错误

系统 SHALL 只允许用户显式恢复非确认点的中断任务，并区分同 Run 已执行和其他 Run 占用容量。

#### Scenario：两个不同冲突

- GIVEN run A 正在执行
- WHEN 再次恢复 run A
- THEN 返回 409 `run_already_active`
- WHEN 尝试启动 run B
- THEN 返回 429 `research_capacity_exhausted`

### REQ-008：前端不会无限锁定操作

系统 SHALL 为可变操作设置有限请求超时；超时后释放操作状态并只做只读对账，绝不自动恢复研究。

#### Scenario：旧服务锁等待

- GIVEN 某个旧实例使操作请求长时间未响应
- WHEN 前端请求超时
- THEN “正在提交”解除，界面显示可重试/已开始/已中断状态，且没有额外的自动恢复调用

## 4. 方案对比与选择

| 方案 | 思路 | 优点 | 代价 | 结论 |
|------|------|------|------|------|
| A：显示层翻译与 Manifest 补丁 | 固定 slug/中文标签、前端换文案、确认后提前写 `researching` | 改动少 | 不能生成任务相关范围，会制造虚假执行状态 | 否决 |
| B：独立 Planner + Checkpoint 只读投影 + 耐久冻结交接 | Web 委托先规划，确认后 `aupdate_state`，再显式后台恢复 | 职责清晰，复用现有 Graph/SQLite，最小地修复真相源分叉 | 新增薄 DTO、Planner 和少量 API 状态投影 | 采用 |
| C：重写为事件驱动状态服务/队列 | 每个节点实时镜像到 Manifest，引入队列和任务持久化 | 可覆盖多进程和更大规模 | 超出单用户本地需求，新增故障语义与维护成本 | 否决 |

### 4.1 调研与成熟能力

| 候选 | 来源 | 选择 | 原因 |
|------|------|------|------|
| LangGraph 持久化 `aget_state()` / `aupdate_state()` | [LangGraph Persistence 官方文档](https://docs.langchain.com/oss/python/langgraph/persistence)；当前项目 `create_fixed_scope_run()` | 采用 | 框架已提供耐久状态读取、状态更新和继续执行能力；无须自建状态库。 |
| LangGraph durable `interrupt()` 与 `Command(resume=...)` | [LangGraph Interrupts 官方文档](https://docs.langchain.com/oss/python/langgraph/interrupts) | 采用 | 确认点已使用此能力；检查与执行需通过不同入口分离。 |
| 现有 LangChain `init_chat_model`、`with_structured_output`、`build_resilient_model` | `providers.py`、`extractors.py` | 采用 | 已处理统一模型初始化、重试、回调和预算事件，Planner 只需薄适配。 |
| Redis/队列/分布式工作流 | 未满足当前单用户单进程需求 | 不采用 | 仅为没有确认的未来规模增加部署、恢复与一致性复杂度。 |

最小自研边界是：用户委托与已规划请求的 DTO 分界、Planner 的提示词与结构化输出校验、Checkpoint 到 HTTP 的只读投影、Registry 的短生命周期预留。重新评估引入队列的触发条件是明确要求多进程、远程部署或跨用户任务并发。

## 5. 逻辑架构与数据流

### 5.1 组件职责

| 组件 | 职责 | 不负责 |
|------|------|--------|
| `ResearchCommission` | Web 原始委托：问题、已知对象、关注点、排除项、网址、候选发现开关 | 层级、维度、冻结范围 |
| `ScopePlanner` | 将委托规划成任务相关的中文 Scope proposal | 发现候选、修改确认结果、执行研究 |
| Scope Graph | 持久化草案、候选确认、范围确认、冻结与上游输入准备 | 上游研究编排 |
| `DiscoveryProvider` | 为候选对象提供来源佐证 | 增加层级、维度或 Scope |
| Application Runner | Graph/Checkpoint/预算/Manifest 生命周期；检查、冻结、继续执行的显式方法 | HTTP、Vue 状态 |
| FastAPI | 将 Run 检查、确认、显式恢复映射为薄 HTTP 接口 | 根据 GET 推测是否应继续执行 |
| Registry | 当前进程执行槽、同 Run 去重、短暂预留 | 耐久状态真相 |
| Vue Store | 202 的本地执行投影、SSE、超时后的只读对账 | 自动恢复或判定执行真相 |

### 5.2 Web 主数据流

```text
用户委托（无默认层级/维度）
  → POST /api/runs
  → ResearchCommission
  → ScopePlanner（结构化中文 proposal）
  → Scope Graph: ScopeSpec 草案 + 可选候选发现
  → durable interrupt
  → 用户编辑并确认
  → freeze_scope（最终文本原样冻结）
  → prepare_research_input（自然语言 brief）
  → 上游 Supervisor / Researcher
  → Extractor / Coverage / Gate（同一 ScopeSpec）
  → Bundle / Manifest / events.jsonl
```

### 5.3 运行检查与恢复数据流

```text
GET /runs/{id}
  → Manifest（历史索引）
  → ApplicationRunner.inspect_run()
  → graph.aget_state()（Checkpoint 只读真相）
  → 本次 HTTP 响应投影
  └─ 不写 Manifest，不调用 ainvoke，不调用 resume

POST scope-confirmation accepted
  → Registry.reserve(run_id)
  → freeze_confirmed_scope()
  → graph.aupdate_state(as_node="freeze_scope")
  → Manifest = scope_frozen
  → Registry.start_reserved(resume_execution())
  → 202 + execution projection

POST /runs/{id}/resume
  → inspect_run() 验证非终态、非确认 interrupt
  → Registry.submit(... resume_execution())
  → 202 + execution projection
```

### 5.4 真相源与失败语义

| 数据 | 真相源 | 读取行为 | 写入/失败语义 |
|------|--------|----------|---------------|
| 运行中的范围、下一节点、确认 interrupt | LangGraph Checkpoint | `inspect_run()` 只读 | 只有确认/恢复显式动作可推进 |
| 终态 Bundle、报告、历史索引 | Run Repository/Manifest | 历史读取 | 终态原子发布；中间态是可重建索引 |
| 当前进程占槽 | `ResearchTaskRegistry` | 仅进程内提示 | 进程退出后消失，不可替代 Checkpoint |
| 页面状态 | Pinia | 可丢失 | 由 202 与只读详情/SSE 重建 |

关键耐久顺序固定为：**Checkpoint `aupdate_state` 成功 → Manifest 更新为 `scope_frozen` → 注册后台执行 → 返回 202**。进程若在后台执行前退出，留下的是可显式恢复的正确 `scope_frozen`，而不是伪造的 `researching`。

## 6. 接口与契约

### 6.1 新增/调整领域接口

```python
class ResearchCommission(FrozenModel):
    query: str
    entities: tuple[ResearchEntity, ...]
    focus_points: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    allowed_urls: tuple[str, ...] = ()
    discover_candidates: bool = False

class ScopeProposal(FrozenModel):
    intent_summary: str
    levels: tuple[AnalysisLevel, ...]
    dimensions: Mapping[AnalysisLevel, tuple[str, ...]]

class ScopePlanner(Protocol):
    async def plan(
        self, commission: ResearchCommission, *, config: RunnableConfig | None = None
    ) -> ScopeProposal: ...
```

`ScopeProposal.dimensions` 直接保存中文自然语言，不新增 `dimension_id`、`label`、全局翻译表或固定维度目录。校验仅检查形态：非空、每个已选层级至少一项、至少包含一个汉字、没有重复项；不检查是否命中预定义词表。

`ResearchRequest` 继续表示已规划/固定输入，供 CLI、Benchmark 与既有固定 Scope 路径使用。Web 创建改为 `ApplicationRunner.create_commission_run(commission)`，其结果仍进入既有 `ResearchRequest + ScopeSpec` 内部契约，避免削弱已有 `ResearchRequest` 的非空层级约束。

### 6.2 HTTP 接口

| 接口 | 输入 | 输出与语义 |
|------|------|------------|
| `POST /api/runs` | `query`、`entities`、`focus_points`、`exclusions`、`allowed_urls`、`discover_candidates` | 进入候选或范围确认；不接受 `levels`、`dimensions`。 |
| `GET /api/runs/{id}` | 无副作用 | Manifest 与 Checkpoint 的只读执行投影；确认载荷来自 Checkpoint。 |
| `POST /api/runs/{id}/scope-confirmation` | 已编辑 Scope | 拒绝时同步取消；接受时耐久冻结、启动后台执行并返回 202 完整详情投影。 |
| `POST /api/runs/{id}/resume` | 无需 DTO | 仅非终态、无用户确认 interrupt、当前未执行时可恢复；返回 202 投影。 |

错误契约：同一 Run 已占用为 `409/run_already_active`；其他 Run 占据容量为 `429/research_capacity_exhausted`；确认类型或 revision 不匹配为 `409`，且不得把 Run 落成 failed。

### 6.3 前端交互契约

- 创建页只收集事实性委托；关注点是强提示但仍由用户在下一页最终编辑。
- 范围接受后 Store 以 202 返回的完整投影一次性更新 `detailByRun` 与列表行，再连接 SSE；不立即用旧 GET 覆盖。
- 操作请求有有限超时。超时后释放 `actionBusy`，只调用 `GET` 对账，绝不调用 resume。
- `stale && recoverable` 显示“恢复研究”；确认 interrupt 不显示恢复按钮；活动 Run 的取消/重复确认立即显示 409 信息。

## 7. 变更面与实施顺序

### P0：范围与简报契约

1. 新增 `scope_planning.py`，复用现有模型路由实现 `LazyScopePlanner`。
2. 在 `models.py` 增加委托/提案 DTO；在 `application.py` 增加 Web 委托创建入口；在 `graph.py` 将 Planner 放在候选发现之前。
3. Web API、Store、新建页移除预置 `levels`/`dimensions`，加入可选 `focus_points`。
4. 候选确认仅更新 entities；`freeze_scope()` 使用 `is None` 区分“未编辑”和“显式空”；不再回填默认维度。
5. 将冻结 Scope 的用户可读摘要写入 brief；`ResearchContract.exclusions` 改用 `scope.exclusions`。

### P1：检查、冻结和显式恢复

1. 将 `project_confirmation()` 扩展为纯 `inspect_run()`；保留兼容别名但不允许任何读取路径调用 `resume_run`。
2. `GET` 用检查结果构造临时响应投影，不将其写回 Manifest 或确认缓存。
3. 新增 `freeze_confirmed_scope()`，使用已有 `aupdate_state(..., as_node="freeze_scope")` 先持久化再启动后台研究。
4. 扩展 Registry 的 reservation/start handshake，新增同 Run 活动错误；添加 `/resume`。

### P2：前端运行状态与测试

1. API 客户端增加可取消超时；Store 消费 202 执行投影并同步详情/列表。
2. 详情页显示显式恢复、明晰的冲突提示与超时后对账结果。
3. 新增 API fake-runtime 端到端测试、Planner/Scope/Brief 回归测试；保留历史兼容测试并将其与 Web 动态路径分离。

## 8. 测试与完成定义

| 层级 | 核心验证 |
|------|----------|
| 领域/Planner | 不同委托得到不同 fake proposal；无中文/空维度失败；不回退固定目录；确认删除不回填。 |
| Scope Graph | 候选确认只改变 entities；冻结 Scope 进入 brief；旧固定 `ResearchRequest` 路径仍可运行。 |
| Runner/API | GET 无 `ainvoke`/无账本写入；重启后确认载荷可读；202 前 `scope_frozen` 已耐久；显式恢复恰好一次。 |
| 并发/错误 | 同 Run 409、他 Run 429、活动 Run 的取消不等待研究锁。 |
| 前端 | `npm run build`；动作超时释放 busy；202 先切 live，再接 SSE。 |
| 端到端 | FastAPI `TestClient` + fake Graph/临时 SQLite：创建 → 确认 → 202 → 重启只读详情 → 显式恢复 → 终态。 |

完成门禁：

- [ ] 本次触及的 Python 路径通过 Ruff。
- [ ] 新旧相关 pytest 均通过；默认测试无网络、无模型、无 Tavily。
- [ ] `npm run build` 通过。
- [ ] 执行 `git diff --check`，并单列现有 `src/open_deep_research/` 无关差异。
- [ ] 不声称运行过真实付费研究，除非单独取得用户授权。

## 9. 风险、否决与审计摘要

| 设计 | 对应需求 | 删除后果 | 选择与最小自研 |
|------|----------|----------|----------------|
| 独立 ScopePlanner | REQ-001 至 REQ-004 | Web 仍只能固定维度或污染 Provider/Supervisor 职责 | 复用 LangChain 结构化输出；仅自研 prompt 与 DTO。 |
| Checkpoint 只读检查 | REQ-005 | 刷新后要么丢确认载荷，要么错误地调用执行恢复 | 复用 LangGraph `aget_state`；仅构造 HTTP 投影。 |
| `aupdate_state` 耐久冻结 | REQ-006 | Manifest 长时间滞后，确认页和执行状态分叉 | 复用项目已有固定 Scope 先例；仅增加小型 runner 方法。 |
| Registry 预留 | REQ-006、REQ-007 | 容量竞争可能在冻结后才报错，用户看到无解释的中间态 | 仅维护进程内集合；不引入队列。 |
| 前端超时与只读对账 | REQ-008 | 用户无法脱离“正在提交”，或前端误触自动恢复 | 原生 `AbortController`；无新依赖。 |

已否决：固定中文目录、全局 `dimension_label()`、`id/label` 双轨、Provider/Supervisor 承担规划、GET 自动恢复、伪造 `researching` Manifest、Redis/队列/第二 Graph。

未决项：无影响本次数据流的开放架构决策。若将来要求 Web 无对象也可提交委托，应单独设计“对象发现前确认”交互；本次维持至少一个已知研究对象，以不扩大现有产品边界。
