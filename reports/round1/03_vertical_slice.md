# R1-03 离线最小流程

任务：Issue #4；规格 0.1；范围 **DEV-003/004/006/008 的离线子集**。
基线：`f7c591a1de2aa03536854e239e6c570a1728ff0f`（最新 origin/main）。
任务分支：`feat/r1-03-offline-vertical-slice`。用户原有未提交 AGENTS.md 修改保留，不提交。

## 范围与状态

本次实现人工永续聚合成交流、raw/control JSONL、可信 manifest、标准化/去重/隔离、可配置 Delta/TI、ZSTD Parquet、历史截止/发布时间及确定性重放。
整个原 R1-03 和 M0/M1/M2 **PARTIAL**，仅本次离线子集按下列实际结果报告。
真实 REST 保持历史 **BLOCKED**；真实 WS **NOT_RUN**；真实数量单位 **UNKNOWN**；持续采集预算 **UNVERIFIED_FOR_CONTINUOUS_COLLECTION**；真实采集及24h **NOT_RUN**。不实现其他特征、研究、账户或交易。

## 开始前证据

已读取 Issue 全文（开始时评论为空）、AGENTS.md、CODEX_START_HERE、任务卡和报告模板、docs/00/01/02/03/05/06/07、原配置/固定样例、现有配置/probe/transport/三组测试和 CI。
已读取 review/R1_01、review/R1_02 原始审查/修复要求及既有修复报告；保留历史失败证据。
PR #1 最终审查 `5483287314` 对提交 `ce8cd8a7d5207093d28ac3230ecdf11aac0713a5` 仅通过离线修复和 CI；未将真实依赖视为关闭。PR #1 inline comments为空，PR #2/#3无后续评论/审查；#3未合并不阻塞本任务。
实施计划先发布于 https://github.com/shukehi/btc_microstructure/issues/4#issuecomment-6109084171 。

## 修改文件

- `src/btc_microstructure/offline_storage.py`：有界队列、轮换 JSONL、校验/原子 manifest、恢复与隔离、Parquet。
- `src/btc_microstructure/offline_core.py`：纯计算内核、精确标准化、经济键去重、质量和截止快照。
- `src/btc_microstructure/offline.py`、`cli.py`：首轮严格配置、人工时间表、离线运行/重放及启动拒绝。
- `config/offline.synthetic.yaml`、`fixtures/synthetic/vertical_slice/`：人工单位和完整消息/调度样例。
- `tests/unit/test_offline_*.py`：新增行为/故障/端到端测试。
- `pyproject.toml`、`requirements-dev.lock`：仅新增 pyarrow==20.0.0；默认发现 tests 全目录；Mypy声明未提供类型存根的 Arrow边界。
- `.github/scripts/`、README：增加明确离线端到端检查及复现命令；required job和隔离保持。
- 本报告及 `reports/round1/vertical_slice/`：计划、真实命令、初次失败/修复验证证据。
原 docs、研究配置、原 synthetic_cases、旧测试/审查/报告不改写。

## 数据语义与版本

新 schema `r1-03.1`、adapter `um-agg-synthetic.1`、feature `delta.1`；独立 manifest schema标识文件提交协议。
`evidence_kind=SYNTHETIC` 表示数据类别；`source_origin=live_ws` 仅模拟原协议接收形态，不扩展原 source_origin 枚举、不表示真实 WS。人工接收时间、元数据和发布记录均不可作为现场证据。
价格/原生数量直接解析十进制字符串，基础量仅在显式 synthetic BTC单位证据下填值；未知单位保留 native量、base/notional为空。Decimal128输入范围超界明确隔离；不静默舍入。派生乘积可使用Decimal256，TI为Float64。
经济主键不含session；raw重复保留，标准化仅计一次；重启以可信raw完整重建去重，未声称网络exactly-once。
冲突在观察后才影响覆盖冲突/原记录的当前窗口；此前已发布快照不改写。ID跳号不推断缺失数量、不调用REST。
窗口 `[t-W,t)`、输入高水位、同时间全局序号、UTC跨日状态；预热/缺口/未知单位为null，健康空窗量0/TI null。
严重时钟/存储故障显式拒绝或null降级。新审计状态 `DUPLICATE`、`FATAL_HEALTH`、`UNVERIFIED` 属于本schema扩展，不替换既有质量枚举。
有历史发布记录时保留；无记录时 feature_available_ns=null，明确未验证发布重放。重放耗时与历史时间分开。

## 需求与测试映射

以下均只指本次离线适用子集；配置校验不算真实运行验收。

| REQ | DEV subset | 原 TEST | 实际 test node 分组 | 证据 |
|---|---|---|---|---|
| 001/011 | 004/008配置 | CFG-01/02、未来独立性 | test_offline_core.py + test_offline_pipeline.py | 最终default JUnit |
| 002/017 | 003持久化 | STORE-01、OPS-03 | test_offline_storage.py + pipeline raw崩溃测试 | 最终default JUnit |
| 003 | 004/006时间 | TIME-01/02 | core边界/精度/午夜/发布测试 | 最终default JUnit |
| 004 | 004聚合成交 | TRADE-01/02/03 | core方向/去重/冲突；pipeline恢复重建 | 最终default JUnit |
| 009/012 | 008 Delta/TI | FEAT-01/02、QUALITY-01成交子集 | core空窗/缺口；pipeline固定三笔 | 最终default JUnit |
| 010 | 006重放 | REPLAY-01 | pipeline两次重放/逻辑摘要/未来不变 | 最终default JUnit |
| 016 | 004离线边界 | OPS-01 | baseline禁网+pipeline入口拒绝+CI隔离 | CI日志 |
| 017 | 003/006故障 | OPS-02断线子集/03 | core GAP；storage故障/容量/停止 | 最终default JUnit |
| 018 | 004证据版本 | DOC-01适用子集 | synthetic unit/source/version gates | 来源登记/metadata/manifest |

TEST-OPS-02的429/418仍由原probe离线回归覆盖，不新增真实传输或重试服务。
BOOK/OI/FUND/LIQ/研究/成本/24h验收等超范围内容 **NOT_RUN/未实现**，不宣称通过。
具体 test node ID 由最终 pytest JUnit 的classname/name记录，并另保存 `test-nodes.txt`。

## 实际执行与环境

当前本机 Python 3.12.13、macOS arm64；aiohttp保持3.12.15；新增Arrow20.0.0实际安装成功。依赖网络下载和GitHub读写是本轮授权联网行为；项目模拟/测试/重放无Binance访问。
本机无法提供Linux网络命名空间证据，记NOT_RUN；GitHub Actions在独立Linux网络命名空间实际执行全部检查。

开始前默认基线183通过、R1-01独立15通过、R1-02独立9通过，三组退出0；`vertical_slice/baseline-*.log/xml/exit`。
初次core检查16通过/2失败，`core-initial.log`；失败为新测试质量枚举过时及发布时间拒绝消息不匹配，按既有枚举和实际时钟拒绝路径修正测试，未放宽数值/时间合同。
最终验证和CLI命令如下；远程CI运行在PR创建后补充。验证脚本每次创建新checks-NN目录，不覆盖失败日志。

## 证据限制与停止

离线数据不证明真实BTC单位、流覆盖、现场时延、吞吐或连续预算；不启动probe、真实连接或长期采集。
本地存储提交是文件系统能力范围的原子提交，断电尾部仍须标记UNCERTAIN_GAP。
推送任务分支并创建PR后等待独立审查，不自行resolve审查线程、不合并、不开启后续任务。

## 最终本地检查

本机 `reports/round1/vertical_slice/checks-02/commands.json` 保存以下每条真实argv、工作目录（项目根目录）与退出码；同目录分项log/JUnit/environment。

| 命令 | 退出码 | 通过/失败/跳过 | 证据 |
|---|---|---|---|
| `.venv/bin/python -m pytest -q --junitxml=.../checks-02/default.xml` | 0 | 252/0/0 | default.log/xml |
| `.venv/bin/python -m pytest -q review/R1_01/checks/test_review_regressions.py --junitxml=.../checks-02/independent01.xml` | 0 | 15/0/0 | independent01.log/xml |
| `.venv/bin/python -m pytest -q review/R1_02/checks/test_r1_02_regressions.py --junitxml=.../checks-02/independent02.xml` | 0 | 9/0/0 | independent02.log/xml |
| `.venv/bin/ruff check src tests .github/scripts/offline_smoke.py` | 0 | PASS | ruff.log |
| `.venv/bin/ruff format --check src tests .github/scripts/offline_smoke.py` | 0 | PASS | format.log |
| `.venv/bin/mypy src .github/scripts/offline_smoke.py` | 0 | PASS，12文件 | mypy.log |
| `.venv/bin/python -m pip check` | 0 | PASS | pip-check.log |
| 原 validate-config / 新 validate-offline | 0/0 | PASS | config.log/offline-config.log |
| `git diff --exit-code origin/main -- docs examples/synthetic_cases.json config/research.example.yaml review tasks CODEX_START_HERE.md` | 0 | PASS | integrity.log |
| `.venv/bin/python .github/scripts/offline_smoke.py --output reports/round1/vertical_slice/smoke-ci-03` | 0 | PASS：五条CLI命令、两次重放、raw-only重建、Parquet读回 | smoke-ci-03.log/exit；CI artifact含完整运行 |
| `.venv/bin/python -m btc_microstructure probe config/research.example.yaml --output data/r1-03-probe-disabled` | 2 | NOT_RUN，未给网络许可 | probe-disabled.log/exit |
| R1-01独立忽略规则（新增生成目录排除后） | 0 | 15/0/0 | gitignore-regression.log |

默认183项全部保留；新增 **69项**：core30、storage18、pipeline9、scenarios12。新增精确node ID见 `test-nodes-final.txt`。所有普通测试在unit禁网fixture下执行；CI additionally网络命名空间隔离。没有skip/xfail。

保存的中间失败：`core-initial.log` 16通过/2失败（新测试枚举/错误消息）；`scenarios-initial.log` 9通过/3失败（人工测试时间表/字段）；`storage-followup.log` 首次注入测试1失败后修复；`pipeline-current.log` 20通过/1失败（派生损坏误记raw缺口，修复分类）。失败日志不删除、不回写为通过。`checks-01`保存较早235项及全检查通过，最终以`checks-02`252项为准。

同会话review两次HIGH发现（fixture序号、raw-only build）及修复已保存在 `code-review.md`；最终针对恢复路径复查APPROVE，不代替提交后的独立审查。

## 离线证据与复现

fixture/config/lock SHA-256见 `input-lock-checksums.json`；合成覆盖接收时间1ns至35.002s，截止1..35秒，5/30秒两个窗口，70行；无真实观测。
人工来源说明 `fixtures/synthetic/vertical_slice/README.md`。原固定三笔预期不变。运行manifest有版本、source模块hash、Git SHA/dirty、锁摘要、配置摘要、输入摘要、随机种子0和环境；`replay_elapsed_ns/build_elapsed_ns`是本次执行耗时，与历史发布时刻分开。
生成的完整本地运行目录保留但不提交；远程相同命令的完整raw/manifest/Parquet随Actions artifact上传。较小日志/JUnit提交以便复核。新增 `.gitignore` 仅排除这些生成目录和ci-results，保留原秘密保护。

安装和复现使用README的Python3.12锁定环境流程及R1-03章节；实际命令包括 validate-offline、collect --mode synthetic、replay 两次、build-features。所有输出须新目录。

离线子集当前 **PASS**；R1-03原任务总体仍 **PARTIAL**。仅在同配置/版本下重放比较；修改研究期限需新运行身份，原特征值/输入选择已单独反事实测试。损坏raw恢复到最后可信游标，不以派生重建关闭输入尾部不确定性。损坏派生仅DERIVED_INVALID，可以从可信raw另目录重建。
