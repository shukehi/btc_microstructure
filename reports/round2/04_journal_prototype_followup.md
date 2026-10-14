# R2-04 / Draft PR #22：L04 append journal/checkpoint 离线原型

依据 Issue #21 评论 6144223092 和独立 Review 5502591499，本轮只交付可回退的 TEST_ONLY 原型，不替换 `Store`、`ProcessWriter`、现有 replay 或 CLI，不改变1秒 grid。基线 head 为 `a38b81ae00667785023919d6010c3769c862d3f0`。Binance/probe/REAL、网络/代理/TLS、正式单位审批、共享出口和600秒演练均未启动。

## 结论

**原型可靠性与30/60/120秒对照为 `PASS_LIMITED_OFFLINE`；建议进入下一轮独立格式/API审查，但不建议本轮直接集成。L04仍PARTIAL，REAL仍BLOCKED，600秒仍NOT_RUN。**

原型在同机、同一 run_id、同一人工aggTrade、10条/模拟事件秒、1秒发布、30秒窗口下，相对旧 Writer 将30/60/120秒真实耗时从4.25/13.85/51.17秒降到0.66/0.92/1.71秒，约6.40×/15.08×/29.95×；文件从317/617/1217降到固定9个。两端每组双重禁网只读 replay 均 PASS，输入不变、旧/新逻辑SHA逐组完全相同，独立左闭右开 oracle通过。

性能收益不是免费：原型最终物理字节约1.00/2.00/3.99MB，高于旧格式0.82/1.61/3.21MB，主要来自frame头/尾、JSON feature journal与双槽checkpoint。它减少文件和重复全目录工作的同时增加约23–24%字节；正式集成前必须审查长期分区消耗及feature存储格式。

## 格式与边界

完整受限设计见 `journal_prototype/FORMAT_DESIGN.md`。实现位于单独的 `journal_prototype.py`，没有运行入口导入它。

- raw/control/feature分别追加，默认4MiB滚动。batch frame包含版本、分区、batch id、序号范围、记录数、payload长度、前链hash、payload SHA、header CRC及commit trailer。
- header+payload fsync后才写并fsync trailer；完整trailer后才推进worker可信游标并ACK。RAW ACK后原控制器才解析/Core；cutoff ACK先于计算；feature ACK先于published receipt。
- feature最终表按≤1MiB payload规划成多frame事务。只有所有chunk齐全才对replay可见；不提高2MiB单frame或2秒ACK上限。
- A/B checkpoint记录generation、全局cursor、各分区file/offset/hash及预算。checkpoint写前和写后完整审核；启动/replay以journal为事实源，交叉验证checkpoint。
- 启动全扫描；每次append前核对可信fd/path的dev/inode/mode/size/mtime/ctime并按内部累计写前预留；每32命令、checkpoint和close完整扫描并重验frame链。symlink、特殊文件、orphan、同长度篡改、inode/size漂移阻断。
- 保持512MiB总量和原八分区、128MiB状态、512MiB RSS、队列、2.5/2.25/2GiB磁盘门槛、2秒ACK和terminal reserve。benchmark最终轮未关闭磁盘门槛。
- `replay_test_only_storage`只有检测到`journal-prototype-1`标识才走原型reader；否则调用未修改的`replay_bounded`。最终旧Writer benchmark实际通过此dispatcher，证明旧格式读法未被替换。

## 故障与恢复

新增10项默认发现测试，使用真实进程writer和实际文件。所有只读恢复均先后比较tree digest，不修改输入。

| 注入 | 实际结果 |
|---|---|
| 第二batch payload fsync后中断、无trailer | 只信第一batch/cursor1；截断事件、无close ACK，PARTIAL |
| 已提交第三条尾部截断 | 只信前两条/cursor2，PARTIAL |
| 第二frame同长度内容修改 | checksum失败，只信cursor1 |
| 第二checkpoint临时文件fsync后中断 | 使用第一有效槽；journal cursor1保留，torn checkpoint标记 |
| recovery目录外symlink | 不跟随；`BLOCKED/RECOVERY_FAILED` |
| 活跃journal同长度外部修改 | 下一append的fd/path签名检查阻断 |
| checkpoint前新增orphan | 写checkpoint前完整审核阻断 |
| 多frame feature表最后chunk截断 | 不暴露不完整表，PARTIAL |
| 完整控制器顺序 | cutoff frame batch < feature batch < published frame batch；双replay与oracle PASS |
| 旧格式dispatcher | 无journal标识时走既有reader；30/60/120实际旧run均PASS |

开发中两次真实失败保留在忽略的 `data/session/` 并摘录于 `evidence-summary.json`：最初120秒收尾因单表frame超过2MiB而`JOURNAL_FRAME_PAYLOAD_LIMIT`；首次分块算法反复序列化增长块，导致2秒`JOURNAL_ACK_TIMEOUT`。修订分别是完整chunk事务和单次逐行计长；没有提高frame/ACK/cap。

## 最终同fixture结果

环境：macOS arm64、Python3.12.13、网络调用四入口禁用；以下每一行均为独立干净Python进程。RSS为父/子历史峰值保守相加，不是同一时刻采样。

| 模拟秒 | Writer | 消息/特征 | 墙钟s | 父/子CPUs | RSS上界MiB | 文件/字节 | ACK p95 | fsync p95 |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 30 | old | 300/30 | 4.246 | 0.464/3.722 | 164.77 | 317 / 815828 | 38.65ms | 旧worker未分离，包含在ACK |
| 30 | journal | 300/30 | 0.663 | 0.204/0.406 | 143.22 | 9 / 1003815 | 1.57ms | 0.109ms |
| 60 | old | 600/60 | 13.847 | 1.403/12.430 | 170.59 | 617 / 1612348 | 72.70ms | 旧worker未分离，包含在ACK |
| 60 | journal | 600/60 | 0.918 | 0.358/0.526 | 145.66 | 9 / 1997493 | 0.73ms | 0.064ms |
| 120 | old | 1200/120 | 51.173 | 4.854/46.186 | 182.95 | 1217 / 3211953 | 141.30ms | 旧worker未分离，包含在ACK |
| 120 | journal | 1200/120 | 1.708 | 0.714/0.944 | 149.69 | 9 / 3993642 | 0.99ms | 0.075ms |

120秒原型执行609个frame、20个checkpoint及42次完整审核。最终分区：raw 1132556B、control 195080B、feature 2653549B、index/checkpoint 2536B、snapshot 9909B、diagnostic 12B、orphan 0；总计3993642B。状态预约20,289,097B，远低于128MiB。所有writer stop reason为`OBSERVATION_COMPLETE`；控制器的整体status仍按历史schema显示PARTIAL，不改写旧验收状态。

逐组逻辑摘要：30秒`57172edc…5750`、60秒`34a5f9e9…bc1de`、120秒`bae15c4c…ec68`；同持续时长的old/journal完全相同。30秒末窗因第一条在0.1秒且右边界30秒消息排除，N=299、B/S/V=.149/.150/.299、Delta=-.001；60/120秒末窗N=300、B/S/V=.150/.150/.300、Delta=0、TI=0，均为独立固定oracle。

小型可复算证据为 `journal_prototype/evidence-summary.json`；原始run保留在忽略的`data/session/journal-safety-final-*`及开发失败目录，不提交大数据。

## API/schema变化与集成风险

若后续集成，只建议新增显式TEST_ONLY选择，不替换默认路径。需要独立审查：

1. 新二进制frame、hash链、table chunk事务和A/B checkpoint schema的版本演进/迁移责任。
2. checkpoint当前用于交叉验证，recovery仍从头扫描；尚未证明checkpoint seek后的前缀认证或快速启动。
3. 完整审核间隔为32命令，期间依赖可信fd/stat及内部账本；恶意并发文件系统攻击、断电、真实满盘/权限变化仍未物理验证。
4. feature journal当前使用canonical JSON而非Parquet，字节高于旧格式；正式存储分类名仍沿用原`feature_parquet`预算只是为保持同一16MiB cap，需要schema命名审查。
5. 进程writer原型约束了command/frame/chunk，但尚未做600秒、100k、4MiB滚动跨多个文件的正式持续验收。

因此结论是：**建议将该PoC提交独立审查，审查通过后再另行授权TEST_ONLY集成；不建议当前提交直接替换Store或启动600秒。** 若审查否定上述恢复/预算模型，应回退整个新增模块，旧路径不受影响。

## 需求映射与验证

适用：REQ-002/003/004/009/010/012/016/017/018；DEV-003/004/006/008/013的aggTrade子集；TEST-TRADE-01/02/03、TIME-01/02、FEAT-01/02、QUALITY-01、STORE-01、REPLAY-01、OPS-01/03。BOOK/OI/FUND/LIQ/研究/成本、600秒及REAL均不在本轮PASS范围。

本地 Python3.12.13 实际命令与结果：

| 命令 | 退出 | 结果 |
|---|---:|---|
| `python -m pytest -q tests/unit/test_journal_prototype.py` | 0 | 10 passed |
| `ruff check` / `ruff format --check`（src/tests/benchmark） | 0/0 | 全部通过，54 files formatted |
| `mypy src .github/scripts/offline_smoke.py` | 0 | 18 source files，无问题 |
| `python -m pytest -q` | 0 | 477 passed，0 failed/error/skipped |
| 四组独立review pytest | 0/0/0/0 | 15 / 9 / 8 / 4 passed |
| `PYTHONPATH=src bash .github/scripts/ci_checks.sh` | 0 | 十三项全部exit0；本机Linux network namespace检查NOT_RUN |

最终六组命令均为：`python reports/round2/journal_prototype/benchmark.py --writer <legacy|journal> --seconds <30|60|120> --output <new-dir>`，每次独立Python进程、输出路径唯一。benchmark禁用socket/DNS四入口并使用最终安全门槛；原始结果保留在忽略目录`data/session/journal-final2-*`。

Hosted结果将在精确提交后回执。PR保持Draft，不合并、不关闭Issue，不启动后续集成或持续演练。
