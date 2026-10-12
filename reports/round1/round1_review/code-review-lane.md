# Fresh native code-reviewer lane

Reviewed commit: 0b65be4933e9c36abedf784e608b832c56cee131. Fresh context, no inherited implementation conversation. Code/spec/security review stopped after the new blocker was reproduced.

Recommendation: REQUEST CHANGES. HIGH: RAW quality flags are discarded by offline_core.py:194/240; window validity at243-273 ignores row flags, so GAP_KNOWN input can produce healthy numeric volume/Delta. Input path offline.py:313 preserves raw flags. Contract docs/01:191 and docs/03:13 requires affected flow windows null.

Independent pure-core comparison: healthy and GAP_KNOWN versions both produce volume0.40, Delta0.20, count2, flags[]; normalized flagged input also flags[]. Script/log/exit receipt are in code-probes/. Probe command exits0 because it prints the observed counterexample; this is not a passing contract assertion. The coordinator's Parquet assertion reproduction exits1.

Covered partly before stop: direction/precision/unknown unit, economic dedup/conflicts, receive-time/order, historical publication, causal horizon separation, offline gates and selected tests. Existing R103 fix evidence read; original R103 findings not reopened. Full security/history/design audit not completed after stop. Standard11checks were coordinator-run, not claimed as re-execution by this lane.

Minimum recommendation for separate task: preserve upstream quality evidence and explicitly propagate invalidating record flags into affected windows; retaining a normalized flag alone does not repair feature validity. Do not blanket invalidate advisory flags. No code fixes or original test changes were performed.
