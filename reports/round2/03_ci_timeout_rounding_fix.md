# Issue #19 · R203-CI-01 本地修复与验证

状态：**本地修复与验证 PASS**。原规格 v0.1；基线 `108da11ed08896f1d47720a1aa641eee826d2556`。本报告记录提交前验证；本任务提交 SHA、PR 与对应 hosted Actions 结果记录在 PR 正文，避免自引用提交哈希。

已按任务在独立 worktree 启动新 Codex CLI 会话 `01a13301-85e8-7ac2-85bf-e66e0d6b3acf` 完成本地实现与验证，随后由主会话检查 diff、证据和原工作区保护结果。完整会话日志留在忽略目录，不上传个人配置或会话全文。

已读取已提交 `AGENTS.md`、`CODEX_START_HERE.md`、Issue #19 全文（无评论）、PR #18 Review 5497928149、PR #15 Review 5488515997，以及相关数据契约、单调时钟/连接约束、测试规范、来源登记、R1-02 任务卡、原超时源码和测试。`gh issue view` / 两次 `gh pr view` 因 GitHub API 连接错误退出 1，随后使用已连接 GitHub 只读接口取得全文及审查记录；未更改网络设置。

- [Issue #19](https://github.com/shukehi/btc_microstructure/issues/19)：唯一实现范围。
- [PR #18 审查](https://github.com/shukehi/btc_microstructure/pull/18#pullrequestreview-5497928149)：确认参数反算缺陷，历史两次 hosted CI 仅作为背景，本次未重新下载其产物。
- [PR #15 审查](https://github.com/shukehi/btc_microstructure/pull/15#pullrequestreview-5488515997)：保留 REST/连接/空闲职责、兼容路径、全局截止、取消、清理及安全约束。

## 修改与确定性证据

| 文件 | 用途 |
|---|---|
| `src/btc_microstructure/probe.py` | 仅修改 `rest_effective`：未截断时返回配置值本身；截断时将剩余预算夹在 `[0, configured_rest]` |
| `tests/unit/test_probe_timeout_rounding.py` | 新增默认发现的 9 个确定性用例，无真实请求或等待 |
| 本报告、`ci_timeout_rounding_fix/` | 小型前后日志、退出码、环境和逐项验证记录 |

绝对 `work_deadline`、`rest_deadline` 及 `timeout_at` 使用方式未修改；REST 仍从请求开始计时，全局仍预留 1 秒清理。WS、外部取消、清理、安全限制、默认请求形状及全部原测试断言保持。没有新增依赖、通用计时框架或运行模块。

先新增测试，在旧源码执行后才修改实现。`before.log` 原始失败日志与 `before.exit` 保留：49.9 起点的 15、20 秒分别得到 `15.000000000000007`、`20.000000000000007`，**2 failed / 7 passed，退出 1**。不是纯算术脚本，也不是重跑原偶发测试碰到失败。

新增测试固定本进程 event loop 的计时读数，实际调用 `run_probe → AioTransport.metadata → fake ClientSession.get`，核对真实构造的 `aiohttp.ClientTimeout.total`、连接/空闲参数、固定 URL、禁重定向和关闭；核对实际传入 `timeout_at` 的绝对全局/REST 截止。覆盖 49.9 的 15/20 秒、0.1 和正常精确起点、全局更短、剩余预算已耗尽、截止相等，以及省略参数时未截断/截断两种兼容路径。

预算耗尽用例以立即返回的人工 HTTP 451 观察零预算参数与原绝对截止，**不证明真实 socket 的零超时语义或硬实时精度**；实际全局过期、取消及清理继续由原超时/中断回归覆盖。所有案例均为 SYNTHETIC，无真实响应样本或单位观测。

## 环境与实际执行

工作目录均为本 worktree 根。Darwin arm64，Python **3.12.13**；复用 `../r104-followup/.venv`，未安装依赖。所有 `requirements-dev.lock` 版本均与环境逐项一致（退出 0）；aiohttp 3.12.15、pydantic 2.12.3、PyYAML 6.0.3、pyarrow 20.0.0、pytest 8.4.2、ruff 0.14.0、mypy 1.18.2。`PYTHONPATH=src`，实际导入路径已确认属于本 worktree，详见 `ci_timeout_rounding_fix/environment.json`。

| 实际命令 | 退出码 | 通过 / 失败 / error / 跳过 | 证据（相对本报告目录） |
|---|---:|---|---|
| `PYTHONPATH=src ../r104-followup/.venv/bin/python -m pytest -q tests/unit/test_probe_timeout_rounding.py`（旧源码） | 1 | 7 / 2 / 0 / 0 | `ci_timeout_rounding_fix/before.log`、`before.exit` |
| `PYTHONPATH=src ../r104-followup/.venv/bin/python -m pytest -q tests/unit/test_probe_timeout_rounding.py tests/unit/test_probe_timeouts.py tests/unit/test_probe_interruptions.py`（修复后） | 0 | 41 / 0 / 0 / 0 | `ci_timeout_rounding_fix/after-targeted.log`、`after-targeted.exit` |
| `PYTHONPATH=src bash .github/scripts/ci_checks.sh` | 0 | 十三项均退出 0，明细如下 | `ci_timeout_rounding_fix/local-ci.log`、`local-ci.exit`、`results.tsv`、`verification.json` |
| `git diff --check` | 0 | 无空白错误，非测试计数 | 本次实际执行 |

CI 脚本所需 `.venv` 是本次创建的临时 symlink，指向复用环境；脚本完成后核对目标并仅删除该 symlink，原环境保留。十三项实际命令逐一保存在 `verification.json`；脚本与工作流原文未改。

| 检查 | 结果 | 退出码 |
|---|---|---:|
| pytest | 326 passed（原 317 + 新 9），0 failed/error/skipped | 0 |
| pytest-independent-r1-01 | 15 passed，0 failed/error/skipped | 0 |
| pytest-independent-r1-02 | 9 passed，0 failed/error/skipped | 0 |
| pytest-independent-r1-03 | 8 passed，0 failed/error/skipped | 0 |
| pytest-independent-r1-04 | 4 passed，0 failed/error/skipped | 0 |
| r104-original-parquet | 原缺口标志回归 PASS | 0 |
| ruff-check | PASS | 0 |
| ruff-format | PASS | 0 |
| mypy | PASS | 0 |
| pip-check | PASS | 0 |
| validate-config | PASS | 0 |
| validate-offline | PASS | 0 |
| offline-e2e | Decimal、重复原文、秒网格、Parquet、两次 replay 与 raw-only build PASS | 0 |

五份 JUnit 实际解析并核对数量；原 CLI 精确 `15.0` 断言及新 9 例确认出现在默认套件。各套件分别记录，不重复累计定向与默认测试数量。未筛查的大型生成物、原 JUnit 及原始 CI 总日志保留在被忽略的 `ci-results/`，不列入共享交付；报告内 CI 日志仅替换本机绝对路径与 PyArrow 编译路径占位符，未删除结果、警告或失败信息。原失败日志未改写。

## 需求映射与边界

| requirement / dev subset | test / 验收 | 证据 | 状态 |
|---|---|---|---|
| Issue #19 / R203-CI-01；DEV-002 的有界 probe 子集 | Issue #19 第 1–5 项：参数保真、上限、兼容、原断言及十三项检查 | 前后日志、`verification.json` | PASS |
| REQ-016 / DEV-002 | TEST-OPS-01 适用子集：公共只读、安全限制不变 | 原源码 diff、现有测试及独立 R1-02 检查 | PASS（子集） |
| REQ-017 / DEV-002 | TEST-OPS-02 适用子集：超时、拒绝、取消、清理及禁止无限重试 | 41 项定向与原默认/独立套件 | PASS（子集） |
| REQ-018 / DEV-002 | TEST-DOC-01 的本次缺陷来源与变更追踪 | Issue/审查链接、本报告 | PASS（记录子集） |

Git 核对：暂存区为空；既有受跟踪文件仅 `probe.py` 有差异。原测试、规格、配置、锁定依赖、工作流、AGENTS、单位/预算文档及历史文件均无修改。本地验证时暂存区为空；后续仅提交本任务允许文件。未解决审查线程、合并或关闭 Issue。

pip check 提示本地 pip 缓存不可写并禁用缓存；本地 Parquet/离线 smoke 输出 PyArrow `sysctlbyname` 沙箱权限警告；相关命令均退出 0，数据校验通过，未关闭沙箱或修改环境以消除警告。Linux 网络命名空间验证 **NOT_RUN**（macOS），hosted Actions **NOT_RUN**；没有用本地通过代替远端 CI。

真实 Binance、probe 真实模式、A1/A2、单位绑定、持续预算与采集均 **NOT_RUN**。单位/预算及历史现场状态不升级。本报告的 hosted Actions 尚未执行；创建任务 PR 后按精确 head 核对远端结果并补充到 PR 正文。不启动下一任务。

本地复现可使用上表定向命令；十三项脚本依赖本次所述临时 `.venv` 接线，已完成并清理。全部命令与计数详见 `ci_timeout_rounding_fix/verification.json`。
