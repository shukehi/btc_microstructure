任务编号：R1-01 / DEV-001 修复 A/B  
原规格版本：0.1  
代码版本/工作树状态：当前目录不是 Git 仓库；无提交号，不初始化、不暂存、不推送。以 `fix/fixed-implementation.sha256` 标识修复版源代码和测试。  
本次范围：按独立审查 CHANGES_REQUESTED 复现并修复秘密文件忽略规则与七个配置硬约束。修复验收 PASS；完整 M0 仍为 PARTIAL。  
未实现范围：R1-02、协议 probe、采集器、研究器、账户、下单；C1–C5 非阻塞事项不扩展处理。

## 执行环境

macOS 27.0.1，Build 26A434，Darwin 27.0.0，arm64；实际解释器为项目 `.venv/bin/python`，Python 3.12.13。
Pydantic 2.12.3、pydantic-core 2.41.4、PyYAML 6.0.3、pytest 8.4.2、ruff 0.14.0、mypy 1.18.2；pip 25.0.1。
未安装、升级或修改依赖。17 个 lock 条目均与实际安装版本精确一致，见 `fix/after/integrity.json`。
这次是原 macOS/Python 3.12 锁定环境的复现；没有将审查方 Linux/Python 3.13 的结果冒充本机结果。
完整依赖：

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

```

## 修改文件

| 文件 | 修改与用途 |
|---|---|
| `.gitignore` | 恢复原交接规则 `.env`、`.env.*`、`*.pem`、`*.key`、`/codex-local-logs/` 与 `!.env.example`；恢复构建/editor 排除项，保留现有嵌套 data/logs 忽略规则 |
| `src/btc_microstructure/config_models.py` | 六项严格 true 硬约束；状态模块启用时必须冻结阈值；不更改字段、原默认配置或摘要协议 |
| `tests/unit/test_foundation_guards.py` | 新增 58 个回归案例：逐字段 false/整数/字符串拒绝、七个真实 CLI 子进程、临时 Git 行为测试、摘要固定值、可选模块与可修改默认值对照 |
| `reports/round1/01_foundation_fix.md` | 本修复报告；旧 `01_foundation.md` 原样保留 |
| `reports/round1/fix/run_checks.py` | 本次复现和修复后验证命令执行器；拒绝覆盖已有修复前失败记录 |
| `reports/round1/fix/verify_fix_integrity.py` | 原始受保护输入与精确依赖校验、七项 CLI 日志生成 |
| `reports/round1/fix/before/` | 真实修复前日志、JUnit、`.gitignore`/配置模型快照（文本副本） |
| `reports/round1/fix/after/`, `fix/fixtures/` | 真实修复后日志、JUnit、七个仅供离线拒绝测试的人工配置副本 |
| `reports/round1/fix/protected.json`, `fixed-implementation.sha256`, `implementation.diff` | 原始材料哈希、当前源码哈希、仅 A/B 的代码 diff |
| `reports/round1/package_foundation_fixed.py` | 单独生成新 ZIP；不覆盖旧打包脚本或旧 ZIP |
| `reports/round1/fixed_package_manifest.json`, `fixed-packaging-audit.json` | 修复版包内清单、秘密筛查与排除说明 |
| 根目录 `foundation_review_fixed.zip`、`.zip.sha256`、`.zip.verify.json` | 新归档、外部校验和与实际打包验证回执 |

原规格、任务卡、配置样例、固定合成预期、原 48 项测试、独立审查输入及证据、原交付报告和旧 ZIP 均未修改；47 个受保护文件逐项哈希一致。
原 `implementation.sha256` 及旧 package_manifest 是历史版本证据，继续原样保留；当前版本使用 `fix/fixed-implementation.sha256` 和 `fixed_package_manifest.json`，不要把历史源码摘要当作当前版本摘要。
修复前模型、原测试与审查定位一致；没有发现需要改变本次 A/B 修复范围的本地版本差异。

## 修复行为与规格依据

- 保留原始 payload：`storage.source_payload_preserved` 必须为 true（docs/02 第 2 节）。
- 缺失与缺口：`quality.unknown_as_null`、`quality.known_gap_invalidates_flow_window` 必须为 true（docs/03 第 1 节）。
- 完整候选期限和共同样本：`research.report_all_horizons`、`report_common_event_sample` 必须为 true（REQ-013、docs/04 第 2 节）。
- 实际标签区间 purge：`research.split.purge_by_actual_label_intervals` 必须为 true（docs/04 第 6 节）。
- 状态阈值冻结：`states.enabled=true` 时，`freeze_thresholds_in_validation_and_test` 必须为 true（docs/04 第 3.1 节）。状态模块禁用时允许该布尔值为 false。

使用严格 bool 类型后再做语义校验，避免单独 `Literal[True]` 会接受整数 1 的陷阱。以上字段始终拒绝 0/1 和字符串。
未锁死其他默认开关；`require_oi_expansion`、`require_l2_confirmation` 等仍可配置。
研究期限、回看窗口、特征频率依然独立；原样例摘要仍是
`b037004b544ca834855821374bd65520a535b509c0b952d9c3f37a97a30d3b43`。

## 需求与验收映射

| requirement_id | dev_task / subset | test_id | 证据文件 | 状态 |
|---|---|---|---|---|
| REQ-016 / AGENTS 安全合同 | DEV-001 修复 A | 新增 TEST-FIX-IGNORE；独立 sensitive-ignore 检查 | `test_sensitive_ignore_rules_use_git`；独立测试日志 | PASS |
| REQ-002 | DEV-001 修复 B / payload | TEST-CFG-02 语义回归；TEST-STORE-01 仅配置前置 | `test_required_guards_reject_false_and_non_booleans`，CLI 日志 | PASS（配置层） |
| REQ-012 | DEV-001 修复 B / 未知和缺口 | TEST-CFG-02 语义回归；TEST-QUALITY-01 仅配置前置 | 同上 | PASS（配置层） |
| REQ-013 | DEV-001 修复 B / 全期限及共同样本 | TEST-CFG-01/02 回归；TEST-RES-03 仅配置前置 | 同上 | PASS（配置层） |
| REQ-014 | DEV-001 修复 B / 阈值冻结与 purge | TEST-CFG-02 回归；TEST-RES-04/05 仅配置前置 | 同上、可选状态模块对照 | PASS（配置层） |
| REQ-001 / REQ-011 | DEV-001 严格布尔、摘要、独立时间参数 | TEST-CFG-01/02 + 新增摘要固定值/可配置对照 | 原 48 项、`test_valid_sample_digest_unchanged`、新增对照 | PASS |
| REQ-015 | DEV-001 原有成本配置行为 | TEST-COST-01 | 原 `test_config.py` 48 项全部保留并复跑 | PASS（配置层） |

上述 STORE/QUALITY/RES 引用表示配置前置约束；没有实施相应运行模块或声称其完整验收通过。

## 实际执行

所有命令工作目录：`/Users/aries/Dve/btc_microstructure`。日志包含完整 stdout/stderr、实际命令、时间及退出码。

| 阶段 | 命令 | 工作目录 | 退出码 | 状态；通过 / 失败 / 跳过 | 日志位置 |
|---|---|---|---|---|---|
| before | `.venv/bin/python -c 'import sys,platform; print(sys.version); print(platform.platform()); print(platform.machine())'` | 项目根目录 | 0 | PASS；— | `reports/round1/fix/before/environment.log` |
| before | `sw_vers` | 项目根目录 | 0 | PASS；— | `reports/round1/fix/before/os.log` |
| before | `.venv/bin/python -m pip list --format=json` | 项目根目录 | 0 | PASS；— | `reports/round1/fix/before/dependencies.log` |
| before | `.venv/bin/python -m pytest -q tests/unit/test_config.py --junitxml=reports/round1/fix/before/baseline.xml` | 项目根目录 | 0 | PASS；48 / 0 / 0 | `reports/round1/fix/before/baseline.log` |
| before | `.venv/bin/python -m pytest -q review/R1_01/checks/test_review_regressions.py --junitxml=reports/round1/fix/before/independent.xml` | 项目根目录 | 1 | FAIL；3 / 12 / 0 | `reports/round1/fix/before/independent.log` |
| after | `.venv/bin/python -c 'import sys,platform; print(sys.version); print(platform.platform()); print(platform.machine())'` | 项目根目录 | 0 | PASS；— | `reports/round1/fix/after/environment.log` |
| after | `sw_vers` | 项目根目录 | 0 | PASS；— | `reports/round1/fix/after/os.log` |
| after | `.venv/bin/python -m pip list --format=json` | 项目根目录 | 0 | PASS；— | `reports/round1/fix/after/dependencies.log` |
| after | `.venv/bin/python -m pytest -q tests/unit/test_config.py --junitxml=reports/round1/fix/after/baseline.xml` | 项目根目录 | 0 | PASS；48 / 0 / 0 | `reports/round1/fix/after/baseline.log` |
| after | `.venv/bin/python -m pytest -q review/R1_01/checks/test_review_regressions.py --junitxml=reports/round1/fix/after/independent.xml` | 项目根目录 | 0 | PASS；15 / 0 / 0 | `reports/round1/fix/after/independent.log` |
| after | `.venv/bin/python -m pytest -q --junitxml=reports/round1/fix/after/full.xml` | 项目根目录 | 0 | PASS；106 / 0 / 0 | `reports/round1/fix/after/full.log` |
| after | `.venv/bin/ruff check src tests` | 项目根目录 | 0 | PASS；— | `reports/round1/fix/after/ruff.log` |
| after | `.venv/bin/ruff format --check src tests` | 项目根目录 | 0 | PASS；— | `reports/round1/fix/after/format.log` |
| after | `.venv/bin/mypy src` | 项目根目录 | 0 | PASS；— | `reports/round1/fix/after/mypy.log` |
| after | `.venv/bin/python -m pip check` | 项目根目录 | 0 | PASS；— | `reports/round1/fix/after/pip-check.log` |
| after | `.venv/bin/python -m btc_microstructure validate-config config/research.example.yaml` | 项目根目录 | 0 | PASS；— | `reports/round1/fix/after/cli-valid.log` |
| after | `shasum -a 256 -c reports/round1/preserved.sha256` | 项目根目录 | 0 | PASS；— | `reports/round1/fix/after/preservation.log` |

其他实际命令：

| 命令 | 工作目录 | 退出码 | 结果 | 证据 |
|---|---|---|---|---|
| `.venv/bin/python reports/round1/fix/run_checks.py before` | 项目根目录 | 0 | 成功确认审查复现为 12 失败/3 通过；其中 pytest 子命令为 FAIL、退出 1，未隐藏 | `fix/before/commands.json` |
| `.venv/bin/python -m pytest -q tests/unit/test_foundation_guards.py --junitxml=reports/round1/fix/before/new-regressions.xml` | 同上 | 1 | FAIL：36 通过/22 失败/0 跳过（修复前） | `fix/before/new-regressions.log/xml` |
| `.venv/bin/ruff format tests/unit/test_foundation_guards.py` | 同上 | 0 | 新增测试格式化；在首次执行前完成 | 会话工具输出 |
| `.venv/bin/ruff format src/btc_microstructure/config_models.py` | 同上 | 0 | 无需格式调整 | 会话工具输出 |
| `.venv/bin/python reports/round1/fix/run_checks.py after` | 同上 | 0 | 所有修复后子检查通过 | `fix/after/commands.json` |
| `.venv/bin/python reports/round1/fix/verify_fix_integrity.py` | 同上 | 0 | 47 个受保护文件、17 个依赖版本、7 项 CLI 拒绝通过 | `fix/after/integrity.json`，`cli-guards.log/json` |
| `.venv/bin/python reports/round1/package_foundation_fixed.py` | 同上 | 0 | 打包并验证 CRC、包内逐文件 SHA-256 | 根目录 `.zip.verify.json` |

七项实际 CLI 命令为 `.venv/bin/python -m btc_microstructure validate-config reports/round1/fix/fixtures/<完整字段名>.yaml`，
每项实际退出 1、stderr 含完整字段路径与 `must be true`；每条完整命令及原始输出见 `fix/after/cli-guards.log/json`。
无效配置拒绝为预期 PASS，不能将该预期退出 1 与测试失败混淆。

修复后默认套件 **106 通过 / 0 失败 / 0 跳过**（原 48 + 新增 58）。独立审查套件另执行 **15 通过 / 0 失败 / 0 跳过**。
原 48 项也单独执行全部通过。独立审查文件未复制成改写版本，直接运行原文件。

同会话 code-reviewer 独立只读复查未发现阻塞问题，记录见 `fix/after/code-review.md`。
其指出 lint 范围差异，已实际复核并保留完整日志：

| 命令 | 工作目录 | 退出码 | 状态 | 完整日志 |
|---|---|---|---|---|
| `.venv/bin/ruff check .`（扩展检查首次） | 项目根目录 | 1 | FAIL：31 项；含本轮执行器 1 个未使用导入（已删除） | `fix/after/ruff-repo.log` |
| `.venv/bin/ruff check reports/round1/fix/run_checks.py reports/round1/fix/verify_fix_integrity.py reports/round1/package_foundation_fixed.py` | 同上 | 0 | PASS：新增交付脚本 | `fix/after/ruff-evidence.log` |
| `.venv/bin/ruff check .`（扩展检查复跑） | 同上 | 1 | FAIL：30 项，全部来自两份原始审查探索脚本 | `fix/after/ruff-repo-final.log` |

全仓 lint 没有通过；本次代码与测试的 `ruff check src tests` 通过。两份原审查探索脚本为证据，未改写以制造全仓绿色。

## 数据证据

来源全部为原配置和人工配置副本；无真实行情、无官方接口新核验、无现场响应、无采集时间范围。
修复前 Python 3.12 原机复现的 `independent.log/xml` 保留全部 12 个失败的原始错误；新增回归测试修复前的全部 22 个失败另存 `new-regressions.log/xml`。
用户提供的 Linux/Python 3.13 独立审查原始证据保留在 `review/R1_01/evidence/`，不改写、不替代本机日志。
原样例文件 SHA-256：`d0556ac8d91986da157bb2844a415d53536b67df92856518adf8fda4058490e4`。
原固定 synthetic_cases SHA-256：`e2c081837f81bc2f0a97491ec355faca11c2130e2afe679faaceda74f0bffa72`。
Git 忽略规则通过临时独立仓库和不存在的人工文件路径验证；未读取或创建真实秘密，未初始化用户项目。

## 失败、阻塞与限制

- FAIL（已复现并修复）：原有忽略规则回退、七项硬约束缺失。原独立检查 12 失败/3 通过；新增测试修复前 22 失败/36 通过。全部原始失败证据保留。
- FAIL（非阻塞材料检查）：全仓 ruff 仍有原审查探索脚本的30项风格/未使用导入诊断，完整保留，未修改历史证据。
- PASS：原测试、全部新增测试、独立审查测试、有效/无效 CLI、lint/格式、类型与依赖一致性、原始材料保护检查。
- BLOCKED：当前无 Git 仓库，无法提供提交号或 Git diff；使用保存的修复前快照、统一 diff 和 SHA-256。
- NOT_RUN：Linux/其他平台复现、第二个全新虚拟环境安装、真实币安接口核验及任何后续运行模块测试。没有新增网络或依赖安装。
- C1–C5 按要求不扩展：研究启动保护区间下限、配置对象可变性仍是后续接口决策；没有宣称它们已修复。端点/来源真实性保持 UNVERIFIED，配置路径合同不变。
- `.gitignore` 不是秘密扫描器，不能移除已跟踪秘密；本次恢复保护规则，不声称已经发生泄密。
- 新 ZIP 保留旧报告和历史摘要作为版本证据；以本修复报告与新清单判断当前结果。旧报告没有被改成“从未失败”。

## 用户复现

已有本机环境从项目根目录执行：

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q review/R1_01/checks/test_review_regressions.py
.venv/bin/python -m btc_microstructure validate-config config/research.example.yaml
.venv/bin/python -m btc_microstructure validate-config reports/round1/fix/fixtures/quality.unknown_as_null.yaml
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src
.venv/bin/python -m pip check
shasum -a 256 -c reports/round1/fix/fixed-implementation.sha256
```

预期默认测试106通过，独立测试15通过；有效配置退出0，人工 false 配置退出1且包含字段路径，检查退出0。
一键重跑修复后检查：`.venv/bin/python reports/round1/fix/run_checks.py after`。
完整性与七项 CLI：`.venv/bin/python reports/round1/fix/verify_fix_integrity.py`。
不要在修复后版本运行 before 来重造历史失败；执行器会拒绝覆盖已有 before 证据。

新机器需先使用 Python 3.12 创建 `.venv`，按原 README 安装 `requirements-dev.lock` 并 editable 安装；
该全新安装流程本轮 NOT_RUN，依赖下载遵守目标环境审批。ZIP 使用项目根相对路径，解压后在该目录执行。
重新打包：`.venv/bin/python reports/round1/package_foundation_fixed.py`。
ZIP 外部校验：`shasum -a 256 -c foundation_review_fixed.zip.sha256`；外部回执是归档旁文件，不嵌入 ZIP 以避免循环摘要。
排除 `.venv`、`.git`、OMX 状态、缓存、构建产物、数据、环境文件、密钥与其他秘密；原审查和修复证据纳入新包。
脱敏/筛查说明见 `fixed-packaging-audit.json`。保留公开人工凭据 URL 测试原值，不修改断言或日志；没有发现需要脱敏的真实秘密。

## 下一任务依赖

本次仅完成 A/B 修复交付后停止。不执行 R1-02、不访问币安、不自动启动任何采集或研究任务。
