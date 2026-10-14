# L04 TEST_ONLY append journal/checkpoint format

Status: isolated offline prototype design. It does not replace `Store`, does not change the
one-second feature grid, and is not reachable from `collect-bounded` or any REAL entry.

## Invariants and layout

- `raw`, `control`, and `feature` are separate logical and budget partitions. Raw/control keep
  the existing global `ingest_sequence`; feature frames do not invent input cursors.
- Files roll at a bounded byte threshold (prototype default 4 MiB), never once per cutoff:
  `journal/raw/raw-000001.jnl`, `journal/control/control-000001.jnl`, and
  `journal/feature/feature-000001.jnl`.
- Existing partition caps remain: raw 192 MiB, control 16 MiB, feature 16 MiB, indexes/checkpoints
  32 MiB, temporary/orphan/quarantine 112 MiB, diagnostics 40 MiB, snapshots 8 MiB, total 512
  MiB. State remains 128 MiB; queue, RSS, disk-free, message, control, and time caps are unchanged.
- Each journal starts with a versioned file header. Each durable batch is one bounded frame:
  fixed header `{magic, version, partition, batch_id, first_sequence, last_sequence,
  record_count, payload_length, previous_frame_sha256, payload_sha256, header_crc32}`, canonical
  JSON payload, then fixed commit trailer `{magic, frame_sha256, frame_length}`.
- The worker writes header+payload, flushes and fsyncs, then writes the commit trailer, flushes and
  fsyncs again. Only a complete verified trailer advances the trusted offset/cursor and may be
  acknowledged. A timeout or failure before acknowledgement keeps the parent cursor conservative.
- Parent→worker send and worker→parent acknowledgement use separate nonblocking pipes under one
  absolute command deadline. The configured ACK bound is `(0,2]` seconds and cannot be raised;
  send backpressure, missing ACK, EOF, or worker exit immediately enters bounded terminate/kill
  cleanup without advancing the last acknowledged cursor.
- `RAW` frame acknowledgement remains before payload decoding/Core processing.
  `FEATURE_CUTOFF` acknowledgement remains before calculation. A feature frame acknowledgement
  remains before its `FEATURE_PUBLISHED` receipt. Missing publication receipts retain
  `feature_available_ns=null/UNVERIFIED` during replay.

## Checkpoint and recovery

- Checkpoints use alternating `checkpoints/slot-a.json` and `slot-b.json`. An envelope contains a
  canonical payload plus SHA-256. The payload records generation, global trusted cursor, and for
  each journal the file index, committed byte offset, last frame hash, and partition usage.
- A checkpoint is written to a bounded `.tmp`, fsynced, atomically replaced into one slot, and its
  directory is fsynced. Old slot plus new temporary bytes are both reserved before the write.
- Startup/read-only recovery first performs a complete physical tree audit. It rejects symlinks,
  special files, path escape, partition/total overflow, and unexpected files. It then validates
  both slots, selects the highest valid generation, and cross-checks it against journal frames.
- Journal basenames are canonical six-digit names. Any alias, duplicate logical number, unexpected
  `.jnl`, or other object in a legal partition directory is charged to the orphan/temporary
  partition before the run is rejected; it is never admitted merely because its suffix is `.jnl`.
- Journals, not checkpoints, are the fact source in this PoC. Frames are scanned with bounded
  lengths, CRC, payload SHA, commit SHA, and per-partition hash chains. Raw/control rows are merged
  by global sequence; only the contiguous prefix beginning at sequence 1 is trusted.
- A half header/payload/trailer, truncation, CRC/hash mismatch, or checkpoint mismatch stops at the
  preceding verified frame and emits `UNCERTAIN_GAP`. A torn newer checkpoint may fall back to the
  older valid slot. Missing format/context, no trustworthy prefix when one is required, permission
  or read failure, or an invalid physical tree is `BLOCKED`; recovery never edits input.
- Complete feature chunks remain candidates until a trusted `FEATURE_CUTOFF` and later trusted
  `FEATURE_PUBLISHED` receipt name the exact table id, batch id, chunk count and logical hash. If
  any link is missing, the derived view stops before that cutoff. Partial raw/control recovery does
  not expose final derived-cache tables; replay returns published features separately from pending
  UNVERIFIED rows and preserves the recorded available time only from trusted receipts.

## Bounded incremental audit

- Worker startup performs a full audit and establishes trusted files, descriptors, stat
  signatures, and exact physical charges.
- Before each append it validates the active descriptor/path identity and
  `{dev,inode,mode,size,mtime_ns,ctime_ns}`, then reserves exact frame growth against partition,
  512 MiB total, disk-free, and temporary coexistence limits before allocation/write.
- Every 32 commands, every checkpoint, and close perform another complete audit and frame
  validation. Newly observed orphan/temp files are charged; symlink, special file, same-length
  mutation, inode replacement, or size drift blocks further writes. Terminal receipts keep the
  existing 1 MiB/256×4 KiB reserve.
- This interval is a PoC parameter, not permission to remove audit. Formal integration must review
  the concurrent external-mutation window and may require a directory lease/watcher or a smaller
  interval.

## Compatibility and prototype API

- `TestOnlyJournalProcessWriter` implements only the existing injected `DurableWriter` surface:
  `append`, `flush`, `append_durable`, `write_snapshot`, `write_table`, and `close`.
  Construction requires an explicit TEST_ONLY marker; no CLI or transport creates it.
- `replay_test_only_storage(root)` dispatches directories carrying
  `journal-format-1` to the prototype read-only reader; directories without that marker are passed
  unchanged to existing `replay_bounded`. No old manifest, synthetic, A2, or R2-04 evidence is
  rewritten.
- Prototype metrics expose frame/fsync/ACK timings, file/partition bytes, audit counts, parent and
  worker RSS, recovery events, and checkpoint generation. Benchmark evidence uses the same
  artificial transport/Core/oracle as the legacy `ProcessWriter` comparison.
- Table chunks use the smaller of the one-MiB table limit and the active roll-file capacity. This
  permits bounded tests with small roll thresholds without weakening the normal four-MiB default.
  File numbering and the partition hash chain continue across roll boundaries; failed creation of
  the next file cannot move the trusted cursor.

## Explicit non-integration boundary and risks

This round does not change `bounded_live` defaults, `ProcessWriter`, `Store`, CLI, schemas accepted
by existing replay, or REAL gates. Integration would add a new storage schema and dispatcher,
change crash/recovery surfaces, and maintain two readers. The PoC scans journals from the start to
cross-check checkpoints; production checkpoint seek/fast-start is not established. Passing the
fault matrix and 30/60/120-second comparison can support an integration recommendation only; it
cannot approve 600 seconds, REAL use, merge, or replacement of the old format.
