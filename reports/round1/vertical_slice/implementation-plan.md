R1-03 only; baseline f7c591a1de2aa03536854e239e6c570a1728ff0f.

1. Separate offline storage, pure normalization/window kernel, synthetic runner/replay.
2. Preserve exact payload and Decimal values; retain schema/adapter/metadata versions and explicit SYNTHETIC evidence independently of source_origin.
3. Commit bounded rotated JSONL segments and ZSTD Parquet through validated temporary files + atomic manifests. Recover from trusted hashes/cursors; quarantine uncertain tails, never delete unique raw.
4. Rebuild economic-key dedup across sessions/restarts from committed raw; snapshot as_received windows at historical cutoff/high water, attach logged publication times.
5. Separate strict offline configuration, collect synthetic, replay/build-features and validation commands. Real collection rejected before network; no probe or Binance access.
6. Test storage faults/resources, exact normalization/units/dedup, boundaries/quality/time, deterministic replay/future independence, CLI/Parquet end-to-end; retain all three baseline suites and isolated CI checks.
7. Deliver report with REQ/DEV/TEST and node IDs, preserved failures, environment/checksums, exact commit and Actions evidence; push task branch and create PR for #4, then stop for independent review.
