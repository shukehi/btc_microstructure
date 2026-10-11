# R1-03 独立审查修复：R103-A / R103-B / R103-C

本次只修复 PR #5 独立审查 [5483466192](https://github.com/shukehi/btc_microstructure/pull/5#pullrequestreview-5483466192) 及三条行内意见。基线/被审查 head：`03ce9c7ea273cf0ab42915a012e10ba7e6193c24`；继续使用原分支 `feat/r1-03-offline-vertical-slice`，不新建替代 PR、不强推。

原规格0.1及原R1-03离线范围不变。真实 REST 保持历史 **BLOCKED**，WS/真实采集/24h **NOT_RUN**，真实单位 **UNKNOWN**、持续预算 **UNVERIFIED**；完整R1-03/M0/M1/M2仍 **PARTIAL**。本次没有连接 Binance、修改网络/代理、读取密钥、下单或开始下一任务。

## 审查和复现

已读取 PR 正文、review全文及三条行内意见、Issue #4全文和评论、AGENTS.md/CODEX_START_HERE/任务卡、原持久化/时间/去重/测试合同及既有审查记录。Git工作区开始时仅 `AGENTS.md` 有用户未提交修改；全文保持，未暂存或提交。其开始前SHA-256在 `vertical_slice_fix/before/environment.json`，交付前核对一致。

审查原文/API回执存于 `vertical_slice_fix/review-5483466192.md/json`，三条原意见存于 `inline-comments.json`；原始Python代码块逐字保存 `review/R1_03/original-test.py.txt`。实际测试文件 `review/R1_03/checks/test_r1_03_review.py` 只移除未使用的Path导入及Ruff格式化。AST等价回执在 `review-test-equivalence.json`，断言/预期未改。

先在原锁定环境（macOS arm64、Python3.12.13、PyArrow20.0.0、aiohttp3.12.15、pytest8.4.2）实际执行原始独立8案例：**5 FAILED / 3 PASSED / 0 SKIPPED，退出1**，与审查相同。未使用Arrow导入占位，不把审查方Python3.13.5环境冒充本机。

完整before日志/JUnit/命令/环境/依赖存于 `vertical_slice_fix/before/`。在首次复现后，另外从基线Git逐字复制隔离src到被忽略的data目录，以同一锁定环境执行新增Parquet故障传播案例：**1 FAILED，退出1**。该案例实际看到旧代码将失败raw尾部的build返回PASS；源码导入路径及日志/JUnit均保存，不冒充首次修改前执行。隔离副本不改用户工作区源码。

## 三项修复及证据映射

| finding | 修改和行为 | REQ / DEV / TEST映射 | 实际新增/独立案例 |
|---|---|---|---|
| [R103-A](https://github.com/shukehi/btc_microstructure/pull/5#discussion_r4241731253) | FLUSH_FAILED审计保留可信游标/失败输入范围；重开从历史失败审计恢复UNCERTAIN_GAP，扫描已隔离raw作为审计写入失败的独立证据；重复重开/重建不能升级PASS | REQ-002/010/012/017；DEV-003/006；TEST-STORE-01、TEST-OPS-03、TEST-REPLAY-01适用子集 | review::test_failed_raw_fsync_remains_uncertain_on_reopen；test_r103_recovery.py 的 fsync/rename/manifest、audit不可写、legacy、Parquet build案例 |
| [R103-B](https://github.com/shukehi/btc_microstructure/pull/5#discussion_r4241731257) | 不可信尾部按bytes逐行尽力诊断；UTF-8错误/残缺JSON/非对象不阻止可信前缀恢复；未知序号null，诊断错误显式记录，隔离保留原bytes | REQ-002/012/017；DEV-003/006；TEST-STORE-01、TEST-OPS-03 | review::test_malformed_raw_tail_is_isolated_without_losing_trusted_prefix[invalid-utf8/json-scalar/json-array]；default混合坏行/有效序号案例 |
| [R103-C](https://github.com/shukehi/btc_microstructure/pull/5#discussion_r4241731259) | 同时刻显式事件按原ingest_sequence排序，与JSON数组排列无关；原输入序号、生成控制序号和运行时高水位分别保留、可追踪 | REQ-003/004/010；DEV-004/006；TEST-TIME-01、TEST-TRADE-03、TEST-REPLAY-01 | review::test_schedule_preserves_explicit_ingest_order_for_equal_timestamps；test_r103_ordering.py 六项数组对照、冲突、会话边界和插入控制后的高水位案例 |

新增默认 **17项**：恢复11项、排序6项；原默认252项和独立15/9项均保留。原独立新检查8项另分组执行。精确node ID见 `vertical_slice_fix/test-nodes.txt`。

A的范围区分：只有raw提交失败/隔离构成raw不确定性；纯派生写盘失败不制造raw缺口，新增实际Parquet对照证明可从健康raw重建PASS。原失败bytes/审计不删除、不覆盖；未知信息不填造。

C的兼容规则：原schema `r1-03.1`继续读取；新增可选追踪字段 `source_ingest_sequence`、`sequence_origin`、`schedule_sequence`、`input_cursor_source_ingest_sequence`。`ingest_sequence/input_cursor`仍为展开后运行时游标，原fixture序号单独保留。生成控制事件不前移源输入高水位；同时间显式事件之间遵守原序号，生成cutoff在边界输入之前，生成published在同时间显式输入之后，窗口仍`[t-W,t)`。旧日志缺少新增字段仍可重放：实际对旧Actions38141380824产物重放PASS，逻辑摘要相同，见 `old-log-replay.log/exit`。

## 修改文件

- `src/btc_microstructure/offline_storage.py`：A/B恢复与诊断、审计flush/fsync。
- `src/btc_microstructure/offline.py`：C稳定排序和源/调度序号映射。
- `tests/unit/test_r103_recovery.py`、`test_r103_ordering.py`：17项项目回归，含真实Arrow/Parquet故障传播。
- `review/R1_03/`：原始独立案例、等价格式化可执行8案例和来源说明。
- `.github/scripts/ci_checks.sh`：独立R1-03日志/JUnit/真实退出码组；新独立测试纳入Ruff。required job仍 `Python 3.12 offline checks`，工作流只读token、checkout不保存凭据和隔离边界未改。
- 本报告及 `reports/round1/vertical_slice_fix/`：原审查、失败/成功记录、命令及校验回执。

原规格/固定样例/配置、依赖锁、原测试/审查/历史报告全部未改，保护检查退出0。没有新增依赖或升级锁项；锁定版本逐项核对全部一致，见after-03/locked-versions.json。

## 实际检查

工作目录均为项目根目录。完整真实argv/cwd/退出码在 `vertical_slice_fix/after-03/commands.json`，每项独立日志；所有输出目录新建，不覆盖before或此前after。

| 命令 | 退出码 | 通过/失败/跳过 | 证据 |
|---|---|---|---|
| `.venv/bin/python -m pytest -q --junitxml=.../after-03/default.xml` | 0 | 269/0/0 | default.log/xml |
| `.venv/bin/python -m pytest -q review/R1_01/checks/test_review_regressions.py --junitxml=.../independent01.xml` | 0 | 15/0/0 | independent01.log/xml |
| `.venv/bin/python -m pytest -q review/R1_02/checks/test_r1_02_regressions.py --junitxml=.../independent02.xml` | 0 | 9/0/0 | independent02.log/xml |
| `.venv/bin/python -m pytest -q review/R1_03/checks/test_r1_03_review.py --junitxml=.../independent03.xml` | 0 | 8/0/0 | independent03.log/xml |
| Ruff check src/tests/smoke/新独立检查 | 0 | PASS | ruff.log |
| Ruff format --check 同范围 | 0 | PASS | format.log |
| `.venv/bin/mypy src .github/scripts/offline_smoke.py` | 0 | PASS | mypy.log |
| `.venv/bin/python -m pip check` | 0 | PASS | pip-check.log |
| validate-config原配置 / validate-offline首轮配置 | 0/0 | PASS | config.log/offline-config.log |
| `.venv/bin/python .github/scripts/offline_smoke.py --output data/r103-fix-smoke-03` | 0 | PASS：collect、两次replay、raw-only build、Parquet读回 | offline-e2e.log |
| git diff基线保护规格/旧测试/锁项/历史证据 | 0 | PASS | integrity.log |

完整保留首轮after-01：266测试及独立15/9/8通过，Ruff仅因新增项目测试2个未使用导入退出1，移除后修正。after-02保存268测试及全部检查通过；后补纯派生故障对照，最终after-03为269。没有删除断言、skip/xfail或改预期制造绿色。

本机不具备Linux网络命名空间验收，记本机NOT_RUN；Actions在既有隔离网络命名空间实际执行四组测试及所有项目检查。真实故障只用临时目录和注入I/O错误，不填满磁盘、不修改系统时间。没有现场/24h观测或新增协议核验。

同会话代码复查未发现代码阻塞项；复查当时指出报告/推送尚未完成，随后交付完成记录在PR。详见 `vertical_slice_fix/code-review.md`。这不替代独立复核，不自动resolve任何审查线程。

## 复现与交付

在原Python3.12锁定环境运行：

```sh
.venv/bin/python -m pytest -q review/R1_03/checks/test_r1_03_review.py
.venv/bin/python -m pytest -q tests/unit/test_r103_recovery.py tests/unit/test_r103_ordering.py
.venv/bin/python reports/round1/vertical_slice_fix/verify.py
```

verify.py自动创建新的after-NN及CLI输出目录。修复状态按实际证据 **PASS**，仅指R103-A/B/C离线修复；独立验收仍待复核。PR #5保留OPEN；新SHA/Actions在远程完成后记录于本报告及PR正文。交付后停止，不启动后续任务。
