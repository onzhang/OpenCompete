# Visa 与 Mastercard 通用竞品分析参考报告（未人工审核草稿）

> 状态：未人工审核。本报告是基于固定来源形成的可审阅草稿，不是权威答案；在具名审核员核验事实、引用和截止日之前，不得用于正式 Judge 评分。

## 1. 范围与财务口径

- 信息截止日：2026-07-15。
- 比较对象仅限 Visa 与 Mastercard；覆盖公司与组织、产业与市场两个层级。
- 公司与组织层覆盖 `positioning_strategy`、`business_model`、`go_to_market`、`operations_capabilities`、`partnerships_ecosystem`，并重点覆盖商业模式、网络与覆盖、财务与规模。
- 产业与市场层覆盖 `market_size_growth`、`customer_segments_demand`、`competitive_landscape`、`regulation_standards`、`trends_risks`，并重点覆盖市场结构、监管环境。
- 财务比较使用截止日前最新完整年度文件：Visa 2025 财年截至 2025-09-30；Mastercard 2025 财年截至 2025-12-31。两者报告期相差三个月，且支付量、GDV、交易和卡片口径不同，不能把数字直接当成同口径市场份额。
- Visa 年报入口直接链接 2025 年报。Mastercard 在 `sources.json` 中的固定 URL 返回 404，但该官方失效页明确给出新的“Annual Reports & Proxy”入口；新入口通过其官方 Q4 数据接口提供 2025 Form 10-K。本草稿引用仍归于固定 `mastercard-annual-reports` 来源，正式审核须确认这一重定向链。

## 2. 结论摘要

Visa 与 Mastercard 都运营连接持卡人、发卡机构、商户和收单机构的四方支付网络，并把网络处理与增值服务作为收入基础。Visa 以 VisaNet、“network of networks”和 Visa as a Service 描述其连接与模块化服务；Mastercard 将收入分为支付网络、增值服务与解决方案，并把战略概括为发展核心业务、拓展客户与地区、建设未来增长领域。[visa-annual-report] [mastercard-annual-reports]

2025 年两家公司都报告了庞大的网络规模，但口径不可直接排名：Visa 报告 14.2 万亿美元支付量、257.5 十亿笔由 Visa 网络处理的交易和 400 亿美元净收入；Mastercard 报告 10.6 万亿美元 GDV、175.5 十亿笔 switched transactions 和 327.91 亿美元净收入。Visa 的支付量不等于 Mastercard 的 GDV，Visa 凭证与 Mastercard 卡片的时点和排除项也不同。[visa-annual-report] [mastercard-annual-reports]

## 3. 公司与组织层

### 3.1 `positioning_strategy`

- **Visa：** 将自身定位为数字支付和全球商业/资金流动网络，通过 VisaNet 提供授权、清算与结算，并以“network of networks”把卡、银行账户和数字钱包连接到多种资金流动端点。[visa-annual-report]
- **Mastercard：** 将自身定位为全球支付行业的技术公司，除 Mastercard、Maestro、Cirrus 品牌网络外，还提供 ACH 批量与实时账户支付、安全、数据洞察、数字认证、处理与网关能力。[mastercard-annual-reports]
- **分析判断：** 两者都在从卡网络向更广的资金流动与服务能力扩展；固定年报能证明战略方向，不能证明新业务的独立市场份额或长期成功率。

### 3.2 `business_model` 与商业模式

Visa 的四方模式连接消费者、发卡机构、收单机构与商户。其收入包括支持客户使用支付服务的 service revenue、授权/清算/结算等 data processing revenue、跨境处理与货币兑换相关的 international transaction revenue，以及其他收入，再扣除客户激励。Visa 明确说明自身不发卡、不放贷、不承担这些活动的信用风险，且其收入不直接来自 interchange reimbursement fee 或 merchant discount rate。[visa-annual-report]

Mastercard 的支付网络收入主要来自按品牌卡 GDV 收取的费用，以及 switching 和其他网络服务费用；增值服务与解决方案通过固定或按交易收费，包含安全、客户获取与互动、市场洞察、数字与认证、处理与网关、ACH/实时账户支付和开放金融。其净收入类别均扣除客户返利与激励。[mastercard-annual-reports]

**分析判断：** 两者都以网络活动规模和服务使用驱动收入，并通过客户激励维护生态关系；但收入分类不同，不能把 Visa 的单项收入标签与 Mastercard 的两大收入类别直接逐项对齐。

### 3.3 `go_to_market`

Visa 报告其客户包括近 14,500 家金融机构，并通过 API、结构化数据交换及与传统金融机构、金融科技和 AI 公司合作提供网络能力。[visa-annual-report]

Mastercard 的网络连接消费者、金融机构、商户、政府、数字伙伴和企业；其战略强调拓展客户与地区，四方网络的直接客户主要是发卡机构、收单机构及其他市场参与者。[mastercard-annual-reports]

**Unknown：** 固定来源没有同口径披露销售组织、渠道伙伴数量、客户获取成本、合同期限或地区渗透率，无法判断获客效率。

### 3.4 `operations_capabilities` 与网络覆盖

- **Visa：** 2025 财年有 329 十亿笔 Visa 品牌支付与现金交易由 Visa 或其他网络处理，其中约 258 十亿笔由 Visa 处理；年报摘要的精确指标为 257.5 十亿笔 Visa 网络处理交易。Visa 报告近 50 亿支付凭证、超过 1.75 亿商户位置、覆盖 200 多个国家和地区及约 160 种货币；商户位置与凭证数据时点为 2025-06-30，部分来自收单机构和第三方。[visa-annual-report]
- **Mastercard：** 2025 年 switched transactions 为 175.5 十亿笔，网络覆盖 220 多个国家和地区、150 多种货币，并描述为连接数亿个受理点和数字接入点。2025 年末 Mastercard 品牌项目卡片合计约 33.91 亿张，但该表排除 Maestro、Cirrus 及其交易量。[mastercard-annual-reports]
- **分析判断：** Visa 提供较精确的商户位置数字，Mastercard 只提供“数亿”受理点；这只能说明披露粒度不同。凭证、卡片、受理点和处理交易定义不一致，不能据此直接认定网络覆盖胜者。

### 3.5 `partnerships_ecosystem`

Visa 的合作生态覆盖金融机构、商户、金融科技、数字钱包、政府与 NGO，并通过开放集成方式提供认证、风险、反欺诈和资金流动模块。[visa-annual-report]

Mastercard 的四方网络以发卡、收单、商户和持卡人为基础，同时把数字伙伴、政府与企业纳入其支付及增值服务客户范围，并通过网络规则维持参与方互操作。[mastercard-annual-reports]

**Unknown：** 年报没有同口径合作伙伴活跃度、集成深度或合作收入数据，不能以合作对象清单推导生态质量排名。

### 3.6 财务与规模

Visa 2025 财年净收入为 400 亿美元，支付量为 14.2 万亿美元，总量（支付量加现金量）为 16.7 万亿美元，Visa 网络处理交易为 257.5 十亿笔，支付凭证为 49 亿个。[visa-annual-report]

Mastercard 2025 财年净收入为 327.91 亿美元，其中支付网络收入 194.76 亿美元、增值服务与解决方案收入 133.15 亿美元；GDV 为 10.6 万亿美元，switched transactions 为 175.5 十亿笔。[mastercard-annual-reports]

**口径限制：** Visa 财年截至 9 月，Mastercard 财年截至 12 月；Visa 支付量排除欧洲联名交易，Mastercard GDV 包括购买量与现金量且关键表排除 Maestro/Cirrus。上述数字适合描述各自披露规模，不构成严格横向份额或增长优劣结论。[visa-annual-report] [mastercard-annual-reports]

## 4. 产业与市场层

### 4.1 `market_size_growth`

**Unknown：** 固定来源没有独立、统一口径的全球支付市场规模或增长率。两家公司自身交易量增长只能反映各自网络，不可代替全市场规模。美联储支付系统入口说明 Federal Reserve Payments Study 会统计美国非现金支付量、现金存取和支付欺诈，但入口页本身没有给出可用于本案例的同期全球市场数字。[federal-reserve-payment-systems]

### 4.2 `customer_segments_demand`

Visa 年报把客户与使用方扩展到消费者、商户、金融机构、企业、政府、金融科技、数字钱包和 NGO；Mastercard 也覆盖消费者、金融机构、商户、政府、数字伙伴和企业。[visa-annual-report] [mastercard-annual-reports]

**Unknown：** 固定来源没有按同一地区、客户类型和期间披露需求量、采用率或价格敏感度，不能判断哪个细分需求增长更快。

### 4.3 `competitive_landscape` 与市场结构

两家公司都处于四方卡网络结构，但竞争不只来自彼此。Visa 年报列出全球/区域网络、本地网络、替代支付提供商及账户到账户网络；Mastercard 年报列出通用支付网络、本地借记网络、ACH 与实时账户支付、数字钱包、金融科技、数字公共基础设施、政府支持方案和数字货币。[visa-annual-report] [mastercard-annual-reports]

美联储支付系统入口同时列出 FedNow、ACH、支票、资金与证券服务，以及金融市场基础设施和支付研究，说明美国支付基础设施并非只有卡网络。该入口不能证明 FedNow 对 Visa 或 Mastercard 的具体份额影响。[federal-reserve-payment-systems]

### 4.4 `regulation_standards` 与监管环境

美联储支付系统入口明确列出 Regulation II（借记卡交换费与路由）、Regulation CC 和 Regulation HH，并把支付系统风险、金融市场基础设施监督和国际标准作为监管主题。[federal-reserve-payment-systems]

Visa 与 Mastercard 的年报都把交换费限制、商户路由/附加费规则、数据本地化、隐私与安全、反洗钱和制裁、地方网络保护等列为监管或经营风险；公司文件描述的是管理层风险判断，不等同于监管机构裁定。[visa-annual-report] [mastercard-annual-reports]

**Unknown：** 美联储入口未对两家公司作个别合规评价，也没有覆盖所有国家和地区；无法据此判断哪家公司监管风险更低。

### 4.5 `trends_risks`

共同趋势包括实时账户支付、数字钱包、代币化、稳定币、AI/代理式商务、数字身份和本地支付基础设施。Visa 强调 network of networks、Visa Direct、代币化和智能商务；Mastercard 披露 ACH/实时账户支付以及数字与认证、安全和开放金融能力。[visa-annual-report] [mastercard-annual-reports]

共同风险包括监管限制、地方网络偏好、数据跨境和本地化要求、网络与信息安全、替代支付竞争，以及跨境活动对宏观和地缘条件的敏感性。[visa-annual-report] [mastercard-annual-reports]

## 5. 公司行为与市场结构的关系

四方网络的规模经济来自发卡机构、收单机构、商户与用户的共同参与；两家公司持续提供客户激励、扩展受理和增加增值服务，是在维护网络参与和提高单一连接的服务价值。[visa-annual-report] [mastercard-annual-reports]

与此同时，实时账户支付、地方网络与监管路由要求可能让部分交易绕开传统全球卡网络。两家公司扩展到账户支付、资金流动、安全和数据服务，可理解为对这一市场结构变化的应对，但固定来源不能证明这些扩展已经抵消核心网络风险。[visa-annual-report] [mastercard-annual-reports] [federal-reserve-payment-systems]

## 6. 限制与待人工审核项

1. Mastercard 固定 URL 已失效；虽能从该官方页面找到新入口并取得 2025 Form 10-K，正式数据集应由人工确认重定向链并在版本升级时修复来源。
2. Visa SEC 固定 URL 当前重定向至季度业绩页，本草稿未用该页面补充季度数字，避免把不同期间混入年度比较。[visa-sec-filings]
3. 网络规模指标定义、排除项和时点不一致；任何市场份额、增长率或效率排名均为 Unknown。
4. 美联储入口只覆盖美国监管与基础设施主题，不能代表全球监管环境。
5. 本报告不构成投资、采购或合作建议，也不对未来业绩作预测。
