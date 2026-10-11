# R1-01 / DEV-001 修复后独立复核

## 结论

**APPROVED_FOR_R1_02：本轮 A/B 修复通过复核，可关闭 R1-01 配置基础任务并进入原任务卡 R1-02。**

此结论仅覆盖工程基础、配置校验和此次修复。它不表示完整 M0、实际行情接口、采集器、研究器或交易策略通过验收。本次没有连接币安、使用账户或修改用户源代码。

## 1. 输入和归档校验

- 输入：`foundation_review_fixed.zip`、外部 `.sha256`、单独上传的 `01_foundation_fix.md`。
- SHA-256：`6928ac7b8cdf971a05acaa12da14b77398449082378750e099274b0e473d4530`，与外部校验文件相同。
- ZIP：264,200 字节、151 个文件；CRC、重复路径、路径穿越和符号链接检查通过。
- `fixed_package_manifest.json` 中 150 个 payload 文件的大小和 SHA-256 逐项匹配；清单自身为第 151 个文件。
- 单独上传的修复报告与包内报告逐字节相同。
- 与原 `foundation_review.zip` 对照：已有文件仅 `.gitignore`、`src/btc_microstructure/config_models.py` 发生变化；没有删除原文件，新增 73 个文件（回归测试、审查与修复材料）。
- 原 48 项测试对应的文件未修改；原规格、配置样例与人工固定样例未修改。
- 原独立审查包中的 21 个文件在移除 ZIP 的外层目录名后，与新包中 `review/R1_01/` 的对应文件逐字节一致。
- 原 `preserved.sha256` 的 23 项、新 `fixed-implementation.sha256` 的 12 项全部匹配。
- 用户保护清单声明 47 项：包内可检验 45 项均一致；旧 `foundation_review.zip` 未重复嵌入，但与本会话先前上传的外部旧包摘要相符；`.DS_Store` 按打包策略排除，本次无法独立核验其内容。这不是受保护源码缺失。
- 执行完成后再次校验 150 个 payload 文件，全部未变。

证据：`evidence/archive_comparison.json`、`manifest_verification.json`、`integrity_details.json`、`original_review_preserved.json`、`post_execution_integrity.json`。

## 2. 执行环境及证据边界

| 项目 | 用户修复报告及随包日志 | 本次独立执行 |
|---|---|---|
| 平台 | macOS arm64 | Linux x86_64 |
| Python | 3.12.13 | 3.13.5 |
| Pydantic | 2.12.3 | 2.13.5 |
| pydantic-core | 2.41.4 | 2.46.5 |
| PyYAML | 6.0.3 | 6.0.3 |
| pytest | 8.4.2 | 9.0.2 |

本环境只有 Python 3.13，未安装 ruff 和 mypy。本次未下载解释器、未安装依赖、未重建用户的 Python 3.12 精确锁定环境，也未在本环境重跑静态检查。用户原机记录的 17 项依赖与其锁文件一致，这是对提交材料的一致性检查，不是本机安装结果。

独立测试使用 `PYTHONPATH=<解压项目>/src`，禁止生成字节码文件，并关闭无关的全局 pytest 自动插件加载。所有命令和退出码写入单独的审查日志，不覆盖用户的 `before/after` 证据。未运行可能覆盖用户证据的原交付执行器。

## 3. 实际重跑结果

| 检查 | 本次实际结果 | 证据 |
|---|---|---|
| 原 48 项测试单独运行 | 48 通过，0 失败，0 跳过 | `original_48.log/xml` |
| 默认套件 | 106 通过，0 失败，0 跳过（包含原 48 项及新增 58 项） | `default_suite.log/xml` |
| 原独立审查套件 | 15 通过，0 失败，0 跳过 | `independent_15.log/xml` |
| 原合法样例 CLI | 退出 0、CONFIG_VALID；能力仍均为 UNVERIFIED | `cli_valid.log` |
| 七个违规配置实际 CLI | 七项均退出 1，均包含完整字段路径和拒绝原因 | `cli_*.log` |
| 原配置摘要 | 与前版相同 | `cli_valid.log` |
| 修复前对照 | 原归档的单独重建副本上，同一独立套件 12 失败、3 通过 | `before_control_rerun.log/xml/json` |

原 48 项属于默认 106 项的子集，不能重复累加。独立 15 项另行执行。无效配置预期退出 1 是正确拒绝，不是验收失败。

修复前对照是本轮新执行：在另一目录使用此前上传的原版本，证明同一测试确实能区分修复前后；它不冒充历史日志，也没有回退或覆盖修复版本。

原样例配置摘要：

```text
b037004b544ca834855821374bd65520a535b509c0b952d9c3f37a97a30d3b43
```

## 4. 原阻塞问题关闭情况

### A：秘密文件忽略规则 — CLOSED

`.gitignore` 已恢复 `.env`、`.env.*`、`*.pem`、`*.key`、`/codex-local-logs/` 等原规则，保留 `!.env.example` 及已有目录排除项。

回归测试在临时独立 Git 仓库中实际运行 `git check-ignore`，同时验证敏感类别被忽略、人工样例可见，并非只断言规则文本存在。原 5 个失败案例全部通过。本次不初始化用户项目，不读取或创建真实密钥，不推断曾发生泄密。

### B：七项配置硬约束 — CLOSED

六项字段通过严格布尔类型加语义校验强制为 true：原 payload 保留、未知值处理、缺口失效、全部期限披露、共同样本披露、真实标签区间 purge。

状态阈值冻结采用条件约束：当 `states.enabled=true` 时必须为 true；状态模块禁用时允许 false。七项字段均继续拒绝整数 0/1 和布尔字符串，CLI 拒绝信息包含字段路径。

修改没有锁死所有工程默认开关；研究期限、指标回看窗口和特征频率依然独立可配置。也没有把配置有效改成真实来源能力已核验。

代码位置：`config_models.py:53–64`（严格 true 类型）、`:199–202`（原 payload）、`:250–251`（质量）、`:276–280`（状态条件约束）、`:289`（purge）、`:316–317`（期限披露）。

## 5. 剩余事项不是本轮新增阻塞

**全仓 lint 并未通过。**已核对提交的完整日志：30 个诊断全部来自此前保存的 `review/R1_01/checks/explore_edges.py` 和 `explore_edges_portable.py`，是原审查探索脚本的风格或未使用导入问题，不是本次产品源码。提交记录中的 `ruff check src tests`、格式检查、mypy 和依赖检查通过；这些是已检查的用户日志，不是本次重跑的静态检查结果。

本次不要求篡改存档证据来获得全仓绿色。后续可以明确持续检查的源码范围；若要规范化探索脚本，应另建工具副本并保留原证据。

原报告 C1–C5 继续作为后续事项：Git 基线、随研究期限调整保护间隔、运行配置对象可变性、来源运行核验、配置相对路径合同。没有将这些尚未实现的运行模块风险升级为本轮新缺陷，也没有宣称这些事项已解决。

本次结论不包括第二个全新虚拟环境安装、实际币安协议、连续运行或任何收益检验。

## 6. 下一步

使用既有项目和 `tasks/02_protocol_probe.md`，不要重建项目或覆盖现有源码。

R1-02 只覆盖首轮 BTCUSDT 永续元数据及聚合成交来源，区分官方资料、synthetic 测试与实际响应三类证据。网络受限时保留 BLOCKED/NOT_RUN，不绕过限制或索取交易密钥。不能一次开启现货、L2、OI、资金和强平，也不能把这个子集写成全部 M0 已通过。

可使用本包 `R1_02_START_PROMPT.md` 作为下一项任务文本。本次复核没有自动开始 R1-02。

## 7. 在用户既有环境重新运行的命令

以下命令是项目已经实现的入口，从项目根目录运行；不重写既有历史证据：

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q review/R1_01/checks/test_review_regressions.py
.venv/bin/python -m btc_microstructure validate-config config/research.example.yaml
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src
```

审查包中的 `checks/run_recheck.py` 和 `check_integrity.py` 记录审查方的执行方式；它们假设指定位置有解压项目和原输入归档，不是新增产品命令。日常使用以上现有项目命令即可，无须为了进入下一阶段再次复制或运行审查工具。
