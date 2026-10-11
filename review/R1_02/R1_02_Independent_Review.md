# R1-02 首轮协议核验：独立审查

- 审查结论：**CHANGES_REQUESTED（两个代码问题）＋ RUNTIME_BLOCKED（真实接口未通过）**。
- 对象：`protocol_probe_review.zip`；SHA-256：`e5ab90935f0e0e770914bb004b7f4515e5c572eaeeca2b4a612b16ed4c903aa4`。
- 依据：用户交付的代码、任务卡、规格、报告与日志；本次实际离线执行；另行核验的官方英文资料。
- 不回退 R1-01 的既有通过结论；本次不实现修复、不启动 R1-03、不访问币安实际接口。

## 1. 结论边界

本轮新增的元数据/聚合成交检查、网络显式开关、固定来源范围、超时/大小限制、模拟与现场证据隔离具有实际实现。原配置与安全回归没有退化。但“应用没有写重试循环”不能等同于“客户端绝不重试”，读取中断也不应抹去已经收到的响应信息。两项遗漏需要在本轮关闭前修复。

现场记录仍为 REST `BLOCKED/TIMEOUT`、WS `NOT_RUN`。这不是本次离线审查重现的网络结果，而是核对用户归档中的现场记录。不能仅据此推断地区限制、DNS 故障、接口停用，或认为网络恢复就能解决全部问题。

## 2. 已执行检查

| 项目 | 本次结果 | 证据 |
|---|---|---|
| 上传 ZIP 与外部 SHA-256 | 一致 | `evidence/archive_check.json` |
| ZIP CRC、重复路径、解压路径安全 | 通过；228 个文件 | 同上 |
| 当前包内文件清单 | 227 个清单条目全部匹配；另 1 个文件为清单本身 | `evidence/integrity.json` |
| 与修复后的 R1-01 归档比较 | 147 个原文件字节相同；4 个原文件按任务修改；无删除 | 同上 |
| 原有文件改动范围 | README、pyproject.toml、requirements-dev.lock、cli.py；README 保留前版全文前缀 | 同上 |
| 当前实现摘要 | 16 个源码/测试/依赖等摘要全部匹配 | 同上 |
| 单独上传报告与 ZIP 内报告 | 字节相同 | 同上 |
| 默认测试套件 | **166 通过，0 失败，0 跳过** | `evidence/default-tests.log/xml` |
| 原 R1-01 独立测试 | **15 通过，0 失败，0 跳过** | `evidence/independent-r1-01.log/xml` |
| 合法配置 CLI | 退出 0，原摘要不变，运行能力仍 UNVERIFIED | `evidence/valid-config.log` |
| 未给网络开关的 probe CLI | 退出 2，写入 NOT_RUN，不构造实际网络访问 | `evidence/network-disabled.log` |
| 新增独立回归测试 | **7 失败、2 通过**；对应下述两类问题 | `evidence/r1-02-regressions.log/xml` |

7 项失败的组成：REST/WS 隐式重试各 1 项；429/451 分别在超时和正文读取错误下丢失头部，共 4 项；200 响应的已读前缀丢失 1 项。它们不是 7 个不同模块故障。

## 3. 执行环境与限制

本次真实执行：Linux x86_64、Python 3.13.5、aiohttp 3.13.3、Pydantic 2.13.5、PyYAML 6.0.3、pytest 9.0.2。通过 `PYTHONPATH=src` 导入被审项目，禁用自动加载第三方 pytest 插件；没有改写项目源码。

用户提交的原机日志对应 macOS/Python 3.12.13/aiohttp 3.12.15。本环境无法安装指定 aiohttp 3.12.15，失败的依赖获取输出已保留。因此，**本次不是原锁定环境的完整复现**。对隐式重试分支，额外读取了 aiohttp **3.12.15 官方源码文档**，确认该版本包含同类默认重试逻辑；它是版本相关的源码证据，不冒充该版本的运行证据。

本次没有独立重跑 ruff、mypy，也没有重建完整锁定依赖、执行 macOS 测试或访问币安。用户归档中的这些检查只能称为提交方记录。全部新增回归用人工样例；重试实验使用真实 aiohttp 请求状态机与注入中间件，但不会调用实际网络发送处理器，socket/DNS 访问被禁止。

## 4. 必须修复 R102-A：底层隐式重试未受到约束

**优先级：P1；位置：**`src/btc_microstructure/probe_transport.py:16–39, 42–78`；`src/btc_microstructure/probe.py:217–219, 294–301, 346–347`。

### 观察与复现

应用只显式调用一次 `session.get()` / `ws_connect()`，但创建会话时没有约束 aiohttp 自身的重试行为。报告中的 `retry_count=0`、预算中的 `max_retries=0` 是常量。

独立实验保留真实 aiohttp 的请求循环，在公共中间件接口注入 `ServerDisconnectedError`。REST 和 WS 握手路径都进入该请求中间件 **2 次**，报告仍为 `retry_count=0`。中间件在发送处理器前抛错，因此这不是“观察到对币安实际发出了两次请求”的现场证据，而是离线复现客户端会再次进入请求处理链。

官方 aiohttp 3.12.15 源码也将 `_retry_connection` 默认设为 true，对 GET 等幂等方法在 `ClientOSError` / `ServerDisconnectedError` 下包含一次重试分支。没有自动重试循环的应用代码仍可能触发这个客户端分支。

### 影响

当前实现不能兑现任务卡/报告承诺的“不重试”，尝试次数和预算报告也不能完整反映传输行为。此问题**不证明用户原来那次 TIMEOUT 发生了重试**：该现场日志不能回答这一点，且连接超时并不等同于本次注入的断连异常。

### 最小修复要求

在当前锁定版本中，明确阻止 REST 和 WS 握手的隐式第二次发送尝试。优先采用可测试、公开的接口策略；不能只改报告数字、换成另一个错误文本，或放宽原有“不重试”合同。若不得不依赖私有属性，要单独记录版本耦合和失败保护，不能静默假定永久稳定。

尝试次数、禁止重试规则与报告字段需一致；模拟实际异常，断言传输处理不会进入第二次发送路径。仅检查“调用一次 session.get”或“report.retry_count==0”不足以验证此规则。

## 5. 必须修复 R102-B：正文中断会丢失已观察到的响应证据

**优先级：P2，阻塞本轮证据完整性验收；位置：**`probe_transport.py:42–57`；`probe.py:235–249, 294–307`。

### 观察与复现

`metadata()` 只有在正文读取结束后才返回 `Reply(status, headers, body)`。其间发生超时或 `ClientPayloadError` 时，响应对象中已有的信息没有随异常传回。`run_probe()` 因此无法记录它们。

人工场景：先给传输层 HTTP 429 或 451、`Retry-After: 60`、`X-MBX-USED-WEIGHT-1M: 7`，再让正文读取超时或报错。最后报告只有 TIMEOUT 或 REST_NETWORK_ERROR，**没有已经观察到的 HTTP 状态和限流头**。

另一场景：HTTP 200，正文先成功交付 21 字节，再超时。应用已读的前缀被丢弃，报告 `bytes_received=0`，也没有可保存的部分响应。这不是线上线路字节数测量问题，而是传输函数确实已经收到的应用正文证据没有交给报告层。

### 影响

“已经返回限流/拒绝状态，但正文未读完”和“尚未收到任何状态”无法区分。之后诊断网络阻塞、审计预算、检查超时发生阶段时，缺少已经存在的证据。

### 最小修复要求

响应头可用时就保留状态、经白名单过滤的预算头和可用时间；正文失败时仍保存这些事实。已接收的有界前缀应带“不完整/截断”标记保存并哈希，不得当完整 JSON 或有效元数据使用。对 200 部分正文不应出现 PASS，也不得继续连接 WS。

允许在明确拒绝状态时立即停止正文读取；没有读过的字节不能凭空补造。对已经读到的字节，也不能静默回填为零。无响应头与有响应头但正文失败必须分别表达。处理取消/超时时仍要遵守总时限、清理和停止要求，不为保留证据而无限等待。

测试使用传输层逐块读取，覆盖头部先到、正文随后失败；只让 FakeTransport 直接返回完整 Reply 的测试无法检验本问题。继续屏蔽 cookies/认证信息和原始敏感异常文本。

## 6. 非修复事项与现场阻塞

R1-01 的配置模型、研究期限参数化、安全忽略规则及原回归未发生本轮退化，不需要重新开发。固定交易所路由、显式网络开关、模拟/真实数据分离、十进制字符串保留、未知数量单位处理也应保留。

官方文档支持当前 exchangeInfo 和组合 aggTrade 的路径与主要字段；这不构成现场成功或 BTC 数量单位已核验的证据。已经提交的真实运行只有一次逻辑探测记录：REST 超时，WS 未执行。原报告正确保留 PARTIAL，而不是伪造现场样例。

本次两项代码修复可以完全离线进行。修复通过后，代码子集可以单独验收；现场访问仍须另有明确授权、符合平台规定、在目标环境内进行有界核验。不要为了让报告变绿修改代理、换地域/入口、循环探测或索取交易密钥。不要事后改写此前现场失败记录。

## 7. 如何使用审查包

将 ZIP **解压内容**放入当前项目 `review/R1_02/`。包内有本报告、`R1_02_FIX_PROMPT.md`、`checks/test_r1_02_regressions.py` 与证据，不含替换项目源码。

在项目根目录、原有 Python 3.12 虚拟环境先执行：

```sh
.venv/bin/python -m pytest -q review/R1_02/checks/test_r1_02_regressions.py
```

当前版本预期暴露上述遗漏；必须记录真实结果。特别是重试分支需在锁定 aiohttp 3.12.15 上核实，本审查的运行环境不代替原机验证。

修复后：重跑原166项、R1-01独立15项、本轮独立9项，以及新增实现侧回归、配置CLI、网络未启用CLI和限定范围静态检查。把前后日志分开保存。不得更改原规格、原样例、固定预期或历史失败日志。

交付 `reports/round1/02_protocol_probe_fix.md`、`protocol_probe_review_fixed.zip` 与 SHA-256；现场仍阻塞就继续如实记录。停止在 R1-02，不自动执行任务03。

## 8. 外部核验来源

以下为本次额外查阅的英文一手资料，区别于用户提交证据。仅保存 URL 和有限事实摘要，不复制资料全文。

1. aiohttp 3.12.15 client source：<https://docs.aiohttp.org/en/v3.12.15/_modules/aiohttp/client.html>。支持 R102-A 的锁定版本源码核验。
2. Binance USDⓈ-M market streams：<https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/market>。支持组合 aggTrade 路径与主要字段。
3. Binance USDⓈ-M market-data REST：<https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data>。支持 exchangeInfo 路径及资料中的请求权重；不证明目标 IP 额度。

精确执行命令与环境在 `evidence/commands.json`、`environment.json`；官方资料核验登记在 `evidence/source_verification.json`。
