**CLEAR — 按新契约，原两项 WATCH 均已关闭；本次限定源码复核未发现新阻塞。**

- **编排与传输诊断已分离。** [probe.py:295](<task-checkout>/src/btc_microstructure/probe.py:295) 的 `source` 仅检查 owned scope 的 `.expired()`；303–318行保留编排 `phase`，旧 transport 缺观测时两个 `transport_*` 字段显式为 UNKNOWN。
- **WS typed timeout 已进入上层诊断。** [probe_transport.py:195](<task-checkout>/src/btc_microstructure/probe_transport.py:195) 与 [222行](<task-checkout>/src/btc_microstructure/probe_transport.py:222) 在 broad `ClientError` 前捕获公开超时类型、记录传输诊断并重抛 `TimeoutError`，由 [probe.py:447](<task-checkout>/src/btc_microstructure/probe.py:447) 填写上层诊断；WS connect/receive 分别在178、219行清除旧诊断，REST 阶段回退也被限定为 `stage == "rest"`（probe.py:313）。

仅确认上述源码修订；未运行测试、修改文件或联网。测试/CI通过及历史真实接口状态均不由本结论升级。
