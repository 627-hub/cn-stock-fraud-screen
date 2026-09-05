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
- Rule 1.3 运费（附注销售费用/营业成本明细）
- Rule 5.3 CFO 变更、5.4 独董辞职（"董事、监事、高级管理人员"章节）
- Rule 5.5 前五大客户/供应商（附注）
- Rule 5.6 跨行业收购（董事会报告"投资状况分析"）
- Rule 5.8 补充（全文搜"非经营性占用"）
- Rule 5.9 监管处罚/立案调查（全文关键词搜索）
- Rule 5.12 客户/供应商重叠勾稽（附注前五名名称，匿名化则 SKIP）
- Rule 5.13 前期差错更正（附注）+ 巨潮全历史检索
- Rule 5.14 实际控制人章节与股权结构图
- Rule 6.2 研发资本化比例（附注"研发支出"）

用用户已有的财报下载能力（或巨潮 `hisAnnouncement/query` 接口，category=`category_ndbg_szsh`）获取年报 PDF，然后：

```bash
pdftotext -layout "{pdf_filepath}" output/{stock_code}/annual_report.txt
```

用 Read 分块读入，定位上述章节。PDF 获取失败则上述规则标 SKIP（注明原因），其余继续。

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
| Layer 0 FAIL | 直接排除 |

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

## 归属与许可

- 规则框架（32 条）: [terancejiang/financial-report-minesweeper](https://github.com/terancejiang/financial-report-minesweeper)，方法论源自唐朝《手把手教你读财报》
- M-Score 引擎: [zack59309-maker/fraudwatch](https://github.com/zack59309-maker/fraudwatch)（MIT），模型 Beneish (1999)
- 本仓库增补 8 条规则、两阶段流水线、数据链路（新浪/巨潮/东财直连）：MIT
- 免责声明: 本工具仅供研究学习，输出不构成投资建议。量化筛查存在固有误报/漏报，最终判断请结合原文核实。
