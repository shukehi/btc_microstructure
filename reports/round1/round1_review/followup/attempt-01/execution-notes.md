# 准备与证据边界

新 worktree / Python3.12 venv；十三项整轮检查只运行一次，ci-results 原先不存在。真实数据、venv、原始运行数据不提交。GitHub main run/PR1审查首次读取出现TLS handshake timeout，有限重试成功；没有 Binance 请求，不推断历史 REST 超时原因。

新只读CLI session 01a12e95-9bee-7123-a563-3c2edee8820c 未继承实现聊天/记忆，负责1/2/3/5/7；native两slice负责4/6/9与8/11/12，没有另开多层审查流程。CLI发现废弃配置项被忽略，未修改全局设置。CLI纯内核首次因只读沙箱临时捕获文件失败，退出1且未执行测试；关闭捕获后22 passed/8 deselected，退出0。一个rg查找无匹配退出2也保留在fresh-cli-receipt.json；这些不归类为业务缺陷。

归档准备第一次复制目录曾未留存（原因未确认，所有原始ci-results/data日志与历史报告仍在）；待检查结束后从原始日志重建本attempt，未重跑/编造测试结果。确认74个原报告/证据文件字节不变。后续仅新增文件，禁止清理共享工作目录。

日志中的本机绝对路径已替换<review-checkout>；原始执行目录为任务隔离checkout，不上传完整环境变量/配置/CLI轨迹。本机Linux网络命名空间NOT_RUN；hosted run独立验证。原报告FAIL与历史真实REST BLOCKED不改写。本次新脚本仅补缺少明确动态证据的控制故障3案例，未纳入默认pytest/CI选择，不更改原suite。

归档privacy初检曾误读pytest生成的忽略pyc导致UnicodeDecodeError，现仅归档文本证据，pyc/缓存不提交；diff初检发现原工具日志空白行尾空格，归档副本仅规范行尾空白，完整原始日志仍保留在本地ci-results/data目录，数值/错误/退出码不改。
