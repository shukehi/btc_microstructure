任务编号：R1-01 / DEV-001  
原规格版本：0.1  
代码版本/工作树状态：无 Git 仓库，无提交号；开始时 `git status --short --branch` 返回 128（not a git repository），未初始化或提交。  
本次范围：工程基础、完整类型化配置、离线校验 CLI、配置层单元测试。R1-01 验证 PASS；完整 M0 仍为 PARTIAL。  
未实现范围：协议 probe、采集、保存/重放、特征与标签计算、研究与成本计算、账户和下单。  
执行环境：macOS 27.0.1（Build 26A434）/ Darwin 27.0.0、arm64；默认 Python 3.14.7，实际开发和测试 Python 3.12.13，项目 `.venv`。依赖完整版本见根目录 `requirements-dev.lock`；运行依赖 Pydantic 2.12.3、PyYAML 6.0.3；pytest 8.4.2、ruff 0.14.0、mypy 1.18.2、setuptools 80.9.0。环境记录见 `environment.json`。

## 修改文件

| 文件 | 用途 |
|---|---|
| `.gitignore` | 排除虚拟环境、缓存、构建元数据、数据和日志 |
| `pyproject.toml` | src 布局、Python 基线、依赖、安装入口与检查配置 |
| `requirements-dev.lock` | 实际安装并通过验证的直接与传递依赖精确版本 |
| `src/btc_microstructure/__init__.py` | 包版本与范围声明 |
| `src/btc_microstructure/__main__.py` | `python -m` 入口 |
| `src/btc_microstructure/config_models.py` | 全部原配置字段及嵌套类型、枚举、范围、跨字段校验 |
| `src/btc_microstructure/config.py` | 安全 YAML、重复键拒绝、路径校验、规范化 SHA-256 |
| `src/btc_microstructure/capabilities.py` | 独立的待核验能力边界，不从配置推断真实能力 |
| `src/btc_microstructure/cli.py` | 纯离线 validate-config、字段错误和退出码 |
| `tests/unit/conftest.py` | 原配置 fixture 与 socket/DNS 禁网保护 |
| `tests/unit/test_config.py` | 48 项离线配置测试（含参数化案例） |
| `README.md` | 保留全部原文，追加已实现范围、安装、命令、摘要和路径合同 |
| `reports/round1/01_foundation.md` | 本报告 |
| `reports/round1/commands.json`, `environment.json` | 检查命令、真实退出码与有限环境信息 |
| `reports/round1/*.log`, `pytest.xml` | 安装、首次测试、最终检查、CLI 与 JUnit 证据 |
| `reports/round1/preserved.sha256`, `original-integrity.json`, `implementation.sha256` | 编辑前哈希、原规格完整性核对与本次源码版本标识 |

未修改原开发规格、任务卡、原配置样例或 `examples/synthetic_cases.json` 固定预期。
23 个受保护文件的编辑前哈希复核全部通过；另按原 HANDOFF_MANIFEST 核对 15 个 original_spec 文件，README 仅比对原长度前缀，其余逐字节哈希一致。

配置摘要为完整已验证模型的规范化 JSON（UTF-8、键排序、紧凑分隔、Decimal 无冗余尾零），再取 SHA-256。列表顺序保留；不添加运行时刻、本机解析路径或环境变量。原样例摘要：
`b037004b544ca834855821374bd65520a535b509c0b952d9c3f37a97a30d3b43`。

本轮交付整理仅改报告并新增验证/打包材料，未改源代码、测试、原样例或依赖声明：

| 文件 | 用途 |
|---|---|
| `reports/round1/verify_delivery.py` | 离线重跑、完整 stdout/stderr、退出码与 JUnit 汇总 |
| `reports/round1/delivery-*.log`, `delivery-commands.json`, `delivery-pytest.xml` | 本轮实际操作系统、Python、依赖、测试、CLI、静态检查和完整性检查原始记录 |
| `reports/round1/invalid-grid.yaml` | 从原样例复制的人工无效配置，仅将 grid 改为 0；不替换原样例 |
| `reports/round1/initial-ruff-error.log`, `initial-mypy-error.log`, `historical-errors-provenance.md` | 历史 FAIL 完整诊断及真实会话来源说明 |
| `reports/round1/package_foundation.py` | 交付白名单打包、秘密模式检查、CRC 与 SHA-256 验证 |
| `reports/round1/package_manifest.json`, `packaging-audit.json` | 包内逐文件哈希、大小、排除规则与脱敏说明 |
| 根目录 `foundation_review.zip`, `.zip.sha256`, `.zip.verify.json` | 实际 ZIP、外部校验和与归档验证回执 |

实际依赖清单（本轮 `pip list --format=json` 已执行并保存）：

```text
annotated-types==0.8.0
iniconfig==2.3.1
mypy==1.18.2
mypy_extensions==1.1.0
packaging==26.3
pathspec==1.1.1
pluggy==1.6.0
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
pip==25.0.1
btc-microstructure==0.1.0 (editable)
```

## 需求与验收映射

| requirement_id | dev_task / subset | test_id | 证据文件 | 状态 |
|---|---|---|---|---|
| REQ-001 | DEV-001 全样例字段、类型、未知键和枚举 | TEST-CFG-01/02 + 新增严格 schema 测试 | `tests/unit/test_config.py`, `pytest.xml` | PASS |
| REQ-011 | DEV-001 期限、回看窗口、更新频率独立配置；不计算标签 | TEST-CFG-01 + 独立性断言 | `test_cfg_01_independent_horizons` | PASS（配置层） |
| REQ-015 | DEV-001 禁用成本允许 null；启用缺参数明确失败 | TEST-COST-01 | `test_cost_01_missing_required`, `test_exact_cost_and_zero_are_explicit` | PASS（配置层） |
| REQ-006 | DEV-001 保留 UNKNOWN 与未核验单位证据 | 新增 OI/能力边界断言；TEST-OI-01/02 未实现 | `test_original_sample_complete`, `test_declared_probe_does_not_verify_capabilities` | PASS（配置层） |
| REQ-016 | DEV-001 只读模式、自动交易禁用、离线入口 | TEST-OPS-01 本范围 + 禁网保护 | `conftest.py`, CLI 单元测试及源码复查 | PASS（本范围） |
| REQ-001 | DEV-001 摘要、YAML、路径、CLI 退出码与错误路径 | 新增测试 | `test_digest_order_and_parameters`, `test_yaml_and_paths`, CLI 测试 | PASS |
| REQ-015 | DEV-012 成本公式 | TEST-COST-02 | 本次无实现 | NOT_RUN（不属本任务） |

## 实际执行

全部命令工作目录为 `/Users/aries/Dve/btc_microstructure`。`.venv/bin/python` 即 JSON 命令记录中的同一绝对解释器。

| 命令 | 工作目录 | 退出码 | 通过/失败/跳过数量 | 日志位置 |
|---|---|---|---|---|
| `ls -la` | 项目根目录 | 0 | — | 会话工具输出（实际文件清单已展示） |
| `git status --short --branch` | 同上 | 128 | 无 Git 仓库 | 会话工具输出 |
| `python3 --version` / `python3.12 --version` | 同上 | 各 0 | 3.14.7 / 3.12.13 | 会话工具输出 |
| `command -v python` / `python --version` | 同上 | 1 / 127 | 无无版本后缀的系统 python 命令 | 会话工具输出 |
| `python3.12 -m venv .venv` | 同上 | 0 | 创建项目环境 | 会话工具输出 |
| `.venv/bin/python -m pip install 'pydantic==2.12.3' 'PyYAML==6.0.3' 'pytest==8.4.2' 'setuptools==80.9.0' 'ruff==0.14.0' 'mypy==1.18.2' 'types-PyYAML==6.0.12.20250915'` | 同上 | 0 | 安装成功 | 会话工具输出；精确解析版本见 lock |
| `.venv/bin/python -m pip freeze --exclude-editable > requirements-dev.lock` | 同上 | 0 | — | `requirements-dev.lock` |
| `.venv/bin/python -m pip install --no-build-isolation --no-deps -e .` | 同上 | 0 | editable 安装成功 | `install.log` |
| `.venv/bin/python -m pytest -q`（首次） | 同上 | 0 | 48 / 0 / 0 | `pytest-initial.log` |
| `.venv/bin/ruff check src tests`（首次） | 同上 | 1 | FAIL：1 个未使用导入，已修正 | `initial-ruff-error.log` |
| `.venv/bin/mypy src`（首次） | 同上 | 1 | FAIL：2 处 PyYAML 未类型化调用，已修正 | `initial-mypy-error.log` |
| `.venv/bin/ruff format src tests` | 同上 | 0 | 最后一次 8 文件无需修改 | 会话工具输出 |
| `.venv/bin/python -m pytest -q --junitxml=reports/round1/pytest.xml` | 同上 | 0 | **48 / 0 / 0** | `pytest.log`, `pytest.xml` |
| `.venv/bin/ruff check src tests` | 同上 | 0 | 全部通过 | `ruff.log` |
| `.venv/bin/ruff format --check src tests` | 同上 | 0 | 8 文件通过 | `format.log` |
| `.venv/bin/mypy src` | 同上 | 0 | 6 源码文件通过 | `mypy.log` |
| `.venv/bin/python -m pip check` | 同上 | 0 | 无损坏依赖 | `pip-check.log` |
| `.venv/bin/python -m btc_microstructure validate-config config/research.example.yaml` | 同上 | 0 | CONFIG_VALID，能力均 UNVERIFIED | `cli-module.log` |
| `.venv/bin/btc-microstructure validate-config config/research.example.yaml` | 同上 | 0 | CONFIG_VALID，摘要相同 | `cli-script.log` |
| `shasum -a 256 -c reports/round1/preserved.sha256` | 同上 | 0 | 23 文件通过 | `preservation.log` |
| Python hashlib 按 HANDOFF_MANIFEST 校验原文件（会话内 heredoc） | 同上 | 0 | 15 文件通过（README 原前缀） | `original-integrity.json` |

无效参数和未知字段的拒绝均包含具体字段路径/原因断言。子进程测试实际验证有效 CLI 退出 0、无效 CLI 退出 1；未通过改变原预期、skip、xfail 或放宽断言取得绿色结果。

同会话只读 code-reviewer 已复查并复跑上述核心检查：0 个实质性发现，48 项通过。其记录见 `review.md`；未启动任务 04。无 Git 提交时以 `implementation.sha256` 标识本次源码、依赖锁与测试内容。

### 本轮交付重跑（2026-10-11 UTC / 2026-10-10 America/Chicago）

执行入口：`.venv/bin/python reports/round1/verify_delivery.py`，工作目录为项目根目录，退出码 0。
该脚本独立记录每个子命令状态；缺 Git 仓库作为已知元数据限制单列 FAIL，不掩盖其退出码。

| 命令 | 工作目录 | 退出码 | 状态 / 通过、失败、跳过 | 完整日志 |
|---|---|---|---|---|
| `sw_vers` | 项目根目录 | 0 | PASS | `delivery-os.log` |
| `.venv/bin/python --version` | 项目根目录 | 0 | PASS | `delivery-python.log` |
| `.venv/bin/python -m pip list --format=json` | 项目根目录 | 0 | PASS | `delivery-dependencies.log` |
| `git status --short --branch` | 项目根目录 | 128 | FAIL：not a git repository；提交信息 BLOCKED | `delivery-git-status.log` |
| `.venv/bin/python -m pytest -q --junitxml=reports/round1/delivery-pytest.xml` | 项目根目录 | 0 | PASS：48 通过 / 0 失败 / 0 跳过 | `delivery-pytest.log` |
| `.venv/bin/python -m btc_microstructure validate-config config/research.example.yaml` | 项目根目录 | 0 | PASS | `delivery-cli-valid.log` |
| `.venv/bin/python -m btc_microstructure validate-config reports/round1/invalid-grid.yaml` | 项目根目录 | 1 | PASS：预期拒绝无效配置（不是检查失败） | `delivery-cli-invalid.log` |
| `.venv/bin/btc-microstructure validate-config config/research.example.yaml` | 项目根目录 | 0 | PASS | `delivery-cli-script.log` |
| `.venv/bin/ruff check src tests` | 项目根目录 | 0 | PASS | `delivery-ruff.log` |
| `.venv/bin/ruff format --check src tests` | 项目根目录 | 0 | PASS | `delivery-format.log` |
| `.venv/bin/mypy src` | 项目根目录 | 0 | PASS | `delivery-mypy.log` |
| `.venv/bin/python -m pip check` | 项目根目录 | 0 | PASS | `delivery-pip-check.log` |
| `shasum -a 256 -c reports/round1/preserved.sha256` | 项目根目录 | 0 | PASS | `delivery-preservation.log` |
| `shasum -a 256 -c reports/round1/implementation.sha256` | 项目根目录 | 0 | PASS | `delivery-implementation.log` |

历史失败日志从本会话首次执行的真实工具输出逐字保存，来源见 `historical-errors-provenance.md`。
没有回退代码以制造失败，也没有补造此前未保存的安装下载原始日志。

打包命令：`.venv/bin/python reports/round1/package_foundation.py`，工作目录为项目根目录；
首次执行退出 1（FAIL）：秘密模式筛查命中测试中固定的 `https://user:password@example.com` 人工地址及对应 JUnit 用例名称，完整输出见 `packaging-initial-error.log`。人工确认这三处均为同一个公开人工测试值后，仅在筛查规则中豁免此精确字符串；未修改测试或日志。重新执行退出 0（PASS）。ZIP CRC/逐文件哈希结果见根目录 `foundation_review.zip.verify.json`（文件存在即脚本验证完成）。
仅收录明确列出的项目文本材料；排除虚拟环境、Git、OMX 状态、缓存、构建产物、大型数据、环境文件及秘密。
脱敏位置说明见 `packaging-audit.json`；本轮选中文件未发现敏感值，无需修改日志内容。

## 数据证据

- synthetic fixture：原样例 `config/research.example.yaml`，SHA-256 `d0556ac8d91986da157bb2844a415d53536b67df92856518adf8fda4058490e4`；另有测试内人工修改参数，无行情时间范围。
- 原固定合成样例 `examples/synthetic_cases.json`，SHA-256 `e2c081837f81bc2f0a97491ec355faca11c2130e2afe679faaceda74f0bffa72`，完整保留；本次不运行其中后续特征/成本计算案例。
- 官方协议资料核验：本次无；只读取原项目规格，未把原说明作为新增核验证据。
- 现场响应：无；未连接币安，无真实行情 fixture，无采集时间范围。
- 网络使用仅为开发依赖安装；普通 import、配置校验与 pytest 无交易所网络调用。

## 失败、阻塞与限制

- PASS：R1-01 软件配置与工程基础离线验收，本轮 48 通过 / 0 失败 / 0 跳过。
- FAIL：首次打包筛查误报人工测试 URL，已保留错误并针对精确人工值修正规则；后续打包通过。历史首次 ruff/mypy 检查已保留完整错误并已修正；本轮 Git 状态检查退出 128，完整错误见 `delivery-git-status.log`。
- BLOCKED：提交号、Git diff 与版本控制状态验收因当前不是 Git 仓库而无法提供。未自动初始化。
- NOT_RUN：Linux x86_64、其他解释器/平台、真实公共端点核验、24h 运行与全部后续模块测试。
- 无 Git 仓库，不能提供提交号或 Git diff；未擅自初始化。其他操作系统的安装兼容性未验证；lock 为精确版本锁，未包含分发包哈希。
- 配置有效不代表任何行情能力/模块已实现。能力边界明确返回 UNVERIFIED，包括实际元数据、路由预算、盘口覆盖、事件时间来源支持与 OI 单位。`protocol_probe_completed` 仅为配置字段，不作为证据。
- 路径按配置文件目录解释；校验不创建目录、不修改文件、不检验运行写权限和空间。原样例 `./data` 的实际相对位置为 `config/data`，已在 README 说明。
- 完整 M0 为 PARTIAL；真实接口验收未执行，本任务无须等待真实接口才能完成离线配置层。

## 用户复现

在项目根目录执行以下已实现命令；首次 Python 3.12 建环境、精确版本安装和 editable 安装均已执行。第二个全新环境的 lock 批量安装复现为 NOT_RUN，跨平台须按当地审批与网络设置安装依赖。

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.lock
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
.venv/bin/python -m btc_microstructure validate-config config/research.example.yaml
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src
.venv/bin/python -m pip check
shasum -a 256 -c reports/round1/preserved.sha256
```

ZIP 内文件以项目根目录为相对根，可解压到新目录后从该目录执行上述命令。
当前机器复跑全部交付检查：`.venv/bin/python reports/round1/verify_delivery.py`；
复现无效配置拒绝：`.venv/bin/python -m btc_microstructure validate-config reports/round1/invalid-grid.yaml`，预期退出 1。
重新生成并验证交付 ZIP：`.venv/bin/python reports/round1/package_foundation.py`，预期退出 0。
校验归档：`shasum -a 256 -c foundation_review.zip.sha256`。

预期：有效配置退出 0；48 项测试通过、无失败或跳过；静态检查退出 0；原文保护哈希通过。可复制配置到临时文件，把 `features.grid_seconds` 改为 0 后执行同一 validate-config：预期退出 1，stderr 含字段路径与正数约束原因；不要修改原样例。

## 下一任务依赖

DEV-001 可供后续任务使用；若用户另行指定任务 02，可从离线 probe 逻辑开始。真实协议、来源单位与现场访问验收仍待后续授权范围与证据。本次到此停止，没有启动任务 02 或任何采集。
