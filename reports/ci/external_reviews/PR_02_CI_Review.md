# PR #2：第一版 GitHub Actions CI 审查记录

## 结论与范围

结论：可合并第一版 CI。未发现本次 CI 范围内的合并阻塞问题。
这是审查建议，不是向 GitHub 提交的正式 Approval；本次未合并 PR、未提交代码、未改分支保护、未触发新的工作流或 Binance 请求。

- 仓库：`shukehi/btc_microstructure`
- PR：https://github.com/shukehi/btc_microstructure/pull/2
- 审查 head SHA：`8454c533f1abe2f68628392c6c85895ac3b4941c`
- 审查时 base SHA：`6b73205424d4f49eb41a29c327bc75ce3f55b2b2`
- 审查时 PR 状态：OPEN、未合并。
- 主要文件：`.github/workflows/ci.yml`、`.github/scripts/ci_checks.sh`、`.github/CI.md`、`reports/ci/`。

## 核验方式

读取 PR 元数据与差异、工作流及检查脚本、pytest 配置；读取 GitHub API 的实际 run/job 记录和最终 job 原始日志；下载最终运行的官方 artifact，并在本地核对 ZIP SHA-256、CRC、JUnit 和逐命令结果。

没有在审查环境中重新运行项目 pytest、ruff 或 mypy。下面的测试结果来自核对过的 GitHub-hosted 执行，不是审查方另一次执行。

## 实际 GitHub-hosted 证据

- Run：https://github.com/shukehi/btc_microstructure/actions/runs/38118334383
- Job：`114407694841`，名称 `Python 3.12 offline checks`
- 事件：`pull_request`；状态 completed；结果 success。
- 环境：Ubuntu 24.04.5 / CPython 3.12.15。
- 实际 checkout：`refs/pull/2/merge`，SHA `0b5d648aead59f3308c161d16061cabd4e067173`。
- 该临时合并提交是 head 与当时 main 的合并测试结果，不表示 PR 已正式合并。
- Artifact：`ci-logs-38118334383-1`，ID `11693832514`。
- Artifact SHA-256：`4c03bc3f8ca9f07c9ebe7b3a2d963b7cf4865076bb50e1a01ffd2f5795e6253f`，与 GitHub 返回摘要一致。
- CRC 检查通过；12 个日志/结果文件。

| 检查 | 核对结果 |
|---|---|
| pytest | JUnit：166 tests，0 failures，0 errors，0 skipped |
| ruff check src tests | 退出 0 |
| ruff format --check src tests | 退出 0 |
| mypy src | 退出 0 |
| pip check | 退出 0 |
| validate-config | 退出 0，CONFIG_VALID，原摘要保持不变 |

## 工作流与权限

工作流配置 PR 指向 main、push 到 main、手动触发。Token 声明 contents: read；checkout 不保留凭据；Action 引用固定完整 SHA；job 时限为 15 分钟。

依赖安装与 GitHub artifact 上传仍使用网络。六项项目检查通过 unshare 在新网络命名空间中运行，降回 runner 用户并启用 no-new-privs。最终日志中的独立命名空间校验通过。不能把这个结果表述为整个 GitHub job 从未联网。

各检查记录实际退出码；某项检查失败后继续执行其他检查，最后依据累计失败标记退出。日志使用 always() 上传，保存期配置为 14 天。

## 测试覆盖边界

当前 `pyproject.toml` 使用 `testpaths = ["tests/unit"]`，CI 执行默认 pytest，不自动包含 `review/` 下的独立审查测试。

因此，这次 166 项通过既不是漏掉了 PR #1 的 17 项新增默认测试，也不是 PR #1 已通过 CI：两个 PR 独立，PR #1 尚未纳入当前被测版本。

PR #1 当前交付称默认测试 183 项，另有 R1-01 独立 15 项和 R1-02 独立 9 项。PR #1 更新到含 CI 的 main 后，合并前应在 CI 中分别运行这三组，保存新的 commit/run 证据；不要仅靠更新分支假定独立检查会被自动发现。

建议在 PR #1 的后续小提交中，将下述两条命令加入同一离线检查脚本，保留默认 pytest 与其他检查、逐项退出码和失败汇总：

```sh
.venv/bin/python -m pytest -q review/R1_01/checks/test_review_regressions.py
.venv/bin/python -m pytest -q review/R1_02/checks/test_r1_02_regressions.py
```

这不是要求在当前 PR #2/main 上运行尚未合并的 R1-02 独立文件，也不要求改写任何测试断言。

## 下一步

1. 由用户确认合并 PR #2；审查方没有执行合并。
2. 核对 main 上的 push 运行；在工作流进入 main 后补验一次手动触发。
3. 将更新后的 main 合入 PR #1 的分支，保留其历史；加入上述独立检查并推送，重新运行 PR CI。
4. 对最新 PR #1 提交完成修复复核后，再决定是否合并 PR #1。
5. 真实 Binance REST/WS 状态不因离线 CI 通过而升级。这里不授权 R1-03 或真实采集。

## 资料来源

- PR #2：https://github.com/shukehi/btc_microstructure/pull/2
- 固定版本工作流：https://github.com/shukehi/btc_microstructure/blob/8454c533f1abe2f68628392c6c85895ac3b4941c/.github/workflows/ci.yml
- 固定版本检查脚本：https://github.com/shukehi/btc_microstructure/blob/8454c533f1abe2f68628392c6c85895ac3b4941c/.github/scripts/ci_checks.sh
- 固定版本 pytest 配置：https://github.com/shukehi/btc_microstructure/blob/8454c533f1abe2f68628392c6c85895ac3b4941c/pyproject.toml
- 实际运行：https://github.com/shukehi/btc_microstructure/actions/runs/38118334383
- 实际 artifact：https://github.com/shukehi/btc_microstructure/actions/runs/38118334383/artifacts/11693832514
- GitHub 官方事件说明：https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows