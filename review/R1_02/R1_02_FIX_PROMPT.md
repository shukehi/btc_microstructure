# R1-02 修复任务：隐式重试与中断响应证据

先读取 `AGENTS.md`、`tasks/02_protocol_probe.md`、`review/R1_02/R1_02_Independent_Review.md`，检查当前工作区和解释器。

本次只修复 R102-A/B。R1-01 不重做；不执行任务03、不添加采集/特征/账户模块、不改变网络配置，不再次访问币安。

## A. 明确控制客户端隐式重试

在原 Python 3.12/aiohttp 3.12.15 环境复现 `ServerDisconnectedError` 与相关客户端断连异常，覆盖 REST 和 WS 握手。独立审查运行于3.13.3，但已读取3.12.15官方源码确认同类重试分支；不能把不同版本的审查运行伪装成原机执行。

阻止客户端隐式第二次发送尝试，不能仅声称“代码没有循环”，也不能只将报告改成另一个常量。优先公开接口、可测试的处理方式；任何私有API依赖必须说明版本、测试和失败保护。不要为此任意升级依赖或放宽“不重试”的合同。

报告的逻辑调用、实际尝试和重试口径需清楚且与实际行为一致。已有真实 TIMEOUT 记录不改写为“发生过/没发生过隐藏重试”的新结论。

## B. 保留已收到的状态、响应头与部分正文

HTTP头到达后立即保留状态和白名单预算头。正文超时、损坏或读取失败时，不丢失已观察到的信息；没有观察到的字段仍留空。

对已读取的有界正文前缀保留字节、哈希、不完整标记。不能把部分JSON当完整元数据，也不能把“已经读取部分”报告为“收到0字节”。明确拒绝后可以立即停止正文读取；此时不要虚构未读取的前缀。

保留总时限、字节上限、可靠清理、无WS回退、无真实密钥/敏感异常泄露等要求。超时处理不得无限等待或吞掉外部取消。

## 执行与交付

先运行 `review/R1_02/checks/test_r1_02_regressions.py`，保留修复前真实输出。独立测试使用人工源和禁网保护；其中重试测试保留真实 aiohttp 请求重试状态机，不访问外部服务器。

保留原规格、配置、原166项测试、原R1-01独立15项检查和所有历史证据。根据两项问题添加实现侧回归，不改预期制造通过。若需改变返回接口，应记录对应版本和调用方，不能隐瞒破坏性变化。

修复后实际运行：

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q review/R1_01/checks/test_review_regressions.py
.venv/bin/python -m pytest -q review/R1_02/checks/test_r1_02_regressions.py
.venv/bin/python -m btc_microstructure validate-config config/research.example.yaml
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src
.venv/bin/python -m pip check
```

另用新的输出目录检查未给 `--allow-network` 的probe仍为NOT_RUN/预期退出2；不要复用现有证据目录。每条命令保存真实退出码和日志，原测试/新增测试数量分开报告。校验原材料未改写。

交付：
- `reports/round1/02_protocol_probe_fix.md`：修复前后行为、实际环境、版本、测试映射、限制、未完成项。
- `protocol_probe_review_fixed.zip` 和对应 SHA-256；包含当前源码、测试、原规格和前后证据，排除虚拟环境、密钥、缓存及无关大文件。

离线修复通过不等于真实API通过。现场仍保留 REST BLOCKED、WS NOT_RUN，除非后续独立授权的现场核验提供新证据。本次修复交付后停止，不执行R1-03。
