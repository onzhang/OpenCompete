# OpenCompete 本地设置与凭据管理 - 架构设计

> 状态：已确认并实施
>
> 日期：2026-08-17
>
> 关联需求：REQ-010、REQ-015、REQ-016、REQ-018；本地 Web 控制台设置工作台

## 1. 目标与非目标

本设计把模型、预算、研究执行和传输限制从手工编辑配置文件扩展为本地单用户的“设置”功能。设置页面必须能管理后续新研究任务使用的非秘密参数，并能设置、替换或清除本机 `.env` 中受控凭据。

本设计不引入多用户、远程设置服务、云端密钥托管、Agent Server、MCP Token Store 或供应商 SDK 分支。`src/open_deep_research/` 不做任何修改：正式研究仍通过上游统一模型初始化与环境变量取凭据。

## 2. 已确认用户语义

- 凭据仅由页面写入本地 `.env`，页面、API 响应、异常与业务产物不回显真实值、掩码片段或 Base URL。
- 连接测试由用户显式触发，模型和 Tavily 测试各只发送一次最小请求，可能产生少量费用。
- 非秘密设置仅影响新任务。任务创建时把完整非秘密设置、设置 revision 与快照 schema 写入 Manifest；已有任务恢复仍使用自身快照。
- 任何 `draft`、待确认、范围冻结或执行中任务存在时，拒绝替换或清除凭据/Base URL。用户需先完成或停止旧任务，以避免凭据轮换破坏恢复。
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
                         v
              Manifest.config_snapshot（仅非秘密）
                         |
                         v
             Budget / Scope / Graph / Extractor 任务级设置
```

`SettingsService` 是唯一的设置写入入口：

- TOML 仅保存白名单中的非秘密运行参数；`data_dir`、遥测和调试控制不允许由页面改变。
- `.env` 仅允许 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`ANTHROPIC_API_KEY`、`ANTHROPIC_BASE_URL`、`GOOGLE_API_KEY` 和 `TAVILY_API_KEY`。重复或复杂受管定义会被拒绝，要求先手工整理。
- `ApplicationRunner._settings_for_run()` 从 Manifest 恢复快照，并据此创建预算 Guard、RunnableConfig、候选发现、Scope Planner 和 Artifact Extractor。测试替身仍优先。
- 如果历史任务 Manifest 丢失或无法读取，恢复对账仍使用既有的当前默认设置降级路径；Checkpoint 恢复完成后重建索引。

秘密绝不进入 Graph State、`RunnableConfig.configurable`、Checkpoint、事件、Manifest、Bundle、报告或评测产物。模型 SDK 与 Tavily 客户端继续仅从进程环境获取凭据。

## 4. 并发与原子性

设置读取和写入使用单进程 `asyncio.Lock` 与 `expected_revision` 乐观锁。`POST /api/runs` 在同一锁下初始化 Manifest，因此设置保存与任务创建不存在“任务拿到半份新配置”的窗口。

TOML 通过 `tomlkit` 保留未知字段与注释；`.env` 与 TOML 都采用临时文件、`fsync` 和 `os.replace()` 原子替换。SQLite 与文件存储没有跨存储事务，任务恢复仍由既有 Manifest/Checkpoint 对账机制负责。

## 5. API 与页面契约

- `GET /api/settings`：严格公开的字段白名单、来源、分组、revision 与凭据状态。
- `PUT /api/settings`：revision 校验、Pydantic 总体验证、原子 TOML 写入，并只重配后续新任务默认值。
- `PUT /api/settings/credentials`：单槽 `set` / `clear`，执行运行状态阻断、外部管理阻断与 Base URL 安全校验。
- `POST /api/settings/connections/{target}/test`：显式最小连接测试；不创建任务、不写事件、不持久化模型文本或原始 Provider 错误。

设置页按模型路由、预算与成本、研究执行、传输与搜索、连接凭据分段展示。凭据输入始终为空且使用密码输入框；清除动作通过应用内确认弹窗执行。

## 6. 安全验证

离线测试覆盖：公开视图零回显、TOML 注释保留、revision 冲突、外部环境锁定、`.env` 设置/清除/重复键拒绝、HTTPS 公网 Base URL、非终态任务阻断、API 响应脱敏、任务快照隔离和历史 Manifest 损坏恢复。

持久化层继续对敏感键及环境秘密扫描。连接测试错误只使用现有 `sanitized_error()` 白名单字段，禁止返回原始响应、Header、请求体、Key 或 Base URL。

## 7. 上游适配审计

本轮用户设置精简对应 REQ-015、REQ-016 以及已确认的模型兼容需求：用户不能编辑 Thinking，DeepSeek Thinking 路由必须自动关闭，而 OpenAI 兼容路由不得收到未知的 `thinking` 参数。

正式应用原本通过 `OpenCompeteSettings.to_runnable_config()` 这一薄适配器向上游传递配置，但上游 `Configuration` 只支持一个全局 `model_thinking`。当主模型是 DeepSeek、备用模型是 OpenAI 时，薄适配器无法表达“只对 DeepSeek 注入关闭参数”，上游会将全局值复用给备用模型，导致 OpenAI 兼容端点拒绝请求。因此最小修改上游 `Configuration`、`_resilient_model()` 和 Tavily 摘要模型构造，新增可选的 `model_thinking_by_model` 映射；未提供该映射时完全保留原来的全局行为。

`tests/opencompete/test_upstream_agent_control.py` 覆盖了原全局行为和按模型覆盖行为。OpenCompete 侧还覆盖 DeepSeek/Anthropic、OpenAI 混合路由、备用模型和连接测试，确保 API Key、Base URL 与其他秘密仍不进入 Graph 配置或持久化产物。

## 8. 依赖决策

新增 `tomlkit>=0.15.1`：这是活跃维护、MIT 许可的 TOML 保留格式库，用于在写入受管字段时保留用户注释和未知配置。标准库 `tomllib` 只提供读取能力，不能安全替代写入；若 Python 标准库未来提供稳定的 TOML round-trip 写入能力，或项目不再要求保留手工注释，应重新评估并移除该依赖。
