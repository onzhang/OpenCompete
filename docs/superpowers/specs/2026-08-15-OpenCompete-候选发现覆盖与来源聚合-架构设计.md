# OpenCompete 候选发现覆盖与来源聚合 - 架构设计

> 状态：已确认实施  
> 日期：2026-08-15  
> 作者：项目所有者、Codex  
> 关联需求：Web 竞品候选发现不能因少量搜索网页而只展示少量竞品  
> 前置基线：`upstream-b764481f`

## 1. 背景与目标

### 1.1 问题

用户以“调研 Trae AI IDE”为例，旧候选确认页仅显示两个候选，即使已返回的公开集合页标题和正文明确列举了大量替代产品。根因不是市场上只有两个竞品，而是候选发现把 Tavily 单次 Search 的网页页大小、网页正文截断和模型请求体大小混为同一个“数量限制”。

候选发现是范围冻结前的辅助决策。它必须由模型理解用户语义、由公开来源佐证产品名，并把全部可核验的候选交给用户选择；它不能依赖固定竞品清单、固定候选数、固定查询数、固定行业分类或固定研究维度。

### 1.2 成功标准

- [x] 单次 Tavily Search 返回的网页页大小不再决定候选产品数量；一个集合页可产生多个候选。
- [x] 模型可在已执行检索发现覆盖缺口后追加新的、去重后的检索词，而非只能执行初始检索计划。
- [x] 结束条件仅为已核验候选的竞争空间覆盖饱和、预算不足、检索前沿耗尽或不可恢复的 Provider 失败；不存在“凑够 N 个候选”或“搜索 N 次后停止”的业务规则。
- [x] 单次供应商请求页大小、单次模型输入字符数、并发量和预算仍作为传输/成本/安全边界存在，并与候选数量语义严格分离。
- [x] 真实模型只运行到候选确认点即可验证：Trae 示例给出 26 个候选、12 个来源域名，未进入 Supervisor/Researcher。

### 1.3 非目标

- 不自建爬虫、网页分页器、搜索索引、向量库或第二搜索引擎。
- 不承诺一次候选发现穷尽互联网中的所有产品，也不把候选集合当作最终研究结论。
- 不新增固定竞品目录、固定分类枚举、固定研究维度或“推荐必选竞品”。
- 不修改 `src/open_deep_research/` 的 Supervisor、Researcher、Prompt 或 Provider 实现。
- 不改变已确认后研究阶段的 Tavily 工具预算与上游 Researcher 行为。

## 2. 约束与已确认事实

| 主题 | 结论 |
|------|------|
| 发现位置 | 候选发现位于 Scope freeze 前；不迁入上游 Supervisor。 |
| 语义理解 | 由轻量结构化 LLM 完成；关键词搜索引擎不能代替意图识别。 |
| 证据契约 | 每个 `DiscoveryCandidate.source_url` 必须逐字来自本轮 Tavily 结果；无来源的模型记忆候选不能显示。 |
| 用户控制 | 所有候选默认未勾选，只有用户明确选中后才进入冻结范围。 |
| 稳定枚举 | 只有 `AnalysisLevel` 是稳定枚举；`category` 是当轮模型生成的可选文本，不能成为固定分类目录。 |
| 停止边界 | 预算、超时、来源安全和供应商 API 参数是保留的技术/安全边界；候选数和查询轮数不是业务上限。 |
| 运行真相 | 发现期间由 Checkpoint 与预算账本记录；读取页面不触发恢复或额外搜索。 |

当前安装的 `tavily-python 0.7.5` 中，`AsyncTavilyClient.search()` 提供 `max_results` 参数；这是**单次供应商请求的网页页大小**，不是候选产品数。当前 SDK 签名没有可复用的 offset 分页参数，因此不以自研分页伪造“无限网页”；对于更大的竞争空间，系统由模型产生不同检索意图并对来源聚合、去重。

## 3. 可测试需求

### REQ-001：候选数量与网页页大小解耦

系统 SHALL 将 `max_results` 仅作为 Tavily 单次 HTTP 请求参数，绝不按它截断 `DiscoveryCandidate` 列表。

#### Scenario：单页集合来源

- GIVEN 一次 Search 只返回一篇包含四个真实产品的集合页
- WHEN 来源绑定完成
- THEN 候选列表包含四个经 URL 核验的产品，而不是一个候选

### REQ-002：候选发现可扩展检索前沿

系统 SHALL 允许覆盖审计在尚未覆盖竞争空间时追加独立检索词；追加词经过规范化去重后才可执行。

#### Scenario：初始计划遗漏替代路径

- GIVEN 初始模型计划只覆盖 AI IDE 直接竞品
- WHEN 覆盖审计识别到“终端编码代理”仍未覆盖并给出新的检索词
- THEN 系统搜索该词、聚合新来源并再次审计，不受预设查询条数限制

#### Scenario：初始检索没有公开来源

- GIVEN 初始检索词未返回任何新公开来源，或只返回本轮已聚合过的重复 URL
- WHEN 覆盖审计识别到该词不足以验证当前竞争空间并给出替代检索词
- THEN 系统跳过空来源的绑定步骤，仍执行覆盖审计、追加去重后的替代词并继续检索
- AND 只有没有可执行的后续词、预算不足或 Provider 不可恢复失败时才结束发现

### REQ-003：覆盖饱和是质量判断而非数量判断

系统 SHALL 仅在已有至少一个可核验候选且覆盖审计判定直接竞争关系与明显替代路径已充分覆盖时停止扩展检索。

#### Scenario：一个候选但覆盖不足

- GIVEN 当前只存在一个经核验候选，且仍有未覆盖的产品形态
- WHEN 覆盖审计返回 `saturated=false`
- THEN 系统继续已有或追加的检索词，而不是因候选数量或网页数停止

### REQ-004：保留安全、预算与传输边界

系统 SHALL 在新外部调用前预留预算、仅接受公开 HTTP/HTTPS URL、保留所有来源正文分片，并在预算耗尽时返回此前已核验的候选。

#### Scenario：后续查询预算不足

- GIVEN 首轮已获得候选且预算不允许下一次 Tavily Search
- WHEN 系统尝试扩展检索
- THEN 不发送下一次请求、保留首轮候选并进入用户确认

### REQ-005：发现页大小不牵连深度研究

系统 SHALL 为候选发现使用独立的 Tavily 网页页大小配置；改变它不得改变上游 Researcher 的 `tavily_max_results_per_query`。

#### Scenario：提高候选发现页大小

- GIVEN `discovery_search_results_per_query=8` 且上游 `tavily_max_results_per_query=5`
- WHEN 创建 Web 发现任务
- THEN `DiscoveryRequest` 使用 8，而上游 RunnableConfig 仍传入 5

## 4. 方案对比与选择

| 方案 | 思路 | 优点 | 代价 | 结论 |
|------|------|------|------|------|
| A：仅把 `max_results` 从 2/5 提高到 20 | 增加一轮网页页大小 | 改动最小 | 仍可能遗漏其他竞争空间，且继续混淆共享研究配置 | 否决 |
| B：动态检索前沿 + 来源完整聚合 + 覆盖饱和 | LLM 规划初始词、Tavily 逐词检索、分批绑定、覆盖审计可追加词 | 满足动态市场与用户意图，仍复用现有 Provider、预算与官方客户端 | 多一次轻量审计调用，需严格去重 | 采用 |
| C：自建爬虫、网页分页或无限递归检索 | 自行控制全部网页获取 | 理论覆盖广 | 超出本地单用户范围，增加安全、合规、预算与恢复复杂度 | 否决 |

### 4.1 调研与成熟能力

| 候选 | 来源 | 结论 | 原因 |
|------|------|------|------|
| Tavily 官方 Python SDK `AsyncTavilyClient.search` | 已安装 `tavily-python 0.7.5` 的运行时签名；[Tavily Search 文档](https://docs.tavily.com/documentation/api-reference/endpoint/search) | 采用 | 已有正式依赖、官方异步客户端、`include_raw_content="markdown"` 与 `max_results` 参数。 |
| LangChain `with_structured_output` | 项目既有 `LlmScopePlanner`、`ArtifactExtractor`；[LangChain Structured output 文档](https://python.langchain.com/docs/how_to/structured_output/) | 采用 | 已处理模型路由、重试和回调，不新增 SDK 分支。 |
| 搜索 Agent / 自建递归爬虫 | 未满足当前单用户、Tavily-only 的已确认边界 | 否决 | 会重写上游/引入不必要的网页执行与治理复杂度。 |

最小自研仅包括：检索前沿的队列、查询文本去重、覆盖审计 DTO 和将其接入现有 `LlmDiscoveryProvider`。一旦产品明确要求多搜索引擎、网页分页、定时监控或跨任务知识共享，再单独重新评估检索工作流或专用搜索服务。

## 5. 逻辑架构与数据流

### 5.1 组件职责

| 组件 | 职责 | 不负责 |
|------|------|--------|
| `LlmScopePlanner` | 规划本次中文动态研究维度和层级 | 生成竞品候选或改写用户选择 |
| `LlmDiscoveryProvider` | 理解候选发现意图、维护检索前沿、调用 Tavily、绑定来源、判断覆盖 | 冻结 Scope、自动把候选加入范围、改变研究维度 |
| Tavily 官方客户端 | 从公开网页返回搜索来源 | 判断产品是否为竞品 |
| `_harden_candidates()` | 核验 URL、去除已有对象和重复产品 | 根据数量截断或编造产品 |
| 候选确认 UI | 展示动态类别及来源，收集用户选择 | 自动选择任何候选 |

### 5.2 主数据流

```text
用户委托 + 已知对象
  → ScopePlanner（独立的动态研究范围）
  → LlmDiscoveryProvider：初始意图与检索前沿
  → Tavily：每次获取供应商允许页大小的公开来源
  → 来源正文：按模型请求体分片，但所有分片均参与绑定
  → 绑定模型：产品名 + 公开 source_url + 动态 category
  → 确定性 URL/名称去重
  → 覆盖审计：饱和则停止；未饱和可追加去重检索词
  → durable candidate-selection interrupt
  → 用户选择后才进入 Scope freeze
```

### 5.3 停止与失败语义

| 条件 | 动作 | 是否丢弃已核验候选 |
|------|------|--------------------|
| 覆盖饱和且候选非空 | 正常停止，进入确认 | 否 |
| 某条检索没有新来源 | 跳过该条来源绑定，但仍运行覆盖审计以决定是否追加替代词 | 否 |
| 检索前沿耗尽 | 正常停止，进入确认或范围确认 | 否 |
| Tavily/模型预算不足 | 不发送下一外部调用，停止扩展 | 否 |
| 单个绑定批次预算不足 | 保留已完成批次 | 否 |
| 不可恢复 Provider 错误 | 取消尚未发送的同轮绑定任务并上抛 | 已完成部分由账本保留，但本轮交由现有 Graph 错误语义处理 |
| 无任何安全来源 | 返回空候选，直接范围确认 | 不适用 |

## 6. 接口与配置契约

```python
class _CoverageAssessment(FrozenModel):
    saturated: bool = False
    rationale: str = ""
    follow_up_queries: tuple[str, ...] = ()
```

`follow_up_queries` 是模型针对尚未覆盖竞争空间提出的临时检索词。它不携带候选产品、范围、层级或维度；执行前必须和已执行/待执行词做规范化去重。

```python
class OpenCompeteSettings(BaseSettings):
    tavily_max_results_per_query: int  # 上游 Researcher 保持原语义
    discovery_search_results_per_query: int  # Discovery 单次相关来源页大小，默认 5
    discovery_binding_max_input_chars: int  # 单次模型请求体边界，不是候选数
```

`discovery_search_results_per_query` 的取值仍受当前 Tavily SDK/服务端的单请求最大值约束；这不是产品层“最多搜索多少网页”的业务规则。候选发现不保存 API Key、原始模型上下文或不可信网页指令。

## 7. 测试与完成定义

| 层级 | 验证 |
|------|------|
| Provider 单测 | 单页多候选、跨来源聚合、URL 核验、无候选数上限、覆盖饱和短路、动态追加检索词、重复词不重发、预算耗尽保留已有池。 |
| Settings / Application | 发现页大小与上游 Researcher 页大小独立传递。 |
| 前端 | 现有动态类别分组、候选计数、来源数、默认未勾选保持可用；生产构建通过。 |
| 真实模型 | 只运行到 Trae 候选确认点，验收多个合理候选和真实来源，不接受 Scope。 |

完成门禁：相关 Ruff、mypy、pytest、`npm run build`、`git diff --check` 均通过；相对上游 `src/open_deep_research/` 无本轮新增修改。

### 7.1 本轮验证记录

- 离线：`tests/opencompete/test_providers.py`、`test_settings.py`、`test_application.py` 与 `test_graph.py` 共 95 项通过；全量为 `415 passed, 1 deselected`。
- 静态：本轮 Python 路径 Ruff 与 mypy 通过；前端 `npm run build` 通过。
- 真实候选确认点：临时 Run 使用“调研 Trae AI IDE 的竞品与适用场景”，得到 26 个候选和 12 个不同来源域名；账本记录 2 次 Tavily 调用、未提交 Scope、未启动上游研究。
- 传输取舍：一次将发现页大小升至 20 的真实压测虽能得到多候选，但导致 39 次 LLM 绑定/审计调用。正式默认改回每轮最相关的 5 个来源；覆盖审计可继续追加检索词，因此没有恢复任何候选或查询总数上限。

## 8. 审计摘要

| 设计 | 对应需求 | 删除后果 | 最小实现 |
|------|----------|----------|----------|
| 动态检索前沿 | REQ-002、REQ-003 | 初始模型计划遗漏时无法补洞，候选数仍偶然偏少 | `deque` + 规范化去重 + `follow_up_queries` |
| 独立发现页大小 | REQ-001、REQ-005 | 提高发现覆盖会无意改变深度研究成本/行为 | 一个 Settings 字段和 Application 参数映射 |
| 完整来源分批绑定 | REQ-001、REQ-004 | 长集合页后半部分产品丢失 | 复用现有 `_binding_batches()`，不新建存储或爬虫 |
| 覆盖饱和审计 | REQ-003 | 系统只能使用固定轮次或无限检索 | 复用摘要模型结构化输出，候选非空时才可停止 |
