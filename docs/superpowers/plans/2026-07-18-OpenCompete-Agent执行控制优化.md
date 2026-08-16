# OpenCompete Agent 执行控制优化 - 实现计划

> 日期：2026-07-18  
> 对应规格：`docs/superpowers/specs/2026-07-18-OpenCompete-Agent执行控制优化-架构设计.md`

## 1. 迭代边界

只实现规格中的方案 A：分离研究工具、反思和模型轮次，增加稳定终止原因、并行额度截断、旧状态兼容和配置身份测试。Coverage Ledger、`Send`、Gap Verifier、定向 URL 工具和领域包全部延后。

## 2. 实施步骤

### Task 1：先写失败特征测试

- 修改：新增 `tests/opencompete/test_upstream_agent_control.py`
- 覆盖：
  - 两次 `search -> think` 后仍可继续第三次搜索；
  - `think_tool` 不增加研究工具计数；
  - 并行研究工具按剩余额度执行；
  - 四种终止原因；
  - 旧 `tool_call_iterations` 状态回退。
- 验证：`uv run pytest -q tests/opencompete/test_upstream_agent_control.py`

### Task 2：扩展通用状态和配置

- 修改：`src/open_deep_research/state.py`
- Produces：`ResearchTerminationReason`、新计数和输出字段。
- 修改：`src/open_deep_research/configuration.py`
- Produces：`max_research_tool_calls`。
- 修改：`src/opencompete/settings.py`
- Produces：正式 Settings 字段、RunnableConfig 透传和额度关系校验。
- 修改：`opencompete.toml`、`opencompete.example.toml`
- 验证：`uv run pytest -q tests/opencompete/test_settings.py`

### Task 3：实现现有 Researcher 内的额度和终止路由

- 修改：`src/open_deep_research/deep_researcher.py`
- Consumes：现有 `researcher_messages`、工具名称和通用配置。
- Produces：实际执行计数、稳定终止原因和 `Command` 路由。
- 约束：
  - 不增加 Graph/Agent；
  - 不改 ModelRuntime 重试；
  - 不吞掉 `CancelledError`；
  - 发送前全局预算拒绝不计入研究工具调用，Supervisor 停止新增研究并保留成功兄弟；
  - `budget_exhausted` 后压缩和最终报告使用确定性部分结果，不再调用模型；
  - 不记录异常正文；
  - 不包含 Benchmark 事实。
- 验证：Task 1 测试全部通过。

### Task 4：固定配置身份和历史结论

- 修改：`tests/opencompete/test_application.py` 或独立配置测试。
- 证明：Manifest 与 RunnableConfig 来自同一 Settings，Agent 额度一致。
- 修改：`docs/用户测试/2026-07-17-OpenCompete-Bench-v2.2-闭环测试报告.md`
- 更正：O02 标为实验身份无效，删除预算放大因果结论。

### Task 5：回归与独立复审

- 运行：
  - `uv run pytest -q tests/opencompete/test_upstream_agent_control.py`
  - `uv run pytest -q tests/opencompete/test_upstream_failure_isolation.py tests/opencompete/test_upstream_callback_propagation.py`
  - `uv run ruff check .`
  - `uv run mypy src/opencompete`
  - `uv run pytest -q`
  - `git diff --check`
- 审计：`git diff upstream-b764481f -- src/open_deep_research`
- 复审：独立 Reviewer 检查正确性、恢复语义、过度设计和硬编码。

完成结果：独立架构复审最终为 GO，无剩余 P0/P1/P2；完整离线套件在不读取项目 `.env` 的约束下通过 311 项，另有 5 项按 live/秘密读取边界排除。

## 3. 下一轮进入条件

只有完成方案 A 的离线门禁后，才允许单独运行真实固定 Scope Pilot。若 Pilot 仍满足以下任一条件，再设计方案 B：

- critical coverage 小于 80%；
- 关键缺口已被反思识别但仍未形成定向任务；
- `ResearchComplete` 时仍有关键 Scope Cell 未闭合；
- Unknown 无法区分“检索后未知”和“未执行”。
