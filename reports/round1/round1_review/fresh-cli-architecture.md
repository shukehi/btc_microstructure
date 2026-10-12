**Architecture status: BLOCK. Review completion: PARTIAL—stopped after confirming a new blocker, as instructed.**

Reviewed commit: `0b65be4933e9c36abedf784e608b832c56cee131`. Initial detached checkout was clean. No code, tests, configuration, Git state, or report files were modified.

**R104-ARCH-A — High: normalization silently clears upstream quality evidence**

- **Trigger:** A valid synthetic RAW trade carries `quality_flags=["GAP_KNOWN"]` and falls inside a fully warmed feature window.
- **Expected:** Preserve the known-gap evidence; affected flow quantities and Delta must be null. This follows `docs/01_data_contracts.md:186` onward and TEST-FEAT-02.
- **Actual:** The normalized trade has `quality_flags=[]`; the 31-second cutoff’s 30-second window also has `quality_flags=[]` and reports Delta `0.40`.
- **Root cause:** `offline.py:313` accepts RAW quality flags, but `offline_core.py:194` constructs replacement flags solely from unit verification and `offline_core.py:240` overwrites the original flags. Window validation at `offline_core.py:250–273` does not consume upstream record flags.
- **Impact:** The raw → normalization → feature trust boundary can convert explicitly degraded input into apparently healthy output. Replay calls the same core (`offline.py:537–542`); deterministic agreement alone cannot detect this shared semantic error.
- **Mapping:** REQ-009/012; DEV-004/008 subset; TEST-FEAT-02 and TEST-QUALITY-01 applicable subset.
- **Confidence:** High; directly reproduced offline.
- **Minimum recommendation:** In a separate repair task, preserve and validate upstream quality evidence and define its window-level effects. Simply retaining flags in the normalized table is insufficient if feature calculation still ignores them.

Reproduction, from the checkout root using the existing `.venv`:

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python - <<'PY'
from btc_microstructure.offline import OfflineConfig, fixture_rows
from btc_microstructure.offline_core import Core

config = OfflineConfig.load("config/offline.synthetic.yaml")
engine = Core(config.core_config(), config.metadata)
for row in fixture_rows(config):
    if row["kind"] == "RAW" and row["raw_ref"] == "fixture:1":
        row["quality_flags"] = ["GAP_KNOWN"]
        print("raw_flags:", row["quality_flags"])
    engine.process(row)

feature = next(
    r for r in engine.features
    if r["feature_cutoff_ns"] == 31_000_000_000
    and r["window_seconds"] == 30
)
print("normalized_flags:", engine.normalized[0]["quality_flags"])
print("feature_flags:", feature["quality_flags"])
print("delta:", feature["trade_delta_base"])
assert feature["trade_delta_base"] is None
PY
```

The executed equivalent additionally blocked socket connection and DNS functions. **Exit 1**, with:

```text
raw_flags= ['GAP_KNOWN']
normalized_flags= []
feature_flags= [] delta= 0.40
AssertionError: contract: GAP_KNOWN must make affected flow window unavailable
```

This was one standalone assertion failure, not a pytest run. Environment observed: **Python 3.12.13**. Dependency-lock matching and all eleven checks remain coordinator-owned.

Twelve-point coverage at the stop condition:

| Point | Status and evidence |
|---|---|
| 1. Direction, aggregation, precision | Inspected `offline_core.py:155–240`: Decimal parsing, maker direction, synthetic unit gate and aggregation naming. Adjacent nodes: `test_normalizes_m_false_as_buy_and_m_true_as_sell`, `test_preserves_decimal_q_and_nq_without_double_counting`. Not rerun. |
| 2. Cross-session dedup/conflicts | Inspected economic key and conflict quarantine, `offline_core.py:132–150`. Adjacent node: `test_duplicate_same_economic_key_across_sessions_is_not_counted_twice`. Not rerun. |
| 3. Receive time, boundaries, ordering, warmup | Inspected `offline_core.py:90–105,243–252`, `offline.py:418–475`. Read boundary, high-water and midnight testnodes; not rerun. |
| 4. Health versus healthy zero | **BLOCK:** confirmed R104-ARCH-A. Existing `test_known_gap_keeps_reconnected_window_unavailable_until_gap_ages_out` covers control events, not this RAW-flag boundary. |
| 5. Storage/repeated recovery | Inspected commit/recovery/cursor validation, `offline_storage.py:252–319,452–620`. Recovery nodes identified, including `test_failed_raw_tail_propagates_through_repeated_raw_only_parquet_builds`; not rerun. |
| 6. Historical publication | Inspected `offline_core.py:106–117`, `offline.py:615–617`: recorded publication or pending unverified rows. No new wall clock in core. Dynamic verification NOT_RUN. |
| 7. Horizons/future input | Inspected immutable cutoff construction and independent configuration fields. Counterfactual probes NOT_RUN. |
| 8. Offline/private-interface boundary | Inspected synthetic-only CLI/config gates (`cli.py:26–39`, `offline.py:102–109`) and socket-blocking test fixture. Full security lane remains separate. |
| 9. Test integrity | Read selected assertions; complete historical integrity comparison NOT_RUN. |
| 10. Python/lock/README | Python version verified; README read. Eleven checks and fresh dependency verification NOT_RUN in this lane. |
| 11. Capabilities/config/manifest identity | Inspected synthetic metadata gate, replay identity checks (`offline.py:628–665`) and UNVERIFIED capabilities (`capabilities.py:14–24`). Adversarial identity probes NOT_RUN. |
| 12. Evidence categories | Read Issue #6 snapshot, PR5 fix review **5483555449**, and delivery/review material. Historical versus synthetic limits remain explicit. Required reading was **not fully completed** before the blocker stop condition. |

No Binance access or realprobe occurred. Historical REST remains **BLOCKED**, WS and bounded live exercise **NOT_RUN**, real quantity units **UNKNOWN**, sustained budget **UNVERIFIED**, and 24-hour acceptance **NOT_RUN**. No full M0/M1/M2 or strategy-capability conclusion is supported.

Next dependency: independently confirm R104-ARCH-A, then scope a separate repair task; complete the unfinished review after repair.