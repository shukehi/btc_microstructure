# R2-04 / L04 Journal 600 秒有界离线验收

任务依据：[Issue #21 授权评论 6164549451](https://github.com/shukehi/btc_microstructure/issues/21#issuecomment-6164549451)，及本轮用户明确的 A → B → C 授权。规格 v0.1；代码基线 `45176e8ac9828f8a016d94a4bdd5baf835a8da46`；沿用既有 PR #22 工作树和本地整改分支。本轮没有修改生产源码、格式、资源上限、单位审批、配置、依赖锁、CI 工作流或历史证据。

**本轮 600 秒人工负载子集：`PASS_LIMITED_OFFLINE`。L04 总体仍 `PARTIAL`，REAL 始终 `BLOCKED`，PR 保持 Draft。** 三种 600 模拟秒场景和唯一一次 600 秒真实墙钟人工流均完成。没有 Binance、真实行情、probe、账户或交易接口访问。这不是完整 L04、100k、生产运行、24h 或研究能力验收。

## 修改与证据

- `journal_600s/acceptance.py`：显式 TEST_ONLY Journal 工厂、按需产生的人工 aggTrade、真实/模拟时钟、观测性统计、RAW ACK 前置检查、独立 RAW 算术 oracle 和两次只读 replay。
- `journal_600s/sitecustomize.py`、`environment_probe.py`：显式启用的 Python audit guard，拒绝 IP socket 与 DNS，保留 asyncio 本地 UNIX IPC；记录真实 import 来源并核对 spawn/pytest 继承。来源错误在项目导入前退出 92。
- `journal_600s/supervise.py`：独立进程组守护，660 秒截止、磁盘 2 GiB 硬 floor、进程组当前 RSS 512 MiB 停止。入场保守预留四个 512 MiB run、1 GiB 验证材料及 2.5 GiB 启动余量；未提高任何既有 cap。
- `journal_600s/local_checks.py`：从原 CI 脚本读取全部十三项，仅替换本地工具绝对路径和新证据输出位置；不改检查要求。
- `tests/unit/test_journal_acceptance_environment.py`：新增三项默认 CI 回归，实际验证 subprocess、pytest、spawn 的来源/禁网继承及来源不符时拒绝。
- 小型机器证据：[evidence.json](journal_600s/evidence.json)。完整 raw、控制记录、Journal、checkpoint、日志、JUnit、import 记录和守护采样保存在该文件指向的唯一 ignored `data/session/` 目录。没有覆盖旧 run 或 `ci-results`。

## 环境、资源与安全门

实际环境：macOS 27.0.1 / arm64，物理内存 16 GiB，Python 3.12.13。使用已存在的共享 Python 3.12 虚拟环境；未重新安装 editable 包或写入该环境。启动核验的共享解释器及其真实解析路径仅保存在本机环境日志，避免提交个人绝对路径。

共享 editable 默认指向主工作区，因此全部 Python 命令使用 `PYTHONPATH="$PWD/src:$PWD/reports/round2/journal_600s"`，其中当前工作树 `src` 在首位。第二项仅提供 opt-in `sitecustomize`。父进程、pytest、spawn 和实际 Worker 启动记录均验证 package origin 在当前工作树；`PYTHONDONTWRITEBYTECODE=1`。静态检查明确检查当前目录的 `src`。

Python guard 在各 Python 子进程启动时继承；未运行 shell 网络工具或真实适配器。macOS 没有声称通过 Linux network namespace；该项本地 `NOT_RUN`，Hosted CI 另行核实。单纯的 Python guard 不冒充 OS 防火墙或对任意非 Python 程序的网络隔离。

预算沿用：30/600/30 秒、总 660 秒；100000 消息、32 MiB WS payload、64 KiB 单条；128 MiB 状态；384 MiB RSS 预警、512 MiB 停止；1000 条/1 MiB/1 秒队列；2 秒 ACK；512 MiB 本 run/八分区；磁盘 2.5/2.25/2 GiB；1 秒 grid、30 秒窗口；Journal 默认 4 MiB 滚动。所有验收场景使用正常磁盘门槛。

初始可用磁盘约 13.87 GiB，足够覆盖保守 5.5 GiB 多场景入场预留。C 的独立守护最低观测 13.7217 GiB；没有制造物理满盘、硬 OOM 或断电。

## 阶段 A

环境 probe：父进程与 spawn 各自实测 IP socket 和 DNS 被拒绝，pytest 实测相同来源与拒绝行为，probe pytest 1 passed。新增默认环境回归 3 passed。

既有四个 Journal 测试文件定向回归 **68 passed / 0 failed,error,skipped，exit 0，98.55 秒**。包含旧 ProcessWriter/Journal 30/60/120 模拟秒逐行对照、RAW-first、FEATURE_CUTOFF → feature transaction → FEATURE_PUBLISHED、重复/冲突/坏 JSON、只读双 replay、故障/截断/checkpoint/hash 链/资源保护。

本轮工具的 30 模拟秒、10 条/s 验证：300 RAW、300 normalized、30 features，双 replay/oracle PASS。正式 C 前另执行一次 30 秒真实时钟安全门禁：300 消息/30 features、实际观察达到 30 秒、双 replay/oracle PASS；截止至发布 p99 46.101 ms。该短门禁没有被算作 600 秒演练。

## 阶段 B：600 模拟事件秒

每组使用真实 spawn Journal Writer、独立新目录及原默认 4 MiB 阈值。模拟时钟在真实墙钟中快速推进，不代表连续 600 秒运行。各组 RAW=生成=接收=可信保存=normalized=replay 数量，quarantine=0；各有 CONTROL 1204、FEATURE_CUTOFF 600、FEATURE_PUBLISHED 600、features 600。

| 速率 | 实际消息 | 控制器墙钟秒 | 父/子 CPU 秒 | 状态预约峰值 B | 父子 RSS 峰值保守和 MiB | 文件/物理 B | RAW/CONTROL/FEATURE 滚动 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1/s | 600 | 8.076736 | 3.181 / 3.822 | 33,918,758 | 169.625 | 9 / 4,243,316 | 0 / 0 / 0 |
| 5/s | 3000 | 11.094344 | 3.903 / 6.697 | 65,206,808 | 227.438 | 10 / 11,239,382 | 0 / 0 / 1 |
| 10/s | 6000 | 13.418701 | 4.623 / 8.502 | 104,382,699 | 294.797 | 13 / 19,886,918 | 1 / 0 / 3 |

每组 600 个窗口均逐一从可信 RAW 原文独立累加 Decimal，核对方向、数量、V、Delta、TI、质量/null、input highwater、发布因果与历史 available；不调用 Core 的窗口或特征函数来生成 oracle。另有独立手算最终完整 30 秒窗口：N=30r、B=S=0.015r BTC、V=0.030r BTC、Delta=0、TI=0。每组 29 个预热窗口的全部成交特征字段为 null，其他 571 个有效。

两次只读 replay 逐结果相等、原始/派生一致、输入目录 SHA 不变；恢复 PASS、无 recovery events。checkpoint、跨文件号和 hash 链由真实恢复校验，八分区数值见机器证据。模拟截止至发布时间为零，仅是 FakeClock 语义；实际 ACK/CPU/墙钟单独记录。

## 阶段 C：唯一一次 600 秒真实墙钟

使用 OS `time.monotonic_ns()` 和实际 `time.time_ns()`；人工 transport 在时间到达时才生成并逐步递交消息，未预生成再 sleep，没有推进 FakeClock。速率 10/s；候选 6000 条均实际生成、接收、可信保存、解析和重放。唯一一次尝试的启动标记使用 exclusive create，未重试。

- 准备：**0.275204084 s**。
- 观察：**600.000274041 s**，600 个 1 秒 cutoff 和发布；实际单调钟起点/完成时点存于机器证据。
- transport 关闭后的排空/最终派生表：**1.135020292 s**；Writer 关闭：**0.389822958 s**。
- 控制器总墙钟：**601.804184750 s**，含回放的独立守护总耗时 **606.680494 s**，均未达到 660 秒。
- RAW 6000、CONTROL 1204、normalized 6000、quarantine 0、features 600；30 个预热窗口 null，570 个有效。独立 RAW 算术 oracle、因果顺序和双 replay 全部通过。
- 600 个 cutoff 至实际 available 的延迟：p50 **70.680 ms**、p95 **127.282 ms**、p99 **180.063 ms**、max **199.766 ms**。这是本机本次人工负载结果。
- 状态预约峰值 **103,999,088 B**；队列峰值 **7 条 / 6,251 B**；队列年龄上限保持 1 秒并由控制器执行，未额外采样其最大实际年龄。
- 运行期间父/子历史 RSS 峰值 **134,135,808 / 145,637,376 B**，保守和 **266.8125 MiB**；守护对含回放的进程组当前 RSS 峰值 **370,327,552 B**，低于 512 MiB。
- 父/子监控样本 **5558 / 5563**，实际最大间隔 **117.590 / 117.757 ms**；100ms 是采样目标，未声称硬实时。
- 父/子 CPU **25.546285 / 11.875026 s**。物理 **13 文件 / 19,708,077 B**，默认滚动 RAW **1**、CONTROL **0**、FEATURE **3**；Journal frames **3019**、checkpoints **94**、完整审计 **190**。
- 外层 Writer 命令墙钟（含序列化等前置工作）p50/p95/p99/max：**1.511 / 3.570 / 87.964 / 1059.687 ms**；Writer 内部 ACK p95/max **3.249 / 710.138 ms**。二者口径不同。fsync **6241** 次、p95/max **0.775 / 6.310 ms**；原 Worker 未提供 fsync p50/p99，明确为未采样，不填造。
- Writer 已关闭、Worker 静止；恢复 PASS、无 recovery events；两次只读 replay 相等，原始目录 SHA 不变，可信 cursor **7204**。

八分区最终物理字节：metadata 9958、RAW 5555942、control 953109、manifests/indexes 2540、normalized/quarantine 0、feature 13186516、temporary/orphan 0、diagnostics/receipts 12。Journal 的最终表按照原格式预算分类计入 feature 类，未改变既有收费语义；全部分区及总量未越限。

## 验收映射

| requirement_id | dev_task / subset | test_id / Issue matrix | 证据 | 状态 |
|---|---|---|---|---|
| REQ-002/003 | DEV-003，成交子集 | TEST-STORE-01、TEST-TIME-01/02，L02/L03/L05 | ACK spy、原文 SHA、真实接收/发布记录、Journal 定向回归 | PASS_LIMITED_OFFLINE |
| REQ-004/009/012 | DEV-004/008，aggTrade/Delta/TI 子集 | TEST-TRADE-01/02/03、TEST-FEAT-01/02、TEST-QUALITY-01 | 既有混合质量回归、独立 RAW oracle、warmup null | PASS_LIMITED_OFFLINE |
| REQ-010 | DEV-006 | TEST-REPLAY-01，L06 | 各场景双只读 replay、input SHA、可信恢复 | PASS_LIMITED_OFFLINE |
| REQ-016/017 | DEV-003/006 资源子集 | TEST-OPS-01/03，L01/L04/L05 | 继承 guard、实际资源/时限、故障定向回归 | PASS_LIMITED_OFFLINE |

TEST-REPLAY-02 的 L2 检查点语义及非成交家族不在本轮范围；不据 Journal checkpoint 冒充其完整验收。

## 最终检查与复现

本次最终本地默认 **538 passed**，四组独立 **15/9/8/4 passed**；实际五份 JUnit 均为 **0 failure/error/skipped**。十三项全部 exit 0，没有 skip/xfail。额外工具 Ruff check/format-check 均 exit 0。默认测试已包括本轮新增三项环境回归。

| 实际检查（原 CI 清单） | 退出码 | 通过数量 / 结果 | 日志（相对本轮唯一目录） |
|---|---:|---|---|
| pytest | 0 | 538 passed | final-ci/pytest.log、pytest.xml |
| pytest-independent-r1-01 | 0 | 15 passed | final-ci/pytest-independent-r1-01.log、.xml |
| pytest-independent-r1-02 | 0 | 9 passed | final-ci/pytest-independent-r1-02.log、.xml |
| pytest-independent-r1-03 | 0 | 8 passed | final-ci/pytest-independent-r1-03.log、.xml |
| pytest-independent-r1-04 | 0 | 4 passed | final-ci/pytest-independent-r1-04.log、.xml |
| r104-original-parquet | 0 | 原始 Parquet 反例/固定预期通过 | final-ci/r104-original-parquet.log |
| ruff-check | 0 | PASS | final-ci/ruff-check.log |
| ruff-format | 0 | PASS | final-ci/ruff-format.log |
| mypy | 0 | PASS | final-ci/mypy.log |
| pip-check | 0 | 无依赖冲突 | final-ci/pip-check.log |
| validate-config | 0 | PASS | final-ci/validate-config.log |
| validate-offline | 0 | PASS | final-ci/validate-offline.log |
| offline-e2e | 0 | 原 synthetic CLI、Parquet、两次 replay/build PASS | final-ci/offline-e2e.log |

本报告提交时新 Hosted CI 尚未触发，记 `NOT_RUN_AT_REPORT_COMMIT`。本轮准确提交 SHA、对应 Actions URL 和其实际成功/失败结果随同一 PR 的交付回执提供；不以历史 CI 或本机 macOS 结果代替 Hosted Linux 隔离证据。

本地执行适配原因：当前工作树没有 `.venv`，共享 editable 指向另一个工作树；原 `.github/scripts/ci_checks.sh` 的工具路径是 `.venv/bin/*`，没有硬编码另一源码路径。适配读取原十三项清单，仅解析成本地共享工具的绝对路径、独立日志/JUnit/临时目录和 smoke 的既有 `--output` 参数。未删检查、改断言、skip/xfail 或修改原脚本。

在项目根目录，显式选择已核验的 Python 3.12 虚拟环境解释器，设置当前源码和 opt-in guard 环境，使用独立的新证据目录。实际完整 argv、退出码和日志在本机 `final-ci/commands.json`、`results.tsv`；小型摘要在 `evidence.json`。`local_checks.py --tools <existing-venv>/bin --output <new-evidence>/final-ci` 执行原十三项。不要重复运行本次已消费的 600 秒真实墙钟尝试；新一次墙钟演练需要新的用户授权。

## 状态与限制

原 `run_result.status=PARTIAL` 与硬编码 `acceptance_status=PARTIAL_L04_L05_BLOCKED` 原样保留，没有为了得到 PASS 修改生产返回值。本轮工具按照授权的时长、完整数量、预算、oracle、恢复和只读重放判定 600 秒人工离线子集通过；不把这个判定升级为总体 L04 或 REAL。

100k 极限、真实市场单位审批/共享出口/物理传输、24h、多源、FS 外部竞争、checkpoint 快速 seek、物理满盘/断电/硬 OOM 仍未验收。Python/GIL/OS 调度和父端同步 fallback audit/fsync 不保证不可中断内核 I/O 的硬截止。没有 `FULL_PHYSICAL_FAULT_PASS`，也没有生产吞吐或真实市场发布时延承诺。

本轮所有执行日志保存在新目录；本轮截至演练结束没有失败项，历史失败证据未改写。完成提交、普通快进推送和 CI 核验后停止，等待 GitHub 独立复核；不合并、改 Ready、关闭 Issue 或开始市场研究。
