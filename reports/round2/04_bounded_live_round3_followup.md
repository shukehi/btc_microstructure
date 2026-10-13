# R2-04 / Draft PR #22 第三轮整改跟进

**本轮仅处理 Review 5500034324 的 F22-02-01、F22-02-02 及对应离线验收记录。** 基线/PR head 为 `76d8d05d07a8fcc3e4dc209cf238e0ae79b8f1d9`；不改原规格、单位候选、网络配置、依赖锁、历史验收文件或原测试预期。`collect-bounded` 和 REAL 继续无条件拒绝；Binance/probe/REAL/600s 均不运行。

## 问题 → 源码 / 测试 / 验收映射

| Review 发现 | 基线源码缺口 | 本轮最小修订边界 | 确定性反例与验收 |
|---|---|---|---|
| F22-02-01 RAW-first | `bounded_live._raw_row()` 在 `DurableWriter.append/flush` 前调用 `payload_for_core`；当解析器在预期捕获集之外失败时，当前路径尚无可信 raw ack。 | 拆分为不做 UTF-8/JSON 解析的有界 raw envelope（长度/hash/base64/时间/session/sequence）与 durable ack 后的 Core payload 构造。只有 `flush` 可信 ack 后才调用 `payload_for_core`/`Core.process`；writer 失败时不前进 Core/cutoff/publish。已提交的坏 UTF-8/JSON 由 Core 以 `SOURCE_SCHEMA_CHANGED` 隔离，重放从同一 durable base64/hash 得到同样结果。 | 新增：注入解析调用顺序，证明 parser 只在 writer ack 后运行；注入 writer flush 失败，证明 parser/Core/cutoff/publish 均未运行。坏 UTF-8/JSON 核对 raw 原文、quarantine、feature null/质量及两次只读 replay 一致。保留 500条/512KiB/0.5s 刷新和1000条/1MiB/1s队列边界，不改为逐条 fsync。 |
| F22-02-02 单类 segment / 分区计费 | `bounded_writer._worker` 用 `any(row.kind == RAW)` 把混合 batch 整体计入 RAW；`_charge_raw_file` 也在发现任一 RAW 后立即归类，控制行可绕过 16MiB control cap。 | `ProcessWriter.append` 在 RAW↔control 切换时先刷新并等待可信 ack，再接受新类别；worker 对人为直接混合 batch 明确拒绝。ProcessWriter 生成的 raw-control manifest 记录实际 `segment_class`，物理审计校验声明与内容；旧的无字段同类 manifest 保持只读兼容，混合/不匹配文件不得被 `any(RAW)` 信任计入 RAW。Store 公开接口不改。 | 新增：直接混合 worker batch 拒绝；ProcessWriter 类别切换自动边界 flush/ack；小 cap 注入下 control 16MiB 和 RAW 192MiB 边界/超1语义；temp/fault/recovery/manifest 分类及不漏计/不重计，不实际分配近200MiB。 |
| 验收状态一致性 | 历史 `acceptance_status=PARTIAL_L04_L05_BLOCKED` 与报告“L05 限定离线 PASS”表述不一致。 | 不回写历史 acceptance_status 或旧证据；本报告分项记录 L04/L05 与本轮两项修复的实际结果。 | L04 离线合同与受控压力单独记录，100k/600s 继续 PARTIAL/NOT_RUN；L05 只在本轮两个边界及既有故障回归通过后标记“限定离线 PASS”；REAL 始终 BLOCKED。 |

## 当前验收记录

- F22-02-01：`PASS_LIMITED_OFFLINE`。新回归证明 parser 只在 raw batch 可信 ack 后运行；注入 raw flush 失败时 parser/Core/cutoff/publish 都未前进；注入事后 parser 非预期失败时 raw base64/hash 已在可信 segment，不写伪特征。坏 UTF-8/JSON 既有回归确认 Core `SOURCE_SCHEMA_CHANGED` 隔离且两次 replay 一致。
- F22-02-02：`PASS_LIMITED_OFFLINE`。ProcessWriter RAW↔control 切换先 flush/ack；直接 worker 混合 batch 以 `MIXED_SEGMENT_CLASS` 拒绝且无 raw manifest。新 manifest 有 `segment_class=RAW|CONTROL`，审计核对实际行类别；旧无字段同类 manifest 仍可只读分类，混合旧文件收费到 orphan 而不再被 `any(RAW)` 吞掉 control。Budget 无大分配验证 control 16MiB 与 RAW 192MiB 的精确边界及超1拒绝。
- L04：本轮物理时钟/ProcessWriter 1000条源完整保存，输入交付 **0.824103s**，全流程 **1.933427s**，两次只读 replay PASS 且与原派生表一致。进一步 100k 受控源在 **2209** 条时以 `ENCODED_STATE_LIMIT` 早停，两次 replay 均 PARTIAL/输入不变；100k完整持续能力保持 `PARTIAL`，600s 保持 `NOT_RUN`。
- L05：本轮两个 review 边界、既有 append/flush/fsync/rename/manifest fault、temp/orphan/recovery/分区及重放回归均通过，状态为 `PASS_LIMITED_OFFLINE`。这不包括真实满盘、断电或硬 OOM。
- REAL：`BLOCKED`。Binance/probe/REAL/600s/NTP/真实满盘/断电/硬OOM 均 `NOT_RUN`。

## 证据保留约定

新反例首先在本基线运行，before 命令、完整日志、JUnit 和退出码保存到忽略的 `data/session/round3/`，报告只收录简短脱敏摘要与 hash。after 使用 Python 3.12.13，`PYTHONPATH=src`；临时 `.venv` symlink 只删本轮自建链接。不 commit/push/回复 GitHub。

## 实际命令、退出码与简短证据

跟踪的脱敏摘要见 [verification.json](bounded_live_round3/verification.json)；完整大日志/物理 run 仅在忽略的 `data/` 中。

| 阶段 | 实际结果 |
|---|---|
| before 新反例 | exit **1**；**4 failed / 3 passed**；失败分别证明 parser早于ack、writer失败前已解析、类别切换未flush、直接混合batch未拒绝。原 log SHA-256 `3b033efc…a0707b`，JUnit `531b2f3d…b0`。 |
| after 定向 | exit **0**；**95 passed / 0 failed/error/skipped**，包含本轮11项、writer fault/temp/recovery、bad UTF-8/JSON、feature失效/replay、live/cutoff/publish 回归。 |
| 1000 物理压力 | exit **0**；1000/1000 raw，normalized 1000，feature 2，`DISCONNECT` 受控停止；两次 replay PASS/input unchanged/matches original。 |
| 100k 进一步受控压力 | exit **0**；请求100000，实际生成/保存2209，`ENCODED_STATE_LIMIT`安全早停；两次 replay PARTIAL/input unchanged，不存在完整原派生表供匹配。 |
| `bash .github/scripts/ci_checks.sh` | exit **0**；`results.tsv` **13/13 exit 0**；默认 **465**、独立 **15/9/8/4**，全0 failure/error/skipped；Ruff check/format、Mypy、pip check、config/offline validation、Parquet与offline-e2e均通过。本地 Linux network namespace 验收 `NOT_RUN`，不冒充 hosted isolation。 |

临时 `.venv -> ../r104-followup/.venv` 已删除。本轮 hosted Actions `NOT_RUN`；未 commit/push/回复 GitHub，留待父会话独立复核后更新同一 Draft PR #22。
