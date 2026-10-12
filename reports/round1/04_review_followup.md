# R1-04 续查与首轮离线收尾

任务：[Issue #6](https://github.com/shukehi/btc_microstructure/issues/6) / [续办6118293337](https://github.com/shukehi/btc_microstructure/issues/6#issuecomment-6118293337)，规格0.1，任务卡 `tasks/04_review.md`。

**reviewed_commit：`4962325fe3ed9a0d03ce8c3d2b42a2db898a7cbe`（本次最新 main）。报告 head 与对应 Actions 见 [PR #7](https://github.com/shukehi/btc_microstructure/pull/7) 顶部；报告提交仅标识审查材料，不替代此被审业务 SHA。**

相对原被审 `0b65be4933e9c36abedf784e608b832c56cee131`，main 新增 PR #10 的 R104-01 定向修复（`3519c9eaf286367d7579ebcc6677716b812f6aae`）及 PR #11 的共享规则（`4962325…`）。已读最新 AGENTS、Issue全文/评论、PR7原报告及5485855471、PR10复核5486128414、PR11同步复核5486282114、PR1/PR5原发现及复核、适用 docs/00/01/02/03/05/06/07 与前三轮实现/修复报告。没有额外业务增量混用。

## 分项结论

| 维度 | 当前结论 |
|---|---|
| 本次约定离线审查 | **COMPLETED，未发现新的已复现阻塞。** 原十二点的首轮适用边界已有可追溯证据；不是无缺陷保证，也不是授权合并 |
| 离线代码 / 计算 / 重放 | **PASS（限定首轮 synthetic 合同）**：本轮十三项检查及独立3项控制故障检查通过，固定数值/质量/null/时间/游标断言支持结论；不只根据绿色CI或共享摘要推断 |
| R104-01 | 修复归属 PR #10；5486128414已定向复核。本轮原反例、独立4案例、默认质量管道回归均通过；旧FAIL继续保留，不重新实施修复 |
| R103-A/B/C | 修复归属PR #5；5483555449历史复核与当前独立8项、默认恢复/排序断言一致，不重开旧线程 |
| 真实协议 | REST **BLOCKED（历史TIMEOUT）**；WS **NOT_RUN**；本次无新现场观测 |
| 真实单位 / 持续预算 | **UNKNOWN / UNVERIFIED**；人工BTC元数据不升级真实能力 |
| 有界实时演练 / 完整24h工程验收 | **NOT_RUN / NOT_RUN** |
| 多期限研究 / 收益能力 / 完整M0/M1/M2 | 研究与收益能力未实现/未验证；完整系统验收仍 **PARTIAL**。有限离线审查完成与这些状态分别记录 |

## 十二点续查表

原证据列均为**历史报告读回**，不能冒充本轮重新执行。结果列中的测试节点本轮已出现在 `followup/attempt-01/pytest*.xml`；静态路径来自本轮源码/断言阅读。文件均相对项目根目录，行号对应 reviewed_commit。更细节点及边界见新会话 `fresh-review.md` 和两个只读 slice 记录。

| # | 原证据与PR #10影响 | 本次补查 / 实际结果 | 剩余限制 |
|---|---|---|---|
| 1 方向/聚合/单位/精度 | 原表1固定方向/Decimal通过；PR10影响normalize质量与版本，需确认数值保留 | core:249-321/370-383直接Decimal、m=true→SELL、q/nq不双计；`test_normalizes_m_false_as_buy_and_m_true_as_sell`、`test_high_precision_decimal_uses_exact_decimal_not_float`、unknown-unit/mismatched-evidence节点及CLI固定.60/.40/2÷3/3通过 | 真实BTC单位UNKNOWN；不称聚合消息数为逐笔数 |
| 2 去重/冲突知识时点 | 原表2跨session和冲突通过；PR10新增质量收据，需复核不会双计/倒灌 | core:195-229经济键无session、冲突隔离；quality_observations与首条经济记录分开。`test_conflicting_duplicate_merges_upstream_quality_with_conflict_audit`、跨session/restarted去重、future-conflict与high-water节点通过 | 真实source_epoch/ID重置和WS/REST跨源合并非本切片 |
| 3 接收时间/边界/预热/跨日 | 原表3通过；PR10影响窗口质量，需边界回归 | core:325-366按receive/high-water选[t-W,t)，offline.py:428-484保留原序号与运行游标映射。左右边界、同刻cutoff两分支、midnight、warmup、R103-C排列/会话边界节点通过 | 午夜证明当前平坦segment/内核窗口连续，不声称实现了规范所有日期分区或真实补回服务 |
| 4 缺口/时钟/队列/存储 | 原表4为R104-01 FAIL/停止扩展；直接受PR10影响，重点补查 | core:47/199/356-366保留质量、按家族失效；管道 `test_gap_known_round_trips_through_parquet_and_degrades_data_health` / 提示混合 / 原Parquet反例均通过。存储queue/low-space/flush失败断言通过；新增 `control_boundaries.py` 两种控制故障+接收钟回拨3项通过，不报健康零 | 故障注入/人工控制，不是物理断电、满盘或真实时钟演练；RAW CLOCK_UNCERTAIN的lead-lag限制与严重控制事件fatal分别解释 |
| 5 raw/派生分离/可信恢复 | 原表5已有R103-A/B回归；PR10未改storage，回归确认 | offline.py:526先持久raw再Core；storage.py:252/452-620可信前缀、失败尾部/隔离恢复，:674-700尽力诊断。`test_failed_raw_commit_keeps_bytes_cursor_and_uncertainty_across_reopens`、`test_failed_raw_tail_propagates_through_repeated_raw_only_parquet_builds`与独立8项通过，历史74文件字节不变 | 实际断电/物理满盘/目标文件系统耐久性NOT_RUN，不承诺exactly-once |
| 6 历史发布时间 | 原表6普通/缺日志通过，复杂控制未扩展；PR10重放版本改变，重点补查 | core:135-164按高水位快照、pending cutoff及记录时点校验；不读新墙钟。`test_feature_available_time_requires_logged_publication`、`test_missing_publication_keeps_pending_available_null_and_replay_unverified`及future-quality保护通过；新增控制故障仍只用给定历史publish且值null；两次replay/raw-only build通过 | SYNTHETIC发布记录只验证合同；缺记录不造真实历史时点；真实发布故障/现场时延NOT_RUN |
| 7 horizon/未来反事实 | 原表7固定场景通过；PR10新增未来质量路径，需回归 | horizon与window分离；`test_research_horizon_changes_do_not_change_past_feature_values`、`test_future_horizon_change_uses_same_raw_and_same_feature_values_with_new_hash`、future RAW/conflict/quality反事实通过。改horizon可改变config_hash，数值不变 | 固定反事实覆盖追加成交/冲突/质量收据，不是所有可能未来变动的形式证明；无未来收益标签实现 |
| 8 默认离线/权限 | 原表8只初查，受停止条件限制；PR10未改CLI/transport，补读全调用路径 | cli.py:26-39/51-75 synthetic dispatch，:83-123固定probe路由/显式网络gate；offline.py:112-115拒绝真实模式；默认socket fixture、live-mode拒绝、无网络开关probe/custom-route拒绝节点通过。src路径无私有/签名/下单/API-key读取依赖 | 不是全面漏洞/渗透审计；本次未启用真实probe |
| 9 测试/预期/容差 | 原表9代表性已读但PARTIAL；PR10新增25默认+4独立+原Parquet检查，重点补查 | 0b65→当前原默认测试无修改/删除。人工.60/.40、null、显式时刻/高水位预期独立于实现，Parquet直接断言None；SOURCE_SAMPLED健康对照防过度失效。无新增skip/xfail或放宽容差；原Float64阈值保留，其他精确相等。五份JUnit全0fail/error/skip，十三退出码0 | 不穷尽全历史每条断言；旧失败档案/不属当前lint选择的探索脚本未改写 |
| 10 环境/README复现 | 原表10旧干净环境269/15/9/8；PR10新增测试/版本但锁不变，最新基线重跑 | 新venv Python3.12.13，全部27个lock条目精确相等，实际import指向审查checkout。当前ci_checks.sh完整执行；CLI smoke实际collect→两replay→raw-only build并读Parquet/比较逻辑表，映射README同一行为；不机械重复同命令 | 本机Darwin arm64，不冒充Linux隔离；README字面demo目录未另跑。Hosted证据另列 |
| 11 全量配置/布尔验收 | 原表11初查后未展开；PR10只增加版本门槛，补读 | capabilities.py:8-24返回UNVERIFIED，validate-config不启动全量来源。strict offline只收synthetic/um_agg_trade/as_received；fake bool/unknown字段/metadata拒绝、`test_declared_probe_does_not_verify_capabilities`及legacy/unknown-version节点通过 | 未构造真实预算/现场身份验证；不以bool或synthetic单位代替真实核验 |
| 12 文档/模拟/实测分离 | 原表12发现后停止全面文档核验；PR10增加修复/版本说明，重点补查 | README R1-03/R104段、当前/历史报告与review按SHA/时间读取；delta.1历史→trades.2-quality/delta.2-quality重算/拒绝门槛一致，现有实际测试验证。原规格/交接前缀与阶段进度有历史时间语境，未回写 | README旧“三组测试”等数量摘要已落后，当前实际五组以脚本/JUnit为准；属非阻塞历史表述，本任务不改文档/规格。未实现及非首轮材料不扩展 |

## 实际执行、命令与环境

证据目录：[round1_review/followup/attempt-01](round1_review/followup/attempt-01)。原 `04_review.md` 与原证据原文保留，旧 reviewed_commit/FAIL/PARTIAL不变；本报告是新基线后续结果。只新增本报告、独立脚本及小型脱敏日志/JUnit/回执，没有业务/原测试/配置/锁/工作流/AGENTS/规格增量。

隔离worktree从原报告head `063da7a…` 普通merge最新main，merge为 `b5c08ae937a87dc18d3896d1771751b72408bf87`；两个父SHA见integrity.json。原工作区未切换、未stash/reset/clean，原AGENTS字节哈希一致。新venv Python3.12.13 / Darwin arm64；PyArrow20.0.0、aiohttp3.12.15、pytest8.4.2及所有lock条目相等；源码import定位明确，environment.json保存摘要，不上传完整本机环境。

本轮整套命令 `bash .github/scripts/ci_checks.sh`，cwd为审查checkout，退出0，使用全新ci-results。每条实际argv/cwd/exit在commands.json，单项log/XML和wrapper checks.log/exit分别保留：

| 检查 | 退出码 | 实际结果 |
|---|---:|---|
| 默认pytest | 0 | 294 passed，0失败/错误/跳过 |
| 独立R1-01 / 02 / 03 / 04 | 0/0/0/0 | 15 / 9 / 8 / 4 passed，各0失败/错误/跳过 |
| 原Parquet缺口复现 | 0 | raw / normalized / feature 均GAP_KNOWN，volume/delta实际None |
| Ruff lint / format | 0/0 | PASS（当前脚本规定范围） |
| Mypy / pip check | 0/0 | PASS |
| validate-config / validate-offline | 0/0 | 原配置CONFIG_VALID且能力UNVERIFIED；synthetic OFFLINE_CONFIG_VALID |
| CLI/Parquet offline-e2e | 0 | PASS，固定Decimal/质量/重复/1s网格/两replay+raw-only build |
| 新独立control_boundaries.py | 0 | 3 passed，0失败/跳过；另列，不混入294或CI默认选择 |

命令 ` .venv/bin/python -m pytest -q reports/round1/round1_review/followup/control_boundaries.py --junitxml=<new-output>/control-boundaries.xml`，实际完整路径见supplement-command.json。脚本只基于规格控制故障/null与历史时间规则，不复制实现生成预期；新脚本Ruff check/format均退出0。无需改业务代码或原测试。原Parquet打印的字符串"None"只是展示，其内部断言为is None，默认pipeline另对Parquet所有流量列真实null断言。

CLI smoke的collect四个RAW / 三个unique / 70 feature行，31s/30s的B=.50/S=.10/V=.60/Delta=.40/TI=2÷3/N=3；两次replay和raw-only build逻辑摘要均 `4b933651c8a63ca21381a101538061c00cc4b0c015e1d631b23d1f6c94fab0f9`。逻辑表值/质量比较和raw重复保留由实际断言验证，summary及smoke-commands.json归档；不上传运行数据。

新Codex CLI session `01a12e95-9bee-7123-a563-3c2edee8820c`只读slice与两native只读slice分工审查十二点，不继承实现聊天；最终结论基于当前源码、断言及上述本轮日志，不以新会话本身作正确性保证。额外小范围CLI测试的只读临时文件失败和22项成功回执另存fresh-cli-receipt.json，不伪装成业务失败或完整验证。native额外运行仅工具观察、没有独立JUnit，不加入正式统计；详见quality-publication-review.md。准备/归档异常与未执行边界见execution-notes.md。

## GitHub读回、历史与交付

- **历史读回**：[main push 38200019517](https://github.com/shukehi/btc_microstructure/actions/runs/38200019517)，event=push，head=`4962325fe3ed9a0d03ce8c3d2b42a2db898a7cbe`，completed/success；metadata保存main-push-run.json。本轮未将该历史运行当成本机重跑，也没有另下载其artifact作哈希核验。
- 最新报告head的Actions必须等待完成并按head读回，结果写入PR #7顶部/最终交付。此前PR7的269/15/9/8绿色只属于旧基线，不替代本轮结果；workflow_dispatch未触发，NOT_RUN。
- 历史审查5485855471确认旧R104反例；PR10复核5486128414确认修复。原失败和旧报告可继续证明缺陷曾存在，不能证明当前未修复，也不能被改写为从未失败。
- 相对main仅本任务审查材料；完整性manifest标识新证据，integrity.json验证74个原报告路径字节未变；原用户AGENTS哈希不变。

## 依赖与结束条件

约定离线范围收尾证据充分，未发现新已复现阻塞，**等待独立复核**后由用户决定是否归档PR7和完成Issue6离线收尾；不自动合并/关闭Issue或解决审查线程。本次无新真实协议观测，不启动后续步骤。

后续只建议：另立有界授权任务补真实接口/单位/持续预算证据；门槛满足后才安排小规模真实采集→保存→重放及受控恢复；24h工程验收再单独设计。多期限研究需合格数据和研究框架，不从离线收尾推导策略收益或交易有效性。
