# 03 · 特征计算规格

版本：0.1｜状态：数学与数据合同；预测能力未验证

## 1. 通用合同

本版默认 `as_received`，所有 W 窗口取 [t−W,t)。其他时间视图需要明确更换 time_view，不能复用同名输出文件。t 是特征截止，不是保证可执行的发布时间。

每行或其明确关联的 manifest 必须包含：instrument_id、feature_cutoff_ns、feature_available_ns、time_view、feature_version、config_hash、input_cursor、dataset_revision、source_scope、book_epoch（适用时）、quality_flags。

各特征使用单独 validity/age 信息；不得把某个可选来源失效扩大成删除整行有效观测。

缺失值统一 null；模型矩阵导出时可映射 NaN，但同时保留原因。窗口预热未满、已知缺口、零分母或无效价格不得填 0。**唯有“无已知缺口且没有成交”的成交量为 0。**

默认成交窗口 W∈{1,5,30,60,300,900} 秒，簿 OFI 窗口 {1,5,30,60} 秒，OI 窗口 {30,60,300,900} 秒。所有窗口与未来研究期限独立配置。

## 2. 特征目录

| 编号 | 家族 | 输出示例 | 依赖 |
|---|---|---|---|
| FEAT-001 | 成交量差 | trade_delta_base_30s | trades |
| FEAT-002 | 成交不平衡 | trade_imbalance_30s | trades |
| FEAT-003 | 累计量差、聚合消息强度、VWAP | cvd_segment_base、agg_message_rate_30s | trades |
| FEAT-004 | 最优价、价差、报价加权价 | mid、spread_bps、weighted_mid | BBO |
| FEAT-005 | 可见深度与不平衡 | depth_bid_base_10bps、obi_10bps | 有效 L2 |
| FEAT-006 | 观测簿 OFI | ofi_depth_base_30s | 有效连续 L2 状态 |
| FEAT-007 | OI 变化 | oi_change_fraction_300s | OI 观测 |
| FEAT-008 | 基差与费率上下文 | perp_index_premium_bps | BBO、mark/index、funding |
| FEAT-009 | 强平快照描述 | observed_liq_snapshot_count_60s | 强平观测 |
| FEAT-010 | 跨市场差异 | trade_imbalance_spot_minus_perp_30s | 两市场成交和报价 |
| FEAT-011 | 价格响应代理 | downside_response_per_sell_btc_30s | 成交、BBO |
| FEAT-012 | 过去收益与波动 | return_bps_60s、realized_vol_bps_300s | 有效 BBO |

## 3. 成交特征 FEAT-001/002/003

令 q_i 是 BTC 数量，p_i 是 USDT/BTC 价格，s_i=+1 表示主动买，s_i=−1 表示主动卖。

\[
B_W=\sum_{i\in[t-W,t),s_i=+1}q_i,\quad
S_W=\sum_{i\in[t-W,t),s_i=-1}q_i
\]
\[
V_W=B_W+S_W,\quad \Delta_W=B_W-S_W,\quad
TI_W=\frac{B_W-S_W}{B_W+S_W}
\]

数量单位 BTC；TI 无量纲且在 [−1,1]。V=0 时 TI=null，而 B、S、V、Delta 可以为 0。USDT 版本逐条使用 p_iq_i，不能用窗口末价乘全部历史 BTC 数量来冒充成交额。

`cvd_segment_base` 在拥有从UTC日初开始的完整输入时，从日初累计Delta；若日中启动且没有完整日初输入，以实际开始时点建立STARTUP segment，不冒充当日完整CVD；发生无法补回的缺口时开启新segment，不跨缺口继续宣称完整 CVD。记录 segment_id、anchor_ns、reset_reason。滚动 Delta 是固定窗口量差，不应含糊命名为“无限累计 CVD”。

`agg_message_rate_W = N_agg/W`；`mean_agg_size_base_W = V_W/N_agg`；N=0 时平均量 null。此平均量是聚合记录平均数量，不是真实单个订单平均大小。

\[
VWAP_W=\frac{\sum_i p_iq_i}{\sum_iq_i}
\]

分母为零则 null；跨市场分别计算。不得把同一消息 q 与 nq 重复相加；可选 q−nq 仅作为该来源中涉及 RPI 的聚合数量差，并要求 0≤nq≤q。

**固定样例：**主动买 0.30、0.20 BTC，主动卖 0.10 BTC：V=0.60，Delta=0.40，TI=2/3，N_agg=3。重复第二条同 ID 后上述值不变。

## 4. 报价特征 FEAT-004

设最优买价 b、卖价 a，对应 BTC 数量 q_b、q_a：

\[
m=(a+b)/2,\quad spread=a-b,\quad
spread_{bps}=10^4(a-b)/m
\]
\[
weighted\_mid=\frac{a q_b+b q_a}{q_b+q_a}
\]

m、spread、weighted_mid 的价格单位为 USDT/BTC。weighted_mid 是本项目指定的报价数量加权价格代理，不将它宣称为完整理论微价格模型或无偏未来价格估计。

默认从 BBO 取得，不能在缺数据时静默切换到最后成交价。簿派生同类字段使用 `book_*` 前缀，保持来源可追溯。

**固定样例：**b=100、a=102、q_b=3、q_a=1：m=101，spread=2，spread_bps=198.019801980198，weighted_mid=101.5。

## 5. 可见深度与 OBI FEAT-005

以有效本地簿的中间价 m_book 为中心，k 单位为 bps：

\[
D^b_k=\sum_{m(1-k/10^4)\le p\le b}q^b(p),\quad
D^a_k=\sum_{a\le p\le m(1+k/10^4)}q^a(p)
\]
\[
OBI_k=\frac{D^b_k-D^a_k}{D^b_k+D^a_k}
\]

默认 k∈{5,10,25}。边界包含等号，价位比较用 Decimal 或定点数。目标区间落入已知覆盖范围且总深度>0 才有效。不能以“已返回的档位加起来”为由忽略未覆盖区域。

可选 USDT 深度逐档求和 pq，名称必须带 `quote` 或 `usdt`；BTC 深度名称必须带 `base`。标准簿口径见数据契约，其不可见部分不是零。

**固定样例：**已知范围内买深度 3 BTC、卖深度 1 BTC，OBI=0.5；同样可见数值但覆盖不足时结果必须为 null。

## 6. 观测簿 OFI FEAT-006

本版采用 Cont–Kukanov–Stoikov 的最优报价状态差分形式。[S10](08_sources_and_decisions.md#source-s10) n 表示原子应用一次有效 depth 消息后的簿状态：

\[
e_n=
\mathbf{1}_{b_n\ge b_{n-1}}q^b_n
-\mathbf{1}_{b_n\le b_{n-1}}q^b_{n-1}
-\mathbf{1}_{a_n\le a_{n-1}}q^a_n
+\mathbf{1}_{a_n\ge a_{n-1}}q^a_{n-1}
\]
\[
OFI_W=\sum_{n:\,t-W\le\tau_n<t} e_n
\]

输出 `ofi_depth_base_W`，单位 BTC。τ 为该观测更新在所选视图的时间。首个有效状态仅作为基准，不凭空产生 e。发生重同步后重置基准，跨 epoch 的窗口无效。

**重要适配限制：**这里 n 是公开聚合深度的观测更新，不是交易所全部逐订单事件。100ms 区间内的中间路径可能不可见，因此不得把本指标称为完整撮合事件 OFI。更改 depth 频率需要更换来源版本并单独检验。

仅比较窗口起点和终点的盘口数量不等于上述求和。q 的减少也不能唯一划分为撤单或成交。本版不实现声称准确的 cancel_rate 或逐订单 replenishment_rate。

**固定样例：**买卖价均不变，买量 2→5、卖量 4→3：e=(5−2)−(3−4)=4 BTC。买价改善时使用新买一数量；卖价改善时使用新卖一数量并取负号。

## 7. OI 特征 FEAT-007

O(t) 为截至 t 已知的最新有效同口径 OI 原值：

\[
\Delta O_W=O(t)-O(t-W),\quad
\Delta O^{rel}_W=\frac{O(t)-O(t-W)}{O(t-W)}
\]

基准为零、端点过期、单位版本变化或区间跨已知不可解释缺口时，结果为 null。只有一条观测时不计算变化。过去端点必须按当时的可用记录匹配，不能选择后来发布但源时间较早的记录。

输出 `oi_change_native_W`、`oi_change_fraction_W`、`oi_unique_observations_W`、两端 age 与单位。比例是小数：0.01 表示 1%。

native 相同不等于“没有任何开平仓”；它只意味着可见净总量没有改变。重复轮询同一来源时点不得增加 unique_observations。

**固定样例：**OI 原量保持 100，价格从 100→110，若单位为 BTC，则估值从 10000→11000 USDT，但 OI 变化仍为 0，不能记录 10% 新仓增长。

## 8. 价格偏离与资金费率 FEAT-008

有效永续 BBO 中间价 m_p、同一时点可用指数 i、标记价 mark：

\[
perp\_index\_premium_{bps}=10^4(m_p/i-1)
\]
\[
mark\_index\_deviation_{bps}=10^4(mark/i-1)
\]

这里的简单相对价差是本项目特征，不等同于交易所资金费率公式中的官方 Premium Index。不得把两个概念同名混用。

保留 `funding_rate_observed`、`next_funding_time` 和明确可用的规则周期。已结算费率特征只使用截至决策已取得的结算记录。

可选 `settled_funding_z_30d`：仅取过去 30 日内、当时已取得、结算周期一致的**不同结算事件**，至少 30 个；均值与样本标准差不含待评价的当前结算事件。std=0、样本不足或周期未知则 null。不能把每秒重复的同一费率当数千个独立样本。

需要按小时比较时，只对周期已知的费率计算 `rate/interval_hours`，说明是线性折算率，不称为保证年化收益；周期变化分组或版本化。

## 9. 强平观测 FEAT-009

默认输出：

```text
observed_long_liq_snapshot_count_W
observed_short_liq_snapshot_count_W
max_observed_long_liq_snapshot_notional_usdt_W
max_observed_short_liq_snapshot_notional_usdt_W
liquidation_source_coverage
```

count 是去除可确认重发后的快照消息数量，不是唯一强平订单数。max 是窗口内单条 z×ap 的最大有效值，不是合计。

健康且无观测消息：count=0、max=null，另有 `has_observed_liquidation=false`。连接缺口：count=null。这样不把“没有观测”写成“证明没有强平”。

不实现未经校正的全量强平量、强平率或 leverage-at-risk。第一版强平特征默认不作为分类器必需输入。

## 10. 现货与永续 FEAT-010

默认比较同一接收窗口上的标准化成交不平衡：

\[
TI^{diff}_W=TI^{spot}_W-TI^{perp}_W
\]

并保留两市场各自成交量。不要仅对无限累计的两条 CVD 作差后断言谁主导价格；两市场规模、聚合规则和起点可能不同。

\[
spot\_perp\_basis_{bps}=10^4(m_p/m_s-1)
\]

两侧报价年龄和时间差必须通过配置门槛。现货缺少源时间时，该字段仅说明本机同一可用时点所见价差。

lead-lag 研究作为报告层任务，以等间隔收益、滞后相关或预先指定模型实施；同时报告时钟、延迟、样本区间。相关先后不能单独证明因果或永久价格领导权。

## 11. 价格响应代理 FEAT-011

使用窗口中间价对数收益 r_bps，定义两个仅作描述的非负比率：

\[
D_W=\frac{\max(0,-r_{bps,W})}{S_W},\quad
U_W=\frac{\max(0,r_{bps,W})}{B_W}
\]

分母≤0 则 null；有效时单位 bps/BTC。该比率不是已识别的因果价格冲击，不应被当作市场冲击成本模型。

“卖方流量大但下行响应小”可成为吸收候选事件的输入，必须同时检查数据质量与价格路径。不得使用 `1/z(abs(return))` 之类可能变号或分母接近零的未经论证打分。

## 12. 过去收益与波动 FEAT-012

\[
r_{bps,W}=10^4\log\frac{m(t)}{m(t-W)}
\]

m 为当时可用、年龄合格的同来源 BBO 中间价。基础 1 秒对数收益 r_j 在完整窗口上计算：

\[
RV_{bps,W}=10^4\sqrt{\sum_j r_j^2}
\]

这是该窗口的已实现波动代理，不年化。任何必需报价缺失则对应窗口 null，不能用长时间前向填充产生一串零收益。

## 13. 标准化、阈值与预热

所有均值、标准差、分位数和截尾阈值必须来自决策时点之前的数据。训练折内估计的静态阈值在验证／测试折冻结；在线滚动阈值只能使用过去数据，并明确窗口与最少样本。

原始特征不被标准化值覆盖。默认 std 用 ddof=1；常数列标准化为 null 并标识，不偷偷加任意 epsilon。确需 epsilon 的公式必须在特征版本中明确单位和理由。

分位数插值方法固定为 linear。预热应按真实连续覆盖时长，而不是仅按 DataFrame 行数判断。

## 14. 实现输出格式

研究基础表每个市场每秒一行；跨市场特征单独按 cutoff 对齐。建议每家族共享一个质量结构，必要时逐字段覆盖：

```text
feature_cutoff_ns | feature_available_ns | instrument_id | time_view
mid | spread_bps | trade_delta_base_30s | trade_imbalance_30s
ofi_depth_base_30s | obi_10bps | oi_change_fraction_300s
perp_index_premium_bps | funding_rate_observed
trade_quality | bbo_quality | book_quality | oi_quality | funding_quality
```

收益标签、模型概率与未来 MFE/MAE 不得写入供特征引擎读取的输入表。训练集拼接发生在研究模块，并由列白名单控制。
