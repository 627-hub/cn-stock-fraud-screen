#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下载年报 PDF 并 pdftotext 转换。用法: python3 fetch_annual_pdf.py <code> [searchkey]"""
import sys, os, json, time, subprocess
import requests

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
OUT_BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")

_ORGID_MAP = None
def cninfo_orgid(code):
    global _ORGID_MAP
    if _ORGID_MAP is None:
        r = requests.get("http://www.cninfo.com.cn/new/data/szse_stock.json",
                         headers=UA, timeout=20)
        _ORGID_MAP = {s["code"]: s["orgId"] for s in r.json().get("stockList", [])}
    return _ORGID_MAP.get(code) or f"gss{'h' if code.startswith('6') else 'z'}0{code}"

def cninfo_search(code, searchkey, page_size=40):
    org = cninfo_orgid(code)
    url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
    payload = {"stock": f"{code},{org}", "tabName": "fulltext", "pageSize": str(page_size),
               "pageNum": "1", "column": "", "category": "category_ndbg_szsh", "plate": "",
               "seDate": "2015-01-01~2026-09-01", "searchkey": searchkey, "secid": "",
               "sortName": "", "sortType": "", "isHLtitle": "true"}
    headers = {**UA, "Content-Type": "application/x-www-form-urlencoded",
               "Referer": "https://www.cninfo.com.cn/new/disclosure",
               "Origin": "https://www.cninfo.com.cn"}
    r = requests.post(url, data=payload, headers=headers, timeout=20)
    d = r.json()
    rows = []
    for it in d.get("announcements", []) or []:
        from datetime import datetime
        ts = it.get("announcementTime")
        date = datetime.fromtimestamp(ts/1000).strftime("%Y-%m-%d") if isinstance(ts,(int,float)) else str(ts)[:10]
        rows.append({"title": it.get("announcementTitle",""), "date": date,
                     "adjunctUrl": it.get("adjunctUrl","")})
    return rows

def download_pdf(code, adjunct, out_dir):
    url = f"https://static.cninfo.com.cn/{adjunct}"
    try:
        r = requests.get(url, headers=UA, timeout=60)
        if r.status_code == 200 and len(r.content) > 1000:
            fname = os.path.join(out_dir, f"annual_{code}.pdf")
            with open(fname, "wb") as f:
                f.write(r.content)
            return fname
    except Exception as e:
        print(f"  下载失败: {e}")
    return None

def main():
    code = sys.argv[1]
    key = sys.argv[2] if len(sys.argv) > 2 else "年度报告"
    out_dir = os.path.join(OUT_BASE, code)
    os.makedirs(out_dir, exist_ok=True)
    anns = cninfo_search(code, key)
    print(f"{code} search='{key}' → {len(anns)} 条")
    # 优先找最近一年的完整年报(非摘要/非半年度)，且年份用最新的
    pdfs = []
    for a in anns:
        t = a["title"]
        if key in t and "摘要" in t:
            continue
        if "半年度" in t or "季度" in t:
            continue
        if a["adjunctUrl"].lower().endswith(".pdf"):
            pdfs.append(a)
    print(f"  年报候选: {len(pdfs)} 条")
    # 只取不含"摘要"的全文(且优先最新年份), 只下载1份, 避免摘要覆盖正文
    full = [a for a in pdfs if "摘要" not in a["title"]]
    target = full[:1] if full else pdfs[:1]
    for a in target:
        fname = download_pdf(code, a["adjunctUrl"], out_dir)
        if fname:
            txt = fname.replace(".pdf", ".txt")
            subprocess.run(["pdftotext", "-layout", fname, txt], check=False)
            print(f"  下载+转换: {fname} → {txt} ({os.path.getsize(txt)} bytes, 标题: {a['title'][:40]})")
        time.sleep(1)

if __name__ == "__main__":
    main()
