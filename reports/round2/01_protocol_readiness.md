# R2-01：一次有界真实公共行情探测（受阻交付）

任务：[Issue #12](https://github.com/shukehi/btc_microstructure/issues/12)，DEV-002 / tasks/02_protocol_probe.md 的真实核验子集。计划：[R2-01-A1 / 6118805616](https://github.com/shukehi/btc_microstructure/issues/12#issuecomment-6118805616)；复核：[6119249208](https://github.com/shukehi/btc_microstructure/issues/12#issuecomment-6119249208)。用户随后在当前会话明确确认当前本机Mac，并授权该计划B段一次探测及C段证据PR。

**实际被执行代码：`46f348ccc24bf544e3efb7d60168198ab8181ecf`。** 运行时main与A1一致；后续证据提交SHA见PR正文，不能冒充新业务版本。本轮不修改业务、原测试、配置、锁、工作流、AGENTS或历史证据，不重开R103/R104；不开发第二套探测器。

## 结果与证据层次

| 维度 | 实际结果 |
|---|---|
| 一次执行与证据交付 | 一次现有probe已执行；CLI退出 **1**。原始输出保留、筛查后提交小型证据；不为获得PASS再试 |
| 本次REST | **BLOCKED / TIMEOUT**。已观察 **HTTP 200**及`x-mbx-used-weight-1m: 41`，但正文未完整，不能将200当成协议/元数据验收通过 |
| 部分正文 | **425,307 bytes**，`body_complete=false`、`body_read_state=INTERRUPTED`、`truncated=true`。有界前缀保存本地并核对SHA，不按完整JSON解析/接受元数据 |
| 本次WS | **NOT_RUN**：逻辑调用0、handler尝试0、0消息/帧。REST未完成后没有绕过单试WS |
| 重试 / 清理 | logical_calls=transport_attempts={rest:1,ws:0}；retry_count=0、blocked_retry_count=0；cleanup **PASS** |
| 数量单位 | **UNKNOWN**。没有完整元数据核验和WS成交样本，更没有直接适用的单位证据 |
| 持续调用预算 | **UNVERIFIED_FOR_CONTINUOUS_COLLECTION**；observed_rate_limits=null；continuous_collection_enabled=false |
| 连续采集 / 有界采集重放演练 / 24h | **NOT_RUN**。本次是协议probe，不是collector；LIVE样本未转成SYNTHETIC或计算Delta |
| 完整M0/M1/M2、研究/收益能力 | 未验收，不因单次响应头或后续离线CI升级 |

A段官方资料是**2026-10-12既有文档核验记录读回**，本轮未重新浏览或发额外诊断请求。B段表内结果是**本次真实运行**。C段hash/隐私/版本/差异核查是**本次离线验证**。后续Actions只验证既有离线suite，不能解除本机REST受阻。

## 执行前条件与精确命令

只复核A1规定的条件：隔离checkout `data/r201-preflight` HEAD为46f348c、tracked tree干净；当前本机Darwin27.0.0/arm64、Python3.12.13；27个lock依赖逐项一致（aiohttp3.12.15、PyArrow20.0.0等）；局部PYTHONPATH=src使cli/probe/probe_transport导入均来自该checkout/src。未重装环境或重跑整轮预检。

锁文件SHA-256：`7fe9043dac17556e80030e61786aaeb9cd95dce480661b01c8e207064defa0bc`；配置摘要：`b037004b544ca834855821374bd65520a535b509c0b952d9c3f37a97a30d3b43`。新输出叶目录执行前不存在。会话没有已知“必须依赖工具不支持的应用层代理”的确定证据；真实出口/路径/地理可用性未知，未额外检查或推断。

实际cwd：`<repo>/data/r201-preflight`；恰好一次调用以下现有CLI，外层仅用标准库subprocess记录命令、时间、stdout/stderr和退出码，不实现或替代传输逻辑：

```sh
PYTHONPATH=src ../r104-followup/.venv/bin/python -m btc_microstructure probe config/research.example.yaml --allow-network --seconds 30 --messages 20 --max-bytes 2097152 --output data/r201-probe/20261012-plan-a1-attempt01
```

这是**已执行命令记录，不是本次交付后的再次运行指令**。授权次数已用完；本报告不授权复跑。原始运行与调用回执留在上述checkout的忽略data目录，原始stdout/stderr不覆盖；公开invocation.redacted.json只将cwd脱敏、去除用户文件哈希，明确与原件不同。

| 时点 / 时限 | UTC或实际值 |
|---|---|
| 外层命令开始 / 结束 | 2026-10-12T06:55:20.997219Z / 06:55:26.273003Z |
| probe开始 / 结束 | 2026-10-12T06:55:21.237620Z / 06:55:26.240755Z |
| 响应头观察时点 | 2026-10-12T06:55:24.534457Z |
| probe elapsed | **5.0027883750153705秒**；不是运行满30秒 |
| 允许时限/数量 | 30s总探测域（29s工作+最多1s清理）、连接/REST阶段5s、WS连接/空闲5s；最多20条WS、2,097,152B应用正文、65,536B单WS消息 |

REST固定GET `https://fapi.binance.com/fapi/v1/exchangeInfo`，params={}。若完整通过才会到固定`wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade`；本次该路径没有尝试。TLS校验、trust_env=False、无显式proxy、DummyCookieJar、重定向拒绝及middleware单尝试保护均原样，未改网络/代理/TUN/DNS/TLS或切换出口/机器。

总30秒是上限；现有`run_probe.perform()`对整个metadata阶段用5秒timeout，故即便在约3.297秒收到200响应头，正文仍受该阶段剩余时间约束。本次保存前缀后约5.003秒停止与该路径相符。**这只是源码与时间记录对应，不证明网络/代理/地区根因，也不证明放宽超时就能成功。** 未尝试扩大时限/预算或修改实现。

## 证据保存、筛查与预算解释

证据目录：`reports/round2/protocol_readiness/20261012-plan-a1-attempt01/`。

- `protocol_probe_report.json`与`endpoint_budget.json`是工具原件的逐字节副本，无手工升级状态；原始输出也保留本地。前者SHA=`901c14db26988cc48f7f47b2699eab394f8b14b8480506e8dc517a678f869575`，后者SHA=`887db8c88aaab44647e58f404fdefc0d9af38a935a4cc9340e1c03cf6dc531b2`。
- **rest-response.bin只留本地，不在PR中**：425,307B、SHA=`281d7af97bc5215fda9907426663a0e3fb31513d4eb1c4738460c680474b7988`。它是未完成的实际响应前缀，不是完整fixture；原report保留sample文件名/hash，公开manifest明确local-only，以免误解为仓库漏了完整可重放样本。不提交整张元数据或不必要的响应摘录。
- 对原响应前缀及公开记录作秘密/认证/cookie/私人路径模式筛查，未命中；人工复读全部公开JSON/log字段。模式扫描不是普遍无秘密证明。公开JSON只含固定端点、白名单权重头、状态/时刻/预算/哈希，无代理配置、Cookie、凭据、环境变量转储或真实私有账户信息。invocation脱敏副本与原件区分。
- `invocation.redacted.json`、`stdout.log`、`stderr.log`、`environment.json`记录实际命令/退出码及非敏感环境；`local-originals-manifest.json`记录本地原件与发布策略；`evidence-verification.json`记录离线一致性/隐私核查。无需为仅新增证据另造业务测试；一致性核查退出0，真实probe退出1不被包装成成功。
- 文档核验权重1是资料事实；本次header **41是该IP所观察的1分钟累计已用权重**，不能说本次请求消耗41、可用剩余为某个数字，或同出口没有其他使用者。完整rateLimits未接受，持续计划/共享出口占用亦未建立，持续预算依赖不关闭。不加压测试、不增发time/ping/REST请求。

## 历史比较与需求映射

旧`fixtures/protocol/r1_02_run01`的probe-0.1只记REST TIMEOUT、0正文/无HTTP头、WS NOT_RUN，继续原样保留。此次probe-0.2的新事实是**已收200/用量头及有界部分正文**，重试处理次数可观察；不能把新事实倒写旧记录，不能把旧常量retry_count视为同等传输证据。R102重试/部分响应修复继续使用，无代码修改。

| 需求 / 任务 / 测试 | 本轮证据与结论 |
|---|---|
| REQ-016 / DEV-002 / TEST-OPS-01 | 仅一次固定公共GET，未读key/签名/私有账户或下单；传输原样；WS未尝试 |
| REQ-017 / DEV-002 / TEST-OPS-02适用部分 | 5s阶段超时明确BLOCKED，handler1/0、retry0、cleanup PASS；不靠无界重试取得成功 |
| REQ-003/018 / TEST-DOC-01、probe部分响应证据 | 时刻、状态200、权重头、未完成前缀/长度/hash保存；未解析接受metadata、未以部分输入进WS |
| ISSUE-003/004真实运行前置 | 完整REST/WS核验仍未完成；单位UNKNOWN、持续预算UNVERIFIED；不声称完整M0或采集可用 |

## PR / 离线CI / 下一决策点

证据分支 `validation/r2-01-protocol-readiness` 从本轮最新main建立，仅新增本报告/小型证据，Refs #12，不自动关闭Issue。正常推送后的精确报告SHA、PR与最新Actions回执以PR正文/交付答复为准；等待该head既有离线CI完成，不将其绿色当本次真实probe PASS。原工作区未提交AGENTS字节保留，未stash/reset/clean；原始样本/旧报告不覆盖。

**下一决策点只有一个：独立复核此次HTTP200但正文未完成的证据后，由用户决定是否另立有界任务审视REST阶段5秒（含正文）的超时边界。** 当前记录不授权改工具/扩大上限/再试，且不能预断调整后会成功。本轮到此停止，不自动重连、不单试WS、不启动连续采集/真实Delta/24h，也不自动合并或关闭Issue。
