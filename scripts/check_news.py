#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_news.py

用途：
    讀取 config/keywords.json 中的關鍵字，透過 Google 新聞 RSS 搜尋相關新聞，
    藉此補足「法規正式公告前」的草案動態、修法趨勢等資訊（全國法規資料庫
    只會顯示「已經正式生效」的條文，抓不到草案階段的新聞）。

    這一段抓到的是「新聞」，不是「法律效力」本身，請務必只當作提醒、
    自己再去查證原始新聞或官方公告，不要直接當作法規已經修正的依據。

輸出：
    data/news_feed.json
"""

import json
import os
import time
import urllib.request
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config", "keywords.json")
DATA_PATH = os.path.join(BASE_DIR, "data", "news_feed.json")

RSS_URL_TEMPLATE = (
    "https://news.google.com/rss/search?q={query}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
)

USER_AGENT = "HR-Law-Tracker/1.0 (+personal use, HR compliance news tracking)"
REQUEST_TIMEOUT = 15
SLEEP_BETWEEN_REQUESTS = 2
ITEMS_PER_KEYWORD = 5
RECENT_WINDOW_DAYS = 14
MAX_TOTAL_ITEMS = 150


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fetch_rss(keyword):
    query = urllib.parse.quote(keyword)
    url = RSS_URL_TEMPLATE.format(query=query)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read()
    except Exception as e:  # noqa: BLE001
        return None, f"抓取失敗：{e}"

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        return None, f"RSS 解析失敗：{e}"

    items = []
    for item in root.findall(".//item")[:ITEMS_PER_KEYWORD]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        source_el = item.find("source")
        source = source_el.text.strip() if source_el is not None and source_el.text else ""
        items.append(
            {
                "title": title,
                "link": link,
                "pub_date": pub_date,
                "source": source,
            }
        )
    return items, None


def main():
    keywords = load_json(CONFIG_PATH, [])
    previous = load_json(DATA_PATH, {"items": []})
    previous_by_link = {item["link"]: item for item in previous.get("items", []) if item.get("link")}

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    cutoff = now - timedelta(days=RECENT_WINDOW_DAYS)

    all_items = []
    errors = []

    for keyword in keywords:
        items, error = fetch_rss(keyword)
        time.sleep(SLEEP_BETWEEN_REQUESTS)

        if error:
            errors.append({"keyword": keyword, "error": error})
            continue

        for item in items:
            link = item["link"]
            prev = previous_by_link.get(link)
            first_seen_at = prev["first_seen_at"] if prev else now_iso
            entry = {
                "keyword": keyword,
                "title": item["title"],
                "link": link,
                "source": item["source"],
                "pub_date": item["pub_date"],
                "first_seen_at": first_seen_at,
            }
            all_items.append(entry)

    # 依連結去重（同一則新聞可能被多個關鍵字命中）
    dedup = {}
    for item in all_items:
        if item["link"] not in dedup:
            dedup[item["link"]] = item
        else:
            # 合併命中的關鍵字
            existing = dedup[item["link"]]
            if item["keyword"] not in existing["keyword"]:
                existing["keyword"] = existing["keyword"] + "、" + item["keyword"]

    merged = list(dedup.values())

    # 標記是否為近期新出現的項目
    for item in merged:
        try:
            first_seen = datetime.fromisoformat(item["first_seen_at"])
        except ValueError:
            first_seen = now
        item["is_new"] = first_seen >= cutoff

    # 新的在前面：優先看 first_seen_at，其次看標題字母排序（pubDate 格式不一致不易直接排序）
    merged.sort(key=lambda x: x["first_seen_at"], reverse=True)
    merged = merged[:MAX_TOTAL_ITEMS]

    output = {
        "generated_at": now_iso,
        "recent_window_days": RECENT_WINDOW_DAYS,
        "items": merged,
        "errors": errors,
    }
    save_json(DATA_PATH, output)

    new_count = sum(1 for i in merged if i["is_new"])
    print(f"完成。共 {len(merged)} 則新聞，其中 {new_count} 則為近 {RECENT_WINDOW_DAYS} 天內新出現，{len(errors)} 個關鍵字查詢失敗。")


if __name__ == "__main__":
    main()
