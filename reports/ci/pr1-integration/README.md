# PR #1 CI integration

PR: https://github.com/shukehi/btc_microstructure/pull/1
Branch: fix/r1-02-protocol-probe

Merged main `bb86a55050f4652cee3db577943e0e43bf966ed2` into the existing fix branch
with an ordinary merge, preserving original fix commit `5f04910` and all earlier evidence.
No history rewrite or force push; PR #1 is not merged automatically.
GitHub review/comments/threads were checked before implementation; none were pending.
The historical runtime block remains REST BLOCKED and WS NOT_RUN.

The original default pytest command and five other checks are unchanged.
Two explicit pytest groups are now included in the same isolated shell flow:

- R1-01: review/R1_01/checks/test_review_regressions.py
- R1-02: review/R1_02/checks/test_r1_02_regressions.py

Each has its own .log and JUnit .xml, plus an actual exit-code row in results.tsv.
Either failure sets the existing job failure flag without skipping later checks.
The workflow YAML is byte-identical to main: job name, contents-read permission,
network namespace/no-new-privileges controls and always-upload mechanism retained.

Local macOS/Python3.12 checks: default183, independent15 and independent9 all passed,
with zero failures/skips; other five commands all exit0. Raw logs/JUnit are under local/.
This local result is not claimed as hosted Linux/network-isolation evidence.
Hosted Actions run/results will be reported after pushing this integration.

Business code, original tests, assertions, config, lockfile and historical evidence
match the original fix branch. The preexisting local AGENTS.md addition is preserved
and excluded from this CI-only commit. No Binance access or network/proxy change,
no R1-03 work; stop for independent review after updating the PR.
