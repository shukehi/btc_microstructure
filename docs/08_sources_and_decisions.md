# 08 · 资料来源、决策与待核验事项

版本：0.1｜资料核验基准：2026-10-10｜使用英文一手资料

## 1. 证据等级

**USER_CONFIRMED**：对话中已经明确的需求。  
**DESIGN_DEFAULT**：本项目建议的可修改设计值。  
**DOC_VERIFIED**：本次读取过官方资料，确认其文档描述。  
**RUNTIME_VERIFIED**：必须实际连接并保存样本才能达到；本次没有此类核验。  
**HYPOTHESIS**：待数据检验的研究解释，不能作为事实或收益承诺。

引用支持外部接口或理论定义，不代表引用者验证了本系统代码。下列链接为来源定位，不包含第三方全文副本。官方页面会更新，未来运行必须重新记录核验时间与实际协议版本。

## 2. 来源登记

<a id="source-s01"></a>
### S01 · WebSocket routed endpoints

机构：Binance Developer Docs。  
标题：Important WebSocket Change Notice — Base URL Split & Migration。  
链接：[官方说明](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Important-WebSocket-Change-Notice)

支持：USDⓈ-M 的 PUBLIC/MARKET/PRIVATE 路由划分。本项目只使用前两者。旧URL兼容细节在页面不同段落不完全一致，本版避免依赖旧URL是否继续可用。

<a id="source-s02"></a>
### S02 · Futures market streams

机构：Binance Developer Docs。  
标题：Market — Futures (USDⓈ-M) WebSocket Market Streams。  
链接：[官方接口](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/market)

支持：聚合成交、q/nq/m与时间字段、mark/index与资金观测、强平快照字段。聚合成交不是原始逐笔或所有强制事件；强平选择规则与S09冲突，见ISSUE-001。

<a id="source-s03"></a>
### S03 · Futures public order-book streams

机构：Binance Developer Docs。  
标题：Public — Futures (USDⓈ-M) WebSocket Market Streams。  
链接：[官方接口](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/public)

支持：BBO与增量深度字段、100ms选项、标准簿的RPI排除口径。本版不启用RPI专用簿。

<a id="source-s04"></a>
### S04 · Futures market-data REST

机构：Binance Developer Docs。  
标题：Market Data — Futures (USDⓈ-M) REST API。  
链接：[官方接口](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)

支持：元数据、快照、当前OI、历史OI、资金历史与机制信息。历史OI最小5分钟、最近一个月；不能替代秒级数据。调用预算仍须M0逐端点核验。

<a id="source-s05"></a>
### S05 · Futures local-book synchronization

机构：Binance Developer Docs。  
标题：How to manage a local order book correctly。  
链接：[官方算法](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly)

支持：快照衔接、U/u/pu、绝对数量与零量删除。该算法是UM适配器依据，不自动适用于现货。

<a id="source-s06"></a>
### S06 · Spot WebSocket streams

机构：Binance Developer Docs。  
标题：Spot WebSocket Market Streams。  
链接：[官方接口](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/ws-streams/~)

支持：现货aggTrade、bookTicker及其字段；现货bookTicker不提供可用于本版源事件对齐的E/T字段。缺失值保留，不制造时间戳。

<a id="source-s07"></a>
### S07 · Public historical files

机构：Binance 官方 GitHub 仓库。  
标题：Binance Public Data。  
链接：[官方仓库](https://github.com/binance/binance-public-data)

支持：日/月归档、校验和、对应接口和现货归档微秒时间变化。文件到达时间不是原来的行情接收时间；本版不假定归档中有所有新WS字段。

<a id="source-s08"></a>
### S08 · Futures limits and errors

机构：Binance Developer Docs。  
标题：General Info。  
链接：[官方规则](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info)

支持：权重与响应头、429退避、418限制及接口安全类型。不能将“公开行情”泛化成所有相关REST路径均无认证要求。

<a id="source-s09"></a>
### S09 · Futures change log

机构：Binance Developer Docs。  
标题：Change Log。  
链接：[官方变更日志](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/change-log)

支持：当前强平说明存在从latest到largest的变更记载，而接口页仍给出另一描述。保留该矛盾，不武断宣布运行时实际选择规则已经确定。

<a id="source-s10"></a>
### S10 · Order Flow Imbalance definition

作者：Rama Cont、Arseniy Kukanov、Sasha Stoikov。  
标题：The Price Impact of Order Book Events。  
链接：[作者论文HTML](https://arxiv.org/html/1011.6402v3)

支持：基于最优报价与数量变化的OFI公式。论文研究对象不是本项目BTC系统；本版只借用定义，不声称其收益或解释力可以直接迁移到BTC。

<a id="source-s11"></a>
### S11 · Open Interest definition

机构：CME Group。  
标题：Open Interest。  
链接：[交易所教育资料](https://www.cmegroup.com/education/courses/introduction-to-futures/open-interest)

支持：OI是未平仓合约总量，区别于成交量。本项目关于“聚合OI不能唯一还原主动交易开平仓”的限制，是结合可用字段所作的识别边界判断。

<a id="source-s12"></a>
### S12 · Futures connection lifecycle

机构：Binance Developer Docs。  
标题：Websocket Market Streams / Connect。  
链接：[官方连接说明](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Connect)

支持：组合消息封装、连接寿命、ping/pong及消息限制。轮换时间23小时是本项目默认值，不是交易所强制值。

<a id="source-s13"></a>
### S13 · Spot metadata

机构：Binance Developer Docs。  
标题：General — Spot REST API。  
链接：[官方接口](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/general)

支持：现货exchangeInfo、symbol参数与元数据/filters结构。运行时读取实际值，不把文档示例精度写死。

## 3. 架构决策记录

| ADR | 决策 | 理由 | 被排除的做法 |
|---|---|---|---|
| ADR-001 | 使用官方来源适配器和统一内部表 | 区分来源事实与内部算法 | 把不同交易所原字段直接混表 |
| ADR-002 | 原始/标准化/特征/标签分层 | 可重算、可审计、防泄漏 | API消息直接生成买卖指令 |
| ADR-003 | 默认as_received，事件修订另存 | 保留当时可用信息边界 | 用历史完整数据假装实时已知 |
| ADR-004 | OI描述与多空解释分开 | 公开字段不能唯一识别开平仓 | new_long_volume伪字段 |
| ADR-005 | 强平只做观测快照统计 | 采样、无稳定订单身份且文档冲突 | 所有z直接求和当总量 |
| ADR-006 | OFI从有效观测簿状态计算 | 定义明确且可测试 | 用Trade Delta冒充OFI |
| ADR-007 | 期限和窗口独立配置 | 用户要求不预设最优期限 | 先确定一个“最佳”周期 |
| ADR-008 | 成本缺参数时关闭净收益分析 | 防止零成本伪优势 | 默认为零手续费／零资金费 |

## 4. 未关闭事项

| issue_id | 内容 | 影响／默认处理 | 关闭所需证据 |
|---|---|---|---|
| ISSUE-001 | 强平选择规则文档冲突 | 只用观测快照；不阻塞其他模块 | 一致官方说明；必要时向交易所确认；有限样本不能证明全量 |
| ISSUE-002 | OI绝对单位需绑定实际合约证据 | native比例可研究；BTC绝对换算暂不启用 | 官方单位说明或可核验合约规格 |
| ISSUE-003 | 实际网络访问与端点预算未测试 | M0前不宣布可运行 | 只读probe记录、样本与预算清单 |
| ISSUE-004 | 真实合约tick/step与资金周期未读取 | 运行时校验，未知值保持null | 元数据与周期证据 |
| ISSUE-005 | 用户部署硬件/系统未记录 | 参考环境只是默认值 | 目标环境清单与实测指标 |
| ISSUE-006 | WS与历史文件新字段及聚合一致性 | 版本化、不盲目去重覆盖 | 同期样本比较和字段清单 |
| ISSUE-007 | 实际费率、模拟数量与延迟未设 | 毛收益研究可做；净收益关闭 | 明确情景参数或获授权的真实成本资料 |

这些事项均无须在此时追加询问用户才继续写文档。它们是后续实现的明确核验任务，不是隐藏假设。

## 5. 本版纠正的早期讨论简化

OI增加不直接等于新增多头或空头；CVD量差不等于盘口OFI；公开强平不是完整爆仓流水；L2不等于L3；交易所事件时间不等于数据可用时间；简单永续/指数价差不等于官方Premium Index。

此前对话中的吸收打分和示意胜率不视为成熟算法或实证结果。任何性能、收益和运行可靠性必须在实现后分别生成证据。
