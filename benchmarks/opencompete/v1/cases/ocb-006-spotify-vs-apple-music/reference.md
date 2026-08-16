# Spotify 与 Apple Music 通用竞品分析参考报告（未人工审核草稿）

> 状态：未人工审核。本草稿由固定来源的可访问内容整理，只用于具名审核员后续核验；在审核通过前不能作为权威答案，也不能用于正式 Judge 评分。

## 1. 范围与地区口径

- 信息截止日：2026-07-15。
- 核心比较对象：Spotify、Apple Music；分析层级仅为产品与服务。
- Spotify 固定产品 URL 自动落到新加坡英文区，Apple Music 产品页为美国区。价格只按各页面自身地区分别陈述，不进行数值高低比较，也不外推为全球价格。[spotify-premium][apple-music-product]
- `apple-music-support` 当前实际是无损音频说明，而不是订阅价格页；本草稿只用它支持音频能力和设备条件，不用它支持价格。[apple-music-support]

## 2. 固定来源核验结果

| 来源 | 截止日可见内容 | 本草稿用途 | 限制 |
|---|---|---|---|
| `[spotify-premium]` | 新加坡区 Premium 页面，列出计划、当地页面价格、试用条件和 Premium 功能。[spotify-premium] | Spotify 套餐与功能。 | 没有目录数量、推荐机制细节或无损音频技术规格。 |
| `[spotify-financials]` | 固定投资者入口拒绝当前访问，没有取得财务正文。[spotify-financials] | 不用于事实结论。 | 本案例仅为产品与服务层，也不应从该入口扩展到公司财务比较。 |
| `[apple-music-product]` | 美国 Apple Music 产品页，列出计划、美国区价格、内容、发现、音频和设备能力。[apple-music-product] | Apple Music 产品、套餐和生态能力。 | 不能与新加坡区 Spotify 价格直接比较。 |
| `[apple-music-support]` | “About lossless audio in Apple Music”，发布日期为 2026-04-03，说明 ALAC、分辨率、平台和硬件条件。[apple-music-support] | 无损音频能力与边界。 | 不包含订阅价格，和 `sources.json` 中的“订阅官方支持页”标题不一致。 |

## 3. 通用必选维度比较

| 维度 | Spotify | Apple Music | 比较判断 |
|---|---|---|---|
| `capabilities` | Premium 页面列出无广告、离线下载、任意顺序播放、无损音质、实时与朋友同听和队列管理。[spotify-premium] | 产品页列出无广告、离线下载、实时歌词、个性化内容、策划歌单、无损音频、空间音频、共同编辑歌单、Apple Music Sing 和 Apple Music Classical。[apple-music-product] | 双方固定页面都覆盖核心流媒体能力；Apple 页面披露的功能类别更细，但来源详略差异不能直接等同于实际能力优劣。 |
| `pricing` | 新加坡区页面显示 Individual 首月为 `$0`、此后 `$11.98/月`，Student 首月为 `$0`、此后 `$6.48/月`，Duo 为 `$16.98/月`，Family 为 `$20.98/月`；Duo 限同住两人，Family 最多六个同住家庭成员。[spotify-premium] | 美国区页面显示 Individual 为 `$10.99/月`、Family 为 `$16.99/月`、Student 为 `$5.99/月`，新订阅者首月免费；Family 可与最多五人分享。[apple-music-product] | 两组价格来自不同国家页面，不能据此判定谁更便宜。结构上，固定页面显示 Spotify 有 Duo，Apple 列出 Apple One 捆绑；该比较仅限页面展示的计划。[spotify-premium][apple-music-product] |
| `user_experience` | Premium 支持取消，Duo 和 Family 成员各有独立账户、已保存音乐和歌单；页面还列出离线、任意顺序播放和队列管理。[spotify-premium] | Family 成员各有个人资料库、推荐和歌单；产品页还列出实时歌词、歌词翻译与发音、共同编辑歌单、Sing 和 CarPlay SharePlay。[apple-music-product] | 两者都呈现个性化与共享场景；固定证据没有相同任务下的易用性、稳定性或可访问性测试，整体体验优劣为 `Unknown`。 |
| `technology_integrations` | 页面只概括可在手机、音箱和其他设备收听，没有给出完整兼容清单。[spotify-premium] | 产品页明确列出 iPhone、iPad、Apple Watch、Mac、Vision Pro、HomePod、Apple TV 4K、CarPlay、Android、Windows、Sonos、智能电视等；无损支持页另列各平台条件。[apple-music-product][apple-music-support] | Apple 固定来源的集成披露更具体；Spotify 实际兼容范围是否更窄为 `Unknown`，不能由页面省略推出。 |
| `service_support` | Premium 页含试用、取消、Duo、Family 和 Student 的 FAQ，并链接支持站点。[spotify-premium] | 产品页含计划、设备、音频和家庭共享 FAQ；支持页给出 iPhone、iPad、Mac、HomePod、Apple TV 4K、Vision Pro、Android 和 PC 的无损设置步骤。[apple-music-product][apple-music-support] | 可确认双方都有页面内自助说明；响应时间、人工支持和问题解决率均为 `Unknown`。 |

## 4. 案例重点维度比较

### 4.1 内容与音频能力

Spotify Premium 页面明确把无损音质列为 Premium 能力，但没有说明编码、最高分辨率或覆盖曲目比例；这些技术细节为 `Unknown`。[spotify-premium]

Apple 产品页称目录超过 1 亿首、支持无损和空间音频，并包含 Apple Music Classical；其支持页说明大多数 Apple Music 目录使用 ALAC，分辨率从 `16-bit/44.1 kHz` 到 `24-bit/192 kHz`，高于 `48 kHz` 的播放在部分设备上需要外置数模转换器，且蓝牙连接通常不支持无损传输。[apple-music-product][apple-music-support] Apple 产品页对无损覆盖使用“整个目录”的表述，而支持页使用“大多数目录”，两者口径不完全一致；本草稿采用更保守的“大多数目录”，完整覆盖比例仍为 `Unknown`。[apple-music-product][apple-music-support]

因此，只能确认 Apple 的固定来源提供了更具体的音频规格，不能在缺少 Spotify 对等规格和同设备听测的情况下判定实际音质优劣。

### 4.2 推荐与发现

Apple 产品页说明会依据收听历史提供个性化艺人和专辑建议，并提供编辑策划歌单、Playlist Playground、艺人通知、演出信息、广播以及 Shazam 识别联动。[apple-music-product] Spotify 固定 Premium 页没有披露推荐算法、个性化入口或编辑策划机制，因此 Spotify 在本维度为 `Unknown`；这表示证据缺口，不表示 Spotify 没有相关功能。[spotify-premium]

### 4.3 定价与套餐

两者都在各自页面提供 Individual、Student 和 Family；Spotify 页面另列 Duo，Apple 页面另列 Apple One 服务捆绑。[spotify-premium][apple-music-product] 由于 Spotify 价格来自新加坡区、Apple 价格来自美国区，税费、币种、促销和购买力口径均未统一，不能进行价格差额或性价比排序。Apple 的固定支持链接不包含订阅价格，因此 `fact-pricing` 对 Apple 支持页的预期未被当前页面满足。[apple-music-support]

### 4.4 平台生态

Apple 固定来源展示了 Apple 自有硬件、CarPlay、Android、Windows、Sonos、智能电视等跨平台入口，并对不同设备的无损条件作出说明。[apple-music-product][apple-music-support] Spotify 页面只笼统提到手机、音箱和其他设备。[spotify-premium] 这支持“Apple 来源披露更完整”，但不足以支持“Apple 实际覆盖更广”；Spotify 的具体平台清单、第三方集成深度和无损设备条件均为 `Unknown`。

## 5. 综合判断

固定证据显示，两项服务都提供无广告、离线与多档订阅等核心付费能力；Spotify 页面突出四种 Premium 计划及共享账户规则，Apple 页面则更详细地呈现音频规格、内容发现、歌词与演唱、古典音乐和跨设备生态。[spotify-premium][apple-music-product][apple-music-support] 由于地区价格不一致、页面披露详略不一致，且没有同一设备、同一曲目、同一地区的体验证据，本草稿不做总体质量或价格优劣排序。

## 6. 待人工审核事项与限制

1. 统一双方价格地区和币种后，才能审核价格比较；当前新加坡区与美国区数字只能分别保留。
2. 核验 `apple-music-support` 的来源标题与实际页面错位，并决定是否在新数据集版本中修正；当前不得用它支持订阅价格。
3. 为 Spotify 补齐固定来源内的目录规模、推荐机制、完整设备清单和无损技术规格之前，相关比较保持 `Unknown`。
4. 页面为厂商自述，尚无固定独立来源或同条件实测；本草稿没有投资、订阅购买或平台选择建议。
