本 slice 完成，**未发现第 1/2/3/5/7 项的新增阻塞**。结论限于首轮 synthetic 离线范围：实现与固定断言支持下述场景，不能据此宣布完整 R1-04 或真实运行验收通过。

被审提交为 **`4962325fe3ed9a0d03ce8c3d2b42a2db898a7cbe`**。工作树 HEAD 为 `b5c08ae…`，已用 Git diff 核对相关 `src/tests/docs/tasks/config/fixtures`、AGENTS.md 和 R1-03 独立测试与被审提交一致，退出 0。历史覆盖表是另一版本的材料，仅用于定位覆盖项，其旧 PASS 未作为本次事实。未读取记忆或既往实现聊天，未联网、委派、修改或生成报告文件。

以下“证据”指本次代码及断言阅读；“推断”指据此作出的范围判断。

1. **方向、聚合单位与 Decimal：固定场景证据充分。**

   **证据：** [offline_core.py:57](<review-checkout>/src/btc_microstructure/offline_core.py:57) 从十进制字符串构造 Decimal，拒绝超出精确表示范围；`:249–287` 严格检查 boolean、`nq≤q`、tick/step，并以精度 80 计算逐条成交额；`:303–321` 明确使用 `AGG_TRADE`、`m=true→SELL`，保留聚合覆盖限制；`:370–383` 只累计 q，数量保持 Decimal，仅 TI 转 Float64，计数为标准化聚合消息数。

   [test_offline_core.py:117](<review-checkout>/tests/unit/test_offline_core.py:117) 的本次通过节点：
   - `test_normalizes_m_false_as_buy_and_m_true_as_sell`
   - `test_preserves_decimal_q_and_nq_without_double_counting`
   - `test_unknown_unit_keeps_base_values_null`
   - `test_mismatched_row_evidence_does_not_use_synthetic_metadata`
   - `test_high_precision_decimal_uses_exact_decimal_not_float`
   - `test_normal_quantity_larger_than_total_quantity_is_quarantined`
   - `test_off_tick_or_off_step_trade_is_quarantined`（两个参数实例）

   [test_offline_pipeline.py:70](<review-checkout>/tests/unit/test_offline_pipeline.py:70) 的 `test_cli_collect_and_replay_round_trip_with_parquet_outputs` 固定断言 B=.50、S=.10、V=.60、Delta=.40、TI=2/3、N=3，并比较重放结果；本 slice 仅阅读，未重跑。

   **限制：** BTC 单位成立条件明确限定为 SYNTHETIC 元数据和行证据，不能升级真实单位核验。映射 REQ-004/009、DEV-004/008 子集、TEST-TRADE-01、TEST-FEAT-01。

2. **跨会话去重、冲突与知识时点：固定场景证据充分。**

   **证据：** [offline_core.py:195](<review-checkout>/src/btc_microstructure/offline_core.py:195) 的经济键不含 session；`:208–229` 比较价格、数量、方向、nq、成交 ID 范围和 T，同键冲突进入隔离而不覆盖首次成交。`:135–150` 在高水位落后时重建输入前缀；`:344–348` 根据冲突观测时间和原成交时间判定受影响窗口。已发布快照不重新计算。

   [test_offline_core.py:184](<review-checkout>/tests/unit/test_offline_core.py:184) 的本次通过节点：
   - `test_duplicate_same_economic_key_across_sessions_is_not_counted_twice`
   - `test_conflicting_same_economic_key_across_sessions_is_quarantined`
   - `test_future_conflict_does_not_rewrite_prior_publication_but_invalidates_overlap`
   - `test_future_conflict_above_high_water_does_not_invalidate_snapshot_quality`

   [test_offline_scenarios.py:224](<review-checkout>/tests/unit/test_offline_scenarios.py:224) 的 `test_restarted_core_from_store_deduplicates_duplicate_id_across_restart` 从持久化 raw 重建 Core，再跨 session 重发，断言仅一条标准化成交；本次仅阅读。

   **限制：** 当前键没有实现已核验的 source_epoch 扩展；首轮未覆盖真实 ID 重置、口径断裂或 WS/REST 跨来源合并，不能把这些能力视为已验收。映射 REQ-004/010、TEST-TRADE-02/03、TEST-REPLAY-01 子集。

3. **接收时间、边界、高水位、warmup 与跨日：内核场景证据充分，跨分区证据有限。**

   **证据：** [offline_core.py:325](<review-checkout>/src/btc_microstructure/offline_core.py:325) 明确以 `ts_receive_ns` 和 `ingest_sequence≤high` 选择 `[t−W,t)`，没有用 E/T 选窗；warmup 按会话起点与窗口左界判断，缺口按区间重叠判断。[offline.py:428](<review-checkout>/src/btc_microstructure/offline.py:428) 至 `:484` 保留显式序号顺序，生成截止时记录运行高水位及原输入序号映射。

   [test_offline_core.py:229](<review-checkout>/tests/unit/test_offline_core.py:229) 的本次通过节点：
   - `test_window_includes_left_boundary_and_excludes_right_boundary`
   - `test_cutoff_high_water_excludes_later_same_timestamp_records`
   - `test_windows_remain_valid_across_utc_midnight`
   - `test_warmup_window_outputs_null_values`
   - `test_complete_healthy_empty_window_outputs_zero_volume_and_null_imbalance`
   - `test_known_gap_keeps_reconnected_window_unavailable_until_gap_ages_out`
   - `test_source_time_precision_is_converted_without_inventing_extra_precision`
   - `test_ms_and_us_time_units_can_represent_the_same_event_time`

   [test_r103_ordering.py:116](<review-checkout>/tests/unit/test_r103_ordering.py:116) 中以下节点已阅读：
   `test_schedule_preserves_declared_order_when_fixture_array_is_permuted`、
   `test_generated_cutoff_does_not_swap_explicit_session_boundary_events`、
   `test_cutoff_uses_left_closed_right_open_window_and_traceable_highwater`、
   `test_runtime_highwater_maps_to_original_sequence_after_inserted_controls`、
   `test_reversed_session_boundary_serialization_keeps_pre_boundary_snapshot`。

   **限制：** 午夜测试证明内存滚动窗口连续；当前 Store 使用平坦 raw segment，未证明规范日期分区下前一日读取的端到端行为。晚到数据不倒改快照有代码及固定场景支持，但真实补回修订视图不在首轮。映射 REQ-003/012、TEST-TIME-01/02、TEST-FEAT-02 及任务卡补充边界测试。

5. **可信存储游标、故障尾部与重复恢复：注入故障的断言覆盖较完整。**

   **证据：** [offline.py:526](<review-checkout>/src/btc_microstructure/offline.py:526) 先 append/flush，再交给 Core；[offline_storage.py:252](<review-checkout>/src/btc_microstructure/offline_storage.py:252) 完成 raw 校验、提交和 manifest 写入后才推进游标。`:452–547` 验证哈希、行数、递增序号及游标链，首次不可信 raw 后停止信任后续提交；`:561–620` 隔离尾部并从审计或隔离 raw 恢复不确定性；`:674–700` 对坏尾部仅作尽力诊断。派生表写入 `tables/`，与 `raw/` 分离。

   已阅读的关键节点：
   - [test_offline_scenarios.py:152](<review-checkout>/tests/unit/test_offline_scenarios.py:152)：`test_raw_is_committed_before_core_raw_crash`
   - [test_offline_storage.py:49](<review-checkout>/tests/unit/test_offline_storage.py:49)：`test_flush_commits_raw_manifest_and_advances_cursor_after_verification`、`test_recovery_stops_at_first_untrusted_raw_commit`、`test_commit_error_blocks_in_process_retries_even_after_io_recovers`
   - [test_r103_recovery.py:32](<review-checkout>/tests/unit/test_r103_recovery.py:32)：`test_failed_raw_commit_keeps_bytes_cursor_and_uncertainty_across_reopens`（fsync/rename/manifest）、`test_quarantined_raw_is_recovery_evidence_when_failure_audit_cannot_be_written`、`test_untrusted_tail_diagnostics_are_best_effort_and_preserve_every_byte`、`test_failed_raw_tail_propagates_through_repeated_raw_only_parquet_builds`

   **推断：** 固定断言能够区分可信前缀、未提交尾部和重复重开后的不确定性，不把可重算前缀冒充完整输入。**限制：** 本 slice 未执行写盘节点；异常断电、真实满盘和目标文件系统耐久性 NOT_RUN，不作 exactly-once 保证。映射 REQ-002/010/017、DEV-003/006、TEST-STORE-01、TEST-REPLAY-01、TEST-OPS-03 子集。

7. **horizon 与未来反事实保护：支持首轮参数隔离，完整反事实覆盖仍有限。**

   **证据：** Core 只以 `window_seconds` 构造窗口；[offline.py:153](<review-checkout>/src/btc_microstructure/offline.py:153) 将 horizon 纳入配置摘要，因此改 horizon 可以改变 config_hash，但不改变计算公式。

   本次通过：
   - [test_offline_core.py:448](<review-checkout>/tests/unit/test_offline_core.py:448)：`test_future_raw_events_do_not_change_prior_high_water_feature`
   - 同文件 `:460`：`test_research_horizon_changes_do_not_change_past_feature_values`
   - 同文件 `:484/504`：上述未来冲突及高水位保护节点。

   已阅读：
   - [test_offline_scenarios.py:317](<review-checkout>/tests/unit/test_offline_scenarios.py:317)：`test_future_horizon_change_uses_same_raw_and_same_feature_values_with_new_hash`
   - [test_quality_propagation.py:317](<review-checkout>/tests/unit/test_quality_propagation.py:317)：`test_future_quality_counterfactual_keeps_past_features_identical`，两个独立运行改变未来质量收据，过去完整特征行相等。这里只核对时间反事实断言，未展开质量审查 slice。

   **限制：** horizon 内核测试比较部分数值字段，端到端测试比较 V/Delta、raw kind/payload 和 hash；未来保护测试覆盖追加成交、冲突及未来质量变化，尚非“修改全部未来输入后，端到端比较全部过去输出”的完整证明。收益标签、研究拼接和泄漏验收未实现，不纳入本次。映射 REQ-001/011、TEST-CFG-01 的参数隔离子集及首轮反事实补充要求。

本次实际执行使用 Python **3.12.13**。纯内存选择测试最终 **22 passed、8 deselected、0 failed、0 skipped，退出 0**；8 项是选择范围外节点，不是 skip。命令为：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -B -m pytest -s -p no:cacheprovider -q tests/unit/test_offline_core.py -k 'normalizes_m_false or preserves_decimal or unknown_unit or mismatched_row_evidence or duplicate_same_economic or conflicting_same_economic or window_includes or cutoff_high_water or windows_remain or warmup_window or complete_healthy or known_gap or source_time_precision or ms_and_us or high_precision_decimal or normal_quantity_larger or off_tick_or_off_step or future_raw or research_horizon or future_conflict'
```

首次同命令未带 `-s`，pytest 临时文件捕获因只读环境失败，退出 1、尚未执行测试；关闭捕获后成功。完整锁定检查及存储/Parquet 重跑结果由协调会话独立提供，本答复不代报。真实协议核验、有界实时演练和完整 24 小时验收均 **NOT_RUN**。最终出现的未跟踪 `reports/round1/round1_review/followup/` 保持原状，本 slice 未写入或清理。