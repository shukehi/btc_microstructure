# R2-04 Journal资源保护合同补齐

基线`b4b8202f6b90be47eca62417900c5557265313e3`；依据PR #22独立Review 5512592593。本轮只修改隔离Journal原型、新增资源测试及本报告/小型证据；旧Store、ProcessWriter、Core、CLI、默认采集、规格、单位审批、锁文件及历史报告均保留。沿用原独立worktree，原工作区仍只有用户AGENTS.md未提交，本轮未写入原工作区。

## 结果与既有缺口

RSS监控和磁盘预警/可靠终止均为`PASS_LIMITED_OFFLINE`。这消除了Review指定的两项资源合同差异，可提交独立复核；不批准自动TEST_ONLY集成。PR继续Draft、L04继续PARTIAL、REAL继续BLOCKED。600秒、硬OOM、物理满盘及断电未执行。

先运行新增反例，原head结果为**7 failed**、exit1；完整日志`data/session/journal-resources-before.log`留在忽略目录，SHA256 `b25baa6aa5210c23a0bd7ad8fa5e779b0bfef6eaa48394062787730ec087389c`。可提交的实际失败摘录为`journal_prototype/resources-before.txt`。失败覆盖独立采样指标缺失、父/子越限无停止、父监控不存在、独立构造未执行2.5GiB启动门槛、无持久预警及预警写失败未阻断。原head会忽略尚未实现的注入参数，测试结果结合源码说明合同缺失，不冒充真实超额内存/磁盘事件。

源码对比：旧Writer有父/子独立线程与共享RSS记录；Journal原来仅在命令处理附近读取ru_maxrss。旧Writer包含中间磁盘预警与fsync回执；Journal只有硬floor判断。原Journal已经正确的Budget分区、512MiB总量、terminal reserve、非阻塞send/ACK、可信cursor及只读恢复未重写。

## RSS监控与有限终止

原型增加父/子独立监控，100ms为目标。子线程采样`ru_maxrss`并通过无锁RawValue记录峰值、采样数、heartbeat和最大实际间隔；父线程独立采样父RSS，读取子峰值，合计超过512MiB即fail-closed。384MiB为预警；采样或监控异常、worker退出也触发失败。注入式sampler只用于TEST_ONLY，默认读取真实本地进程数据。

前台append/send/ACK等待也检查资源状态。Worker空闲采用50ms有界IPC poll，以便接收资源失败标记；受控hang依然由独立线程采样。父发现超限先通过共享标记给worker0.15秒协作收尾窗口，然后使用原0.1秒terminate join、0.2秒kill join停止；停止状态若无法确认则STOP_FAILED，不宣称干净关闭。父cursor始终保持最后成功ACK。

Worker可协作时沿用有界fsync错误receipt；不读取IPC的SIGSTOP worker被终止后，由父端审核run目录、执行同一Budget/terminal预约，再尽力写`JOURNAL_PARENT_FAILURE`，记录最后ACK cursor和停止状态。父端复用已有Worker错误记录时再次fsync文件及目录，避免仅凭记录文字存在就断言durable。失败的回执明确记录FAILED；没有无限重试、删除日志或把无close ACK标为PASS。协作/强制路径由同一个停止锁及幂等标记避免重复写回执。

验证包括：IPC等待期间无前台命令的独立采样；挂起第二batch时父/子RSS越限；各进程均低于512MiB但合计越限；父线程采样异常和退出检测；子监控退出；Worker意外退出；SIGSTOP worker因父RSS越限被有限kill；可信cursor、持久receipt和PARTIAL恢复。

**测量限制：** ru_maxrss是进程历史高水位，父子峰值之和是保守上界，不是同一瞬时RSS；无锁共享记录也不是原子多字段快照。1秒heartbeat过期为保守停止条件。Python线程、GIL与OS调度不保证100ms硬实时，join数值是请求等待预算；硬OOM/内核不可中断I/O未验证。父端fallback receipt使用同步fsync，不能保证OS严重I/O故障时回执及时或成功，但Worker停止先于fallback写入。

## 磁盘预警、预算与终止

独立构造在正常硬floor配置下核对启动剩余至少2.5GiB。每次写前预约、payload fsync后、commit trailer fsync后、ACK前和收尾均复查；低于2GiB停止，不提交未完成frame、不推进未确认cursor。2GiB整也无法覆盖任何正写入预约，因此拒绝。

低于2.25GiB且未达硬floor时置storage_warning，在继续写前以同一terminal reserve预约并fsync一次`DISK_FREE_WARNING`及目录，包含观察值、阈值、可信cursor；重复观察只保留一次。warning写入/fsync失败停止，storage_warning_receipt不虚报。硬floor使terminal也无法预留时，明确`receipt_status=FAILED`，保留已有可信前缀；不为了留下新回执越过2GiB门槛。

Budget及八分区数值完全复用旧Writer；所有run文件总512MiB、terminal保留1MiB/256×4KiB。新增构造校验拒绝提高RSS/总文件/队列上限或把正常硬floor降到2GiB以下。已有`min_free_bytes=0`仅用于明确的离线故障fixture，默认路径和最终烟测使用正常门槛。人工磁盘读数通过spawn可序列化sampler注入，未制造真实磁盘填满。

| 场景 | 实际断言 / 状态 |
|---|---|
| 2.5GiB减1B启动 | STARTUP_DISK_FREE_LIMIT，PASS_LIMITED_OFFLINE |
| 恰2.5GiB启动、恰2.25GiB运行 | 可启动；预警为false，PASS_LIMITED_OFFLINE |
| 2.25GiB减1B | durable warning恰1条，重复写不重复警告，PASS_LIMITED_OFFLINE |
| warning fsync前注入失败 | 当前cursor保持1，worker停止，PASS_LIMITED_OFFLINE |
| 2GiB整/减1B | 写预约拒绝；cursor保留，PARTIAL恢复；减1B的错误receipt不能预约则FAILED，PASS_LIMITED_OFFLINE |
| payload fsync后读数降至硬floor下 | 不写commit trailer，恢复只信cursor1并标FRAME_TRUNCATED，PASS_LIMITED_OFFLINE |
| 父子越限/线程失败/Worker退出 | 独立停止、最后ACK不变、错误receipt或诚实FAILED，PASS_LIMITED_OFFLINE |

## 本次验证

Python3.12.13/macOS arm64；锁定依赖未改。新增22个参数化case，资源和既有Journal定向合计47通过。最终十三项检查同时覆盖默认、四组独立套件、原Parquet/CLI烟测、静态检查与配置校验。

| 实际命令 | exit | 结果 |
|---|---:|---|
| `PYTHONPATH=src python -m pytest -q tests/unit/test_journal_resources.py`（原head） | 1 | 7 failed，失败日志/摘录保留 |
| `PYTHONPATH=src python -m pytest -q tests/unit/test_journal_resources.py tests/unit/test_journal_reliability_review.py tests/unit/test_journal_prototype.py` | 0 | 47 passed，0 failure/error/skip |
| `PYTHONPATH=src bash .github/scripts/ci_checks.sh` | 0 | 十三项exit0；默认514、独立15/9/8/4，全部0 failure/error/skip |
| `ruff check` / `ruff format --check` / `mypy src .github/scripts/offline_smoke.py` | 0/0/0 | 18源码类型检查通过 |
| `benchmark.py --writer journal --seconds 30 --output <new-dir>` | 0 | 300消息/30特征、双重只读replay PASS、输入不变、固定oracle PASS |

本地Linux网络namespace验证NOT_RUN，Hosted隔离由CI验证；所有行情输入均为人工fixture，未运行probe/真实行情。

最终30模拟秒烟测：墙钟0.635069秒，父/子CPU 0.214796/0.389482秒；9文件、1,003,819B，状态预约5,082,962B。ACK p95 1.055ms、fsync p95 0.0682ms。父/子历史RSS峰值77,316,096/77,479,936B；父/子监控各3次，最大实际间隔110.057/110.022ms（目标100ms）。总文件字节和状态预约与RSS各自报告，未互相冒充。该样本只验证短时资源接线，没有新增长持续验收结论。

映射：REQ-002/010/016/017，DEV-003/006/013成交子集，TEST-STORE-01、TEST-REPLAY-01、TEST-OPS-03；原单位、数据、时间/发布及只读重放回归同默认套件执行。

## 交付边界

本轮两项指定资源保护已支持有限离线PASS，可交独立复核。正式TEST_ONLY接线、checkpoint快速seek、恶意并发FS竞争、默认4MiB长时滚动、600秒、100k、硬OOM/满盘/断电仍未验收。原型没有增加数据库功能。新SHA/Actions精确回执随同一Draft PR提交评论提供，完成后停止等待审查。
