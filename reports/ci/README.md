# GitHub Actions CI v1 delivery

Repository: shukehi/btc_microstructure  
Branch: `chore/github-actions-ci`, created from latest `main` at `6b73205424d4f49eb41a29c327bc75ce3f55b2b2`.  
PR: https://github.com/shukehi/btc_microstructure/pull/2  
Workflow commit: `1e10b17358614d27ff6db011ba40954453fd743d`.

## Actual first GitHub-hosted execution

Run: https://github.com/shukehi/btc_microstructure/actions/runs/38118075146  
Event: pull_request; conclusion: **success**; Ubuntu24.04, Python **3.12.15**.  
Job time: 2026-10-11 06:26:42Z–06:27:03Z.

| Check | Actual result | Exit code | Raw evidence |
|---|---|---|---|
| pytest | 166 passed / 0 failed / 0 skipped | 0 | runs/38118075146/pytest.log and pytest.xml |
| ruff check src tests | PASS | 0 | ruff-check.log |
| ruff format --check src tests | PASS | 0 | ruff-format.log |
| mypy src | PASS | 0 | mypy.log |
| pip check | PASS | 0 | pip-check.log |
| validate-config | PASS; original config digest unchanged | 0 | validate-config.log |

All paths are under `runs/38118075146/`. `results.tsv` records actual per-command exit
codes; `workflow.log` is the full downloaded GitHub run log; `run.json` records job/step
status and head SHA. `checksums.sha256` hashes the exact downloaded files.
The first CI run did not fail; no synthetic failure log was created.
Local macOS checks also passed, with logs in local-checks.log/local-results.tsv;
those are not claimed as Linux network-isolation evidence.

## Scope and controls

Added only `.github/workflows/ci.yml`, `.github/scripts/ci_checks.sh`, CI documentation
and verification logs. Business code, tests, original expectations, specification,
pyproject.toml and requirements-dev.lock match `main` byte-for-byte.

Triggers: PR targeting main, push to main, workflow_dispatch. Token: contents read only.
Checkout does not persist credentials; actions use immutable full SHAs.
Dependencies install from the existing lock before network isolation. All six project
checks then run as the runner user inside a newly created network namespace, with
no external interface/default route and no privilege escalation. The actual runner
printed `PASS: project checks use a separate network namespace`.
No live probe/collector/trading commands or trading keys are configured or invoked.
Failure of a check still runs the remaining checks and returns a failed job;
install/infrastructure failures leave dependent checks NOT_RUN. Logs are uploaded
with always() and retained for 14 days, while this checked-in copy is persistent.

## Limits and unchanged PR

No unresolved code or CI failure from the first run.
Push-to-main and manual-dispatch events are configured but **NOT_RUN** in this change:
the new workflow must first exist on the default branch for manual dispatch.
This PR is not automatically merged to make that happen.
PR #1 was neither modified nor merged; its head remained
`5f04910d45831ac2857bc027cfdff749f6e021fe` and its state OPEN during verification.
