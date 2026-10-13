# R2-04 / Draft PR #22：本地审查修订跟进

**本地可靠性修订与离线验证完成；整体仍为 PARTIAL，REAL 保持 BLOCKED。** 默认 **454** 项、独立 **15/9/8/4** 项全部通过，十三项检查均退出 0。真实时钟/ProcessWriter 的 1000 条样本完整处理；100k 流式源在生成 **2066** 条后因保守状态预约上限早停，不称为完成 100k、600 秒或现场验收。

基线及当前未提交 HEAD：`17e131afe40d9b84cb84599259fcf8ea8451a60f`。任务 [Issue #21](https://github.com/shukehi/btc_microstructure/issues/21)，[Draft PR #22](https://github.com/shukehi/btc_microstructure/pull/22)，Refs #17/#12；审查 [5498954239](https://github.com/shukehi/btc_microstructure/pull/22#pullrequestreview-5498954239)。本轮未 commit/push、未回复 GitHub、未改变 Draft/Issue 状态。新 head / 本轮 hosted Actions **NOT_RUN**，由父会话独立复核后处理；既有 Actions 不作为本轮结果。

已读 worktree 提交版 AGENTS、CODEX_START_HERE、父会话取得的 Issue/评论/review 完整正文（inline `[]`）、R2-03 设计/预算/单位候选、Core/Store/CLI、bounded/unit_binding 和新增测试。交接输入 hash、Python 3.12.13 与27项锁定版本核对见 [baseline.json](bounded_review_followup/baseline.json)、[environment.json](bounded_review_followup/environment.json)。`PYTHONPATH=src` 已确认导入当前 worktree。原单位候选仍 pending/runtime_applied=false；没有 Binance/probe/密钥/代理/TLS 操作。

## review → 源码、测试、验收

| review 条目 | 实际修订 / 验收 | 状态 |
|---|---|---|
| 1 物理适配器与发包方向 | `bounded_transport.py` 实现固定 REST/WS 路由；REST 完整15s、connect/idle5s、2MiB及有界超限检测；WS64KiB、autoping/autoclose关闭、heartbeat=None、TLS默认验证、trust_env=False、禁止重定向/压缩和隐式重试；强制 TEST_ONLY session/factory，拒绝真实构造。在 pong/close 实际方法边界预留并计数，包括 aiohttp EOF/error 内部 close；普通1次/1s、总63次，any2次/1s、总64次及close保底。真实 aiohttp 状态机只连接内存 reader/writer/connector，未创建物理网络 | PASS：离线接口合同；真实访问 BLOCKED |
| 2 RSS / 分配前状态 / 压力 | `bounded_live.py` 用持久增长与 scratch 分开的预约，在控制/特征写入与 Core.process 前检查；raw解析/编码前检查并在派生拒绝时保留唯一 raw，停止派生；避免逐消息扫描/编码整个队列。`bounded_writer.py` 独立父/子监测，100ms目标、RawValue无可遗留共享锁、父监测线程独立采样/判停；挂起及父RSS超限回归通过。物理压力数字见下一节 | PASS：分配前拒绝/监测；100k完整吞吐 PARTIAL |
| 3 全文件 / 写前预留 / terminal | writer 对每次预留与提交审计有界全目录，拒绝symlink/特殊文件/越界；raw/table须匹配有界manifest的路径/hash才能归typed，否则收费为孤儿；temp、旧新文件、manifest、隔离及其他文件逐个计费。批次在 Store.append 前预约；Parquet在Arrow/读回/buffer前预约，复用同一Arrow，计入每列页/footer开销；JSON/Decimal、snapshot decode、parent队列有分配前界。保留512MiB/八分区、64MiB单Parquet、manifest8192B/4096条等cap。2.5GiB启动、2.25GiB durable预警、2GiB硬floor；普通写入保留1MiB terminal容量，terminal最多256条/4096B，故障及预警receipt fsync；有限ACK/join及不确定前缀保留 | PASS：受控离线边界/故障；真实满盘/断电 NOT_RUN |
| 4 REST失败先durable | writer先ready，REST原文/hash/状态/partial snapshot及REST_RESPONSE先durable，再HTTP/schema/身份/单位验收；失败STARTUP_FAILURE，WS调用0。超时保留adapter观察到的正文前缀；**取得任何status/body之前的网络失败也无条件durable写STARTUP_FAILURE，并记录response_observed=false**。停止原因写入terminal控制receipt。状态不足保存raw-only而不生成不完整最终派生表 | PASS：无响应网络失败、HTTP拒绝、partial、parse、identity、unit、timeout回归 |
| 5 TEST_ONLY provenance | 新raw/control采用 `um-agg-test-only-live.1`，与Core绑定派生版本一致；旧synthetic namespace、unit_binding候选与旧记录不回写 | PASS |

源码仅修改 `bounded_live.py`、`bounded_transport.py`、`bounded_writer.py`；Core、Store、CLI、unit_binding 复用且源码未改。新增默认发现的六组测试：`test_bounded_live_followup.py`、`test_bounded_transport_followup.py`、`test_bounded_writer_followup.py`、`test_bounded_review_followup.py`、`test_bounded_aiohttp_contract.py`、`test_bounded_state_contract.py`。无新依赖、skip/xfail或原断言修改。

## before 与修订中的失败保留

[证据索引](bounded_review_followup/evidence-index.json)保存原位置、忽略目录保留位置、原字节SHA及JUnit数量。75份日志/JUnit/退出码完整保存在 `data/session/full-evidence/`，没有删除或覆盖上轮报告/历史失败。主要 before：REST/provenance新回归4 fail；完整跟进7 fail；缺适配器1 fail；writer接口/逃逸3 fail、2 pass；独立父监测2 fail、11 pass；DEL/Decimal2 fail、17 pass。原默认391在修订前已重跑通过。

本轮额外保存：集成 **4 fail/116 pass**（escaping预约、重复close、孤儿分类），其后修复；REST无任何响应时缺STARTUP_FAILURE的独立回归为 **1 fail**，修复后相关合同 **16 pass**。首次最终物理1000虽交付0.785s，但收尾错误的过宽估计触发PARQUET_TEMP_LIMIT，结果BLOCKED，完整记录 `data/physical-final-1000/`。修为准确无整块分配的字符串上界并补每列Parquet固定开销，没有关闭cap。一次父会话CI与残留CLI并发写同一 `ci-results`，导致R104临时文件被移动而失败，完整保存在 `data/session/ci-results-race-invalid/`，不作为验收；终止已完成的新CLI后以全新输出重跑十三项成功。

## 物理压力及只读重放

脚本 [physical_pressure.py](bounded_review_followup/physical_pressure.py) 使用 `time.time_ns/monotonic_ns/perf_counter/process_time`、真实ProcessWriter；source逐条生成，父/子均阻断socket connect/DNS。各run源码前后SHA一致。完整raw/control/Parquet留忽略 `data/physical-final3-*`；小型结果分别为 [1000条](bounded_review_followup/physical-final3-1000.json) 与 [100k流式源](bounded_review_followup/physical-final3-100k.json)。

| 观测 | 1000源 | 100k流式源 |
|---|---:|---:|
| 实际生成/保存raw | 1000 / 1000 | 2066 / 2066 |
| stop / coverage | PARTIAL / DISCONNECT | PARTIAL / ENCODED_STATE_LIMIT |
| 物理输入交付秒 | 0.741134 | 1.428518 |
| 物理全流程秒（含启动/收尾） | 1.832946 | 1.877236 |
| 父 / 子 CPU 秒 | 0.666110 / 0.759747 | 1.180177 / 0.664186 |
| 在线normalized / published feature | 1000 / 2 | 1783 / 2，未写最终不完整派生表 |
| 队列峰值 条 / bytes / 最老ns | 500 / 444084 / 258516666 | 500 / 449500 / 270365875 |
| 父 / 子 ru_maxrss峰值 bytes | 84525056 / 103170048 | 84721664 / 97173504 |
| 合计峰值保守上界 bytes | 187695104 | 181895168 |
| 父线程采样次数 / 最大间隔ns | 14 / 105075875 | 15 / 111942792 |
| 文件增长采样峰值 / 最终bytes | 1025036 / 1036601 | 1898189 / 1898189 |
| 最后确认游标 | 1008 | 2074 |

1000样本达到该次≤1秒交付，不能外推峰值/现场吞吐。100k未跑完：停止由128MiB**保守状态预约**触发，不是实际RSS耗尽；`encoded_state_bytes`兼容字段与 `reserved_state_upper_bound_bytes` 明确是预约上界，非准确物理内存。未用FakeClock/关闭cap/预生成100k列表换成功。100ms为采样目标，表中给实际间隔；ru_maxrss峰值相加不是同时瞬时RSS，不宣称OS硬OOM保障。

[physical-replays-final.json](bounded_review_followup/physical-replays-final.json)：两次禁网、只读、字节不变；分别核验1000/2066原文hash及2/2特征的独立窗口选择、Decimal买卖量/Delta/TI、count、warmup/null与原发布时刻。1000与原派生表一致，replay PASS；100k无完整最终表，replay PARTIAL，不填造完成状态。默认套件另保留0.30+0.20−0.10固定手算oracle、损坏/缺发布/冲突等用例。

## 实际命令与十三项离线CI

所有命令从本worktree执行，`PYTHONPATH=src`，Python3.12.13，依赖27项锁定版本匹配；无安装。完整命令、外层有限超时、exit和耗时在 `data/session/*.command.json/.exit/.log`，JUnit在下表位置。最终结果见 [ci-summary.json](bounded_review_followup/ci-summary.json)、[results.tsv](bounded_review_followup/results.tsv)。

| 命令 / 阶段 | exit / 数量 | 完整证据 |
|---|---|---|
| `python -m pytest ...` 原默认（新增前） | 0 / 391 pass | `data/session/baseline-default.xml/.log/.exit` |
| writer/状态最终定向 | 0 / 36 pass | `data/session/writer-final-tight.xml/.log/.exit` |
| `python physical_pressure.py --count 1000 --output <new ignored dir>` | 0；run PARTIAL，已完整处理1000 | `data/session/physical-final3-1000.*`、run result.json |
| 同脚本 `--count 100000` | 0；run PARTIAL，2066后受限停止 | `data/session/physical-final3-100k.*`、run result.json |
| 两次物理prefix replay + independent oracle | 0 | `data/session/physical-replays.*` |
| `bash .github/scripts/ci_checks.sh`，最终全新输出 | **0**，27.303s | `data/session/final-thirteen-clean.*`、`ci-results/` |
| 默认 pytest | 0 / **454 pass，0 fail/error/skip** | `ci-results/pytest.xml/.log` |
| 独立 R1-01 / 02 / 03 / 04 | 各0 / **15 / 9 / 8 / 4**，0 fail/error/skip | `ci-results/pytest-independent-r1-*.xml/.log` |
| 原Parquet、Ruff check/format、Mypy、pip check、validate-config/offline、offline-e2e | 八项各0 | `ci-results/`同名日志与results.tsv |

适用映射：REQ-001/002/003/004/009/010/012/016/017/018；DEV-003/004/006/008/013成交子集；TEST-CFG-01/02、STORE-01、TIME-01/02、TRADE-01/02/03、FEAT-01/02、QUALITY-01、REPLAY-01、OPS-01/02/03、DOC-01。Issue21 L01/L02/L03/L05/L06/L07限定离线PASS；L04分配/连接/资源/早停合同PASS、完整100k/600s能力PARTIAL，REAL始终BLOCKED。没有验收BOOK/OI/FUND/LIQ或研究/成本模块。

## 完整性、限制与交还

[verification.json](bounded_review_followup/verification.json)记录最终源码/新测试SHA、原899个已跟踪规格/配置/测试/CI/历史文件未变、旧391断言保留、原工作区未提交AGENTS hash不变、临时venv link已移除。原所有失败报告保留。本轮独立角色只读审查的最终技术结论见 [independent-local-review.md](bounded_review_followup/independent-local-review.md)，不替代GitHub审查。

真实Binance/probe/600s/NTP/满盘/断电/硬OOM/24h与本轮hosted Actions均NOT_RUN；正式单位审批和可信共享出口预算仍未具备，REAL BLOCKED。Python线程的调度/GIL及OS硬故障不能由此采样证明可优雅收尾；实际不可确认writer停止/刷盘仍报STOP_FAILED/UNCERTAIN_GAP。所有可实现修订已完成；100k保守预约早停保留PARTIAL。

复现沿用README与`.github/scripts/ci_checks.sh`，将已有锁定Python3.12环境临时链接为`.venv`并设置`PYTHONPATH=src`；运行压力脚本须使用全新忽略目录，运行CI前保留旧`ci-results`后使用全新输出，结束只移除自身link。交还父会话独立核对本地diff/证据后再提交现有PR分支、逐条回复review并记录准确Actions。此会话停止，不commit/push、不启用REAL、不扩大到下一任务。
