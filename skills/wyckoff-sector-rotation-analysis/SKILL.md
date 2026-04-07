---
name: fingenius-sector-wyckoff-analysis
description: |
  Analyze A-share sector rotation with the FinGenius Wyckoff sector specialist. Use this skill whenever the user asks for 行业轮动, 一级行业资金流入, 板块资金面, 威科夫量价分析, 板块强度, 离散度, 主线板块, 细分板块筛选, 核心标的, or wants to know which sectors are healthiest right now. Trigger this skill even if the user phrases it loosely, such as “看看今天哪些行业最强”, “按资金流和量价找主线”, “用威科夫分析板块”, “找低离散高强度方向”, or “从一级行业到细分板块挑龙头”.
argument-hint: "[市场范围] [时间尺度] [偏好：一级行业/细分板块/核心标的]"
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - WebSearch
---

# FinGenius Sector Wyckoff Analysis Skill

This skill performs a top-down A-share sector scan using a strict 威科夫量价框架.

It must cover the full chain:

1. 一级行业资金流与量价状态
2. 一级行业威科夫阶段判断
3. 强度与离散度评分
4. 细分板块筛选
5. 核心标的挑选

Do not jump directly to stocks. The sector-level pass is mandatory.

## Use this skill when

- The user asks for 行业轮动、板块主线、资金流入行业、市场主攻方向.
- The user wants 威科夫量价分析 applied to sectors or themes.
- The user asks which industries or sub-sectors deserve attention now.
- The user explicitly mentions 强度、离散度、共振、抱团、主线、板块扩散.
- The user wants to move from 一级行业 → 细分板块 → 核心标的.

## Inputs

- Optional market scope: `A股全市场`, `沪深300`, `中证1000`, `科创板`, `创业板`, or custom universe.
- Optional time scale: `日内`, `1-3日`, `1-2周`, `波段`.
- Optional preference: `一级行业`, `细分板块`, `核心标的`, or full top-down scan.

If the user does not specify a time scale:

- Use `最近1-3日` to verify current fund-flow continuity.
- Use `最近20-60个交易日` 日K to judge Wyckoff phase and structure.

## Data-source strategy

Use a layered source strategy instead of relying on memory. This skill is self-contained: the required data scripts are vendored inside this skill folder.

### Primary stack

- `mx-data`: 一级行业/板块实时行情、主力资金流、历史行情
- `mx-xuangu`: 细分板块成分股、板块内初筛
- `stock_data`: 个股实时行情、日K、盘口
- `mx-search`: 新闻、政策、催化验证

### When to prefer each source

- For **一级行业资金流 / 板块实时状态**, prefer `mx-data`.
- For **细分板块成分股 / 板块内候选股**, prefer `mx-xuangu`.
- For **核心标的实时价 / 日K / 量价结构**, prefer `stock_data`.
- For **新闻催化 / 政策 / 情绪确认**, use `mx-search` as supplement.

### Availability check

Before analysis, first check which vendored backends are currently usable:

```bash
python3 skills/wyckoff-sector-rotation-analysis/scripts/data_source_router.py availability
```

This checks:

- whether `MX_APIKEY` is set
- whether the vendored `mx_data.py`, `mx_xuangu.py`, `mx_search.py`, `stock_data.py` exist
- which stack should be used first

## Concrete commands

### A. 一级行业 / 板块资金流与实时行情

Prefer:

```bash
python3 skills/wyckoff-sector-rotation-analysis/scripts/data_source_router.py \
  sector-flow --query "今日A股一级行业主力资金流向"

python3 skills/wyckoff-sector-rotation-analysis/scripts/data_source_router.py \
  sector-spot --query "今日A股一级行业最新涨跌幅 成交额 换手情况"
```

Useful query templates:

```bash
"今日通信设备板块主力资金流向"
"今日半导体板块主力资金流向"
"今日通信设备板块最新涨跌幅 成交额 主力资金流向"
"最近3个交易日半导体板块资金净流入"

> 提示：妙想对“具体行业/板块”的资金流问法更稳定；如果“全市场一级行业资金流排名”无结构化结果，改成逐个候选行业查询。
```

### B. 细分板块与成分股

Prefer:

```bash
python3 skills/wyckoff-sector-rotation-analysis/scripts/data_source_router.py \
  sector-members --query "通信设备板块成分股"

python3 skills/wyckoff-sector-rotation-analysis/scripts/data_source_router.py \
  sector-members --query "光通信板块涨幅居前的股票"
```

Useful query templates:

```bash
"某一级行业下有哪些细分板块"
"光模块板块成分股"
"算力租赁板块中最近3日涨幅居前股票"
"机器人板块成交额靠前股票"
```

### C. 核心标的实时行情

Prefer:

```bash
python3 skills/wyckoff-sector-rotation-analysis/scripts/data_source_router.py \
  stock-quote 600498 --json
```

Direct backend call:

```bash
python3 skills/wyckoff-sector-rotation-analysis/scripts/backends/stock_data/stock_data.py \
  quote 600498 --json
```

### D. 核心标的日K数据

Prefer:

```bash
python3 skills/wyckoff-sector-rotation-analysis/scripts/data_source_router.py \
  stock-kline 600498 -n 60 --scale 240 --json
```

Direct backend call:

```bash
python3 skills/wyckoff-sector-rotation-analysis/scripts/backends/stock_data/stock_data.py \
  kline 600498 -n 60 --scale 240 --json
```

### E. 新闻 / 政策 / 催化验证

Prefer:

```bash
python3 skills/wyckoff-sector-rotation-analysis/scripts/data_source_router.py \
  stock-news --query "光通信板块近期新闻"
```

Direct backend call:

```bash
python3 skills/wyckoff-sector-rotation-analysis/scripts/backends/mx_search.py \
  "光通信板块近期新闻"
```

## Non-negotiable data requirements

You must fetch **current** data before making any judgment. Do not rely on stale memory.

### Required data at the 一级行业 level

For each candidate 一级行业, obtain as much of the following as the environment supports:

- 最新实时涨跌幅
- 最新实时成交额 / 成交量
- 最近1-3日资金净流入 / 净流出
- 最近20-60个交易日的日K数据或足够的历史价格量能代理
- 行业内上涨家数 / 下跌家数 or other breadth proxy when available

### Required data at the 细分板块 level

For shortlisted sub-sectors, obtain:

- 最新实时涨跌幅
- 最新实时成交额 / 成交量
- 最近20-60个交易日日K或板块历史走势代理
- 板块内部同步性信息: 领涨股、跟涨股、强势股占比 or breadth proxy

### Required data at the 核心标的 level

For each selected stock, obtain:

- 最新实时价格、涨跌幅、成交额 / 成交量
- 最近20-60个交易日日K
- 相对板块的强弱表现

### If data is missing

- If real-time quotes are missing, explicitly say the实时部分不可验证.
- If recent日K数据 is missing, do not pretend to do Wyckoff analysis.
- If sector breadth / dispersion proxies are unavailable, state the proxy you used.
- Always include a concrete `analysis_time` and explain the data cutoff.

## Required execution order

Use this exact order.

1. Run backend availability check.
2. Pull current market snapshot.
3. Pull 一级行业实时数据 and recent资金流.
4. Pull strongest candidate industries' 细分板块 and成分股.
5. Pull candidate stocks' 实时数据 and recent日K数据.
6. Then perform Wyckoff, strength, and dispersion analysis.
7. Summarize in Chinese with exact `analysis_time` and source notes.

## Core methodology

### 1) 一级行业资金流筛选

Start from all 一级行业. Rank them by **资金流质量** rather than just total money amount.

Prefer industries where:

- price rises with expanding volume
- consolidation happens while sell pressure contracts
- pullbacks are shallow and liquidity remains healthy
- inflow shows continuity instead of one-day spikes

Downgrade industries where:

- price rises but net outflow dominates
- volume expands but price extension is poor
- repeated upper shadows or failed breakouts appear
- one-day news bursts vanish immediately

### 2) 威科夫量价分析

For every shortlisted industry or sub-sector, explicitly classify it into one of these states:

- `吸筹迹象`
- `再吸筹 / 整理`
- `上升趋势 / Markup`
- `派发风险`
- `Markdown 风险`

Explain clearly:

- 需求还是供给在主导
- 量价是否一致
- 回撤是否健康
- 突破是否有效
- 是否存在假突破、冲高回落、放量不涨、缩量阴跌等警报

### 3) 强度判断

You must produce both a **numeric score** and a **verbal label**.

#### 强度分数

Set `strength_score` on a rough scale from `-100` to `+100`.

Estimate it from:
 
 - relative return vs broad market
- recent fund-flow direction and continuity
- trend persistence
- breakout efficiency
- pullback resilience
- volume support quality

#### 强度标签

- `强度很强`: `> +40`
- `强度偏强`: `+20 ~ +40`
- `强度微强`: `0 ~ +20`
- `强度转弱`: `0附近反复`
- `强度为负`: `< 0`

Hard rule:

- `strength_score` must stay **above 0** for a sector to count as healthy.
- Prefer `强度偏强` and `强度很强`.
- If the theme is hot but `strength_score < 0`, classify it as risk, not a main line.

### 4) 离散度判断

You must produce both a **numeric score** and a **verbal label**.

#### 离散度分数

Set `dispersion_score` on a rough scale from `0` to `100`.

Lower is better.

Estimate it from:

- leaders and followers moving together or not
- breadth of participation
- whether only one or two names are hard-carrying the theme
- whether volume expansion is broad-based or isolated
- whether top sub-sectors are synchronized or fragmented

#### 离散度标签

- `离散度低`: `0 ~ 25`
- `离散度中低`: `25 ~ 45`
- `离散度中高`: `45 ~ 65`
- `离散度高`: `> 65`

Hard rule:

- Lower dispersion is better.
- Between two sectors with similar strength, always prefer the one with lower dispersion.
- High strength + high dispersion = `交易型机会`, not `高确定性主线`.

### 5) 细分板块下钻

Inside stronger 一级行业, find sub-sectors that:

- keep `strength_score > 0`
- have lower or at least not worse dispersion than the parent industry
- show better Wyckoff structure than adjacent themes
- have broader participation instead of only one emotional leader

For each selected sub-sector, state:

- why it stands out
- current Wyckoff phase
- `strength_score` and label
- `dispersion_score` and label
- whether it is `主线候选`, `跟随性机会`, or `仅观察`

### 6) 核心标的筛选

For each selected sub-sector, list only `1-3` core names.

Selection rules:

- prefer the cleanest price-volume structure, not the most famous name by default
- require relative strength leadership or clear follow-through after breakout
- avoid pure one-day limit-up anomalies without sponsorship
- if no stock is clean enough, say so directly

For each core name, include:

- 股票名称与代码
- 实时状态: 最新价 / 涨跌幅 / 成交额 if available
- 日K结构特征
- role: 龙头 / 中军 / 补涨 / 趋势核心
- current Wyckoff state
- action type: `跟踪`, `等回踩`, `只观察`

## Output expectations

Respond in Chinese and keep factual data separate from inference.

- `分析时间`: exact timestamp and data cutoff
- `数据来源`: realtime quote / sector flow / daily K sources used
- `市场总览`: risk appetite and sector rotation backdrop
- `一级行业筛选结果`: flow, Wyckoff phase, strength, dispersion, conclusion
- `重点细分板块`: why selected, phase, strength, dispersion, risk notes
- `核心标的`: live status + daily-K structure + role
- `最终排序与行动建议`

## Final ranking format

End with four buckets:

- `最优先跟踪`
- `次优先跟踪`
- `只适合交易，不适合当主线`
- `回避方向`

## Boundaries

- 聚焦行业轮动、资金流、量价和结构，不要扩展成大篇幅基本面分析.
- 不要把热度误判成健康主线.
- 不要在没有实时数据或近期日K数据的情况下假装做精确判断.
- 如果市场没有满足 `强度为正` 且 `离散度不高` 的方向，要直接说“当前缺少干净主线”.
- 可以给跟踪优先级，但不要伪装成确定性收益承诺.
