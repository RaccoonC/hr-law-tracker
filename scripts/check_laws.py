#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_laws.py

用途：
    讀取 config/laws.json 中列出的法規（以「全國法規資料庫」的 PCode 標示），
    逐一抓取該法規的「修正日期」與「條文全文」，並與上次執行時記錄下來的
    資料比對。

    1. 修正日期／公布日期：與上次比對，若不同代表該法規有異動，
       會在 data/laws_status.json 中標記 updated = true，並額外標記
       newly_detected = true（僅在「這次執行才第一次偵測到」的情況下為 true，
       用來給 Email 通知判斷要不要寄信，避免同一次異動連續 14 天每天都寄信）。

    2. 條文全文：只有在「這是第一次抓到這部法規」或「修正/公布日期跟上次不一樣」
       時，才會重新擷取全文並覆蓋 data/laws_fulltext.json 裡對應的項目；
       如果日期沒有變化，直接沿用舊資料，不重新擷取，降低對官網的存取頻率，
       也降低擷取邏輯萬一失敗時把舊的好資料洗掉的風險。

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
    3. 條文全文的擷取邏輯（extract_fulltext）是用「第 X 條」的第一次出現位置
       當作條文本體的開始，並在常見的頁尾字樣（如「回頁首」）出現處截斷。
       這是根據頁面一般結構寫的通用邏輯，第一次正式跑之後，建議你抽查幾筆
       data/laws_fulltext.json 的內容，確認擷取範圍正確（沒有把導覽列雜訊
       也存進去、也沒有把條文尾端切斷）。如果發現擷取範圍不對，這段函式
       是最需要調整的地方，做法跟你之前調整 AMEND_PATTERN 正規表達式時一樣。
    4. 法規本身在中華民國著作權法第 9 條規定不受著作權保護，所以重製全文
       本身沒有著作權疑慮；但條文仍有時效性，請務必透過網頁上顯示的
       「條文更新時間」與官方連結，提醒使用者以官方網站最新版本為準。

輸出：
    data/laws_status.json
    data/laws_fulltext.json
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
FULLTEXT_PATH = os.path.join(BASE_DIR, "data", "laws_fulltext.json")

LAW_URL_TEMPLATE = "https://law.moj.gov.tw/LawClass/LawAll.aspx?media=print&pcode={pcode}"
LAW_VIEW_URL_TEMPLATE = "https://law.moj.gov.tw/LawClass/LawAll.aspx?PCode={pcode}"

USER_AGENT = (
    "HR-Law-Tracker/1.0 (+personal use, HR compliance tracking; "
    "low-frequency daily check; contact: repo owner)"
)

# 修正日期／公發布日 分開比對，因為不是每部法規都有修正過
AMEND_PATTERN = re.compile(
    r"修正日期\s*[:：]?\s*民國\s*(\d{1,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
)
PUBLISH_PATTERN = re.compile(
    r"公\s*[（(]?\s*發?\s*[）)]?\s*布日期?\s*[:：]?\s*民國\s*(\d{1,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
)
ABOLISH_PATTERN = re.compile(
    r"廢止日期\s*[:：]?\s*民國\s*(\d{1,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
)

# 條文本體擷取用：找「第 X 條」的第一次出現位置當作本體開始
ARTICLE_START_PATTERN = re.compile(r"第\s*[一二三四五六七八九十百千0-9]+\s*條")
# 條文本體常見的頁尾雜訊字樣，出現位置之後的內容一律捨棄
FULLTEXT_FOOTER_MARKERS = [
    "回頁首", "友善列印", "工具箱", "資料來源：全國法規資料庫",
    "◇ 資料來源", "列印時間", "本資料庫為法務部",
]
FULLTEXT_MIN_LENGTH = 30  # 擷取結果太短，視為擷取失敗，不儲存

REQUEST_TIMEOUT = 15
SLEEP_BETWEEN_REQUESTS = 3  # 秒，避免高頻率打官方網站
RECENT_WINDOW_DAYS = 14  # 標記為「有更新」的天數，之後自動退回一般顯示


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        print(f"警告：{path} 內容無法解析（{e}），改用預設空值繼續執行。"
              f"這個檔案接下來會被本次執行的結果覆蓋掉。")
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


import html as html_module


def strip_tags(raw_html):
    """把 HTML 標籤去掉，只留文字內容，並把多個空白/換行合併成一個空白。
    這樣「修正日期：</th> <td> 民國 113 年 ... </td>」這種被標籤隔開的內容，
    去除標籤後會變成「修正日期： 民國 113 年 ...」，才比對得到。
    """
    text = re.sub(r"<script[^>]*>.*?</script>", " ", raw_html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_module.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _format_date(match):
    roc_year, month, day = match.groups()
    return f"民國{roc_year}年{int(month):02d}月{int(day):02d}日"


def extract_fulltext(stripped_text):
    """從已去除 HTML 標籤的整頁文字中，嘗試截取「條文本體」部分。
    抓不到起始位置，或擷取結果過短（研判擷取失敗），回傳 None，
    呼叫端遇到 None 時應該沿用舊資料，而不是拿空字串覆蓋掉舊資料。
    """
    m = ARTICLE_START_PATTERN.search(stripped_text)
    if not m:
        return None
    body = stripped_text[m.start():]
    cut_positions = [body.find(marker) for marker in FULLTEXT_FOOTER_MARKERS]
    cut_positions = [p for p in cut_positions if p > 0]
    if cut_positions:
        body = body[:min(cut_positions)]
    body = body.strip()
    if len(body) < FULLTEXT_MIN_LENGTH:
        return None
    return body


def fetch_law_page(pcode):
    """回傳 (dates_dict或None, fulltext字串或None, 錯誤訊息或None, debug資訊dict)
    dates_dict 格式: {"amend_date": str或None, "publish_date": str或None, "abolish_date": str或None}
    """
    url = LAW_URL_TEMPLATE.format(pcode=pcode)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    debug = {"url": url}
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
    except urllib.error.HTTPError as e:
        debug["status"] = e.code
        return None, None, f"HTTP {e.code}", debug
    except urllib.error.URLError as e:
        return None, None, f"連線失敗：{e.reason}", debug
    except Exception as e:  # noqa: BLE001
        return None, None, f"未知錯誤：{e}", debug

    text = strip_tags(html)

    amend_m = AMEND_PATTERN.search(text)
    publish_m = PUBLISH_PATTERN.search(text)
    abolish_m = ABOLISH_PATTERN.search(text)

    dates = {
        "amend_date": _format_date(amend_m) if amend_m else None,
        "publish_date": _format_date(publish_m) if publish_m else None,
        "abolish_date": _format_date(abolish_m) if abolish_m else None,
    }

    if not dates["amend_date"] and not dates["publish_date"]:
        # 兩個都找不到，才視為真正的抓取失敗
        debug["text_snippet_start"] = text[:500]
        debug["text_snippet_around_title"] = None
        title_idx = text.find("法規名稱")
        if title_idx != -1:
            around = text[max(0, title_idx - 50): title_idx + 300]
            debug["text_snippet_around_title"] = around
        return None, None, "頁面中找不到修正日期或公發布日欄位（頁面格式可能已變更，需要人工確認）", debug

    fulltext = extract_fulltext(text)
    return dates, fulltext, None, debug


def main():
    laws = load_json(CONFIG_PATH, [])
    previous = load_json(DATA_PATH, {"laws": [], "generated_at": None})
    previous_by_name = {item["name"]: item for item in previous.get("laws", [])}

    previous_fulltext = load_json(FULLTEXT_PATH, {"laws": []})
    previous_fulltext_by_name = {item["name"]: item for item in previous_fulltext.get("laws", [])}

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    results = []
    fulltext_results = {}  # name -> entry，最後轉成 list 存檔
    fulltext_refetched = 0
    fulltext_carried_over = 0
    fulltext_failed = 0

    for law in laws:
        name = law.get("name", "")
        pcode = (law.get("pcode") or "").strip()
        category = law.get("category", "未分類")
        prev = previous_by_name.get(name, {})
        prev_ft = previous_fulltext_by_name.get(name)

        entry = {
            "name": name,
            "category": category,
            "pcode": pcode,
            "view_url": LAW_VIEW_URL_TEMPLATE.format(pcode=pcode) if pcode else None,
            "last_amend_date": prev.get("last_amend_date"),
            "publish_date": prev.get("publish_date"),
            "updated": False,
            "newly_detected": False,
            "updated_detected_at": prev.get("updated_detected_at"),
            "fetch_error": None,
            "checked_at": now_iso,
        }

        if not pcode:
            entry["fetch_error"] = "尚未設定 pcode，請至 law.moj.gov.tw 搜尋此法規並補上"
            results.append(entry)
            if prev_ft:
                fulltext_results[name] = prev_ft
                fulltext_carried_over += 1
            continue

        dates, fulltext, error, debug = fetch_law_page(pcode)
        time.sleep(SLEEP_BETWEEN_REQUESTS)

        if error:
            entry["fetch_error"] = error
            entry["debug"] = debug  # 除錯用，排查穩定後可以移除這欄
            # 抓取失敗時保留上次的資料，不覆蓋掉
            results.append(entry)
            if prev_ft:
                fulltext_results[name] = prev_ft
                fulltext_carried_over += 1
            continue

        entry["last_amend_date"] = dates["amend_date"]
        entry["publish_date"] = dates["publish_date"]

        # 比對用的「有效日期」：優先看修正日期，沒有修正過的話用公發布日
        prev_effective = prev.get("last_amend_date") or prev.get("publish_date")
        curr_effective = dates["amend_date"] or dates["publish_date"]

        if prev_effective and curr_effective and prev_effective != curr_effective:
            # 偵測到日期改變（包含「第一次被修正」這種從無修正日變成有修正日的情況）
            entry["updated"] = True
            entry["updated_detected_at"] = now_iso
            entry["newly_detected"] = True
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

        # ---- 全文處理 ----
        dates_changed_or_missing = (
            prev_ft is None
            or prev_ft.get("last_amend_date") != entry["last_amend_date"]
            or prev_ft.get("publish_date") != entry["publish_date"]
        )

        if dates_changed_or_missing and fulltext:
            fulltext_results[name] = {
                "name": name,
                "category": category,
                "pcode": pcode,
                "last_amend_date": entry["last_amend_date"],
                "publish_date": entry["publish_date"],
                "content": fulltext,
                "fulltext_updated_at": now_iso,
                "view_url": entry["view_url"],
            }
            fulltext_refetched += 1
        elif dates_changed_or_missing and not fulltext:
            # 日期有變但這次沒擷取到全文（可能頁面結構跟預期不同），
            # 沿用舊資料比放空好，並且印出警告方便排查
            if prev_ft:
                fulltext_results[name] = prev_ft
                fulltext_carried_over += 1
            fulltext_failed += 1
            print(f"警告：{name}（{pcode}）日期有變動，但這次未能擷取到條文全文，"
                  f"沿用舊資料（若無舊資料則暫缺），請檢查 extract_fulltext() 的擷取邏輯是否需要調整。")
        else:
            # 日期沒變，沿用舊資料，不重新擷取
            if prev_ft:
                fulltext_results[name] = prev_ft
                fulltext_carried_over += 1

    output = {
        "generated_at": now_iso,
        "recent_window_days": RECENT_WINDOW_DAYS,
        "laws": results,
    }
    save_json(DATA_PATH, output)

    fulltext_output = {
        "generated_at": now_iso,
        "laws": list(fulltext_results.values()),
    }
    save_json(FULLTEXT_PATH, fulltext_output)

    updated_count = sum(1 for r in results if r["updated"])
    newly_count = sum(1 for r in results if r["newly_detected"])
    error_count = sum(1 for r in results if r["fetch_error"])
    print(f"完成。共 {len(results)} 筆法規，{updated_count} 筆標記為近期更新"
          f"（其中 {newly_count} 筆是這次才新偵測到），{error_count} 筆抓取失敗或未設定 pcode。")
    print(f"全文：{fulltext_refetched} 筆重新擷取，{fulltext_carried_over} 筆沿用舊資料，"
          f"{fulltext_failed} 筆本次應更新但擷取失敗。")


if __name__ == "__main__":
    main()
