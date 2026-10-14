# R2-04：Journal显式TEST_ONLY小范围集成

基线`493b400655d75b430a6e1255b0d2b5ae195691c9`；依据独立Review 5513253078和本轮用户授权。沿用PR #22独立worktree。范围为控制器的显式Writer选择、deadline能力传播、Journal只读表比较及集成测试/证据。旧ProcessWriter、Store、Core、CLI、普通配置schema、原预算/单位审批/网络配置和历史证据未修改。

## API与生命周期

`run_test_only_bounded_live(...)`新增两个仅限Python TEST_ONLY运行API的关键字参数：

- `test_only_writer="process"`为默认值；只有显式`"journal"`分支才延迟导入并构造Journal。
- `test_only_writer_factory=...`为故障测试提供构造后注入，factory必须显式标记`TEST_ONLY=True`。现有writer实例注入与选择/factory不可同时使用。

网络授权/共享预算拒绝门、transport TEST_ONLY和poll检查先于Writer选择。普通`BoundedLiveConfig`不含选择字段，配置额外字段仍拒绝；REAL CLI依然直接BLOCKED。新进程验证仅import控制器不会导入Journal。没有新增CLI参数、实际网络入口或存储功能。

控制器先拒绝任何既有输出目录，再创建唯一空run目录；factory在此后构造。Journal已有初始化可以接受控制器创建的空目录，拒绝非空目录，本轮保留该正确逻辑。两个Writer均通过真实进程初始化/关闭；已有运行目录测试比较前后字节不变。

## 统一期限

原writer_budget以`isinstance(ProcessWriter)`限制deadline赋值，Journal会遗漏。本轮按对象具有deadline能力传播同一绝对上限：准备、启动、观察、排空/派生和close仍取原控制器global/phase/真实墙钟guard的最小值。初始构造ACK也限制为`min(2s, 当前准备/启动/global剩余)`。普通ProcessWriter仍是默认，内部实现未改。

分别对两种真实Writer记录snapshot/batch/table/close命令，断言都收到非空且不超过控制器绝对全局上限的deadline。保留原2秒ACK、队列/RSS/状态/八分区/512MiB及磁盘门槛。同步父fallback审计/fsync的硬I/O限制仍沿用上轮审查边界，不因传播deadline宣称硬实时终止所有OS操作。

## 确定性对照

同一人工均匀aggTrade流10条/模拟事件秒，1秒grid、30秒窗口、同一run_id/FakeClock/精确payload；使用正常2.5GiB启动门槛。两个Writer顺序执行，输出目录独立。每组各运行两次只读replay，检查输入字节不变。

| 模拟秒 | 输入/特征 | ProcessWriter墙钟 | Journal墙钟 | 文件数：旧/新 | 对照 |
|---:|---:|---:|---:|---:|---|
| 30 | 300/30 | 4.5513s | 0.5770s | 317/9 | PASS_LIMITED_OFFLINE |
| 60 | 600/60 | 16.2917s | 1.1014s | 617/9 | PASS_LIMITED_OFFLINE |
| 120 | 1200/120 | 57.6393s | 1.8533s | 1217/9 | PASS_LIMITED_OFFLINE |

逐行比较原文base64/hash、RAW/control ingest_sequence/session/接收时刻、标准化/隔离结果、Decimal Delta/TI、质量/null、cutoff/input高水位、published与compute_done观察时刻和历史available。只排除`FEATURE_PUBLISHED.feature_manifest`的**物理存储格式引用**字段：旧格式为Parquet manifest，新格式为Journal事务receipt。旧receipt的实际文件SHA和保存cutoff逐个校验；Journal reader继续验证table_id/batch_id/chunk_count/逻辑SHA与cutoff/published的闭合。其余控制字段不抹去或改写。

两端派生逻辑SHA完全相同：30秒`95b6fe83…037d6`，60秒`006993e8…1559c`，120秒`e685f218…a0c5f`；完整摘要和排除物理receipt后的RAW/control摘要见小型证据JSON。30秒末窗独立oracle为N299、B/S/V=.149/.150/.299 BTC、Delta=-.001、TI=-1/299；60/120秒末窗N300、B/S/V=.150/.150/.300、Delta=0、TI=0。所有oracle、两次replay和历史available对照均通过。

另用买.30+.20、卖.10、重复、同ID数量冲突和坏JSON人工流验证：unique=3、quarantine=3、raw保留6条；首窗Delta=.40/TI=2/3，冲突和schema变化窗口量为null并保留标记。该测试发现Journal存储JSON中的缺字段与Arrow异构表读回的null列表示不同，导致逻辑值一致却被报重放FAIL。本轮仅用已存在的Arrow codec规范化**两侧表比较**，没有改变存储字节、Core计算、字段单位或预期值。

## 集成故障与失败证据

接线前3项测试实际失败，exit1：新选择/factory API尚未存在两项TypeError，以及注入Writer的deadline能力未获传播。完整本地日志为`data/session/journal-integration-before.log`，SHA256 `35397c3b602a9483cd1c9b19462bb1e150cdd25e5b02ac4d25451e097bb7a0cf`；提交实际摘录，不修改历史证据。

初次混合质量对照暴露上述Journal codec比较FAIL；初次磁盘停止夹具沿用`min_free_bytes=0`，因而没有启用硬floor。前者修复比较层，后者改用正常预算完成真实合同测试，不降低上限/断言。

| 集成故障 | 检查结果 |
|---|---|
| Worker hang/ACK超时 | BLOCKED/STOP_FAILED，最后可信ACK cursor3，无特征发布，Worker停止 |
| payload fsync后中断 | 未完成frame不进入Core，cursor3，PARTIAL只读恢复 |
| Worker异常退出 | fail-closed，无特征、最后ACK不变、PARTIAL恢复 |
| 子RSS越限 | 资源监控停止，未确认RAW不派生，cursor3 |
| 磁盘2.25GiB预警 | durable receipt为true，可正常收尾，可信recovery PASS |
| 磁盘降至2GiB以下 | 写入拒绝，cursor3，无特征，PARTIAL恢复 |

以上故障通过显式TEST_ONLY factory注入，不用修改默认Writer符号启用Journal。坏输入解析另有集成spy，确认Journal可信RAW batch已提交后才调用payload parser。

## 验证与状态

Python3.12.13/macOS arm64、锁定依赖，socket/DNS受现有离线测试护栏约束。新增21个参数化集成case。实际验证结果：

| 检查 | exit | 结果 |
|---|---:|---|
| Journal集成/资源/可靠性/原型定向套件 | 0 | 68 passed，0 failed/error/skipped |
| 默认pytest（十三项CI内执行） | 0 | 535 passed，0 failed/error/skipped |
| 四组独立审查pytest | 0/0/0/0 | 15/9/8/4 passed，0 failed/error/skipped |
| 十三项完整离线检查 | 0 | 全部exit0，包括原Parquet与CLI烟测 |
| Ruff / format / Mypy | 0/0/0 | 18源码类型检查通过 |

本机Linux网络namespace验证NOT_RUN，Hosted隔离与新SHA/Actions回执在同一Draft PR评论提供。完整本地CI日志为忽略目录`data/session/journal-integration-ci.log`。复现：

```
PYTHONPATH=src python -m pytest -q -s tests/unit/test_journal_integration.py
PYTHONPATH=src python -m pytest -q tests/unit/test_journal_integration.py tests/unit/test_journal_resources.py tests/unit/test_journal_reliability_review.py tests/unit/test_journal_prototype.py
PYTHONPATH=src bash .github/scripts/ci_checks.sh
```

映射REQ-001/002/003/004/009/010/012/016/017/018；DEV-003/004/006/008/013成交子集；TEST-TRADE-01/02/03、FEAT-01/02、TIME-01/02、QUALITY-01、STORE-01、REPLAY-01、OPS-01/03。

显式TEST_ONLY接线、30/60/120模拟对照和集成故障为限定离线验收。整体L04仍PARTIAL、REAL仍BLOCKED、PR保持Draft。600秒、100k、默认4MiB长时滚动、硬OOM/真实满盘/断电、FS并发竞争、checkpoint快速seek仍未验收；FakeClock历史available相等不能证明真实发布时延。完成后停止，等待独立复核。
