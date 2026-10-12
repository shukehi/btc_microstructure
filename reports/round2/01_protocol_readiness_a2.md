# R2-01-A2：第二次有界真实探测续报

任务：[Issue #12](https://github.com/shukehi/btc_microstructure/issues/12)。[A2精确计划6121584764](https://github.com/shukehi/btc_microstructure/issues/12#issuecomment-6121584764)、[预检复核6121694427](https://github.com/shukehi/btc_microstructure/issues/12#issuecomment-6121694427)、[用户授权记录6134591088](https://github.com/shukehi/btc_microstructure/issues/12#issuecomment-6134591088)。这次授权针对A2当前本机Mac的一次运行，不沿用A1。

**实际执行代码SHA：`fa22876158ed1cbfca8b3d0c839b3de03ae91989`。** 证据分支`validation/r2-01-a2-evidence`从执行后核对仍相同的最新main建立；证据提交SHA/PR/对应Actions在PR正文与交付答复单独列出，不能冒充新的被执行业务版本。

## 分项实际结果

| 项目 | 本次结论 |
|---|---|
| 本次有界probe | **PASS，CLI退出0**；约 **11.494633791968226s**，只运行一次；取得20条消息后结束，不等待满30秒 |
| REST exchangeInfo | **PASS**：HTTP200、完整正文 **1,152,517B**，body_complete=true / COMPLETE / truncated=false；BTCUSDT目标字段和filters通过现有工具核验 |
| WS BTCUSDT aggTrade | **PASS**：REST完整及metadata通过后才握手；工具记录101，实际20帧/20条组合数据，全部解析通过，正文合计 **4,082B** |
| 合计预算/计数 | 正文合计 **1,156,599B < 2,097,152B**；logical_calls=transport_attempts={rest:1,ws:1}；retry_count=0、blocked_retry_count=0；cleanup PASS |
| 数量单位 | **UNKNOWN**：工具metadata与每条inspection保持UNKNOWN；baseAsset=BTC和短样本字段不自动成为q绝对单位的直接证明 |
| 持续预算 | **UNVERIFIED_FOR_CONTINUOUS_COLLECTION**，continuous_collection_enabled=false；一次成功不证明长期额度、同出口占用或持续可靠性 |
| 实际持续采集 / LIVE采集→保存→特征重放 / 24h | **NOT_RUN**；现有collect/replay为synthetic-only，未改标LIVE、未计算实测Delta |
| 完整M0/M1/M2 / 其他来源 / 研究收益 | 未验收/未验证，不因这两个端点的短样本PASS升级 |

本次PASS是两个限定公共接口的有界协议样本结论。A1的HTTP200/425307B不完整正文/BLOCKED仍为其历史事实，旧记录不回写。A2完整REST预算15s与本次约6.872s的REST完成路径相符，但不同时间的A1/A2不是受控网络实验，不能归因历史超时根因、将预算变化认定为唯一成功原因或保证未来成功。

## 一次执行与原始时序

执行前只读取授权、引用计划/复核及固定目录已提交AGENTS，核对代码/导入/解释器/27项锁依赖/配置/目录条件；没有重新做整轮预检或离线总审查。最新Issue12没有A2执行回执、远端无A2证据PR，指定输出和调用回执目录均不存在。随后以exclusive mkdir建立一次性调用标记，保存后只调用一次原有CLI；该标记永久保留，不能把重复收到授权当作新的一次额度。

当前本机Darwin27.0.0 / arm64 / Python3.12.13，既有venv复用、未重装依赖；局部PYTHONPATH=src使cli/probe/probe_transport来自固定A2 checkout。27个lock条目全部匹配，lock SHA-256为`7fe9043dac17556e80030e61786aaeb9cd95dce480661b01c8e207064defa0bc`；配置摘要仍`b037004b544ca834855821374bd65520a535b509c0b952d9c3f37a97a30d3b43`。

实际cwd：`<repo>/data/r201-a2-preflight`。**下面是已执行命令记录，不是再次执行指令；本次A2一次授权已消耗。**

```sh
PYTHONPATH=src ../r104-followup/.venv/bin/python -m btc_microstructure probe config/research.example.yaml --allow-network --seconds 30 --rest-total-seconds 15 --messages 20 --max-bytes 2097152 --output data/r201-probe/20261012-plan-a2-attempt01
```

| 记录 | UTC / 数值 |
|---|---|
| 外层命令开始 / 结束 | 2026-10-12T23:33:27.707164Z / 23:33:39.553181Z |
| probe开始 / 结束 | 2026-10-12T23:33:27.991704Z / 23:33:39.485982Z |
| 响应头观察 | 2026-10-12T23:33:32.635892Z |
| 完整REST保留观察时刻 | 2026-10-12T23:33:34.863713Z |
| WS请求开始 | 2026-10-12T23:33:34.881067Z |
| 第1 / 第20条WS本地接收 | 2026-10-12T23:33:38.032042Z / 23:33:38.890376Z |
| REST请求→响应头 / 请求→完整保留 | **4.643918s / 6.871739s**，由UTC记录差计算，不称纯网络连接耗时 |
| probe elapsed | **11.494633791968226s**，单调时钟；与外层启动/写报告时间分开 |

显式配置为总30s、REST15s、connect/idle5s、20消息、2MiB、单WS消息64KiB。报告实际effective_timeouts为work29s、REST15s（cap=REST_TOTAL）、WS connect5s（cap=CONNECT）、WS receive5s（cap=IDLE）；清理最多1s。REST15s包含连接、响应头及完整正文，不按块续期，WS只用同一全局剩余工作域。未触发超时，timeout_diagnostics保留NOT_STARTED/NOT_EXPIRED表示没有超时诊断事件，不能拿它否定实际REST/WS操作曾发生。

固定REST为GET `https://fapi.binance.com/fapi/v1/exchangeInfo`、params={}；WS为`wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade`，URL仅订阅一个流。请求处理器计数不是TCP包/成功发包数量。工具的101来自成功WS握手API；成功握手headers未由aiohttp公开WS API提供，因此空rate_headers不证明网络未返回头。未修改网络、代理、TLS、重定向或单尝试保护，未额外发送time/ping/DNS诊断或访问账户/签名/下单接口。

## 字段、单位与预算证据

完整REST工具核验：BTCUSDT / BTC / USDT / USDT / PERPETUAL / TRADING；tickSize=`0.10`、stepSize/minQty=`0.001`。不将pricePrecision/quantityPrecision当tick/step。20条组合消息的stream均为btcusdt@aggTrade，payload仅含当前公开aggTrade字段，unknown_fields和unknown_wrapper_fields全部为空；每条保留原字符串价格/数量、E/T、a/f/l、m及st，来源时钟记ms。聚合消息数不是原始逐笔数，不从f/l跨度推算准确逐笔总量。

真实metadata中的rateLimits已观察并保存：REQUEST_WEIGHT每分钟2400；另有ORDERS每分钟1200/10秒300。这些是本次响应快照；ORDERS不能套用成公共REST调用预算。REST白名单头`x-mbx-used-weight-1m:1`是当时IP累计用量，不保证专属于本进程、不是未来剩余额度或持续额度承诺。现有文档权重1沿用A2预检资料，本轮没有再次请求文档或加压测限流。

仍缺：持续请求/订阅计划、同出口并行占用、长期覆盖/限额变化/峰值和资源实测。此次report/budget刻意保留UNKNOWN单位及UNVERIFIED持续预算，不手工回写它们；没有采集或收益结论。

## 原件、公开副本与筛查

新证据目录：`reports/round2/protocol_readiness/a2/20261012-plan-a2-attempt01/`。所有新协议样本均 **LIVE**；没有改标SYNTHETIC。

- 原始输出完整留在A2 checkout的`data/r201-probe/20261012-plan-a2-attempt01`，调用标记/原始stdout/stderr在同级`20261012-plan-a2-invocation`。A1原目录、旧报告与历史失败不覆盖。
- **1,152,517B完整rest-response.bin只留本地、不上传**；SHA-256 `d5ae0e3682f28512d34ec9ab20131a964df6c76118b8a29e12a76e7b57cbd610`。公开manifest明确local-only；原report仍记录其文件名/hash。远端审查未取得原件时只能核对公开声明，不能冒称独立复算了该原件。
- `protocol_probe_report.json`与`endpoint_budget.json`是原始工具记录逐字节副本；20份小型WS原始帧合计4,082B，逐条检查公开字段及所有hash后按原字节发布。未发完整大REST或工程ZIP。
- `rest-btcusdt-excerpt.json`从已完成的本地REST选取BTCUSDT必需资产/状态/precision/filters及rateLimits，显式标为DERIVED_PUBLIC_METADATA_EXCERPT、来源LIVE/hash及“非逐字节原件”；不冒充完整REST样本或单位证明。
- `invocation.redacted.json`只脱敏cwd、去掉私人用户文件digest，保留准确argv/时间/退出码/授权链；明确与原调用回执不同。execution-conditions.json只含必要非敏感环境和一致性结果，不转储环境变量/网络/代理配置。
- 原样本hash、工具report/budget、20个WS样本和本地大REST全部核对一致；公开数据无未知payload字段/认证头/cookie/私有账户或私人路径。广义筛查先命中authorization_comment字段名而退出1，人工确认它是公开GitHub授权评论编号，不是HTTP认证头或凭据；原始内容不改，后续离线核查退出0。该筛查过程没有重跑probe，详见screening-notes/evidence-verification。
- 模式扫描加人工复读不是普遍无秘密保证；局部原件/脱敏副本/派生摘录的发布策略和hash在local-originals-manifest、publication-manifest中分开记录。

## 检查、需求映射与交付

| 检查 / 需求 | 实际证据与结果 |
|---|---|
| 执行前一致性、输出唯一性 / REQ-018、DEV-002 | 固定代码/27-lock/导入/配置/模型和未执行目录核查退出0；独占调用标记保留，实际CLI仅一次 |
| 本次真实协议 / TEST-DOC-01适用、REQ-003/018 | REST完整及WS20条通过，LIVE原字节/hash/时刻可追溯；不当成全部来源M0 |
| 权限/预算 / REQ-016/017、TEST-OPS-01/02适用 | 固定公共端点、1/1 handler、retry0、cleanup PASS；访问限制/未出现的限流场景不是新增现场验证 |
| 离线证据筛查/一致性 | 初筛字段名误报保留说明；最终验证退出0，逐字节副本/20样本/hash和语义状态一致 |
| 原工作区/历史保护 | 用户未提交AGENTS字节与执行前一致，原分支保留；未stash/reset/clean，A1不覆盖、代码/锁/配置/CI不变 |

未为仅新增报告/真实证据重跑本机全套功能测试。正常推送后等待新证据head的现有Actions，准确run/head/实际结果记录在PR正文/最终交付；CI继续离线，不能代替本机本次LIVE证据、证明用户出口或升级单位/预算。执行SHA与证据SHA始终分别列出。

本次仅新增A2续报和小型筛查后的证据，Refs #12，不自动合并/关闭Issue、不开始连续采集或下一任务。一次A2权限已用完；完成即停止等待独立复核，后续任何真实执行或LIVE采集开发需要新的明确范围/授权。
