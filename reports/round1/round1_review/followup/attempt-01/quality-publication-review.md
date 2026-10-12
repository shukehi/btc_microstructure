# 新上下文只读续查：质量 / 发布 / 断言（4/6/9）

Reviewed commit: 4962325fe3ed9a0d03ce8c3d2b42a2db898a7cbe。native code-reviewer 结论：有限范围未发现新阻塞。

- offline_core.py:47 incoming_quality 校验枚举；:199 起 normalize 保留上游质量、另存收据；:356-366 仅在高水位与观察时间范围内合并质量，FLOW_INVALID_FLAGS 决定 null。SOURCE_SAMPLED 保留限制，不一概无效。
- 关键独立断言：test_quality_propagation.py::test_healthy_duplicate_trade_keeps_manual_flow_expected_values（.60/.40、精确 Decimal）；::test_quality_left_boundary_is_included_and_right_boundary_is_excluded；::test_later_duplicate_quality_above_high_water_does_not_invalidate_snapshot；::test_duplicate_quality_at_cutoff_uses_high_water_for_existing_economic_event[False/True]；::test_future_quality_counterfactual_keeps_past_features_identical。
- test_quality_pipeline.py::test_gap_known_round_trips_through_parquet_and_degrades_data_health 对实际 null/data_valid 断言；::test_collect_replay_twice_and_raw_only_build_keep_gap_nulls_and_raw_bytes 将固定 null 预期与两次重放/raw-only build 一同验证，不只比较两个相同算法的摘要。
- offline_core.py:135-150 重建截止前缀；:151-164 发布事件要求 pending cutoff、available==事件接收时点、>=cutoff/dependency_available_ns。没有控制日志不能证明真实历史发布时间；缺日志保持 UNVERIFIED。offline.py:638/682 的旧版本 replay/build 门槛保留。
- 原默认测试相对 0b65be4 无修改/删除，只新增25项质量测试；独立4案例和原 Parquet 脚本显式在 CI 执行。新质量测试未发现 skip/xfail 或放大容差；Float64 对照 rel=1e-10/abs=1e-12，Decimal/ID/quality/null 精确比较。

该 lane 额外运行过 focused29、R10325、原Parquet、Ruff/Mypy/compile，成功结果只存在工具输出，没有独立保存JUnit/log，因此不把这些数量加入主报告的正式验证统计。原Parquet输出仅存临时目录。本轮正式统计依据协调会话保存的全套十三项日志/JUnit与独立3项控制故障日志。附加运行没有更改源码/测试或清理目录。

附加命令（cwd=<review-checkout>，未设PYTHONPATH，使用该checkout新venv的editable安装）：
```
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/unit/test_quality_propagation.py tests/unit/test_quality_pipeline.py review/R1_04/checks/test_r104_quality_review.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python review/R1_04/original/reproduce_gap_flag.py --output-parent <temporary-output>/r104-followup-review-gap
.venv/bin/ruff check --no-cache src tests review/R1_04/checks/test_r104_quality_review.py review/R1_04/original/reproduce_gap_flag.py .github/scripts/offline_smoke.py
.venv/bin/mypy --cache-dir <temporary-cache>/r104-followup-mypy src .github/scripts/offline_smoke.py
.venv/bin/python -m py_compile src/btc_microstructure/offline_core.py src/btc_microstructure/offline.py tests/unit/test_quality_propagation.py tests/unit/test_quality_pipeline.py review/R1_04/checks/test_r104_quality_review.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/unit/test_r103_ordering.py tests/unit/test_r103_recovery.py review/R1_03/checks/test_r1_03_review.py
```
六项均退出0；只读文件大小工具误用裸python时command not found，不是项目测试失败，未修改解释器或全局设置。未运行新真实连接/系统时钟修改/物理断电或满盘。无LSP接口，用静态分析替代，不冒充LSP结果。
