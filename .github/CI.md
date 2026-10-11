# CI v1

`ci.yml` runs on PRs targeting `main`, pushes to `main`, and manual dispatch.
The token has only `contents: read`; checkout does not persist credentials.
Official actions are pinned to immutable SHAs with their verified release tags.

Python 3.12 installs the existing `requirements-dev.lock`, then installs the project
with `--no-build-isolation --no-deps -e .`. No lockfile or business/test changes are made.
Dependency download and GitHub artifact transfer need normal package/GitHub access.
All project checks run as the runner user inside a fresh Linux network namespace
with no external interface/default route. `setpriv --no-new-privs` prevents privilege
escalation by the checks. The host network and proxy settings are not modified.
No trading keys, project secrets, live probe, collection or trading commands are used.

`.github/scripts/ci_checks.sh` runs every requested check and records each actual exit
code; a failure does not skip later checks or turn the job green. `ci-results/*.log`,
`*.xml`, and `*.tsv` are uploaded with `always()` for 14 days, including install errors.
Infrastructure/install failure leaves dependent checks NOT_RUN, not PASS.
Manual dispatch becomes available once the workflow exists on the default branch;
this PR is not automatically merged to enable it.

The first PR-triggered run is the Linux/network-isolation acceptance evidence.
Local macOS execution of the check script cannot validate Linux `unshare` behavior.
The default pytest selection from pyproject.toml remains unchanged. Two additional
explicit pytest invocations run the R1-01 and R1-02 independent review files in the
same network-isolated check flow. Each receives a separate .log, JUnit .xml and row
in results.tsv. Either nonzero exit code fails the job without skipping the other
checks. No assertion or historical review evidence is edited.
