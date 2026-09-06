#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cn-stock-fraud-screen 批量数据采集
对指定股票抓取: 新浪三表(年报期) + 巨潮公告列表(含年报PDF下载地址)
输出到 output/{code}/ 供后续规则评估
数据免费零鉴权。
"""
import sys, os, json, time
import requests

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

OUT_BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")

# ── 新浪三表 ──
def sina_financial_report(code, report_type, num=24):
    prefix = "sh" if code.startswith("6") else "sz"
    url = "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
    params = {"paperCode": f"{prefix}{code}", "source": report_type, "type": "0",
              "page": "1", "num": str(num)}
    try:
        r = requests.get(url, params=params, headers=UA, timeout=20)
        report_list = r.json().get("result", {}).get("data", {}).get("report_list", {}) or {}
        rows = []
        for period in sorted(report_list.keys(), reverse=True):
            rec = {"period": period}
            for it in report_list[period].get("data", []) or []:
                t, v = it.get("item_title"), it.get("item_value")
                if t and v is not None:
                    rec[t] = v
            rows.append(rec)
        return rows
    except Exception as e:
        return [{"error": str(e)}]

# ── 巨潮 orgId ──
_ORGID_MAP = None
def cninfo_orgid(code):
    global _ORGID_MAP
    if _ORGID_MAP is None:
        try:
            r = requests.get("http://www.cninfo.com.cn/new/data/szse_stock.json",
                             headers=UA, timeout=20)
            _ORGID_MAP = {s["code"]: s["orgId"] for s in r.json().get("stockList", [])}
        except Exception as e:
            print(f"[WARN] orgId 映射拉取失败: {e}")
            _ORGID_MAP = {}
    org = _ORGID_MAP.get(code)
    return org or f"gss{'h' if code.startswith('6') else 'z'}0{code}"

# ── 巨潮公告检索 ──
def cninfo_announcements(code, searchkey="", page_size=40):
    org = cninfo_orgid(code)
    url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    payload = {
        "stock": f"{code},{org}", "tabName": "fulltext", "pageSize": str(page_size),
        "pageNum": "1", "column": "", "category": "", "plate": "", "seDate": "",
        "searchkey": searchkey, "secid": "", "sortName": "", "sortType": "",
        "isHLtitle": "true",
    }
    headers = {**UA, "Content-Type": "application/x-www-form-urlencoded",
               "Referer": "https://www.cninfo.com.cn/new/disclosure",
               "Origin": "https://www.cninfo.com.cn"}
    r = requests.post(url, data=payload, headers=headers, timeout=20)
    d = r.json()
    rows = []
    for item in d.get("announcements", []) or []:
        from datetime import datetime
        ts = item.get("announcementTime")
        date = datetime.fromtimestamp(ts/1000).strftime("%Y-%m-%d") if isinstance(ts,(int,float)) else str(ts)[:10]
        rows.append({
            "title": item.get("announcementTitle", ""),
            "type": item.get("announcementTypeName", ""),
            "date": date,
            "adjunctUrl": item.get("adjunctUrl", ""),
            "announcementId": item.get("announcementId", ""),
        })
    return rows

def main():
    codes = sys.argv[1:]
    if not codes:
        print("用法: python3 collect_data.py <code> [code2...]")
        sys.exit(1)
    os.makedirs(OUT_BASE, exist_ok=True)
    for code in codes:
        os.makedirs(os.path.join(OUT_BASE, code), exist_ok=True)
        print(f"=== {code} ===")
        # 三表
        tables = {}
        for rtype, tname in [("lrb","lrb"),("fzb","fzb"),("llb","llb")]:
            rows = sina_financial_report(code, rtype, 24)
            tables[tname] = rows
            print(f"  {tname}: {len(rows)} 期")
            time.sleep(0.5)
        with open(os.path.join(OUT_BASE, code, "sina_tables.json"), "w", encoding="utf-8") as f:
            json.dump(tables, f, ensure_ascii=False, indent=1)
        # 巨潮公告(年度报告)
        anns = cninfo_announcements(code)
        print(f"  公告: {len(anns)} 条")
        with open(os.path.join(OUT_BASE, code, "cninfo_anns.json"), "w", encoding="utf-8") as f:
            json.dump(anns, f, ensure_ascii=False, indent=1)
        time.sleep(0.8)

if __name__ == "__main__":
    main()
