# OpenCompete 全项目审计与 Agent 方案调研

> 日期：2026-10-01（Asia/Shanghai）\
> 源码基线：`227d354d3f24ebdc2d72e73c3fb43eaaf22f9145`，本地 `main`，审计开始时工作区干净。\
> 目的：为“突出 AI Agent 工程能力与求职展示”确定下一阶段方向。\
> 性质：源码审计、离线实验与公开资料调研；不是已实施修复，也不是本轮真实模型评测。\
> 配套：[架构设计](../superpowers/specs/2026-10-01-OpenCompete可信研究Agent演进-架构设计.md)、[分阶段开发计划](../superpowers/plans/2026-10-01-OpenCompete可信研究Agent-分阶段开发计划.md)。

## 1. 结论与推荐方向

建议把项目定位为“可恢复、可追溯、可验证的竞品研究 Agent”，形成三条能在面试中现场证明的能力链：

1. 多 Agent 研究执行 → 单调用重试 → 预算预留 → 进程退出后恢复 → 正确保留部分结果。
2. 公开来源 → 可信证据 → 结论与比较 → 完整报告 → 基于同一证据的报告追问。
3. 故障注入与缺陷样本 → 严格评测契约 → 固定实验输入 → 独立展示质量、事实、完成率、成本。

先修评测、报告、恢复和事件投影中的已复现问题，再交付“执行检查器 + 可信成绩页”，随后增加“证据限定的报告追问”。带血缘的补研、变化比较和领域分析包作为后续独立切片。近期不建设多租户平台、任意工具市场或另一套通用 Agent 框架。

一个新的外部事实需要纳入技术路线：Open Deep Research 的 GitHub 页面显示仓库于 **2026-08-21 归档**。这不意味着当前实现立即不可用，但“持续获得上游修复”不再是可以依赖的假设。保留固定基线和现有语义，同时安排限时的 Deep Agents 对比实验；实验通过之前不迁移主链路。

## 2. 调查范围、方法与限制

本轮覆盖正式业务模块、上游改动、前端、API/CLI、配置与凭据、持久化、评测、基准数据、离线测试、历史运行元信息、开发流程，以及 Legacy/托管兼容代码的职责。先使用 CodeGraph，再核查源码与测试。CodeGraph 索引包含其他仓库，结论仅使用本项目路径。

“全项目”指目录、职责与关键路径全覆盖，不表示逐字审阅所有锁文件、全部历史报告和每个测试断言。未直接打开或输出 `.env` 内容，未输出实际凭据；默认测试和应用导入可能通过配置加载器读取环境。本地数据仅读取受控元信息及必要的评测字段。没有执行浏览器攻击、真实模型、Tavily 或新一轮付费 Judge。

### 2.1 规模快照

以下是工作区中指定扩展名的文本文件统计，不是生产代码净行数，不用于衡量复杂度或个人贡献。统计包含空行和注释；扩展名为 `.py/.vue/.js/.css/.md/.json`，不含锁文件与 notebook。

| 区域 | 文件数 | 行数 | 本轮关注点 |
|---|---:|---:|---|
| `src/opencompete` | 53 | 20,342 | 应用、证据、预算、模型、持久化、评测、API |
| `src/open_deep_research` | 6 | 4,190 | Supervisor/Researcher 与上游差异 |
| `src/legacy` | 13 | 4,368 | 历史对照，不纳入正式新功能主链 |
| `src/security` | 1 | 156 | Supabase/Agent Server 兼容认证，不是本地 API 的保护层 |
| `frontend/src` | 22 | 7,817 | Vue 页面、Pinia 状态、报告和配置 |
| `tests` | 63 | 19,107 | 默认离线、跨进程、恢复、预算、安全、评测 |
| `docs` | 24 | 5,118 | 审计开始时的规格、计划、历史复盘 |
| `benchmarks` | 117 | 6,788 | v1/v2/v3 数据与诊断脚本 |

相对固定上游 `upstream-b764481f`，整个仓库为 292 文件、+63,131/-1,084 行；其中上游核心六文件为 **+2,122/-292 行**。不能再把当前核心改动描述成“只有两处小补丁”。

### 2.2 实际环境

本轮环境读取到：LangGraph 1.2.9、LangChain 1.3.9、langchain-core 1.4.8、langgraph-checkpoint 4.1.1、SQLite Saver 3.1.0、FastAPI 0.141.1、sse-starlette 2.1.3、Pydantic 2.11.5、PyMuPDF 1.26.0。正式复现须同时记录 `uv.lock` 哈希并使用 frozen 安装；这里只报告本机已安装版本。

### 2.3 现有能力地图

| 能力 | 当前状态与主要证据 | 下一步意义 |
|---|---|---|
| 动态研究范围和候选发现 | `graph.py:113/198/268`、`scope_planning.py`、`providers.py`；包含覆盖审计与追加检索词 | 不再另建“候选发现 Agent” |
| 人工确认和冻结 | durable `interrupt`、候选选择、范围 revision 冲突保护 | 在其上明确补研确认边界 |
| 多 Agent 研究 | `graph.py:373` 嵌入真正的上游编译子图；Supervisor 分派 Researcher | 保持可恢复图拓扑 |
| 重试与主备切换 | `model_routing.py:43`，prepared Runnable 上使用官方 retry/fallback | 统一装配，避免重试整图 |
| 成本与恢复 | SQLite Saver、持久 ledger、`call_id/attempt_id/execution_id`、进程级互斥 | 补组合场景与可视化证据 |
| 证据链与报告 | 可信 `ToolMessage.artifact`、Source/Evidence/Claim/Comparison、完整叙事和失败保底 | 修复误判与来源时间语义 |
| Web 工作台 | 新建、确认、进度、重试、显式恢复、取消、历史删除、报告引用、矩阵、Markdown 导出 | 优化契约和回放，不再从零开发 Web |
| 设置和连接管理 | 主备、预算、凭据设置/清除/显式查看、连接测试、配置快照 | 保留已确认交互并补本地服务防护 |
| 评测 | 多代本地 Judge、FACT/硬门禁、官方 DRB 外部工具路径 | 优先修复 v3 缺省通过与溯源 |
| 领域方法论 | 通用 Analysis Packs 和既有 Skill 取舍记录 | 可按需增加声明式领域包 |

源码入口均相对仓库根目录；完整路径前缀分别为 `src/opencompete/`、`src/open_deep_research/` 和 `frontend/src/`。

## 3. 已复现问题与源码支持的改进点

“复现”表示本轮执行了离线最小实验；“源码确认”表示读取到明确实现，但未完成真实外部场景验证；“设计分歧”表示需要先统一产品语义，不能直接按普通 bug 删除旧行为。

| 编号 | 优先级/证据 | 问题与影响 | 精确定位（基线行号） |
|---|---|---|---|
| F01 | P0，复现 | 空 RACE `dimensions` 被补成 target/reference 各 5 分，得到 Overall 0.5；无效 Judge 输出成为有效成绩 | `src/opencompete/evaluation/competitive_v3_judge.py:261` |
| F02 | P0，复现+源码 | FACT 缺失关系默认 supported；payload 没有用于核验的 Evidence 摘录/Source；Unknown=1、矛盾数=0 写死 | `competitive_v3_judge.py:110/357`、`competitive_v3_service.py:347` |
| F03 | P0，源码确认 | 官方 Prompt 哈希为占位锚点；v3 结果缺少完整 Judge/Prompt/代码身份；不能支持真实溯源主张 | `competitive_v3_race.py:35`、`competitive_v3_models.py:259` |
| F04 | P0，解析复现 | Marked 保留 HTML 事件属性，报告随后进入 `v-html`，构成 HTML 注入面；尚未执行浏览器攻击 | `frontend/src/pages/ReportPage.vue:69/75/244` |
| F05 | P0，源码确认 | 本地 API 无明确 Host/Origin 防护，允许配置非回环监听；同源存在显式凭据查看接口，放大 HTML 注入影响 | `api/server.py:72/95`、`api/routes.py:245` |
| F06 | P0，复现 | 合法报告离线重物化 revision=2 后，再 resume 被判“业务产物超前于 Checkpoint”，无法正常处理 | `application.py:565`、`persistence/reconciliation.py:51` |
| F07 | P0，复现 | 普通单词 `capabilities` 触发 internal_schema_leak，成功叙事进入硬失败替换路径 | `report_publication_gate.py:23`、`materialize.py:578` |
| F08 | P1，复现 | 相同事件重放使 spent 翻倍；并行 attempt 共用 hold key 导致结束后仍残留预留；输出预留硬编码 2400 | `frontend/src/stores/runs.js:49/268/381` |
| F09 | P1，设计分歧 | 核心允许 awaiting checkpoint 覆盖 cancelled manifest；Web 又把取消视为终态 | `application.py:390/1426`、`test_application.py:887` |
| F10 | P1，源码确认 | 新任务先初始化后申请容量，容量失败时可能遗留未返回用户的任务；创建请求无结果去重契约 | `api/routes.py:364/367`、`api/server.py:63` |
| F11 | P1，源码确认 | 物化时间被写成 Source.accessed_at/Evidence.observed_at，离线重物化可看似重新抓取 | `materialize.py:208/248/298` |
| F12 | P1，源码确认 | 评测成本恒为零且 token 使用实例累计值，批次后续项可能包含前项消耗；长报告裁剪未披露 | `competitive_v3_judge.py:59/75/110`、`competitive_v3_service.py:94/214` |

### 3.1 评测问题的最小反例

- 向 `_parse_race_raw()` 提交 `{"dimensions":[]}`：四维均为 0.5、Overall=0.5。正确处理应是 `invalid_output`，没有有效分数。
- FACT 输入仅包含 supported 的 Reference 评判，Target Claim 和关系评判全空：当前仍可得到 eligible=true、coverage=1、unknown=1、contradiction=0。正确处理应拒绝不完整评判。
- 现有 v2 已有完整 ID 集合校验与真实聚合逻辑，见 `competitive_judge.py:784`、`competitive_service.py:328`。优先提取可复用的校验函数，不复制出第四套 Judge。

### 3.2 报告、恢复与前端最小反例

- 使用已有测试图及真正的 SQLite Saver：完成 → `rematerialize_report()` → 新 Runner `resume_run()`，抛 `PersistenceError`；研究调用仍为一次。
- 报告“建议优先考虑支持 capabilities API 的工具。来源 https://example.com”被硬门禁误判。
- Marked 对带 `onerror` 的 `img` 返回原样 HTML；此实验只检查字符串，没有执行脚本。
- Pinia 同一完成事件处理两次：spent 从 1 增至 2；两个同节点调用完成后 holds 为空而 reserved 仍有值。

### 3.3 需要分清的既有需求

2026-08-17 设置规格明确允许用户点击后查看完整 Key。这是既有需求，不应把接口的存在本身说成未授权功能。方案应保留显式查看的语义、`no-store` 与秘密清理，并加固同源、Host 和渲染边界；若以后要取消查看能力，应另做产品决策。

取消与暂停同样不能互换。现有测试锁定了某些恢复行为，先批准状态转换契约，再迁移旧状态；不能通过增加几个路由判断掩盖底层分歧。

## 4. 其他维度的开发机会

| 方面 | 现状 | 建议 |
|---|---|---|
| 模块边界 | `application.py` 1,757 行；路由装配分布在研究、发现、规划、提取器 | 按具体用例抽取执行生命周期/发布/只读查询；维持 Runner 门面，不按行数机械拆包 |
| 上游维护 | 六文件补丁较多且上游归档 | 建逐补丁 REQ、适配不足原因、特征测试台账；框架升级与功能迭代分开 |
| 前后端契约 | 响应中存在宽泛 dict，手写 JS 客户端 | 先收紧 Pydantic DTO，生成 OpenAPI 类型；关键 store 渐进类型化 |
| Web 与 CLI | Web 默认 Commission 动态规划，CLI new 仍固定 ResearchRequest/levels | 默认委托入口统一，固定 scope 留作显式兼容/benchmark 模式 |
| 成绩页 | API 的分数字段固定 None；页面有均分占位 | 只读加载已保存评测，未运行与零分区分，禁止 GET 发起 Judge |
| 可观测性 | 事件已有调用身份；前端保留最近 200 条，单个 currentModel 表达并行 | 使用调用/尝试树，完整历史按需回放；不显示隐藏思维链或原始 Prompt |
| 无障碍 | 引用靠全 HTML 正则增强，Enter/Space 支持不足；模态框焦点需补齐 | 安全 token/节点层引用转换，键盘闭环，评估原生 dialog |
| 交付 | Markdown 导出已有 | 优先脱敏证据包；PDF/Word 排在真实格式需求之后 |
| 性能 | 未建立固定 p50/p95 基线 | 先按阶段/调用测耗时与 token，再优化；不先加 Redis/向量库 |
| CI | 前端只有 dev/build/preview；GitHub 仅两个 Claude 工作流 | 补无秘密的质量流水线、关键前端回归、锁定环境复现 |
| 依赖 | 主依赖包含托管/Legacy/多供应商兼容包；Dependabot YAML 重复 updates 键 | 先盘点 import/许可证/安装成本；修配置；兼容能力验证后才拆 extras |
| 文档 | README 的“冻结前无外部调用”“Key 永不回显”、v1 介绍与新功能不完全一致 | 描述规划/发现的真实成本、普通/显式凭据响应差异、v3 限制 |
| 分发 | 前端开发依赖 Vite 代理，尚缺他人可验证的完整离线演示流程 | 做锁定依赖安装、合成离线演示、显式 live 模式和诊断命令 |

`src/security/auth.py` 是上游托管路径的 Supabase 认证；其存在不能证明本地 FastAPI 已鉴权。Legacy 与官方兼容源码不做顺带清理。PyMuPDF 等依赖的许可证需单独盘点；本轮未完成整个依赖树的许可证或漏洞审计。

## 5. 本轮验证与历史证据边界

### 5.1 默认离线质量检查

| 命令 | 本轮结果 |
|---|---|
| `uv run ruff check .` | 全部通过 |
| `uv run mypy src/opencompete` | 53 个源文件无问题 |
| `uv run pytest -q` | 457 passed，1 deselected，28 warnings，79.61 秒 |

子审计另跑七组后端针对性测试：58 passed；这是上述测试的子集，不能相加成 515 个不同测试。警告主要涉及 Pydantic、LangGraph 和 TestClient 弃用。没有新增业务代码，没有运行 live。默认测试通过并不覆盖 F01–F12，下一步必须把反例变为回归测试。

### 5.2 锁定安装版本的 LangGraph 小实验

使用本机 LangGraph 1.2.9 与 AsyncSqliteSaver 3.1.0、内存 SQLite，无真实外部请求：

| 实验 | 观察 | 决策含义及限制 |
|---|---|---|
| 同一 superstep 的两个兄弟节点，一个成功、一个第一次失败，随后 `ainvoke(None)` | 成功节点执行 1 次，失败节点执行 2 次，两个结果均保留 | 使用官方 pending writes；无需自己重跑全部兄弟。此实验未模拟进程强杀；项目跨进程测试另有覆盖 |
| 节点内计数后 `interrupt()`，随后 `Command(resume=True)` | interrupt 前代码执行 2 次 | 人工确认前的副作用必须幂等或置于独立 durable 节点；不能承诺任意 Python 调用 exactly-once |

### 5.3 当前本地文件盘点

| 范围 | 白名单统计 |
|---|---|
| `.opencompete-data/runs` | 5 个 Manifest：3 partial、1 failed、1 cancelled；0 评测 |
| `.opencompete-bench-v2-data/runs` | 8 个 Run：6 completed、2 partial；16 份历史评测 |
| 上述 16 份评测 | 全部 eligible_for_comparison=false；只有 3 份 quality/fact 同时 completed |
| Bench v1 / v2 / v3 | 8 个 draft / 8 个 AI 双审 provisional / 1 个 AI 双审案例 |

这些文件不是固定实验队列，不据此计算系统完成率。v3 同案 Reference Claim 从 v2 的 26 条缩为 5 条，也不能跨数据版本比较 FACT。

2026-07-18 历史文档记载的 run `125b644a-6766-4cac-9e6b-1105c8ed419c` 在本轮检查的两个目录内缺失。其 RACE 0.558、FACT 0.90 只能记作“历史文档主张，本轮未复核原始产物”；结合 F01–F03，不作为下一阶段质量提升基线。

## 6. AI Agent 市场能力与成熟框架调研

### 6.1 调研口径

2026-10-01 实际访问项目官方 README、GitHub 元数据/页面及官方文档。以下是有代表性的开源产品能力样本，不是市场占有率调查。README 的功能主张未全部安装验证，不能推导在 OpenCompete 锁定依赖中即插即用。GitHub API 后续触发公共限流，改用官方文档和普通 GitHub 页面补充；没有声称对候选做了完整安全审计。

### 6.2 框架与产品比较

| 项目及来源 | 官方材料可确认的能力 | 维护/许可证快照 | 对本项目的取舍 |
|---|---|---|---|
| [Open Deep Research](https://github.com/langchain-ai/open_deep_research) | Supervisor/Researcher、多模型、搜索、报告、DRB 路径 | MIT；页面显示 2026-08-21 归档；快照 `1b7d2e80db9faa586165c60e09096dbbfd483a64` | 当前内核继续使用，建立维护责任和替代评估门禁 |
| [Deep Agents](https://github.com/langchain-ai/deepagents) | 子 Agent、上下文管理、持久记忆、HITL、Skills、可插拔文件/沙箱/MCP | MIT；当日元数据仍有更新；快照 `839ccee06c98a04b4b90ac69af32513b920091e5` | 同 LangChain/LangGraph 生态，优先作为隔离对照；默认文件/执行能力不符合现有安全边界 |
| [DeerFlow](https://github.com/bytedance/deer-flow) | 通用任务、子 Agent、Skills、记忆、沙箱与文件交付 | MIT；当日仍更新；快照 `7c52d5a187c93c8c851a89036b8c28c017283a01` | 学习任务工作台和产物交付；整包引入会扩大部署与执行面 |
| [GPT Researcher](https://github.com/assafelovic/gpt-researcher) | Web/本地文档研究、来源报告、记忆与上下文、报告交付 | Apache-2.0；最近推送 2026-09-26；快照 `0957c301ed06c2a5857b834358c7227c739041d4` | 用作同类研究功能对照；不替代现有证据/预算/恢复契约 |
| [Dify](https://github.com/langgenius/dify) | Workflow、RAG、Agent、工具/Marketplace、运维界面 | 当日仍更新；Dify Open Source License；快照 `f004f6b67d6408e11aeb5a2cc7a14d6c9ea628c3` | 借鉴产品闭环，不嵌入整个平台；[许可证](https://github.com/langgenius/dify/blob/main/LICENSE) 含多租户与标识附加条件，不能按纯 Apache-2.0 处理 |
| [CrewAI](https://github.com/crewAIInc/crewAI) | Crews、Flows、领域知识/Skills；另有控制面观测产品 | 核心 MIT；当日仍更新；快照 `fbcf2de39d3f8252500b74a0751b92c5774c751b` | 编排职责重复；托管控制面不等于本地核心免费具备全部能力 |
| [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) | 图工作流、并行/交接、Checkpoint、HITL、OpenTelemetry、DevUI、Skills | MIT；当日仍更新；快照 `f1fb145c4d80191aa86b3480d12112d828233763` | 作为架构对照；当前切栈迁移成本高，不解决已复现的领域契约问题 |

维护判断仅是快照，不保证未来支持。Stars 数量未用于选型打分。以上整套框架均不直接成为本轮新增依赖；精确依赖兼容、漏洞和完整传递许可证在进入实验前验证。

### 6.3 常见能力的适用性与排序

| 能力 | OpenCompete 现状 | 机会与边界 | 建议 |
|---|---|---|---|
| Planning、多 Agent、工具调用 | 已有 | 把真实分派和停止原因展示清楚 | 优先增强 |
| HITL、断点恢复、重试 | 已有 | 统一生命周期，故障可复现 | 优先增强 |
| Trace、成本、评测闭环 | 部分已有，存在正确性缺口 | 准确事件投影与可比较实验 | 第一阶段 |
| 有引用的报告追问 | 缺少 | 固定某个 Bundle 版本，独立问题只读查询，拒答缺证；多轮上下文另行评估 | 首个新 Agent 功能 |
| 受控补研、批判/反思 | 有研究循环与发现覆盖审计 | 面向显式缺口补研，控制轮次与成本；不是重造现有循环 | 追问完成后 |
| 文件 RAG、混合检索 | 正式范围未开放本地文档 | 先做已有 Evidence 检索；文件上传属于新的信任边界 | 延后 |
| Skills / 领域包 | 通用 Analysis Packs 已有 | 借鉴渐进加载，声明式维度/方法，版本和许可证明确 | 可选小切片 |
| 长期记忆 | Checkpoint 有线程状态；无全局业务记忆 | 先存用户显式偏好和委托模板；事实不跨 run 静默复用 | 延后 |
| 手动刷新、变化比较 | 缺少 | 新 run 留父版本和来源时间；变化独立成产物 | 第二阶段 |
| 定时监控/通知 | 缺少、当前非目标 | 先验证手动刷新价值；调度不解决研究质量 | 延后 |
| MCP、工具市场 | 上游兼容存在，正式禁用 | 明确只读工具需求、白名单、权限和预算后再设计 | 延后 |
| 浏览器执行、代码沙箱 | 当前禁止 | 会引入执行、文件和出网隔离的全新边界 | 暂不做 |
| 多用户协作、RBAC、计费 | 当前单用户 | 无已确认需求，不能为求职展示默认引入 | 暂不做 |

## 7. 重要技术选型与来源

| 候选 | 需求匹配与采用方式 | 兼容/许可证/成本/退出与验证 |
|---|---|---|
| [LangGraph Checkpointer 与 Store](https://docs.langchain.com/oss/python/langgraph/persistence)、[interrupt](https://docs.langchain.com/oss/python/langgraph/interrupts) | 继续执行恢复与人工确认；Store 只在跨线程记忆需求成立后使用 | 当前锁定版本已做最小实验；官方库 MIT；不替代业务发布事务；保留标准 Bundle 降低退出成本 |
| [LangChain Retrieval](https://docs.langchain.com/oss/python/langchain/retrieval) | 对比两阶段 RAG、Agentic RAG；第一版先确定性检索 Evidence 再回答 | 当前已依赖 LangChain；无需新增框架；复杂自主工具循环须证明优于简单检索 |
| [Deep Agents Skills](https://docs.langchain.com/oss/python/deepagents/skills)、[Agent Skills 规范](https://agentskills.io/specification) | 借鉴元信息与按需加载；不把脚本执行能力整包开放 | 规范兼容不等于允许执行；先评估现有 Analysis Pack 能否满足，必要时仅适配静态说明 |
| [SQLite FTS5](https://www.sqlite.org/fts5.html) | Evidence 规模确实需要索引时提供本地全文检索/BM25 | SQLite 公有领域；无服务运维；中文切词、trigram 短查询和实际构建选项要实验，不能直接承诺中文召回 |
| [Marked](https://marked.js.org/)、[DOMPurify](https://github.com/cure53/DOMPurify) | Marked 明确不净化 HTML；在安全渲染边界引入 sanitizer | DOMPurify Apache-2.0 或 MPL-2.0；浏览器端少量依赖；解析后净化、净化后不能再插入不可信字符串 |
| [Vue 测试指南](https://vuejs.org/guide/scaling-up/testing.html)、[Vitest](https://vitest.dev/guide/)、[Playwright](https://playwright.dev/docs/intro) | reducer/组件测试与少量关键浏览器闭环 | Vitest/Vue Test Utils 为 MIT，Playwright Apache-2.0；开发依赖；进入实施时锁定 Node/Vite 兼容版本 |
| [FastAPI Middleware](https://fastapi.tiangolo.com/advanced/middleware/)、[Starlette Middleware](https://starlette.dev/middleware/#trustedhostmiddleware) | TrustedHost + 明确 Origin/请求上下文策略，保护本地控制面 | 已有框架能力；MIT/BSD；Host 保护不等于 Origin/CSRF 保护，需组合和浏览器测试 |
| [openapi-typescript](https://openapi-ts.dev/introduction) | 从 OpenAPI 3.0/3.1 生成前端类型 | MIT；开发期生成，不增加运行时客户端；前提是后端响应 schema 精确，类型不代替运行时校验 |
| [APScheduler 3.x](https://apscheduler.readthedocs.io/en/3.x/userguide.html) | 仅在周期监测需求确认后评估 coalescing/misfire/max_instances | MIT；当前不引入；单调度器与持久 job store 有运维成本，不能借调度器绕过 run 准入/预算 |

目前选择“现有框架 + 少量契约代码”，没有依据为这项本地研究任务引入 Postgres、Redis、Celery、Kafka、向量数据库或通用 Agent 插件平台。需要自研的是业务规则：证据 ID 完整性、发布版本对账、事件投影、评测资格与父子研究血缘；成熟框架不能替项目定义这些领域语义。

## 8. 应该怎样证明下一步有价值

先修 Judge，再冻结新的实验基线。建议首批选择三个不同领域及一个证据稀缺案例，记录固定输入、来源采集窗口、模型/价格配置、Prompt/代码/数据哈希。每个候选策略做配对比较；不得同时更换模型、预算、数据集和评分协议后归因于某一个改动。

分别报告：RACE Overall 与四维、FACT 支持率/Unknown/矛盾、硬门禁、完成率、质量评测有效样本数、研究与评测费用、token、时延。小样本只给描述性结论与逐题结果，不宣称统计显著或普遍领先。

求职演示优先展示一个“正常完成”、一个“单兄弟失败仍保留结果”、一个“进程退出后恢复”和一个“证据不足拒答”。离线故障演示明确标记模拟；真实质量展示必须连接可校验的 Run/Bundle/评测元数据。收益目标在方案中可以提出，但实施之前不写成已实现成绩。
