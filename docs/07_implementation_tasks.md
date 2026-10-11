# 07 · 实现任务与模块合同

版本：0.1｜任务状态初始均为 NOT_STARTED；不代表已有代码

## 1. 任务顺序

| 任务 | 交付 | 依赖 | 完成条件 |
|---|---|---|---|
| DEV-001 | 配置模型、枚举、配置校验 | 无 | CFG测试通过，未知值不静默接受 |
| DEV-002 | 协议probe与端点预算登记 | DEV-001 | 样本、元数据、路由和预算证据 |
| DEV-003 | 事件封装、原始日志、manifest | DEV-001 | STORE与崩溃恢复测试通过 |
| DEV-004 | 永续/现货成交与BBO适配器 | DEV-002/003 | 方向、单位、去重和时钟测试 |
| DEV-005 | OI、mark/index与资金表 | DEV-002/003 | OI、资金和过期测试 |
| DEV-006 | 控制日志与简单确定性重放 | DEV-003/004 | 相同输入一致，不读取未来数据 |
| DEV-007 | 永续L2同步与检查点 | DEV-002/003/006 | BOOK全套测试与重放对照 |
| DEV-008 | 第一批特征与逐家族质量 | DEV-004/005/007 | FEAT与QUALITY测试 |
| DEV-009 | 强平补充适配器 | DEV-002/003 | 不输出伪造总量，LIQ测试通过 |
| DEV-010 | 状态与多期限标签 | DEV-008 | 事件可用时点、路径和截尾测试 |
| DEV-011 | walk-forward与报告 | DEV-010 | 泄漏、重叠、全部期限披露测试 |
| DEV-012 | 成本敏感性模块 | DEV-011＋成本配置 | COST测试通过，毛净分离 |
| DEV-013 | 健康检查、受控故障、24h验收 | 各基础模块 | 有证据的运行验收报告 |

DEV-009 是补充支线，不阻塞不依赖强平的研究。不能反过来先开发复杂状态分类器，再补原始数据与时间规则。

## 2. 模块输入与输出

| 模块 | 输入 | 输出／副作用 |
|---|---|---|
| Collector | 配置、端点预算 | RawEnvelope和连接控制事件；不做策略计算 |
| Normalizer | RawEnvelope、元数据 | 标准记录／隔离记录；不丢失raw_ref |
| Deduplicator | 标准记录 | 唯一记录与重复／冲突审计 |
| BookEngine | 快照、增量、控制事件 | BookState、epoch、覆盖、有效性 |
| StorageWriter | 有界批次 | 文件与原子提交manifest |
| FeatureEngine | 截止高水位、已知历史 | 带available时点的特征快照 |
| StateEngine | 特征快照、冻结阈值 | 描述标签、候选事件、原因码 |
| LabelBuilder | 发布事件、后续价格 | 独立label表；不得反馈给FeatureEngine |
| ResearchRunner | 输入manifest、预登记研究配置 | 全候选报告、模型与结果manifest |

组件间传递带类型的记录或不可变快照，不共享可被研究线程随时修改的全局字典。适配器只负责来源语义，通用计算不包含散落的交易所字段名。

## 3. 项目目录建议

```text
btc_microstructure/
  config/
  src/btc_microstructure/
    config_models.py
    adapters/binance_um.py
    adapters/binance_spot.py
    events.py
    metadata.py
    storage/
    book/
    features/
    states/
    research/
    operations/
  tests/unit/
  tests/protocol/
  tests/replay/
  tests/integration/
  fixtures/synthetic/
  fixtures/protocol/
  docs/
```

协议样本与合成样本分开；日志和真实数据不直接提交进公共仓库。文档配置样例可作为模型开发的输入，但不能替代配置schema。

## 4. 单个开发任务的验收模板

```text
task_id:
requirement_ids:
input_contracts:
output_contracts:
error_behavior:
test_ids:
fixture_manifest:
implementation_commit:
verification_result: NOT_RUN / PASS / FAIL
known_limitations:
```

提交代码时必须附带需求和测试编号，说明是否改变原始字段含义、窗口边界或数据可用时点。改变公式需要更新 feature_version 和对应固定预期。

## 5. 首个实现切片

先完成“一个永续聚合成交流 → 原始日志 → 标准化与去重 → 一个30秒Delta特征 → 保存 → 离线重放”的最小切片。它应包括重复消息、断线标记与边界测试，而不只是打印行情。

在该切片正确之后加入现货与OI，再构建L2同步。首个切片不输出买卖信号，不假装完成整个系统。

## 6. 尚待实施确认的内容

实际部署系统和资源、端点的现场访问能力、当前元数据精度、OI绝对单位证据、资金结算周期证据、真实流量峰值与历史文件字段版本，均在M0/M1中生成证据。它们不妨碍本版规格成稿，但相应依赖未关闭时不能宣布软件可用于正式研究。
