#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_laws.py

用途：
    讀取 config/laws.json 中列出的法規（以「全國法規資料庫」的 PCode 標示），
    逐一抓取該法規的「修正日期」，並與上次執行時記錄下來的日期比對。
    如果日期不同，代表該法規有異動，會在 data/laws_status.json 中標記 updated = true。

注意事項（請務必先讀）：
    1. 全國法規資料庫的內容「每週五」才會整批更新一次，所以就算每天執行，
       實際偵測到異動的時間點也大概是每週五之後才會出現。
    2. 全國法規資料庫的一般網頁對自動化存取有 robots 限制，本腳本改用其
       「列印精簡版」頁面（?media=print），並：
         - 加上識別用的 User-Agent
         - 每次請求之間 sleep，降低對主機的負擔
         - 一次只抓取設定檔中列出的法規，不做大量爬取
       如果日後這個方式失效（例如頁面改版、被擋），請改用官方 Open API
       （https://law.moj.gov.tw/api/）下載整批 JSON 再自行過濾，
       或考慮改為「手動比對＋網頁提醒你該去查了」的半自動模式。
    3. 本腳本僅擷取「修正日期」這個中繼資料欄位做比對，不會、也不應該
       用來重製法規全文（避免法律時效性與著作權疑慮），完整條文請一律
       連回官方網站查看。

輸出：
    data/laws_status.json
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "laws.json")
DATA_PATH = os.path.join(BASE_DIR, "data", "laws_status.json")

LAW_URL_TEMPLATE = "https://law.moj.gov.tw/LawClass/LawAll.aspx?media=print&pcode={pcode}"
LAW_VIEW_URL_TEMPLATE = "https://law.moj.gov.tw/LawClass/LawAll.aspx?PCode={pcode}"

USER_AGENT = (
    "HR-Law-Tracker/1.0 (+personal use, HR compliance tracking; "
    "low-frequency daily check; contact: repo owner)"
)

# 修正日期／公發布日 通常會以「民國 000 年 00 月 00 日」的格式出現
DATE_PATTERN = re.compile(
    r"(?:修正日期|公發布日|廢止日期)\s*[:：]?\s*民國\s*(\d{1,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
)

REQUEST_TIMEOUT = 15
SLEEP_BETWEEN_REQUESTS = 3  # 秒，避免高頻率打官方網站
RECENT_WINDOW_DAYS = 14  # 標記為「有更新」的天數，之後自動退回一般顯示


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fetch_amend_date(pcode):
    """回傳 (民國年月日字串或None, 錯誤訊息或None, debug資訊dict)"""
    url = LAW_URL_TEMPLATE.format(pcode=pcode)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    debug = {"url": url}
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            debug["status"] = resp.status
            debug["final_url"] = resp.geturl()
            raw = resp.read()
            debug["byte_length"] = len(raw)
            # 頁面通常是 utf-8 或 big5，先試 utf-8 失敗再試 big5
            try:
                html = raw.decode("utf-8")
                debug["encoding_used"] = "utf-8"
            except UnicodeDecodeError:
                html = raw.decode("big5", errors="ignore")
                debug["encoding_used"] = "big5"
    except urllib.error.HTTPError as e:
        debug["status"] = e.code
        return None, f"HTTP {e.code}", debug
    except urllib.error.URLError as e:
        return None, f"連線失敗：{e.reason}", debug
    except Exception as e:  # noqa: BLE001
        return None, f"未知錯誤：{e}", debug

    m = DATE_PATTERN.search(html)
    if not m:
        # 找不到預期格式時，把抓到的內容前後各存一小段，方便排查
        # （例如判斷是不是被導向錯誤頁、驗證頁，或格式跟預期不同）
        snippet = re.sub(r"\s+", " ", html).strip()
        debug["html_snippet_start"] = snippet[:500]
        debug["html_snippet_around_title"] = None
        title_idx = html.find("法規名稱")
        if title_idx != -1:
            around = re.sub(r"\s+", " ", html[max(0, title_idx - 50): title_idx + 300]).strip()
            debug["html_snippet_around_title"] = around
        return None, "頁面中找不到修正日期欄位（頁面格式可能已變更，需要人工確認）", debug

    roc_year, month, day = m.groups()
    date_str = f"民國{roc_year}年{int(month):02d}月{int(day):02d}日"
    return date_str, None, debug


def main():
    laws = load_json(CONFIG_PATH, [])
    previous = load_json(DATA_PATH, {"laws": [], "generated_at": None})
    previous_by_name = {item["name"]: item for item in previous.get("laws", [])}

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    results = []

    for law in laws:
        name = law.get("name", "")
        pcode = (law.get("pcode") or "").strip()
        category = law.get("category", "未分類")
        prev = previous_by_name.get(name, {})

        entry = {
            "name": name,
            "category": category,
            "pcode": pcode,
            "view_url": LAW_VIEW_URL_TEMPLATE.format(pcode=pcode) if pcode else None,
            "last_amend_date": prev.get("last_amend_date"),
            "updated": False,
            "updated_detected_at": prev.get("updated_detected_at"),
            "fetch_error": None,
            "checked_at": now_iso,
        }

        if not pcode:
            entry["fetch_error"] = "尚未設定 pcode，請至 law.moj.gov.tw 搜尋此法規並補上"
            results.append(entry)
            continue

        date_str, error, debug = fetch_amend_date(pcode)
        time.sleep(SLEEP_BETWEEN_REQUESTS)

        if error:
            entry["fetch_error"] = error
            entry["debug"] = debug  # 除錯用，排查穩定後可以移除這欄
            # 抓取失敗時保留上次的資料，不覆蓋掉
            results.append(entry)
            continue

        entry["last_amend_date"] = date_str

        if prev.get("last_amend_date") and prev["last_amend_date"] != date_str:
            # 偵測到修正日期改變 -> 標記為有更新
            entry["updated"] = True
            entry["updated_detected_at"] = now_iso
        elif prev.get("updated_detected_at"):
            # 檢查是否還在「近期更新」的顯示視窗內
            try:
                detected = datetime.fromisoformat(prev["updated_detected_at"])
                if (now - detected).days <= RECENT_WINDOW_DAYS:
                    entry["updated"] = True
                    entry["updated_detected_at"] = prev["updated_detected_at"]
            except ValueError:
                pass

        results.append(entry)

    output = {
        "generated_at": now_iso,
        "recent_window_days": RECENT_WINDOW_DAYS,
        "laws": results,
    }
    save_json(DATA_PATH, output)

    updated_count = sum(1 for r in results if r["updated"])
    error_count = sum(1 for r in results if r["fetch_error"])
    print(f"完成。共 {len(results)} 筆法規，{updated_count} 筆標記為近期更新，{error_count} 筆抓取失敗或未設定 pcode。")


if __name__ == "__main__":
    main()
