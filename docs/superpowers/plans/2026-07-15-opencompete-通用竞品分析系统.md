# OpenCompete 通用竞品分析系统实现计划

> **面向智能体执行者：** 按任务逐步执行；每一步使用 checkbox 跟踪。前一任务的接口或验证未完成前，不合并依赖它的任务。

**目标：** 在不依赖 Agent Server 的前提下，将固定上游 Open Deep Research 扩展为可确认范围、可恢复、可控制预算、可保存结构化报告并可离线评测的本地通用竞品分析 CLI。

**架构：** 以 `docs/superpowers/specs/2026-07-15-opencompete-通用竞品分析系统-架构设计.md` 为唯一批准规格。默认不修改 `src/open_deep_research/`，在新的扁平包 `src/opencompete/` 中组合上游 `deep_researcher_builder`、LangGraph durable interrupt、`AsyncSqliteSaver`、本地 Run Repository 和评测模块；只有失败隔离测试证明适配器无法满足 REQ-005/REQ-009 时，才对上游 `asyncio.gather` 做最小补丁。

**技术栈：** Python 3.11+、LangGraph 1.2.x、LangChain、Pydantic 2、Pydantic Settings、Tavily、`langgraph-checkpoint-sqlite` 3.1.x、SQLite、pytest、Ruff、mypy、`uv`。

## 全局约束

- 固定上游基线是 `b764481fca7f0dbf00b2c70239bd97cea59d1059`；交付前必须审计 `git diff upstream-b764481f -- src/open_deep_research`。
- 正式创建、恢复、查看、报告和评测功能不得依赖 Agent Server、`langgraph.json`、LangGraph Studio 或 LangSmith。
- CLI 只调用 Application Runner，不直接操作 Graph、SQLite、目录布局或 Tavily。
- Analysis Packs 和业务模型不依赖 LangGraph；`open_deep_research` 不得反向依赖 `opencompete`。
- 第一版正式搜索只允许 Tavily 和用户确认的公开 HTTP/HTTPS 网址；MCP、代码执行、本地文件读取和其他搜索路径禁用。
- API Key 不得进入命令参数、Graph State、RunnableConfig `configurable`、Checkpoint、日志、Manifest、Bundle 或 Report。
- 预算耗尽、证据不足或局部失败必须产生 `partial` 或 `Unknown`，不得伪装成完整结果。
- 所有说明文档和用户可见错误信息使用中文。
- 禁止未经批准的依赖新增、顺手重构上游、复制 Supervisor/Researcher、`type: ignore` 和宽泛异常吞噬。
- 模型 SDK 内部重试保持为零；单个逻辑模型调用总计最多五次，按主供应商三次、备用供应商两次分配，仅临时错误允许切换。
- 用户可见和持久化的模型错误只使用安全白名单字段，不保存原始响应正文、完整 Header、请求正文、API Key 或 Base URL。

### 任务 13：模型降级、错误详情与 CLI 运行体验

**对应需求：** REQ-014、REQ-015、REQ-016、REQ-017。

**文件：**

- Modify: `AGENTS.md`
- Modify: `opencompete.example.toml`
- Modify: `src/opencompete/settings.py`
- Create: `src/open_deep_research/model_routing.py`
- Modify: `src/opencompete/models.py`
- Modify: `src/opencompete/security.py`
- Modify: `src/opencompete/callbacks.py`
- Modify: `src/opencompete/application.py`
- Modify: `src/opencompete/cli.py`
- Modify: `src/open_deep_research/configuration.py`
- Modify: `src/open_deep_research/deep_researcher.py`
- Modify: `src/open_deep_research/utils.py`
- Test: `tests/opencompete/test_settings.py`
- Test: `tests/opencompete/test_security.py`
- Test: `tests/opencompete/test_callbacks.py`
- Test: `tests/opencompete/test_application.py`
- Test: `tests/opencompete/test_cli.py`
- Test: `tests/opencompete/test_live_full_report.py`

**步骤：**

- [ ] 用锁定依赖和用户本地环境对备用 DeepSeek Anthropic 兼容入口执行最小真实结构化输出与工具调用测试，只输出成功状态或安全错误字段。
- [ ] 新增非秘密备用模型与重试分配配置；密钥和 Base URL 继续只从环境读取，配置快照不得包含它们。
- [x] 使用 LangChain `with_retry` 与 `with_fallbacks` 的薄封装实现单次调用主三备二、退避和非秘密路由元数据；Application Runner 不重试整张 Graph，业务模型与 Prompt 不出现供应商条件分支。
- [ ] 优先复用 LangChain 官方 `with_retry`/`with_fallbacks` 能力；若其回调与持久化无法表达总计五次的契约，只在适配层实现最小调度，不复制模型 SDK 生命周期。
- [ ] 扩展 attempt 事件的安全字段，保证实际 Provider、模型、HTTP 状态、异常类型、供应商错误码、请求 ID、尝试序号和 fallback 标记可审计。
- [ ] 使用现有 Rich 为交互式 CLI 增加阶段、尝试、切换和退避状态；非 TTY 环境输出稳定逐行消息。
- [ ] 改善最终失败和 `show` 未产出状态，明确任务已保存、当前阶段、报告状态和完整 `resume` 命令。
- [ ] 增加主供应商临时失败后切换、永久错误不切换、总尝试不超过五次、秘密扫描、窄终端和跨进程恢复测试。
- [ ] 运行定向测试、全量离线测试、Ruff、mypy、上游差异审计和显式 live 完整报告测试。

**完成条件：**

- [ ] 一个逻辑模型调用最多产生五个 `call_attempt_started`，前三次为主供应商，后两次为备用供应商。
- [ ] 用户在等待期间能看见当前阶段、尝试进度、Provider 切换和下一次动作。
- [ ] 最终失败包含足以排障的安全详情和恢复命令，本地产物秘密扫描通过。
- [ ] 备用供应商完成结构化输出、工具调用、研究压缩和最终报告真实验证。
- [ ] 原失败任务能够从同一 Checkpoint 恢复并形成可查看的真实报告。

## 文件图

| 文件 | 职责 |
|------|------|
| `src/opencompete/__init__.py` | 包版本与稳定公开符号 |
| `src/opencompete/models.py` | Scope、证据链、Run 和预算 DTO |
| `src/opencompete/analysis.py` | 通用 Analysis Pack 和冻结 Scope 构造 |
| `src/opencompete/settings.py` | TOML、环境变量和 CLI 非秘密覆盖的配置加载 |
| `src/opencompete/security.py` | URL 公网校验和日志脱敏 |
| `src/opencompete/errors.py` | 配置、认证、预算、Provider、校验和持久化错误分类 |
| `src/opencompete/budget.py` | 异步预算预留、结算、取消和快照 |
| `src/opencompete/callbacks.py` | LangChain 模型/工具调用预算与用量回调 |
| `src/opencompete/providers.py` | Tavily-only 发现接口、调用 ID 和 Provider 错误映射 |
| `src/opencompete/persistence/` | Saver 生命周期、durable call ledger、Run Repository、原子发布和对账 |
| `src/opencompete/extractors.py` | 受约束的结构化 Claim/Comparison 提取协议与生产实现 |
| `src/opencompete/materialize.py` | 捕获 Source/Evidence，并组合 Extractor 结果生成可追溯 Bundle |
| `src/opencompete/graph.py` | 外层 Scope Graph、durable interrupt 和上游子图组合 |
| `src/opencompete/application.py` | 创建、恢复、查看、列表和评测用例编排 |
| `src/opencompete/evaluation/` | Benchmark 读取、硬门禁、RACE/FACT Judge 协议和结果保存 |
| `src/opencompete/cli.py` | `new/resume/list/show/evaluate` 命令和交互确认 |
| `tests/opencompete/` | 全部默认离线测试和显式 live 冒烟测试 |
| `benchmarks/opencompete/v1/` | 固定数据集清单、6 至 10 个案例、Criteria 和参考报告 |
| `opencompete.example.toml` | 不含密钥的本地配置示例 |

## 并行执行批次

- 批次 A：任务 2、3、4、5 在任务 1 完成后并行，分别由独立子智能体拥有文件。
- 批次 B：任务 6 在任务 2/4 完成后，任务 7 在任务 2/5 完成后并行。
- 批次 C：任务 8 依赖任务 2/5；任务 9 依赖任务 6/7。
- 批次 D：任务 10 至 12 由主智能体集成、修复和验收。

---

### 任务 1：项目入口与依赖锁

**文件：**

- Create: `src/opencompete/__init__.py`
- Create: `src/opencompete/cli.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `.gitignore`
- Create: `pytest.ini`
- Create: `opencompete.example.toml`
- Test: `tests/opencompete/test_package.py`

**接口：**

- Produces: 可导入的 `opencompete` 包和只提供 `--help` 的最小 `opencompete = "opencompete.cli:main"` 脚本壳；任务 9 扩展正式命令。
- Produces: 运行时直接依赖 `pydantic-settings>=2.14,<3`、`langgraph-checkpoint-sqlite>=3.1,<4`，开发时直接依赖 `pytest-asyncio>=1.3,<2`。

**步骤：**

- [x] 创建包导入失败测试，断言 `opencompete` 可导入且版本字符串存在。
- [x] 运行 `uv run pytest -q tests/opencompete/test_package.py`，确认失败。
- [x] 在 `pyproject.toml` 增加脚本和两项成熟依赖，不删除上游依赖；将显式 packages 改为 `find` 并包含 `open_deep_research*`、`opencompete*`、`legacy*`。
- [x] 新建 `pytest.ini` 注册 `live`、`langsmith` marker，默认 `-m "not live and not langsmith"`，防止上游昂贵测试进入离线门禁。
- [x] 在 `.gitignore` 增加 `.opencompete-data/`，配置示例不包含密钥字段。
- [x] 执行 `uv lock` 和 `uv sync --extra dev`。
- [x] 运行包测试并记录锁定的 LangGraph 1.2.9、Checkpoint 4.1.1、SQLite Checkpointer 3.1.0、Pydantic Settings 2.14.2；将 mypy 升级到 2.3.x，修复上游 1.16 分析 LangSmith 生成客户端时的内部崩溃。

**完成条件：**

- [x] `uv run python -c "import opencompete"` 成功。
- [x] `uv run opencompete --help` 能加载入口且不导入 Agent Server。
- [x] 满足 REQ-001、REQ-010、REQ-013 的基础条件。

---

### 任务 2：业务模型、证据链与通用 Analysis Pack

**文件：**

- Create: `src/opencompete/models.py`
- Create: `src/opencompete/analysis.py`
- Test: `tests/opencompete/test_models.py`
- Test: `tests/opencompete/test_analysis.py`

**接口：**

- Consumes: Pydantic 2。
- Produces: `AnalysisLevel`、`RunStatus`、`ClaimStatus`、`ResearchEntity`、`ResearchRequest`、`ScopeSpec`、`Source`、`Evidence`、`Claim`、`Comparison`、`ReportArtifact`、`RunBundle`、`RunManifest`、`RunSummary`、`RunHandle`。
- Produces: `build_draft_scope(request) -> ScopeSpec`、`freeze_scope(scope, confirmation) -> ScopeSpec`、`validate_traceability(bundle) -> list[GateViolation]`。

**步骤：**

- [x] 写失败测试：三个层级任意组合、实体非空、冻结后不可静默修改、已知 Claim 必须引用有效 Evidence/Source、无证据 Claim 必须为 `unknown`。
- [x] 运行 `uv run pytest -q tests/opencompete/test_models.py tests/opencompete/test_analysis.py`，确认失败。
- [x] 实现 DTO、规范化 ID、通用必选维度和 Scope revision。
- [x] 明确通用维度映射：产品与服务、公司与组织、产业与市场分别有稳定的基础维度。
- [x] 运行测试并执行 `uv run mypy src/opencompete/models.py src/opencompete/analysis.py`。

**完成条件：**

- [x] 证据链硬门禁能够确定性发现悬空引用和伪已知 Claim。
- [x] 未知行业无需领域包即可生成可确认 Scope。
- [x] 满足 REQ-002、REQ-003、REQ-004、REQ-005。

---

### 任务 3：配置、错误分类与安全边界

**文件：**

- Create: `src/opencompete/settings.py`
- Create: `src/opencompete/errors.py`
- Create: `src/opencompete/security.py`
- Test: `tests/opencompete/test_settings.py`
- Test: `tests/opencompete/test_security.py`

**接口：**

- Produces: `OpenCompeteSettings`、`load_settings(config_path, cli_overrides)`。
- Produces: `OpenCompeteError` 及配置、认证、预算、临时 Provider、数据校验、持久化子类。
- Produces: `validate_public_http_url(url) -> str`、`redact_text(text, secrets) -> str`、`sanitized_error(exc) -> ErrorRecord`。

**步骤：**

- [x] 写失败测试：CLI 非秘密覆盖 > 环境变量 > TOML > 默认值；环境变量密钥不出现在 `model_dump()` 或 RunnableConfig `configurable`。
- [x] 写失败测试：拒绝 `file:`、localhost、回环、私网、链路本地和保留 IP；接受规范公开 HTTPS URL。
- [x] 写失败测试：错误摘要和日志文本会脱敏 API Key、Bearer Token 和 Authorization Header。
- [x] 实现 Pydantic Settings 官方 Source 组合，不自研隐式配置框架。
- [x] 运行 `uv run pytest -q tests/opencompete/test_settings.py tests/opencompete/test_security.py`。

**完成条件：**

- [x] 密钥不能通过 CLI 字段进入配置模型。
- [x] LangSmith 默认关闭且正式功能不读取其返回值。
- [x] 满足 REQ-010、REQ-011、REQ-014。

---

### 任务 4：预算账本、调用回调与 Tavily-only Provider

**文件：**

- Create: `src/opencompete/budget.py`
- Create: `src/opencompete/callbacks.py`
- Create: `src/opencompete/providers.py`
- Test: `tests/opencompete/test_budget.py`
- Test: `tests/opencompete/test_callbacks.py`
- Test: `tests/opencompete/test_providers.py`

**接口：**

- Consumes: `OpenCompeteSettings`、安全 URL 校验和 LangChain async callback API。
- Produces: `BudgetLedger` Protocol、`BudgetGuard.reserve(call_id, estimate)`、`settle(reservation_id, actual)`、`cancel(reservation_id)`、`snapshot()`；本任务使用内存 Ledger 验证纯逻辑。
- Produces: `BudgetCallback`，在模型和 `tavily_search` 调用前预留、结束后结算。
- Produces: `DiscoveryProvider` Protocol、`TavilyDiscoveryProvider.discover(request, budget) -> list[ResearchEntity]`。

**步骤：**

- [x] 写并发失败测试：剩余预算只允许一个调用时，两个并行 `reserve` 只有一个成功。
- [x] 写回调失败测试：模型输入/最大输出和 Tavily query 数都会预留；未知实际用量保留预留值。
- [x] 写 Provider 失败测试：只返回候选对象，不能修改 `ScopeSpec`；临时错误与认证错误分类不同。
- [x] 实现稳定 `call_id` 和实际 `attempt_id` 契约；跨崩溃 `replayed` 判定由任务 5 的 durable Ledger 完成。
- [x] 运行 `uv run pytest -q tests/opencompete/test_budget.py tests/opencompete/test_callbacks.py tests/opencompete/test_providers.py`。

**完成条件：**

- [x] 并行调用不能共同透支本地预算。
- [x] Provider 不启用 MCP、原生搜索或其他搜索引擎。
- [x] 满足 REQ-005、REQ-006、REQ-009、REQ-011。

---

### 任务 5：持久化、事件日志与 SQLite durable ledger

**文件：**

- Create: `src/opencompete/persistence/__init__.py`
- Create: `src/opencompete/persistence/models.py`
- Create: `src/opencompete/persistence/checkpoints.py`
- Create: `src/opencompete/persistence/ledger.py`
- Create: `src/opencompete/persistence/repository.py`
- Create: `src/opencompete/persistence/reconciliation.py`
- Test: `tests/opencompete/persistence/test_checkpoint_store.py`
- Test: `tests/opencompete/persistence/test_execution_ledger.py`
- Test: `tests/opencompete/persistence/test_run_repository.py`
- Test: `tests/opencompete/persistence/test_reconciliation.py`
- Test: `tests/opencompete/persistence/helpers/checkpoint_worker.py`

**接口：**

- Consumes: `RunManifest`、`RunBundle`、`RunSummary`、安全脱敏函数。
- Produces: `RunRepository.initialize()`、`publish()`、`load_verified()`、`inspect()`、`list_runs()`、`append_event()`。
- Produces: `SqliteCheckpointStore.open_saver()` 异步上下文管理器；Graph 编译和执行只发生在上下文内部，Saver 不跨 event loop 复用。
- Produces: `SqliteExecutionLedger`，通过标准库 `sqlite3` 的短事务和 `asyncio.to_thread`，在同一 `checkpoints.sqlite3` 的独立 `oc_budget_reservations`、`oc_call_attempts` 表中持久化预留、结算、attempt 和 replay 状态；不直接导入传递依赖 `aiosqlite`。
- Produces: 纯函数 `reconcile(checkpoint, artifacts) -> ReconcileDecision`，决策包含 `CONTINUE_GRAPH/REBUILD_ARTIFACTS/NOOP/HISTORY_ONLY/CONFLICT/UNRECOVERABLE`。

**步骤：**

- [x] 写原子发布失败测试：Bundle/Report 成功但 Manifest 前失败时，不展示为 `completed`。
- [x] 写对账失败测试：revision 或哈希不一致时从 Bundle 重建 Report 并最后发布 Manifest。
- [x] 写事件测试：JSONL 只追加且敏感字段被拒绝或脱敏。
- [x] 写跨进程测试：进程 A 写 Checkpoint 后退出，进程 B 从同一 SQLite 文件读取并继续。
- [x] 写跨崩溃 Ledger 测试：未结算 attempt 在新进程中被识别为可能已消费，下一 attempt 标记 `replayed` 且旧预留不返还。
- [x] 写对账矩阵测试：无 Checkpoint 的有效产物只能历史查看；Repository 超前或 Scope 冲突必须失败关闭。
- [x] 实现同目录临时文件、flush/fsync、`os.replace` 和 Manifest 最后写入。
- [x] 运行 `uv run pytest -q tests/opencompete/persistence`。

**完成条件：**

- [x] `.opencompete-data/` 布局与设计规格一致。
- [x] Saver 生命周期不依赖 Agent Server。
- [x] 当前锁定组合在 Windows 跨进程恢复：LangGraph 1.2.9、checkpoint 4.1.1、SQLite Checkpointer 3.1.0。
- [x] 满足 REQ-007、REQ-008、REQ-010、REQ-014。

---

### 任务 6：报告结构化与确定性硬门禁

**文件：**

- Create: `src/opencompete/extractors.py`
- Create: `src/opencompete/materialize.py`
- Test: `tests/opencompete/test_extractors.py`
- Test: `tests/opencompete/test_materialize.py`

**接口：**

- Consumes: 冻结 `ScopeSpec`、上游 `final_report`、`raw_notes` 和预算快照。
- Produces: `ArtifactExtractor` Protocol、`LangChainArtifactExtractor` 和离线 `FakeArtifactExtractor`。
- Produces: `materialize_bundle(run_id, scope, upstream_state, budget, extractor) -> RunBundle`。
- Produces: 只对上游 `--- SOURCE n ---`、`URL:`、摘要做 Source/Evidence 捕获；Claim/Comparison 来自 Pydantic 结构化 Extractor，不能从自由文本猜测事实。

**步骤：**

- [x] 写失败测试：来源去重、Source/Evidence 稳定 ID、Extractor 只能引用已捕获 ID、无支撑结论转为 `Unknown`。
- [x] 写失败测试：Extractor 返回未知 Source/Evidence ID 时确定性校验拒绝，不能发布为已知 Claim。
- [x] 写失败测试：冻结 Scope 外实体不能进入 Comparison，网页指令文本不能产生新维度。
- [x] 实现 Source/Evidence 受控解析和结构化 Extractor；无法建立支持关系时保留缺口。
- [x] 运行 `uv run pytest -q tests/opencompete/test_extractors.py tests/opencompete/test_materialize.py`。

**完成条件：**

- [x] Bundle 能追溯每个已知 Claim，报告 Markdown 原文仍保存。
- [x] 满足 REQ-002、REQ-004、REQ-005、REQ-011。

---

### 任务 7：外层 Scope Graph 与上游研究子图组合

**文件：**

- Create: `src/opencompete/graph.py`
- Test: `tests/opencompete/test_graph.py`
- Test: `tests/opencompete/test_graph_composition.py`

**接口：**

- Consumes: `deep_researcher_builder`、`ScopeSpec`、`BudgetCallback` 和 `materialize_bundle`。
- Produces: `build_graph(checkpointer, research_graph=None) -> CompiledStateGraph`。
- Produces: 节点 `prepare_scope -> confirm_scope -> freeze_scope -> prepare_research_input -> odr_research -> materialize -> validate -> END`；发布由 Runner 在 Graph 终态后执行。

**步骤：**

- [x] 写失败测试：首次调用在 `interrupt()` 返回待确认 Scope，确认前假 Research Graph 调用数为零。
- [x] 写失败测试：`Command(resume=...)` 后冻结 Scope，并把 `allow_clarification=False`、`search_api="tavily"`、`mcp_config=None` 传入上游。
- [x] 写失败测试：恢复时使用同一 `thread_id=run_id`，删除 `langgraph.json` 不影响构建。
- [x] 写组合实验：只给最外层 Graph 注入 Saver，上游 `deep_researcher_builder.compile()` 作为编译子图节点，验证 Checkpoint 能恢复到子图内部状态。
- [x] 使用可注入假 Research Graph 保持默认测试离线；禁止在自定义异步节点中手工调用上游 `ainvoke()` 把整个研究退化为父 Graph 的大原子节点。
- [x] OpenCompete 自有 DTO 以 JSON 兼容值进入 Checkpoint；真实 SQLite 文件不得包含 `opencompete.models` 构造器元数据，并通过严格 msgpack 恢复实验。
- [x] 运行 `uv run pytest -q tests/opencompete/test_graph.py tests/opencompete/test_graph_composition.py`。

**完成条件：**

- [x] 用户确认是 durable interrupt，不是 `goto=END`。
- [x] `src/open_deep_research/` 只保留任务 11 已由特征测试证明必要的最小差异。
- [x] 满足 REQ-001、REQ-002、REQ-007、REQ-013。

---

### 任务 8：Application Runner 与进程内互斥

**文件：**

- Create: `src/opencompete/application.py`
- Test: `tests/opencompete/test_application.py`

**接口：**

- Consumes: Settings、Graph、Checkpoint Store、Run Repository 和 Evaluation Service。
- Produces: `ApplicationRunner.create_run()`、`resume_run()`、`list_runs()`、`load_run()`、`evaluate_dataset(EvaluationRequest)`。

**步骤：**

- [x] 写失败测试：创建任务发布 `draft`，到 interrupt 后为 `awaiting_confirmation`。
- [x] 写失败测试：同一进程同一 `run_id` 并发恢复只有一个持有锁。
- [x] 写失败测试：预算耗尽和局部错误发布 `partial`，持久化错误绝不发布 `completed`。
- [x] 实现 Saver 和 Repository 生命周期；Manifest 状态只在对应持久化步骤完成后更新。
- [x] 运行 `uv run pytest -q tests/opencompete/test_application.py`。

**完成条件：**

- [x] Runner 是 CLI 唯一应用入口。
- [x] 运行时不导入 LangGraph Server、LangSmith Client 或 MCP Store。
- [x] 满足 REQ-001、REQ-005、REQ-007、REQ-008、REQ-009。

---

### 任务 9：交互式 CLI

**文件：**

- Modify: `src/opencompete/cli.py`
- Test: `tests/opencompete/test_cli.py`

**接口：**

- Consumes: `ApplicationRunner` 和 Settings Loader。
- Produces: `main(argv: Sequence[str] | None = None) -> int`。

**步骤：**

- [x] 写失败测试：`new/resume/list/show/evaluate` 帮助与参数存在，任何命令都没有 API Key 参数。
- [x] 写失败测试：`new` 展示建议 Scope，用户确认后恢复同一 run；拒绝时保存 `cancelled`。
- [x] 写失败测试：`show` 和 `list` 只调用 Repository 路径，不构建研究 Graph。
- [x] 使用标准库 `argparse` 和现有 Rich，不新增 CLI 框架。
- [x] 运行 `uv run pytest -q tests/opencompete/test_cli.py`。

**完成条件：**

- [x] 五个正式命令在没有 `langgraph.json` 时工作。
- [x] 用户可见状态、缺口和错误信息为中文。
- [x] 满足 REQ-001、REQ-002、REQ-008、REQ-010。

---

### 任务 10：OpenCompete Bench 与离线评测

**文件：**

- Create: `src/opencompete/evaluation/__init__.py`
- Create: `src/opencompete/evaluation/models.py`
- Create: `src/opencompete/evaluation/dataset.py`
- Create: `src/opencompete/evaluation/hard_gates.py`
- Create: `src/opencompete/evaluation/judge.py`
- Create: `src/opencompete/evaluation/service.py`
- Create: `benchmarks/opencompete/v1/dataset.json`
- Create: `benchmarks/opencompete/v1/cases/ocb-001-aws-lambda-vs-azure-functions/`
- Create: `benchmarks/opencompete/v1/cases/ocb-002-m365-vs-google-workspace/`
- Create: `benchmarks/opencompete/v1/cases/ocb-003-visa-vs-mastercard/`
- Create: `benchmarks/opencompete/v1/cases/ocb-004-ups-vs-fedex/`
- Create: `benchmarks/opencompete/v1/cases/ocb-005-kindle-vs-kobo/`
- Create: `benchmarks/opencompete/v1/cases/ocb-006-spotify-vs-apple-music/`
- Create: `benchmarks/opencompete/v1/cases/ocb-007-supercharger-vs-ionity/`
- Create: `benchmarks/opencompete/v1/cases/ocb-008-cloud-infrastructure-competition/`
- Test: `tests/opencompete/evaluation/test_dataset.py`
- Test: `tests/opencompete/evaluation/test_hard_gates.py`
- Test: `tests/opencompete/evaluation/test_service_offline.py`
- Test: `tests/opencompete/evaluation/test_result_persistence.py`
- Test: `tests/opencompete/evaluation/test_bench_contract.py`

**接口：**

- Consumes: 固定 Benchmark Case、Reference Report、Criteria 和目标 `RunBundle`。
- Produces: `EvaluationTarget`、`EvaluationRequest`、`EvaluationBatchResult`、`load_dataset(path)`、`evaluate_hard_gates(case, bundle)`、`evaluate_with_judge(case, bundle, judge)`、`save_evaluation(result)`。
- Produces: `ReportJudge` Protocol；默认离线硬门禁不访问网络，显式配置后 Judge 只调用模型。

**步骤：**

- [x] 写失败测试：评测器没有搜索 Provider 依赖，目标 Bundle 不被修改。
- [x] 写失败测试：范围越界、悬空引用、错误 `Unknown`、预算违规和 revision 不一致会形成硬门禁失败。
- [x] 在上述八个明确目录中分别创建 `case.json`、`prompt.md`、`reference.md`、`criteria.json`、`sources.json`；每个案例含带 `as_of` 的权威来源清单和人工审核字段。
- [x] 实现 RACE 四维和 FACT 支持关系结果契约；无 Judge 时保存 `status="not_run"` 和原因，不计算无依据综合总分。
- [x] 运行 `uv run pytest -q tests/opencompete/evaluation`。

**完成条件：**

- [x] `opencompete evaluate <dataset> --targets <targets.json>` 可批量绑定案例与历史 Run；`--case/--run` 可评测单个案例。
- [x] Judge 模式不调用 Tavily或其他搜索。
- [x] 满足 REQ-004、REQ-012。

---

### 任务 11：上游并行失败隔离最小补丁

**文件：**

- Test: `tests/opencompete/test_upstream_failure_isolation.py`
- Modify: `src/open_deep_research/deep_researcher.py`
- Conditional Modify: `src/open_deep_research/utils.py`

**接口：**

- Consumes: 上游 `supervisor_tools` 当前 `asyncio.gather` 行为。
- Produces: 成功 Researcher 结果在同批其他 Researcher 失败时仍进入 notes/raw_notes。

**步骤：**

- [x] 先写上游特征测试，证明默认 `asyncio.gather()` 会因一个 Researcher 异常丢掉同批成功结果，且 `or True` 会吞掉所有异常。
- [x] 将 Supervisor 批量调用改为 `asyncio.gather(..., return_exceptions=True)`，逐项保留成功结果并为失败项生成脱敏 ToolMessage；删除 `if ... or True` 宽泛吞错路径。
- [x] 写 Callback 传播特征测试，验证 Tavily 网页摘要模型调用是否收到预算 Callback；只有失败时才在 `utils.py` 最小透传 RunnableConfig/callbacks。
- [x] 写上游异常脱敏特征测试，确保工具、最终报告和网页摘要日志不保存供应商异常正文，只保留异常类型。
- [x] 验证预算拒绝即使被 `execute_tool_safely` 转为字符串，也能从 durable ledger 得到 `partial`，不会继续透支。
- [x] 运行上游特征测试和全部 Graph 测试。
- [x] 输出 `git diff upstream-b764481f -- src/open_deep_research` 并逐行审查。

**完成条件：**

- [x] 成功结果不会因同批单个失败而丢失。
- [x] 上游差异只包含已由失败测试证明必要的失败隔离，或预算 Callback 透传。
- [x] 满足 REQ-005、REQ-009、REQ-013。

---

### 任务 12：恢复、安全和无 Agent Server 端到端验收

**文件：**

- Create: `tests/opencompete/test_end_to_end.py`
- Create: `tests/opencompete/test_no_agent_server.py`
- Create: `tests/opencompete/test_secret_persistence.py`
- Create: `tests/opencompete/test_recovery_process.py`
- Modify: `README.md`
- Modify: `.env.example`

**接口：**

- Consumes: 全部正式模块和假 Provider/假 Research Graph。
- Produces: 不联网的完整生命周期验收和中文运行说明。

**步骤：**

- [x] 覆盖 `new -> interrupt -> 进程退出 -> resume -> completed/partial -> show -> evaluate`。
- [x] 测试临时隐藏 `langgraph.json` 后全部正式流程仍通过。
- [x] 用固定秘密哨兵扫描 SQLite Checkpoint、Manifest、Bundle、Report 和 events，断言零泄漏；该哨兵不得放入 RunnableConfig `configurable`。
- [x] 验证 `src/open_deep_research` 相对基线差异并运行上游评测模块导入测试。
- [x] README 增加中文 OpenCompete CLI 快速开始，不删除上游原始说明和 Server 调试入口。

**完成条件：**

- [x] `uv run pytest -q` 全部离线测试通过。
- [x] `uv run ruff check .` 通过。
- [x] `uv run mypy src/opencompete` 通过。
- [x] `git diff --check` 通过。
- [x] 设计规格中 14 条 REQ 均有当前测试或产物证据。

### 任务 13：真实供应商完整报告闭环

**文件：**

- Create: `tests/opencompete/test_live_full_report.py`
- Modify: `src/open_deep_research/configuration.py`
- Modify: `src/open_deep_research/deep_researcher.py`
- Modify: `src/open_deep_research/utils.py`
- Modify: `src/opencompete/settings.py`
- Modify: `src/opencompete/extractors.py`
- Modify: `src/opencompete/security.py`
- Modify: `opencompete.example.toml`

**需求证据与取舍：**

- [x] 真实失败证明 OpenAI SDK 默认 600 秒超时和内部重试会破坏 attempt 与真实请求的一一对应；使用官方 `timeout`、`max_retries` 参数，SDK 重试关闭，由 Graph 记录重试。
- [x] 真实失败证明压缩请求同时受网页结果数量、内容规模和 tool-call 消息形态影响；使用 Tavily 官方 `max_results`、既有 `max_content_length` 和 ToolMessage 纯文本 transcript，不自研搜索器。
- [x] 真实失败证明最终报告对临时 5xx 立即降级；只对连接、超时、429 和 5xx 有限重试，认证与其他 4xx 不重试。
- [x] 真实失败证明兼容供应商可能弱化 `max_tokens`；按研究单元公平保留输入头尾，并在 Prompt 中增加基于既有 token 配置的字符硬限制。
- [x] 模型候选允许进入规范化层，但发布 `Claim`、`Comparison` 和 `RunBundle` 仍使用严格 Pydantic 与硬门禁。

**完成条件：**

- [x] `.env.example` 恢复为无密钥中文模板，真实 `.env` 被 Git 忽略。
- [x] live 使用用户提供的 Base URL、模型、OpenAI Key 和 Tavily Key；Base URL 根路径失败后只尝试同主机 `/v1`。
- [x] GitHub Copilot 与 Cursor、`capabilities` 与 `pricing` 的真实报告闭环通过。
- [x] Source、Evidence、Claim、Comparison 和 Markdown 非空，两个实体均被覆盖，零 traceability violation。
- [x] 报告磁盘内容、同进程新 Runner 复载和新 Python 进程 CLI `show` 一致。
- [x] `compress_research`、`final_report_generation`、`materialize` 都存在真实成功 attempt。
- [x] SQLite、WAL 和全部本地产物中真实 Key 与 Authorization 模式零落盘。
- [x] 最终离线门禁为 240 项通过，live 默认排除；无 `langgraph.json` 同样 240 项通过。

## 规格覆盖检查

| REQ | 覆盖任务 |
|-----|----------|
| REQ-001 | 1、7、8、9、12 |
| REQ-002 | 2、6、7、9、12 |
| REQ-003 | 2、4、7、10 |
| REQ-004 | 2、6、10、12 |
| REQ-005 | 2、4、6、8、11、12 |
| REQ-006 | 4、8、12 |
| REQ-007 | 5、7、8、12 |
| REQ-008 | 5、8、9、12 |
| REQ-009 | 4、8、11、12 |
| REQ-010 | 1、3、5、9、12 |
| REQ-011 | 3、4、6、7、12 |
| REQ-012 | 2、10、12 |
| REQ-013 | 1、7、11、12 |
| REQ-014 | 3、5、8、12 |

## 自检

- [x] 无占位词、空步骤或模糊文件路径。
- [x] 类型与符号名跨任务一致。
- [x] 每条 REQ 至少有一个确定性验证任务。
- [x] 默认测试不访问网络、不需要 API Key、不启动 Agent Server。
- [x] 子智能体文件所有权互不冲突，公共入口由主智能体合并。
- [x] 每个命令可以从仓库根目录直接复制执行。
