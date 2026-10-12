# 新上下文只读续查：入口 / 能力 / 文档（8/11/12）

Reviewed commit: 4962325fe3ed9a0d03ce8c3d2b42a2db898a7cbe。native code-reviewer 有界 slice；未发现新阻塞。以下为本轮代码阅读，不冒充另一次完整动态验证。

- CLI / import：pyproject.toml:12-13、__main__.py:1-3 接同一 main；cli.py:26-39 仅支持 collect --mode synthetic，:51-75 dispatch 后才导入离线模块。offline.py:53-55/112-115 拒绝真实模式；test_offline_pipeline.py::test_validate_offline_rejects_live_mode_before_any_transport。
- Probe：cli.py:83-89 固定路由，:92-123 无 --allow-network 则 NOT_RUN/2，不构造真实 transport；test_probe_transport.py::test_cli_network_requires_flag / test_probe_rejects_custom_routes_before_network。默认 tests/unit/conftest.py:15-23 阻断 socket/DNS。src 内有界调用路径搜索没有私有账户、签名、下单或 API-key 读取实现；probe.py 的 account_bytes 是字节预算，不是账户接口。不把此限定检查称作穷尽安全审计。
- 能力：capabilities.py:8-24 始终给 UNVERIFIED；cli.py:147-158 打印状态；test_config.py::test_declared_probe_does_not_verify_capabilities 直接验证布尔不构成凭据。config_models.py 严格 bool 与 test_foundation_guards.py 的独立字段/CLI 拒绝保护保留。
- Replay/build：offline.py:638-693 校验 schema/adapter/evidence/normalization/feature version；旧 delta.1 replay 拒绝，可信 raw 可到新目录 build。test_quality_pipeline.py 的 legacy/unknown 版本对照直接断言。
- README 的原规格前缀和各阶段“当时没有采集器”等是历史进度；R1-03/R104追加段明确 synthetic、当前版本、真实依赖未核验。README 关于三组测试的旧摘要不代表当前五组执行清单，以 ci_checks.sh 与本次 results.tsv 为准。原报告的 R104 FAIL 只适用于旧 SHA；04_quality_propagation_fix.md 记录后来修复，不改旧报告。
- 限制：现场、真实单位/预算、24h、完整渗透或全历史断言审计均未进行。有限离线审查可完成与整个系统/现场仍 PARTIAL 是两个状态。
