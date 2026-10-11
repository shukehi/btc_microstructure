# 01 · Binance 数据契约

版本：0.1｜状态：接口资料已核验，实际连接与样本验证待 M0

## 1. 规范边界

本文件约定**内部表结构**，不是逐字复制交易所接口。接口字段映射取自官方资料；表名、主键、质量标志和处理行为为本项目设计。所有 REST 响应必须保存实际 URL、参数、请求起止时刻、HTTP 状态及可用的限流响应头，不保存密钥。

## 2. 连接与订阅清单

永续高频盘口与普通市场流使用分离的入口，不依赖旧式无路由连接能否兼容。[S01](08_sources_and_decisions.md#source-s01)

```text
永续 PUBLIC：wss://fstream.binance.com/public/stream
永续 MARKET：wss://fstream.binance.com/market/stream
永续 REST：  https://fapi.binance.com
现货 WS：    wss://stream.binance.com:9443/stream
现货 REST：  https://api.binance.com
```

| 内部 source_stream | 数据来源 | 启用级别 |
|---|---|---|
| um_agg_trade | MARKET `btcusdt@aggTrade` | 必需 |
| um_bbo | PUBLIC `btcusdt@bookTicker` | 必需 |
| um_depth | PUBLIC `btcusdt@depth@100ms` | M2 必需 |
| um_mark | MARKET `btcusdt@markPrice@1s` | 必需 |
| um_liquidation_snapshot | MARKET `btcusdt@forceOrder` | 补充，可降级 |
| spot_agg_trade | 现货 `btcusdt@aggTrade` | 必需 |
| spot_bbo | 现货 `btcusdt@bookTicker` | 必需 |

现货与永续聚合规则不同，统一的是价格、数量、方向字段，而不是消息粒度。[S02](08_sources_and_decisions.md#source-s02) [S06](08_sources_and_decisions.md#source-s06)

| REST 用途 | 路径 | 调度默认值／行为 |
|---|---|---|
| 永续元数据 | `/fapi/v1/exchangeInfo` | 启动、每小时；缓存版本 |
| 永续簿快照 | `/fapi/v1/depth?symbol=BTCUSDT&limit=1000` | 初始化、失效恢复；统一限速 |
| 当前 OI | `/fapi/v1/openInterest?symbol=BTCUSDT` | 每 5 秒；每源最多一个在途请求 |
| 标记/指数补充 | `/fapi/v1/premiumIndex?symbol=BTCUSDT` | 启动和流异常诊断；不冒充连续 WS |
| 历史费率 | `/fapi/v1/fundingRate?symbol=BTCUSDT` | 初次回填；随后按结算计划补查 |
| 费率机制调整 | `/fapi/v1/fundingInfo` | 启动、每 15 分钟 |
| 历史 OI 统计 | `/futures/data/openInterestHist` | 可选离线数据，单独表，不填秒级缺口 |
| 永续成交补回 | `/fapi/v1/aggTrades` | 按 ID 和受限时间范围补回，使用统一限速 |
| 现货元数据 | `/api/v3/exchangeInfo?symbol=BTCUSDT` | 启动、每小时 |
| 现货成交补回 | `/api/v3/aggTrades` | 单独适配器；不套用永续参数限制 |

永续 REST 路径来源为 S04；现货历史入口及公共文件关系见 S07，现货元数据见[S13](08_sources_and_decisions.md#source-s13)。轮询间隔是项目默认值，不能被理解为数据实际每 5 秒一定更新，也不是交易所批准的调用预算。[S04](08_sources_and_decisions.md#source-s04) [S07](08_sources_and_decisions.md#source-s07)

M0 必须核验当前参数范围、权重和共享限额，生成 endpoint_budget.json。未通过时不启用持续采集。仅可由该预算文件和响应头决定限速，不从其他市场复制常数。

## 3. 公共事件头与原始日志

原始日志 `raw_events` 保留完整 payload，不因解析失败而丢失。标准化记录关联原始位置。

| 字段 | 类型／空值 | 约定 |
|---|---|---|
| schema_version | string，必填 | 本项目数据结构版本 |
| adapter_version | string，必填 | 来源解析与映射版本 |
| venue | string，必填 | `binance` |
| market_type | enum，必填 | `um_perpetual` / `spot` |
| instrument_id | string，必填 | 如 `binance:um_perpetual:BTCUSDT` |
| source_stream | string，必填 | 来源清单中的名字 |
| source_origin | enum，必填 | `live_ws` / `rest_poll` / `rest_backfill` / `archive` |
| source_time_unit | enum，必填 | `ms` / `us` / `ns` / `none` |
| ts_event_ns | int64，可空 | 外部事件时间；只做单位换算 |
| ts_trade_ns | int64，可空 | 外部成交时间；非成交数据可空 |
| ts_receive_ns | int64，可空 | 实时接收完整消息时的 UTC 墙钟；历史未知则空 |
| ts_download_ns | int64，可空 | 补回或归档下载时间；不是历史接收时间 |
| receive_monotonic_ns | int64，可空 | 同一进程内耗时与接收顺序辅助；不跨机器比较 |
| session_id | string，必填 | 每次连接或导入批次唯一 |
| ingest_sequence | int64，必填 | 单一采集调度器递增序号 |
| raw_ref | string，必填 | 原始文件标识、偏移或行号 |
| metadata_version | string，可空 | 合约元数据版本；未确认时显式未知 |
| quality_flags | list[string]，必填 | 正常为空数组，不用 null 表示正常 |

所有 ns 字段统一为 UTC Unix 纳秒整数，但源精度仍可能只有毫秒；换算不增加信息。原始字符串字段在 raw 保留。接收时间减事件时间称为 `observed_delivery_lag`，不称为精确网络延迟，因为其中还包含时钟误差和服务端处理。

## 4. 合约元数据 `instruments`

主键 `(instrument_id, metadata_version)`；记录 `effective_from_observed_ns`、原始响应摘要和来源。

必需列：`symbol`、`base_asset`、`quote_asset`、`margin_asset`（现货可空）、`contract_type`、`status`、`price_tick`、`quantity_step`、`minimum_quantity`、`quantity_unit`、`oi_unit`、`unit_evidence_id`。

本项目预期合约是 BTC/USDT 线性永续；启动时必须校验，不允许仅因 symbol 名字相似就接入反向、交割或其他结算合约。数量和价格对齐使用实际 tick/step；不将显示小数位数当成 tick size。

**单位证据规则：** 官方字段未明确的单位，标为 `UNKNOWN`；不得由示例数字大小猜测。比率类 OI 特征可在同一来源、同一版本和稳定口径下计算；绝对量跨表比较必须有单位证据。

## 5. 成交 `trades`

### 5.1 字段与映射

| 内部字段 | 来源／定义 |
|---|---|
| record_kind | `AGG_TRADE`；本版不冒称逐笔原始成交 |
| agg_trade_id | `a`，以字符串或 int64 保存 |
| first_trade_id / last_trade_id | `f` / `l`，可空 |
| price_quote | `p`，Decimal |
| quantity_base | `q`，在 BTCUSDT 数量口径核验后写入 |
| normal_quantity_base | 永续可选 `nq`；缺失为 null，不能用 q 代替 |
| notional_quote | `price_quote × quantity_base`，单位 USDT |
| buyer_is_maker | `m`，必须为 boolean |
| aggressor_side | `m=true → SELL`；`m=false → BUY` |
| ts_event_ns / ts_trade_ns | `E` / `T` 换算；归档缺失 E 则空 |
| symbol_type_raw | 可选 `st` 原值；与目标合约元数据交叉检查 |
| source_coverage | 明确该流的成交覆盖口径 |

永续 `q` 包含涉及 RPI 的市场成交，`nq` 是不涉及 RPI 的数量；不能将 q 与 nq 都加入总成交量。该聚合流不能被当成覆盖保险基金和 ADL 的完整事件流水。[S02](08_sources_and_decisions.md#source-s02)

### 5.2 去重与一致性

强唯一键为 `(venue, market_type, symbol, record_kind, agg_trade_id)`，协议发生 ID 重置或口径断裂时增加已核验的 `source_epoch`。不能用连接 session_id 区分同一笔重发成交。

同键同内容：保留 raw，标准化层只计一次。同键不同价格、数量或方向：生成 `DUPLICATE_CONFLICT`，进入隔离表，禁止任选后到值覆盖。

ID 跳跃是需要调查的完整性信号，不自动证明丢失了同样数量的原始成交。补回失败后记录已知缺口。WS 和历史补回若在新增字段或聚合口径上不一致，保留来源，不静默合并。

`agg_message_count` 只表示聚合记录数。`last_trade_id-first_trade_id+1` 可以保留为 ID 范围宽度，但本版不把它作为保证准确的原始成交笔数。

## 6. 最优报价 `bbo`

字段：`bid_price`、`bid_quantity`、`ask_price`、`ask_quantity`、`update_id`、`ts_event_ns`、`ts_transaction_ns`、`ts_receive_ns`、`quote_clock`、`validity`。映射采用 `b/B/a/A/u`；永续可有 `E/T`。[S03](08_sources_and_decisions.md#source-s03)

当前现货 bookTicker 公开结构没有 E/T；本项目保持来源时间为空，以接收时间对齐，不从邻近成交猜测时间。[S06](08_sources_and_decisions.md#source-s06)

报价必须满足 bid>0、ask>bid、两侧数量>0。否则保存记录并标记不可用。没有新报价不必然是断线；超过预设年龄时只将报价特征置为 STALE，并分别记录连接健康。

bookTicker 的 update_id 不能直接套用 diff-depth 的 pu 连续性规则。与 L2 的交叉检查必须考虑各自接收时间，不能因不同步的瞬时差异立刻认定来源损坏。

## 7. 订单簿 `book_updates` / `book_snapshots`

增量每个消息一行，保留 `U`、`u`、`pu`、`bids[]`、`asks[]`、事件和事务时间。数组中的数量为该价格档位的新绝对数量。整条消息原子应用，不能将每档按价格排序后当成时间上独立的市场事件。[S05](08_sources_and_decisions.md#source-s05)

本版标准深度不包含 RPI 挂单；记录 `book_scope=standard_visible_ex_rpi`。即使成交 q 包含 RPI，也不要求每笔成交都能与标准深度减少一一对应。[S03](08_sources_and_decisions.md#source-s03)

快照字段：`snapshot_id`、`book_epoch`、`last_update_id`、`bids[]`、`asks[]`、`requested_limit`、`snapshot_available_ns`、`known_bid_floor`、`known_ask_ceiling`、`source_kind`、`raw_ref`。

`source_kind` 区分 REST 初始化快照和本地重放检查点。只从有效簿创建检查点；检查点还必须记录最后已应用序号、精度元数据及其输入清单。

REST 快照只覆盖有限档位。单独观察到远端新档位不能证明中间未知区域完整。深度特征需要验证其整个价格区间均处于已知覆盖区；不足则 null。

## 8. OI `open_interest_observations`

必需列：`oi_native`、`oi_unit`、`oi_base`（可空）、`source_observation_ns`、`ts_request_ns`、`ts_receive_ns`、`unit_evidence_id`。原始 `openInterest` 保留为 Decimal；来源时间使用响应 `time`。[S04](08_sources_and_decisions.md#source-s04)

派生名义金额只能命名 `oi_notional_usdt_estimated`，同时记录估值价格及其时间。`oi_native` 尚未确认可换算 BTC 时，该金额保持 null。不能把价格升高导致的估值增长解释成新增合约。

相同来源时间和相同数值重复返回时，新增一个轮询收据而不是新增一个独立 OI 更新。分别输出 `poll_success_age_ms` 与 `observation_age_ms`；频繁轮询不保证数据新鲜。

历史 OI 统计最小周期为 5 分钟，当前文档给出的可回看范围为最近一个月。它使用独立表 `open_interest_history` 和 period 字段，绝不插值成为秒级真实观测。[S04](08_sources_and_decisions.md#source-s04)

## 9. 价格与资金费率

### 9.1 `mark_index_observations`

保存 `mark_price=p`、`index_price=i`、`funding_rate_observed=r`、`next_funding_time=T`；外层 E 是事件时间。字段 P 不是指数价格，也不是资金费率。r 是该时刻公开流中的观测值，不命名为已结算费率或保证准确的预测费率。[S02](08_sources_and_decisions.md#source-s02)

### 9.2 `funding_settlements`

以 `(instrument_id, funding_time)` 为候选键，保存实际返回的 `fundingRate`、关联 `markPrice`、首次取得时间、修订版本及可选来源附加字段。相同结算时点不同费率必须记录修订，不能覆盖审计历史。

### 9.3 `funding_rules`

保存 `fundingIntervalHours`、上下限和首次观测时间。fundingInfo 是调整信息，未返回某合约不能自动证明其周期为 8 小时。周期证据可以来自核验的合约规则或连续实际结算时点；未知时禁止按 8 小时换算。[S04](08_sources_and_decisions.md#source-s04)

费率使用小数，例如 0.0001 表示 0.01%。费率观测、已结算费率和机制规则必须分表，不能把结算历史回填成过去已知的预测值。

## 10. 强平 `liquidation_snapshots`

保留 `side=S`、`order_type=o`、`time_in_force=f`、`original_qty=q`、`order_price=p`、`average_fill_price=ap`、`last_fill_qty=l`、`cumulative_fill_qty=z`、`status=X`、`order_trade_time=T`，以及外层 E。[S02](08_sources_and_decisions.md#source-s02)

内部可增加 `liquidated_position_side`：SELL 通常映射 LONG、BUY 通常映射 SHORT；它不是市场所有账户的持仓明细。输出仍保留原始方向和类型，不把强平执行一律称为普通市价单。

**覆盖冲突：** 接口页写每个 1000ms 窗口最新快照；变更日志写改为最大一笔。两者都不支持完整强平总量，本版保存 `selection_rule=DOCUMENT_CONFLICT`，只做观测快照统计。冲突见 ISSUE-001。[S02](08_sources_and_decisions.md#source-s02) [S09](08_sources_and_decisions.md#source-s09)

公开字段没有可依赖的唯一强平订单 ID。相同 payload 的摘要可用于消除字节级重发，但不能保证不同快照属于不同订单。禁止跨快照无条件累加 z；q 是原始委托量，不是已成交量。

允许的单条派生量：`snapshot_cumulative_notional_usdt = z × ap`。z=0 时为 0；z>0 且 ap≤0 时为 null。窗口仅默认输出观测快照数与单条名义值的最大值，不输出总强平量或其占总成交量比例。

## 11. 历史文件与缺失数据

归档导入必须保留文件路径、校验和、下载时点、来源时间单位和覆盖字段。官方公开文件为按日/月提供的数据；现货归档从 2025-01-01 起使用微秒时间，不能与毫秒 WS 无条件采用同一解析器。[S07](08_sources_and_decisions.md#source-s07)

历史文件通常没有真实本地接收时间。`ts_receive_ns=null`，`ts_download_ns` 写本次下载时间；只有显式模拟的延迟模型才能构造 `simulated_available_ns`，且输出必须标注 simulated，不得冒充真实观测。

不能从 OHLCV、部分深度汇总或当前快照恢复过去完整事件级 L2。无历史 L2 的时期不产出伪造 OFI、挂撤单或吸收确认特征。

## 12. 质量标志

| 标志 | 含义 | 默认影响 |
|---|---|---|
| WARMUP | 窗口未积累完整历史 | 该窗口特征 null |
| GAP_KNOWN | 已知采集缺口 | 受影响流量窗口 null |
| SEQUENCE_INVALID | 簿序列无效 | 禁止簿派生指标 |
| COVERAGE_INSUFFICIENT | 目标价格范围覆盖不足 | 对应深度与 OBI null |
| STALE | 观测超过年龄阈值 | 对应状态或报价不可用 |
| CLOCK_UNCERTAIN | 墙钟跳变或同步不可靠 | 禁止跨市场 lead-lag 结论 |
| UNIT_UNKNOWN | 数量口径未确认 | 禁止单位相关换算 |
| SOURCE_SAMPLED | 来源本身是采样／聚合 | 保留限制，不等于采集故障 |
| DUPLICATE_CONFLICT | 同键出现不同内容 | 隔离、调查 |
| SOURCE_SCHEMA_CHANGED | 字段类型或关键语义改变 | 隔离或停止对应适配器 |
| HISTORY_ONLY | 没有真实接收时间 | 不进入真实 as_received 回测 |

正常窗口的零量与缺失窗口必须在表中可区分。每个特征独立携带质量，不因强平缺失而删除有效的成交特征。
