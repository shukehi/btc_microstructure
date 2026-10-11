# R1-01 / DEV-001 独立复核报告

## 结论

**CHANGES_REQUESTED：先完成一轮小范围修复，再把 R1-01 标记为最终验收通过。**

已完成的工程基础可以保留，无须重做。提交中的原有 48 项测试在本次复核环境全部通过；但新增的、从原规格与原交接包提取的 15 项检查中，12 项失败、3 项通过。失败归属于 **两类问题**，不是 12 个独立模块故障。

本报告基于用户提交的源代码、规格、原始交接包和实际离线执行。未修改用户提交的源代码、测试或规格，未连接币安，未开始任务 02。未发现正在执行的采集、账户或下单实现。

## 1. 输入及完整性

| 检查 | 结果 |
|---|---|
| `foundation_review.zip` SHA-256 | `1a71528421cb446c0e49c6ab1035c4a9faa590da279d73a378b61043d7f82092`，与外部校验文件及回执一致 |
| 归档大小 / 条目数 | 159,621 字节 / 78 个文件 |
| ZIP CRC、路径穿越和符号链接检查 | 通过 |
| 包内清单 | 77 个列明 payload 文件大小与 SHA-256 一致；清单本身是第 78 个归档文件 |
| `preserved.sha256` | 23 项通过 |
| `implementation.sha256` | 10 项通过 |
| 对照原 Codex 交接包 | 23 个文件逐字节一致；README 保留原文前缀并追加；`.gitignore` 被替换 |
| 单独上传的 `01_foundation.md` | 与归档内交付报告逐字节一致 |
| 复核后再次核对 payload | 77 个文件均未被本次复核修改 |

证据：`evidence/integrity.json`、`evidence/post-review-source-integrity.json`。校验和说明此次收到的文件一致，不等于代码正确，也不等于安全审计已经完整。

## 2. 执行环境与复现范围

| 项目 | 用户交付记录 | 本次独立执行 |
|---|---|---|
| 操作系统 | macOS arm64 | Linux x86_64 |
| Python | 3.12.13 | 3.13.5 |
| Pydantic | 2.12.3 | 2.13.5 |
| pydantic-core | 2.41.4 | 2.46.5 |
| PyYAML | 6.0.3 | 6.0.3 |
| pytest | 8.4.2 | 9.0.2 |

**本次不是原 Python 3.12 锁定环境的独立重建。**当前环境没有 Python 3.12，尝试获取解释器因 DNS 解析失败而受阻；没有继续绕过网络限制。未在本环境重跑 ruff、mypy 或精确 lock 的全新安装。用户包中的 macOS 测试和静态检查日志已检查，但不冒充本次执行结果。

复核依赖使用本环境已有版本，未改写提交的依赖声明或锁文件。命令和状态见 `evidence/review-commands.json`，环境见 `evidence/review-environment.json`。

| 实际执行 | 结果 | 证据 |
|---|---|---|
| 原有 pytest 套件 | 48 通过、0 失败、0 跳过；退出 0 | `baseline-py313.log/xml` |
| 原始样例的模块 CLI | 退出 0，CONFIG_VALID | `cli-valid.log` |
| `invalid-grid.yaml` | 退出 1，明确指出 `features.grid_seconds` | `cli-invalid.log` |
| 配置摘要 | 与交付报告一致：`b037004b544ca834855821374bd65520a535b509c0b952d9c3f37a97a30d3b43` | `cli-valid.log` |
| 新增规格检查 | 12 失败、3 通过、0 跳过；退出 1 | `review-regressions.log/xml` |

原有测试确实检查了合法原样例、独立时间参数、未知字段、错误枚举、非法窗口、成本参数、精确数量、CLI、摘要和未核验能力。没有把“基础未实现”作为缺陷。

## 3. 必须修复 A：`.gitignore` 丢失原有秘密文件保护

**级别：P2；首次 Git 暂存或提交前修复。**

### 位置与依据

- 提交的 `.gitignore` 全文只有 11 行。
- 原始交接包的 `.gitignore` 包含 `.env`、`.env.*`、`*.pem`、`*.key` 和 `/codex-local-logs/`；当前版本没有。
- `AGENTS.md:17–23` 要求不得提交密钥和个人配置；`docs/06_operations.md` 第 8 节要求排除密钥、认证头及账户信息。
- 原始忽略文件完整副本附为 `gitignore.original-handoff.txt`。

### 复现

在临时、独立的 Git 目录中复制当前 `.gitignore`，使用 `git check-ignore --no-index` 检查，不初始化或修改用户项目：

| 路径 | 当前是否忽略 | 原交接包预期 |
|---|---:|---:|
| `.env` | 否 | 是 |
| `.env.production` | 否 | 是 |
| `private.key` | 否 | 是 |
| `certificate.pem` | 否 | 是 |
| `codex-local-logs/session.log` | 否 | 是 |
| `.venv/x` | 是 | 是 |

这是一个**可确认的保护规则回退**。它不说明已经发生泄密；当前交付报告明确项目尚未初始化 Git，复核也没有发现提交行为。ZIP 的白名单打包筛查不能替代未来 Git 工作流中的忽略规则。

### 最小修复

恢复原交接包的秘密文件和本机日志排除规则，保留需要的新规则，保留 `!.env.example` 的例外。补充临时 Git 仓库测试，确认敏感类别被忽略、人工 `.env.example` 仍可见。不要借此自动初始化用户仓库、暂存文件或推送。

`.gitignore` 不是秘密检测工具，也不能移除已经跟踪的秘密；本次修复只恢复被删除的第一层保护。

## 4. 必须修复 B：规格硬约束被当作可任意关闭的布尔选项

**级别：P2；DEV-001 配置语义验收缺口。**

### 位置

`src/btc_microstructure/config_models.py`：

- Storage：190–191 行。
- Quality：239–243 行。
- States：260 行。
- Split：272 行。
- Research：299–300 行。

这些字段只检查 `bool` 类型，没有相应语义约束。与 `automatic_trading`、`split.shuffle` 的明确禁止逻辑不同，以下反向设置通过模型校验和实际 CLI，并输出 CONFIG_VALID。

### 已用公共 CLI 路径验证的七个案例

每个案例只在原样例的临时副本中修改一个值，其余保持原样；`states.enabled` 仍为 true。

| 错误设置 | 原规格要求 |
|---|---|
| `storage.source_payload_preserved: false` | `docs/02_ingestion_and_replay.md:27–29`：每条消息保留原始 payload |
| `quality.unknown_as_null: false` | `docs/03_feature_specifications.md:11–13`：缺失统一 null，不能伪造零值 |
| `quality.known_gap_invalidates_flow_window: false` | 同一特征合同：已知缺口不能当作完整有效的零成交窗口 |
| `research.report_all_horizons: false` | `docs/00_scope.md` REQ-013，以及研究规格的全部候选期限披露要求 |
| `research.report_common_event_sample: false` | `docs/04_state_and_research_spec.md:13–17`：输出各期限样本与共同有效样本两种汇总 |
| `states.freeze_thresholds_in_validation_and_test: false` | `docs/04_state_and_research_spec.md:36–40`：验证与测试折阈值冻结 |
| `research.split.purge_by_actual_label_intervals: false` | `docs/04_state_and_research_spec.md:100–108`：按真实标签区间清除跨界训练标签 |

当前结果：七个案例均返回 0，而应在本版支持的配置语义下明确拒绝，并指出字段路径与原因。

### 影响边界

这里证明的是“配置校验接受了与规格矛盾的请求”，**没有证明已经发生数据丢失、数据泄漏或错误策略收益**。相应采集、特征与研究模块尚未实现。现在修复可以避免后续模块把 CONFIG_VALID 当成全部语义要求已满足。

### 最小修复

对这些规范明确要求的条件增加严格的固定值或语义校验，在适用模块启用时拒绝违反要求的配置。保留原配置字段、合法样例和所有旧测试，不通过删除字段或修改规范来解决。

继续拒绝整数 0/1 和布尔字符串等不符合现有严格类型合同的输入。扩展同类校验时区分“必须遵守的规格”与“可修改工程默认值”，**不要把所有默认 true/false 一律锁死**。

新增检查见 `checks/test_review_regressions.py`。它包含 5 个忽略规则失败案例、7 个配置约束失败案例，以及 3 个正向/已实现行为对照。当前失败日志是实际执行记录，不是构造的通过报告。

## 5. 非阻塞事项与明确排除的指控

### C1：无 Git 基线

提交报告如实说明未初始化 Git，已有逐文件哈希可用于本次材料追溯。缺 Git 提交不等于代码或测试不存在。修复 A 后，下一轮工作前由用户按其工作流建立版本基线；本次不要求擅自初始化或推送。

### C2：增加研究期限后，保护窗口如何同步

模型接受 `research.horizon_seconds=[86400]`，但仍保留原来的 14400 秒分割保护间隔和 bootstrap 最小区块。也接受将两者直接设为 1 秒。

研究规格第 6 节描述保护间隔与区块长度覆盖最大研究期限的默认策略，当前配置没有这种跨字段检查。不过实际研究器尚未实现，未来也可以在研究启动时计算有效下限，因此**不据此断言已发生标签泄漏或统计失真，也不将其列为本次两个阻塞缺陷之一**。

在研究模块实现前，应写清选择：不一致配置当场拒绝，或明确计算并记录实际有效值。不得悄悄沿用短窗口。回看窗口与研究期限继续保持独立。

### C3：加载后的配置对象可变

在本次 Pydantic 2.13.5 环境中，列表原地追加可绕过字段校验；`reconnect_initial_seconds=1000` 触发 after-validator 异常后，实例仍可能保留非法值。当前项目没有此类配置修改调用，所以这里只记录未来接口风险，不作为已触发的业务缺陷。可在未来采用不可变运行配置、受控重建或使用前再校验；仅设置 `frozen=True` 不一定能冻结嵌套列表。

### C4：端点与数据源真实性

模型允许一般 URL 或未验证的流名不等于已经获准访问；源码保留 UNVERIFIED 能力状态。这是任务 02 的运行核验边界。本次不要求用猜测的交易所协议替换官方或现场证据，也没有进行任何行情连接。

### C5：配置文件相对路径

README 已明确 `./data` 相对于配置文件目录，原样例指向 `config/data`。这是一项已记录的设计选择，不把它单独算作错误。后续各入口必须采用相同合同。

## 6. 交还 Codex 的验收要求

1. 本次只修复 A/B，不进入 `tasks/02_protocol_probe.md`。
2. 保留原始规格、原配置样例、人工预期和原 48 项测试；为缺口增加有效的回归断言。
3. 在用户原来的 Python 3.12 虚拟环境和锁定依赖中重跑原套件、新检查、有效/无效 CLI、ruff、mypy；无法执行的项目保留真实状态。
4. 每个受限配置返回非零退出码并包含对应字段路径；合法原样例仍通过，摘要保持一致，除非有正式记录的摘要协议变更。
5. 验证秘密文件忽略规则，但不读取真实秘密、不创建真实密钥、不推送。
6. 保存修复前失败日志和修复后新日志，提交新的修复报告及新版 ZIP；旧报告不要被改成“从未发生过失败”。
7. 原始全部候选研究期限仍可配置，不将任何交易周期写死。

## 7. 复现方式

从用户解压后的项目根目录执行，替换审查包路径；命令不安装依赖、不连接行情：

```sh
PYTHONPATH=src .venv/bin/python -m pytest -q
PYTHONPATH=src .venv/bin/python -m pytest -q /path/to/review/checks/test_review_regressions.py
.venv/bin/python -m btc_microstructure validate-config config/research.example.yaml
```

修复前，新增测试预期显示 12 失败 / 3 通过；修复后，应真实通过，不能使用 skip、xfail 或改为“接受 false”来消除失败。当前复核的精确 Python 与依赖见环境 JSON，不把跨环境结果冒充原机结果。

审查包中的 `explore_edges.py` 保留实际执行版本；`explore_edges_portable.py` 是便于用户在项目根目录执行的移植副本，后者未用于上述历史执行日志。探索输出仅表示输入是否被接受，不代表其每一项都被定性为缺陷。真正的阻塞检查以本报告 A/B 与 `test_review_regressions.py` 为准。
