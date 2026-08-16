# OpenCompete Web 动态范围与安全恢复实施计划

> 关联设计：[2026-08-15-OpenCompete-Web动态范围与安全恢复-架构设计.md](../specs/2026-08-15-OpenCompete-Web动态范围与安全恢复-架构设计.md)
>
> 目标：以最小改动移除 Web 固定研究矩阵，保证确认后状态耐久交接、只读检查和显式恢复，并用离线端到端测试验证。

## 全局约束

- 新 Web 路径不得引入固定维度目录、维度 slug 翻译表、`dimension_label()` 或 `id/label` 双轨。
- `AnalysisLevel` 保持三值稳定枚举；动态维度直接是中文自然语言字符串。
- 仅 Web 委托走 Planner；CLI、历史 Run、`create_fixed_scope_run()` 与 Benchmark 固定 Scope 保持兼容。
- `GET`、列表、服务启动、SSE 订阅不得执行研究、恢复研究或消耗预算。
- 仅 `POST /api/runs/{id}/resume` 可恢复非确认点任务；不得修改 `src/open_deep_research/`。
- 默认验证不得读取 `.env`、调用模型、Tavily 或外网。

## 文件地图

| 文件 | 改动职责 |
|------|----------|
| `src/opencompete/models.py` | 新增委托、规划提案和检查投影领域 DTO。 |
| `src/opencompete/scope_planning.py` | 新增薄 Planner，复用现有 LangChain 模型路由。 |
| `src/opencompete/analysis.py` | 使冻结严格遵从显式确认，不再回填维度。 |
| `src/opencompete/graph.py` | 委托先规划；候选选择仅更新实体。 |
| `src/opencompete/application.py` | 委托创建、纯 `inspect_run`、耐久冻结和显式执行。 |
| `src/opencompete/research_contracts.py` | 将冻结 Scope 的自然语言摘要传给上游 brief。 |
| `src/opencompete/api/{schemas,routes,tasks,errors}.py` | 新输入契约、检查投影、预留启动、恢复端点和错误区分。 |
| `frontend/src/{api/client.js,stores/runs.js,pages/NewResearchPage.vue,pages/RunDetailPage.vue}` | 委托表单、超时、202 投影、SSE 同步与恢复交互。 |
| `tests/opencompete/...` | Planner、冻结、brief、Graph、Runner 与 API fake-runtime 覆盖。 |

## Task 1：动态 Web 委托与 ScopePlanner

**涉及文件：** `models.py`、新增 `scope_planning.py`、`application.py`、`graph.py`、`api/schemas.py`、`NewResearchPage.vue`、`stores/runs.js`、相关测试。

1. 先写离线 `FakeScopePlanner` 测试：不同委托产生不同的中文 Scope，Planner 失败不回退固定目录。
2. 新增 `ResearchCommission` 与 `ScopeProposal`，保持 `ResearchRequest` 原有强契约不变。
3. 实现 `LazyScopePlanner`，复用 `init_chat_model`、`with_structured_output`、`build_resilient_model` 和现有 callbacks；校验不使用词表。
4. 新增 `ApplicationRunner.create_commission_run()`；Graph 输入支持 `commission` 或既有 `request`，但二者互斥。
5. Web `POST /runs` 改为 Commission；创建页移除默认三层和固定维度，增加可选关注点。

**完成条件：** 新 Web 路径没有 `DEFAULT_DIMENSIONS`、不接受 `levels/dimensions`，而 CLI/固定 Scope 测试仍通过。

## Task 2：确认范围与研究 brief 的同一契约

**涉及文件：** `analysis.py`、`graph.py`、`research_contracts.py`、`test_analysis.py`、`test_graph.py`、`test_prepare_research_brief.py`、Benchmark 契约测试。

1. 让 `freeze_scope()` 用 `is None` 区分未编辑与显式空集合；让 `ScopeSpec` 负责明确校验错误。
2. 候选选择只替换 `request.entities` 和 `scope.entities`，不重建 Scope。
3. User brief 加入最终对象、层级中文名、动态维度、排除项和网址；保留内部 ID/JSON 泄漏门禁。
4. `ResearchContract.exclusions` 改用 `scope.exclusions`。
5. 把当前固定目录断言限定为 legacy/固定 Benchmark 兼容，不再用它验证 Web 生产范围。

**完成条件：** 用户编辑后的 Scope、brief、Extractor 和 Gate 使用同一份动态文本；历史 slug bundle 的保底渲染不受影响。

## Task 3：纯检查、耐久冻结、显式恢复与错误语义

**涉及文件：** `models.py`、`application.py`、`api/schemas.py`、`api/routes.py`、`api/tasks.py`、`api/errors.py`、新增 `tests/opencompete/api/`。

1. 写 fake Graph 测试，证明 `inspect_run()` 仅调用 `aget_state()`，不会 `ainvoke`、`aupdate_state`、写 Manifest、事件或预算。
2. 用 Checkpoint interrupt 重写确认前置校验；缓存只作性能优化，不参与正确性。
3. 实现 `freeze_confirmed_scope()`：`aupdate_state(as_node="freeze_scope")` 成功后，再同步 Manifest 为 `scope_frozen`。
4. 为 Registry 增加最小 reserve/start handshake；拆分同 Run 活动（409）和容量耗尽（429）。
5. 接受范围先预留槽、耐久冻结、启动后台执行，再以完整详情投影返回 202。
6. 新增 `POST /runs/{id}/resume`，仅允许可恢复的非确认点中间态。

**完成条件：** 重启后 GET 可只读恢复确认载荷；202 前 Scope 已冻结；同 Run/他 Run 冲突错误不同；只有显式 POST 能继续中断研究。

## Task 4：Vue 状态机和动作超时

**涉及文件：** `frontend/src/api/client.js`、`frontend/src/stores/runs.js`、`frontend/src/pages/RunDetailPage.vue`、`NewResearchPage.vue`。

1. `request()` 支持 `AbortController`、超时和动作型错误码；读取与长创建操作用不同 deadline。
2. Store 将 202 完整执行投影原子写入详情和列表后再连接 SSE；删除立即 GET/列表覆盖。
3. SSE 同时更新 `detailByRun` 和列表行，清除已完成确认态。
4. 详情页仅在 `pending_confirmation=true` 时渲染确认表单；`recoverable=true` 显示显式恢复按钮。
5. 超时后释放 `actionBusy` 并只读对账；不自动 `resume`。

**完成条件：** 用户不再看到“已执行但仍可确认”的页面，也不会永久卡在“正在提交”。

## Task 5：离线验证与收尾

1. 运行新增/相关 Python 测试和 API fake-runtime 端到端测试。
2. 对本次触及的 Python 文件运行 Ruff，修复本次引入的问题，不批量处理既有 v3 无关 debt。
3. 运行 `npm run build`。
4. 运行全量 `pytest -q`；若已有无关失败，隔离并报告具体来源。
5. 运行 `git diff --check`、`git diff --stat upstream-b764481f`、`git diff upstream-b764481f -- src/open_deep_research`，确认本次未扩大上游改动。

## 需求覆盖

| 需求 | 覆盖任务 |
|------|----------|
| REQ-001 动态中文范围 | Task 1、Task 2 |
| REQ-002 原样冻结 | Task 2 |
| REQ-003 Scope 约束研究 | Task 2 |
| REQ-004 候选不重规划 | Task 2 |
| REQ-005 安全只读检查 | Task 3 |
| REQ-006 耐久冻结交接 | Task 3、Task 4 |
| REQ-007 显式恢复与容量错误 | Task 3、Task 4 |
| REQ-008 前端不无限锁定 | Task 4 |
