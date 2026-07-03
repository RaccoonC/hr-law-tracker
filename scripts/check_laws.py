#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_laws.py

用途：
    讀取 config/laws.json 中列出的法規（以「全國法規資料庫」的 PCode 標示），
    逐一抓取該法規的「修正日期/公布日期」，並與上次執行時記錄下來的日期比對。
    如果日期不同，代表該法規有異動，會在 data/laws_status.json 中標記 updated = true。
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
import html as html_module

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "laws.json")
DATA_PATH = os.path.join(BASE_DIR, "data", "laws_status.json")

LAW_URL_TEMPLATE = "https://law.moj.gov.tw/LawClass/LawAll.aspx?media=print&pcode={pcode}"
LAW_VIEW_URL_TEMPLATE = "https://law.moj.gov.tw/LawClass/LawAll.aspx?PCode={pcode}"

USER_AGENT = (
    "HR-Law-Tracker/1.1 (+personal use, HR compliance tracking; "
    "low-frequency daily check; contact: repo owner)"
)

# 💡【優化1】修正正則表達式：涵蓋「修正日期」、「公布日期」、「發布日期」與「廢止日期」
DATE_PATTERN = re.compile(
    r"(?:修正日期|公布日期|發布日期|廢止日期)\s*[:：]?\s*民國\s*(\d{1,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
)

REQUEST_TIMEOUT = 15
SLEEP_BETWEEN_REQUESTS = 3  # 秒，避免高頻率打官方網站
RECENT_WINDOW_DAYS = 14  # 標記為「有更新」的天數，之後自動退回一般顯示
MAX_RETRIES = 3 # 💡【優化2】設定連線失敗時的最大重試次數


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def strip_tags(raw_html):
    """把 HTML 標籤去掉，只留文字內容，並把多個空白/換行合併成一個空白。"""
    text = re.sub(r"<script[^>]*>.*?</script>", " ", raw_html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_module.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_amend_date(pcode):
    """回傳 (民國年月日字串或None, 錯誤訊息或None, debug資訊dict)"""
    url = LAW_URL_TEMPLATE.format(pcode=pcode)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    debug = {"url": url}
    html = ""
    
    # 💡【優化2】加入自動重試機制，對抗政府網站的偶發性連線不穩定
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                debug["status"] = resp.status
                debug["final_url"] = resp.geturl()
                raw = resp.read()
                debug["byte_length"] = len(raw)
                
                try:
                    html = raw.decode("utf-8")
                    debug["encoding_used"] = "utf-8"
                except UnicodeDecodeError:
                    html = raw.decode("big5", errors="ignore")
                    debug["encoding_used"] = "big5"
                
                break # 成功取得 html 就跳出重試迴圈
                
        except urllib.error.HTTPError as e:
            if e.code == 404: # 404 找不到網頁，重試也沒用，直接中斷
                debug["status"] = e.code
                return None, f"HTTP {e.code} 網頁不存在", debug
            if attempt == MAX_RETRIES - 1:
                return None, f"HTTP {e.code} (已重試 {MAX_RETRIES} 次)", debug
            time.sleep(SLEEP_BETWEEN_REQUESTS)
            
        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                return None, f"連線錯誤：{e} (已重試 {MAX_RETRIES} 次)", debug
            time.sleep(SLEEP_BETWEEN_REQUESTS)

    if not html:
        return None, "無法取得網頁內容", debug

    text = strip_tags(html)
    m = DATE_PATTERN.search(text)
    
    if not m:
        debug["text_snippet_start"] = text[:500]
        debug["text_snippet_around_title"] = None
        title_idx = text.find("法規名稱")
        if title_idx != -1:
            around = text[max(0, title_idx - 50): title_idx + 300]
            debug["text_snippet_around_title"] = around
        return None, "頁面中找不到修正日期或公布日期欄位（需要人工確認）", debug

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
            entry["debug"] = debug
            results.append(entry)
            continue

        entry["last_amend_date"] = date_str

        if prev.get("last_amend_date") and prev["last_amend_date"] != date_str:
            entry["updated"] = True
            entry["updated_detected_at"] = now_iso
        elif prev.get("updated_detected_at"):
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
