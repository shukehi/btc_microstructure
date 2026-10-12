# R2-02：探测器超时分层与最小离线诊断

任务：Issue #14；Refs #12。基线`fd4fbd73fc6a7c0c2a4cff77ba97492dfdc56525`，分支`fix/r2-02-probe-timeouts`。本任务不授权真实探测，所有新增输入均SYNTHETIC；旧A1现场HTTP200但正文不完整/BLOCKED不改写。

## 实现前参数映射与兼容策略

| 参数 | 旧计时位置 | 目标计时位置 / 兼容要求 |
|---|---|---|
| total_seconds / --seconds | 全局工作total−1，清理最多1s | 单调全局工作截止保持2–60s总范围，REST→WS不重新发放；shell/落盘非硬实时上限 |
| connect_seconds | run_probe包围整段metadata；session HTTP total/connect/sock_connect及WS握手，默认5s、最大10s | 继续连接获取/新连接及WS握手；新REST参数省略时仍回退旧5s完整REST行为 |
| rest_total_seconds / --rest-total-seconds | 无 | 可选，严格有限正数≤20s；REST请求开始起算、含连接/响应头/完整正文，取与剩余全局工作截止较早者，不逐chunk续期 |
| idle_seconds | HTTP sock_read、WS完整消息等待，默认5s、最大10s | 保留：HTTP是peer一部分数据的读取空闲，WS是完整消息等待，不称连接超时 |
| HTTP ClientTimeout | total=connect，可能与编排超时重叠 | 显式REST配置接HTTP total，connect/sock_connect/sock_read仍有界；WS不得继承REST阶段剩余计时 |
| cleanup | finally最多1s | 保留外部取消传播、快照和有界清理，清理失败不能PASS |

候选30/5/15/5/1仅为本任务离线验证组合，不是新默认或联网授权。旧命令省略新参数、旧注入transport方法签名/原报告字段含义和原安全界限保留。新字段只追加配置/有效时限及安全阶段/来源枚举；没有阶段观测的旧transport应UNKNOWN，不伪造DNS/TLS/代理/地区归因。

## 修改前真实离线对照（已保存，非现场）

在固定基线、复用Python3.12.13锁定环境与当前checkout/src，执行`PYTHONPATH=src .venv/bin/python reports/round2/probe_timeouts/old_timeout_control.py`，退出0，日志`probe_timeouts/before/old-default.log/exit`。实际AioTransport.metadata分块读路径，socket禁止；人工每0.03s到块，连接/旧REST预算0.06s，空闲0.05s。完整元数据需要更久，旧行为HTTP200、100字节前缀、BLOCKED/TIMEOUT、WS0、cleanup PASS；HTTP ClientTimeout.total确为0.06s。退出0表示对旧保护行为断言成立，不是将受阻改成成功。

历史真实REST保持A1的BLOCKED，WS NOT_RUN、单位UNKNOWN、持续预算UNVERIFIED、连续采集/24h NOT_RUN。

## 最终实现与报告兼容

源码只改cli.py / probe.py / probe_transport.py；新增默认发现的`tests/unit/test_probe_timeouts.py`及README说明、本报告/小型证据。省略rest_total_seconds时依然使用旧connect预算和原REST调用参数；显式时run_probe取REST请求开始点+预算与全局工作截止的较早者，传给HTTP per-request ClientTimeout.total。HTTP connect/sock_connect/读取空闲不取消；session默认total仍为连接预算，只有REST请求覆盖total，因此WS不继承REST剩余计时。

全局截止用`asyncio.get_running_loop().time()`，全程只有一个工作域；REST、WS握手和每次完整消息等待分别取较早截止。绝对截止不会在每个HTTP chunk重新开始。原2–60s全局/最大10s连接与idle/2MiB正文/64KiB单消息/20消息/最多1s清理上限未扩大，零重试/重连、固定只读URL、禁止重定向、trust_env=False、DummyCookieJar/TLS保持。

`schema_version`继续probe-0.2，仅追加字段，旧字段含义不改；limits增加可选rest_total_seconds（省略为null）。新字段：configured_timeouts明确配置/回退模式；effective_timeouts列工作、REST及实际启动的WS阶段的有效预算与cap_source；rest/ws的timeout_diagnostics分四种事实：

| 字段 | 事实来源 / 不能推断的内容 |
|---|---|
| phase | 编排器当前等待操作，如REST_HEADERS_OR_BODY、WS_CONNECT、WS_RECEIVE；不冒充底层已观察到响应头 |
| source | **只**由owned asyncio.Timeout.expired()确定；未到期UNKNOWN、同时到期MULTIPLE_EXPIRED。配置cap_source的SHARED_DEADLINE表示相同截止配置，不表示谁触发 |
| transport_phase | AioTransport实际观察的REST_WAIT_HEADERS_OR_CONNECT / REST_READ_BODY或WS操作；旧transport无观察则明确UNKNOWN |
| transport_source | 捕获的公开ConnectionTimeoutError / SocketTimeoutError给出AIOHTTP_CONNECT_TIMEOUT / AIOHTTP_SOCKET_READ_TIMEOUT；普通TimeoutError为PYTHON_TIMEOUT_UNKNOWN，外层取消无特定传输异常则UNKNOWN |

公开typed异常可先于本层scope到期，所以source=UNKNOWN且transport_source明确并不矛盾。WS转换时清除旧REST诊断，typed超时在broad ClientError前捕获；不记录原异常文本。没有根据elapsed、阶段或状态200猜测具体计时器/网络/DNS/TLS/代理/地区原因。

HTTP sock_read是读取peer部分数据的空闲，而WS ClientWSTimeout.ws_receive是完整消息等待；单调elapsed/UTC归档分开。aiohttp底层计时及事件循环调度有精度/取消开销，编排器绝对截止继续有界；不声称硬实时或TCP发包计数。headers_received_ns仍为首个响应头观察，response_received_ns只是保留观察时刻。部分正文/白名单头/hash仍保存且不得进入完整metadata验证/WS；外部CancelledError按原约定传播，仅能取得当时快照，不强造完整CLI落盘成功。

## T01–T07实际证据

下表新节点均在`tests/unit/test_probe_timeouts.py`，最终新增23个实际case（20个函数，CLI无效参数4组）。默认原294项及独立15/9/8/4原文未改，新增合计默认317。所有计时案例均socket阻断/可注入transport或假session；真正AioTransport的参数与分块reader路径被执行，没有另写网络探测器或使用真实端点。

| 验收 | 当前实际节点 / 独立预期 | 结论 |
|---|---|---|
| T01 兼容/参数 | keep_old_default_and_reject_invalid_rest_total_values、cli_rest_total_seconds_reaches_limits_before_transport、cli_rejects_invalid_rest_total_seconds_before_transport[0/21/nan/inf]；模型另拒绝负数/True/非有限。候选映射test_report_records_candidate_timeout_mapping，HTTP request timeout与WS独立选项实际捕获 | PASS；无效输入在构造transport前拒绝；旧model/安全上限不改 |
| T02 慢完整 | run_probe_uses_rest_total_for_metadata_without_changing_legacy_default比较旧/新；aio_transport_slow_complete_rest_body_can_pass_with_explicit_rest_total实际分块完整metadata+有效WS | PASS；0.025s每块<0.05s idle，完整0.05s>0.03s连接/旧REST且<0.2s显式REST。与before旧0.06s中断对照分开，不接受部分JSON |
| T03 REST总超时 | aio_transport_rest_total_timeout_retains_prefix_and_blocks_ws：连续0.035s块，REST总0.055s、idle0.2s，前缀保留、source=REST_TOTAL、观察为读正文、无WS | PASS；不让每个块续命。前缀/null/阶段为固定断言，无放大数值容差 |
| T04 连接/等头/空闲 | uses_rest_request_timeout_without_leaking_it_to_ws检查HTTP total/connect/sock_connect/sock_read；records_connection_timeout_before_headers、records_header_wait_socket_timeout_before_body、records_socket_read_timeout_as_body_phase及unattributed_transport_timeout_keeps_unknown_source | PASS；真实公开异常类型注入，连接与读空闲的证据不同，等头没有伪造body/header观察，未知计时器仍UNKNOWN |
| T05 全局/WS/清理/取消 | global_deadline_shorter_than_rest_total_stops_before_ws（global work1s<REST5s）；ws_receive_keeps_global_elapsed_budget_after_rest_success；ws_handshake_timeout_reports_connect_phase；actual AioTransport WS connect/receive typed diagnostics；cleanup_timeout_blocks_otherwise_successful_probe（close挂起被1s取消且不能PASS）；原test_external_cancellation_propagates_after_snapshot_and_cleanup | PASS；全局不重置、握手/完整消息等待受限、取消时response上下文退出/清理，外部取消不吞掉 |
| T06 原保护 | 原probe默认77项+独立R1-02的9项，以及默认全集/独立四组；200部分JSON、帧/正文超界、429/418/451/拒绝、重定向、真实aiohttp请求循环隐式重试注入、schema、无网络gate均保留 | PASS；省略新参数旧断言仍成立；不更改旧测试结构/预期，不新增skip/xfail |
| T07 保存/证据 | save_probe_persists_timeout_limits_and_offline_diagnostics与save_probe_persists_blocked_timeout_diagnostics验证成功/受阻的configured/effective/diagnostics、样本hash及budget回读；synthetic-readback/另保存最小人工PASS/REST_BODY超时JSON | PASS；SYNTHETIC、quantity_unit=UNKNOWN、持续预算UNVERIFIED_FOR_CONTINUOUS_COLLECTION和continuous=false不升级 |

短时参数是为离线确定性场景缩小连接/REST/idle，不是全部参数等比例缩放；全局下限2s保持。fake session测试不声称在真实TCP上测得aiohttp所有计时精度；它们验证实际库参数映射、AioTransport reader/取消/保留路径与编排行为，typed超时仅为人工注入。CLI gate集成测试使用假transport（即使命令参数含网络flag也无真实请求）；不会把这些输出作为现场LIVE证据。人工保存样本仅用于回读测试。

## 实际命令、环境与迭代记录

复用既有锁定venv，Python3.12.13/macOS Darwin arm64、aiohttp3.12.15、PyArrow20.0.0；27个requirements-dev.lock条目逐项相等，无安装/升级。当前checkout以`PYTHONPATH=src`导入；环境在after-03/environment.json，测试源码hash在tested-source-sha256.json。所有cwd为本任务隔离checkout，不混入原工作区未提交规则。

| 命令 / 阶段 | 真实退出 / 数量 | 保存位置 |
|---|---|---|
| 旧默认对照`PYTHONPATH=src .venv/bin/python .../old_timeout_control.py` | 0，旧受阻行为断言成立 | before/old-default.log/exit、receipt.json |
| 原probe兼容定向5组（含独立R1-02） | 0，86 passed | after-01/original-probe-compat.log/xml/exit |
| `PYTHONPATH=src bash .github/scripts/ci_checks.sh`首轮 | 0，默认312/独立15/9/8/4、十三项0 | after-01/各log/XML/results.tsv/commands.json |
| 同命令第二轮 | 0，默认315/独立15/9/8/4、十三项0 | after-02/；新增边界后、最终诊断细化前的中间结果 |
| 新诊断开发中attempt03定向 | **20通过/3失败**；shell tee管道回报0不能视为pytest通过，单独pytest退出未可靠捕获；JUnit确认3 failures | test-results/test-probe-timeouts-attempt03.log/xml；保留失败，不当验收结果 |
| 修订后定向attempt04 | 0，23 passed | test-results/test-probe-timeouts-attempt04.log/xml |
| **最终同一CI脚本第三轮** | **0，默认317、独立15/9/8/4全部0fail/error/skip；十三项均0** | **after-03/**，每项准确argv/cwd/退出在commands.json |

初始18项只有工作lane工具输出，不冒充独立归档；已落盘的20/21项、probe组合97及后续attempt04记录均保留；没有覆盖中间失败。attempt03包含WS typedtimeout待修路径和新诊断字段契约尚未对齐的断言：已修WS路径，并将本轮新测试明确为“owned source”与“transport typed source”分别断言。没有为了通过把未知owned scope推断成CONNECT/IDLE，也没有更改原独立检查/旧测试。证据helper初次被直接Ruff检查发现E701，已仅格式化；修复前日志/判定不回写，helper直接Ruff随后通过。

最终十三项包含默认/四组独立、原Parquet缺口脚本、Ruff lint/format、Mypy、pip check、原研究配置/离线配置校验、CLI/Parquet smoke。Ruff/Mypy/pip/config/E2E均退出0；旧固定Decimal、两replay/raw-only build摘要一致。脚本本身、工作流/锁文件没改，新回归在现有默认发现范围，CI自动执行。本机Linux网络命名空间为NOT_RUN，socket阻断及人工传输为本机离线证据；hosted Linux隔离由最终PR Actions日志单独核对，不冒充本机能力。

技术依据（本次读取锁定版本官方英文资料，不读取交易所）：[aiohttp3.12.15 ClientTimeout](https://docs.aiohttp.org/en/v3.12.15/client_reference.html#aiohttp.ClientTimeout)、[Python3.12 asyncio timeout_at/expired](https://docs.python.org/3.12/library/asyncio-task.html#timeouts)。前者定义whole-request、连接及部分数据读取职责和公开异常；后者定义事件循环绝对截止/expired证据。工程参数上限由Issue规定，不由文档或模拟结果代表交易所额度。

## 复查与完整性

采用code-review技能的code/spec/security与架构两个只读lane。native architect的安装模型在当前账户不可用，改由独立只读CLI同一architect prompt完成，未由作者自审替代；会话回执在review/。首轮架构WATCH指出旧transport未知阶段和WS typedtimeout被broad ClientError掩盖，修订后复核**CLEAR**。code-reviewer初查未发现HIGH代码问题，要求补齐本报告及helper格式；该交付项在完成后再次复核，结果另存review/。这些同任务上下文隔离检视不替代后续GitHub独立复核，也不授予合并权限。

原用户AGENTS的内容哈希不变，未stash/reset/clean或切换原目录。受保护原测试/独立检查/配置/规格/lock/工作流/Core/存储、R2-01-A1全部历史LIVE证据保持字节不变；本地原始425,307B前缀也未改或上传。新证据路径仅归档小型脱敏文本/JUnit和明确标为SYNTHETIC的人工样本，不含完整环境/私人路径/真实秘密/工程ZIP。日志归档副本只作路径脱敏和行尾空白规范，原始日志仍留本地ci-results/data。

## 分项结论、复现与停止

- R2-02参数分层/最小诊断/T01–T07离线结果：**PASS（本任务范围）**，待独立复核；完整系统/真实接入验收不升级。
- 最近真实REST仍为A1 HTTP200、425,307B不完整正文的**BLOCKED**，WS **NOT_RUN**；本任务**没有新的真实请求**、网络/代理/TLS变更或真实现场诊断。
- 数量单位 **UNKNOWN**，持续预算 **UNVERIFIED**，连续采集/有界实采演练/24h **NOT_RUN**，研究/收益能力未实现/未验证。

离线复现：已安装的Python3.12锁定环境下运行`.venv/bin/python -m pytest -q tests/unit/test_probe_timeouts.py`及当前`bash .github/scripts/ci_checks.sh`（必须新输出目录/干净checkout，保留既有ci-results）；本机复用其他venv时加`PYTHONPATH=src`。最终PR精确SHA/Actions回执见PR正文及交付答复；hosted绿色不等于现场REST通过。

本PR关联Issue #14且Refs #12，**不Closes #12**；不强推、不合并、不关闭Issue12、不再次真实探测。只有代码独立复核并经用户授权合并后，才可在Issue12提出新的精确版本/目标环境/单次额度计划，并取得新联网授权；本任务不执行此后续步骤，交付后停止等待独立复核。
