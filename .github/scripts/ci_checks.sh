#!/usr/bin/env bash
# Called inside the workflow's network namespace. Never run probe/collect/trading.
set -u -o pipefail

mkdir -p ci-results
: > ci-results/results.tsv
failed=0

if [[ -n "${CI_HOST_NET_NAMESPACE:-}" ]]; then
  current_network="$(readlink /proc/self/ns/net)"
  if [[ -z "$current_network" || "$current_network" == "$CI_HOST_NET_NAMESPACE" ]]; then
    echo "FAIL: project checks are not in a separate network namespace"
    exit 1
  fi
  echo "PASS: project checks use a separate network namespace"
elif [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
  echo "FAIL: network isolation was not requested by the workflow"
  exit 1
else
  echo "Local run: Linux network-namespace validation NOT_RUN"
fi

run_check() {
  local name="$1"
  shift
  echo "::group::${name}"
  "$@" 2>&1 | tee "ci-results/${name}.log"
  local code="${PIPESTATUS[0]}"
  printf '%s\t%s\n' "$name" "$code" >> ci-results/results.tsv
  echo "Exit code: ${code}"
  echo "::endgroup::"
  if [[ "$code" -ne 0 ]]; then
    failed=1
  fi
}

run_check pytest .venv/bin/python -m pytest -q --junitxml=ci-results/pytest.xml
run_check pytest-independent-r1-01 .venv/bin/python -m pytest -q review/R1_01/checks/test_review_regressions.py --junitxml=ci-results/pytest-independent-r1-01.xml
run_check pytest-independent-r1-02 .venv/bin/python -m pytest -q review/R1_02/checks/test_r1_02_regressions.py --junitxml=ci-results/pytest-independent-r1-02.xml
run_check ruff-check .venv/bin/ruff check src tests .github/scripts/offline_smoke.py
run_check ruff-format .venv/bin/ruff format --check src tests .github/scripts/offline_smoke.py
run_check mypy .venv/bin/mypy src .github/scripts/offline_smoke.py
run_check pip-check .venv/bin/python -m pip check
run_check validate-config .venv/bin/python -m btc_microstructure validate-config config/research.example.yaml
run_check validate-offline .venv/bin/python -m btc_microstructure validate-offline config/offline.synthetic.yaml
run_check offline-e2e .venv/bin/python .github/scripts/offline_smoke.py

if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    printf '## Offline checks\n\n| Check | Exit code |\n|---|---|\n'
    while IFS=$'\t' read -r name code; do
      printf '| %s | %s |\n' "$name" "$code"
    done < ci-results/results.tsv
    printf '\nProject checks ran in an isolated network namespace. No live probe was enabled.\n'
  } >> "$GITHUB_STEP_SUMMARY"
fi

exit "$failed"
