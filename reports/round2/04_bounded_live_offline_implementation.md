# Issue #21 / R2-04：有界 aggTrade 本地离线实现（PARTIAL）

**结论：可审查的 PARTIAL；完整 L04/L05 验收 BLOCKED，不可启用 REAL。**
基线：`a082edae460a27b19bd2bc9ded6a26903dbf1b18`，分支 `feat/r2-04-bounded-live-aggtrade`。Refs #17 / #12。本报告记录提交前离线阶段；最新提交 SHA、Draft PR 和该 head 的 hosted Actions 结果记在 PR 正文，避免自引用 SHA。已在此 worktree 启动新 Codex CLI 会话 `01a13312-8593-7ca3-9fb9-b4af3725c3b5`，主会话随后核对源码范围、测试产物及用户改动保护。本次没有行情请求、真实 probe、代理/TLS 改动或密钥读取。原工作区仅 AGENTS 未提交，最终 SHA-256 与本次保护记录一致；原规格、配置、锁、工作流、单位候选、历史证据及基线原测试/断言均未修改。

## 开发前模块映射与最终改动

| 当前模块 | 复用 / 最小扩展 |
|---|---|
| `cli.py` / `offline.py` | 仅 CLI 增加 bounded validate、拒绝 REAL collect、只读 replay；旧 synthetic / probe 入口保留 |
| `probe.py` / `probe_transport.py` | 复用严格 JSON、Decimal、身份检查；源码未改，不调用真实传输 |
| `offline_core.py` | 同一 Core，显式 `binding=UnitBinding`；默认 synthetic 保留，TEST_ONLY 有独立版本/来源/发布命名空间 |
| `offline_storage.py` | 原可信 JSONL、Parquet、manifest 校验/原子提交与 Decimal codec 未改；隔离 writer 在子进程复用 |
| 新 `unit_binding.py` | 候选原字节 hash/version、身份/filters、专用 TEST_ONLY fixture；REAL CLI 无条件拒绝，候选仍 pending/runtime_applied=false |
| 新 `bounded_live.py` / `bounded_transport.py` | FakeTransport/FakeClock 入口；有界 poll、阶段/全局截止、公共 tick、raw-first 与实际观察发布回执；物理 adapter 仅拒绝用 placeholder |
| 新 `bounded_writer.py` / `bounded_replay.py` | 单向 OS 管道 IPC、有限 ack/join、可信前缀、资源预留；只读重建并独立比较原派生表 |
| 新 `tests/unit/test_bounded*`、`test_unit_binding.py`、`bounded_fakes.py` | 默认发现范围：65 个新增案例；没有第二套 CI 发现路径或 skip/xfail |

## 父会话 critical 处理与故障证据

读取 `data/session/parent-review.md` 后确认无活跃原子代理；统一所有权。修复 `>=` 精确截止与 None 返回前检查，删除无界 receive fallback 和废弃 watchdog；poll、REST、connect、send、close 均有等待上限。消息与静默 tick 共用 queue age / 0.5s flush / cutoff 调度；首次 cutoff 严格晚于观察起点并落在 UTC 网格。特征持久化回执先于 PUBLISHED，记录实际观察及 manifest 引用。

writer 在 snapshot、connect、取消及正常停止的各分支均尝试有限 close/join；失败保留最后确认游标并报告 STOP_FAILED/BLOCKED。未用线程 fsync 的 asyncio 取消冒称停止。测试覆盖 append/flush/fsync/rename/manifest、挂起 writer、无法确认终止、worker RSS 启动失败和原目录拒绝恢复。Core 状态编码检测、主/worker RSS 峰值合计、批量 raw 与派生发布前检查已加入，**不是下述完整资源保证**。

原静默挂起进程由父会话结束；未再次杀进程。最小案例通过 20s 外层进程限时；后续定向/整脚本均由外层进程时限包装。失败日志仍在忽略目录：`live-process-initial.log`、`parent-critical-tests.log`、`post-parent-targeted.log`、`parent-critical-tests-after.log`、`burst-after-accounting.log`、`final-required-targeted.log`；旧错误 PASS 报告另存 `stale-report-before-parent-fixes.md`，没有作为验收事实引用。

新增启动测试原 fixture 的 REST20s/connect11s 已违反各自15s/10s上限，先正确触发 REST_TIME_LIMIT；改用14s/7s与较短20s启动预算核验启动门，保留原断言与失败日志。新增 burst 原一秒物理处理实验未处理完1000条；方向计数测试明确注入轻量 writer，原固定1000断言保留。TEST_ONLY 的短观察时段由注入时钟推进，另有共享真实全局 watchdog；**虚拟1s推送不等于本机真实1000条/s吞吐或真实接收时间**。

## 实际验证

环境：Darwin arm64 / Python **3.12.13**，既有锁定环境，无依赖安装。`PYTHONPATH=src` 导入已核对本工作树。临时 `.venv` 仅指向既有 `../r104-followup/.venv`；交还前只删除自身 symlink。

| 本次最终命令 / 检查 | 退出码、实际数量 | 证据（忽略目录） |
|---|---|---|
| `.venv/bin/python data/session/run_bounded_check.py 20 silence-regression .venv/bin/python -m pytest -q tests/unit/test_bounded_live.py::test_silent_poll_watchdog_stops_without_waiting_for_next_market_message` | 0，1 passed | `data/session/silence-regression.log/.exit` |
| 同包装器60s，`pytest -q tests/unit/test_bounded_live.py tests/unit/test_bounded_parent_review.py tests/unit/test_bounded_writer.py tests/unit/test_bounded_replay.py tests/unit/test_bounded_core.py tests/unit/test_unit_binding.py tests/unit/test_bounded_cli.py --junitxml=data/session/final-targeted.xml` | 0，65 passed，0 fail/error/skip | `data/session/final-targeted-verified.log/.exit`、`final-targeted.xml` |
| `.venv/bin/python data/session/run_bounded_check.py 180 final-thirteen bash .github/scripts/ci_checks.sh`（包装器设置 `PYTHONPATH=src`） | 0；十三项均0 | `data/session/final-thirteen.log/.exit`、`ci-results/results.tsv` |
| 默认 pytest（十三项之一） | 0，**391 passed**，0 fail/error/skip；326基线 +65新增 | `ci-results/pytest.xml/.log` |
| 独立 R1-01 / 02 / 03 / 04（十三项之四） | 各0，**15 / 9 / 8 / 4 passed**，0 fail/error/skip | `ci-results/pytest-independent-r1-*.xml/.log` |
| 原 Parquet；Ruff check/format；Mypy；pip check；validate-config/offline；原 offline-e2e（其余八项） | 各0 | `ci-results/` 同名日志与 `results.tsv` |

开始时原十三项也全部0，默认326、独立15/9/8/4，证据 `data/session/baseline-ci-results/`。Linux网络命名空间验证本机 **NOT_RUN**；不冒充远端 Linux/Actions。PyArrow CPU sysctl 的沙箱诊断保留，未改变检查结果。小型脱敏汇总见 `bounded_live_offline/verification.json`。

## L01–L07 与原需求映射

适用：REQ-001/002/003/004/009/010/012/016/017/018，DEV-003/004/006/008/013 成交子集；TEST-CFG-01/02、STORE-01、TIME-01/02、TRADE-01/02/03、FEAT-01/02、QUALITY-01、REPLAY-01、OPS-01/02/03、DOC-01。BOOK/OI/FUND/LIQ、研究/成本不在本次范围。

| ID | 最终状态 | 实际证明 / 缺口 |
|---|---|---|
| L01 | PASS 限定离线门；REAL BLOCKED | pending/hash/version/身份/filters/跨市场/TEST_ONLY/旧入口/目录拒绝；尚无可信正式运行审批机制与物理入口 |
| L02 | PASS 限定人工样例 | B=.50、S=.10、V=.60、Delta=.40、TI=2/3；Decimal、nq null、重复/冲突、坏 UTF8/JSON 原字节及同 Core 重放 |
| L03 | PASS 限定 FakeClock 案例 | 左闭右开/高水位/暖机、静默 UTC 网格、durable-before-compute/publish、实际确认观察、缺回执 UNVERIFIED/null；真实 UTC/NTP/发布耗时 NOT_RUN |
| L04 | **PARTIAL / 完整验收 BLOCKED** | 已测阶段与全局截止、静默推进、消息/字节/control方向、状态/RSS判停；以下生产连接与完整资源边界未证明 |
| L05 | **PARTIAL / 完整验收 BLOCKED** | 已测有限 IPC/ack/join、五点故障、可信前缀、temp+final/八分区/总cap与free边界；完整保护仍有下述缺口 |
| L06 | PASS 限定生成 fixture | 两次只读重放逐表等值 +独立手算；派生表错误 FAIL、孤儿/缺发布回执 PARTIAL；输入 hash 不变；没有倒填 A2 |
| L07 | PASS 本地回归 | 原326和独立套件保留通过，新增65在默认391内；原 Parquet、CLI、R203-CI-01继续通过 |

## 必须阻塞、不能以未获联网授权混淆的实现缺口

1. **物理 adapter 未实现/未离线验证**：固定 URL/TLS/redirect/trust_env/REST体与connect-idle细分、单REST/单WS零重试、真实发送点 pong/close（含库内部控制）均缺接线证明；placeholder 与 Fake 调用次数不是该实现。
2. **RSS/状态保护未完整达标**：worker 只在命令边界采峰值，没有阻塞 I/O 期间100ms连续采样；主/worker峰值合计是保守观测而非同时RSS。128MiB编码状态在检查点判停，没有所有 Core 状态分配前的精确预留保证；100k输入完整压力与扫描开销未验收。
3. **完整存储预算未达标**：启动2.5GiB和写前2GiB floor已接入，2.25GiB预警状态、39+1MiB terminal预留/256条收尾收费未完整实现或验证；manifest等采用有上限的保守预留，会较早停止，不能称精确实际占用计费。全磁盘失败时回执无法保证可写。
4. **吞吐未验收**：保留修复过程真实 process writer 的一秒预算失败；最终人工1000条的进程 writer 样本（socket guard、无行情请求）完成1000条，实际含收尾耗时约2.04s，证据 `data/session/final-pressure.json/.log`，并非1000条/s现场吞吐；轻量 writer方向测试不替代 fsync吞吐/队列压力、真实600s或24h。同步 Core计算/编码本身没有可抢占硬时限；异步传输的有限等待依赖合作式取消，拒绝取消的物理适配层没有隔离证明。
5. **历史信任与现场验收**：单位候选、共享IP可信预算仍待独立审批；正式 runtime绑定与完整恢复/版本/元数据 provenance 审计未闭合。真实请求、probe、NTP、600s、物理满盘/断电/硬内存均 NOT_RUN。以上实现缺口独立于本轮网络授权。

按 Issue 停止扩展并交付 **PARTIAL** Draft PR。完整 L04/L05 验收仍 BLOCKED；不启用 REAL、不合并、不关闭 Issue、不继续下一任务或长期采集。等待独立复核。

共享证据：`bounded_live_offline/verification.json`、`environment.json`、`results.tsv`、`targeted-final.txt`；开发中失败的筛查副本为 `pressure-failure-before.txt` 与 `scheduler-failure-before.txt`，原日志及校验哈希见 `failure-evidence-index.json`。最终源码额外人工压力结果见 `final-pressure.json`（1000条、可信游标1004、含收尾2.04秒，仍PARTIAL）；其脚本 `reproduce_pressure.py` 仅用同一人工fixture与socket guard，不会执行行情请求，需从仓库根运行并使用全新忽略输出目录。完整原始日志保留在忽略目录，未上传会话全文。27项锁定依赖逐项匹配与导入核对见 `environment.json`。
