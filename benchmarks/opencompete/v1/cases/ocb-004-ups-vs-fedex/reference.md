# UPS 与 FedEx 通用竞品分析参考报告（未人工审核草稿）

> 状态：未人工审核。本草稿由固定来源的可访问内容整理，只用于具名审核员后续核验；在审核通过前不能作为权威答案，也不能用于正式 Judge 评分。

## 1. 范围与口径

- 信息截止日：2026-07-15。
- 核心比较对象：UPS、FedEx；不引入其他承运商作为核心比较对象。
- 分析层级：产品与服务、公司与组织。
- 证据边界：只使用 `sources.json` 固定的四个入口。入口失效、正文未返回或正文没有所需数据时，相关结论标为 `Unknown`。
- 数字口径：固定来源未能提供可核验的年度报告正文或运输服务正文，因此本草稿不填写收入、利润、运量、网络节点、时效或价格数字。

## 2. 固定来源核验结果

| 来源 | 截止日可见内容 | 可用于本草稿的证据 | 限制 |
|---|---|---|---|
| `[ups-annual-reports]` | URL 返回 UPS 的“Page Not Found”页面；页面导航虽包含 “Annual Reports”，但没有年度报告正文或财务数字。[ups-annual-reports] | 只能确认该固定 URL 已失效。 | 不能据此填写 UPS 收入、利润、运量或网络规模。 |
| `[ups-services]` | 对固定 URL 的只读访问未收到可解析正文。[ups-services] | 无。 | UPS 服务组合、时效、覆盖、价格及支持渠道均为 `Unknown`。 |
| `[fedex-annual-reports]` | 页面标题和导航为 “Annual reports”，但取得的正文只有站点框架，没有报告条目、期间或财务数字。[fedex-annual-reports] | 只能确认这是 FedEx 年报入口的页面框架。 | 不能据此填写 FedEx 收入、利润、运量或网络规模。 |
| `[fedex-services]` | 固定 URL 显示 “FedEx \| System Down”，称当前请求无权查看该网页，并给出 `1.800.GoFedEx / 1.800.463.3339` 作为运输协助电话。[fedex-services] | 可确认故障页提供电话支持兜底。 | 页面没有运输产品、时效、价格或覆盖信息。 |

## 3. 产品与服务层比较

| 维度 | UPS | FedEx | 比较判断 |
|---|---|---|---|
| `capabilities` | `Unknown`：固定服务入口没有返回正文。[ups-services] | `Unknown`：固定服务入口只返回系统故障页。[fedex-services] | 无法核验双方具体运输能力，不能把品牌常识写成服务事实。 |
| `pricing` | `Unknown`：无价格、计费区间、附加费或地区口径。[ups-services] | `Unknown`：无价格、计费区间、附加费或地区口径。[fedex-services] | 不具备同路线、同重量、同服务等级的可比报价。 |
| `user_experience` | `Unknown`：无下单、追踪、改派、理赔或通知流程证据。[ups-services] | `Unknown`：故障页不能代表正常客户流程。[fedex-services] | 仅凭入口可访问性不足以评价长期客户体验。 |
| `technology_integrations` | `Unknown`：无 API、插件或平台集成正文。[ups-services] | `Unknown`：无 API、插件或平台集成正文。[fedex-services] | 无法比较集成覆盖、能力深度或实施成本。 |
| `service_support` | `Unknown`：固定页面未返回支持渠道正文。[ups-services] | 故障页提供全天候措辞的电话运输协助入口 `1.800.GoFedEx / 1.800.463.3339`。[fedex-services] | 只能确认 FedEx 在该故障页有电话兜底；不能由此推断双方整体支持质量。 |
| 服务组合 | `Unknown`：无服务名称、时效或适用地区。[ups-services] | `Unknown`：无服务名称、时效或适用地区。[fedex-services] | `fact-services` 所需的双方官方服务证据均不完整。 |
| 网络覆盖 | `Unknown`：无国家、网点、枢纽、车辆或航线数据。[ups-services][ups-annual-reports] | `Unknown`：无国家、网点、枢纽、车辆或航线数据。[fedex-services][fedex-annual-reports] | 不能用“全球”等未量化措辞替代可比网络口径。 |
| 客户体验 | `Unknown`：无独立体验指标或官方流程正文。[ups-services] | 故障页反映一次访问失败并给出电话替代渠道，但单次页面状态不能代表总体体验。[fedex-services] | 无法形成方向性优劣结论。 |

## 4. 公司与组织层比较

| 维度 | UPS | FedEx | 比较判断 |
|---|---|---|---|
| `positioning_strategy` | `Unknown`：失效入口没有年度报告中的战略陈述。[ups-annual-reports] | `Unknown`：页面框架没有年度报告中的战略陈述。[fedex-annual-reports] | 不能从网站导航或公司名称推导定位差异。 |
| `business_model` / 商业模式 | `Unknown`：无分部收入、客户结构或价值获取口径。[ups-annual-reports] | `Unknown`：无分部收入、客户结构或价值获取口径。[fedex-annual-reports] | 无法建立可核验的商业模式比较。 |
| `go_to_market` | `Unknown`：无直销、渠道或客户分层材料。[ups-annual-reports] | `Unknown`：无直销、渠道或客户分层材料。[fedex-annual-reports] | 无法评价获客和市场进入方式。 |
| `operations_capabilities` / 运营效率 | `Unknown`：无运量、单件收入、成本或资产利用指标。[ups-annual-reports] | `Unknown`：无运量、单件收入、成本或资产利用指标。[fedex-annual-reports] | `fact-operations` 未满足，不能形成效率排序。 |
| `partnerships_ecosystem` | `Unknown`：无合作伙伴或生态正文。[ups-annual-reports] | `Unknown`：无合作伙伴或生态正文。[fedex-annual-reports] | 无法比较生态广度或合作深度。 |
| 财务与规模 | `Unknown`：固定入口没有报告期间和数字。[ups-annual-reports] | `Unknown`：固定入口没有报告期间和数字。[fedex-annual-reports] | 收入、利润、运量和网络数字全部留空，避免混用财年或统计口径。 |

## 5. 跨层判断

现有固定证据不能把公司战略、商业模式或运营投入与具体服务表现建立可追溯关系。唯一可确认的服务层事实是 FedEx 的故障页提供电话协助，但这既不能说明正常服务能力，也不能支持公司层面的运营效率结论。[fedex-services] 因此，UPS 与 FedEx 的方向性竞争差异为 `Unknown`。

## 6. 待人工审核事项与限制

1. 修复或重新版本化失效或内容不足的 UPS 年报、UPS 服务、FedEx 年报和 FedEx 服务来源之前，本案例不应转为 `approved`。
2. 人工审核员需从同一数据集版本内可访问的双方正式年报核对报告期、币种、收入、利润、运量及网络口径；当前四个固定入口无法完成该工作。
3. 人工审核员需取得双方正式服务说明，以同一地区、路线、包裹属性和服务等级比较时效与价格；当前证据不具备该可比性。
4. 本草稿没有投资、采购或承运商选择建议，也没有把页面访问失败解释为企业实际运营优劣。
