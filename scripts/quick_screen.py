#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股红旗快筛（批量版）— 10 条三表规则 + 4 条接口规则
规则阈值对齐 references/checklist-rules.md；用于多标的初筛（M-Score 盲区补筛），
命中 FAIL/WARN 再进 Phase 3 深度排雷。全流程免费无 Key。

秒级三表规则（新浪三表，与 M-Score 同源）:
  1.6 减值暴增 | 2.3 存贷双高 | 3.1 应收超收入 | 3.2 存货周转↓+毛利率↑
  3.3 在建工程滞固 | 4.1 OCF/净利 | 4.2 收现比 | 4.4 核心利润背离
  4.5 FCF | 4.6 少数股东背离

接口规则:
  0.1 审计意见(同花顺F10) | 0.2 按时披露(巨潮年报列表)
  5.9/5.13 监管前科(巨潮标题检索, 启发式) | 5.11 控股股东质押(东财)

用法:
  python3 quick_screen.py 600519 000858 600276 ...
  python3 quick_screen.py 600519 --out /tmp/quick.jsonl

内置防误报修正（2026-09 实测 41 只沉淀）:
  1.6: 纯 yoy 触发（占归母|利润|≤5%）FAIL 降级 WARN——A 股减值年度波动大，
       上期为 0/极小时 yoy 失真，以占比为主判据
  4.4: 核心利润公式未扣研发费用，高研发行业（医药/半导体/军工电子/新材料）
       系统性假阳——evidence 中附研发提示，人工降权（不自动改判）
限流: 新浪 0.8s / 巨潮 1.2s / 同花顺 1.5s / 东财 1.2s（41 只约 10-15 分钟）
"""
import sys, os, json, time, re, datetime
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mscore_feed import sina_financial_report, stock_name, fnum

_OUT_DEFAULT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "output", "quick_results.jsonl")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
      "Referer": "https://data.eastmoney.com/"}
THS_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
          "Referer": "http://basic.10jqka.com.cn/"}

# ─────────────────────────── 三表取数 ───────────────────────────

def fetch_yearly(code: str):
    """新浪三表 → 按年正序 [{period, 科目: float}]（仅年报期，最新 6 期）"""
    tables = {}
    for src in ("lrb", "fzb", "llb"):
        tables[src] = sina_financial_report(code, src, 24)
        time.sleep(0.8)
    lrb = {r["period"]: r for r in tables["lrb"] if r["period"].endswith("1231")}
    years = sorted(lrb.keys())[-6:]
    out = []
    for p in years:
        rec = {"period": p}
        for src in ("lrb", "fzb", "llb"):
            for it in tables[src]:
                if it["period"] == p:
                    for t, v in it.items():
                        if t != "period":
                            rec[t] = fnum(v)
        out.append(rec)
    return out

def g(rec, *names, default=None):
    for n in names:
        if n in rec and rec[n] is not None:
            return rec[n]
    return default

# ─────────────────────────── 10 条三表规则 ───────────────────────────

def r16_impair(y):
    """1.6 减值暴增: FAIL 占归母|利润|>5%（或纯yoy>100%）| WARN yoy>50% / 纯yoy触发"""
    now, prev = y[-1], y[-2]
    imp = abs(g(now, "资产减值损失", default=0) or 0) + abs(g(now, "信用减值损失", default=0) or 0)
    imp_p = abs(g(prev, "资产减值损失", default=0) or 0) + abs(g(prev, "信用减值损失", default=0) or 0)
    profit = abs(g(now, "归属于母公司所有者的净利润", default=0) or 0)
    if imp == 0:
        return "PASS", "减值合计0"
    yoy = (imp - imp_p) / imp_p * 100 if imp_p > 0 else 999.0
    ratio = imp / profit * 100 if profit > 0 else None
    ev = f"减值{imp/1e8:.2f}亿 yoy{yoy:+.0f}%"
    if ratio is not None:
        ev += f" 占归母|利润|{ratio:.1f}%"
    if ratio is not None and ratio > 5:
        return "FAIL", ev
    if yoy > 100 and (ratio is None or ratio <= 5):
        return "WARN", ev + " [纯yoy触发→降权]"
    if yoy > 50:
        return "WARN", ev
    return "PASS", ev

def r23_cash_debt(y):
    """2.3 存贷双高: FAIL cash>debt且隐含利率>7.5% | WARN cash>0.5debt且>5.5%
    注: 债务基数极小时(如仅租赁)隐含利率失真, evidence 附现金/债务绝对额供人工复核"""
    now = y[-1]
    cash = g(now, "货币资金", default=0) or 0
    debt = (g(now, "短期借款", default=0) or 0) + (g(now, "长期借款", default=0) or 0) + (g(now, "应付债券", default=0) or 0)
    if debt <= 0:
        return "PASS", f"无有息借债(现金{cash/1e8:.2f}亿)"
    fin = abs(g(now, "财务费用", default=0) or 0) or abs(g(now, "利息费用", default=0) or 0)
    implied = fin / debt * 100
    ev = f"现金{cash/1e8:.2f}亿/有息债{debt/1e8:.2f}亿 隐含利率{implied:.1f}%"
    if debt < cash * 0.3:
        # 存贷双高需要"大现金+大债务"双大；债务基数<现金30%时隐含利率必然失真
        # （实测: 某标的 0.41/0.18亿→"利率27%"实为净收益; 另一标的 7.8/0.6亿→29%）
        return "WARN", ev + " [债务基数<现金30%,利率失真,人工复核]"
    if cash > debt and implied > 7.5:
        return "FAIL", ev
    if cash > 0.5 * debt and implied > 5.5:
        return "WARN", ev
    return "PASS", ev

def _yoy(cur, prev):
    if cur is None or prev is None or prev == 0:
        return None
    return (cur - prev) / abs(prev) * 100

def r31_ar(y):
    """3.1 应收增速>收入增速: FAIL ratio>2 | WARN ratio>1.5 或营收降应收升"""
    now, prev = y[-1], y[-2]
    ar = g(now, "应收账款")
    rev = g(now, "营业收入")
    rev_p = g(prev, "营业收入")
    if ar is None or not rev:
        return "SKIP", "应收/营收科目缺失"
    if ar < rev * 0.05:
        return "PASS", f"应收{ar/1e8:.2f}亿<营收5%"
    ar_g = _yoy(ar, g(prev, "应收账款"))
    rev_g = _yoy(rev, rev_p)
    if ar_g is None or rev_g is None:
        return "SKIP", "上年数据缺失"
    ev = f"应收{ar_g:+.1f}% vs 营收{rev_g:+.1f}%"
    if rev_g <= 0 and ar_g > 0:
        return "WARN", ev + " 营收降应收升"
    if rev_g <= 0:
        return "PASS", ev
    ratio = ar_g / rev_g
    if ratio > 2.0:
        return "FAIL", ev + f" 比{ratio:.2f}"
    if ratio > 1.5:
        return "WARN", ev + f" 比{ratio:.2f}"
    return "PASS", ev

def r32_inventory(y):
    """3.2 存货周转↓+毛利率↑: FAIL周转降>20%且毛利率升>3pp | WARN降>10%且毛利率升 (FAIL 触发combo+10)"""
    now, prev = y[-1], y[-2]
    inv, inv_p = g(now, "存货"), g(prev, "存货")
    cogs, cogs_p = g(now, "营业成本"), g(prev, "营业成本")
    rev, rev_p = g(now, "营业收入"), g(prev, "营业收入")
    if inv is None or inv == 0 or inv_p in (None, 0):
        return "SKIP", "存货缺失(轻资产/贸易型)"
    if not (cogs and cogs_p and rev and rev_p):
        return "SKIP", "成本/营收缺失"
    inv_pp = g(y[-3], "存货") if len(y) >= 3 and g(y[-3], "存货") is not None else inv_p
    turn = cogs / ((inv + inv_p) / 2)
    turn_p = cogs_p / ((inv_p + inv_pp) / 2)
    tc = (turn - turn_p) / turn_p * 100
    gm = (rev - cogs) / rev * 100
    gm_p = (rev_p - cogs_p) / rev_p * 100
    gmc = gm - gm_p
    ev = f"存货周转{tc:+.1f}% 毛利率{gmc:+.1f}pp"
    if tc < -20 and gmc > 3:
        return "FAIL", ev + " ★combo+10"
    if tc < -10 and gmc > 0:
        return "WARN", ev
    return "PASS", ev

def r33_cip(y):
    """3.3 在建工程滞固: FAIL 连续3+年 | WARN 近窗口有滞固年"""
    bad = 0
    streak = 0
    detail = []
    for i in range(1, len(y)):
        cip, cip_p = g(y[i], "在建工程合计"), g(y[i-1], "在建工程合计")
        fa, fa_p = g(y[i], "固定资产净额", "固定资产净值", "固定资产原值"), \
                   g(y[i-1], "固定资产净额", "固定资产净值", "固定资产原值")
        if cip is None or cip_p is None or cip_p == 0:
            continue
        cip_g = (cip - cip_p) / abs(cip_p) * 100
        if cip_g > 30:
            fa_g = (fa - fa_p) / abs(fa_p) * 100 if (fa and fa_p not in (None, 0)) else None
            if fa_g is None or fa_g < cip_g * 0.5:
                bad += 1
                streak += 1
                detail.append(f"{y[i]['period'][:4]}:在建+{cip_g:.0f}%/固资+{fa_g:.0f}%"
                              if fa_g is not None else f"{y[i]['period'][:4]}:在建+{cip_g:.0f}%/固资缺失")
            else:
                streak = 0
        else:
            streak = 0
    if streak >= 3:
        return "FAIL", "连续3年+ " + "; ".join(detail[-3:])
    if bad >= 1:
        return "WARN", "; ".join(detail[-3:])
    return "PASS", "在建工程无滞固"

def r41_ocf(y):
    """4.1 OCF/净利<1: FAIL 连续3+年 | WARN 近5年2年<1"""
    neg, streak, max_streak = 0, 0, 0
    for r in y[-5:]:
        p = g(r, "归属于母公司所有者的净利润")
        o = g(r, "经营活动产生的现金流量净额")
        if p is None or p <= 0 or o is None:
            streak = 0
            continue
        if o / p < 1:
            neg += 1
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    if max_streak >= 3:
        return "FAIL", f"连续{max_streak}年OCF/净利<1"
    if neg >= 2:
        return "WARN", f"近5年{neg}年<1"
    return "PASS", f"达标({neg}年<1)"

def r42_cash_rev(y):
    """4.2 收现比: FAIL <0.8连续2+年 | WARN 最新<0.9 (含税1.13口径)
    注: 票据结算型业务(商票背书抵付采购)不进现金流, 收现比结构性偏低——
    FAIL 需结合年报票据/客户结构定性(实测有标的商票占应收近七成)"""
    vals = []
    for r in y[-5:]:
        rev = g(r, "营业收入")
        sc = g(r, "销售商品、提供劳务收到的现金")
        if rev and sc is not None:
            vals.append((r["period"][:4], sc / (rev * 1.13)))
    if not vals:
        return "SKIP", "销售收现/营收缺失"
    lt_streak, max_streak = 0, 0
    for _, v in vals:
        if v < 0.8:
            lt_streak += 1
            max_streak = max(max_streak, lt_streak)
        else:
            lt_streak = 0
    latest = vals[-1][1]
    ev = "近" + str(len(vals)) + "年 " + ",".join(f"{yy}:{v:.2f}" for yy, v in vals)
    if max_streak >= 2:
        return "FAIL", ev
    if latest < 0.9:
        return "WARN", ev
    return "PASS", ev

def r44_core(y):
    """4.4 核心利润背离: FAIL 连续2年>40% | WARN >20%
    注: 公式未扣研发费用, 高研发行业(研发/营收>5%)系统性假阳——evidence 提示人工降权"""
    divs = []
    rd_hint = ""
    for r in y[-2:]:
        rev = g(r, "营业收入")
        core = (rev or 0) - (g(r, "营业成本", default=0) or 0) - (g(r, "销售费用", default=0) or 0) \
               - (g(r, "管理费用", default=0) or 0) - (g(r, "财务费用", default=0) or 0)
        p = g(r, "归属于母公司所有者的净利润")
        if p in (None, 0) or rev is None:
            continue
        divs.append(abs(core - p) / abs(p) * 100)
        rd = g(r, "研发费用")
        if rev and rd is not None:
            rd_hint = f" [研发{rd/rev*100:.1f}%营收,高则人工降权]"
    if not divs:
        return "SKIP", "科目缺失"
    ev = "背离" + "/".join(f"{d:.0f}%" for d in divs) + rd_hint
    if len(divs) == 2 and all(d > 40 for d in divs):
        return "FAIL", ev
    if divs[-1] > 20:
        return "WARN", ev
    return "PASS", ev

def r45_fcf(y):
    """4.5 净利增+FCF负: FAIL 3+年 | WARN 2年"""
    cnt = 0
    for i in range(1, len(y)):
        r, rp = y[i], y[i-1]
        p, pp = g(r, "归属于母公司所有者的净利润"), g(rp, "归属于母公司所有者的净利润")
        ocf = g(r, "经营活动产生的现金流量净额")
        capex = g(r, "购建固定资产、无形资产和其他长期资产所支付的现金", default=0) or 0
        if p is None or p <= 0 or ocf is None or pp is None:
            continue
        fcf = ocf - capex
        if fcf < 0 and p > pp:
            cnt += 1
    if cnt >= 3:
        return "FAIL", f"{cnt}年净利增且FCF<0"
    if cnt == 2:
        return "WARN", f"{cnt}年"
    return "PASS", f"达标({cnt}年)"

def r46_minority(y):
    """4.6 少数股东权益/损益背离: FAIL eq>=20%且(pnl<5%或损益负)连2期 | WARN eq>=10%且pnl<0.3eq连2期"""
    recs = []
    for r in y[-3:]:
        eq = g(r, "少数股东权益")
        eqt = g(r, "所有者权益(或股东权益)合计")
        mp = g(r, "少数股东损益")
        np_ = g(r, "净利润")
        if eq is None or eqt in (None, 0):
            return "SKIP", "少数股东权益科目缺失"
        eq_r = eq / eqt * 100
        pnl_r = mp / np_ * 100 if (mp is not None and np_) else None
        recs.append((eq_r, pnl_r, mp))
    if recs[-1][0] < 5:
        return "PASS", f"少数权益占比{recs[-1][0]:.1f}%<5%"
    f_fail = all(e >= 20 and (p is None or p < 5 or m is None or m < 0) for e, p, m in recs[-2:])
    w = all(e >= 10 and (p is None or p < e * 0.3) for e, p, m in recs[-2:])
    ev = "/".join(f"eq{e:.0f}%pnl{p:.0f}%" if p is not None else f"eq{e:.0f}%pnl?" for e, p, m in recs[-2:])
    if f_fail:
        return "FAIL", ev
    if w:
        return "WARN", ev
    return "PASS", ev

THREE_TABLE_RULES = [
    ("1.6", "减值暴增", r16_impair), ("2.3", "存贷双高", r23_cash_debt),
    ("3.1", "应收超收入", r31_ar), ("3.2", "存货周转↓毛利率↑", r32_inventory),
    ("3.3", "在建工程滞固", r33_cip), ("4.1", "OCF/净利<1", r41_ocf),
    ("4.2", "收现比", r42_cash_rev), ("4.4", "核心利润背离", r44_core),
    ("4.5", "净利增FCF负", r45_fcf), ("4.6", "少数股东背离", r46_minority),
]

# ─────────────────────────── 接口规则 ───────────────────────────

def cninfo_post(orgid, code, **kw):
    """巨潮公告查询。注: column 参数实测被忽略(沪股照常返回), 保留 szse 仅为兼容"""
    data = {"pageNum": 1, "pageSize": 30, "column": "szse", "tabName": "fulltext",
            "stock": f"{code},{orgid}", "secid": "", "sortName": "", "sortType": "",
            "isHLtitle": True}
    data.update(kw)
    r = requests.post("http://www.cninfo.com.cn/new/hisAnnouncement/query",
                      data=data, headers={**UA, "Referer": "http://www.cninfo.com.cn/"}, timeout=20)
    r.raise_for_status()
    time.sleep(1.2)
    return r.json()

def get_orgid(code):
    r = requests.post("http://www.cninfo.com.cn/new/information/topSearch/query",
                      data={"keyWord": code, "maxNum": 5},
                      headers={**UA, "Referer": "http://www.cninfo.com.cn/"}, timeout=15)
    for x in r.json():
        if x.get("code") == code:
            return x.get("orgId")
    time.sleep(1.0)
    return None

def r02_annual_disclosure(orgid, code):
    """0.2 按时披露: 最新年报公告 ≤ 次年4月30。
    标题正则须兼容沪市变体"公司名2025年度报告"(无"年"字), 排除 摘要/半年度/问询;
    同报告期多条(更正后/更新版)取最早公告日"""
    try:
        d = cninfo_post(orgid, code, category="category_ndbg_szsh",
                        seDate="2024-01-01~2026-12-31")
    except Exception as e:
        return "SKIP", f"巨潮失败:{type(e).__name__}"
    anns = d.get("announcements") or []
    seen = {}
    for a in anns:
        t = re.sub("<.*?>", "", a.get("announcementTitle", ""))
        m = re.search(r"(\d{4})年?年度报告", t)
        if m and "摘要" not in t and "半年度" not in t and "问询" not in t:
            yr = int(m.group(1))
            ts = datetime.datetime.fromtimestamp(a["announcementTime"] / 1000)
            if yr not in seen or ts < seen[yr]:
                seen[yr] = ts
    if not seen:
        return "SKIP", "未取到年报公告(新股或巨潮无数据)"
    yr = max(seen)
    due = datetime.datetime(yr + 1, 4, 30)
    ann = seen[yr]
    ev = f"{yr}年报 公告{ann:%Y-%m-%d} (截止{due:%Y-%m-%d})"
    return ("PASS" if ann <= due else "FAIL"), ev

MONITORS = ["立案", "警示函", "行政处罚"]

def r59_513_history(orgid, code):
    """5.9/5.13 监管前科(快筛启发式): 巨潮标题检索无法区分对象/性质/语境,
    预筛命中一律标 WATCH/WARN 需人工按 checklist 5.9 结构化句式复核"""
    out = {kw: [] for kw in MONITORS}
    for kw in MONITORS:
        try:
            d = cninfo_post(orgid, code, searchkey=kw, seDate="2021-01-01~2026-12-31")
        except Exception:
            continue
        for a in (d.get("announcements") or []):
            t = re.sub("<.*?>", "", a.get("announcementTitle", ""))
            ts = datetime.datetime.fromtimestamp(a["announcementTime"] / 1000)
            out[kw].append((t, ts))
    n_li = len(out["立案"])
    n_punish = len(out["行政处罚"])
    n_warn = len(out["警示函"])
    latest = []
    for kw in MONITORS:
        for t, ts in sorted(out[kw], key=lambda x: -x[1].timestamp())[:2]:
            latest.append(f"[{kw}]{ts:%Y-%m-%d} {t[:38]}")
    if n_li > 0:
        v = "WARN"
        ev = f"立案{n_li}条 警示函{n_warn} 处罚{n_punish} | " + " ; ".join(latest[:3])
    elif n_punish + n_warn >= 2:
        v = "WARN"
        ev = f"监管记录{len(latest)}条 | " + " ; ".join(latest[:3])
    elif n_punish + n_warn == 1:
        v = "WATCH"
        ev = " ; ".join(latest[:2])
    else:
        v = "PASS"
        ev = "近5年无立案/警示函/行政处罚记录"
    ev += " ⚠标题检索无法区分对象/性质,需人工按5.9结构化句式复核"
    return v, ev

AUDIT_PAT = re.compile(r"(标准无保留意见|带强调事项段的无保留意见|带持续经营重大不确定性段落的无保留意见|带解释说明段的无保留意见|保留意见|无法表示意见|否定意见)")

def r01_audit(code):
    """0.1 审计意见: 同花顺 F10 财务页"年报审计意见"表(秒级, 免读年报PDF)。
    新股/页面无该区块 → SKIP; 此处结果与年报PDF"审计意见类型"字段互为印证"""
    try:
        r = requests.get(f"http://basic.10jqka.com.cn/{code}/finance.html",
                         headers=THS_UA, timeout=20)
        r.encoding = "gbk"
        html = r.text
    except Exception as e:
        return "SKIP", f"THS失败:{type(e).__name__}"
    time.sleep(1.5)
    found = []
    for m in re.finditer(r"<td>(20\d\d)</td>.*?(?:</tr>|$)", html, re.S):
        row = m.group(0)
        ops = AUDIT_PAT.findall(row)
        if ops:
            found.append((m.group(1), ops[-1]))
    if not found:
        return "SKIP", "页面未解析到审计意见(新股或改版)"
    found.sort()
    yr, op = found[-1]
    ev = f"{yr}年报: {op} (来源:同花顺F10)"
    if op == "标准无保留意见":
        return "PASS", ev
    if op.startswith("带") and "无保留" in op:
        return "WARN", ev + " 非标-带强调事项"
    return "FAIL", ev

def r511_pledge(code):
    """5.11 控股股东质押: FAIL >50% | WARN 10-50%。
    东财 RPTA_APP_ACCUMDETAILS: IS_CONTROL_SHAREHOLDER=1 直接标记控股股东。
    质押率反推: 总股本=PF_NUM/PF_TSR*100; 质押率≈ACCUM_PLEDGE_TSR/(HOLD_NUM/总股本)。
    ⚠ 取 NOTICE_DATE 最新一条, 若公告久远(如>3年)需注明数据时点"""
    try:
        r = requests.get("https://datacenter-web.eastmoney.com/api/data/v1/get",
                         params={"sortColumns": "NOTICE_DATE", "sortTypes": "-1",
                                 "pageSize": 50, "pageNumber": 1,
                                 "reportName": "RPTA_APP_ACCUMDETAILS", "columns": "ALL",
                                 "source": "WEB", "client": "WEB",
                                 "filter": f'(SECURITY_CODE="{code}")'},
                         headers=UA, timeout=15)
        rows = (r.json().get("result") or {}).get("data") or []
    except Exception as e:
        return "SKIP", f"东财失败:{type(e).__name__}"
    time.sleep(1.2)
    ctrl = [x for x in rows if x.get("IS_CONTROL_SHAREHOLDER") == "1"]
    if not ctrl:
        if rows:
            return "PASS", f"近2年无控股股东质押记录(共{len(rows)}条质押)"
        return "PASS", "无质押记录"
    latest = ctrl[0]
    holder = latest.get("HOLDER_NAME", "?")
    notice = str(latest.get("NOTICE_DATE", ""))[:10]
    hold_num = (latest.get("HOLD_NUM") or 0) * 1e4  # 万股→股
    pf_num = latest.get("PF_NUM")
    pf_tsr = latest.get("PF_TSR")
    accum = latest.get("ACCUM_PLEDGE_TSR")
    ratio = None
    if pf_num and pf_tsr:
        total_shares = pf_num / (pf_tsr / 100)
        if total_shares > 0 and hold_num > 0:
            h_share = hold_num / total_shares * 100
            if accum is not None and h_share > 0:
                ratio = accum / h_share * 100
    if ratio is None and pf_num and hold_num:
        pledged = sum((c.get("PF_NUM") or 0) for c in ctrl[:20])
        ratio = pledged / hold_num * 100
    if ratio is None:
        return "SKIP", f"控股股东{holder}有质押记录但比例无法推算"
    ev = f"控股股东{holder[:14]} 质押/持股≈{ratio:.1f}% (公告{notice})"
    try:
        nd = datetime.datetime.strptime(notice, "%Y-%m-%d")
        if (datetime.datetime.now() - nd).days > 1095:
            ev += " ⚠数据时点久远,需核最新"
    except ValueError:
        pass
    if ratio > 50:
        return "FAIL", ev
    if ratio >= 10:
        return "WARN", ev
    return "PASS", ev

# ─────────────────────────── 主流程 ───────────────────────────

def screen_one(code):
    rec = {"code": code, "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "three_table": {}, "interface": {}, "errors": []}
    try:
        rec["name"] = stock_name(code)
    except Exception:
        rec["name"] = None
    time.sleep(0.5)
    try:
        y = fetch_yearly(code)
        rec["years"] = [r["period"][:4] for r in y]
        if len(y) >= 2:
            for rid, rname, fn in THREE_TABLE_RULES:
                try:
                    v, ev = fn(y)
                except Exception as e:
                    v, ev = "SKIP", f"{type(e).__name__}:{e}"
                rec["three_table"][rid] = {"name": rname, "verdict": v, "evidence": ev}
        else:
            rec["errors"].append(f"年报期不足({len(y)})")
    except Exception as e:
        rec["errors"].append(f"三表:{type(e).__name__}:{e}")
    orgid = get_orgid(code)
    if orgid:
        for rid, fn in (("0.2", lambda: r02_annual_disclosure(orgid, code)),
                        ("5.9/5.13", lambda: r59_513_history(orgid, code))):
            try:
                v, ev = fn()
            except Exception as e:
                v, ev = "SKIP", f"{type(e).__name__}:{e}"
            rec["interface"][rid] = {"verdict": v, "evidence": ev}
    else:
        rec["interface"]["0.2"] = {"verdict": "SKIP", "evidence": "orgId获取失败"}
        rec["interface"]["5.9/5.13"] = {"verdict": "SKIP", "evidence": "orgId获取失败"}
    for rid, fn in (("0.1", lambda: r01_audit(code)), ("5.11", lambda: r511_pledge(code))):
        try:
            v, ev = fn()
        except Exception as e:
            v, ev = "SKIP", f"{type(e).__name__}:{e}"
        rec["interface"][rid] = {"verdict": v, "evidence": ev}
    return rec

def main(argv):
    codes, out = [], _OUT_DEFAULT
    it = iter(argv)
    for a in it:
        if a == "--out":
            out = next(it, out)
        elif re.fullmatch(r"(\d{6}|(sh|sz|bj)?\d{6})", a.lower()):
            codes.append(re.sub(r"^(sh|sz|bj)", "", a.lower()))
    if not codes:
        print(__doc__)
        print("用法: python3 quick_screen.py <code1> [code2 ...] [--out FILE]")
        sys.exit(1)
    done = set()
    if os.path.exists(out):
        with open(out) as f:
            for line in f:
                try:
                    done.add(json.loads(line)["code"])
                except Exception:
                    pass
    for code in codes:
        if code in done:
            print(f"[skip] {code} 已完成", flush=True)
            continue
        rec = screen_one(code)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        tt = " ".join(f"{k}:{v['verdict'][0]}" for k, v in sorted(rec["three_table"].items()))
        itf = " ".join(f"{k}:{v['verdict'][0]}" for k, v in sorted(rec["interface"].items()))
        print(f"[done] {code} {rec.get('name')} | {tt} | {itf} | err={rec['errors'] or '-'}", flush=True)
    print(f"ALL DONE → {out}")

if __name__ == "__main__":
    main(sys.argv[1:])
