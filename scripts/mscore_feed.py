#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股 Beneish M-Score 初筛 —— 新浪财报三表 → fraudwatch 引擎

数据源: 新浪财经 quotes.sina.cn (免费、无 Key)
模型:   Beneish M-Score (Beneish 1999), 引擎来自 fraudwatch (MIT)
        https://github.com/zack59309-maker/fraudwatch
用法:   python3 mscore_feed.py <6位股票代码> [期数, 默认16(含季报, 筛年报期)]
依赖:   pip install requests pandas numpy tabulate
        pip install git+https://github.com/zack59309-maker/fraudwatch.git

说明:
- 折旧以"累计折旧逐年差额"近似(忽略处置影响), DEPI 为近似值
- 净利润为归母口径; 全部科目换算为亿元
- M-Score > -2.22 为灰区预警信号, 是筛查参考而非定性结论
"""
import sys
import json
import requests

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


def stock_name(code: str):
    """腾讯行情接口核实 代码→公司名 (防代码张冠李戴)"""
    try:
        prefix = "sh" if code.startswith("6") else "sz"
        r = requests.get(f"https://qt.gtimg.cn/q={prefix}{code}",
                         headers={**UA, "Referer": "https://gu.qq.com/"}, timeout=10)
        r.encoding = "gbk"
        return r.text.split("~")[1]
    except Exception:
        return None


def sina_financial_report(code: str, report_type: str, num: int = 6) -> list:
    """新浪财报三表, 返回按报告期倒序的记录列表 (原始值单位: 元)"""
    prefix = "sh" if code.startswith("6") else "sz"
    url = "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
    params = {"paperCode": f"{prefix}{code}", "source": report_type, "type": "0",
              "page": "1", "num": str(num)}
    r = requests.get(url, params=params, headers=UA, timeout=15)
    report_list = r.json().get("result", {}).get("data", {}).get("report_list", {}) or {}
    rows = []
    for period in sorted(report_list.keys(), reverse=True)[:num]:
        rec = {"period": period}
        for it in report_list[period].get("data", []) or []:
            t, v = it.get("item_title"), it.get("item_value")
            if t and v is not None:
                rec[t] = v
        rows.append(rec)
    return rows


def fnum(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def pick(rec: dict, names: list):
    for n in names:
        if n in rec:
            v = fnum(rec[n])
            if v is not None:
                return v
    return None


def build_statements(code: str, num: int):
    """新浪三表(年报期) → fraudwatch FinancialStatement 列表(亿元, 按年份正序)"""
    from fraudwatch.models import FinancialStatement
    lrb = sina_financial_report(code, "lrb", num)
    fzb = sina_financial_report(code, "fzb", num)
    llb = sina_financial_report(code, "llb", num)
    annual = [t for t in zip(lrb, fzb, llb) if t[0]["period"].endswith("1231")]
    annual = sorted(annual, key=lambda t: t[0]["period"])
    out = []
    for i, (l, b, c) in enumerate(annual):
        acc_dep = pick(b, ["累计折旧"]) or 0.0
        acc_dep_prev = pick(annual[i - 1][1], ["累计折旧"]) if i > 0 else None
        dep = abs(acc_dep - acc_dep_prev) / 1e8 if acc_dep_prev is not None else 0.0
        out.append(FinancialStatement(
            code=code, year=int(l["period"][:4]),
            revenue=(pick(l, ["营业收入"]) or 0.0) / 1e8,
            cogs=(pick(l, ["营业成本"]) or 0.0) / 1e8,
            net_profit=(pick(l, ["归属于母公司所有者的净利润"]) or 0.0) / 1e8,
            operating_cf=(pick(c, ["经营活动产生的现金流量净额"]) or 0.0) / 1e8,
            total_assets=(pick(b, ["资产总计"]) or 0.0) / 1e8,
            current_assets=(pick(b, ["流动资产合计"]) or 0.0) / 1e8,
            current_liab=(pick(b, ["流动负债合计"]) or 0.0) / 1e8,
            total_liab=(pick(b, ["负债合计"]) or 0.0) / 1e8,
            accounts_recv=(pick(b, ["应收账款"]) or 0.0) / 1e8,
            depreciation=dep,
            sgna=((pick(l, ["销售费用"]) or 0.0) + (pick(l, ["管理费用"]) or 0.0)) / 1e8,
            gross_ppe=(pick(b, ["固定资产原值"]) or 0.0) / 1e8,
            intangibles=(pick(b, ["无形资产"]) or 0.0) / 1e8,
        ))
    return out


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    code = sys.argv[1].strip()
    num = int(sys.argv[2]) if len(sys.argv) > 2 else 16
    try:
        from fraudwatch.models import CompanyProfile
        from fraudwatch.rules.engine import detect
    except ImportError:
        print("ERROR: 未安装 fraudwatch\n  pip install git+https://github.com/zack59309-maker/fraudwatch.git")
        sys.exit(2)

    name = stock_name(code)
    if name is None:
        print(f"ERROR: 无法核实代码 {code} 的公司名称, 请检查代码是否正确")
        sys.exit(3)

    stmts = build_statements(code, num)
    if len(stmts) < 2:
        print(f"ERROR: 年报期数据不足({len(stmts)}期), M-Score 至少需要 2 期")
        sys.exit(4)

    profile = CompanyProfile(code=code, name=name, sector="", flagged=False, statements=stmts)
    result = detect(profile)
    result["_data_years"] = [s.year for s in stmts]
    result["_data_source"] = "sina_quotes(新浪财报三表)"
    print(json.dumps(result, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
