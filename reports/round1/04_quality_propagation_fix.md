# R104-01：GAP_KNOWN质量传播修复

任务：[Issue #8](https://github.com/shukehi/btc_microstructure/issues/8)，仅修复一个R104-01，不重做系统、不重开R103-A/B/C。基线最新main为 `0b65be4933e9c36abedf784e608b832c56cee131`，与Issue已确认业务代码一致；分支 `fix/r104-01-quality-propagation`。报告PR #7固定提交 `063da7acd32433d9b3fbc8643ee3f4861c03a69d`仅作只读来源，未向报告分支推送业务代码。

## 读取、保护与来源

已读取AGENTS.md、CODEX_START_HERE、Issue8全文/最新评论（开始与交付前均无新增评论）、Issue6、PR7报告/原反例/失败输出、[独立复核5485855471](https://github.com/shukehi/btc_microstructure/pull/7#pullrequestreview-5485855471)，以及docs/01事件/去重/质量合同、docs/02截止/高水位/重放、docs/03通用/成交合同、docs/05适用验收及当前内核/管道/存储/CLI/测试/CI。

`review/R1_04/original/reproduce_gap_flag.py`逐字复制自上述报告SHA；原报告和失败输出另存 `quality_propagation_fix/original-*`，不改历史。来源路径/SHA-256见sources.json。四个独立内核案例原文另存original/test_r104_quality_review.py.txt，可执行文件只作Ruff格式化，AST等价且断言不变，见review-equivalence.json。

用户原有未提交AGENTS.md始终保留、未stash/reset/clean或暂存提交；哈希保存在before/environment.json并交付前核对。原规格/配置/样例/依赖锁、原测试/独立15/9/8与R103历史报告不改；after-02/integrity.log退出0。没有增加依赖或重构存储。

## 修复前实际复现

在原 `.venv` Python3.12.13、PyArrow20.0.0、aiohttp3.12.15、pytest8.4.2 / macOS arm64环境，以正常项目导入和真实Parquet执行：

| 命令 | 退出码 | 实际结果 | 证据 |
|---|---:|---|---|
| `.venv/bin/python -m pytest -q review/R1_04/checks/test_r104_quality_review.py --junitxml=reports/round1/quality_propagation_fix/before/kernel.xml` | 1 | 2失败/2通过/0跳过 | before/kernel.log/xml/exit |
| `.venv/bin/python review/R1_04/original/reproduce_gap_flag.py --output-parent data/r104-quality-before` | 1 | raw有GAP，标准化/特征为[]，Delta=.40/V=.60；合同断言失败 | before/parquet.log/exit |

实际cwd/argv/退出码在before/commands.json，版本与用户文件哈希在environment.json。初次元数据收集误用系统Python3.14，查询PyArrow失败；实际两个复现命令均使用 `.venv`3.12，元数据随后用同一解释器重采，见setup-note.txt。没有用模块提取、Arrow占位或改预期凑数。

## 修复语义与版本

1. RAW输入不改写。标准化按docs/01枚举校验flags为list[str]，保留上游标记并合并派生单位原因。缺省字段兼容旧人工包装为[]；显式null、字符串、未知/非字符串值进入隔离，quality包含SOURCE_SCHEMA_CHANGED，原值以upstream_quality_flags_json及原始payload/接收记录追溯。
2. 当前成交家族失效原因：WARMUP、GAP_KNOWN、UNIT_UNKNOWN、DUPLICATE_CONFLICT、SOURCE_SCHEMA_CHANGED、HISTORY_ONLY。GAP使B/S/V/Delta/TI及原有不可用count合同为null，不能过滤掉坏记录再宣称量完整。上游UNIT_UNKNOWN阻止写造base/notional。
3. SOURCE_SAMPLED只保留限制，不使成交流失效。SEQUENCE_INVALID/COVERAGE_INSUFFICIENT/STALE的其他家族影响不扩大到成交；RAW CLOCK_UNCERTAIN保留原lead-lag限制，不新增流量失效策略，现有CLOCK_UNCERTAIN控制事件的fatal行为不改。没有新增通用质量框架。
4. 去重仍按经济键，只保留首条标准化经济记录且不原地注入后来信息；每条质量收据独立记录观察序号/接收点/原经济接收点，重复/冲突审计合并上游与DUPLICATE/CONFLICT原因并链接经济序号。健康重发不撤销已知证据，也不重复计量。
5. 质量仅在接收序号<=截止高水位、观察时刻<=截止，且原经济点或该收据点位于`[t-W,t)`时影响当前窗口；不构造两点之间的缺口区间。唯一RAW在右边界t仍排除；重复质量在t对已在窗内的旧经济事件，仅当高水位已包含这条收据时可生效。已发布快照不改写，两个点都离开窗口后局部影响结束；真实区间仍由GAP_OPEN/CLOSE表示。
6. 特征新增data_valid；summary列valid/invalid行数、feature_quality_counts和data_health。WARMUP可作为运行期正常不可用行，SOURCE_SAMPLED与WARMUP并存不误算为非预热错误；进程/存储status=PASS与数据健康分开。带缺口的处理可以正常完成，但data_health=DEGRADED，相关行null/原因明确。
7. raw schema/adapter继续 `r1-03.1` / `um-agg-synthetic.1`（原始封装未变）；标准化新增 `normalization_version=trades.2-quality`，特征版本 `delta.2-quality`，manifest保存两个版本。旧人工缺字段可读；旧delta.1派生表不与新语义冒充相同实时重放，replay明确拒绝并指向build-features。build可从可信旧raw/control到新目录重算，保存build_from/新版本；未知版本仍拒绝，旧原始/派生文件不覆盖。

版本实际核验：除自动化legacy gate案例，另用**修复前真实保存**的delta.1错误输出（Delta=.40）执行新build，得到delta.2-quality的null/GAP_KNOWN。旧raw、manifest、Parquet全部SHA未变；结果 `actual-legacy-rebuild.json`。这是修正版历史重算，不声称旧程序当时已发布正确值。

## Q01–Q08与原需求映射

| 验收 | 实际测试节点/证据 | 需求及原TEST | 结果 |
|---|---|---|---|
| Q01原反例 | original/reproduce_gap_flag.py修复后实际执行；test_quality_pipeline.py::test_gap_known_round_trips_through_parquet_and_degrades_data_health；独立raw两项 | REQ-009/012；DEV-004/008；TEST-FEAT-02/QUALITY-01成交子集 | PASS：raw/std/features可追溯，真实Parquet B/S/V/Delta/TI/count为null |
| Q02健康/空窗/预热 | test_healthy_duplicate_trade_keeps_manual_flow_expected_values及原healthy-empty/warmup测试 | REQ-004/009/012；TEST-TRADE-01/02、FEAT-01/02 | PASS：.60/.40/2/3/3保持，0与null区分 |
| Q03提示/混合 | test_source_sampled_is_advisory_and_does_not_null_trade_window；test_gap_known_mixed_with_source_sampled_nulls_flow_and_preserves_flags；pipeline采样/预热对照 | REQ-009/012；FEAT-01/02 | PASS：提示不伪造缺口，混合正确无效 |
| Q04时间/范围 | test_quality_left_boundary_is_included_and_right_boundary_is_excluded；test_later_duplicate_quality_above_high_water_does_not_invalidate_snapshot；test_future_quality_counterfactual_keeps_past_features_identical；same-cutoff参数两分支 | REQ-003/010/011；TIME-01/02、REPLAY-01 | PASS：5/30s局部、边界、高水位、未来不倒灌 |
| Q05去重/隔离 | test_conflicting_duplicate_merges_upstream_quality_with_conflict_audit；test_rebuild_raw_prefix_preserves_cross_session_quality_without_recount；非法flags/错误payload | REQ-002/004/012；TRADE-02/03、STORE-01 | PASS：不双计、不抹质量、不修改首条标准化记录 |
| Q06持久/重放 | test_collect_replay_twice_and_raw_only_build_keep_gap_nulls_and_raw_bytes；actual-legacy-rebuild.json | REQ-002/010/018；DEV-003/006；STORE-01、REPLAY-01 | PASS：两次replay/raw-only build与固定null预期一致，raw哈希不变 |
| Q07非法输入 | test_invalid_quality_flags_are_quarantined_with_original_flag_json[4参数]；pipeline unknownquality/unknownversion | REQ-012/017；QUALITY-01、OPS-03适用 | PASS：不变为健康[]，原输入与原因保留 |
| Q08防退化 | 原269项、独立R1-01/02/03的15/9/8、原控制gap/精度/时间/发布/恢复、两配置、CLI smoke | REQ-001/003/004/010/016/017；原适用TEST | PASS，全部实际执行，无skip/xfail |

精确节点清单test-nodes.txt。新增默认25项：内核19、管道6；保留原269，默认合计294；四个R1-04独立案例另分组。数学Float64对照按原rtol1e-10/atol1e-12，没有放大容差、删除断言或改原样例。

## 实际执行结果

全部cwd为项目根目录；完整真实argv/cwd/退出码在quality_propagation_fix/after-02/commands.json，每项独立log/JUnit；同目录locked-versions.json逐项确认现有lock全部相等。验证脚本自动创建新after-NN目录与data输出，不覆盖before/历史证据。after-01曾完整292及全部检查通过；新增同刻高水位两分支后，以after-02的294为最终本机结果。

| 检查 | 退出码 | 通过/失败/跳过 |
|---|---:|---|
| 默认pytest | 0 | 294/0/0 |
| 独立R1-01 | 0 | 15/0/0 |
| 独立R1-02 | 0 | 9/0/0 |
| 独立R1-03 | 0 | 8/0/0 |
| 独立R1-04 | 0 | 4/0/0 |
| 原始Parquet反例脚本 | 0 | 真实Parquet读回标记保留、合同断言通过 |
| Ruff check / format | 0/0 | PASS |
| Mypy / pip check | 0/0 | PASS |
| 原配置 / 首轮配置验证 | 0/0 | PASS |
| 原CLI/Parquet smoke | 0 | PASS，原固定值及两次重放/raw-only重建 |
| 保护文件diff | 0 | 原规格/配置/锁/原测试/R103证据/工作流不变 |

原反例日志中的`"None"`来自原脚本对读回对象的str显示；内部断言是`is None`，新项目Parquet测试对全部流量列直接断言真实null，不把字符串当null。

CI保留原十一项并增加独立R1-04及原Parquet断言两组，共十三项检查。独立案例各有JUnit/日志/退出码，任一失败使job失败；required job仍 `Python 3.12 offline checks`，只读权限、checkout不保存凭据、网络命名空间边界不变；工作流文件原样。原脚本不仅存进报告，修复后断言会在CI实际执行。

同会话focused code-reviewer检查Q01–Q08及最后同刻边界：0代码阻塞，COMMENT，不代替独立复核。Reviewer的Markdown误送Ruff/研究validator误用离线YAML是工具选择错误，正确范围随后通过，记录在code-review.md；未据这些错误扩大业务修复。无LSP接口，用Mypy/Ruff/编译作诊断。

## 修改文件与复现

源码仅offline_core.py（质量校验/分类/收据知识/行级有效性和版本）、offline.py（统计/版本校验和旧raw重算）；新增tests/unit/test_quality_propagation.py、test_quality_pipeline.py。CI脚本接入两组，README追加新版本/数据健康说明。review/R1_04保存固定源和原样预期。本报告/quality_propagation_fix保存小型证据。未修改offline_storage.py或旧报告PR分支。

在原Python3.12锁定环境执行：

```sh
.venv/bin/python -m pytest -q review/R1_04/checks/test_r104_quality_review.py
.venv/bin/python -m pytest -q tests/unit/test_quality_propagation.py tests/unit/test_quality_pipeline.py
.venv/bin/python review/R1_04/original/reproduce_gap_flag.py --output-parent data/r104-quality-manual
.venv/bin/python reports/round1/quality_propagation_fix/verify.py
```

## 限制与停止

本次R104-01定向实现/离线回归按证据PASS；修复前FAIL保留。R1-04整体覆盖仍PARTIAL，不用Closes #6。报告PR #7是否合并不是依赖。修复PR关联Issue8、Refs #6和报告PR7，保持OPEN等待独立复核。

真实REST历史BLOCKED；WS/有界真实演练/24h NOT_RUN；真实数量单位UNKNOWN、持续预算UNVERIFIED。无新真实数据、协议核验、交易或收益结论；未填满物理磁盘、未改系统时钟/网络。只运行当前修复与必要回归，不开始下一任务。

修复PR和源码提交Actions见下节；本报告随后归档源码提交的远程回执，最终报告head与最新运行以PR正文/交付答复为准。

## 修复PR及远程验收

- 独立修复 [PR #10](https://github.com/shukehi/btc_microstructure/pull/10)，base main，Closes #8、Refs #6及报告PR7。PR7仍OPEN、head063da7a未改变，业务代码没有提交到报告PR。Issue6仍由后续独立收尾决定，未在本任务关闭。
- 修复源码提交：`68d0eae17468d5ddf59d601aed05a3e850ce8202`；随后仅归档远程证据，最终head/对应运行见PR正文和交付答复。
- [Actions38194103810](https://github.com/shukehi/btc_microstructure/actions/runs/38194103810)，pull_request，精确head=上述源码提交，**SUCCESS**；required job `Python 3.12 offline checks` SUCCESS。实际Linux/Python3.12.15。
- 五份JUnit实际294/15/9/8/4均0fail/error/skip；results.tsv十三项退出0。新独立R1-04有单独JUnit，原Parquet反例在CI实际退出0，源码/shape的null断言由默认管道测试实际执行。网络命名空间校验及CLI/Parquet smoke均PASS。
- 小型实际日志/JUnit/results.tsv保存在 `quality_propagation_fix/ci-38194103810/`；run/job/API/artifact回执另存。整个before失败记录与历史PR7结论原样保留。
- artifact `ci-logs-38194103810-1`，ID11722508441，338131字节，SHA-256 `189bdc1a4f730847f8c42f22e27fb9e5f0aca7fd637c27ac41a8e28f9a313a23`。实际下载ZIP哈希与GitHub一致、CRC通过，回执artifact-validation.json；14天保留。不提交整个工程ZIP。

所有验收只针对R104-01定向离线实现。修复PR保持OPEN、auto-merge关闭，不自动合并；交付后停止，等待独立复核，R1-04整体PARTIAL及真实能力门槛仍保留。
