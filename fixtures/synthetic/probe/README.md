Synthetic R1-02 protocol probe fixtures.

These files are hand-written protocol-shape examples for offline tests only.
They are not copied from Binance, are not market observations, and must not be
reported as live endpoint evidence.

The fixture names describe the test intent:

- `metadata.valid.json`: minimal USD-M BTCUSDT perpetual exchangeInfo shape.
- `metadata.duplicate-symbol.json`: invalid duplicate BTCUSDT instrument shape.
- `aggtrade.combined.json`: combined-stream aggTrade envelope with optional
  USD-M fields.
- `aggtrade.raw-extra.json`: raw aggTrade payload with an unknown extension
  field.
