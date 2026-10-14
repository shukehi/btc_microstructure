# R2-04 / Draft PR #22：Journal / Checkpoint 格式与API可靠性审查

基线：`7019bb8a3b73982fae3e14e0c9bd99b909b1850e`。范围仅为独立`journal_prototype.py`、相关测试、格式设计和本报告；旧`Store`、`ProcessWriter`、CLI、Core/特征定义、1秒grid及默认采集路径均未修改。没有运行Binance、probe、REAL或600秒演练。

## 结论

四项结果均为 **PASS_LIMITED_OFFLINE**。格式/API已经达到“可提交下一轮独立审查的TEST_ONLY集成前提”，但这不是集成批准：本轮没有接线、替换Store或改变默认行为。L04继续PARTIAL，REAL继续BLOCKED。

修复前新审查套件实际为 **10 failed / 3 passed**。失败证明了三个实质缺陷及一个滚动边界缺口：阻塞`send_bytes`不受总期限约束；非规范`.jnl`可按可信分区收费但绕过扫描；部分输入仍暴露完整派生缓存/feature损坏仍重放published；table chunk不服从小roll阈值且缺新文件创建故障证据。原实现对规范文件缺号、非法文件头、坏hash链已经正确停止后续文件，未做无意义修改。首个rolled文件被截断可能得到`trusted_cursor=null`，这是安全结果，原反例断言已纠正。

## 1. 有限命令超时

### 缺陷

旧`_command()`直接调用阻塞`Connection.send_bytes()`，之后才对ACK执行poll；大命令遇到停止读取的worker会无限卡在send。小命令无ACK虽在2秒内抛错，但worker继续存活；worker先退出则泄漏`BrokenPipeError`。三者均不能支持“send+ACK共同有限、超时后writer静止”的合同。

### 最小修复与证据

- 改为父→子请求、子→父ACK两条单向pipe；父端fd非阻塞，使用有界`select/os.write`发送兼容frame，并以同一个绝对deadline读取ACK frame。
- `ack_seconds`强制`0 < value <= 2`；不能为通过测试提高上限。已有controller全局deadline仍可进一步缩短该deadline。
- send backpressure、ACK超时、EOF、worker exit统一为`WriterFailure`；立即执行0.1秒terminate join，仍存活则0.2秒kill join，关闭pipe。cursor只保留最后一次成功ACK。
- 实际反例：worker `SIGSTOP`后发送约1MiB命令、worker `SIGSTOP`后小命令无ACK、worker命令前被kill；均在测试外层0.8秒门槛内返回、worker不再存活、cursor保持1。`ack_seconds=2.01`构造明确拒绝。

状态：`PASS_LIMITED_OFFLINE`。限制：手工4-byte IPC framing基于本项目Python3.12/macOS/Linux的`multiprocessing.Connection`协议；若正式集成需审查跨Python/平台兼容性。

## 2. Journal文件可信性

### 缺陷

旧`_charge_path()`只检查目录和`.jnl`后缀，因此`journal/raw/raw-1.jnl`会进入raw分区；`_scan_partition()`只glob/处理预期前缀，文件可被计为可信类别却完全不验证。

### 最小修复与证据

- 三分区只接受`raw|control|feature-XXXXXX.jnl`六位规范名；其他别名、重复逻辑编号或任意`.jnl`进入temporary/orphan物理计费，然后run/recovery BLOCKED。
- `JournalAuditFailure`携带已遍历的实际分区收费和文件集合，证明拒绝发生在物理字节被计入之后；不通过忽略文件获得PASS。
- 规范文件继续验证连续编号、file header、frame header CRC、payload SHA、commit hash、跨文件previous hash及记录序号。
- 反例覆盖：非规范别名；删除中间编号；非法头；同长度payload/hash破坏；用`os.replace`把活跃path换成相同字节的新inode。后四者均不信受影响位置后的数据，运行中替换在下一append前由fd/path签名阻断。

状态：`PASS_LIMITED_OFFLINE`。合法旧文件的截断/坏链保持物理收费并只恢复先前frame；规范目录中未知文件不自动“隔离改写”，而是只读拒绝。

## 3. 派生特征可信前缀

### 缺陷

旧recovery独立组装所有完整feature frame。RAW/control发生损坏、全局可信cursor缩短时，返回值仍含最终`normalized/features`缓存；feature frame自身损坏时，完整control日志中的`FEATURE_PUBLISHED`仍可驱动Core输出published feature。这会把已持久化但依赖不完整的数据误当成可信派生结果。

### 最小修复与证据

- 完整feature chunk先作为candidate，不直接暴露。一个published feature必须同时存在：可信且更早的同cutoff `FEATURE_CUTOFF`、完整`features_pending` table transaction、匹配的table id/batch id/chunk count/逻辑SHA，以及可信`FEATURE_PUBLISHED`。
- 任一关联失败，从对应cutoff之前截断派生replay前缀并记录`FEATURE_PUBLICATION_UNVERIFIED`；不使用之后的published receipt。
- RAW/control扫描存在任何部分故障时，最终派生缓存表全部隐藏；replay只从可信原始/control前缀重算。返回的`features`仅含已验证published，未发布窗口另列`pending_features`，不能冒充历史可用。
- 增加`physical_trusted_cursor`与派生`trusted_cursor`区分；available time只能等于可信published receipt记录值，仍由Core检查`available>=cutoff/dependency`。
- 实际反例分别损坏第二个RAW frame、第二个control frame和第一个feature frame。前两者只输出cursor以内且有对应published receipt的特征；完整最终缓存不暴露。第三个从首个cutoff前截断，published特征为空、状态PARTIAL。两次读回不修改输入。

状态：`PASS_LIMITED_OFFLINE`。保守策略可能放弃物理上仍完整的后续RAW/control；这是避免跨缺口派生的设计选择，不是数据修复。

## 4. 文件滚动

### 修复与验证

- table chunk payload上限改为`min(1MiB, 当前roll文件可容纳字节)`；不再因小roll测试让单chunk先超过文件上限。
- 新文件创建前新增`roll/before_create`故障点。第二文件创建失败时不增加index、不写frame、不前移cursor；旧文件和cursor2可恢复，terminal receipt保留。
- 以32KiB阈值运行30模拟秒/10条每秒完整controller，RAW、CONTROL、FEATURE均跨至少两个文件。每个分区文件名严格从1连续；`_scan_partition`跨文件延续previous hash；恢复cursor等于最后输入序号。
- 每个分区的物理文件字节和原预算partition收费逐字节相等；没有把RAW/CONTROL/FEATURE互相借额度。
- 两次只读replay完全相同、输入tree digest不变、状态PASS。另截断第一rolled control文件尾部，恢复两次一致且不信任后续文件；若第一frame即受损，`trusted_cursor=null`是正确安全结果。

状态：`PASS_LIMITED_OFFLINE`。默认4MiB实际跨文件的长时压力和磁盘故障仍NOT_RUN；本轮只证明滚动算法与小阈值边界。

## 回归与性能烟测

定向最终：`tests/unit/test_journal_reliability_review.py`与原prototype测试合计 **25 passed**。修复前10/3原输出保留在本次会话和忽略的本地运行记录，不修改旧测试预期。

最终源码下的Journal 30/60/120模拟秒烟测：

| 秒 | 消息/特征 | 墙钟 | 文件/字节 | ACK p95 | fsync p95 | RSS峰值和 | replay/oracle |
|---:|---:|---:|---:|---:|---:|---:|---|
| 30 | 300/30 | 0.623s | 9 / 1003813 | 1.17ms | 0.092ms | 143.81MiB | 两次PASS / PASS |
| 60 | 600/60 | 0.965s | 9 / 1997491 | 1.02ms | 0.075ms | 145.89MiB | 两次PASS / PASS |
| 120 | 1200/120 | 1.784s | 9 / 3993640 | 0.59ms | 0.071ms | 149.84MiB | 两次PASS / PASS |

逻辑SHA仍分别为`57172edc…5750`、`34a5f9e9…bc1de`、`bae15c4c…ec68`，与此前同fixture一致。该烟测只证明可靠性修复未破坏30/60/120 PoC，不是600秒验收。

## 未解决问题与集成判断

- checkpoint仍用于交叉验证而非快速seek；启动从头扫描。
- 完整audit间隔中的恶意并发TOCTOU、物理断电、权限变化、满盘和硬OOM未实机验证。
- 非阻塞IPC实现在当前CPython3.12/macOS/Linux验证，跨平台协议需独立审查。
- partial时隐藏全部最终派生缓存是安全但保守的API变化；集成方必须显式处理`physical_trusted_cursor`、`trusted_cursor`和`pending_features`。
- 默认4MiB滚动、600秒、100k及真实墙钟均未运行。

结论：**四项已达到提交独立复核的TEST_ONLY集成前提，但未达到自动集成条件。** 建议独立审查本提交的IPC framing、派生关联和partial API后，再由用户另行授权是否接入TEST_ONLY选择；本轮不接线、不启动600秒、不改变Draft/L04/REAL状态。

本地Python3.12.13最终结果：

| 命令 | 退出 | 结果 |
|---|---:|---|
| `pytest -q tests/unit/test_journal_reliability_review.py tests/unit/test_journal_prototype.py` | 0 | 25 passed |
| `pytest -q` | 0 | 492 passed，0 failed/error/skipped |
| 四组独立review pytest | 0/0/0/0 | 15 / 9 / 8 / 4 passed |
| `ruff check` / `ruff format --check` | 0/0 | 全部通过；55 files formatted |
| `mypy src .github/scripts/offline_smoke.py` | 0 | 18 source files无问题 |
| `PYTHONPATH=src bash .github/scripts/ci_checks.sh` | 0 | 十三项全部exit0；本机Linux network namespace检查NOT_RUN |

Hosted Actions将在精确提交后记录。PR保持Draft，不合并、不关闭Issue。
