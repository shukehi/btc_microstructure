# Persistence dependency references

Read 2026-10-11; official Apache Arrow 20.0 docs, not Binance evidence.

- https://arrow.apache.org/docs/20.0/python/generated/pyarrow.decimal128.html : decimal128 uses a declared precision and scale, maximum precision 38. Reject unsupported precision instead of rounding.
- https://arrow.apache.org/docs/20.0/python/generated/pyarrow.parquet.write_table.html : write_table supports compression='zstd'; readback validates logical content separately from physical metadata.
- https://arrow.apache.org/docs/20.0/python/generated/pyarrow.Table.html : from_pylist accepts an explicit schema; inference must not discard keys that appear after the first row.

Only new runtime dependency pyarrow==20.0.0; install actually succeeded on Python3.12 macOS arm64. No existing lock entries upgraded. Linux installation exercised by Actions.
