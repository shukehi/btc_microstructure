# 发给 Codex：R1-01 修复任务

```text
请先读取 AGENTS.md、tasks/01_foundation.md、原开发规格，以及我提供的
R1_01_Independent_Review.md 和 checks/test_review_regressions.py。

本次只进行 R1-01 / DEV-001 修复，不启动 tasks/02_protocol_probe.md，
不连接币安，不实现采集器或研究模块。

先核对本地源代码与复核指出的位置；若版本不同，记录实际差异。
独立复核使用 Linux/Python 3.13.5，原交付使用 macOS/Python 3.12.13。
请先在本项目既有 Python 3.12 虚拟环境复现，不为制造一致而修改锁定依赖。

修复 A：恢复原交接包 .gitignore 中遗漏的秘密文件和本机日志规则。
至少覆盖 .env、.env.*、*.pem、*.key、/codex-local-logs/，
保留 !.env.example，并保留合理的现有规则。
通过临时 Git 仓库测试，不能只断言字符串出现在文件里。
不要自动初始化用户仓库、暂存所有文件或推送。

修复 B：原规格明确要求、目前却能设成 false 并通过 CLI 的字段：
- storage.source_payload_preserved
- quality.unknown_as_null
- quality.known_gap_invalidates_flow_window
- research.report_all_horizons
- research.report_common_event_sample
- states.freeze_thresholds_in_validation_and_test
- research.split.purge_by_actual_label_intervals

在适用的原样例条件下，对这些违反规格的设置明确拒绝，
返回非零退出码，指出完整字段路径与原因。
继续保持严格布尔类型，不能意外接受整数 0/1 或字符串。
逐项依据原规格区分硬约束和可修改默认值，不把全部默认开关一律锁死。
不要删除字段、改原样例、改规范、放宽测试或用 skip/xfail 消除失败。

审查报告 C1–C5 为非阻塞事项，不借此扩展功能；必要时登记后续决策。

验证：
- 保留原 48 项测试，并新增这些回归案例；
- 在项目 Python 3.12 环境执行完整测试和独立审查测试；
- 执行有效/无效 validate-config、ruff、mypy；
- 原样例仍通过，配置摘要应保持一致；
- 检查原规格与固定样例未修改。

保存修复前失败证据；新增 reports/round1/01_foundation_fix.md，
记录修改文件、环境、真实命令与退出码、通过/失败/跳过数量、
未执行项和复现方法，不把旧报告改成从未失败。

生成 foundation_review_fixed.zip 及校验和，
排除虚拟环境、缓存、Git 元数据、秘密及大型数据。
交付后停止，不自动开始下一任务。
```
