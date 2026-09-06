#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补采 2025 年报公告日期 (Rule 0.2 用最新年报期)。用法: python3 annual_date_2025.py <code>..."""
import sys, os, json, time
import requests

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output", "annual_dates_2025.json")

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

def cninfo_search(code, searchkey, page_size=40):
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
    out = {}
    for code in codes:
        rows = cninfo_search(code, "2025年年度报告")
        time.sleep(0.4)
        best = None
        for r in rows:
            t = r.get("title","")
            if "error" in r: continue
            if "2025" in t and "年度报告" in t and "摘要" not in t and "半年度" not in t and "问询" not in t:
                best = r; break
        if best is None:
            for r in rows:
                t = r.get("title","")
                if "error" in r: continue
                if "2025" in t and "年度报告" in t and "半年度" not in t and "问询" not in t:
                    best = r; break
        out[code] = {"title": best.get("title","") if best else "", "date": best.get("date","") if best else "", "n": len(rows)}
        print(f"{code} -> {out[code]['date']} | {out[code]['title'][:45]}")
        time.sleep(0.5)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("saved", OUT)

if __name__ == "__main__":
    main()
