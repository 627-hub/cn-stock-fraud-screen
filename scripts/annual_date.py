#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采集每只股票的 2024 年年度报告公告日期 (Rule 0.2 按时披露)。用法: python3 annual_date.py <code>..."""
import sys, os, json, time, re
import requests

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
OUT_BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")

_ORGID_MAP = None
def cninfo_orgid(code):
    global _ORGID_MAP
    if _ORGID_MAP is None:
        try:
            r = requests.get("http://www.cninfo.com.cn/new/data/szse_stock.json", headers=UA, timeout=20)
            _ORGID_MAP = {s["code"]: s["orgId"] for s in r.json().get("stockList", [])}
        except Exception:
            _ORGID_MAP = {}
    return _ORGID_MAP.get(code) or f"gss{'h' if code.startswith('6') else 'z'}0{code}"

def cninfo_search(code, searchkey, page_size=30):
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
            dt = datetime.fromtimestamp(ts/1000).strftime("%Y-%m-%d") if isinstance(ts,(int,float)) else str(ts)[:10]
            rows.append({"title": it.get("announcementTitle",""), "date": dt})
        return rows
    except Exception as e:
        return [{"error": str(e)}]

def main():
    codes = sys.argv[1:]
    if not codes:
        print("用法: python3 annual_date.py <code> [code2...]")
        sys.exit(1)
    out = {}
    for code in codes:
        # 检索 2024 年度报告(称 title 含"2024"且含"年度报告"，不含"摘要"/"半年度")
        rows = cninfo_search(code, "2024年年度报告")
        time.sleep(0.4)
        best = None
        for r in rows:
            t = r.get("title","")
            if "error" in r: continue
            if "2024" in t and "年度报告" in t and "摘要" not in t and "半年度" not in t:
                best = r; break  # 优先取全文年报
        if best is None:
            for r in rows:
                t = r.get("title","")
                if "error" in r: continue
                if "2024" in t and "年度报告" in t and "半年度" not in t:
                    best = r; break
        out[code] = {"title": best.get("title","") if best else "", "date": best.get("date","") if best else "", "n_results": len(rows)}
        print(f"{code} -> {out[code]['date']} | {out[code]['title'][:45]}")
        time.sleep(0.5)
    # 保存
    with open(os.path.join(OUT_BASE, "annual_dates.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\nsaved annual_dates.json")

if __name__ == "__main__":
    main()
