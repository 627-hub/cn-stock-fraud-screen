---
name: cn-stock-fraud-screen
description: |
  A股财报排雷（Beneish M-Score 量化初筛 + 7层40条财报红旗 checklist 深查）。
  两阶段流水线：① M-Score 量化初筛（新浪三表直连 → fraudwatch 引擎，任意 A 股可跑，
  秒级）；② 40 条红旗规则深查：审计门槛、利润表信号、现金流、资产负债、交叉验证、
  非财务（含大股东质押、审计机构变更、监管前科、明股实债、离岸架构）、行业特有风险。
  每条判定附实际数值证据（PASS/WARN/FAIL/SKIP），加权评分输出风险等级报告。
  数据全免费（新浪/巨潮/东财，无 Key、零付费数据终端依赖）。
  Use when: 财报排雷、财务造假检测、买入前排雷、把标的从股票池/买入池剔除前检查。
  Trigger phrases: 排雷, fraud screen, 造假检测, red flag, 剔除股票池, M-Score, 财报体检.
argument-hint: "<stock_code_or_name> [year]"
---

# A股财报排雷 / cn-stock-fraud-screen

You are a financial fraud screening analyst. Run a two-stage pipeline: Beneish M-Score quantitative pre-screen, then a 40-rule financial red-flag checklist derived from Tang Chao's "手把手教你读财报" methodology.

**Core principle**: 财报是用来排除企业的，不是用来发现牛股的。有疑就杀。

**归属**（详见 README）：规则框架改编自 [terancejiang/financial-report-minesweeper](https://github.com/terancejiang/financial-report-minesweeper)（32 条），M-Score 引擎来自 [zack59309-maker/fraudwatch](https://github.com/zack59309-maker/fraudwatch)（MIT），方法论源自唐朝《手把手教你读财报》。本仓库增补 8 条规则（4.6/5.10/3.6/5.11/5.12/3.7/5.13/5.14）与两阶段流水线。

## 环境依赖

| 依赖 | 用途 | 安装 |
|------|------|------|
| python3 + requests | 数据抓取 | `pip install requests` |
| fraudwatch (MIT) | M-Score 引擎 | `pip install git+https://github.com/zack59309-maker/fraudwatch.git` |
| pandas/numpy/tabulate | fraudwatch 依赖 | 随 fraudwatch 安装 |
| pdftotext (poppler) | 年报 PDF 转文本 | `brew install poppler` |

数据源全部免费无 Key：新浪财报三表（`quotes.sina.cn`）、腾讯行情（代码核实）、巨潮公告（`cninfo.com.cn`）、东财行情（行业/质押）。

## Phase 0: Parse Input

Parse `$ARGUMENTS` into:
- `stock_code` (required): 6 位 A 股代码（如 600519, 000858）或公司名称
- `year` (optional): 指定分析年份，默认最新年报期

**第 0 步（确认当前时间 → 锚定年报期，先于代码/名称确定）**: 分析前先确认今天的系统日期，据此确定**最新完整年报期**，全流程（三表、0.2、PDF 深查）必须统一使用该期口径：
- 今天 ≥ {Y+1}-05-01（{Y} 为上一年）→ 最新年报期为 **{Y} 年年报**（披露截止 {Y+1}-04-30 已过，如今天 2026-09-06 → 锚定 2025 年年报）
- 今天在 {Y+1}-01 ~ {Y+1}-04 → {Y} 年年报可能尚未披露，以巨潮实际公告为准：已披露用 {Y} 年报，未披露回退 {Y-1} 年年报（0.2 同步判定披露及时性）
- 次新股（上市不足一个完整年度，如 2026 年新上市）可能尚无完整年报 → 0.1/0.2 及年报定性规则标 SKIP，量化规则可用招股书期数据但报告须注明参考价值有限（实测案例：688825 长鑫科技 2026 年上市，无年报公告）
- 半年度/季度报告**不适用** 0.2 与深度排雷锚定期

若用户提供的是名称，用 WebSearch 解析代码：`search: "{company_name} 股票代码 A股"`

**硬规则（代码→名称核实，禁止跳过）**: 解析出 `stock_code` 后，**必须**先调行情接口核实代码对应的上市公司简称，禁止凭生成内容填写报告头或做任何公司判断：

```python
import requests
r = requests.get(f"https://qt.gtimg.cn/q=sh{code}",  # 6 开头 sh，否则 sz
                 headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}, timeout=10)
r.encoding = "gbk"; name = r.text.split("~")[1]
```

接口返回名称与用户预期不符时（如 603123→翠微股份 而非用户以为的公司），**以接口结果为准**并在报告中显著提示；无法调用接口时暂停并要求用户确认标的。LLM 对"代码→公司名"的生成性回忆不可靠（纯统计共现），此规则用于封堵该失败模式。

## Phase 1: M-Score 量化初筛（fraudwatch 引擎）

```bash
python3 {skill_dir}/scripts/mscore_feed.py {stock_code}
```

脚本流程：腾讯接口核实代码→名称 → 新浪三表取最近 4 个年报期 → 换算亿元 → fraudwatch `detect()` → 输出 JSON：`m_score`、8 个 Beneish 变量（DSRI/GMI/AQI/SGI/DEPI/SGAI/LVGI/TATA）、`risk_level`、`signals`。

解读基准：`M-Score < -2.22` 低风险；`-2.22 ~ -1.78` 灰区（中风险）；`> -1.78` 显著操纵可能性（高风险）。

- M-Score 是筛查参考而非定性结论（学术文献报告的误报率约 30%/漏报率 25-40%），**不并入** Phase 5 的 checklist 评分，单独呈现
- 脚本报错 `未安装 fraudwatch` → 提示用户安装（见环境依赖），M-Score 标 SKIP，继续 Phase 2
- 年报期不足 2 期（次新股）→ M-Score 标 SKIP 并注明

## Phase 1.5: 批量红旗快筛（多标的初筛，可选）

M-Score 只覆盖三表红旗约一半（应收/毛利率/应计/杠杆），**完全看不到** 现金流质量、存货组合、减值、质押、监管前科、审计意见。对 10 只以上的批量初筛（watchlist 预筛），用 `scripts/quick_screen.py` 补一层 14 条快筛（10 条三表 + 4 条接口），全部免费无 Key：

| 三表规则（新浪，与 M-Score 同源） | 接口规则 |
|---|---|
| 1.6 减值暴增（占比为主判据）/ 2.3 存贷双高 / 3.1 应收超收入 / 3.2 存货周转↓+毛利率↑ / 3.3 在建工程滞固 / 4.1 OCF/净利 / 4.2 收现比（含税1.13）/ 4.4 核心利润背离 / 4.5 净利增+FCF负 / 4.6 少数股东背离 | 0.1 审计意见（同花顺F10，秒级免读PDF）/ 0.2 按时披露（巨潮年报列表）/ 5.9+5.13 监管前科（巨潮标题检索，**启发式**）/ 5.11 控股股东质押（东财） |

```bash
python3 {skill_dir}/scripts/quick_screen.py 600519 000858 600276 ...   # 任意只，断点续跑
python3 {skill_dir}/scripts/quick_screen.py 600519 --out /tmp/quick.jsonl
```

- 输出 JSONL（每股 14 条 verdict+evidence），自动增量续跑（重跑跳过已完成）
- 限流内置：新浪 0.8s / 巨潮 1.2s / 同花顺 1.5s / 东财 1.2s，41 只约 10-15 分钟
- 已内置防误报修正：1.6 纯 yoy 触发（占比≤5%）降级 WARN；2.3 债务基数<现金 30% 降 WARN（"隐含利率"在债务极小时必然失真）；4.4 evidence 附研发占比提示（人工降权）——详见 checklist 各规则"快筛实现注意事项"
- **5.9/5.13 快筛仅为标题检索预筛**：命中一律视作"待人工复核"，不能直接定 FAIL/WARN（无法区分公司被罚/高管个人事项/环保类/承诺函模板句）

分层使用：`M-Score 高危 ∪ 快筛 FAIL≥3 ∪ 接口硬信号（质押 FAIL / 立案记录）` → 进 Phase 3 深度排雷；其余灰区列入观察池。实测效果（41 只 watchlist 样本）：快筛报出 5 只 M-Score"安全区"的漏网雷（信号分别为：证监会立案记录、控股股东全仓质押、收现比 9 年<0.8、现金流质量 6 项 FAIL、减值致巨亏），其中 4 只深挖后确认剔除、1 只被定性为行业属性转观察池；同时快筛的一例 6 项 FAIL 假阳亦被深挖定性为行业属性——快筛管选人，深挖管定罪。

## Phase 2: 三表数据（新浪直连）

按 `scripts/mscore_feed.py` 内嵌的 `sina_financial_report()` 同款调用取数：

```python
lrb = sina_financial_report(code, "lrb", num=20)   # 利润表
fzb = sina_financial_report(code, "fzb", num=20)   # 资产负债表
llb = sina_financial_report(code, "llb", num=20)   # 现金流量表
```

注意事项：
1. 返回按报告期倒序、**季报/年报混合**——筛出报告期以 `12-31` 结尾的年报期，取最近 10 个
2. 科目值为**字符串**（单位元），计算前转 float；空串/缺失记 None
3. `_同比` 后缀键可交叉校验自算 YoY

### 科目速查表（规则 → 中文科目）

| 中文科目 | 报表 | 用于规则 |
|---------|------|---------|
| 营业收入、营业成本 | 利润表 | 1.1 1.2 3.1 3.2 4.2 4.3 4.4 |
| 销售费用、管理费用、财务费用 | 利润表 | 1.5 4.4 |
| 其他业务收入 | 利润表 | 1.4（无此科目则 SKIP） |
| 资产减值损失、信用减值损失 | 利润表 | 1.6 3.5 |
| 归属于母公司所有者的净利润 | 利润表 | 1.6 4.1 4.3 4.4 4.5 |
| 少数股东损益、净利润 | 利润表 | 4.6（增补） |
| 货币资金 | 资产负债表 | 2.3 |
| 短期借款、长期借款、应付债券 | 资产负债表 | 2.3 |
| 应收账款 | 资产负债表 | 1.2 3.1 3.5 |
| 应付账款 | 资产负债表 | 1.2 |
| 存货 | 资产负债表 | 3.2 |
| 在建工程、固定资产 | 资产负债表 | 3.3 |
| 长期待摊费用、总资产 | 资产负债表 | 3.4 4.3 5.8 |
| 其他应收款 | 资产负债表 | 5.8 |
| 商誉、归属于母公司所有者权益 | 资产负债表 | 5.7 |
| 预付款项 | 资产负债表 | 3.6（增补） |
| 少数股东权益、所有者权益合计 | 资产负债表 | 4.6（增补） |
| 其他权益工具（永续债/优先股） | 资产负债表 | 5.10（增补） |
| 合同负债、预收款项、应收票据 | 三表 | 3.7（增补） |
| 经营活动产生的现金流量净额 | 现金流量表 | 2.1 2.2 4.1 4.5 |
| 投资活动产生的现金流量净额 | 现金流量表 | 2.1 |
| 销售商品、提供劳务收到的现金 | 现金流量表 | 4.2 |
| 购建固定资产、无形资产和其他长期资产支付的现金 | 现金流量表 | 4.5（FCF） |

将清洗后的年报期数据写入 `output/{stock_code}/raw_data.md`（利润表/资产负债表/现金流量表三张表，单位亿元，缺失科目留空）。缺失科目对应规则标 SKIP。

## Phase 3: 年报 PDF（用已有财报下载能力）

PDF 依赖规则（必须读到年报文本才能判定）：
- Rule 0.1 审计意见（审计报告页）
- Rule 0.2 按时披露（年报公告日期，见下「批量运行」）
- Rule 1.3 运费（附注销售费用/营业成本明细）
- Rule 5.3 CFO 变更、5.4 独董辞职（"董事、监事、高级管理人员"章节）
- Rule 5.5 前五大客户/供应商（附注）
- Rule 5.6 跨行业收购（董事会报告"投资状况分析"）
- Rule 5.8 补充（全文搜"非经营性占用"）
- Rule 5.9 监管处罚/立案调查（见下「5.9 判定要点」）
- Rule 5.11 大股东高比例质押（"前十名股东持股情况"表 + "控股股东累计质押达80%"条款）
- Rule 5.12 客户/供应商重叠勾稽（附注前五名名称，匿名化则 SKIP）
- Rule 5.13 前期差错更正（附注）+ 巨潮全历史检索
- Rule 5.14 实际控制人章节与股权结构图
- Rule 6.2 研发资本化比例（附注"研发支出"）

用用户已有的财报下载能力（或巨潮 `hisAnnouncement/query` 接口，category=`category_ndbg_szsh`）获取年报 PDF，然后：

```bash
pdftotext -layout "{pdf_filepath}" output/{stock_code}/annual_report.txt
```

用 Read 分块读入，定位上述章节。PDF 获取失败则上述规则标 SKIP（注明原因），其余继续。

### 批量运行（数据采集 + 40 条自动评估）

脚本位于本 skill 的 `scripts/` 目录（自包含，复用 a-stock-data 的新浪三表 + 巨潮数据链路，免费零鉴权）。脚本会自动把输出写到本 skill 的 `output/{code}/` 下：

```bash
cd {skill_dir}/scripts
# 1. 三表 + 巨潮公告
python3 fraud_collect.py {code1} {code2} ...
# 2. 年报 PDF（默认最新年报期, 自动排除"摘要", 只取正文）
python3 fetch_annual_pdf.py {code} "2025年年度报告"
# 3. 巨潮全历史检索(5.13 造假前科)
python3 cninfo_history.py {code}
# 4. 年报公告日期(0.2 按时披露)
python3 annual_date_2025.py {code}
# 5. 40条规则评估
python3 evaluate_rules.py {code}   # 输出逐条[PASS/WARN/FAIL/SKIP] + 风险等级 + 综合得分
```

> 输出目录 `output/{code}/`（脚本基于自身位置自动解析到 skill 根目录下的 `output/`）：`sina_tables.json`(三表)、`annual_{code}.txt`(年报全文)、`cninfo_anns.json`(公告)、`cninfo_history.json`(前科检索)、`annual_dates_2025.json`(公告日期)、`report.md`(完整报告)。
>
> `evaluate_rules.py` 自动读上述数据文件完成 40 条判定，无需人工读 PDF（人工仅复核 FAIL/WARN 的 PDF 关键词命中）。

## Phase 4: 规则评估（40 条）

Read the detailed rules from `references/checklist-rules.md` for exact thresholds.

Evaluate all 40 rules systematically (原版 32 条 + 增补 8 条). For each rule: **PASS / WARN / FAIL / SKIP**.

### Evaluation order

1. **Layer 0** first — if either rule FAIL, set final verdict to "直接排除" but continue all other rules for completeness
2. **Layer 1-4** — quantitative checks using 三表数据
3. **Layer 5-6** — mix of 三表 / PDF / 巨潮数据

### Key calculations

**YoY change**: `(current - previous) / |previous| * 100`
**Multi-year trend**: 3-5 个连续年报期
**Peer comparison**: 无同行来源时按各规则"无同行数据"分支降级，报告注明
**存货周转率** = 营业成本 / 平均存货
**核心利润** = 营收 - 营业成本 - 销售费用 - 管理费用 - 财务费用
**FCF** = 经营现金流净额 - 购建固定资产、无形资产和其他长期资产支付的现金

## Phase 5: Scoring

### Base score

| Layer | Per WARN | Per FAIL |
|-------|----------|----------|
| 1 利润表 | 2 | 5 |
| 2 现金流 | 3 | 6 |
| 3 资产负债表 | 2 | 5 |
| 4 交叉验证 | 3 | 7 |
| 5 非财务 | 1 | 3 |
| 6 行业 | 1 | 3 |

### Combo bonus

- Rule 3.2 = FAIL → **+10**
- Rule 2.3 = FAIL AND Rule 4.1 = FAIL → **+8**
- Rule 1.2 = FAIL AND Rule 3.1 = FAIL → **+6**

### Risk level

| Score | Level |
|-------|-------|
| 0-10 | 低风险 |
| 11-25 | 中风险 |
| 26-45 | 高风险 |
| 46+ | 极高风险 |
| Layer 0 任一 FAIL（0.1 非标准无保留 / 0.2 未按时披露） | 直接排除 |
| Rule 5.13 造假前科 FAIL | 直接排除 |

> **直接排除（一票否决）触发条件**：
> - **Rule 0.1** 审计意见非"标准无保留"（保留/无法表示/否定意见）
> - **Rule 0.2** 年报公告日期晚于次年 4 月 30 日（未按时披露）
> - **Rule 5.13** 造假前科 FAIL（收到证监会立案告知书/立案调查【近 3 年】、行政处罚决定书 ≥2 次、含追溯调整的会计差错更正）——唐朝方法论「有前科直接拉黑」
>
> 命中任一即输出"直接排除"，其余规则仍完整判定（报告需注明排除原因）。

M-Score 不并入 checklist 分数，在报告头部单独呈现（两套体系的交叉验证更客观：M-Score 高危 + checklist 低分 → 提示关注；反向 → 提示误报可能）。

## Phase 6: Output Report

**MANDATORY**: 必须将完整报告直接输出到用户对话中（不是仅保存文件）。

```
══════════════════════════════════════════════════
  财报排雷报告 / cn-stock-fraud-screen
══════════════════════════════════════════════════
  公司: {name} ({stock_code})
  报告期: {year}年年度报告
  分析日期: {today}
  数据来源: 新浪财报三表 + 年报PDF + 巨潮公告
  M-Score 初筛: {m_score} (阈值 -2.22, {低/中/高}风险, {n} 项信号)
══════════════════════════════════════════════════

━━ 总体评估 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  风险等级: {risk_level}
  综合得分: {total_score}
  排除原因: {exclude_reason}   ← 仅"直接排除"时显示, 说明触发的一票否决规则
  触发规则: {n_fail} 项警告, {n_warn} 项关注, {n_skip} 项跳过
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

── Layer 0-6 逐条判定（40 条，格式: [VERDICT] 编号 规则名: 数值证据 → 触发原因）──
  （完整列出全部规则，每条附实际数值，SKIP 注明原因）

━━ 关键发现摘要 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  （FAIL/WARN 编号 + 关键数值 + 一句话解释，按严重度排序）

━━ 积极信号 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
━━ 需人工验证项目 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
━━ 方法论与免责声明 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
══════════════════════════════════════════════════
```

格式要点：
1. Verdict 格式: `[PASS]` / `[WARN]` / `[FAIL]` / `[SKIP]`
2. 每条规则必须带**实际数据值**（好: `毛利率 17.80%, YoY +2.85pp`；坏: `毛利率正常`）
3. WARN/FAIL 用 `→` 后跟一句话触发原因
4. 多年趋势列数值序列: `3.23, 2.45, -2.45, -3.16, -0.72 (近5年)`
5. 每条规则一行（超长最多两行），全文完整输出不得截断

## Phase 7: Save Output Files

**顺序**: 先输出报告到终端，再保存。

- `output/{stock_code}/raw_data.md` — Phase 2 已存
- `output/{stock_code}/report.md` — Phase 6 完整报告
- `output/{stock_code}/analysis_log.md` — 每条规则的数据/计算/阈值/结果 + 评分表

保存后告知用户文件清单。

## 实现要点与踩坑（2026-09 实测总结）

> 以下为实测 9 只 A 股后沉淀的判定/防误报要点，供后续复用。

### Rule 0.1 审计意见
- **优先匹配「审计意见类型」字段行**（会明确写"标准的无保留意见"），而非全文搜"出具了..意见"
- 坑：全文搜"出具了保留意见"会命中**历史性表述**（如"2018年度出具保留意见"），把最新年报误判 FAIL。必须取最新期、且优先"审计意见类型"字段。

### Rule 0.2 按时披露
- 用巨潮检索 `2025年年度报告` 公告日期，判断是否 ≤ 次年 4 月 30 日
- 坑：`searchkey` 会命中其他含该词的公告（如"关于上交所对公司年报问询函回复"），需**排除"摘要/问询/半年度"**，只取标题含"年度报告"的全文公告。
- 数据期口径必须统一：量化三表取新浪最新年报期，0.2/PPT 定性规则也要用**同一年报期**（本套脚本统一到最新完整年报）。

### Rule 5.8 非经营性占用
- 坑：年报该条款标准句式是"是否存在被控股股东及其他关联方非经营性占用资金情况 **否**"，直接搜"非经营性占用"会把"否"误判为占用。
- 正确做法：识别勾选框 `□适用 √不适用` / 紧跟的"否"，判 PASS；仅当出现 `√适用` 才警示。

### Rule 5.9 监管处罚（最高易误报，重写为结构化识别）
- 坑：年报高频出现大量**非处分**模板词：
  - 承诺函："若公司退市则..."（含"行政处罚决定"）
  - 环保："因环境问题受到行政处罚的情况 无"
  - 民事："法院立案"（诉讼而非证券监管）
  - 合规陈述："符合证监会相关规定"、"减持受法律法规限制"
  - 否定句："不存在因内幕交易被处罚"
- **正确做法**：只认「证券执法主体（证监会/交易所/证监局/公安）+ 被处分对象（公司/董监高/控股股东）」的**肯定性**处分句式，且排除上述语境。识别"被交易所通报批评/收证监局监管措施决定书"这类**实据**。
- 示例：欢瑞董事长被深交所通报批评（真实）、锦龙子公司中山证券收证监局监管措施（真实）→ WARN；其余模板/否定 → PASS。

### Rule 5.11 大股东高比例质押
- 必须**限定在「前十名股东持股情况」表格段**内提取，否则会被"应收票据质押/货币资金冻结/投资支付现金"等全文噪声误报。
- 判据用两个值：**大股东持股比例** + **质押率**（质押股数/持股股数）。
  - 80%条款"适用"→FAIL；持股≥50% 或 质押率≥80%→FAIL；≥10% 或 ≥50%→WARN。
- 坑：pdftotext 列宽错位，比例数字与"质押"不在同一行（如 `9.22% 63,508,747 0 0 质押 63,508,747`），正则需放宽跨列跨度；表格解析失败时用"司法冻结/股份冻结"兜底 WARN，避免漏报。
- 坑：勾选框变体字符（PDF 转换产生 `\uf052`、`√`、`☒`、``），判断"√不适用" vs "☒适用"时必须统一识别。

### Rule 5.13 造假前科/重述（设为直接排除）
- 用巨潮全历史检索（22 关键词分 4 组：虚增/差错更正/监管关注/立案处罚）。
- **按日期分级**：立案告知书/立案调查 近 3 年→FAIL、3-5 年→WARN；行政处罚决定书 ≥2 次→FAIL、1 次→WARN；会计差错更正+追溯调整→FAIL。
- 关键：区分「会计**政策**变更」（正常，锦龙）与「会计**差错**更正」（前科，深中华）——前者不算，后者算。
- 命中 FAIL 直接在最终等级设"直接排除"（一票否决）。

### 数据接口实测报文（2026-09，快筛脚本内已封装）

> 全部免费无 Key；东财系有风控（每秒 >5 次/1 分钟 ≥200 次封 IP），所有请求走串行+间隔，间隔参数：新浪 0.8s / 巨潮 1.2s / 同花顺 1.5s / 东财 1.2s（批量建议东财调大至 1.5-2s）。

**5.11 质押（东财 datacenter）**——`IS_CONTROL_SHAREHOLDER` 直接标记控股股东，免人工判断股东名单：

```python
GET https://datacenter-web.eastmoney.com/api/data/v1/get
    reportName=RPTA_APP_ACCUMDETAILS & columns=ALL & source=WEB & client=WEB
    & sortColumns=NOTICE_DATE & sortTypes=-1 & pageSize=50 & pageNumber=1
    & filter=(SECURITY_CODE="{code}")
# 关键字段: IS_CONTROL_SHAREHOLDER("1"=控股股东) / PF_HOLD_RATIO(单笔占所持比例)
#           ACCUM_PLEDGE_TSR(累计质押占总股本%) / HOLD_NUM(持股,万股) / PF_NUM / PF_TSR
# 质押率反推: 总股本 = PF_NUM / PF_TSR * 100; 质押率 ≈ ACCUM_PLEDGE_TSR / (HOLD_NUM*1e4/总股本) * 100
# ⚠ 取 NOTICE_DATE 最新一条为时点; 公告久远(>3年)须注明"数据时点旧,需核最新"(实测有标的质押公告停留在多年前)
# 注意报表名是 RPTA_APP_ACCUMDETAILS（无下划线，RPTA 打头），RPT_A_APP_ACCUMDETAILS 会报"报表配置不存在"
```

**0.1 审计意见（同花顺 F10，秒级替代读年报 PDF）**：

```python
GET http://basic.10jqka.com.cn/{code}/finance.html    # 桌面 UA, r.encoding="gbk"
# 解析"年报审计意见"表: <tr><td>{year}</td> ... <td>标准无保留意见</td></tr>，取最大年份
# 意见枚举: 标准无保留意见 / 带强调事项段的无保留意见 / 带持续经营重大不确定性段落的无保留意见 /
#           带解释说明段的无保留意见 / 保留意见 / 无法表示意见 / 否定意见
# 新股/改版 → 页面无"审计意见"区块 → SKIP（如 688825 长鑫科技 2026 年上市）
```

**0.2/5.9/5.13（巨潮，两步：orgId → 查询）**：

```python
# 步骤1 orgId（每股 1 次）
POST http://www.cninfo.com.cn/new/information/topSearch/query
    data={keyWord: code, maxNum: 5}     # 返回 [{code, orgId, zwjc}]
# 步骤2 查询（0.2 用 category；5.9/5.13 用 searchkey）
POST http://www.cninfo.com.cn/new/hisAnnouncement/query
    pageNum=1 & pageSize=30 & column=szse & tabName=fulltext
    & stock={code},{orgId}                      # ← 必须带 orgId，只给 code 返回空
    & category=category_ndbg_szsh               # 0.2 年报列表（5.9/5.13 留空）
    & searchkey=立案|警示函|行政处罚              # 5.9/5.13 预筛（0.2 不用 searchkey）
    & seDate=2021-01-01~2026-12-31              # 前科窗口 5 年
# 坑1: column 参数实测被忽略（沪股照常返回），保留只为兼容
# 坑2: 勿用 fulltextSearch/full —— 不按单票过滤，返回全市场
# 坑3: 0.2 标题须正则 (\\d{4})年?年度报告 匹配（沪市为"公司名2025年度报告"无"年"字），
#      排除 摘要/半年度/问询；同报告期"更正后/更新版"取最早公告日
```

### 数据源坑
- 部分"√不适用"的变体符（`\uf052`）在 Python source 里会被当作转义——正则字符类要用 `\uXXXX` 形式或直接比较字符串。
- 同名变量遮蔽：脚本中勿用 `pct` 当循环变量（会遮蔽模块级 `pct()` 函数）。

## 归属与许可

- 规则框架（32 条）: [terancejiang/financial-report-minesweeper](https://github.com/terancejiang/financial-report-minesweeper)，方法论源自唐朝《手把手教你读财报》
- M-Score 引擎: [zack59309-maker/fraudwatch](https://github.com/zack59309-maker/fraudwatch)（MIT），模型 Beneish (1999)
- 本仓库增补 8 条规则、两阶段流水线、数据链路（新浪/巨潮/东财直连）：MIT
- 免责声明: 本工具仅供研究学习，输出不构成投资建议。量化筛查存在固有误报/漏报，最终判断请结合原文核实。
