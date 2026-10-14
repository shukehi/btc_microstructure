# R2-04 / Draft PR #22：L04 有界容量诊断与最小修订

本轮依据 Issue #21 评论 6143219972，只处理 L04 单源 TEST_ONLY 容量与持续运行能力。基线为 PR #22 head `a6dad88f7bd328f8526f0cd3b200e82fc08e1a66`；Python 3.12.13、macOS arm64。没有 Binance/probe/REAL 请求，没有修改网络、代理、TLS、单位审批、原规格、配置样例、历史失败证据、128 MiB 状态上限或 512 MiB 文件上限。

## 结论与状态

**L04 仍为 PARTIAL，REAL 仍为 BLOCKED。** 状态预约的主要假阳性已证实并最小修订；1000 条和 2000 条人工突发都能完整派生，100k 上限源的首次安全停止从约 1781 条 Core RAW 后移到 10494 条 Core RAW。60 秒、10 条/模拟事件秒的严格 `ProcessWriter` 样本完整、数值 oracle 正确、两次只读 replay PASS，耗时从 43.51 秒降到 15.00 秒。

但 30 秒→60 秒样本中文件数 317→617、墙钟 4.55→15.00 秒、batch p95 39.4→75.4 ms；剩余成本来自每个 1 秒 cutoff 产生的不可变 raw/control/feature 文件及每次写前/写后全目录审核。Core 的 60 次窗口扫描合计仅 9.9 ms，不是当前主因。短阶段没有支持安全进入 600 秒严格 ProcessWriter 验收，因此 1/5/10 条/秒的 600 秒模拟事件测试和一次 600 秒真实墙钟离线演练均按门禁保持 `NOT_RUN`，没有把内存 writer 的 600 秒语义回归冒充物理持续能力。

## 第一阶段：基线诊断

诊断脚本 `reports/round2/bounded_capacity/profile_capacity.py` 使用人工唯一 aggTrade、禁 socket/DNS、真实 `ProcessWriter`，分别记录 Core 可达 Python 对象、预约、当前父/子 RSS、CPU、writer 命令、文件增长与双 replay。预约值与 RSS/heap 分开报告；小型汇总为 `reports/round2/bounded_capacity/evidence-summary.json`，原始生成 run 留在忽略的 `data/session/`，不提交人工大文件。

| 输入 | 旧版实际 Core RAW | 旧版预约/返回 | Core heap | 文件 | 结果 |
|---:|---:|---:|---:|---:|---|
| 100 | 100 | 7,545,265 B | 370,594 B | 159,768 B | 完整 |
| 500 | 500 | 37,476,465 B | 1,776,754 B | 555,786 B | 完整 |
| 1000 | 1000 | 74,906,607 B | 3,537,704 B | 1,053,557 B | 完整 |
| 2000 | 1782 | 失败前约 128 MiB | 6,303,328 B | 1,883,552 B | `ENCODED_STATE_LIMIT` |

确定性首个失败是第 1782 个 Core RAW 的分配前检查：`committed=133,072,166`、pending 双倍预约 `1,069,500`、本次 persistent/scratch `74,440/3,339`，预测 `134,219,445`，比 128 MiB 上限多 1,717 B。当时 Core heap 仅 6,303,497 B；父/子当前 RSS 89,014,272/98,156,544 B，run 文件 1,890,329 B。累计 RAW persistent 预约为 132,490,952 B，是停止主因；一次 cutoff 又把 545,632 B 作为持久状态累计，其中随全部 normalized 行增长的窗口扫描量实际只应是临时 scratch。

Core heap 分项为 history 2,125,475 B、normalized 3,954,852 B、seen 索引独占 216,288 B、窗口/特征 2,856 B、quality 710 B、基础 3,316 B。`seen.values()` 与 1781 条 normalized 是同一批对象，因此去重索引只有字典/键的独占开销，并非又保存一份 normalized；真实重复主要是 history RAW 与 normalized 表示。Core `_window` 的一次扫描约 0.70 ms。结论是：旧早停以保守预约失真为主，确有 history+normalized 双表示，但在该点不是实际内存耗尽；CPU 主因也不是窗口扫描。

## 第二阶段：已证实瓶颈的最小修订

1. `StateReservations.reserve_core_row` 保留分配前检查和 128 MiB cap。RAW 改为 `8192 + 4 × encoded row` 的持久上界及 2/3 倍 scratch；它仍覆盖 history 加 normalized/seen 或 quarantine 及容器余量，不再无条件每条加 64 KiB。cutoff 只把控制 history 和固定数量特征行计为 persistent；随 normalized 数增长的 selected/扫描只计本次 scratch。
2. `ProcessWriter` 仍在每次审核遍历全部 run 文件、拒绝 symlink/特殊文件/逃逸并按八分区收费。仅缓存 stat 签名未变化的 manifest 解析、数据 hash 与 RAW/CONTROL/table 分类；文件变化、同长度篡改、新 orphan 会使缓存失效并重新验证。新增回归证明篡改后原 RAW 不再获可信分区、未知文件继续进入 orphan 分区。
3. 未修改 Core、raw-first、Decimal/单位、去重/冲突、quality/null、cutoff/highwater、发布回执、只读 replay、队列/RSS/磁盘/文件 cap 或 REAL fail-closed。

修订后相同 100/500/1000/2000 条样本全部完整，committed 预约分别为 1,332,593 / 6,365,393 / 12,664,478 / 25,336,180 B；Core heap 分别为 370,594 / 1,776,754 / 3,537,704 / 7,071,821 B。100k 上限源在生成 10,500 条、处理 10,494 条后仍按原 128 MiB 上限安全停止；首个失败仍是 RAW 分配前检查，预测仅超 817 B。当时 Core heap 37,034,957 B、父/子当前 RSS 146,194,432/90,603,520 B、文件 9,966,282 B；双 replay 为 PARTIAL、只读且输入不变，没有称为 100k 完成。

## 第三阶段门禁与持续能力

| 场景 | 实际结果 | 状态 |
|---|---|---|
| 6000 条、10条/模拟秒、600秒，内存记录 writer | 6000 normalized、600 feature、最终预约 98,870,347 B；末尾30秒固定 oracle 为 B/S/V=0.150/0.150/0.300 BTC、Delta=0、TI=0、N=300 | 仅状态/语义回归 PASS；不是物理 writer 验收 |
| 严格 ProcessWriter，600条、10条/模拟秒、60秒 | 600 normalized、60 feature、617文件、1,620,876 B；14.999秒，父/子CPU 1.641/13.198秒；batch/table p95 75.4/80.8ms；双 replay PASS | `PASS_LIMITED_OFFLINE` 短阶段 |
| 1000条单秒突发 | 完整1000 normalized、1 feature；预约12,656,387 B；双 replay PASS | `PASS_LIMITED_OFFLINE` |
| 严格 ProcessWriter，600秒、1/5/10条/秒 | 短阶段已显示文件审核随累计文件数增长，未满足进入门禁 | `NOT_RUN_SCALING_BLOCKED` |
| 单机离线600秒真实墙钟 | 前一门禁未通过 | `NOT_RUN` |
| Binance/REAL | 未授权，入口不变 | `BLOCKED` |

“是否能在所选负载持续600秒”的诚实答案是：**未证明，不能标 PASS。** 目前能确认状态预算可容纳 10条/秒 × 600秒的单窗口语义路径，但严格 ProcessWriter 的持续写盘/全目录审核尚不能据短样本安全外推为 600 秒。100000 是上限，本轮没有要求或宣称完成。

若继续，建议先由用户选择一项设计预算，再另轮复核：

- 首选：设计可恢复的追加式 raw/control 与 feature journal/checkpoint，减少每秒不可变 segment/manifest 数，同时保留每个 cutoff 的 durable-before-compute 和 published-after-feature 顺序；这是存储格式变更，需要 L05/只读 replay 独立复审。
- 备选：正式降低首次演练 grid/发布频率；这会改变首次演练预算与输出频率，按 Issue 评论必须先审批，本轮未做。
- 单纯降低消息上限不能解决“即使低消息率仍每秒发布并积累文件”的 writer 主瓶颈；单纯提高 128 MiB/512 MiB、取消全目录审核或删除历史文件均被拒绝。

## 需求、测试与验证

适用映射：REQ-001/002/003/004/009/010/012/016/017/018；DEV-003/004/006/008/013 的 aggTrade/Delta 子集；TEST-TRADE-01/02/03、TEST-TIME-01/02、TEST-FEAT-01/02、TEST-QUALITY-01、TEST-STORE-01、TEST-REPLAY-01、TEST-OPS-01/02/03。新测试覆盖 6000/600 的状态预约与固定 oracle、审核缓存命中/篡改失效/orphan 计费；历史 L01–L07 与 F22 修复测试未改预期。

本地 Python 3.12.13 实际结果：

| 命令 | 退出 | 结果 |
|---|---:|---|
| `python -m pytest -q tests/unit/test_bounded_capacity.py tests/unit/test_bounded_state_contract.py ...` | 0 | 6 passed |
| `python -m pytest -q` | 0 | 467 passed，0 failed/error/skipped |
| 四组独立 review pytest | 0/0/0/0 | 15 / 9 / 8 / 4 passed |
| `PYTHONPATH=src bash .github/scripts/ci_checks.sh`（最终完整重跑） | 0 | 十三项全部 exit 0；默认467、独立15/9/8/4，ruff/mypy/pip/config/offline smoke均PASS；本机Linux网络namespace检查NOT_RUN |

十三项首次完整运行保留在忽略目录 `data/session/l04-ci-first-failed-20261013`：默认及独立套件通过，但 mypy 因缓存可空类型未显式收窄而退出1，offline-e2e 因旧 `ci-results/offline-smoke` 已存在、拒绝覆盖而退出1。修复一行静态收窄后，将整份失败输出移入上述目录，再从空 `ci-results` 完整重跑十三项；没有只挑失败项或删除失败证据。

限制：父/子 RSS 表中的 current RSS 来自 `ps` 采样；`writer_metrics` 的 `ru_maxrss` 合计仍只是两个进程历史峰值之和，不是同一瞬时实测。Core heap 使用递归 `sys.getsizeof` 对共享 identity 去重，是 Python 可达对象近似，不是 OS RSS。物理满盘、断电、硬 OOM、正式单位审批、共享出口与 REAL 均未验证。
