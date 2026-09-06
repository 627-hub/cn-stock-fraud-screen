#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
巨潮全历史检索（5.13 造假前科/重述）
对每只股票用各关键词检索公告标题/全文, 判断是否存在:
  A. 行政处罚涉及虚假记载/虚增/资金占用
  B. 会计差错更正/追溯重述
  C. 仅警示函/监管关注
输出 output/{code}/cninfo_history.json
数据源: cninfo hisAnnouncement/query (按stock过滤, 用searchkey)
"""
import sys, os, json, time, re
import requests

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
OUT_BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")

# 5.13 检索关键词组
KEYWORDS = {
    "A_虚假记载/虚增/占用": ["虚假记载", "虚增", "资金占用", "财务造假", "虚构利润", "虚增收入", "信息披露违法违规"],
    "B_差错更正/重述": ["会计差错更正", "前期差错", "追溯调整", "追溯重述", "更正公告", "会计政策变更"],
    "C_监管关注/警示": ["警示函", "监管关注", "监管函", "问询函", "纪律处分", "公开谴责", "通报批评"],
    "D_立案/处罚": ["立案", "行政处罚", "处罚决定书", "立案调查", "涉嫌信息披露"],
}

_ORGID_MAP = None
def cninfo_orgid(code):
    global _ORGID_MAP
    if _ORGID_MAP is None:
        try:
            r = requests.get("http://www.cninfo.com.cn/new/data/szse_stock.json",
                             headers=UA, timeout=20)
            _ORGID_MAP = {s["code"]: s["orgId"] for s in r.json().get("stockList", [])}
        except Exception:
            _ORGID_MAP = {}
    return _ORGID_MAP.get(code) or f"gss{'h' if code.startswith('6') else 'z'}0{code}"

def cninfo_search(code, searchkey, page_size=25):
    org = cninfo_orgid(code)
    url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    payload = {"stock": f"{code},{org}", "tabName": "fulltext", "pageSize": str(page_size),
               "pageNum": "1", "column": "", "category": "", "plate": "",
               "seDate": "", "searchkey": searchkey, "secid": "", "sortName": "",
               "sortType": "", "isHLtitle": "true"}
    headers = {**UA, "Content-Type": "application/x-www-form-urlencoded",
               "Referer": "https://www.cninfo.com.cn/new/disclosure",
               "Origin": "https://www.cninfo.com.cn"}
    try:
        r = requests.post(url, data=payload, headers=headers, timeout=20)
        d = r.json()
        rows = []
        for it in d.get("announcements", []) or []:
            from datetime import datetime
            ts = it.get("announcementTime")
            date = datetime.fromtimestamp(ts/1000).strftime("%Y-%m-%d") if isinstance(ts,(int,float)) else str(ts)[:10]
            rows.append({"title": it.get("announcementTitle",""), "date": date,
                         "type": it.get("announcementTypeName","")})
        return rows
    except Exception as e:
        return [{"error": str(e)}]

def main():
    codes = sys.argv[1:]
    if not codes:
        print("用法: python3 cninfo_history.py <code> [code2...]")
        sys.exit(1)
    for code in codes:
        os.makedirs(os.path.join(OUT_BASE, code), exist_ok=True)
        result = {}
        print(f"=== {code} ===")
        for group, kws in KEYWORDS.items():
            group_hits = []
            for kw in kws:
                rows = cninfo_search(code, kw, 25)
                time.sleep(0.4)
                # 过滤掉含error的
                real = [r for r in rows if "error" not in r]
                if real:
                    # 去掉标题里明显不含关键词干扰的? 保留全部, 交由评估
                    group_hits.append({"kw": kw, "hits": real})
            result[group] = group_hits
            print(f"  {group}: {len(group_hits)} 组关键词命中")
        with open(os.path.join(OUT_BASE, code, "cninfo_history.json"), "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
        time.sleep(0.5)

if __name__ == "__main__":
    main()
