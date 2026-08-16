# OpenCompete 候选发现覆盖与来源聚合实施计划

> 关联设计：[2026-08-15-OpenCompete-候选发现覆盖与来源聚合-架构设计.md](../specs/2026-08-15-OpenCompete-候选发现覆盖与来源聚合-架构设计.md)  
> 目标：候选发现按竞争空间覆盖、公开来源和预算运行，不再由网页页大小、固定查询轮数或固定候选数截断。

## 全局约束

- 不新增固定竞品名单、固定候选数、固定查询数、固定分类目录或固定研究维度。
- `AnalysisLevel` 和动态中文 Scope 的现有契约不变；候选发现不能改写 Scope。
- 保留 Tavily 官方客户端、公开 URL 校验、BudgetGuard、模型路由和显式用户确认。
- 不修改 `src/open_deep_research/`，不引入爬虫、分页服务、队列、缓存或额外搜索引擎。
- 供应商页大小、模型输入字符数、并发量、超时和预算只能表达技术/成本边界，不能表达候选产品数量上限。

## 文件地图

| 文件 | 职责 |
|------|------|
| `src/opencompete/providers.py` | 动态检索前沿、覆盖审计追加词、来源聚合与确定性去重。 |
| `src/opencompete/settings.py` | 将发现页大小与上游 Researcher 页大小解耦。 |
| `src/opencompete/application.py` | 将独立发现页大小注入 Scope Graph。 |
| `opencompete.toml` | 为本地真实发现配置供应商允许的完整单页大小。 |
| `opencompete.example.toml` | 说明两个页大小字段的不同语义。 |
| `README.md` | 用中文说明停止条件与技术边界。 |
| `tests/opencompete/test_providers.py` | 离线候选发现行为回归。 |
| `tests/opencompete/test_settings.py`、`tests/opencompete/test_application.py` | 独立配置传递回归。 |

## Task 1：把检索计划变成可扩展前沿

**涉及文件：** `src/opencompete/providers.py`、`tests/opencompete/test_providers.py`

**Consumes：** `_IntentDraft.search_queries`、`_CoverageAssessment`、`_guarded_tavily_search()`。  
**Produces：** 只在未覆盖时可追加的、去重检索前沿。

- [x] 先增加离线失败测试：覆盖审计返回新的 `follow_up_queries` 后，Provider 执行该新词并聚合候选。
- [x] 增加重复追加词测试，证明已执行或待执行词不会再次发出 Tavily 请求。
- [x] 在 `_CoverageAssessment` 增加可选 `follow_up_queries`，提示词明确禁止固定数量凑词。
- [x] 将 `for` 循环替换为本地 `deque` 前沿：初始词和覆盖审计追加词统一规范化去重；预算、前沿耗尽和饱和是唯一停止机制。
- [x] 保留“候选为空不能因饱和提前停止”的现有语义。
- [x] 空结果或仅重复 URL 的检索轮次仍执行覆盖审计；只有跳过来源绑定，不能跳过替代检索词的生成。

**完成条件：** REQ-002、REQ-003 的 Provider 测试通过，且没有按候选数或查询数的 `break`。

## Task 2：解耦候选发现页大小与深度研究配置

**涉及文件：** `src/opencompete/settings.py`、`src/opencompete/application.py`、`opencompete.toml`、`opencompete.example.toml`、相关测试。

**Consumes：** Tavily SDK 单次 `max_results` 参数。  
**Produces：** `discovery_search_results_per_query`，默认每轮读取最相关的 5 个来源；继续检索由覆盖缺口决定，不由页大小决定。

- [x] 增加 Settings 回归测试：新字段默认/边界校验有效，`tavily_max_results_per_query` 仍只进入上游 RunnableConfig。
- [x] 应用图工厂只向发现节点传递新字段，保持上游配置值不变。
- [x] 本地 TOML 和示例配置用中文注释明确两个字段的语义。
- [x] 不修改 `DiscoveryCandidate`、用户选择 API 或前端候选数量逻辑。

**完成条件：** REQ-001、REQ-005 通过，发现提高网页页大小不会扩散到上游 Researcher。

## Task 3：保留来源完整性与预算失败语义

**涉及文件：** `src/opencompete/providers.py`、`tests/opencompete/test_providers.py`

- [x] 验证长集合页被 `discovery_binding_max_input_chars` 分片后，每个字符仍至少进入一个绑定批次。
- [x] 验证后续 Search 或绑定批次预算不足时，已有 `DiscoveryCandidate` 不会被清空。
- [x] 验证所有候选 URL 仍须来自本轮实际来源，已有对象/重复产品仍由 `_harden_candidates()` 剔除。

**完成条件：** REQ-004 通过；不增加新的持久化或网络依赖。

## Task 4：验证、真实候选确认点和收尾

- [x] 运行 Ruff、mypy 和候选发现/Settings/Application 相关 pytest。
- [x] 运行 `npm run build`，确认动态候选分组页面仍可构建。
- [x] 使用真实模型与 Tavily 创建一个新的“调研 Trae AI IDE 的竞品与适用场景”任务，仅运行到候选确认 interrupt；不接受 Scope、不启动 Supervisor/Researcher。
- [x] 记录候选数量、合理候选例子、去重来源数、实际 Tavily 调用数和预算快照；不得记录 Key、完整网页正文或模型提示。
- [x] 运行 `git diff --check`、相对上游统计和 `src/open_deep_research/` 差异检查。

**完成条件：** 所有完成门禁通过；需要重启的本地 API 进程由其所有者重启后，前端才能使用新 Python 代码。

## 需求覆盖

| 需求 | 覆盖任务 |
|------|----------|
| REQ-001 | Task 2、Task 3 |
| REQ-002 | Task 1 |
| REQ-003 | Task 1 |
| REQ-004 | Task 3 |
| REQ-005 | Task 2 |
