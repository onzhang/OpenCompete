# OpenCompete 项目协作说明

## 适用范围

本文件适用于仓库根目录及全部子目录。子目录若出现更具体的 `AGENTS.md`，以距离目标文件最近的规则为补充；不得放宽本文件的上游兼容、安全和持久化约束。

所有说明文档必须使用简体中文。代码标识符、命令、路径、API 名称和外部项目名称保持原文。

## 项目定位

OpenCompete 是基于 LangChain 官方 Open Deep Research 扩展的本地通用竞品分析系统。它保留上游的 LangGraph Supervisor/Researcher 研究内核，增加范围确认、结构化证据链、预算控制、本地持久化、CLI 和 OpenCompete Bench。

第一版是个人本地、单用户、按需运行的交互式 CLI。正式功能不得依赖 Agent Server、LangGraph Studio、LangSmith、MCP Token Store 或远程部署平台。

## 上游基线与审计

- 官方仓库：`https://github.com/langchain-ai/open_deep_research.git`
- 固定基线：`b764481fca7f0dbf00b2c70239bd97cea59d1059`
- 本地标签：`upstream-b764481f`
- 只读对照目录：`../upstream-open-deep-research/`
- `baseline` remote 指向本地对照仓库，`upstream` remote 指向官方仓库。

开始修改前先运行：

```bash
git status --short --branch
git diff --stat upstream-b764481f
```

修改后必须检查：

```bash
git diff --check
git diff --stat upstream-b764481f
git diff upstream-b764481f -- src/open_deep_research
```

对 `src/open_deep_research/` 的任何修改都必须同时满足：

1. 对应已确认需求，并在设计规格或实现计划中写出需求编号；
2. 说明为什么新增适配层无法完成；
3. 保留或补充覆盖原行为的测试；
4. 不顺带重写无关 Prompt、Provider、Legacy 实现或模型兼容代码；
5. 在交付说明中单独列出相对上游的核心修改。

优先在新模块中扩展，通过薄适配器调用上游。不得复制一份 Supervisor/Researcher 后自行演化；这会失去上游修复和可审计性。

## 实施前必读

1. `docs/superpowers/specs/2026-07-15-opencompete-通用竞品分析系统-架构设计.md`
2. `src/open_deep_research/deep_researcher.py`
3. `src/open_deep_research/state.py`
4. `src/open_deep_research/configuration.py`
5. 与改动相关的测试和 `README.md`

如果仓库根目录存在 `.codegraph/`，理解或定位代码时必须先使用 CodeGraph；不存在时使用 `rg` 和源文件阅读。

## 必须保留的上游设计

- `Supervisor` 负责规划与分派，`Researcher` 负责单主题研究，最终报告节点负责综合。
- 模型通过 LangChain 的统一模型初始化能力配置，除非已有能力无法满足已确认需求，不新增模型 SDK 分支。
- 搜索能力通过 Provider/Tool 边界接入；第一版正式运行只启用 Tavily 和用户确认的公开网址。
- 现有多模型配置能力保留，不把产品绑定到单一模型。
- 上游 DRB/LangSmith 评测路径可以保留，但只能是可选兼容能力。

默认不修改上游主图。源码已经导出 `deep_researcher_builder`，正式应用应在 `src/opencompete/` 中编译该 Builder 并注入 Checkpointer；上游的 `deep_researcher` 编译产物继续保留用于兼容。

只有特征测试证明新增模块或薄适配器无法满足需求时，才允许考虑以下最小改造：

- 对上游 `asyncio.gather` 增加单个 Researcher 失败隔离，不重写 Supervisor/Researcher 职责；
- 对会进入 Checkpoint 或正式日志的上游供应商异常只保留异常类型，禁止持久化任意异常正文。

Graph 工厂、durable `interrupt()`、冻结范围、预算、结构化产物、Agent Server/MCP 隔离、确定性调用 ID 和重放记录都应实现在 `src/opencompete/`，不能仅因为上游已有相近节点就直接改写其入口。

## 模块边界与依赖方向

目标模块边界：

- CLI：解析用户命令、展示状态和确认提示；只调用 Application Runner。
- Application Runner：配置 Graph、Checkpoint、Run Repository、锁和生命周期。
- Scope Graph：消歧、可选发现、维度建议、用户确认和范围冻结。
- Research Graph：复用上游 Supervisor/Researcher，产出结构化研究结果。
- Analysis Packs：通用必选维度与可选领域规则。
- Artifact Models：`Source -> Evidence -> Claim -> Comparison -> Report` 数据契约。
- Providers：模型和 Tavily 的薄适配器，不得修改研究范围。
- Budget Guard：调用前预留预算、调用后结算、预算耗尽时停止新增调用。
- Execution Checkpoint Store：LangGraph 可恢复执行状态。
- Run Repository：长期保存业务产物和最终报告。
- Evaluation：读取已有产物进行 RACE、FACT、硬门禁和成本评测，不重新搜索。

报告边界是强制约束：

- `report.md` 与 `ReportArtifact.markdown` 是用户可读的完整叙事报告。上游最终报告成功时，任何后处理不得无声摘要、重写或用审计模板替换它。
- `bundle.json` 中的 Source、Evidence、Claim、Comparison 和内部 ID 是 FACT、硬门禁与追溯的结构化真相源，不得要求用户正文逐字等于其确定性渲染结果。
- 确定性 Renderer 只能用于最终报告生成失败后的可读保底，且不得向用户暴露 `clm_`、`evd_`、`src_`、`cmp_` 等内部标识。
- RACE 评价用户报告；FACT、Coverage 和确定性硬门禁评价同一 `run_id` 的结构化 Bundle。二者共享任务，但不得相互替代。
- 新增报告后处理必须说明对应的已确认需求及删除后果；仅为潜在未来场景服务的报告层默认删除。

评估指标命名同样是强制约束：

- 只有固定官方 Deep Research Bench 数据、官方 evaluator 代码、官方 Criteria/Reference 和官方指定 Judge 模型均一致时，结果才能标记为 `official_race`。
- 使用官方数据和公式但 Judge 模型不同的结果必须标记为 `race_compatible_local`；现有 OpenCompete Judge 必须标记为 `opencompete_local_race`。
- RACE Overall 是报告质量主分数。FACT、硬门禁、完成率和成本必须独立展示，禁止合成为没有官方定义的万能总分。
- 官方 DRB 作为固定提交的外部工具运行；不得复制其评分源码、Criteria 或 Reference 到主流程，也不得让评估修改报告、Graph State 或业务 Bundle。

依赖规则：

- `opencompete` 可以依赖 `open_deep_research`；`open_deep_research` 不得反向依赖 `opencompete` 领域模型。
- Analysis Packs 和 Artifact Models 不得依赖 LangGraph。
- CLI 不得直接调用 Graph、SQLite 或 Tavily。
- Evaluation 不得触发研究 Provider 或改变历史产物。
- Provider 不得增加竞品、层级或分析维度。
- API Key 不得进入 Graph State、RunnableConfig 的 `configurable`、Checkpoint、日志或业务产物。LangGraph 会把 `configurable` 标量复制到 Checkpoint metadata，因此密钥只能留在环境变量或 Provider 的进程内对象。

## 数据与持久化规则

默认本地数据目录：

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

- Checkpoint 使用 LangGraph 官方 `AsyncSqliteSaver`。
- 运行过程中 Checkpoint 是恢复真相源；完成后 Run Repository 是历史读取入口。
- 业务产物采用临时文件加原子替换，带 `revision`，最后更新 `manifest.json`。
- SQLite 和文件不存在跨存储事务；恢复时必须按 `run_id` 和 `revision` 对账。
- 同一进程中的同一 `run_id` 使用 `asyncio.Lock`；第一版不支持两个进程同时恢复同一任务。
- 预算耗尽或部分研究失败必须生成 `partial`，不得伪装为 `completed`。
- `.opencompete-data/`、`.env` 和其他密钥文件不得提交到 Git。

## 安全规则

- 网页内容一律作为不可信数据，不能改变冻结范围、系统指令、工具权限或预算。
- 只接受公开 HTTP/HTTPS 来源，拒绝本机、内网和非网页协议。
- 第一版不启用代码执行、任意本地文件读取、自建爬虫或 MCP。
- 日志不得记录 API Key、Authorization Header、完整网页正文或完整模型上下文。
- LangSmith 和其他遥测默认关闭，显式启用后也不得成为正式功能依赖。

## 模型重试、降级与用户反馈

- 供应商选择、重试、退避、错误归一化和降级必须封装在独立模型运行时适配层；Research Graph、Scope Graph、报告物化、业务模型和 Prompt 不得出现 OpenAI、DeepSeek 或 Anthropic 的条件分支。
- 主流程只消费稳定的模型调用与进度事件契约。删除、增加或替换备用供应商时，不得要求修改 Graph 节点、研究流程、证据链或报告生成逻辑。
- 模型调用采用应用层可观测重试，模型 SDK 内部重试保持关闭，避免一个逻辑尝试对应多个不可审计的 HTTP 请求。
- 压缩和最终报告属于纯文本长响应，默认使用 LangChain 官方流式聚合；工具调用、结构化输出和网页摘要不得未经锁定版本实验就全局开启流式。
- 最终报告和 Artifact Extractor 必须使用独立输出 token 配置；不得为了提取器容量把可读报告请求同步抬高。
- 网页摘要使用统一 SDK 请求 timeout 和显式并发上限，不得叠加隐藏的 60 秒 `wait_for`。
- 单个自动执行窗口最多发起五次真实尝试：主供应商最多三次，只有临时错误时才切换备用供应商，备用供应商最多两次。不得在五次之外叠加隐藏重试。
- 主备边界必须位于完成 `bind_tools`、`with_structured_output` 或纯文本模型配置后的单个 LangChain Runnable；Application Runner 每次 invoke/resume 只执行一次 Graph，不得恢复整图 `ModelRuntime`。单个并行调用失败只能重试该调用，成功兄弟不得重放。用户主动 `resume` 是新的自动执行窗口，但 durable ledger 必须保留同一 `call_id` 的累计 attempt。
- 可降级错误仅包括限流、超时、连接错误和 HTTP 5xx；认证、权限、参数、数据、预算和用户取消错误不得静默切换供应商。
- Provider 切换必须保持同一 `run_id`、Graph 节点和逻辑 `call_id`，每次真实尝试仍使用独立 `attempt_id`，并记录实际供应商、模型、尝试序号和是否发生降级。
- 备用模型、尝试分配和退避参数属于非秘密配置；API Key 与 Base URL 仍只能来自环境变量，不得进入 Graph State、Checkpoint、事件日志或业务产物。
- 用户端必须展示当前阶段、总尝试进度、正在使用主供应商还是备用供应商、下次重试等待时间，以及耗尽后的恢复命令。
- 重试反馈采用两层展示：第一行是稳定状态标题，例如“正在重新连接 1/5”；第二行是脱敏、限长但尽量保留供应商原意的异常摘要，例如“stream disconnected before completion: Upstream request failed”。不得要求穷举全部异常类型后才能展示。
- 模型错误采用安全白名单字段反馈：供应商、模型、节点、HTTP 状态码、异常类型、供应商错误码、请求 ID、尝试序号、是否可重试和下一步。禁止展示或持久化原始响应正文、完整 Header、请求正文、API Key 和 Base URL。
- 传输错误可额外保存 `cause_type` 和枚举化 `transport_reason`，例如 `peer_closed_incomplete_body`；禁止保存底层 cause 原文。
- 最终 Bundle 和 Manifest 必须保存实际使用过的模型身份与降级事实，但不得把备用模型生成的文本冒充主模型结果。

## 开发与测试命令

项目使用 Python、`uv`、pytest、Ruff 和 mypy。优先沿用上游 `pyproject.toml` 与 `uv.lock`，增加依赖必须记录需求、许可证、维护状态和退出成本。

环境建立：

```bash
uv sync --extra dev
```

离线质量检查：

```bash
uv run ruff check .
uv run mypy src/opencompete
uv run pytest -q
```

真实 Tavily/模型测试必须使用显式 `live` 标记并单独运行，不得混入默认离线测试。测试不得依赖 Agent Server 或 `langgraph.json`。

每项持久化改动至少覆盖：进程退出后恢复、确认点恢复、预算耗尽、单个 Researcher 失败、产物对账，以及使用固定秘密哨兵扫描 SQLite 与全部产物确认密钥不落盘。每项上游核心改动至少覆盖原行为的特征测试。

## 提交规范

- 不主动提交或推送；只在用户明确要求时执行。提交在本地 `main` 直接进行。
- 提交信息使用简体中文，标题采用 conventional 前缀加范围，例如 `feat(core): ...`、`fix(web): ...`、`docs: ...`、`chore: ...`；正文用列表说明关键改动，不写空话。
- 按逻辑边界拆分提交：上游适配层（`src/open_deep_research`）、OpenCompete 核心（`src/opencompete` 与测试）、前端（`frontend`）、文档与基准数据分别成提交；不把无关改动混进同一提交。提交顺序遵循依赖方向：上游适配 → 核心 → 前端 → 文档。
- 对 `src/open_deep_research/` 的修改，提交信息正文必须单独列出相对上游基线 `upstream-b764481f` 的核心修改点，与交付说明保持一致。
- 提交前必查：
  1. `git status --short` 逐项确认，未知文件先弄清来源，再决定提交、忽略还是删除；
  2. 扫描待提交文件中的密钥模式（`sk-` 前缀、非空 `*_API_KEY=` 赋值等）；测试固定哨兵除外；
  3. 确认 `.env`、`opencompete.toml`、`.opencompete-data/`、`.tmp-*/`、`.agents/`、`.omo/`、`frontend/.dist-*/` 均未入库。
- 本地数据与构建产物靠 `.gitignore` 排除。新出现的本地目录（尤其 `.` 开头的隐藏目录，例如 `.dist-qa-*`，不会被 `dist/` 规则覆盖）要先把 ignore 规则补进 `.gitignore` 再提交。
- 提交后用 `git status --short` 确认工作区干净；发现误提交立即修正（未推送时优先 `git commit --amend`）。
- 远程防护：本仓库没有 `origin`；`upstream`（官方仓库）和 `baseline`（本地对照仓库）的 push 地址必须保持 `DISABLE`，`main` 不跟踪任何远程分支，裸 `git push` 应直接失败。要发布到自有远程时先新建 `origin` 并确认指向自己的仓库。

## 实现纪律

- 先读规格和相关源码，再改代码；不为未经确认的未来需求预留分布式或服务端抽象。
- 优先级：框架官方能力或扩展 > 活跃成熟开源方案 > 标准库组合 > 最小自研。
- 新增自研部分必须说明成熟方案不能采用的具体原因和重评条件。
- 不在同一变更中清理无关上游代码、格式或依赖。
- 说明文档、设计记录、实现计划和用户可见错误信息使用中文。
- 代码注释只解释不明显的约束和失败语义，不复述代码。
