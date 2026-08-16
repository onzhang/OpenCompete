# Microsoft 365 与 Google Workspace 通用竞品分析参考报告（未人工审核草稿）

> 状态：未人工审核。本报告是基于固定来源形成的可审阅草稿，不是权威答案；在具名审核员核验事实、引用和截止日之前，不得用于正式 Judge 评分。

## 1. 范围与证据状态

- 信息截止日：2026-07-15。
- 比较对象仅限 Microsoft 365 与 Google Workspace，层级仅限产品与服务。
- 覆盖通用必选维度 `capabilities`、`pricing`、`user_experience`、`technology_integrations`、`service_support`，以及案例重点维度协作套件、定价与版本、安全与合规、生态集成。
- `m365-business-plans` 的固定 URL 通过真实浏览器可访问，但会重定向到新的 Microsoft 官方方案页；普通 HTTP 客户端收到 WAF 拦截页。
- `m365-security` 的固定 URL 返回 404，无法核验其正文。[m365-security] `workspace-security` 的固定 URL 则重定向到“100 人以上企业安全检查清单”，页面标示最后更新于 2026-07-10；它不再是 `sources.json` 标题所称的单一“安全调查工具说明”。[workspace-security]
- 两个方案页都是动态页面且没有历史快照。下述页面值可形成草稿，但不能证明页面在截止日后从未变化，正式审核须重放或留存 2026-07-15 快照。

## 2. 结论摘要

两者都提供企业邮箱、会议、文件存储和协作应用，但固定来源呈现的产品组织不同：Microsoft 页面以 Business Basic、Apps for business、Business Standard with Copilot 和 Business Premium with Copilot 等 SKU 组合桌面应用、Teams、存储和安全；Google 页面以 Business Starter、Standard、Plus、Enterprise 的连续层级比较 Gmail、Meet、Drive、协作应用、Gemini 与安全管理能力。[m365-business-plans] [workspace-pricing]

固定来源不能支持简单的“谁更便宜”。Microsoft 页面显示美国区按用户、按月列价并按年付费；Google 页面在本次中国区域渲染中只显示 Enterprise 联系销售，虽说明一年承诺可节省 16%、所有方案按月结算，却没有展示 Starter/Standard/Plus 的可引用单位价格。[m365-business-plans] [workspace-pricing]

## 3. 通用必选维度比较

### 3.1 `capabilities`

- **Microsoft 365：** Business Basic 提供 Web 和移动应用、企业邮箱、Teams、每用户 1 TB 云存储以及 Bookings、Planner、Forms 等应用；Business Standard with Copilot 增加 Word、Excel、PowerPoint、Outlook 的桌面/Web/移动版本，并在方案页中加入 Microsoft 365 Copilot Business。[m365-business-plans]
- **Google Workspace：** 方案比较覆盖 Gmail、Meet、Chat、Calendar、Drive、Docs、Sheets、Slides 和 Gemini。四个层级都提供企业邮箱；Meet 参与者上限按 Starter、Standard、Plus、Enterprise 顺序为 100、150、500、1,000 人，会议时长上限均为 24 小时。[workspace-pricing]
- **分析判断：** 两者都覆盖核心协作链路。Microsoft 固定来源更明确地区分桌面 Office 应用与轻量 Web/移动层级；Google 固定来源更明确展示同一套云协作产品随 SKU 提升的会议、存储、AI 与管理差异。这是产品打包方式差异，不是用户体验优劣结论。

### 3.2 `pricing`

- **Microsoft 365 页面值：** Business Basic 为每用户每月 7 美元、Apps for business 为 10 美元、Business Standard with Copilot 为 23.50 美元、Business Premium with Copilot 为 32 美元，页面均标注按年订阅并按所示月单价计费。[m365-business-plans]
- **Google Workspace 机制：** 页面说明一年承诺相对灵活方案节省 16%，所有方案按月结算；Starter、Standard、Plus 最多 300 位用户，Enterprise 无最小或最大用户数限制。[workspace-pricing]
- **Unknown：** 当前访问地区未展示 Google Starter、Standard、Plus 的单位价格，Enterprise 仅为联系销售；因此无法与 Microsoft 的美国区公开价格进行同地区、同功能、同合同期限比较。Microsoft 的 Copilot 组合 SKU 与 Google 页面内含的 Gemini 权益也不是天然同包可比。

### 3.3 `user_experience`

- **Microsoft 365：** Business Basic 被描述为 Web 和移动应用；Standard with Copilot 与 Premium with Copilot 明确包含桌面、Web 和移动版 Word、Excel、PowerPoint、Outlook，并结合 Teams、Exchange、OneDrive 与 SharePoint。[m365-business-plans]
- **Google Workspace：** 固定页面证明其协作入口包括 Gmail、Meet、Chat、Calendar、Drive、Docs、Sheets、Slides，并按层级提供会议录制、AI 辅助和存储差异。[workspace-pricing]
- **Unknown：** 来源没有相同任务的完成时间、离线能力测试、迁移摩擦、终端性能或用户满意度数据，不能把应用清单直接转换为“更易用”的结论。

### 3.4 `technology_integrations`

- **Microsoft 365：** 方案页列出的组合生态包括 Outlook、Exchange、Teams、OneDrive、SharePoint、Loop、Bookings、Planner、Forms；Premium with Copilot 还列出 Entra ID、Intune、Defender 和 Purview。[m365-business-plans]
- **Google Workspace：** 页面列出 Gmail、Meet、Chat、Calendar、Drive、Docs、Sheets、Slides，并在站点应用清单中列出 Forms、Sites、Keep 与 Apps Script；安全与管理入口包括 Admin、Endpoint、Vault 和 Work Insights。[workspace-pricing]
- **Unknown：** 固定来源没有双方 Marketplace 应用数量、API 覆盖、第三方集成质量或迁移工具的同口径数据，不能按列出的自有产品数量判断生态规模。

### 3.5 `service_support`

- **Microsoft 365：** Business Basic、Standard with Copilot 和 Premium with Copilot 的方案摘要均列出全天候电话和 Web 支持。[m365-business-plans]
- **Google Workspace：** Enterprise 摘要列出对重大问题提供更快响应的 Enhanced Support；页面还说明 Enterprise 可从 Standard 付费升级到 Enhanced，或从 Enhanced 升级到 Premium Support。[workspace-pricing]
- **Unknown：** 固定来源没有双方支持响应时限、覆盖语言、严重等级和价格的完整对照，也没有 Google 非 Enterprise 层级的同粒度支持承诺。

## 4. 案例重点维度

### 4.1 协作套件

Microsoft 365 把 Office 桌面/Web/移动应用、Teams 通信、Exchange 邮箱、OneDrive/SharePoint 内容层组合在商业方案中；Google Workspace 把 Gmail、Meet、Chat、Calendar、Drive 和浏览器协作应用放在统一层级表中。[m365-business-plans] [workspace-pricing]

Google 的固定页面显示存储额度按 Starter、Standard、Plus、Enterprise 分别为每用户 30 GB、2 TB、5 TB、5 TB 且 Enterprise 可申请更多；Microsoft 商业方案摘要则统一列出每用户 1 TB 云存储。[workspace-pricing] [m365-business-plans]

**分析边界：** Google 使用组织内池化存储，而 Microsoft 页面只陈述每用户额度；固定来源没有给出共享、超额购买与保留策略的完整同口径规则，不能只按名义容量判断实际可用性。

### 4.2 定价与版本

Microsoft 当前固定页面把部分标准/高级商业层级与 Copilot 组合展示，Basic 则不以 Copilot 命名；Google 页面把 Gemini 权益直接放入四层功能表，Starter 的部分 Workspace 应用 AI 能力受限，而 Standard 以上展示更多权益。[m365-business-plans] [workspace-pricing]

**Unknown：** 缺少 Google 当前访问地区自助购买单价，也缺少双方相同地区的税费、促销、月付/年付解约和附加服务总价，所以不能计算等价席位的年化总成本。

### 4.3 安全与合规

Microsoft 方案页声称 Premium with Copilot 包括基于策略的身份与访问管理、设备安全与管理、高级威胁防护，以及敏感数据发现、分类和保护；Standard with Copilot 与 Basic 只在摘要中列出核心身份管理和自动垃圾邮件/恶意软件过滤。[m365-business-plans]

Google 定价页显示四层均支持两步验证；端点管理按 Starter、Standard、Plus、Enterprise 顺序为 Fundamental、Fundamental、Advanced、Enterprise。Enterprise 摘要还列出 S/MIME、DLP、情境感知访问、数据区域和 Cloud Identity Premium。[workspace-pricing]

Google 的安全检查清单要求大型组织考虑强制两步验证、安全密钥、活动报告与告警、端点管理、Drive/Gmail DLP、情境感知访问、客户端加密、管理员审计日志以及安全中心调查和处置；页面也提醒部分设置依赖具体 Workspace 或 Cloud Identity 版本。[workspace-security]

**Unknown：** `m365-security` 固定 URL 已失效，无法从该来源核验 Microsoft 的管理实施步骤、合规清单或版本前提；`workspace-security` 的 URL 已改变主题，无法据此确认安全调查工具在各 Google SKU 的精确授权。因而本草稿不做合规认证覆盖或安全强弱排名。

### 4.4 生态集成

Microsoft 的固定页面展示 Office、通信、内容协作与身份/终端/威胁/数据治理产品的同厂商组合；Google 的固定页面展示 Workspace 协作应用、Apps Script 以及 Admin、Endpoint、Vault 等管理入口。[m365-business-plans] [workspace-pricing]

**分析判断：** 两者都能在自有生态内形成从内容生产到管理的闭环，但第三方扩展、跨套件互操作和迁移成本均缺少固定证据，保持 Unknown。

## 5. 限制与待人工审核项

1. Microsoft 安全文档固定 URL 返回 404；正式审核前应修复数据集来源并提升数据集版本，而不是在本草稿中替换来源。
2. Google 安全 URL 已重定向且标题不再匹配 `sources.json`，正式审核须确认是否仍符合 Criteria 的来源意图。
3. 两个定价页均为动态、地区化页面。Microsoft 数字是美国区页面值，Google 本次在中国区域未显示自助单价；跨地区价格比较为 Unknown。
4. 方案页中的功能声明没有替代实际租户测试、合规证明或服务合同。本报告不提供采购建议。
