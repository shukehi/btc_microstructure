# BTC 永续合约微观结构研究系统
## 开发规格 v0.1 · Binance BTCUSDT

**文档基准日期：2026-10-10**  
**交付状态：开发规格初稿；不是已实现的软件。**  
**已执行：官方资料核验、需求与规则编写、文档包静态检查。**  
**未执行：行情端点连通性测试、真实数据采集、软件单元测试、性能测试、策略回测。**

本包将已经明确的需求转为可实施、可测试的规格：Python 单机、Binance BTCUSDT USDⓈ-M 永续、同交易所 BTCUSDT 现货作为默认对照、公共行情只读、多研究期限并行检验。本文不预设有效交易周期，不承诺预测优势或收益。

### 阅读入口

| 文件 | 正文内容 | 开发用途 |
|---|---|---|
| [00_scope.md](docs/00_scope.md) | 范围、需求编号、设计选择、完成标准 | 明确项目边界 |
| [01_data_contracts.md](docs/01_data_contracts.md) | 接口、字段、单位、时间、唯一键、覆盖限制 | 实现适配器和数据模型 |
| [02_ingestion_and_replay.md](docs/02_ingestion_and_replay.md) | 采集、订单簿状态机、持久化、重放、时间视图 | 实现数据管线 |
| [03_feature_specifications.md](docs/03_feature_specifications.md) | 特征公式、窗口、单位、空值、来源、测试样例 | 实现特征引擎 |
| [04_state_and_research_spec.md](docs/04_state_and_research_spec.md) | 状态、收益标签、MFE/MAE、样本外检验、成本 | 实现研究模块 |
| [05_testing_and_acceptance.md](docs/05_testing_and_acceptance.md) | 测试案例、阶段验收、需求追溯 | 编写测试与验收报告 |
| [06_operations.md](docs/06_operations.md) | 配置、运行契约、健康检查、恢复、安全 | 编写运行入口与监控 |
| [07_implementation_tasks.md](docs/07_implementation_tasks.md) | 模块接口、任务依赖、交付顺序 | 安排实现工作 |
| [08_sources_and_decisions.md](docs/08_sources_and_decisions.md) | 英文一手资料、决策记录、待核验事项 | 核对外部事实与变更 |
| [research.example.yaml](config/research.example.yaml) | 参数化研究与采集配置 | 实现配置模型的样例 |
| [synthetic_cases.json](examples/synthetic_cases.json) | 人工构造输入与预期结果 | 编写测试的固定样本，不是真实行情 |

### 规范措辞

“必须”表示发布门槛；“应”表示默认实现；“可”表示非必需扩展。所有数值阈值除特别说明外均为**工程或研究默认值**，不是交易所限制、实測性能或验证后的交易参数。

正文用 `[S01]` 等标识外部资料，用 `REQ-*` 标识需求，用 `TEST-*` 标识待实现测试。外部接口仅在“资料已核验”层面成立；实际部署前仍必须完成协议冒烟测试。

### 本版最重要的约束

订单流、持仓与市场解释分层。禁止将 OI 变化命名成已识别的新增多空仓；禁止将聚合成交消息数称为原始成交笔数；禁止将强平快照之和称为全市场爆仓量。数据缺失不等于零，当时可用的数据不等于事后补齐的数据。

先实现 M0/M1 的配置、原始日志、适配器与重放，再实现 L2 和特征。所有测试在本文中是**规范与预期**，并非执行成功的报告。

### 软件开发进度：R1-01 / DEV-001

已实现工程基础、完整 v0.1 类型化配置、离线 `validate-config` 和单元测试。
上面的交付状态与未执行清单是原文档交付时的说明；本次软件验证证据见
[任务报告](reports/round1/01_foundation.md)。目前没有采集器、特征计算、研究、
账户或交易功能。配置中的 `states.enabled` 等开关仅被建模，并不意味着对应模块已实现。

开发与测试使用 macOS arm64、Python 3.12.13、本项目 `.venv`。
`requirements-dev.lock` 锁定本次实际安装和测试的运行、测试、静态检查及构建依赖。
从项目根目录运行（安装依赖需要包索引网络，验证与测试无需网络）：

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.lock
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
.venv/bin/python -m btc_microstructure validate-config config/research.example.yaml
.venv/bin/btc-microstructure validate-config config/research.example.yaml
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src
```

成功验证退出码为 0，输出 `CONFIG_VALID`、SHA-256 和仍待核验的能力；
无效配置退出码为 1，stderr 列出字段路径和原因；CLI 用法错误退出码为 2。
不会打印完整配置、输入错误值或环境变量，不展开环境变量或 `~`。
未知字段、重复 YAML 键、非法布尔字符串、重复窗口、非正时间长度会被拒绝。
研究期限 `research.horizon_seconds`、指标回看窗口 `features.*_lookbacks_seconds`
和特征更新频率 `features.grid_seconds` 相互独立。延迟允许显式为 0，未知值须为 null。
数量和费率/滑点接受精确十进制字符串或整数，不接受 YAML 浮点；启用成本分析必须
明确数量、双边费率、双边延迟及额外滑点，执行与资金模型必须为支持的枚举。

摘要定义：对完整已验证模型作 UTF-8 JSON 序列化，键排序、无多余空格，
Decimal 用无冗余尾零的十进制字符串表示，然后计算 SHA-256。
列表顺序保留，配置中路径字符串保留；不加入解析后的本机绝对路径、时间戳或环境。
同一配置更换 YAML 键顺序不改变摘要，改变期限、窗口或频率会改变摘要。

路径相对于配置文件所在目录解释（因此原样例的 `./data` 指向 `config/data`）；
校验不创建目录，拒绝目录路径穿过已有文件；非 null 的预算文件必须存在。
写权限、剩余空间和目录创建由未来启动流程检查。
`capabilities.py` 保留后续启动能力检查边界：真实协议、元数据、事件时间来源支持、
盘口价格范围覆盖、OI 单位证据和缺流降级声明均需独立证据。
本任务没有检验快照档位能否覆盖实际价格带，也不把
`protocol_probe_completed: true` 当成核验凭据；`oi_unit: UNKNOWN` 保持未知。
本次结果不等于完整 M0 或任何现场接口验收。

### 软件开发进度：R1-02 / DEV-002 首轮子集

新增可注入传输层的有界只读 `probe`，仅检查 BTCUSDT USDⓈ-M 永续元数据和
聚合成交流，不启用原全量配置中的其他来源。资料核验、人工模拟和真实响应分别记录，
现场状态以 [R1-02 报告](reports/round1/02_protocol_probe.md) 为准。
本模块不会采集长期行情、计算特征或更改 `protocol_probe_completed`。

安装新增的精确依赖版本后，以下命令默认不访问网络，输出 NOT_RUN 并退出 2：

```sh
.venv/bin/python -m pip install -r requirements-dev.lock
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
.venv/bin/python -m btc_microstructure probe config/research.example.yaml --output reports/round1/probe/manual-disabled
```

仅在明确获准真实访问且平台允许时加 `--allow-network`；输出目录必须不存在，避免覆盖证据：

```sh
.venv/bin/python -m btc_microstructure probe config/research.example.yaml --allow-network --seconds 20 --messages 20 --max-bytes 2097152 --output fixtures/protocol/manual-r1-02
```

此命令最多一次 REST GET、一次 WS 连接，不重试、不跟随重定向、不切换备用入口。
REST 被拒绝或限流后停止，不尝试 WS。只用固定官方 URL，拒绝配置中的自定义 probe 入口。
默认总时限 30 秒，硬上限 60 秒（含最多 1 秒清理）、连接 5 秒、读空闲 5 秒；
有效消息上限 20、REST 与 WS 应用数据总预算最多 2 MiB、WS 单消息上限 64 KiB。
REST 最多额外读 1 字节识别超限，保留样本仍截断于预算并标记 truncated。
为避免超出总预算，剩余空间不足一条最大 WS 消息时提前停止接收。
这些是本地工程上限，不是交易所额度；响应头/实际 rateLimits 单独保留，连续采集始终未启用。
退避重试上限为 0：遇 429 保存 Retry-After 后停止，没有自动恢复或重连。

成功的有界协议样本退出 0；拒绝、超时或协议错误退出 1；未显式启用网络退出 2。
即使有限样本通过，也不能据此声称连续采集预算、所有来源或数量单位已核验。
聚合数量只保留原生十进制字符串，`quantity_unit=UNKNOWN`；本次不实现成交标准化。
输出 `protocol_probe_report.json`、`endpoint_budget.json` 及带哈希的有限原始响应文件。
`.bin` 是原始公开响应字节；元数据可能包含交易所整张符号表，但只校验 BTCUSDT，不订阅其他品种。
传输层保持 TLS 校验，使用库默认的 `trust_env=False`，不读取 `.netrc` 或环境认证信息，
不打印完整环境、认证头、代理信息或底层异常中的敏感细节，不修改系统网络/代理设置。

默认 pytest 仍纯离线：

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q tests/unit/test_config.py tests/unit/test_foundation_guards.py
.venv/bin/python -m pytest -q review/R1_01/checks/test_review_regressions.py
```

人工协议样例在 `fixtures/synthetic/probe/`；真实执行证据在 `fixtures/protocol/`，不得互相冒充。
只有显式 CLI 会触发真实网络探测；普通导入、配置校验和默认测试不会连接交易所。
