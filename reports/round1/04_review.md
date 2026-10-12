# R1-04 首轮复查：存在新阻塞，不作无条件离线验收结论

任务：[Issue #6](https://github.com/shukehi/btc_microstructure/issues/6)，原任务卡 `tasks/04_review.md`。

**reviewed_commit：`0b65be4933e9c36abedf784e608b832c56cee131`（最新main、PR #5合并提交）。审查分支 `review/r1-04-round1-assessment`。本报告PR的提交SHA仅标识报告版本，不能冒充新的被审业务代码版本。** 与Issue制定时main一致，无额外增量混用。

本次发现 **R104-01（HIGH/P1）**：raw上的GAP_KNOWN被标准化丢弃，受影响窗口仍输出健康Delta/量。两条新上下文审查lane独立得到同一发现；实际Parquet端到端复现成立。按Issue要求停止扩展，未修复代码、未增改原测试、未更改原规格/配置/依赖锁/工作流，未重开已关闭R103线程。

## 分项结论

| 维度 | 本次结论 |
|---|---|
| 审查与交付 | 报告/必要证据交付；整体复查覆盖 **PARTIAL**，确认新阻塞后按指令停止扩大检查 |
| 既有十一项检查 | **PASS**：全新Python3.12锁定环境实际269/15/9/8通过，十一项退出0 |
| 离线代码/计算/重放合同 | **CHANGES_REQUESTED / FAIL（R104-01）**；既有suite成功不能证明所有质量合同成立 |
| 架构审查 | **BLOCK**；raw→标准化→特征的质量信任边界存在已复现遗漏 |
| 真实REST/WS | 仅保留历史 **BLOCKED / NOT_RUN**；本次无新现场观测 |
| 真实数量单位/持续预算 | **UNKNOWN / UNVERIFIED**；synthetic证据不升级真实能力 |
| 有界实时演练 / 24h验收 | **NOT_RUN / NOT_RUN** |
| 多期限研究、收益和交易能力 | 未实现/未验证，不作有效性结论；完整M0/M1/M2不宣布通过 |

## 新会话、隔离与证据边界

新Codex CLI会话ID `01a12d62-2d5f-7ce1-9000-188a35649b85`，Codex CLI0.162.1，workspace-write沙箱，没有继承实现聊天或读取其记忆；最终架构报告 `round1_review/fresh-cli-architecture.md`。另一个fresh-context native code-reviewer负责代码/规格/安全lane，报告 `code-review-lane.md`。两lane都对同一提交的新上下文代码作判断，协调会话只负责隔离环境、实际检查、反例核验和归档；不把原实现会话总结当验收事实。新会话自身也不保证正确性。

为排除用户工作区影响，使用项目内 `data/r104/checkout` 的detached Git worktree（精确reviewed_commit），创建新的 `.venv` 并实际按原lock安装。用户未提交 `AGENTS.md`留在主工作区，没有stash/reset/clean或提交；开始/结束SHA-256一致，见baseline.json及source-integrity.json。隔离checkout的tracked diff退出0。

**证据分类：** 下文“本次实际执行”是本机新环境重跑；“历史读回”是GitHub既有运行或旧报告核对；“代码分析”是具体代码路径判断；“推断/未知”另标。没有把其中一类冒充另一类。

## R104-01：已知缺口质量标记在派生边界丢失

- **严重性/置信度：HIGH（P1），高；本次实际复现。**
- **精确提交：** `0b65be4933e9c36abedf784e608b832c56cee131`。
- **位置：** `src/btc_microstructure/offline.py:313`保留原RAW标记；`offline_core.py:194`只构造单位标记，`:240`覆盖已有quality_flags；`:243-273`的窗口质量判定不检查选中记录的上游质量标记。
- **合同：** `docs/01_data_contracts.md:191`规定GAP_KNOWN受影响流量窗口null；`docs/03_feature_specifications.md:13`只允许无已知缺口的健康零成交输出0。映射REQ-009/012、DEV-004/008子集、TEST-FEAT-02/TEST-QUALITY-01适用内容。
- **触发：** 复制既有人工三笔/重复样例，仅给第一条RAW增加 `quality_flags: [GAP_KNOWN]`；时间、数量、方向、单位、预期公式和充分warmup保持。只写新data目录，不改原fixture。
- **预期：** raw和标准化保留缺口证据，31秒截止/30秒窗口的数量与Delta为null，质量包含GAP_KNOWN。
- **实际：** raw读回保留GAP_KNOWN；标准化Parquet读回quality_flags=[]；特征Parquet读回quality_flags=[]、V=0.60、Delta=0.40；collect摘要status=PASS。合同断言退出1，完整输出 `round1_review/gap-flag-reproduction.log/exit`。
- **影响范围：** 明确携带该原始质量标记、落在窗口且没有额外GAP控制事件抵消遗漏的离线输入会被当作健康流量。**推断：** 实时/重放共用该内核，因此两者摘要一致不能排除同一个语义错误；未据此声称有真实行情/研究污染事故。其他质量标记受影响程度未继续扩展验证，不列成已证实的多个缺陷。
- **最小建议（不在本任务执行）：** 另立修复任务，按既有质量枚举规则保留和校验上游标记，并把应使流量无效的原因传至相应窗口。仅在标准化表保留标记不够；不要将SOURCE_SAMPLED等提示标记一概改成缺口。

复现，在被审checkout、已安装的锁定环境中：

```sh
.venv/bin/python /Users/aries/Dve/btc_microstructure/reports/round1/round1_review/reproduce_gap_flag.py
```

这条命令会新建synthetic数据目录并输出实际/预期，当前代码预期退出1以展示反例。脚本对socket连接/DNS设禁网保护。它属于独立审查证据，**没有放入或修改原默认测试套件**。native lane另有两笔健康/带标记对照脚本及实际输出 `code-probes/`（打印观察结果退出0，不冒充合同通过）；fresh CLI原始单断言退出1回执 `fresh-cli-probe-receipt.json`。三者是同一个发现的核验，不累计成三项缺陷。

## 十二个审查重点及覆盖限制

下列test node名称均指本次local-checks对应JUnit实际执行；静态检查深度受发现后的停止条件限制。表中的PASS仅指相应固定场景，不是整家族无缺陷保证。

| # | 审查重点、代码位置 | 本次证据/结论与边界 |
|---|---|---|
| 1 | 方向、聚合、单位、精度；offline_core.py:155-240 | 代码检查；test_normalizes_m_false_as_buy_and_m_true_as_sell、test_preserves_decimal_q_and_nq_without_double_counting、test_high_precision_decimal_uses_exact_decimal_not_float、test_unknown_unit_keeps_base_values_null已执行PASS；人工BTC证据不升级LIVE |
| 2 | 跨会话/恢复去重、冲突时点；offline_core.py:132-150 | test_duplicate_same_economic_key_across_sessions_is_not_counted_twice、test_restarted_core_from_store_deduplicates_duplicate_id_across_restart、test_future_conflict_does_not_rewrite_prior_publication_but_invalidates_overlap PASS；经济键不含session，冲突原发布时间不倒改；未扩展真实source_epoch |
| 3 | 接收时间、边界、高水位、排序、warmup、午夜；offline_core.py:90-105/243-252，offline.py:418-475 | test_window_includes_left_boundary_and_excludes_right_boundary、test_cutoff_high_water_excludes_later_same_timestamp_records、test_windows_remain_valid_across_utc_midnight、test_source_time_precision_is_converted_without_inventing_extra_precision、test_runtime_highwater_maps_to_original_sequence_after_inserted_controls等PASS；非as_received启动拒绝固定测试PASS |
| 4 | 缺口、时钟、队列/存储错误与健康零 | **FAIL：R104-01**。控制GAP/warmup/健康空窗及容量故障既有测试PASS，不覆盖RAW标记传播；停止额外故障探索 |
| 5 | raw/派生分离、可信游标和重复恢复；offline_storage.py:252-319/452-620 | test_raw_is_committed_before_core_raw_crash、test_failed_raw_commit_keeps_bytes_cursor_and_uncertainty_across_reopens、test_untrusted_tail_diagnostics_are_best_effort_and_preserve_every_byte、test_failed_raw_tail_propagates_through_repeated_raw_only_parquet_builds等PASS。R103独立8项仍PASS，不重开旧发现；实际断电/满物理磁盘NOT_RUN |
| 6 | 历史发布时点；offline_core.py:106-117，offline.py:615-617 | test_feature_available_time_requires_logged_publication、test_missing_publication_keeps_pending_available_null_and_replay_unverified及CLI两次重放PASS；core无新墙钟替换历史发布时间。更复杂发布故障探测未扩展 |
| 7 | horizon/未来不改变过去特征 | test_research_horizon_changes_do_not_change_past_feature_values、test_future_horizon_change_uses_same_raw_and_same_feature_values_with_new_hash、test_future_raw_events_do_not_change_prior_high_water_feature PASS；未实现未来收益研究 |
| 8 | 默认离线、真实拒绝、私有/密钥边界；cli.py:26-39，offline.py:102-109 | synthetic-only gates与socket阻断fixture代码检查，相关拒绝测试PASS；本次实际运行无Binance。完整安全审计在阻塞后未继续，不保证所有潜在漏洞不存在 |
| 9 | 测试真实性、预期/skip/xfail/容差 | 本次原suite269+独立32全部0skip；代表性Decimal/边界/冲突断言已读，保护文件hash/diff一致；未做全历史每个断言的穷尽追溯，标PARTIAL。原review失败记录只作为历史，不据其断言当前未修复 |
| 10 | Python3.12、锁定依赖、干净环境、README | 新venv实际安装，逐项lock版本相等，十一项检查PASS；README提供的单命令offline_smoke实际执行了collect/two replays/raw-only build和Parquet数值/质量/游标/摘要核对；字面data/r1-03-demo路径的重复命令未另跑，行为等价命令见local-checks/smoke-commands.json |
| 11 | 全量配置不误启能力、布尔不放行；capabilities.py:14-24，offline.py:102-109/628-665 | 原validate-config仅校验/UNVERIFIED；首轮strict配置拒绝未支持source/timeview/伪布尔，test_offline_config_rejects_unsupported_source_timeview_and_fake_booleans及test_limits_unknown_keys_and_fake_metadata_units_are_rejected PASS。未补构造LIVE预算，额外身份攻击探测NOT_RUN |
| 12 | 文档/模拟/实测/NOT_RUN分离 | 指定旧报告和GitHub审查的证据范围核对，原REST BLOCKED/WS NOT_RUN保持；新补充用例显式SYNTHETIC。发现后停止全面文档核验，未读完的非首轮/历史材料保留为未覆盖，不据报告总结代替事实 |

## 本次实际离线执行

隔离checkout：`/Users/aries/Dve/btc_microstructure/data/r104/checkout`。新venv：Python3.12.13/macOS arm64；锁定aiohttp3.12.15、PyArrow20.0.0等全部版本一致。安装命令/日志与版本摘要在 `clean-environment/`。安装依赖和GitHub读写使用授权网络，不意味着项目测试可访问交易所。

实际命令 `bash .github/scripts/ci_checks.sh`，退出0；工作目录上述checkout，原脚本未改。每项准确argv/cwd/退出码在 `local-checks/commands.json`，日志/JUnit独立保存：

| 检查 | 退出码 | 实际结果 |
|---|---:|---|
| 默认pytest | 0 | 269 passed，0fail/error/skip |
| 独立R1-01 | 0 | 15 passed，0fail/error/skip |
| 独立R1-02 | 0 | 9 passed，0fail/error/skip |
| 独立R1-03 | 0 | 8 passed，0fail/error/skip |
| Ruff lint | 0 | PASS |
| Ruff format --check | 0 | PASS |
| Mypy | 0 | PASS |
| pip check | 0 | PASS |
| 原配置validate-config | 0 | PASS，能力仍UNVERIFIED |
| 首轮validate-offline | 0 | PASS，synthetic范围 |
| CLI/Parquet smoke | 0 | PASS，4raw/3unique，70feature行，V=.60/Delta=.40/TI=2/3，两次重放和raw-only build摘要一致 |

本机Linux网络命名空间检查为NOT_RUN，日志明确注明；socket禁网fixture/纯离线入口正常执行。后述Actions隔离是真实Linux-hosted证据，不能冒充本机。

补充反例：fresh CLI合同断言1失败；协调端到端合同断言1失败；native观察脚本退出0打印反例。这不改变或伪装为原269测试失败。未新增skip/xfail。审查准备中两次报告路径错误FileNotFoundError已记录setup-01/02.txt，之后改为绝对路径；是协调归档错误，不是被审代码缺陷。CLI启动曾出现模型列表刷新超时/忽略旧配置项提示，随后新会话实际完成审查；未修改本机配置或网络。

## 历史读回与新报告PR的Actions

- PR #1最终复核5483287314针对ce8cd8a；PR #5原发现5483466192针对03ce9c7；修复复核5483555449针对9a363dc。原文与最新评论快照在github/。旧结论仅证明当时范围，R103-A/B/C已解决；本次不据旧失败重开它们。
- main push [38185147921](https://github.com/shukehi/btc_microstructure/actions/runs/38185147921)的event=push、head=`0b65be4933e9c36abedf784e608b832c56cee131`、SUCCESS。实际下载产物核对四份JUnit269/15/9/8均0fail/error/skip、十一项退出0；这是**历史运行读回**，不是本次重跑。证据 `github/main-push-run.json`、`main-push-artifacts.json`及`main-push-readback/`。
- 本报告PR精确head及新Actions在创建后记录；新PR的绿色CI只证明原十一项检查运行，**不会消除R104-01**，补充反例不在原suite内，未改工作流使其变绿。
- workflow_dispatch未触发，**NOT_RUN**；没有为了补记录改变CI。只提交指定报告/小型证据，不提交venv/运行数据/整工程ZIP/秘密或用户AGENTS改动。

## 后续依赖顺序（仅建议，不执行）

1. 独立复核R104-01及本报告的部分覆盖边界；确认后另立修复Issue，补充RAW质量传播回归，再完成被停止的审查项。
2. 离线合同关闭后，另行授权并补齐真实接口/单位/持续预算证据；权限或地区拒绝不绕过。
3. 上述门槛满足后才另行安排有界真实采集→保存→重放；随后独立设计24h工程验收。
4. 足够合格数据与研究设计齐备后再讨论多期限描述/研究；不推导买卖信号、收益承诺或交易能力。

本次仅提交报告PR，正文使用 **Refs #6**；不自动关闭Issue、不合并、不自行修复或执行这些后续建议。报告交付后停止，等待独立复核。
