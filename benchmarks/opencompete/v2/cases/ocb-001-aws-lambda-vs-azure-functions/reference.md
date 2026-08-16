# AWS Lambda 与 Azure Functions 事件驱动计算平台选型参考报告

> Reference 模式：`positive`。本报告由 AI 竞品分析专家依据截至 2026-07-17 可读取的官方资料编写，未经过人类审核。动态价格没有冻结快照，Azure Functions 适用 SLA 未取得可定位正文，因此相应结论保持 Unknown。

## 1. 决策摘要

本案例不支持“全局赢家”。平台工程委员会应先确定默认云控制面，再按四类工作负载设置例外门槛：突发型 HTTP API、消息与文件异步处理、延迟敏感型常驻容量、有状态工作流。

可确认的差异是：AWS Lambda 对单函数内存、超时和扩容速度给出明确配额；Azure Functions 把 Flex Consumption 等托管方案、触发器/绑定、Durable Functions、虚拟网络和 Application Insights 监控组合在正式产品文档中。[aws-lambda-quotas] [aws-lambda-concurrency] [azure-functions-overview] [azure-functions-scale] [azure-functions-networking] [azure-functions-monitoring]

推荐采用“现有控制面决定默认平台，PoC 决定例外”的策略。AWS 事件源、IAM、VPC 和运维体系已标准化时，Lambda 的组织切换摩擦通常更低；Azure 消息、身份、网络、Application Insights 和 .NET 工具链已标准化时，Azure Functions 通常更自然。这是组织适配分析，不是性能胜负。[aws-lambda-product] [azure-functions-overview]

## 2. 可比口径与事实矩阵

| 决策项 | AWS Lambda 已确认事实 | Azure Functions 已确认事实 | 决策边界 |
|---|---|---|---|
| 内存与执行时间 | 内存为 128–10,240 MB，1 MB 递增；单次函数超时 900 秒。[aws-lambda-quotas] | 不同托管方案的执行边界不同；本 Reference 不用单一数字概括所有方案。[azure-functions-scale] | 长任务必须按具体托管方案和编排方式比较 |
| 扩缩容 | 文档列出每函数每 10 秒 1,000 个执行环境的扩容配额；并发定义为同时处理的在途请求数。[aws-lambda-quotas] [aws-lambda-concurrency] | Flex Consumption 可按事件、单实例并发和单函数负载动态扩展到 1,000 个实例。[azure-functions-scale] | 两个“1,000”不是同一指标，不能直接判定谁扩容更快 |
| 事件与工作流 | 本案例固定来源没有 AWS 编排服务的直接资料，组合能力需另行评审。 | 官方概览明确触发器/绑定，并把 Durable Functions 列为事件驱动工作流路径。[azure-functions-overview] | 函数与编排组合必须作为整体 PoC，不能用单边资料宣布胜者 |
| 私网 | 函数接入 VPC 后默认只能访问该 VPC 内可用资源，互联网出口需额外配置。[aws-lambda-vpc] | 虚拟网络集成可让函数应用访问虚拟网络资源。[azure-functions-networking] | 必须验证 DNS、出口、私有终结点、连接数和故障排查 |
| 监控 | 本案例没有固定 AWS 监控文档形成同口径事实表。 | Azure Functions 与 Application Insights 有内置集成。[azure-functions-monitoring] | 监控能力存在不等于观测成本或故障效率更优 |
| SLA | AWS Lambda SLA 声明区域月度可用率至少 99.95%。[aws-lambda-sla] | 固定 Azure SLA 入口访问受阻，具体适用条款未取得。[azure-functions-sla] | 双方 SLA、排除项和抵扣比较为 Unknown |

## 3. 竞争机制

### 3.1 扩缩容数字不能直接横比

AWS 的“每函数每 10 秒 1,000 个执行环境”是函数扩容配额；Azure Flex 的“最多 1,000 个实例”是托管方案的动态扩展上限描述。[aws-lambda-quotas] [azure-functions-scale] 两者的实例模型、单实例并发和触发器行为不同。成熟比较必须使用相同请求模型和业务 SLO 实测，不能把两个相同数字当作同一能力。

### 3.2 网络选择改变运行与排障路径

Lambda 连接 VPC 后默认互联网访问行为发生变化；Azure 虚拟网络集成则把出站访问纳入虚拟网络路径。[aws-lambda-vpc] [azure-functions-networking] 因此私网不是“是否支持”的布尔比较，而是 DNS、NAT、地址、私有终结点、安全组/网络规则和可观测性的组合设计。

### 3.3 编排能力会扩大锁定边界

Azure 官方资料明确 Durable Functions 工作流路径。[azure-functions-overview] 但本案例没有 AWS 侧对应编排服务的固定直接来源，不能据此判定 Azure 全面胜出。真正的锁定边界包括事件格式、状态语义、重放、身份、网络、日志、告警和 IaC，而不仅是函数语言。

## 4. 替代解释与 Unknown

- **TCO 为 Unknown。** 两个价格页均为动态页面，未保存同日、同地区内容快照，也缺少企业折扣、出站流量、日志、存储和支持数据。[aws-lambda-pricing] [azure-functions-pricing]
- **性能为 Unknown。** 官方配额和扩缩容说明不能替代相同代码、运行时、内存、区域和负载下的 P50/P95/P99、失败率和扩容测试。
- **双边 SLA 比较为 Unknown。** AWS 条款可以定位，但 Azure Functions 具体适用条款没有取得；不能用单边合同或营销可用性形成排名。[aws-lambda-sla] [azure-functions-sla]
- **服务支持质量为 Unknown。** 本案例没有双方目标支持计划、严重度响应、升级路径、价格和真实工单处理结果。SLA 是服务承诺，不等于企业支持质量。
- **开发效率为 Unknown。** 工具和语言存在不等于目标团队完成任务更快。

## 5. 条件化建议

### 优先 AWS Lambda

适用于 AWS 事件源、IAM、VPC、部署和运维流程已经标准化，并且工作负载能在 900 秒执行边界内完成的场景。若核心链路需要稳定尾延迟，仍必须验证预置容量、扩容过程和完整成本。

### 优先 Azure Functions

适用于 Azure 消息与存储事件、虚拟网络、Application Insights 和 .NET/Visual Studio 运维链路已经标准化的场景。若依赖 Durable Functions，应把状态、重放、超时和退出路径纳入架构评审。

### 不应立即选边

- 延迟敏感核心链路尚未完成相同负载压测；
- 私网出口、DNS 或第三方 API 路径尚未验证；
- 需要以 SLA 抵扣或企业支持作为采购门槛，但 Azure 适用条款尚未冻结；
- 支持响应、升级和恢复协作是硬门槛，但双方书面支持方案和桌面演练尚未完成；
- 价格模型缺少同日区域快照、常驻容量、日志和网络成本；
- 严格多云退出是硬要求，但事件、状态、身份和观测尚未做迁移测试。

## 6. 最小 PoC 与结论翻转条件

使用相同业务代码、依赖、区域级别和内存档位，执行三组测试：突发 HTTP、队列消费、私网数据库访问。记录 P50/P95/P99、扩容时间、限流、失败/重试、部署回滚、日志追踪完整性和完整账单。有状态工作流另测状态重放、失败恢复和退出成本。

采购验证还应取得双方目标支持计划和报价，并用同一严重故障场景执行桌面演练，记录首次响应、升级链、责任边界和恢复协作。未完成该验证前，支持质量保持 Unknown。

以下任一结果可翻转默认建议：

1. 非默认云在核心负载上达到 SLO，且完整 TCO 显著低于迁移和双控制面成本；
2. 默认云无法满足私网、执行时间、扩容或恢复门槛；
3. 目标工作流对某一方原生事件、监控或编排的依赖足以抵消切换成本；
4. 合同 SLA、支持或合规条款成为硬性淘汰条件。
