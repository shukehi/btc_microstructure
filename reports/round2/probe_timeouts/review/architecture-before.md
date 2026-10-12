**WATCH — 超时边界设计基本成立，但诊断契约仍有两处缺口。** 本结论针对基线 `fd4fbd73fc6a7c0c2a4cff77ba97492dfdc56525` 上当前未提交内容，仅为静态架构审查。

1. **旧 transport 的观察阶段没有保持 UNKNOWN。**
   [probe.py:303](<task-checkout>/src/btc_microstructure/probe.py:303) 默认取编排阶段；REST 调用前又将其设为 `REST_HEADERS_OR_BODY`（363–368行）。反例：现有 `GenericTimeout` 无任何阶段观测却立即抛出 `TimeoutError`；[测试:447](<task-checkout>/tests/unit/test_probe_timeouts.py:447) 只检查来源 UNKNOWN，没有检查阶段。这与[任务报告:16](<task-checkout>/reports/round2/02_probe_timeouts.md:16) 的约定不一致。
   **最小建议：** 分开记录编排操作与 transport 观察阶段；后者缺观测时明确 UNKNOWN，并补阶段断言。

2. **WS 底层超时可能绕过超时诊断。**
   [probe_transport.py:194](<task-checkout>/src/btc_microstructure/probe_transport.py:194) 和202–203行将 `aiohttp.ClientError` 转为普通网络错误；相比之下，REST 在147–164行先处理具体超时。反例：WS 握手先抛 `ConnectionTimeoutError`，或接收先抛 `SocketTimeoutError`，会走 `AccessError` 分支；[probe.py:452](<task-checkout>/src/btc_microstructure/probe.py:452) 的诊断仅在 `TimeoutError` 分支填写，因而可能保留 `NOT_STARTED/NOT_EXPIRED`。现有[握手测试:475](<task-checkout>/tests/unit/test_probe_timeouts.py:475) 使用 ScriptedTransport，未覆盖此路径。
   **最小建议：** WS 在通用 ClientError 前处理具体超时，追加安全 transport 来源；编排来源仍仅依据 owned scope 的 `expired()`，不得据阶段推定 CONNECT/IDLE。

静态检查支持以下设计：

- **owned 超时归因保守：** [probe.py:295](<task-checkout>/src/btc_microstructure/probe.py:295) 检查 `expired()`；没有到期为 UNKNOWN，多个到期为 MULTIPLE_EXPIRED。
- **全局预算不重置：** [probe.py:209](<task-checkout>/src/btc_microstructure/probe.py:209) 建立一次固定截止；REST、WS连接、WS接收分别在348、387、414行取该截止与局部截止的较早者。
- **共享 session 不继承 REST 剩余预算：** [probe_transport.py:104](<task-checkout>/src/btc_microstructure/probe_transport.py:104) 仅覆盖 REST 请求选项；WS 在179–182行使用连接域和 ClientWSTimeout。
- **部分正文与取消路径保持保守：** [probe_transport.py:169](<task-checkout>/src/btc_microstructure/probe_transport.py:169) 将中断正文标为 INTERRUPTED；[probe.py:379](<task-checkout>/src/btc_microstructure/probe.py:379) 阻止不完整正文进入验证/WS。470–492行保留观察并限时清理，未捕获外部 CancelledError。**取消后的自动落盘不能据此宣称成立**：[cli.py:130](<task-checkout>/src/btc_microstructure/cli.py:130) 只有正常返回后才调用 save_probe。

本 lane 未联网、未运行测试、未修改文件、未调用子代理或读取记忆；未核验 Issue/PR 在线记录及并行 CI。历史真实 HTTP200/部分正文仍为 BLOCKED，WS NOT_RUN，不升级验收状态（[README:203](<task-checkout>/README.md:203)）。审查到此停止，不执行合并。
