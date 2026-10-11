# R1-03 synthetic vertical slice fixture

`warmed_delta.json` is an artificial BTCUSDT USDⓈ-M aggregate-trade schedule for offline tests only.

- Evidence kind: `SYNTHETIC`.
- Quantity unit: BTC only because `config/offline.synthetic.yaml` provides `SYNTHETIC:manual-btc-quantity`.
- Source origin remains `live_ws` to exercise the internal Binance aggregate-trade envelope contract; this fixture is not a live Binance capture.
- The feature schedule emits 1 second `FEATURE_CUTOFF` / `FEATURE_PUBLISHED` control events from 1s through 35s with injected receive and publish timestamps.
- The fixed acceptance window at cutoff 31s contains buy 0.30, buy 0.20, sell 0.10 BTC, plus a duplicate receipt of the second trade. Expected unique results are volume 0.60, delta 0.40, trade imbalance 2/3, and `agg_message_count` 3.

The file must not be used as protocol evidence, endpoint budget evidence, quantity-unit evidence for live Binance data, or a trading/backtest result.
