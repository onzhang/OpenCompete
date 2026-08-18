# OpenCompete 本地设置与凭据管理 - 架构设计

> 状态：已确认并实施
>
> 日期：2026-08-17
>
> 关联需求：REQ-010、REQ-015、REQ-016、REQ-018、REC-001、CONN-001；本地 Web 控制台设置工作台

## 1. 目标与非目标

本设计把模型、预算、研究执行和传输限制从手工编辑配置文件扩展为本地单用户的“设置”功能。设置页面必须能管理后续新研究任务使用的非秘密参数，并能设置、替换或清除本机 `.env` 中受控凭据。

本设计不引入多用户、远程设置服务、云端密钥托管、Agent Server、MCP Token Store 或供应商 SDK 分支。`REC-001` 要求用户显式恢复时使用当前模型与连接继续原 Checkpoint；`CONN-001` 要求主连接和备用连接分别保存兼容格式、Base URL、API Key 与模型名。为满足这两个需求，OpenCompete 增加进程内连接适配层，并对上游统一模型 Runnable 增加最小的 `base_url`、连接角色和按模型 Thinking 参数传递能力。

## 2. 已确认用户语义

- API Key 默认遮罩；只有用户主动点击显示时才返回当前角色的完整 Key，随后可再次隐藏，刷新或离页不会保留前端值。普通设置 API 不回显 Key；Base URL 仅作为公开连接配置返回。
- 连接测试由用户显式触发，主连接、备用连接和 Tavily 各只发送一次最小请求，可能产生少量费用。
- 新任务使用当前设置。已有任务的 Scope、研究对象、分析维度、预算上限、已结算用量、Checkpoint、调用账本和历史事件保持原任务契约；用户显式恢复时，只有接下来新发起的模型调用使用当前模型与连接设置。
- 任何真实 in-flight Graph 执行存在时，拒绝替换或清除凭据/Base URL；失败、暂停且可恢复的任务允许用户先更新连接，再显式恢复。
- 进程外已设置的 `OPENCOMPETE_*` 或受管凭据环境变量保持优先。页面显示“外部环境”且拒绝写入不会生效的值。
- 用户设置台只展示模型与连接、研究预算、研究深度与速度、搜索范围。每个公开字段通过圆形 `!` 在悬停或键盘焦点时说明作用范围与数值影响；revision 仍只用于 API 乐观锁，不显示给用户。
- Judge、费率与价格版本、SDK 重试、内部内容截断和编排阈值属于开发/评测 TOML 配置，不进入用户公开 API，也不能从页面写入。
- Thinking 由系统按实际模型路由自动管理：DeepSeek 经 Anthropic 兼容端点时关闭，OpenAI、Google 与 Claude 路由不接收该参数。压缩和最终报告固定采用流式聚合，作为长响应传输可靠性保护，不提供用户开关。

## 3. 数据流与边界

```text
设置页 -> FastAPI -> SettingsService -> opencompete.toml / .env
                         |                    |
                         |                    +-> 原子替换、仅本地可读
                         v
              ApplicationRunner 新任务默认设置
                         |
                         +-> 新任务 Manifest.config_snapshot（仅非秘密）
                         |
                         +-> 显式恢复：原任务契约 + 当前模型运行时
                         v
             Budget / Scope / Graph / Extractor 任务级设置
```

`SettingsService` 是唯一的设置写入入口：

- TOML 仅保存白名单中的非秘密运行参数；`data_dir`、遥测和调试控制不允许由页面改变。
- `.env` 仅允许 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`ANTHROPIC_API_KEY`、`ANTHROPIC_BASE_URL`、`GOOGLE_API_KEY` 和 `TAVILY_API_KEY`。重复或复杂受管定义会被拒绝，要求先手工整理。
- `ApplicationRunner._settings_for_run()` 仍用于只读检查和恢复对账，以取得原任务契约与预算费率；显式 `resume_run()` 另外通过 `_resume_settings_for_run()` 以白名单覆盖模型、连接格式、主备尝试和传输运行时字段。预算 Guard 与 durable ledger 始终使用原任务上限和已结算用量。
- `Scope Graph`、上游 Research Graph、候选发现、Scope Planner 和 Artifact Extractor 都通过同一进程内连接上下文读取主/备用 Key 与 Base URL；连接角色只进入非秘密路由元数据。
- 如果历史任务 Manifest 丢失或无法读取，恢复对账仍使用既有的当前默认设置降级路径；Checkpoint 恢复完成后重建索引。

秘密绝不进入 Graph State、`RunnableConfig.configurable`、Checkpoint、事件、Manifest、Bundle、报告或评测产物。Key 与 Base URL 只存在于 `.env`/环境变量和单次执行窗口的进程内连接对象；模型 SDK 与 Tavily 客户端继续不记录完整响应和请求上下文。

## 4. 并发与原子性

设置读取和写入使用单进程 `asyncio.Lock` 与 `expected_revision` 乐观锁。`POST /api/runs` 在同一锁下初始化 Manifest，因此设置保存与任务创建不存在“任务拿到半份新配置”的窗口。

TOML 通过 `tomlkit` 保留未知字段与注释；`.env` 与 TOML 都采用临时文件、`fsync` 和 `os.replace()` 原子替换。SQLite 与文件存储没有跨存储事务，任务恢复仍由既有 Manifest/Checkpoint 对账机制负责。

## 5. API 与页面契约

- `GET /api/settings`：严格公开的字段白名单、来源、分组、revision、连接格式、Base URL 与凭据状态。
- `PUT /api/settings`：revision 校验、Pydantic 总体验证、原子 TOML 写入，并更新后续新任务以及下一次显式恢复的模型运行时默认值。
- `PUT /api/settings/credentials`：单槽 `set` / `clear`，执行真实运行状态阻断、外部管理阻断与 Base URL 安全校验。
- `GET /api/settings/connections/{role}/api-key`：仅在用户主动 reveal 时返回当前角色完整 Key，响应 `Cache-Control: no-store`，服务端不记录值。
- `POST /api/settings/connections/{target}/test`：显式最小连接测试；不创建任务、不写事件、不持久化模型文本或原始 Provider 错误。

设置页按模型路由、预算与成本、研究执行、传输与搜索、连接凭据分段展示。凭据输入始终为空且使用密码输入框；清除动作通过应用内确认弹窗执行。

## 6. 安全验证

离线测试覆盖：公开视图零回显、显式 Key reveal 与 `no-store`、TOML 注释保留、revision 冲突、外部环境锁定、`.env` 设置/清除/重复键拒绝、HTTPS 公网 Base URL、真实 in-flight 任务阻断、失败可恢复任务允许换连接、API 响应脱敏、任务契约与恢复运行时隔离和历史 Manifest 损坏恢复。

持久化层继续对敏感键及环境秘密扫描。连接测试错误只使用现有 `sanitized_error()` 白名单字段，禁止返回原始响应、Header、请求体、Key 或 Base URL。

## 7. 上游适配审计

本轮用户设置精简对应 REQ-015、REQ-016，以及已确认的 `REC-001`、`CONN-001` 模型兼容需求：用户不能编辑 Thinking，DeepSeek Thinking 路由必须自动关闭；主/备用连接必须能分别提供 API Key 与 Base URL；显式恢复必须在不重置任务契约的前提下使用当前连接。

仅靠 OpenCompete 适配层无法完成的原因是：上游的统一模型工厂需要在单个 LangChain Runnable 的 `configurable_fields` 中声明 `base_url` 和 `api_key` 才能让主备路由使用不同端点；上游工具摘要路径也需要同样的连接角色；而全局 Thinking 字段无法表达“只对 DeepSeek 注入关闭参数”。因此对 `src/open_deep_research/` 采用最小适配：扩展 `configurable_fields`，在主/备用 Runnable 构造处传入角色化 `api_key`、`base_url` 和非秘密 `connection_role` 元数据，并保留可选的 `model_thinking_by_model` 映射。没有进程内连接上下文或对应角色字段时，旧环境变量和原有全局行为保持兼容。

`tests/opencompete/test_upstream_agent_control.py` 覆盖原全局行为和按模型覆盖行为。OpenCompete 侧覆盖 DeepSeek/Anthropic、OpenAI 混合路由、备用模型、Scope Planner、候选发现、Artifact Extractor、连接测试与恢复设置隔离，确保 API Key、Base URL 与其他秘密仍不进入 Graph 配置或持久化产物。

## 8. 依赖决策

新增 `tomlkit>=0.15.1`：这是活跃维护、MIT 许可的 TOML 保留格式库，用于在写入受管字段时保留用户注释和未知配置。标准库 `tomllib` 只提供读取能力，不能安全替代写入；若 Python 标准库未来提供稳定的 TOML round-trip 写入能力，或项目不再要求保留手工注释，应重新评估并移除该依赖。
