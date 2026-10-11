任务编号：R1-02 / DEV-002 修复 R102-A/B  
原规格版本：0.1  
代码版本/工作树：基线提交 `6b73205424d4f49eb41a29c327bc75ce3f55b2b2`；工作分支 `fix/r1-02-protocol-probe`。修复源码用 `probe_fix/implementation.sha256` 标识；本次按用户要求提交并创建指向 main 的 PR，不自动合并。  
本次范围：仅约束客户端隐式请求重试，以及保留正文中断前已观察到的状态、预算头、可用时间和有界前缀。  
结果：**离线修复 PASS**；真实接口仍为历史 REST **BLOCKED/TIMEOUT**、WS **NOT_RUN**。整体运行核验仍为 PARTIAL。  
未实现范围：R1-03、采集/特征/研究/账户/下单；本次不访问币安、不修改网络或代理设置。

## 执行环境

原项目 `.venv`：macOS 27.0.1 / Darwin 27.0.0 arm64，Python **3.12.13**，aiohttp **3.12.15**，Pydantic 2.12.3、PyYAML 6.0.3、pytest 8.4.2、ruff 0.14.0、mypy 1.18.2。
完整依赖版本见未改写的 `requirements-dev.lock` 和 `probe_fix/after_02/dependencies.log`；26项锁定版本全部精确一致，未升级、降级或安装依赖。
独立审查方原来使用的 Linux/Python3.13/aiohttp3.13.3 记录仍作为其历史证据，本次在原机锁定版本复现，不混用环境结果。

## 修改文件

| 文件 | 用途 |
|---|---|
| `src/btc_microstructure/probe_transport.py` | 公开 session middleware 约束一次请求处理链；安全异常转换；请求处理次数/阻止重入计数；响应头与逐块正文快照；拒绝状态立即停止读正文；取消不吞掉 |
| `src/btc_microstructure/probe.py` | Reply增加兼容默认字段；报告升级probe-0.2；保存中断快照/字节计数/完整性；记录逻辑调用与可观察请求处理次数，未知传输可见性单列；预算报告同步口径 |
| `tests/unit/test_probe_attempts.py` | 新增8项回归，用真实aiohttp请求循环注入断连/ClientOSError检查REST和WS只进入下游一次，并验证阻止二次重入与未观察传输的口径 |
| `tests/unit/test_probe_interruptions.py` | 新增9项逐块传输回归：payload错误/超时、有效JSON前缀仍不完整、拒绝不读、无响应头、字节上限、外部取消、总时限 |
| `reports/round1/02_protocol_probe_fix.md` | 本报告，旧报告原样保留 |
| `reports/round1/probe_fix/before/` | 原机7失败/2通过的完整日志/JUnit/命令、修复前两份源码快照 |
| `reports/round1/probe_fix/after_01/`, `after_02/` | 修复后两轮完整日志、命令与JUnit，含首轮格式FAIL；after_02为最终结果 |
| `reports/round1/probe_fix/verify_offline.py`, `verify_integrity.py`, `render_synthetic.py` | 无真实网络的可复现检查、原材料保护、人工中断响应生成 |
| `reports/round1/probe_fix/synthetic-interrupted/` | SYNTHETIC的21字节前缀、原始字节/hash、不完整状态及预算报告 |
| `reports/round1/probe_fix/protected.json`, `integrity.json`, `integrity-first.log`, `implementation.sha256`, `implementation.diff` | 原始保护快照、内容完整性结果、首次OS元数据漂移失败、源码哈希与diff |
| `reports/round1/probe_fix/source_verification.json`, `code-review.md` | SDK公开API依据和同会话只读复查记录 |
| `review/R1_02/` | 用户提供的原始独立审查包；未改写，随提交保留原失败证据 |
| `reports/round1/package_protocol_probe_fixed.py`, `probe_fixed_package_manifest.json`, `probe-fixed-packaging-audit.json` | 独立修复版打包程序、清单、排除/敏感模式检查 |
| 根目录 `protocol_probe_review_fixed.zip`, `.zip.sha256`, `.zip.verify.json` | 新交付包、外部校验和/验证回执；旧包保留 |

原规格、配置样例、原166项测试、R1-01独立15项、R1-02独立9项及历史证据均未改写。
243个既有内容文件哈希一致；原保护快照还包含4个Finder `.DS_Store`，其中1个运行期间发生OS元数据漂移。
首次检查因此FAIL，完整诊断保留在 `integrity-first.log`；未回退或改写该文件，而是在内容核验中明确排除4个非内容OS文件。原保护快照本身不改写；`.DS_Store` 不纳入ZIP/Git。

## 修复前后行为

### R102-A：隐式重试与计数

修复前，真实aiohttp3.12.15请求循环在注入ServerDisconnectedError后进入下游中间件两次；应用报告却只包含常量retry_count=0。
本次原机独立测试首先真实暴露REST和WS这两项失败，未访问外部服务器。

修复使用公开 `ClientSession(middlewares=...)`：在第一个中间件中计数并调用下游；
`ClientOSError` / `ServerDisconnectedError` 转为项目 `AccessError`（不是aiohttp.ClientError或OSError），
因此不再被客户端幂等GET重试分支捕获。另有每阶段一次的入口保护，任何二次重入在下游之前被拒绝。
没有更改私有 `_retry_connection`，没有升级依赖或只修改报告数字。
REST不使用请求级middleware覆盖；WS握手通过session middleware的同一请求路径。

报告口径：
- `logical_calls`：应用对metadata/connect的实际调用次数。
- `transport_attempts`：已允许进入aiohttp下游请求处理链的次数，分别记录rest/ws；不是TCP连接竞速、数据包重传或成功发送数量。
- `retry_count`：从可观察处理次数推导的重复处理次数；`blocked_retry_count`单列入口已阻止的重入。
- `retry_count_scope`：公开handler可见时为APPLICATION_AND_REQUEST_HANDLER；旧注入传输不提供计数时，transport_attempts为null，明确APPLICATION_ONLY_TRANSPORT_UNOBSERVED，不冒称看见底层发送。
- `max_retries=0`仍是策略上限；预算另存观察计数，不把策略常量当现场事实。

公开API/版本核验见 `source_verification.json`：
[3.12.15客户端源码](https://docs.aiohttp.org/en/v3.12.15/_modules/aiohttp/client.html)、
[公开中间件说明](https://docs.aiohttp.org/en/v3.12.15/client_advanced.html#client-middleware)。
本机实际SDK循环测试是运行依据；本轮没有重新核验或连接币安接口。

### R102-B：头部先到，正文随后中断

修复前，429/451的状态和预算头因正文超时/ClientPayloadError丢失；200场景已交付21字节也被记为0且无法保存。
修复后，响应对象可用时立即保存状态、白名单预算头及 `headers_received_ns`；逐块读入前缀保持有界。
正文超时、总时限取消或payload错误后，报告保留已读字节并标记 `body_complete=false`、`truncated=true`、`body_read_state=INTERRUPTED`。
`save_probe`保存前缀原始字节与SHA-256，部分正文不参与完整JSON/元数据核验；即使前缀本身是合法JSON，也不能PASS或继续WS。

明确非200状态立即停止正文读取，保留头部，状态NOT_READ_HTTP_REJECTION；没有读过的正文长度记0，不能虚构前缀。没有观察到响应头时，不补造HTTP状态或头部。
原2MiB预算与超限检测的额外1字节仍有效，实际保存最多预算大小；清理仍最多1秒并计入总限制。
外部CancelledError不转换为普通返回，完成有界清理后继续向调用方传播；只读 `metadata_observation` 仍提供最后快照，供调用方审计，测试验证了这一点。
Cookies/认证头不保留，底层敏感异常文本不进入报告。

### 接口版本与兼容边界

新报告 `schema_version=probe-0.2`；历史probe-0.1报告不回写。
Reply追加 `body_complete`、`headers_received_ns`、`body_read_state`，旧三参数构造与Transport的方法签名保持兼容。
AioTransport新增只读进度/计数属性；旧注入传输无需实现新属性。run_probe消费可观察进度，保留旧完整Reply路径。
本次不更改配置摘要协议，合法原样例摘要仍为
`b037004b544ca834855821374bd65520a535b509c0b952d9c3f37a97a30d3b43`。

## 需求与验收映射

| requirement_id | dev_task / subset | test_id | 证据文件 | 状态 |
|---|---|---|---|---|
| REQ-016/017 | DEV-002 R102-A 客户端一次处理/无隐式重试 | TEST-OPS-01/02适用部分；独立test_no_hidden_second_attempt_on_disconnect；新增attempts测试 | before独立失败、after_02独立/added日志 | PASS（离线） |
| REQ-003/018 | DEV-002 R102-B 已观察响应事实与前缀保留 | 新增TEST-PROBE-EVIDENCE；独立头部/前缀检查 | test_probe_interruptions.py，独立9项、synthetic-interrupted | PASS（离线） |
| REQ-017 | DEV-002 总时限、字节上限、取消/清理 | TEST-OPS-02适用部分；新增中断回归 | 新增9项body测试、原probe测试 | PASS（离线） |
| REQ-001/011/015/016 | R1-01基础和R1-02既有行为不退化 | 原166项及R1-01独立15项 | after_02/original166.xml、independent01.xml | PASS |
| REQ-018 | 真实来源与运行预算 | ISSUE-003/004前置 | 历史fixtures/protocol/r1_02_run01 | REST BLOCKED；WS NOT_RUN |

## 实际执行

所有命令工作目录：`/Users/aries/Dve/btc_microstructure`。以下子命令退出码独立记录，执行器本身退出0不掩盖修复前pytest退出1。

| 阶段 | 命令 | 工作目录 | 退出码 | 状态；通过 / 失败 / 跳过 | 日志 |
|---|---|---|---|---|---|
| before | `.venv/bin/python -m pytest -q review/R1_02/checks/test_r1_02_regressions.py --junitxml=reports/round1/probe_fix/before/independent02.xml` | 项目根目录 | 1 | **FAIL；2 / 7 / 0** | `probe_fix/before/independent02.log/xml` |
| after_01 | `.venv/bin/python -c 'import platform,sys,aiohttp; print(platform.platform()); print(sys.version); print(aiohttp.__version__)'` | 项目根目录 | 0 | PASS；— | `reports/round1/probe_fix/after_01/environment.log` |
| after_01 | `.venv/bin/python -m pip list --format=json` | 项目根目录 | 0 | PASS；— | `reports/round1/probe_fix/after_01/dependencies.log` |
| after_01 | `.venv/bin/python -m pytest -q tests/unit/test_config.py tests/unit/test_foundation_guards.py tests/unit/test_probe.py tests/unit/test_probe_transport.py --junitxml=reports/round1/probe_fix/after_01/original166.xml` | 项目根目录 | 0 | PASS；166 / 0 / 0 | `reports/round1/probe_fix/after_01/original166.log` |
| after_01 | `.venv/bin/python -m pytest -q tests/unit/test_probe_attempts.py tests/unit/test_probe_interruptions.py --junitxml=reports/round1/probe_fix/after_01/added.xml` | 项目根目录 | 0 | PASS；16 / 0 / 0 | `reports/round1/probe_fix/after_01/added.log` |
| after_01 | `.venv/bin/python -m pytest -q --junitxml=reports/round1/probe_fix/after_01/default.xml` | 项目根目录 | 0 | PASS；182 / 0 / 0 | `reports/round1/probe_fix/after_01/default.log` |
| after_01 | `.venv/bin/python -m pytest -q review/R1_01/checks/test_review_regressions.py --junitxml=reports/round1/probe_fix/after_01/independent01.xml` | 项目根目录 | 0 | PASS；15 / 0 / 0 | `reports/round1/probe_fix/after_01/independent01.log` |
| after_01 | `.venv/bin/python -m pytest -q review/R1_02/checks/test_r1_02_regressions.py --junitxml=reports/round1/probe_fix/after_01/independent02.xml` | 项目根目录 | 0 | PASS；9 / 0 / 0 | `reports/round1/probe_fix/after_01/independent02.log` |
| after_01 | `.venv/bin/python -m btc_microstructure validate-config config/research.example.yaml` | 项目根目录 | 0 | PASS；— | `reports/round1/probe_fix/after_01/config.log` |
| after_01 | `.venv/bin/python -m btc_microstructure probe config/research.example.yaml --output reports/round1/probe_fix/after_01/network-disabled` | 项目根目录 | 2 | PASS；— | `reports/round1/probe_fix/after_01/network-disabled.log` |
| after_01 | `.venv/bin/ruff check src tests` | 项目根目录 | 0 | PASS；— | `reports/round1/probe_fix/after_01/ruff.log` |
| after_01 | `.venv/bin/ruff format --check src tests` | 项目根目录 | 1 | FAIL；— | `reports/round1/probe_fix/after_01/format.log` |
| after_01 | `.venv/bin/mypy src` | 项目根目录 | 0 | PASS；— | `reports/round1/probe_fix/after_01/mypy.log` |
| after_01 | `.venv/bin/python -m pip check` | 项目根目录 | 0 | PASS；— | `reports/round1/probe_fix/after_01/pip-check.log` |
| after_01 | `shasum -a 256 -c reports/round1/preserved.sha256` | 项目根目录 | 0 | PASS；— | `reports/round1/probe_fix/after_01/original-specs.log` |
| after_02 | `.venv/bin/python -c 'import platform,sys,aiohttp; print(platform.platform()); print(sys.version); print(aiohttp.__version__)'` | 项目根目录 | 0 | PASS；— | `reports/round1/probe_fix/after_02/environment.log` |
| after_02 | `.venv/bin/python -m pip list --format=json` | 项目根目录 | 0 | PASS；— | `reports/round1/probe_fix/after_02/dependencies.log` |
| after_02 | `.venv/bin/python -m pytest -q tests/unit/test_config.py tests/unit/test_foundation_guards.py tests/unit/test_probe.py tests/unit/test_probe_transport.py --junitxml=reports/round1/probe_fix/after_02/original166.xml` | 项目根目录 | 0 | PASS；166 / 0 / 0 | `reports/round1/probe_fix/after_02/original166.log` |
| after_02 | `.venv/bin/python -m pytest -q tests/unit/test_probe_attempts.py tests/unit/test_probe_interruptions.py --junitxml=reports/round1/probe_fix/after_02/added.xml` | 项目根目录 | 0 | PASS；17 / 0 / 0 | `reports/round1/probe_fix/after_02/added.log` |
| after_02 | `.venv/bin/python -m pytest -q --junitxml=reports/round1/probe_fix/after_02/default.xml` | 项目根目录 | 0 | PASS；183 / 0 / 0 | `reports/round1/probe_fix/after_02/default.log` |
| after_02 | `.venv/bin/python -m pytest -q review/R1_01/checks/test_review_regressions.py --junitxml=reports/round1/probe_fix/after_02/independent01.xml` | 项目根目录 | 0 | PASS；15 / 0 / 0 | `reports/round1/probe_fix/after_02/independent01.log` |
| after_02 | `.venv/bin/python -m pytest -q review/R1_02/checks/test_r1_02_regressions.py --junitxml=reports/round1/probe_fix/after_02/independent02.xml` | 项目根目录 | 0 | PASS；9 / 0 / 0 | `reports/round1/probe_fix/after_02/independent02.log` |
| after_02 | `.venv/bin/python -m btc_microstructure validate-config config/research.example.yaml` | 项目根目录 | 0 | PASS；— | `reports/round1/probe_fix/after_02/config.log` |
| after_02 | `.venv/bin/python -m btc_microstructure probe config/research.example.yaml --output reports/round1/probe_fix/after_02/network-disabled` | 项目根目录 | 2 | PASS；— | `reports/round1/probe_fix/after_02/network-disabled.log` |
| after_02 | `.venv/bin/ruff check src tests` | 项目根目录 | 0 | PASS；— | `reports/round1/probe_fix/after_02/ruff.log` |
| after_02 | `.venv/bin/ruff format --check src tests` | 项目根目录 | 0 | PASS；— | `reports/round1/probe_fix/after_02/format.log` |
| after_02 | `.venv/bin/mypy src` | 项目根目录 | 0 | PASS；— | `reports/round1/probe_fix/after_02/mypy.log` |
| after_02 | `.venv/bin/python -m pip check` | 项目根目录 | 0 | PASS；— | `reports/round1/probe_fix/after_02/pip-check.log` |
| after_02 | `shasum -a 256 -c reports/round1/preserved.sha256` | 项目根目录 | 0 | PASS；— | `reports/round1/probe_fix/after_02/original-specs.log` |

其他实际操作：
- `git switch -c fix/r1-02-protocol-probe`：退出0。
- `.venv/bin/python -m pytest -q review/R1_02/checks/test_r1_02_regressions.py`：首次修复后9通过，退出0；`independent02-initial-after.log`。
- `.venv/bin/python -m pytest -q tests/unit/test_probe_attempts.py tests/unit/test_probe.py tests/unit/test_probe_transport.py`：68通过，退出0；`initial-targeted.log`。
- `.venv/bin/ruff format tests/unit/test_probe_interruptions.py`：退出0，仅格式化本次新文件；首轮format FAIL完整输出保留。
- `.venv/bin/python reports/round1/probe_fix/verify_integrity.py`：退出0；243个内容文件/26个锁定版本一致；`integrity.json`。
- `.venv/bin/python reports/round1/probe_fix/render_synthetic.py`：退出0，生成SYNTHETIC中断前缀证据；不构造真实网络。
- `git diff --check`（未暂存源码）：退出0；新脚本的限定ruff检查退出0。
- `git diff --cached --check`：退出2（FAIL），诊断来自Markdown换行空格和必须逐字保留的失败日志/XML/diff空白；完整输出在staged-whitespace.log。没有为通过检查改写历史证据。
- `git diff --cached --check -- src tests`：退出0（PASS），源码与测试无空白错误，日志在staged-code-whitespace.log。
- `.venv/bin/python reports/round1/package_protocol_probe_fixed.py`：交付打包命令，实际退出0；归档CRC/逐文件哈希见 `.zip.verify.json`，交付验证记录见 `probe_fix/artifact-verification.log`。

最终：原166 **166通过/0失败/0跳过**；新增17 **17通过/0失败/0跳过**；默认合计 **183通过/0失败/0跳过**。
R1-01独立15 **15通过/0失败/0跳过**；R1-02独立9 **9通过/0失败/0跳过**。未删除或修改旧测试，没有skip/xfail。
`verify_offline.py`每次创建新的after_NN目录，保留初轮检查与历史失败，不覆盖旧证据。
网络未启用CLI的退出2表示NOT_RUN；其gate行为检查通过不代表真实接口通过。
限定静态检查为src/tests及本轮交付脚本，未宣称全仓历史审查脚本lint通过。

同会话只读复查已完成：源码/测试无阻塞问题，报告/ZIP/校验和的交付项经验证后关闭，结论APPROVE；详见 `probe_fix/code-review.md`。

## 数据证据

- 官方/SDK资料：仅核验本次修复所需aiohttp公开API及锁定源码；没有新增币安资料/现场访问。来源与时间在source_verification.json。
- 模拟：真实aiohttp请求循环+人工中间件在下游发送处理器前抛断连错误，socket/DNS被测试禁止；这证明处理链不会二次进入，不是对交易所实际发送的观测。
- 模拟前缀：`synthetic-interrupted/`标为SYNTHETIC/BLOCKED。HTTP200人工头已观察、正文中断，精确保留21字节，sha256为
  `4129dc3cd95c666526dc62dff63e4fb2c2a7b50114c58e65638ee304ce27c82a`。
  人工ClientSession没有进入真实aiohttp发送处理器，所以该样例handler计数0；其source字段明确说明fake session。实际循环计数由单独attempts回归验证。
- 历史审查：用户review/R1_02/evidence的Linux/Python3.13日志原样保留；本次before/after日志另存，明确原机Python3.12。
- 真实接口：**本次NOT_RUN**。原先唯一真实尝试的REST BLOCKED/TIMEOUT、WS NOT_RUN记录逐字节保留，原ZIP也保留。
  不能从旧TIMEOUT记录推断隐式重试发生过或未发生；旧常量retry_count不作为底层尝试证据，不事后改写。

## 失败、阻塞与限制

- FAIL（已复现并修复）：锁定环境独立7项失败，归属A/B两类问题；完整错误、源码前像和JUnit保留。
- FAIL（已修正）：after_01新增测试文件的格式检查；测试本身182通过。最终after_02增加总时限证据回归并格式化后183通过、全部限定检查通过。
- FAIL（完整性初检）：Finder元数据漂移；原错误和原保护快照保留，后续明确排除非内容OS文件，243个真实内容文件一致。
- PASS：离线R102-A/B、全部原/新增/独立测试、配置CLI、无网络gate和限定静态检查。
- BLOCKED：历史真实REST访问；WS NOT_RUN。修复不解除现场访问阻塞，不启用连续采集或把配置protocol_probe_completed改为true。
- NOT_RUN：本次真实探测、Linux/其他版本复跑、全新依赖环境安装、任何任务03及长期采集。没有新增依赖，没有修改网络/代理设置。
- 公开中间件与重试语义依赖锁定aiohttp3.12.15；未来升级必须重跑真实请求循环注入回归。不得添加覆盖session guard的请求级middleware。
- 请求处理链计数不等于TCP包/底层连接次数；未观察到的传输维度明确未知。
- 敏感异常、cookies、认证信息不序列化；本次日志和人工样例未发现真实秘密需脱敏。打包审计明确列出排除与人工例外。

## 用户复现

在项目根目录、已有原Python3.12虚拟环境执行：

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q tests/unit/test_probe_attempts.py tests/unit/test_probe_interruptions.py
.venv/bin/python -m pytest -q review/R1_01/checks/test_review_regressions.py
.venv/bin/python -m pytest -q review/R1_02/checks/test_r1_02_regressions.py
.venv/bin/python -m btc_microstructure validate-config config/research.example.yaml
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src
.venv/bin/python -m pip check
.venv/bin/python reports/round1/probe_fix/verify_integrity.py
shasum -a 256 -c reports/round1/probe_fix/implementation.sha256
```

预期默认183、新增17、独立15/9全部通过；配置摘要不变。完整新日志重跑：
`.venv/bin/python reports/round1/probe_fix/verify_offline.py`，该脚本没有网络启用开关。
单独gate检查须用不存在的目录：
`.venv/bin/python -m btc_microstructure probe config/research.example.yaml --output reports/round1/probe_fix/manual-disabled`，预期退出2、NOT_RUN。
不要使用--allow-network；本次修复不含任何真实访问命令。

重新生成人工前缀证据可指定新目录：
`.venv/bin/python reports/round1/probe_fix/render_synthetic.py reports/round1/probe_fix/manual-synthetic`。
ZIP：`.venv/bin/python reports/round1/package_protocol_probe_fixed.py`；
外部校验：`shasum -a 256 -c protocol_probe_review_fixed.zip.sha256`。
新环境安装使用原README精确lock流程；本轮全新安装NOT_RUN，遵守目标环境审批。
归档按项目根相对路径，含当前源码、全部测试、原规格、前后失败/通过证据和本报告；排除venv/Git/缓存/环境文件/密钥/大型数据。
外部`.zip.verify.json`在归档旁，避免循环摘要；旧清单属于历史版本，当前使用probe_fixed_package_manifest。

## 下一任务依赖

R1-02离线A/B修复结束后提交指定分支并创建指向main的PR，**不自动合并**。
真实运行前置仍受阻，本次不开始tasks/03_vertical_slice.md、不启动其他模块。
