任务编号：R1-02 / DEV-002 首轮子集  
原规格版本：0.1  
代码版本/工作树状态：无 Git 仓库，`git status --short --branch` 实际退出 128；未初始化、提交或推送。源代码已写入工作区并纳入 ZIP，以 `probe/implementation.sha256` 和包内清单追溯。  
本次范围：仅 BTCUSDT USDⓈ-M 永续 exchangeInfo 与 aggregate trade 的资料核验、离线模拟、可注入有界只读 probe。  
交付状态：**PARTIAL**。官方资料 DOC_VERIFIED，离线实现/测试 PASS；真实 REST **BLOCKED**（超时），真实 WS **NOT_RUN**。本次不代表完整 M0 或真实接口验收通过。  
未实现范围：任务03、持续采集、成交标准化/去重/重放/Delta、信号、账户和下单；未启用现货、L2、OI、资金或强平。

## 执行环境

macOS 27.0.1（Build 26A434），Darwin 27.0.0，arm64；项目 `.venv/bin/python` 为 Python 3.12.13，pip 25.0.1。
运行依赖 Pydantic 2.12.3、PyYAML 6.0.3、新增 aiohttp 3.12.15；pytest 8.4.2、ruff 0.14.0、mypy 1.18.2。
原17个锁定版本未升级；新增 aiohttp 及8个传递依赖，实际安装后精确锁定，共26项，完整版本如下。

```text
aiohappyeyeballs==2.7.1
aiohttp==3.12.15
aiosignal==1.4.0
annotated-types==0.8.0
attrs==26.1.0
frozenlist==1.8.0
idna==3.20
iniconfig==2.3.1
multidict==6.9.1
mypy==1.18.2
mypy_extensions==1.1.0
packaging==26.3
pathspec==1.1.1
pluggy==1.6.0
propcache==0.5.4
pydantic==2.12.3
pydantic_core==2.41.4
Pygments==2.21.0
pytest==8.4.2
PyYAML==6.0.3
ruff==0.14.0
setuptools==80.9.0
types-PyYAML==6.0.12.20250915
typing-inspection==0.4.4
typing_extensions==4.16.0
yarl==1.25.1

```

依赖安装与本地 editable 安装实际成功。第二个全新环境的完整安装演练、Linux 或其他解释器复跑为 NOT_RUN。
依赖下载遵守本次环境的 never 审批设置，没有提权或修改网络设置。

## 修改文件

| 文件 | 用途 |
|---|---|
| `src/btc_microstructure/probe.py` | 限制模型、注入传输协议、元数据/聚合消息字段检查、运行状态、原始样本与预算报告；无采集器/研究计算 |
| `src/btc_microstructure/probe_transport.py` | aiohttp 公共 REST/WS 传输，仅固定两个官方入口；连接/读取限时、字节限制、TLS、自动 pong、拒绝重定向、无重试或认证读取 |
| `src/btc_microstructure/cli.py` | 新增 probe 子命令；默认不联网，必须 `--allow-network`；保留 validate-config 行为 |
| `tests/unit/test_probe.py` | 新增47项离线解析、失败、限制、时间、证据测试 |
| `tests/unit/test_probe_transport.py` | 新增13项传输选项、握手拒绝、CLI gate/硬上限/自定义路由拒绝测试 |
| `fixtures/synthetic/probe/README.md` 与4个JSON | 手工构造并明确标记 synthetic 的元数据与聚合消息，不是官方真实响应 |
| `fixtures/protocol/r1_02_run01/protocol_probe_report.json`, `endpoint_budget.json` | 单次真实探测的超时/BLOCKED及未核验预算；本次没有真实响应样本 |
| `pyproject.toml`, `requirements-dev.lock` | 精确声明新增 aiohttp 并锁定实际可安装版本 |
| `README.md` | 追加 R1-02 已实现命令、网络开关、上限、状态与证据边界；保留原文和 R1-01 说明 |
| `reports/round1/02_protocol_probe.md` | 本报告 |
| `reports/round1/probe/docs_verification.json` | 官方 URL、核验时间、字段/路线依据与未关闭差异 |
| `reports/round1/probe/offline_01/` 和 `offline_02/` | 原106、全部测试、独立15测试日志/JUnit（offline_02为最终166）、CLI/静态检查/环境/命令退出码 |
| `reports/round1/probe/verify_offline.py`, `render_synthetic.py` | 可重复离线验证与人工样例证据生成；不启用真实网络 |
| `reports/round1/probe/synthetic-run/` | SYNTHETIC 协议样本、原始字节/hash、endpoint_budget；不作为现场证据 |
| `reports/round1/probe/live-command.log/json` | 真实单次执行的完整命令、进程起止时刻、退出码和输出 |
| `reports/round1/probe/protected.json`, `integrity.json`, `implementation.sha256`, `source.diff` | 原始材料保护、源码版本和实际差异 |
| `reports/round1/probe/dependency-install.log`, `transport-initial.log`, `initial-mypy.log`, `agent-bootstrap-failures.log` | 实际安装/初测/历史失败记录，失败来源明确标注 |
| `reports/round1/package_protocol_probe.py`, `probe_package_manifest.json`, `probe-packaging-audit.json` | 本任务项目白名单打包、逐文件哈希及秘密筛查 |
| 根目录 `protocol_probe_review.zip`, `.zip.sha256`, `.zip.verify.json` | 新版项目归档和外部验证回执；不覆盖两个既有 ZIP |

原开发规格、原配置样例、固定预期、R1-01配置模型/安全规则、原106项测试及原独立15项检查均未修改。46个受保护文件哈希一致。
原配置摘要仍为 `b037004b544ca834855821374bd65520a535b509c0b952d9c3f37a97a30d3b43`，未把 `protocol_probe_completed` 改为 true。

## 需求与验收映射

| requirement_id | dev_task / subset | test_id | 证据文件 | 状态 |
|---|---|---|---|---|
| REQ-018 | DEV-002 当前官方资料、已知差异与证据层次 | TEST-DOC-01 适用部分 | `docs_verification.json` | DOC_VERIFIED；真实状态单列 |
| REQ-016 | DEV-002 固定公共端点、显式 gate、无认证/私有账户/下单 | TEST-OPS-01 本范围 | `test_probe_transport.py`，源码与 offline日志 | PASS（离线） |
| REQ-017 | DEV-002 超时、403/418/429/451、无界重试防护 | TEST-OPS-02 本范围 | `test_probe.py`、`test_probe_transport.py` | PASS（模拟）；现场BLOCKED |
| REQ-003/004 | DEV-002 E/T/ms、a/f/l、m、十进制字符串的协议检查 | 新增 TEST-PROBE-SCHEMA；非完整 TRADE/STORE 验收 | synthetic fixtures 与默认测试日志 | PASS（模拟） |
| REQ-001/011/015 | R1-01 基础保持 | 原 TEST-CFG-01/02、TEST-COST-01 与修复回归 | original106.xml；independent15.xml | 106+15 全部 PASS |
| REQ-018 | DEV-002 首轮真实访问与预算 | ISSUE-003/004 相关前置 | 实际 protocol_probe_report/endpoint_budget | BLOCKED / UNVERIFIED |

## 实际执行

全部命令工作目录为 `/Users/aries/Dve/btc_microstructure`。记录只含项目所需有限环境信息，不转储环境变量。

| 命令 | 工作目录 | 退出码 | 状态；通过 / 失败 / 跳过 | 日志位置 |
|---|---|---|---|---|
| `sw_vers` | 项目根目录 | 0 | PASS；— | `reports/round1/probe/offline_01/os.log` |
| `.venv/bin/python --version` | 项目根目录 | 0 | PASS；— | `reports/round1/probe/offline_01/python.log` |
| `.venv/bin/python -m pip list --format=json` | 项目根目录 | 0 | PASS；— | `reports/round1/probe/offline_01/dependencies.log` |
| `.venv/bin/python -m pip install --no-build-isolation --no-deps -e .` | 项目根目录 | 0 | PASS；— | `reports/round1/probe/offline_01/install-local.log` |
| `.venv/bin/python -m pytest -q tests/unit/test_config.py tests/unit/test_foundation_guards.py --junitxml=reports/round1/probe/offline_01/original106.xml` | 项目根目录 | 0 | PASS；106 / 0 / 0 | `reports/round1/probe/offline_01/original106.log` |
| `.venv/bin/python -m pytest -q --junitxml=reports/round1/probe/offline_01/default.xml` | 项目根目录 | 0 | PASS；165 / 0 / 0 | `reports/round1/probe/offline_01/default.log` |
| `.venv/bin/python -m pytest -q review/R1_01/checks/test_review_regressions.py --junitxml=reports/round1/probe/offline_01/independent15.xml` | 项目根目录 | 0 | PASS；15 / 0 / 0 | `reports/round1/probe/offline_01/independent15.log` |
| `.venv/bin/ruff check src tests` | 项目根目录 | 0 | PASS；— | `reports/round1/probe/offline_01/ruff.log` |
| `.venv/bin/ruff format --check src tests` | 项目根目录 | 0 | PASS；— | `reports/round1/probe/offline_01/format.log` |
| `.venv/bin/mypy src` | 项目根目录 | 0 | PASS；— | `reports/round1/probe/offline_01/mypy.log` |
| `.venv/bin/python -m pip check` | 项目根目录 | 0 | PASS；— | `reports/round1/probe/offline_01/pip-check.log` |
| `shasum -a 256 -c reports/round1/preserved.sha256` | 项目根目录 | 0 | PASS；— | `reports/round1/probe/offline_01/preservation.log` |
| `.venv/bin/python -m btc_microstructure validate-config config/research.example.yaml` | 项目根目录 | 0 | PASS；— | `reports/round1/probe/offline_01/validate-config.log` |
| `.venv/bin/python -m btc_microstructure probe config/research.example.yaml --output reports/round1/probe/offline_01/network-disabled` | 项目根目录 | 2 | PASS；— | `reports/round1/probe/offline_01/network-disabled.log` |
| `.venv/bin/python -m pip install 'aiohttp==3.12.15'` | 同上 | 0 | PASS：安装成功 | `probe/dependency-install.log` |
| `.venv/bin/python -m pip freeze --exclude-editable > requirements-dev.lock` | 同上 | 0 | PASS：26项实际锁定 | 根目录 lock |
| `.venv/bin/python -m pytest -q tests/unit/test_probe_transport.py`（初测） | 同上 | 0 | 13 / 0 / 0 | `probe/transport-initial.log` |
| `.venv/bin/mypy src`（首次） | 同上 | 1 | FAIL：1项 no-any-return，已将字节预算计数显式转为int后修正 | `probe/initial-mypy.log`（真实工具输出原文存档） |
| `python -m pytest tests/unit/test_probe.py -q`（测试代理首次） | 同上 | 127 | FAIL：无 python 命令 | `probe/agent-bootstrap-failures.log` |
| `/Users/aries/.local/bin/python3.12 -m pytest tests/unit/test_probe.py -q`（测试代理） | 同上 | 1 | FAIL：系统解释器无pytest；随后项目venv通过 | 同上，附真实会话来源 |
| `.venv/bin/python reports/round1/probe/verify_offline.py` | 同上 | 0 | PASS：上表全量离线检查 | `probe/offline_01/commands.json` |
| `.venv/bin/python reports/round1/probe/render_synthetic.py` | 同上 | 0 | PASS，SYNTHETIC，无网络 | `probe/synthetic-run/` |
| `.venv/bin/python -m btc_microstructure probe config/research.example.yaml --allow-network --seconds 20 --messages 20 --max-bytes 2097152 --output fixtures/protocol/r1_02_run01` | 同上 | 1 | **BLOCKED**：TIMEOUT；WS NOT_RUN | `probe/live-command.log/json` |
| `.venv/bin/python reports/round1/package_protocol_probe.py` | 同上 | 0 | 项目打包和CRC/哈希检查 | 根目录 `.zip.verify.json` |

最终默认套件 **166通过 / 0失败 / 0跳过**，其中原106项完整保留，新增60项；独立原15项另跑 **15通过 / 0失败 / 0跳过**。
无 skip/xfail，无原预期修改。静态检查明确限定 `src tests`；不把历史证据脚本的全仓 lint 称为通过。
网络 gate 的预期退出2是“未执行真实网络”，该 gate 行为测试通过并不代表网络验收通过。

### 交付前代码复查与最终重跑

同会话只读 code-reviewer 发现：字节预算先耗尽且已有少量有效样本时，原分支会返回PASS。
新增独立断言先实际复现该问题：
`.venv/bin/python -m pytest -q tests/unit/test_probe.py::test_partial_sample_exhausting_byte_budget_cannot_pass --junitxml=reports/round1/probe/review-before.xml`，
工作目录为项目根目录，退出1，**0通过/1失败/0跳过**，完整错误见 `probe/review-before.log/xml`。
随后将该分支明确改为BLOCKED，保留已收到的样本和哈希；未改变该断言，也未修改原有测试预期。只读复查代理已确认Finding resolved、无其他实质性发现，见 `probe/code-review.md`。
只重跑离线检查，没有再次访问真实接口。真实尝试发生在该修正之前，但在REST阶段即超时，未触达这个WS字节预算分支。

初轮offline_01是165项；最终offline_02是166项，不删除初轮或失败记录。

| 最终实际命令 | 工作目录 | 退出码 | 状态；通过 / 失败 / 跳过 | 日志 |
|---|---|---|---|---|
| `sw_vers` | 项目根目录 | 0 | PASS；— | `reports/round1/probe/offline_02/os.log` |
| `.venv/bin/python --version` | 项目根目录 | 0 | PASS；— | `reports/round1/probe/offline_02/python.log` |
| `.venv/bin/python -m pip list --format=json` | 项目根目录 | 0 | PASS；— | `reports/round1/probe/offline_02/dependencies.log` |
| `.venv/bin/python -m pip install --no-build-isolation --no-deps -e .` | 项目根目录 | 0 | PASS；— | `reports/round1/probe/offline_02/install-local.log` |
| `.venv/bin/python -m pytest -q tests/unit/test_config.py tests/unit/test_foundation_guards.py --junitxml=reports/round1/probe/offline_02/original106.xml` | 项目根目录 | 0 | PASS；106 / 0 / 0 | `reports/round1/probe/offline_02/original106.log` |
| `.venv/bin/python -m pytest -q --junitxml=reports/round1/probe/offline_02/default.xml` | 项目根目录 | 0 | PASS；166 / 0 / 0 | `reports/round1/probe/offline_02/default.log` |
| `.venv/bin/python -m pytest -q review/R1_01/checks/test_review_regressions.py --junitxml=reports/round1/probe/offline_02/independent15.xml` | 项目根目录 | 0 | PASS；15 / 0 / 0 | `reports/round1/probe/offline_02/independent15.log` |
| `.venv/bin/ruff check src tests` | 项目根目录 | 0 | PASS；— | `reports/round1/probe/offline_02/ruff.log` |
| `.venv/bin/ruff format --check src tests` | 项目根目录 | 0 | PASS；— | `reports/round1/probe/offline_02/format.log` |
| `.venv/bin/mypy src` | 项目根目录 | 0 | PASS；— | `reports/round1/probe/offline_02/mypy.log` |
| `.venv/bin/python -m pip check` | 项目根目录 | 0 | PASS；— | `reports/round1/probe/offline_02/pip-check.log` |
| `shasum -a 256 -c reports/round1/preserved.sha256` | 项目根目录 | 0 | PASS；— | `reports/round1/probe/offline_02/preservation.log` |
| `.venv/bin/python -m btc_microstructure validate-config config/research.example.yaml` | 项目根目录 | 0 | PASS；— | `reports/round1/probe/offline_02/validate-config.log` |
| `.venv/bin/python -m btc_microstructure probe config/research.example.yaml --output reports/round1/probe/offline_02/network-disabled` | 项目根目录 | 2 | PASS；— | `reports/round1/probe/offline_02/network-disabled.log` |

本轮新增的离线验证、人工证据生成和打包脚本另行执行限定ruff检查，退出0；完整命令/输出在 `probe/report-scripts-lint.log`。

## 数据证据

### 1. 官方英文资料（DOC_VERIFIED）

核验时刻：2026-10-11T04:55:23.989397+00:00。通过官方网页检索工具读取；这不是本机交易所 API 连接证据。
URL、范围内事实和差异完整保存于 `probe/docs_verification.json`，只保存有限摘要，不复制网页全文。

| 来源 | 官方页面 | 证据状态 |
|---|---|---|
| S01 | [S01 官方英文资料](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Important-WebSocket-Change-Notice) | DOC_VERIFIED（仅本范围） |
| S02 | [S02 官方英文资料](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/market) | DOC_VERIFIED（仅本范围） |
| S04 | [S04 官方英文资料](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data) | DOC_VERIFIED（仅本范围） |
| S08 | [S08 官方英文资料](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info) | DOC_VERIFIED（仅本范围） |
| S12 | [S12 官方英文资料](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Connect) | DOC_VERIFIED（仅本范围） |
| CLIENT | [CLIENT 官方英文资料](https://docs.aiohttp.org/en/v3.12.15/client_reference.html) | DOC_VERIFIED（仅本范围） |

字段映射：metadata 以 symbol/contractType/baseAsset/quoteAsset/marginAsset/status 检查目标永续；tickSize 来自 PRICE_FILTER，stepSize/minQty 来自 LOT_SIZE；实际 rateLimits 另存，不使用文档示例常数。
聚合消息核对 e/E/T/s/a/f/l/p/q/m；可选 nq、st 保留，新增非核心字段记录名字并保存原始数据。固定组合 URL 要求 stream/data 封装；未知事件/合约类型/核心字段变化明确失败。
时间单位按官方说明记录 ms；p/q/nq 保留原始十进制字符串。原生 quantity 仍为 UNKNOWN 单位，未构造 BTC 量或收益结果。

差异/未核验事项：
- DOC-R102-01：官方旧入口兼容段落存在表述差异；本次仅用明确的 routed MARKET 入口，与原规格一致，未更改原规格。
- UNIT-R102-01：聚合数量字段页没有明确的 BTC 单位声明；仅凭 baseAsset 或样例数字不足以提升为绝对单位已验证。本任务保留原生数量。
- 文档权重1是资料证据，不能据此推断当前 IP 的可用预算。实际 rateLimits/响应头本次未取得。
- aiohttp 版本页 ClientWSTimeout 的版本注记有歧义，已通过本机3.12.15导入和模拟传输测试确认所用API可用。

### 2. 模拟测试（SYNTHETIC）

`fixtures/synthetic/probe/` 的4个JSON由人工编写，README明确来源；不冒充官方响应或真实BTC价格。
`probe/synthetic-run/protocol_probe_report.json` 标为 SYNTHETIC，REST/WS样例原始字节及 SHA-256 同目录保存。
模拟 PASS 只证明离线解析与有界状态处理：不证明真实URL可达、额度获准或字段单位已确认。
除普通解析与拒绝测试，还实际测试连接、空闲和总时限、REST超限1字节、WS帧/消息/总字节限制，以及cleanup和样本hash。

### 3. 真实访问（LIVE，BLOCKED）

实际进程起止 UTC：2026-10-11T05:05:04.597096+00:00 至 2026-10-11T05:05:09.915325+00:00。
引擎记录耗时约5.006秒；配置总上限20秒，单次连接/请求上限5秒、消息20条、应用数据2MiB、WS单消息64KiB。

| 项目 | 实际观察 |
|---|---|
| REST | 仅一次 `GET https://fapi.binance.com/fapi/v1/exchangeInfo`，无查询参数 |
| REST结果 | BLOCKED：TIMEOUT；HTTP状态、限流头、完成的响应样本均未取得，不推测限制原因 |
| WS | NOT_RUN；未握手、未订阅、无真实消息；没有使用其作为REST失败后的备用入口 |
| 字节/消息 | 无交付给检查器的响应payload，0条WS消息；不据此声称网络线路上从未发生任何字节交换 |
| 重试/退避 | retry_count=0，backoff cap=0；失败即停止，无自动恢复、换IP或备用URL |
| 预算 | endpoint_budget 已生成，observed_rate_limits=null，连续采集权限/预算未核验；continuous_collection_enabled=false |
| 真实元数据/时间单位/数量单位 | NOT_RUN/UNVERIFIED；本次没有真实字段样例可验 |

没有真实响应fixture，所以没有真实响应hash；只有实际执行状态与预算报告，不能拿 synthetic hash 填补。
`fixtures/protocol/r1_02_run01/` 与 synthetic 目录隔离；其中 LIVE 表示真实尝试来源，不表示成功。
WS成功握手的预算头若库公开API未提供，会明确留空，不使用私有属性或假造头字段。

## 失败、阻塞与限制

- PASS：R1-02 离线实现、166默认测试、原15独立回归、原配置/样例保护、限定范围静态检查。
- DOC_VERIFIED：上述官方英文页面已实际查阅；不等于现场核验。
- BLOCKED：本机公共REST访问超时，未取得可存档响应；WS NOT_RUN。未重试，不绕过平台、地区或网络限制，不读取交易密钥。
- FAIL（已修正/已记录）：代码复查新增的字节预算误判回归1项；首次mypy类型检查1项；测试代理误用系统解释器的两次失败。完整错误原文保留，最终用项目venv验证通过。
- NOT_RUN：真实WS、其他来源、Linux/其他解释器、全新环境安装、长期运行、完整M0、任务03及所有采集/特征/研究验收。
- 单次有界probe不证明持续额度、全行情完整性、ping长期存活或任何交易优势。项目总状态为PARTIAL，不能启用正式持续采集。
- 当前无Git元数据，不能生成提交号；交付源码、差异和哈希已打包，未擅自初始化或推送。
- 系统网络/代理未修改。aiohttp不读取环境代理认证或netrc，不传API密钥；底层错误文字及非预算头不写入报告，相关遗漏原因明确说明。其他日志未发现需要脱敏的真实秘密。

## 用户复现

在项目根目录、已有Python3.12环境运行（安装依赖需要包索引访问，遵守目标环境审批）：

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.lock
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q tests/unit/test_config.py tests/unit/test_foundation_guards.py
.venv/bin/python -m pytest -q review/R1_01/checks/test_review_regressions.py
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src
.venv/bin/python -m btc_microstructure validate-config config/research.example.yaml
.venv/bin/python -m btc_microstructure probe config/research.example.yaml --output reports/round1/probe/manual-disabled
```

预期：166默认/106原有/15独立全部通过；validate-config退出0且原摘要不变；无网络开关的probe退出2并保存NOT_RUN。
`verify_offline.py` 每次创建新的 offline_NN 目录，保留已有失败记录。
人工样例复现：`.venv/bin/python reports/round1/probe/render_synthetic.py reports/round1/probe/manual-synthetic`。
真实复现仅在再次明确允许且平台允许时进行，使用不存在的输出目录（不要覆盖已有证据）：

```sh
.venv/bin/python -m btc_microstructure probe config/research.example.yaml --allow-network --seconds 20 --messages 20 --max-bytes 2097152 --output fixtures/protocol/manual-r1-02
```

不要循环运行该命令探测限制；拒绝、限流、超时后停止。当前机器的真实结果为BLOCKED，不能承诺复跑会通过。
打包复现：`.venv/bin/python reports/round1/package_protocol_probe.py`。
归档校验：`shasum -a 256 -c protocol_probe_review.zip.sha256`；CRC与每个payload哈希已由打包脚本检查。
ZIP排除venv/Git/缓存/构建产物/数据/环境文件/密钥，仅纳入指定源代码、规格、测试、fixtures和验证日志；`.zip.verify.json`是归档旁外部回执。
原文档交付清单与旧源码清单是历史版本证据，当前以probe版清单为准。

## 下一任务依赖

已完成本次可完成的DEV-002离线子集并明确现场阻塞。真实metadata、WS样例和运行预算仍未核验，因此正式采集的前置条件未满足。
本次到此停止，不自动进入tasks/03_vertical_slice.md，不启动长期采集或其他模块。
