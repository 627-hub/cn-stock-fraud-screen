#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cn-stock-fraud-screen 40条红旗规则引擎
基于新浪三表(量化) + 年报PDF(定性) + 巨潮公告 对A股财报排雷。
用法: python3 evaluate_rules.py <code> [year]
输出: output/{code}/report.md + analysis_log.md
数据免费零鉴权。
"""
import sys, os, json, re, math
from datetime import date

OUT_BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")

# ── 三表解析 ──
def load_tables(code):
    p = os.path.join(OUT_BASE, code, "sina_tables.json")
    if not os.path.exists(p):
        return None, None, None
    with open(p, encoding="utf-8") as f:
        t = json.load(f)
    return t.get("lrb", []), t.get("fzb", []), t.get("llb", [])

def fnum(v):
    if v is None: return None
    try: return float(str(v).replace(",", ""))
    except (TypeError, ValueError): return None

def pick(rec, names):
    if not rec: return None
    for n in names:
        if n in rec:
            v = fnum(rec[n])
            if v is not None: return v
    return None

def annual_rows(rows, years=None):
    """筛选年报期(period 以1231结尾), 按年份正序"""
    ann = [r for r in rows if r.get("period") and r["period"].endswith("1231")]
    ann = sorted(ann, key=lambda r: r["period"])
    if years:
        ann = [r for r in ann if int(r["period"][:4]) in years]
    return ann

def yoy(cur, prev):
    if cur is None or prev is None or prev == 0: return None
    return (cur - prev) / abs(prev)

def pct(a, b):
    if b is None or b == 0: return None
    return a / b * 100

# ── 读数辅助 ──
def val(lrb, fzb, llb, year, lva, fza=None, lla=None):
    """从三表中取某年份科目值"""
    lrows = annual_rows(lrb); frows = annual_rows(fzb); rows = annual_rows(llb)
    for r in lrows:
        if int(r["period"][:4]) == year:
            v = pick(r, lva)
            if v is not None: return v
    return None

# 简化的按引用取科目(从对应表)
def get_col(rows, year, names):
    ann = annual_rows(rows)
    for r in ann:
        if int(r["period"][:4]) == year:
            return pick(r, names)
    return None

def load_pdf_text(code):
    p = os.path.join(OUT_BASE, code, f"annual_{code}.txt")
    if os.path.exists(p):
        with open(p, encoding="utf-8", errors="ignore") as f:
            return f.read()
    return ""

def load_anns(code):
    p = os.path.join(OUT_BASE, code, "cninfo_anns.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return []

# ── 审计意见(0.1) ──
def rule_0_1(text):
    # 优先匹配"审计意见类型"字段行的取值（最可靠: 会明确写"标准的无保留意见"）
    m = re.search(r"审计意见类型\s*[:：]?\s*([^\n]{0,30})", text)
    if m:
        s = m.group(1).strip()
        if "无保留" in s:
            return ("PASS", f"审计意见类型: {s}")
        if "保留" in s or "无法表示" in s or "否定" in s:
            return ("FAIL", f"审计意见类型: {s}")
        return ("PASS", f"审计意见类型: {s}")
    # 其次匹配"出具了...意见"
    m = re.search(r"出具了([^。]{0,20}意见)", text)
    if m:
        s = m.group(1)
        if "标准" in s or "无保留" in s:
            return ("PASS", f"审计意见: {s}")
        else:
            return ("FAIL", f"审计意见: {s}")
    return ("SKIP", "年报PDF未提取到审计意见表述")

# ── 审计机构变更(5.1) ──
def rule_5_1(text):
    # 找多个会计师事务所名称
    firms = set(re.findall(r"([\u4e00-\u9fa5]{2,12}会计师事务所(?:\(特殊普通合伙\))?)", text))
    # 去掉"天健会计师事务所"这类常见的，需要看是否同一家。简单判断叫法是否多样
    # 用"事务所"出现的不同名称数近似
    return ("SKIP", "需多年年报对比审计机构，本期PDF仅1年")

# 核心评估
def evaluate(code, year=None):
    lrb, fzb, llb = load_tables(code)
    if lrb is None:
        return {"error": "无三表数据"}
    text = load_pdf_text(code)
    anns = load_anns(code)
    lrows = annual_rows(lrb); frows = annual_rows(fzb); lrows_cf = annual_rows(llb)
    if not lrows:
        return {"error": "无年报期数据"}

    # 年报年份
    latest_y = int(lrows[-1]["period"][:4])
    if year is None:
        year = latest_y
    # 用于趋势的年份序列
    ys = [int(r["period"][:4]) for r in lrows][-6:]

    R = {}  # rule_no -> (verdict, detail)
    R["0.1"] = rule_0_1(text)

    # Rule 0.2 按时披露 —— 年报公告日期须在次年4月30日前 (Layer 0, FAIL=直接排除)
    # 用最新年报期(2025)的公告日期
    try:
        ad_path = os.path.join(OUT_BASE, "annual_dates_2025.json")
        if not os.path.exists(ad_path):
            ad_path = os.path.join(OUT_BASE, "annual_dates.json")
        if os.path.exists(ad_path):
            with open(ad_path, encoding="utf-8") as f:
                ad = json.load(f)
            ann_date = ad.get(code, {}).get("date", "")
            import datetime as _dt
            if ann_date:
                ann_dt = _dt.datetime.strptime(ann_date, "%Y-%m-%d").date()
                deadline = _dt.date(year + 1, 4, 30)  # 年报期(2025-12-31)次年的4月30日为披露截止
                if ann_dt <= deadline:
                    R["0.2"] = ("PASS", f"年报公告 {ann_date}，在截止日 {deadline} 前")
                else:
                    R["0.2"] = ("FAIL", f"年报公告 {ann_date}，晚于截止日 {deadline}")
            else:
                R["0.2"] = ("SKIP", "未获取到年报公告日期")
        else:
            R["0.2"] = ("SKIP", "未采集年报公告日期")
    except Exception as e:
        R["0.2"] = ("SKIP", f"延迟披露无法判定: {e}")

    def g(table_rows, y, names):
        return get_col(table_rows, y, names)

    # Layer1 利润表
    def rev(y): return g(lrows, y, ["营业收入"])
    def cogs(y): return g(lrows, y, ["营业成本"])
    def gm(y):
        r = rev(y); c = cogs(y)
        return (r - c) / r if r and c is not None and r > 0 else None
    def sel_exp(y): return g(lrows, y, ["销售费用"])
    def adm_exp(y): return g(lrows, y, ["管理费用"])
    def fin_exp(y): return g(lrows, y, ["财务费用"])
    def profit(y): return g(lrows, y, ["归属于母公司所有者的净利润"])
    def ar(y): return g(frows, y, ["应收账款"])
    def ap(y): return g(frows, y, ["应付账款"])
    def inv(y): return g(frows, y, ["存货"])
    def total_assets(y): return g(frows, y, ["资产总计"])
    def cash(y): return g(frows, y, ["货币资金"])
    def st_borr(y): return g(frows, y, ["短期借款"])
    def lt_borr(y): return g(frows, y, ["长期借款"])
    def bond_pay(y): return g(frows, y, ["应付债券"])
    def goodwill(y): return g(frows, y, ["商誉"])
    def eq_attr(y): return g(frows, y, ["归属于母公司所有者权益"])
    def oth_receiv(y): return g(frows, y, ["其他应收款"])
    def lt_exp(y): return g(frows, y, ["长期待摊费用"])
    def cip(y): return g(frows, y, ["在建工程"])
    def fixed_assets(y): return g(frows, y, ["固定资产"])
    def prepay(y): return g(frows, y, ["预付款项"])
    def contract_liab(y): return g(frows, y, ["合同负债"])
    def adv_receipt(y): return g(frows, y, ["预收款项"])
    def notes_recv(y): return g(frows, y, ["应收票据"])
    def oth_equity(y): return g(frows, y, ["其他权益工具"])

    def ocf(y): return g(lrows_cf, y, ["经营活动产生的现金流量净额"])
    def icf(y): return g(lrows_cf, y, ["投资活动产生的现金流量净额"])
    def cash_sales(y): return g(lrows_cf, y, ["销售商品、提供劳务收到的现金"])
    def capex(y): return g(lrows_cf, y, ["购建固定资产、无形资产和其他长期资产支付的现金"])
    def asset_impair(y): return g(lrows, y, ["资产减值损失"])
    def credit_impair(y): return g(lrows, y, ["信用减值损失"])

    def common_R():
        return R

    prev_y = year - 1

    # Rule 1.1 毛利率异常
    gm_c = gm(year); gm_p = gm(prev_y)
    if gm_c is not None and gm_p is not None:
        delta = (gm_c - gm_p) * 100
        if abs(delta) > 10: R["1.1"] = ("FAIL", f"毛利率 {gm_c:.2%} vs 上年 {gm_p:.2%}, YoY {delta:+.1f}pp")
        elif abs(delta) > 5: R["1.1"] = ("WARN", f"毛利率 YoY {delta:+.1f}pp")
        else: R["1.1"] = ("PASS", f"毛利率 {gm_c:.2%}, YoY {delta:+.1f}pp")
    else: R["1.1"] = ("SKIP", "毛利率数据不足")

    # Rule 1.2 毛利率↑+应收↑+应付↓
    ar_c = ar(year); ar_p = ar(prev_y); ap_c = ap(year); ap_p = ap(prev_y)
    cls = []
    if gm_c is not None and gm_p is not None and gm_c > gm_p: cls.append("毛利率↑")
    ar_g = yoy(ar_c, ar_p); rev_g = yoy(rev(year), rev(prev_y))
    if ar_g is not None and rev_g is not None and ar_g > rev_g: cls.append("应收增速>营收")
    ap_g = yoy(ap_c, ap_p)
    if ap_g is not None and ap_g < 0: cls.append("应付↓")
    n = len(cls)
    if n >= 3: R["1.2"] = ("FAIL", "; ".join(cls))
    elif n == 2: R["1.2"] = ("WARN", "; ".join(cls))
    elif n == 1: R["1.2"] = ("PASS", "; ".join(cls) if cls else "无组合信号")
    else: R["1.2"] = ("PASS", "无组合信号")

    # Rule 1.3 运费(需PDF附注) → 通常SKIP
    if "运输" in text or "运费" in text:
        R["1.3"] = ("SKIP", "运费披露需读附注明细，本期暂不判定")
    else: R["1.3"] = ("SKIP", "未单独披露运费")

    # Rule 1.4 其他业务收入占比
    oth = g(lrows, year, ["其他业务收入"])
    if oth is not None and rev(year):
        ratio = oth / rev(year) * 100
        oth_p = g(lrows, prev_y, ["其他业务收入"])
        ratio_p = (oth_p / rev(prev_y) * 100) if oth_p is not None and rev(prev_y) else None
        rc = ratio - ratio_p if ratio_p is not None else 0
        if ratio > 15 or rc > 10: R["1.4"] = ("FAIL", f"其他业务收入占营收 {ratio:.1f}%, 变动 {rc:+.1f}pp")
        elif ratio > 5 and rc > 3: R["1.4"] = ("WARN", f"其他业务收入占营收 {ratio:.1f}%, 变动 {rc:+.1f}pp")
        else: R["1.4"] = ("PASS", f"其他业务收入占营收 {ratio:.1f}%")
    else: R["1.4"] = ("SKIP", "无其他业务收入科目")

    # Rule 1.5 费用率异常下降
    exp_r = ((sel_exp(year) or 0) + (adm_exp(year) or 0) + (fin_exp(year) or 0)) / rev(year) if rev(year) else None
    y3 = [rev(y) for y in ys[-3:] if rev(y)]
    exp_3 = [((sel_exp(y) or 0)+(adm_exp(y) or 0)+(fin_exp(y) or 0)) for y in ys[-3:] if rev(y)]
    # 简化
    if exp_r is not None:
        exp_r_list = []
        for y in ys[-3:]:
            r = rev(y); e = (sel_exp(y) or 0)+(adm_exp(y) or 0)+(fin_exp(y) or 0)
            if r: exp_r_list.append(e/r*100)
        avg3 = sum(exp_r_list)/len(exp_r_list) if exp_r_list else None
        drop = avg3 - exp_r*100 if avg3 is not None else 0
        if drop > 5: R["1.5"] = ("FAIL", f"费用率 {exp_r*100:.1f}%, 近3年均值 {avg3:.1f}%, 下降 {drop:.1f}pp")
        elif drop > 3: R["1.5"] = ("WARN", f"费用率下降 {drop:.1f}pp")
        else: R["1.5"] = ("PASS", f"费用率 {exp_r*100:.1f}%")
    else: R["1.5"] = ("SKIP", "费用率数据不足")

    # Rule 1.6 资产减值暴增
    imp_c = abs(asset_impair(year) or 0) + abs(credit_impair(year) or 0)
    imp_p = abs(asset_impair(prev_y) or 0) + abs(credit_impair(prev_y) or 0)
    imp_yoy = yoy(imp_c, imp_p)
    imp_to_profit = pct(imp_c, abs(profit(year) or 0)) if profit(year) else None
    if imp_c > 0 and imp_yoy is not None:
        if imp_yoy > 1.0 or (imp_to_profit is not None and imp_to_profit > 5):
            R["1.6"] = ("FAIL", f"减值 {imp_c/1e8:.2f}亿, YoY {imp_yoy*100:.0f}%, 占净利 {imp_to_profit:.1f}%" if imp_to_profit else f"减值 YoY {imp_yoy*100:.0f}%")
        elif imp_yoy > 0.5:
            R["1.6"] = ("WARN", f"减值 YoY {imp_yoy*100:.0f}%")
        else: R["1.6"] = ("PASS", f"减值 {imp_c/1e8:.2f}亿, YoY {imp_yoy*100:.0f}%")
    else: R["1.6"] = ("PASS", "减值需数据少")

    # Layer2 现金流
    # Rule 2.1 经营CF优+投资CF持续大额负
    y5 = ys[-5:] if len(ys)>=5 else ys
    cnt21 = 0
    for y in y5:
        o = ocf(y); i = icf(y)
        if o and i and o > 0 and abs(i) > o * 0.8: cnt21 += 1
    if cnt21 >= 4 and (ocf(prev_y) > 0): R["2.1"] = ("FAIL", f"近{len(y5)}年{len(y5)}年中{cnt21}年经营CF为正但投资CF大额流出")
    elif cnt21 >= 2: R["2.1"] = ("WARN", f"近{len(y5)}年{cnt21}年经营CF优但投资CF大额流出")
    else: R["2.1"] = ("PASS", f"近{len(y5)}年{cnt21}年触发")

    # Rule 2.2 经营CF持续为负
    neg_cnt = sum(1 for y in y5 if ocf(y) is not None and ocf(y) < 0)
    if neg_cnt >= len(y5)-1 or neg_cnt >= 3:
        R["2.2"] = ("FAIL", f"近{len(y5)}年{neg_cnt}年经营CF为负")
    elif neg_cnt == 2: R["2.2"] = ("WARN", f"近{len(y5)}年{neg_cnt}年经营CF为负")
    else: R["2.2"] = ("PASS", f"近{len(y5)}年{neg_cnt}年经营CF为负")

    # Rule 2.3 高现金+高息借债
    c_sh = cash(year); debt = (st_borr(year) or 0)+(lt_borr(year) or 0)+(bond_pay(year) or 0)
    fe = abs(fin_exp(year) or 0)
    if c_sh is not None and debt and debt > 0:
        implied = fe / debt * 100 if fe else 0
        if c_sh > debt and implied > 7: R["2.3"] = ("FAIL", f"货币资金{ c_sh/1e8:.1f}亿 > 有息负债{debt/1e8:.1f}亿, 隐含利率{implied:.1f}%")
        elif c_sh > debt*0.5 and implied > 5: R["2.3"] = ("WARN", f"现金/有息负债比例高, 隐含利率{implied:.1f}%")
        else: R["2.3"] = ("PASS", f"现金{ c_sh/1e8:.1f}亿, 有息负债{debt/1e8:.1f}亿, 隐含利率{implied:.1f}%")
    else: R["2.3"] = ("SKIP", "有息负债数据不足")

    # Layer3 资产负债表
    # Rule 3.1 应收增速>收入增速
    if ar_g is not None and rev_g is not None and rev_g > 0:
        ratio = ar_g / rev_g
        if ar(year) and ar(year) < rev(year)*0.05: R["3.1"] = ("PASS", "应收占营收<5%")
        elif ratio > 2.0: R["3.1"] = ("FAIL", f"应收增速 {ar_g*100:.0f}% 远超营收增速 {rev_g*100:.0f}%, 比值{ratio:.1f}")
        elif ratio > 1.5: R["3.1"] = ("WARN", f"应收增速/营收增速={ratio:.1f}")
        else: R["3.1"] = ("PASS", f"应收增速/营收增速={ratio:.1f}")
    elif rev_g is not None and rev_g < 0 and ar_g is not None and ar_g > 0:
        R["3.1"] = ("WARN", "营收下滑但应收增长")
    else: R["3.1"] = ("SKIP", "数据不足")

    # Rule 3.2 存货周转↓+毛利率↑
    inv_c = inv(year); inv_p = inv(prev_y)
    turn_c = (cogs(year)/ ((inv_c+inv_p)/2)) if inv_c and inv_p and (inv_c+inv_p)>0 else None
    inv_p2 = inv(prev_y); inv_p3 = inv(prev_y-1)
    turn_p = (cogs(prev_y)/ ((inv_p2+inv_p3)/2)) if inv_p2 and inv_p3 and (inv_p2+inv_p3)>0 else None
    if turn_c is not None and turn_p is not None and turn_p > 0:
        tt = (turn_c - turn_p)/turn_p*100
        if tt < -20 and gm_c is not None and gm_p is not None and (gm_c-gm_p) > 0.03:
            R["3.2"] = ("FAIL", f"存货周转率 {turn_c:.2f} vs {turn_p:.2f} ({(tt):.0f}%), 但毛利率↑{(gm_c-gm_p)*100:.1f}pp")
        elif tt < -10 and gm_c is not None and gm_p is not None and gm_c > gm_p:
            R["3.2"] = ("WARN", f"存货周转率下降{tt:.0f}% 但毛利率↑")
        else: R["3.2"] = ("PASS", f"存货周转率 {turn_c:.2f} vs {turn_p:.2f} ({(tt):.0f}%)")
    else: R["3.2"] = ("SKIP", "存货数据不足")

    # Rule 3.3 在建工程不转固
    if cip(year) and cip(prev_y):
        cg = yoy(cip(year), cip(prev_y))
        fg = yoy(fixed_assets(year), fixed_assets(prev_y))
        if cg is not None and cg > 0.3 and fg is not None and fg < cg*0.5:
            R["3.3"] = ("WARN", f"在建工程增{cg*100:.0f}% 但固定资产仅增{fg*100:.0f}%")
        else: R["3.3"] = ("PASS", f"在建工程增{(cg*100 if cg else 0):.0f}%")
    else: R["3.3"] = ("SKIP", "在建工程数据不足")

    # Rule 3.4 长期待摊费用大增
    if lt_exp(year) and total_assets(year):
        yoy_lte = yoy(lt_exp(year), lt_exp(prev_y))
        ratio_ta = lt_exp(year)/total_assets(year)*100
        if yoy_lte is not None and yoy_lte > 1.0: R["3.4"] = ("FAIL", f"长期待摊费用 YoY {yoy_lte*100:.0f}%")
        elif yoy_lte is not None and yoy_lte > 0.5: R["3.4"] = ("WARN", f"长期待摊费用 YoY {yoy_lte*100:.0f}%")
        else: R["3.4"] = ("PASS", f"长期待摊占比{ratio_ta:.1f}%")
    else: R["3.4"] = ("SKIP", "数据不足")

    # Rule 3.5 坏账计提(同行, 无则SKIP)
    R["3.5"] = ("SKIP", "无同行坏账计提对比数据")

    # Rule 3.6 预付款项异常
    if prepay(year) and rev(year):
        pr_r = prepay(year)/rev(year)*100
        pr_ta = prepay(year)/total_assets(year)*100
        yoy_pr = yoy(prepay(year), prepay(prev_y))
        if pr_r > 20 and yoy_pr is not None and yoy_pr > 1.0:
            R["3.6"] = ("FAIL", f"预付/营收 {pr_r:.1f}%, YoY {yoy_pr*100:.0f}%")
        elif pr_r > 10 and yoy_pr is not None and yoy_pr > 0.5:
            R["3.6"] = ("WARN", f"预付/营收 {pr_r:.1f}%, YoY {yoy_pr*100:.0f}%")
        else: R["3.6"] = ("PASS", f"预付/营收 {pr_r:.1f}%")
    else: R["3.6"] = ("SKIP", "预付款项数据不足")

    # Rule 3.7 渠道压货
    cl_c = (contract_liab(year) or 0)+(adv_receipt(year) or 0)
    cl_p = (contract_liab(prev_y) or 0)+(adv_receipt(prev_y) or 0)
    if rev(year):
        cl_r = cl_c/rev(year)*100
        if cl_c and cl_p:
            cl_g = (cl_c-cl_p)/cl_p
            if rev_g is not None and rev(year) > rev(prev_y) and cl_g < -0.2:
                R["3.7"] = ("WARN", f"合同负债+预收降{cl_g*100:.0f}% 但营收增")
            else: R["3.7"] = ("PASS", f"合同负债/营收 {cl_r:.1f}%")
        else: R["3.7"] = ("PASS", f"合同负债/营收 {cl_r:.1f}%")
    else: R["3.7"] = ("SKIP", "数据不足")

    # Layer4 交叉验证
    # Rule 4.1 经营CF/净利<1
    cfp_cnt = 0
    for y in y5:
        o = ocf(y); p = profit(y)
        if o is not None and p is not None and p > 0 and o/p < 1: cfp_cnt += 1
    if cfp_cnt >= 3: R["4.1"] = ("FAIL", f"近{len(y5)}年{cfp_cnt}年经营CF/净利<1")
    elif cfp_cnt == 2: R["4.1"] = ("WARN", f"近{len(y5)}年{cfp_cnt}年经营CF/净利<1")
    else: R["4.1"] = ("PASS", f"近{len(y5)}年{cfp_cnt}年经营CF/净利<1")

    # Rule 4.2 销售收现/营收<1
    cs = cash_sales(year); rr = rev(year)
    if cs is not None and rr and rr > 0:
        c2r = cs/rr
        if c2r < 0.8: R["4.2"] = ("FAIL", f"销售收现/营收 = {c2r:.2f}")
        elif c2r < 0.9: R["4.2"] = ("WARN", f"销售收现/营收 = {c2r:.2f}")
        else: R["4.2"] = ("PASS", f"销售收现/营收 = {c2r:.2f}")
    else: R["4.2"] = ("SKIP", "数据不足")

    # Rule 4.3 利润膨胀→资产膨胀
    ag = yoy(total_assets(year), total_assets(prev_y)); rg = rev_g; pg = yoy(profit(year), profit(prev_y))
    if ag is not None and rg is not None and rg > 0:
        if ag > rg*3 and pg is not None and pg > 0: R["4.3"] = ("FAIL", f"资产增速{ag*100:.0f}% >> 营收增速{rg*100:.0f}% 且利润增")
        elif ag > rg*2 and pg is not None and pg > 0: R["4.3"] = ("WARN", f"资产增速{ag*100:.0f}% >> 营收增速{rg*100:.0f}%")
        else: R["4.3"] = ("PASS", f"资产增速{ag*100:.0f}% vs 营收增速{rg*100:.0f}%")
    else: R["4.3"] = ("PASS", "资产/营收增速数据有限")

    # Rule 4.4 核心利润 vs 净利背离
    cp = (rev(year) or 0)-(cogs(year) or 0)-(sel_exp(year) or 0)-(adm_exp(year) or 0)-(fin_exp(year) or 0)
    np = profit(year)
    if cp is not None and np is not None and np != 0:
        div = abs(cp-np)/abs(np)*100
        if div > 40 and np > 0: R["4.4"] = ("FAIL", f"核心利润{cp/1e8:.1f}亿 vs 净利{np/1e8:.1f}亿, 背离{div:.0f}%")
        elif div > 20: R["4.4"] = ("WARN", f"核心利润与净利背离{div:.0f}%")
        else: R["4.4"] = ("PASS", f"核心利润与净利背离{div:.0f}%")
    else: R["4.4"] = ("PASS", f"核心利润{ (cp or 0)/1e8:.1f}亿")

    # Rule 4.5 净利增长+FCF持续为负
    fcf_cnt = 0
    for y in y5:
        p = profit(y); o = ocf(y); cx = capex(y)
        if p is not None and o is not None and cx is not None and p > 0 and (o-cx) < 0:
            fcf_cnt += 1
    if fcf_cnt >= 3: R["4.5"] = ("FAIL", f"近{len(y5)}年{fcf_cnt}年净利增长但FCF为负")
    elif fcf_cnt == 2: R["4.5"] = ("WARN", f"近{len(y5)}年{fcf_cnt}年净利增但FCF为负")
    else: R["4.5"] = ("PASS", f"近{len(y5)}年{fcf_cnt}年FCF为负")

    # Rule 4.6 少数股东权益/损益背离
    min_eq = g(frows, year, ["少数股东权益"]); all_eq = g(frows, year, ["所有者权益合计"])
    min_pnl = g(lrows, year, ["少数股东损益"]); tot_pnl = g(lrows, year, ["净利润"])
    if min_eq is not None and all_eq and all_eq > 0 and min_pnl is not None and tot_pnl and tot_pnl != 0:
        eq_r = min_eq/all_eq*100; pnl_r = min_pnl/tot_pnl*100
        if eq_r >= 20 and pnl_r < 5:
            R["4.6"] = ("FAIL", f"少数股东权益占{eq_r:.0f}% 但损益仅占{pnl_r:.0f}%")
        elif eq_r >= 10 and pnl_r < eq_r*0.3:
            R["4.6"] = ("WARN", f"少数股东权益{eq_r:.0f}% vs 损益{pnl_r:.0f}%")
        else: R["4.6"] = ("PASS", f"少数股东权益{eq_r:.0f}% vs 损益{pnl_r:.0f}%")
    else: R["4.6"] = ("SKIP", "少数股东数据不足")

    # Layer5 非财务
    R["5.1"] = ("SKIP", "需多年审计机构对比")
    # 5.2/5.3/5.4 需PDF, 用关键词近似
    # Rule 5.9 监管处罚/立案 —— 基于"执法主体 + 被处分对象"结构化判定，避免误报/漏报
    # 核心: 只认证券监管执法主体(证监会/交易所/证监局/公安经济犯罪)对 公司/董监高/控股股东 的肯定性处分
    # 明确排除: ①承诺函模板("若上市公司退市则...") ②环保"无处罚" ③民事案件"法院立案" ④否定性("不存在/无")
    reg_kw = ["立案", "警示函", "行政处罚", "立案调查", "公开谴责", "通报批评", "监管措施", "纪律处分", "处罚决定"]
    hits = [k for k in reg_kw if k in text]

    # 证券执法主体
    REG_ORG = r"(?:中国证券监督管理委员会|中国证监会|证监会|深圳证券交易所|上海证券交易所|深交所|上交所|证监局|证券监管局|公安部|公安机关)"
    # 被处分对象
    TARGET = r"(?:公司|该公司|本公司|发行人|子公司|控股股东|实际控制人|董事长|总经理|董[事监]事|高级管理人员|财务负责人|董监高)"

    # 真实处分的肯定性句式
    real_patterns = [
        # 被执法主体通报批评/公开谴责/纪律处分/出警示函/立案调查
        rf"被?{REG_ORG}[^。]{{0,30}}(?:给予|作出)?[^。]{{0,10}}(?:通报批评|公开谴责|纪律处分|警示函|行政处罚|责令改正|限制)",
        rf"{TARGET}[^。]{{0,40}}(?:被|受到|收到)[^。]{{0,20}}(?:通报批评|公开谴责|纪律处分|警示函|行政处罚|限制业务|责令)",
        # 收到证监局《...决定书》(如锦龙子公司中山证券)
        rf"(?:收到|被出具|被采取)[^。]{{0,40}}?(?:行政监管措施决定书|行政监管措施|监管措施决定书|立案[调]?[查]?)",
        # 证监会立案调查
        rf"{REG_ORG}(?:已|已对|对)[^。]{{0,30}}立案(?:调查|侦查)",
    ]
    is_real = False
    matched_hint = ""
    # 强排除语境: 合规性陈述 / 承诺函 / 民事法院立案 / 环保无处罚 / 一般合规整改
    EXCLUDE_CTX = r"(?:承诺|若[^。]{0,30}(?:证监会|公安)[^。]{0,20}立案|案[件][调]?[查]?结论明确|不转让|减持|公开发行|符合.{0,12}(?:法律|法规|规定|准则|条件)|依据.{0,10}(?:法律|法规|准则)|股票上市规则|相关规则|补偿投资者|赔偿投资者|完善(?:公司)?治理|信息披露|环保|土地|摇号|纠纷|诉讼|仲裁|法院|如造成|不存在|不适用|未发生|未受到|未收到)"
    for p in real_patterns:
        for m in re.finditer(p, text):
            ctx = text[max(0, m.start()-70):m.end()+60]
            # 扩大否定/排除扫描(PDF中否定词常在句首, 距离处分动词较远)
            if any(neg in ctx for neg in ["不存在","未发生","未受到","未收到","是否","不适用","未曾","并未","尚不存在","亦不存在","不存在因"]):
                continue
            # 强排除: 承诺函模板 / 合规陈述 / 民事法院 / 环保
            if re.search(EXCLUDE_CTX, ctx):
                continue
            is_real = True
            matched_hint = ctx[:70]
            break
        if is_real: break

    # 兜底: 明确的"被...通报批评/公开谴责" (欢瑞: 2022被深交所通报批评)
    if not is_real:
        for m in re.finditer(r"(?:被|受到|给予)[^。]{0,50}(?:通报批评|公开谴责)", text):
            ctx = text[max(0, m.start()-70):m.end()+30]
            if any(neg in ctx for neg in ["是否存在","不存在","不适用","未发生","未受到"]):
                continue
            if re.search(EXCLUDE_CTX, ctx):
                continue
            is_real = True
            matched_hint = ctx[:60]
            break

    if is_real:
        R["5.9"] = ("WARN", f"年报含证券监管真实处分表述: {', '.join(hits)}（如: {matched_hint}…）")
    else:
        # 无真实处分: 检查是否存在"不存在处罚"之类的明确否定，以增强报告可信度
        if re.search(r"不存在(?:处罚|违规|监管)|未(?:受到|发生)(?:处罚|违规)", text):
            R["5.9"] = ("PASS", "年报声明不存在证券监管处罚")
        else:
            R["5.9"] = ("PASS", "未发现证券监管真实处分(模板/民事/环保/否定已排除)")

    # Rule 5.7 商誉过大
    gd = goodwill(year); eq = eq_attr(year)
    if gd is not None and eq and eq > 0:
        gd_r = gd/eq*100
        if gd_r > 40: R["5.7"] = ("FAIL", f"商誉/归母权益 {gd_r:.0f}%")
        elif gd_r > 20: R["5.7"] = ("WARN", f"商誉/归母权益 {gd_r:.0f}%")
        else: R["5.7"] = ("PASS", f"商誉/归母权益 {gd_r:.0f}%")
    else: R["5.7"] = ("PASS", "无商誉或数据不足")

    # Rule 5.9 监管处罚/立案 —— 识别否定性表述，避免误报
    # 找"非经营性占用": 若有"否"或"不适用"紧跟则 PASS
    occ = re.search(r"非经营性占用资金情况\s*([^\n，。]{0,20})", text)
    if occ:
        s = occ.group(1).strip()
        # 勾选框: □适用 ☒不适用 / √不适用 —— 有"不适用"即无占用
        if "不适用" in s or "否" in s or "无" in s:
            R["5.8"] = ("PASS", "年报声明无非经营性占用资金情况")
        else:
            R["5.8"] = ("WARN", f"非经营性占用资金情况: {s}")
    else:
        othr = oth_receiv(year); ta = total_assets(year)
        if othr is not None and ta and ta > 0:
            othr_r = othr/ta*100
            if othr_r > 5: R["5.8"] = ("FAIL", f"其他应收款/总资产 {othr_r:.1f}%")
            elif othr_r > 3: R["5.8"] = ("WARN", f"其他应收款/总资产 {othr_r:.1f}%")
            else: R["5.8"] = ("PASS", f"其他应收款/总资产 {othr_r:.1f}%")
        else: R["5.8"] = ("SKIP", "其他应收款数据不足")

    # Rule 5.10 明股实债
    if oth_equity(year) and eq:
        oe_r = oth_equity(year)/eq*100
        if oe_r > 10: R["5.10"] = ("WARN", f"其他权益工具/归母权益 {oe_r:.0f}%")
        else: R["5.10"] = ("PASS", f"其他权益工具/归母权益 {oe_r:.0f}%")
    else: R["5.10"] = ("PASS", "无其他权益工具(永续债)")

    # Rule 5.11 大股东高比例质押 —— 用年报"控股股东/第一大股东累计质押占其持股达80%"条款勾选 + 前十名股东质押股数
    # 判据: 该条款勾选"适用" → 质押≥80% FAIL; 未勾选但前十名股东有质押 → 按比例 WARN
    try:
        # 提取 80% 条款勾选状态
        m80 = re.search(r"累计质押股份数量[\s\S]{0,70}?80%", text)
        clause_apply = False
        if m80:
            seg = text[m80.start():m80.start()+45]
            # 勾选"适用"的chk符: √/☒/\uF052/ + 适用 (PDF转换变体符)
            if re.search(r"(?:√|☒|\uf052|\uf04e|\u2713)\s?适用", seg) or "\uf052适用" in seg or "√适用" in seg or "☒适用" in seg:
                clause_apply = True
        # 寻找前十名股东表格中"质押"股数 —— 限定在"持股情况/质押、标记或冻结"表头后的表格段内提取
        # 避免全文"应收票据质押/货币资金冻结"等误报
        max_pledge_pct = 0.0
        # 定位表头
        segs = []
        for hd in ["前十名股东持股情况", "前 10 名股东持股情况", "质押、标记或冻结情况", "质押、标记或冻结"]:
            idx = text.find(hd)
            if idx != -1:
                segs.append(text[idx:idx+2200])
                break
        if segs:
            seg = segs[0]
            # 抓 "持股比例 X% ... 质押 Y股" → 计算质押率 = 质押股数/持股股数
            max_pledge_pct = 0.0   # 持股比例(大股东持股%)
            max_pledge_rate = 0.0  # 质押率(质押股数/持股股数)
            # 格式A: "9.22% 63,508,747 0 0 质押 63,508,747" (比例→持股股数→...→质押股数)
            for m in re.finditer(r"([\d.]+)%\s+([\d,]+)\s+[\d,.]*\s*[\d,.]*\s*(?:质押|冻结)\s*([\d,]+)", seg):
                try:
                    hold_pct = float(m.group(1)); hold_shares = float(m.group(2).replace(",",""))
                    pled_shares = float(m.group(3).replace(",",""))
                    if 0.5 <= hold_pct <= 75:
                        max_pledge_pct = max(max_pledge_pct, hold_pct)
                        if hold_shares > 0:
                            rate = pled_shares / hold_shares * 100
                            max_pledge_rate = max(max_pledge_rate, rate)
                except (ValueError, ZeroDivisionError):
                    pass
            # 格式B: "20.93 0 质押 58,180,000" (国芳: 比例 0 质押股数, 持股数在别列)
            for m in re.finditer(r"([\d.]+)\s+[\d.]*\s*(?:质押|冻结)\s*([\d,]+)", seg):
                v = float(m.group(1))
                if 0.5 <= v <= 75:
                    max_pledge_pct = max(max_pledge_pct, v)
        # 兜底信号: 表格解析失败但年报出现"司法冻结/股份冻结"且涉及大股东持股 → 大股东质押风险
        frozen_flag = bool(re.search(r"(司法冻结|股份冻结|股票冻结|冻结.*股权)", text)) and \
                      bool(re.search(r"(质押|冻结)", text))
        # 80% 条款优先
        if clause_apply:
            R["5.11"] = ("FAIL", "控股股东/第一大股东及其一致行动人累计质押达其所持股份80%以上 (年报适用)")
        elif max_pledge_pct >= 50 or max_pledge_rate >= 80:
            R["5.11"] = ("FAIL", f"大股东大额质押: 持股{max_pledge_pct:.0f}%, 质押率{max_pledge_rate:.0f}%")
        elif max_pledge_pct >= 10 or max_pledge_rate >= 50:
            R["5.11"] = ("WARN", f"大股东质押: 持股{max_pledge_pct:.0f}%, 质押率{max_pledge_rate:.0f}%")
        elif frozen_flag:
            R["5.11"] = ("WARN", "年报含大股东股份司法冻结/质押信号, 需人工核实比例")
        else:
            R["5.11"] = ("PASS", "控股东/主要股东未见高质押(年报条款<80%, 前十名质押比例低或无损)")
    except Exception as e:
        R["5.11"] = ("SKIP", f"质押数据解析失败: {e}")

    # Rule 5.13 造假前科/重述 —— 巨潮全历史检索
    # 读取已采集的 cninfo_history.json; 若未采集则 SKIP
    hist_path = os.path.join(OUT_BASE, code, "cninfo_history.json")
    if os.path.exists(hist_path):
        with open(hist_path, encoding="utf-8") as f:
            hist = json.load(f)
        # 收集 D组(立案/处罚)与 B组(差错更正) 的标题+日期
        d_titles = []; b_titles = []
        for gh in hist.get("D_立案/处罚", []):
            for h in gh.get("hits", []):
                if h.get("title") and "error" not in h:
                    d_titles.append((re.sub(r"<[^>]+>", "", h["title"]), h.get("date","")))
        for gh in hist.get("B_差错更正/重述", []):
            for h in gh.get("hits", []):
                t = h.get("title","")
                if "error" not in t and any(k in t for k in ["差错更正","追溯调整","前期差错"]):
                    b_titles.append((re.sub(r"<[^>]+>", "", t), h.get("date","")))
        # 取立案/处罚的最新年份(判断"近3年/近5年")
        import datetime
        def latest_year(items):
            years = []
            for t, dt in items:
                if dt:
                    y = dt[:4]
                    if y.isdigit(): years.append(int(y))
            return max(years) if years else 0
        cur_y = date.today().year
        d_set = set(t for t, _ in d_titles); b_set = set(t for t, _ in b_titles)
        d_latest = latest_year(d_titles)
        penalty = 0
        reason = []
        # 立案告知书/立案调查: 近3年 FAIL, 3-5年 WARN
        if any("立案" in t and ("告知书" in t or "通知" in t or "调查" in t) for t in d_set):
            if d_latest >= cur_y - 3:
                penalty = 7; reason.append(f"收到中国证监会立案告知书/立案调查({d_latest}年)")
            elif d_latest >= cur_y - 5:
                penalty = 3; reason.append(f"历史收到立案调查通知({d_latest}年)")
            else:
                penalty = max(penalty, 3); reason.append(f"历史立案调查({d_latest}年)")
        # 行政处罚决定书: ≥2次 FAIL, 1次 WARN
        n_pen = sum(1 for t in d_set if "处罚决定书" in t or "行政处罚决定" in t)
        if n_pen >= 2:
            penalty = max(penalty, 7); reason.append(f"历史收到证监会行政处罚决定书 ×{n_pen}次")
        elif n_pen == 1:
            penalty = max(penalty, 3); reason.append("历史收到证监会行政处罚决定书 ×1次")
        # 事先告知书
        if any("事先告知书" in t for t in d_set):
            penalty = max(penalty, 3); reason.append("历史收到行政处罚事先告知书")
        # 会计差错更正/追溯调整
        if any("差错更正" in t for t in b_set):
            if any("追溯调整" in t for t in b_set):
                penalty = max(penalty, 7); reason.append("前期会计差错更正及追溯调整")
            else:
                penalty = max(penalty, 3); reason.append("前期会计差错更正")

        if penalty >= 7:
            R["5.13"] = ("FAIL", f"造假前科: {' + '.join(reason)} (巨潮全历史检索)")
        elif penalty >= 3:
            R["5.13"] = ("WARN", f"前科/重述信号: {' + '.join(reason)} (巨潮全历史检索)")
        else:
            R["5.13"] = ("PASS", "巨潮全历史检索未发现立案处罚/差错更正前科")
    else:
        R["5.13"] = ("SKIP", "未采集巨潮历史检索数据")

    # Rule 5.14 离岸架构
    if re.search(r"BVI|开曼|百慕大|维尔京|香港|SPV|离岸|英属", text):
        R["5.14"] = ("WARN", "年报含离岸/境外主体表述，需核实股权结构")
    else: R["5.14"] = ("PASS", "无离岸架构关键词")

    # Layer6 行业
    # Rule 6.1 农林渔牧
    agri_kw = ["农业", "林业", "养殖", "种植", "饲料", "畜牧", "水产"]
    ind = text[:500] + " ".join(re.findall(r"[\u4e00-\u9fa5]{2,6}(?:公司|股份|集团)", text[:2000]))
    R["6.1"] = ("WARN", "农林渔牧行业(生物资产难以审计)") if any(k in text[:3000] for k in agri_kw) else ("PASS", "非高风险行业")

    # Rule 6.2 研发资本化
    m_cap = re.search(r"资本化[^。]{0,20}?(\d+(?:\.\d+)?)%", text)
    R["6.2"] = ("SKIP", "研发资本化比例需读附注明细")

    # ── 评分 ──
    base = {"1":(2,5),"2":(3,6),"3":(2,5),"4":(3,7),"5":(1,3),"6":(1,3)}
    total = 0; n_warn=0; n_fail=0; n_skip=0
    for k, (v, d) in R.items():
        layer = k.split(".")[0]
        if v == "WARN":
            total += base.get(layer,(1,1))[0]; n_warn += 1
        elif v == "FAIL":
            total += base.get(layer,(1,1))[1]; n_fail += 1
        elif v == "SKIP":
            n_skip += 1
    # 组合加分
    if R.get("3.2",("",))[0]=="FAIL": total += 10
    if R.get("2.3",("",))[0]=="FAIL" and R.get("4.1",("",))[0]=="FAIL": total += 8
    if R.get("1.2",("",))[0]=="FAIL" and R.get("3.1",("",))[0]=="FAIL": total += 6

    # 等级 (Layer 0 任一 FAIL → 直接排除; 5.13 造假前科 FAIL → 直接排除)
    if R.get("0.1",("PASS",))[0] == "FAIL" or R.get("0.2",("PASS",))[0] == "FAIL":
        level = "直接排除"
        if R.get("0.1",("PASS",))[0] == "FAIL":
            R["_exclude_reason"] = "审计意见非标准无保留 (Rule 0.1)"
        else:
            R["_exclude_reason"] = "年报未按时披露 (Rule 0.2)"
    elif R.get("5.13",("PASS",))[0] == "FAIL":
        level = "直接排除"
        R["_exclude_reason"] = "财务造假前科/立案处罚 (Rule 5.13)"
    elif total >= 46: level = "极高风险"
    elif total >= 26: level = "高风险"
    elif total >= 11: level = "中风险"
    else: level = "低风险"

    return {
        "code": code, "year": year, "total": total, "level": level,
        "n_fail": n_fail, "n_warn": n_warn, "n_skip": n_skip,
        "rules": R, "data_years": ys,
    }

if __name__ == "__main__":
    code = sys.argv[1]
    year = int(sys.argv[2]) if len(sys.argv) > 2 else None
    res = evaluate(code, year)
    if "error" in res:
        print(f"ERROR: {res['error']}"); sys.exit(1)
    print(f"===== {code} 2024年报 40条排雷 =====")
    print(f"风险等级: {res['level']} | 综合得分: {res['total']} | FAIL:{res['n_fail']} WARN:{res['n_warn']} SKIP:{res['n_skip']}")
    print("-"*70)
    for k in sorted(res["rules"].keys()):
        v, d = res["rules"][k]
        print(f"  [{v}] 规则{k}: {d}")
