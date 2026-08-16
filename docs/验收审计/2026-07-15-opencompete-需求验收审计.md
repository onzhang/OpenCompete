# OpenCompete 需求验收审计

> 审计日期：2026-07-15  
> 固定上游基线：`b764481fca7f0dbf00b2c70239bd97cea59d1059`  
> 验收范围：本地 CLI、范围冻结、研究子图组合、预算、SQLite 恢复、本地产物、离线评测及上游最小补丁

## 1. 结论

OpenCompete 的本地正式运行路径已经证明可以在不启动 Agent Server、不使用 LangGraph Studio、LangSmith 或 MCP 的情况下工作。2026-07-16 最终门禁为 240 项默认测试通过，临时移走 `langgraph.json` 后同样 240 项通过；严格 msgpack 与真实 SQLite/跨进程恢复专项 12 项通过。Manifest 损坏恢复、最终 Markdown 证据闭合、生产 Judge/评测溯源和网页提示注入场景均已补强，因此 REQ-001 至 REQ-014 在当前首版范围内全部通过。

同日使用用户提供的 OpenAI 兼容供应商和 Tavily 运行真实完整报告闭环，最终一次在 9 分 43 秒内通过。该 live 测试真实覆盖研究、搜索、网页摘要、两个压缩节点、最终报告、结构化提取、SQLite 与本地产物复载、新 Python 进程 CLI `show` 和秘密扫描。测试预算按用户要求单独放宽，生产默认预算配置未改变。

本结论只表示代码与离线数据契约通过验收，不表示 OpenCompete Bench 已正式发布评分。八份参考报告已有实质性中文草稿，但仍保持 `draft`，需要项目所有者组织具名人工审核；默认 Application Runner 也没有配置 Judge，因此 RACE 和 FACT 会诚实保存为 `not_run`。

## 2. 需求与证据映射

| 需求 | 验收结论 | 主要确定性证据 |
|---|---|---|
| REQ-001 本地独立运行 | 通过 | `test_no_agent_server.py` 验证空工作目录完整生命周期；`test_graph_composition.py` 验证构建不读取 `langgraph.json`；移走该文件后全部 240 项默认测试通过。 |
| REQ-002 范围确认与冻结 | 通过 | `test_graph.py` 验证确认前不研究、确认后冻结和拒绝错误 thread；`test_analysis.py` 验证 revision 与重复确认；`test_materialize.py` 拒绝范围外结论。 |
| REQ-003 通用多层竞品分析 | 通过 | `test_analysis.py` 验证未知行业和三个层级的任意组合；`test_bench_contract.py` 用生产 Scope 规则构建八个跨领域案例。 |
| REQ-004 结构化证据链 | 通过 | `test_models.py` 验证悬空引用；`test_materialize.py` 验证 Source/Evidence 捕获和无效引用降级；`test_hard_gates.py` 对目标 Bundle 再次失败关闭。 |
| REQ-005 Unknown 与部分结果 | 通过 | `test_materialize.py` 验证无支撑结论、上游错误、预算耗尽和单 Researcher 失败都进入 `partial`；`test_upstream_failure_isolation.py` 保留同批成功结果。 |
| REQ-006 硬预算控制 | 通过 | `test_budget.py`、`test_callbacks.py` 和 `test_execution_ledger.py` 验证调用前预留、调用后结算、并发不透支及未知用量保留预留值。 |
| REQ-007 可恢复执行 | 通过 | `test_checkpoint_store.py` 和 `test_recovery_process.py` 使用两个新进程恢复确认点与研究子图；`test_graph_composition.py` 验证外层 Saver 恢复嵌入子图；`test_application.py` 覆盖 Manifest 缺失、非法 JSON 和状态/revision 不一致时以 Checkpoint 为真相源；严格 msgpack + 真实 SQLite 专项通过。 |
| REQ-008 长期业务产物 | 通过 | `test_run_repository.py` 验证 Bundle、Report、Manifest 原子发布与哈希；`test_application.py` 验证损坏报告修复和损坏 Bundle 从终态 Checkpoint 重建；`test_end_to_end.py` 验证历史查看。 |
| REQ-009 失败隔离与重放审计 | 通过 | `test_budget.py` 和 `test_execution_ledger.py` 验证稳定 `call_id`、独立 attempt 及跨进程 `replayed`；上游失败隔离特征测试验证成功结果不丢失。 |
| REQ-010 配置与密钥隔离 | 通过 | `test_settings.py` 验证密钥不进入 Settings 或 RunnableConfig；`test_secret_persistence.py` 扫描 SQLite 和全部业务产物；Repository 与 CLI 另有嵌套字段和异常脱敏测试。 |
| REQ-011 不可信网页边界 | 通过 | `test_security.py` 拒绝非网页协议、本机、内网和保留地址；`test_graph.py` 在研究前拒绝私网 URL；`test_upstream_prompt_injection.py` 验证网页、搜索结果和压缩 transcript 不能改变冻结范围、工具、权限或预算。 |
| REQ-012 可复现评测入口 | 通过 | 固定数据集、硬门禁、目标 revision、无搜索依赖、目标不可变及结果持久化均有测试；生产 Judge 默认关闭，显式配置后保存模型、Prompt/契约版本和案例内容哈希，FACT 逐 Claim-Evidence 评价。 |
| REQ-013 上游兼容与差异审计 | 通过 | `test_no_agent_server.py` 验证上游评测模块仍可导入；上游特征测试覆盖失败隔离、Callback、提示注入、异常脱敏和兼容 transcript；差异相对固定 Git 基线单独审计。 |
| REQ-014 本地可诊断性 | 通过 | `test_run_repository.py` 验证 JSONL 只追加与秘密脱敏；事件包含 `execution_id`、`attempt_id`、重试/重放和安全异常类；真实 5xx 被分类为 `temporary_provider_error`，不保存 URL、Header 或响应正文。 |

## 3. 最终质量门禁

| 门禁 | 结果 |
|---|---|
| `uv lock` | 通过，解析 237 个包。 |
| `uv sync --extra dev --locked` | 通过，锁文件与环境一致。 |
| `uv run pytest -q` | 通过，240 项；1 项 live 默认排除。 |
| `uv run ruff check .` | 通过。 |
| `uv run mypy src/opencompete tests/opencompete/test_live_full_report.py` | 通过，28 个源文件。 |
| 临时移走 `langgraph.json` 后运行 `tests/opencompete` | 通过，240 项；文件已恢复。 |
| `LANGGRAPH_STRICT_MSGPACK=true` 的 SQLite/恢复专项 | 通过，12 项。 |
| 真实模型 + Tavily 完整 live | 通过，1 项，9 分 43 秒。 |
| `git diff --check` | 通过。 |
| `git diff upstream-b764481f -- src/open_deep_research` | 已输出并逐项审阅，仅包含第 4 节列出的最小补丁。 |

测试中的 26 条 warning 来自固定上游使用的 Pydantic/LangGraph 弃用接口。当前没有顺带迁移上游 Graph API，因为这不属于已确认需求，且会扩大相对固定基线的差异。

## 4. 相对上游的最小修改

1. `deep_researcher.py`：并行 Researcher 使用 `return_exceptions=True`，逐项保留成功结果、传播取消，并把失败正文收敛为异常类型；删除会吞掉全部异常的 `or True`。
2. `configuration.py` 与模型创建点：透传单次请求超时和 SDK 重试次数。OpenCompete 设置 SDK 重试为 0，由 LangChain/Graph 产生可审计 attempt。
3. `utils.py`：透传 Tavily 官方 `max_results`；网页摘要失败只记录异常类型，不记录可能包含秘密的异常正文。
4. `deep_researcher.py`：压缩只把 ToolMessage 证据组成不可信纯文本 transcript，原始 AI/Tool 消息仍进入 `raw_notes`；手写重试显式标记 `retry_number`。
5. `deep_researcher.py`：最终报告只对连接、超时、429 和 5xx 有限重试；对每个研究单元公平保留输入头尾，并把现有 token 上限转成 Prompt 字符硬限制。
6. `deep_researcher.py` 与 `utils.py`：工具、压缩、摘要和最终报告错误不再把任意供应商异常正文写入持久化状态。

这些修改对应 REQ-005、REQ-009、REQ-010、REQ-013 和 REQ-014。外层范围、预算、Checkpoint 生命周期、业务产物和评测均位于 `src/opencompete/`，没有复制或重写上游 Supervisor/Researcher。

## 5. 未关闭的发布边界

1. 八份参考报告仍是 `draft`。具名审核人核对事实、截止日、引用支持关系和措辞之前，CLI 默认拒绝正式使用该数据集评分。
2. 默认 Runner 未配置报告 Judge。即使参考报告审核完成，默认评测也只保存硬门禁和成本，RACE、FACT 为 `not_run`。
3. 真实 live 证明了当前用户提供供应商在本次配置下可用，但不构成对该供应商 SLA、未来模型版本或实际账单的承诺；更换供应商后应重新运行显式 live。
4. 第一版只保证同一进程内同一 `run_id` 互斥，不支持两个进程同时恢复同一任务。
