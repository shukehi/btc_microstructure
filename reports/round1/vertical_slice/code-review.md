# R1-03 same-session reviewer passes

This is a native code-reviewer inspection within the development session, not independent acceptance. Historical external reviews remain untouched. Independent review is still required after PR delivery.

## Pass 1: REQUEST CHANGES

HIGH: checked-in fixture duplicated explicit ingest_sequence 1 for SESSION_OPEN and first RAW, so CLI collect failed before raw persistence. Reproduced by reviewer with checked-in configuration. Fixed fixture explicit sequence; retained order validator. Added checked-in CLI regression and smoke. This was an implementation draft, not an accepted result.

## Pass 2: REQUEST CHANGES

HIGH: build-features dispatched replay comparator, requiring existing derived tables and failing `no committed table named features` after a derived-table crash even when raw/control manifests remained trusted. Reviewer reproduced in a temporary copy with only derived files/manifests omitted. Split rebuild from comparison, added raw-only regression and CI smoke copying only trusted raw/manifests.

Reviewer checks at pass2: 52 targeted tests passed; CLI happy-path smoke passed; Mypy/Ruff/format/pip check passed. These happy-path checks did not prove raw-only rebuild. No hardcoded secret patterns observed. Storage broad exception boundaries retained evidence and raised errors.

## Leader integration

Further hardened temporary-manifest quarantine, persistent uncertainty across reopens, feature manifest time ranges, and rejection of in-process retries after ambiguous commit failure. Added explicit regressions. Publication/control/version/resource tests and full checks are recorded separately, with failures preserved.

Final follow-up verdict recorded below after fixes.

## Final focused follow-up: APPROVE (0 new blockers)

Reviewer verified distinct build_features path, trusted raw/control validation, stale/missing derived manifest classification and raw-only rebuild. Actual focused regressions 2 passed; storage filtered check 1 passed/17 deselected; fresh project-local smoke collect/replay1/replay2/raw-only build all PASS; Mypy 12 files passed. Scope was this recovery fix, not independent whole-system acceptance. Broader final evidence is checks-02 plus CI.
