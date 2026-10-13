# R2-03：BTCUSDT数量单位与首个有界LIVE接入方案

任务：[Issue #17](https://github.com/shukehi/btc_microstructure/issues/17)，Refs #12。实际基线 **`fd6ad845a913ba41950e01788acc627a31aa8409`**；分支`docs/r2-03-units-and-bounded-collection`。已读全文/最新澄清[6135185376](https://github.com/shukehi/btc_microstructure/issues/17#issuecomment-6135185376)、Issue12最新评论、[PR16复核5497278104](https://github.com/shukehi/btc_microstructure/pull/16#pullrequestreview-5497278104)。本次main相对fa22876仅归档A2报告/证据，无新增业务变化。

原工作区开始仅AGENTS.md未提交；开始前记录其完整性及Git状态，未覆盖/暂存/stash/reset/clean。先fetch origin/main再建立独立worktree，在该目录启动**新Codex CLI会话`01a132a7-1299-7ed0-a66e-739ba7e8a19a`**，直接读取该worktree已提交AGENTS、任务/规格/相关源码/保存的Issue与审查。上级/系统指令仍适用，不宣称换目录消除其效力；新会话未继承实现聊天/记忆，仅负责本范围仓库映射/已存数据离线核查。协调端读取官方资料并整合本份交付，未另起多轮方案工作流。

## 三个独立判断

| 判断 | 当前结论 / 边界 |
|---|---|
| 单位证据 | **充分支持当前核对身份的BTC映射**：BTCUSDT UM perpetual的q/nq为BTC数量，p为USDT/BTC，p×q为USDT，无额外COIN-M乘数。是有依据的文档推导，不由数值/步长或baseAsset单独猜测；新证据候选**待独立审查、runtime_applied=false** |
| 预算与设计 | **足以支撑下一单个有界离线实现任务的待审草案**：600s观察、全流程≤660s，单REST/WS、有限内存/磁盘/收尾与明确验收。不是可执行配置/本次联网许可，也不保证能完成600s |
| 现场/持续能力 | 只复用历史A2完整REST+20WS的PASS。新单位尚未运行时绑定；共享出口余量、真实600s采集/计算/发布/重放、持续预算、24h均**UNKNOWN/UNVERIFIED/NOT_RUN**；不宣称完整M0/M1/M2或研究/收益能力 |

本轮不开发采集器、不运行probe、不请求新行情/下载市场数据、不改网络/代理/TLS/业务/旧测试/规格/配置/锁/工作流/AGENTS或历史报告。只新增本报告和`units_and_collection_plan/`中的两份候选JSON、sources、无网络核查脚本及新输出；不重复提交20样本、旧日志或整份官方PDF。

## 1. 单位证据链：身份、数量口径与物理单位分别闭合

来源ID及**实际读取日期2026-10-13 UTC**、最终URL/章页/短摘录/内容hash/适用性见[sources.json](units_and_collection_plan/sources.json)。下文“官方定义”“既有真实观测”“推导”“工程选择”分别标明。

| 证据层 / 来源 | 精确支持的命题 | 不能单独推出什么 |
|---|---|---|
| **官方正式规格S01**：[USDS-M永续规格](https://www.binance.com/en/about-legal/contractspecs-usdsmperpetual)，正式PDF发布2026-01-05，第2页§5–9、第3页§13 | 标的/结算资产由该合约身份确定；价格以结算资产报价和结算；合约数量定义转引Clearing Procedures | §13不是BTC单位数字；不能停留在嵌入空页面，也不能将其UI“Size”单独当API单位证明 |
| **官方定义S02**：[Clearing Procedures](https://www.binance.com/en/about-legal/clearing-procedures)，PDF第4页/印刷第2页§4.12、4.13(a) | USDS-M合约数量是所代表的标的资产数量；COIN-M的张数/美元乘数是不同定义 | 不把COIN-M BTCUSD的100USD乘数移植BTCUSDT，也不把期权Contract Unit套入本字段 |
| **官方线性关系S02**：PDF第31页/印刷第29页§85.1 | USDS-M持仓价值按数量乘价格的线性维度计算，佐证无需COIN-M倒数/额外美元乘数 | 这里只作量纲支持，不把成交p等同Mark Price、不计算账户P&L |
| **官方API S03**：[Aggregate Trade Streams](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/market) q/nq/p/示例及迁移说明 | q是聚合市场成交数量含RPI，nq是非RPI子集，p为成交价格；st=1为UM。q/nq描述同一数量家族 | Quantity这个词本身不足绑定BTC；100ms聚合不等于全流最多10条/s；并非保险基金/ADL完整事件流水 |
| **官方解释佐证S04/S05**：[产品区别FAQ](https://www.binance.com/en-IN/support/faq/detail/85eac2bba0b342819122dc9bd4745e9b)及[规格FAQ主入口](https://www.binance.com/en/support/faq/detail/360033161972) | BTC/USDT合约单位为一个基础资产单位的解释，与当前正式定义一致；规格FAQ给出S01/S02官方链 | S04发表于2021、S05提示可能过时，只佐证，不覆盖当前正式规则，也不据可选UI显示推导API单位 |
| **既有真实观测A2**：完整本地REST/发布摘录及20WS | BTCUSDT、PERPETUAL、TRADING、baseBTC/quoteUSDT/marginUSDT；stream=btcusdt@aggTrade、st1；q/nq/p原字符串及字段类别与上述产品/接口一致 | 数字大小、0.001步长、q=nq或baseAsset单个字段不是单位证明；20条也不是全量/峰值证据 |

**推导：** S01/S02规定当前产品类的数量物理量是标的资产数量；S03把q及nq定义为该合约聚合成交的总量/子集，而不是两份独立量或报价名义额；A2把本次目标确认为BTC标的/USDT结算的UM永续、st1，S04的一个BTC基础单位解释交叉佐证。因此限定该身份，可明确建立：

| 字段/派生量 | 单位/精确转换 |
|---|---|
| q | BTC；`quantity_base=Decimal(raw.q)`，转换倍率1 |
| nq（可选） | BTC；存在才`Decimal(raw.nq)`，缺失null，0≤nq≤q；不补成q，不与q相加 |
| p | USDT/BTC；`price_quote=Decimal(raw.p)` |
| 成交名义金额 | USDT；`notional_quote=Decimal(p)*Decimal(q)`，无额外100USD/contract或反向合约乘数 |

这不是“一张订单一定1BTC”或“USDT永远价值1USD”的结论；数量可为小数，计价/结算与保证金账户/杠杆身份分开。结论仅适用`binance:um_perpetual:BTCUSDT / aggTrade`，不推广到OI、现货、COIN-M、1000前缀或其他symbol。当前官方FAQ主链指向的文件适用Binance RIE/RCH USDS-M定义；本任务不据此判断任何个人账户/所在地交易资格。

**PDF取得与核验：** 网页工具取得正式入口/iframe，但其PDF解析失败；直接读取同一官方静态PDF（HTTP200，正常TLS，无市场API）后提取文字并渲染核对§定义页，确实取得正文而非将空页记DOC_VERIFIED。S01为174265B、SHA`783cb1eb3e538284ed8577eb1687de7f16b3038d43ece2fe8a84d40c0449648a`；S02为666270B、SHA`a86691f40d8155992c622715ce727d608d7f972ebb93ce90fa0106830e8a0a36`。PDF/截图仅留忽略data目录，公开只存来源/hash/有限摘录，不整份复制。S02未标明可确认的发布日期，sources中为null，不拿PDF技术metadata猜生效日期。

### 新单位候选及生效边界

[quantity_unit_evidence.json](units_and_collection_plan/quantity_unit_evidence.json)新增版本化`unit_evidence_id=DOC:binance:um_perpetual:BTCUSDT:aggTrade:2026-10-13:v1`、逐字段单位/倍率/公式、来源推导、A2摘要引用、复核/失效条件。文档结论充分与记录待独立审查可以同时成立；**runtime_applied=false，effective_from_observed_ns=null**，没有生成已执行绑定事实。

未来独立审查通过后，resolver验证证据hash/版本/审批/完整身份/当前metadata/有效区间，返回不可变绑定对象；不能靠`verified=true`、BTC字符串或随意ID前缀放行。A2完整REST hash只是历史依据，**不要求未来全份exchangeInfo逐字节等于A2**（其中有时变数据）；新run记录自己的完整metadata hash，匹配单位相关身份/协议及审核的参数约束，再写独立run-binding回执。首个实现对过滤器变更采取拒绝启动/重新核对，不无声套用旧tick/step。

官方定义/API数量口径、身份/st/路由、适配器语义或证据撤销/修订发生变化需重核；首次绑定时证据超过7天需资料复核是本方案工程策略，不是交易所规定。首次未来绑定后才记录观察起点，A1/A2当时UNKNOWN原文不改；A2没有cutoff/published日志，不为其倒填可交易时点或当前BTC Delta。

## 2. 已存A2的实际离线核查

新CLI实际使用Python3.12.13/Darwin arm64既有锁定环境，先阻断socket连接/DNS再导入当前worktree相关模块/检查保存数据，没有实例化网络transport、Store恢复或调用Core.process。其本地回执显示31项已发布清单及20WS hash相符，直接Decimal构造与原字符串一致，完整REST复读不变。协调端最后交付的核查脚本再次验证必要的JSON/引用/hash/字段/算术，见第5节；不重复提交原20条内容。

本地完整REST可得：1,152,517B，hash`d5ae0e3682f28512d34ec9ab20131a964df6c76118b8a29e12a76e7b57cbd610`，恰一条BTCUSDT、发布摘录全部选取字段及rateLimits与原件一致。20WS共4082B、最小204/最大205/均值**204.1B**，本地接收跨度**0.858334s**；本批均有nq，不据此将可选字段变必需。不算样本买卖Delta，也不将样本转为synthetic-only输入。

这些是对**保存的LIVE证据的本次离线核查**；不是新LIVE观测/新的协议请求，不证明峰值、未来10分钟覆盖或24h吞吐。原probe metadata/inspection的UNKNOWN和budget的UNVERIFIED均仍为原值。

## 3. 首个有界会话预算：可供验收的工程草案

[bounded_collection_plan.json](units_and_collection_plan/bounded_collection_plan.json)是**DRAFT_PENDING_REVIEW_NOT_EXECUTABLE**，`network_authorized=false/runtime_applied=false/continuous_collection_enabled=false`，不是当前可加载的生产配置。所有MiB=1048576B、GiB=1073741824B。600秒是候选观察段，不是交易周期/本次权限或完成保证。

| 类别 | 明确候选预算 / 理由与停止 |
|---|---|
| 全流程 | **660s=启动30+观察600+收尾30**；绝对单调总截止，阶段只用剩余时间。提前启动完成不延长观察600；任何更早cap/失败均提前结束 |
| 启动 | 本地就绪5s+REST完整15s（连接/读取空闲各5s包含其中）+WS握手/URL订阅10s；raw writer、审核绑定、当前metadata与预算准入未就绪不能RUNNING |
| 收尾 | 冻结接收1s→关闭WS/HTTP5s→已收raw排空/flush10s→派生/manifest收尾14s，合计30；单批raw flush≤2s，各项受总截止截断。已收到队列可在收尾落盘，观察期不延长 |
| 请求 | 一GET exchangeInfo、一WS握手/单btcusdt@aggTrade URL订阅；JSON订阅0；重试/重连/轮询/补回/其他来源0。probe本身仍默认30、范围2–60s、20消息，不循环或改上限 |
| REST自身权重 | S08当前文档为1×1=**1**；对A2的2400/min快照仅为1/2400≈0.041667%算术，不承诺实际余量。A2头1是当时IP累计，ORDERS1200/min及300/10s不套用公共GET |
| 共享IP准入 | S06按IP共享。候选在所有适用窗口保留10%余量：`共享已用/预留+本请求≤limit−ceil(10%×limit)`；2400示例保留240，响应后used>2160/缺失/规则异常即停、不再发请求查余量。当前共享占用UNKNOWN，需未来授权启动前可信协调约束；无法约束则拒绝真实启动。Connect未列连接/IP窗口数字，不复制Spot300/5min或宣称WS握手REST权重为0 |
| 服务器→客户端市场数据 | **100000条**、WS应用payload累计**32MiB**、单条**64KiB**，任何先到即止；服务端控制另≤64条/8KiB。600s平均166.67条/s只是cap/时长，非峰值或市场输出上限 |
| 客户端→服务器控制/心跳 | 官方Connect的10 incoming/s按服务器接收方向理解，不能当成交推送≤10/s；100ms是同价/同taker聚合，不是全流最多10/s。工程选择任意滚动1s总≤**2**、总≤**64**、单payload≤125B、出站控制payload总≤8000B（与入站控制8KiB分开计数）；常规≤1/s且总≤63，预留一次close槽。只处理必须的同payload pong及一次close，无自发ping/JSON订阅。必要pong超常规cap时立即用预留close结束，不为保持连接而忽视心跳 |
| 心跳/静默 | S07服务器ping每3min、pong期限10min；需实际计数发送方向，不能只开当前probe的autoping就声称已计数。首个新连接层可显式处理ping/pong并纳入控制ledger。市场成交静默**60s**候选结束为PARTIAL，不单凭无成交证明网络故障或健康零；仍处理控制与总截止 |
| 队列/写盘 | **1000条/1MiB/最老monotonic年龄1s**；flush触发500条/512KiB/0.5s，单编码记录≤96KiB；独立100ms watchdog，静默也检查年龄。条数/字节/年龄任一先到停止，不覆盖旧输入 |
| Core状态 | raw≤100000、control≤4096、总输入≤104096；最多6回看窗口、1s网格、≤600个cutoff/3600特征行（首个网格严格晚于观察起点，不在起点额外发一行；若墙钟漂移会需第601个则提前停止/PARTIAL，不静默丢网格）；首个任务默认30s，示例可选1/5/30/60/300/900。状态编码预算128MiB，RSS预警384/停止512MiB、100ms观测；不是OS硬内存或当前Core性能已通过，100k历史扫描开销须离线压力验收 |
| 磁盘free | 启动≥**2.5GiB**、预警**2.25GiB**、停止**2GiB**，写前预留及watchdog检查；启动余量覆盖512MiB新run，但共享磁盘消耗未知，不能保证故障事件一定可写。不删唯一raw |
| 所有新run文件 | **512MiB总cap**，含raw封装、控制、Parquet、临时/孤儿/隔离/旧新并存/错误回执。逐写reservation同时满足分区+总cap+free门槛，未知Parquet大小必须计数/分块写，不写完才查或假设压缩率 |

### 文件分配及可复算上界

| 分区 | MiB |
|---|---:|
| REST原件/metadata/证据快照 | 8 |
| WS raw封装 | 192 |
| 控制日志 | 16 |
| manifest/索引 | 32 |
| normalized+quarantine Parquet合计 | 96 |
| feature Parquet | 16 |
| 正在写/孤儿/隔离临时文件 | 112 |
| diagnostics/receipt | 40 |
| **合计** | **512** |

raw保存应用UTF8原字节的可逆base64，解析时才解码给同一Core，不无条件再存一份payload副本。所有编码记录的上限包含换行/字段转义；每条自身封装字段长度必须受限、overhead≤1024B（比初始CLI建议512B留足身份/单位引用空间），超限停止；原始字节仍受单64KiB及总32MiB限制。令S=33554432B、N=100000，base64+封装最坏上界`4×floor((S+2N)/3)+1024N=147405908B < 192MiB=201326592B`。单条`4×ceil(65536/3)+1024=88408B < 96KiB`，也在1MiB队列字节cap内。

control≤4096×4KiB=16MiB；manifest≤4096×8KiB=32MiB，禁止每消息一manifest；单raw段/临时≤1MiB，单次Parquet临时≤64MiB，其与raw临时同存仍在112MiB临时分区内，旧最终文件另收费。diagnostics40MiB中预留1MiB terminal（256×4KiB），常规最多39MiB；不能将terminal当无限flush空间或保证全磁盘失败时可写。每个文件只归一个收费类，rename转类而非凭空豁免；所有复制/隔离副本同样收费。

A2均值尺寸示例：100000×204.1=20410000B≈19.46MiB，仅说明这20条的均值应用于一个计数示例，小于32MiB；**不是600秒预测或压力上界**。大消息/突发/写盘慢/资源cap可能先到，实际不足600秒必须报告STOPPED_EARLY/PARTIAL及首末接收时刻，不能称完成十分钟。

停止预算是有界等待/操作协议，不能靠asyncio timeout假装同步fsync线程可取消。下一实现需要可终止/隔离writer与有界ack/join；超时不前移可信游标、冻结网络、不无限等待。若操作系统未确认writer静止，报告STOP_FAILED/BLOCKED待人工处置，不假称所有字节保存/干净退出；物理断电/满盘/内核I/O延迟本轮NOT_RUN。

## 4. 最小LIVE连接与保存/计算/重放设计

下列均为未来实现设计，不是本轮新增模块。保留现有probe和synthetic命令；不复制第二套计算内核、不循环probe。

| 当前位置（本基线行号） | 真实门槛 / 最小未来变化 |
|---|---|
| cli.py:26/52；offline.py:112/225/266 | collect只接受synthetic；OfflineConfig和metadata证据必须SYNTHETIC。**原类/入口/错误行为保留**；另立BoundedLiveConfig与显式默认禁网入口/独立离线reader |
| offline_core.py:233/267/349 | normalize与window两处单位门槛限定SYNTHETIC，Core不是入口级完全拒绝解析LIVE。新增一个默认仍为原synthetic谓词的不可变unit-binding policy，两处及高水位快照重建同传；LIVE仅接受审核resolver对象，不只换字符串/布尔 |
| offline_core.py:10/161/403；offline.py:638/658/692 | 当前版本/RECORDED_SYNTHETIC及synthetic-original含义不能冒充LIVE。新来源明确raw/adapter/normalization/feature/view/revision/证据版本，旧派生/重放validator不扩大到任意LIVE；同一Core仅显式provenance参数扩展，默认不变 |
| offline_storage.py:117/209/252/315/340/452/492 | 复用有界append、raw提交后游标、Parquet/校验/可信恢复；补全run预算reservation和静默watchdog/有界writer。Store(root)恢复会写errors/quarantine，不能直接称纯只读reader；新replay读取原日志时使用复用校验的只读入口，不在原件上自动恢复/改写 |
| probe.py:20/59/98/148；probe_transport.py:50/70/176/215 | 复用严格JSON/Decimal/metadata/字段解析及固定端点/单尝试/redirect/TLS安全模式；probe控制器与其2–60s/20条上限原样。新bounded transport要实测控制消息方向计数，不能以当前不可见autoping计数宣称满足预算 |

建议新增最小职责为`unit_binding.py`（审核证据→不可变绑定）、`bounded_live.py`（配置/状态机/时钟/预算/序号与发布控制）、`bounded_transport.py`（一次REST/WS及有方向的控制消息），名称待下一任务确定。Core/Store的必要小接口扩展及只读reader在同一有界任务中处理，原配置/测试/版本语义回归保护。不引入分布式系统、交易、其他指标或长期重连框架。

路径与状态：

1. **STARTING**：唯一新run目录、显式许可门槛、预算协调/磁盘、raw writer就绪；一次完整REST先保存、身份/审核证据匹配；一次WS握手与SUBSCRIBED控制提交后记真实UTC/monotonic观察起点，非“等第一笔成交才开始”。未知单位可以保留原字节，但首个BTC特征运行不得伪装RUNNING；配置未就绪即START_FAILED。
2. **RUNNING/durable-first**：完整应用消息接收时记录真实UTC接收、monotonic、E/T原值及ms精度、session/source/metadata与unit引用、原字节/hash/base64。单sequencer分配RAW/control全局序号，同时间按序号，不按E/T重排；raw/control可信提交后才解析、单位绑定、经济键去重和同一Core。meta/证据快照run内不可变，经济键不加session，source_epoch未核验不发明。
3. **截止与发布**：真实UTC网格记录FEATURE_CUTOFF(t、输入高水位及调度观察)，先把该前缀持久提交后计算`[t-W,t)`；窗口与未来horizon分离。结果落盘后，受控publisher确认已发布时取真实观察时间，形成FEATURE_PUBLISHED（available≥cutoff及所有依赖、cursor、逻辑hash/manifest）。确认时间与控制回执durable时间分开，不用计划时间/文件mtime/新重放墙钟替代。
4. **崩溃窗口诚实处理**：特征可见但发布回执未durable时，下次原raw可重算，历史发布时间仍UNVERIFIED/null。只在回执durable后宣称RECORDED_LIVE；不保证原子exactly-once发布。缺原发布日志的A2永不后补成当时可交易视图。
5. **故障与停止**：冲突/必需schema或身份变化/未知单位/时钟回拨/断线/队列或存储故障保留raw及原因，相关家族null或停止发布；SOURCE_SAMPLED仅聚合限制，不等于采集缺口，ID跳号不证明丢了相同笔数。坏UTF8/JSON先保存字节；超大/未完成帧无法取得完整字节时只保留可得受限证据并标truncated/UNKNOWN，不伪造完整raw。写盘失败不前移cursor、不继续健康发布；派生失败标DERIVED_INVALID，raw好坏独立。
6. **STOPPING**：冻结观察范围/最后高水位，及时关闭网络，限时保存已接收队列及控制、收尾派生/manifest。normal time完成与early cap/PARTIAL、STOP_FAILED分开。未提交尾部/失败raw保持UNCERTAIN_GAP，保留可信前缀/原件，不能靠移走文件消除风险或删唯一raw。
7. **离线replay**：禁止socket，使用同一raw/control、metadata/证据/config/版本快照，复用Core而非另读“最新”单位或网络。核对normalized/quarantine/feature数值、精确Decimal/ID/quality/null/cursor/历史available及两次摘要，同时用独立人工oracle，不能只比较两个同错摘要或Parquet物理字节。

Core当前保留历史/标准化与高水位重建状态，有界输入/编码/RSS限制必须覆盖这些数组及快照，不能只限制网络队列就宣称总内存有界；writer故障、控制消息审计、出版边界/内存压力仍是下一任务必须证明的实现行为，本轮没有执行它们。

## 5. 下一项单个有界离线实现任务及验收（只建议，不创建/启动）

先实现上述显式unit-binding/独立bounded来源入口、一次连接注入层、raw/control/同Core/发布/无网络replay与预算；只跑人工传输/固定Clock/保存样本的离线测试和代码PR。独立复核并经用户授权合并后，才另在Issue12提交固定代码/环境/额度的一次真实10min计划，取得新权限；**本轮不做后续实现或采集**。

| 原要求映射 | 下一任务独立oracle与验收（当前均NOT_RUN） |
|---|---|
| REQ-001/018；DEV-002/004；TEST-CFG-01/02、DOC-01 | 缺审批/hash/身份/时效/倍率/metadata来源错均拒绝特征启动；raw字节仍保留，q/nq/p字段单位按审核记录；任意BTC字符串/verified布尔/SYNTHETIC改标不能放行；旧synthetic/LIVE拒绝原测试仍成立 |
| REQ-004/009；DEV-004/008成交子集；TEST-TRADE-01/02/03、FEAT-01/02 | 人工买.30/.20卖.10：V=.60、Delta=.40、TI=2/3、Nagg=3；q/nq不双计、nq缺失null、p×q精确；重复计一次、冲突/缺口/null、健康空窗量0且TI null；不以A2样本当已验证实时Delta |
| REQ-003/011；TEST-TIME-01/02及CFG-01适用 | E/T很早但接收在右边界不能入窗；左闭右开/同刻序号/高水位/warmup/跨日；改future输入或horizon不改过去特征。实际发布晚于cutoff、缺回执available null，不补墙钟 |
| REQ-016/017；TEST-OPS-01/02/03 | fakeClock验证30/600/30与全程660和最早截止；每项资源cap界内/恰边界/+1固定小fixture；1000市场消息/s不误触发客户端控制cap，ping/PONG方向/预留close；断线429/418/451/redirect/隐式重试即止，不单试旁路、不重复握手 |
| REQ-002/010/017；DEV-003/006；TEST-STORE-01、REPLAY-01、OPS-03 | raw write/fsync/rename/manifest逐点失败游标不前移；raw好/派生坏对照、重复重开可信前缀/不确定尾部；所有临时/旧新共存计cap，静默queue年龄watchdog；writer超时有界ack/join、非可取消线程假保证 |
| REQ-010；DEV-006/008；TEST-REPLAY-01 | 同raw/control在线fake计算、两次离线replay逐行逻辑等值，再对照人工固定值/quality/null；原件hash不变、metadata/evidence引用正确，新旧namespace拒混；发布回执丢失保持UNVERIFIED |

上述编号来自原docs/00/05/07，只映射适用成交子集，不宣称BOOK/OI/FUND/LIQ/研究/成本验收通过。单任务的未知压力结果若不足，停止/下调待审预算或另述最小必要修订，不悄悄扩功能。

## 6. 本轮实际验证与可复现方式

新增[verify_plan.py](units_and_collection_plan/verify_plan.py)只读JSON/已有样本/可选本地原件，标准库且无下载/市场网络路径，安装socket阻断后执行；不调用Core/Store或probe。核查JSON重复键/语法、来源ID/引用、31个发布摘要、20样本原字符串及Decimal、原件/摘录/hash、单位候选runtime状态、时间相加、base64/单条/队列/控制/manifest/分区/free预算关系。人工`100×.30=30USDT`仅公式说明，不是A2实测特征。它不能自动证明文档语义推导，后者由上文所读正文承担。

在本任务worktree、既有Python3.12.13实际运行：

```sh
../r104-followup/.venv/bin/ruff check reports/round2/units_and_collection_plan/verify_plan.py
../r104-followup/.venv/bin/ruff format --check reports/round2/units_and_collection_plan/verify_plan.py
../r104-followup/.venv/bin/python -B reports/round2/units_and_collection_plan/verify_plan.py --output reports/round2/units_and_collection_plan/offline_checks.json --local-rest ../r201-a2-preflight/data/r201-probe/20261012-plan-a2-attempt01/rest-response.bin --contract-pdf data/r203/official-contract-specs.pdf --clearing-pdf data/r203/clearing-procedures.pdf
```

**实际最终均退出0**，结果[offline_checks.json](units_and_collection_plan/offline_checks.json)：20样本/31hash一致，原件及两PDF hash通过，时间660、请求权重1、raw最坏147405908B/单条88408B、全文件536870912B自洽；无市场请求。输出以exclusive create避免覆盖；复跑应换新output路径。其他机器没有本地原件/PDF可省略可选参数，明确输出相应NOT_RUN，不能假称完整原件核验。命令只是离线复现，不含真实运行许可。

首轮新脚本因禁网guard置于后续stdlib imports之前触发Ruff E402（退出1），算术核查退出0；随后仅调整新脚本stdlib import位置，保留guard在任何数据检查前。原脚本及初次输出留本地data，最终重新核查/直接Ruff通过；未改业务或固定预期。新CLI的初始字段核查回执也只留data，最终小脚本重新核对必要事实，不重复上传旧数据。

**该新核查脚本不在现有CI选择范围内，上述是本次本机单独执行，不冒充CI运行。** 文档/证据PR沿用既有离线CI，正常推送后等待精确head的Actions，run/状态/实际统计在PR正文及交付回执给出；不重装环境/重做R1总审查，绿色CI不代表本文候选采集器/真实600秒已通过。

## 未解事项与结束

本任务的证据链/预算草案/最小设计交付完成；单位文档结论明确，候选记录和设计待独立复核，均未应用到runtime。未来真实启动还需要批准的单位绑定/实现验收和可信共享出口窗口约束；未满足则不启动，不用当前A2的低用量头猜余量。A2的历史协议PASS、文档确认单位、运行时绑定尚未实现、持续预算UNVERIFIED及10min/24h NOT_RUN必须分别记录。

只创建本任务报告PR，Refs #17、Refs #12；不合并、不关闭Issue12、不创建/启动下一任务、不执行任何实采或重新probe、不修改网络/代理/TLS与用户改动。完成后停止等待独立复核。

## 首轮既有CI发现（不在本任务修代码）

[Run38329799046](https://github.com/shukehi/btc_microstructure/actions/runs/38329799046) 对证据head4cc3202…实际FAIL：默认316通过/1失败（0error/skip），独立15/9/8/4通过；十三项中pytest退出1、其余十二项0。新文档核查脚本未由该CI执行，不能把它的本机PASS用来覆盖这次失败。小型精确记录见[ci_observation.json](units_and_collection_plan/ci_observation.json)，完整原日志/JUnit下载保留本地。

**R203-CI-01 / MEDIUM：原probe时限反算的浮点精确值。** `test_cli_rest_total_seconds_reaches_limits_before_transport` 第168行期望15.0，实际15.000000000000007。业务代码与基线无差异；run_probe用绝对起点加配置形成截止，再以截止减起点反算rest_effective并model_copy传入transport。纯离线算术起点49.9的`(49.9+15)-49.9`即可得到同一值，无需运行probe/市场请求。该误差远小于实际计时精度，不据此声称严重超额/网络根因，但确实破坏了精确参数断言并造成CI不稳定。

最小建议是分开绝对截止与传给transport的时长：未被全局截断时保留原配置精确值，并将反算有效值夹在配置上限内；不靠放宽原测试制造通过。本轮不实现该建议、不修改业务/旧测试/工作流，不为洗绿重复失败run。此处新增CI观察会产生新的报告提交和正常CI，后续如通过也不代表R203-CI-01已修复；两次结果均在PR正文提供，停止等待独立复核决定后续。单位证据和预算算术结论与该已有代码问题分开。
