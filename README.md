# cn-stock-fraud-screen — A股财报排雷

> AI 财报排雷 Skill：Beneish M-Score 量化初筛 + 7 层 40 条财报红旗 Checklist 深查。
> 数据全免费（新浪 / 巨潮 / 东财，无 Key），零付费数据终端依赖。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Rules](https://img.shields.io/badge/rules-7%20layers%20%C3%97%2040-blue)
![Python](https://img.shields.io/badge/python-3.9%2B-green)

**核心理念**：财报是用来排除企业的，不是用来发现牛股的。有疑就杀。

这是一个面向 AI Agent（Claude Code / opencode 等）的 Skill。给它一个股票代码，
它会输出一份带数值证据的排雷报告，告诉你这只股票有哪些财务红旗、要不要从股票池剔除。

## 两阶段流水线

```
输入: 股票代码 (如 603123)
  │
  ├─ Phase 0  代码→公司名硬核实 (腾讯行情接口, 杜绝 LLM 张冠李戴)
  │
  ├─ Phase 1  M-Score 量化初筛 (秒级)
  │           新浪财报三表 → fraudwatch 引擎 (Beneish 1999, 8 变量)
  │           → M-Score + DSRI/GMI/AQI/SGI/DEPI/SGAI/LVGI/TATA
  │
  ├─ Phase 2  三表数据 (新浪, 近 10 个年报期)
  │
  ├─ Phase 3  年报 PDF → 文本 (审计意见/董监高/前五客户/附注)
  │
  ├─ Phase 4  40 条红旗规则评估 (PASS/WARN/FAIL/SKIP, 每条附数值证据)
  │
  ├─ Phase 5  加权评分 + 危险组合加分 → 风险等级
  │
  └─ Phase 6  排雷报告 (终端完整输出 + 落盘)
```

M-Score 与 Checklist 分数**独立呈现、互为交叉验证**——M-Score 高危但 checklist 低分
提示可能的误报，反之提示需要补查。

## 规则体系（7 层 40 条）

| 层 | 规则 | 检查内容 |
|----|------|---------|
| L0 门槛 (2) | 0.1-0.2 | 审计意见（非标=一票否决）、披露时效 |
| L1 利润表 (6) | 1.1-1.6 | 毛利率异常、毛利↑应收↑应付↓、运费背离、其他业务收入、费用率骤降、减值暴增 |
| L2 现金流 (3) | 2.1-2.3 | 经营/投资 CF 背离、经营 CF 持续为负、**存贷双高**（含利息收益率交叉验证） |
| L3 资产负债表 (7) | 3.1-3.7 | 应收增速、存货周转↓+毛利率↑、在建工程不转固、长期待摊、坏账计提、**预付款项异常**、**渠道压货** |
| L4 交叉验证 (6) | 4.1-4.6 | 经营CF/净利润、销售收现/营收、资产膨胀、核心利润背离、FCF、**少数股东权益-损益背离（明股实债）** |
| L5 非财务 (14) | 5.1-5.14 | 审计机构变更、大股东减持、CFO/独董异动、客户供应商集中度与**重叠勾稽(round-trip)**、跨行业收购、商誉、其他应收款、监管处罚、**明股实债定性**、**大股东质押**、**造假前科与重述**、**离岸架构** |
| L6 行业 (2) | 6.1-6.2 | 农林渔牧、研发资本化 |

粗体为本仓库在原版 32 条之上的 8 条增补。

### 对做空机构指控的覆盖

| 指控类别（浑水/香橼/兴登堡常用） | 覆盖 |
|---|---|
| 虚增收入（应收/现金流背离） | ✅ L1/L3/L4 多条交叉 |
| 毛利率异常（康得新/瑞幸式） | ✅ 1.1/3.2 |
| 存贷双高/假现金（康得新/Wirecard式） | ✅✅ 2.3 |
| 资金占用/外流（康美式） | ✅ 5.8/4.6/3.6 |
| 循环交易 round-trip（浑水 Sino-Forest 式） | ✅ 5.12 |
| 明股实债/少数股东背离 | ✅ 4.6/5.10（增补） |
| 渠道压货 | ✅ 3.7（报表足迹层面） |
| 造假前科重犯 | ✅ 5.13 |
| 治理红旗（质押/减持/换所/CFO） | ✅ 5.1-5.4/5.11 |
| 客户实体质量核验、实地调研、单位经济模型 | ⚠️ 报表之外，列为"需人工验证项目" |

## 快速开始

### 1. 安装为 Agent Skill

```bash
git clone https://github.com/<you>/cn-stock-fraud-screen.git
cp -R cn-stock-fraud-screen ~/.claude/skills/        # Claude Code
# 或 ~/.config/opencode/skills/                       # opencode
```

### 2. 安装依赖

```bash
pip install requests pandas numpy tabulate
pip install git+https://github.com/zack59309-maker/fraudwatch.git
brew install poppler    # pdftotext, 可选 (PDF 依赖规则需要)
```

### 3. 使用

在支持 Skill 的 Agent 环境里直接说：

```
对 603123 排雷
/cn-stock-fraud-screen 600519 2025
把 002450 从我的股票池剔除前帮我查一下
```

单独跑 M-Score 初筛：

```bash
python3 scripts/mscore_feed.py 600519
```

### 报告示例（节选）

```
══════════════════════════════════════════════════
  财报排雷报告 / cn-stock-fraud-screen
══════════════════════════════════════════════════
  公司: 翠微股份 (603123)          ← 接口核实, 非记忆生成
  M-Score 初筛: -3.093 (阈值 -2.22, 中风险, 3 项信号)
──────────────────────────────────────────────────
  风险等级: 高风险   综合得分: 26
──────────────────────────────────────────────────
  [FAIL] 2.3 高现金+高息借债: 货币资金21.33亿 > 有息负债9.69亿;
         利息费用/有息负债=9.75%(>基准+4pp); 现金收益率仅0.7%
         → 经典存贷双高形态
  [FAIL] 2.2 经营CF持续为负: 3.23, 2.45, -2.45, -3.16, -0.72 (近5年)
         → 连续3年为负
  ...
```

## 数据源

| 数据 | 来源 | 成本 |
|------|------|------|
| 三表（利润/资产/现金流） | 新浪 `quotes.sina.cn` | 免费，无 Key |
| 代码→公司名核实 | 腾讯 `qt.gtimg.cn` | 免费 |
| 年报公告/PDF、全历史违规检索 | 巨潮 `cninfo.com.cn` | 免费 |
| 行业、质押辅助 | 东财 `push2.eastmoney.com` | 免费 |

无需 Tushare/Wind/Choice 等付费数据终端。

## 项目结构

```
cn-stock-fraud-screen/
├── SKILL.md                    # Skill 定义（两阶段流水线工作流）
├── README.md
├── LICENSE
├── references/
│   └── checklist-rules.md      # 40 条规则的阈值与判定逻辑
├── scripts/
│   └── mscore_feed.py          # 新浪三表 → fraudwatch M-Score 初筛
└── CHANGELOG.md
```

## 归属与致谢

- **规则框架（32 条）**：改编自 [terancejiang/financial-report-minesweeper](https://github.com/terancejiang/financial-report-minesweeper)
- **方法论**：唐朝《手把手教你读财报》
- **M-Score 引擎**：[zack59309-maker/fraudwatch](https://github.com/zack59309-maker/fraudwatch)（MIT），模型出自 Beneish, M.D. (1999). *The Detection of Earnings Manipulation*
- **本仓库增补**：8 条规则（4.6/5.10/3.6/5.11/5.12/3.7/5.13/5.14）、两阶段流水线、新浪/巨潮/东财直连数据链路

## 免责声明

本工具仅供学习与研究。量化筛查存在固有误报（约 30%）与漏报（25-40%），
M-Score 与任何单一规则都**不是**舞弊结论；完全体外循环、审计合谋类舞弊
（Wirecard 型）超出财报筛查的能力边界。输出不构成投资建议，请以公司公告
原文为准并独立判断。A 股市场无法便捷做空个股，本工具定位是**排雷/回避**
而非做空决策。
