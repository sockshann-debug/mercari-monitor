"""
Mercari 商品監測機器人 - Telegram 通知版
使用方式：
  1. 設定 .env 或環境變數 TELEGRAM_TOKEN 和 TELEGRAM_CHAT_ID
  2. 在 config.py 或下方 KEYWORDS 設定監測關鍵字
  3. python mercari_monitor.py
"""

import os
import time
import json
import logging
import requests
from datetime import datetime

# ─── 設定區 ──────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "A")    # Bot Token
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")  # 你的 Chat ID

# 監測關鍵字清單，每個項目可設定 min_price / max_price（0 = 不限）
KEYWORDS = [
    {"keyword": "TWICE ミナ", "min_price": 0, "max_price": 5000},
    # {"keyword": "iPhone 15",       "min_price": 0, "max_price": 0},
]

CHECK_INTERVAL = 60   # 每幾秒檢查一次（建議 60 秒以上）
MAX_ITEMS      = 30   # 每次最多抓幾筆

# ─── 日誌設定 ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── Mercari API ──────────────────────────────────────────────────────────────

MERCARI_API = "https://api.mercari.jp/v2/entities:search"
HEADERS = {
    "Content-Type": "application/json",
    "X-Platform": "web",
    "User-Agent": "Mozilla/5.0 (compatible; MercariMonitor/1.0)",
}

def build_payload(keyword: str, min_price: int = 0, max_price: int = 0) -> dict:
    return {
        "userId": "",
        "pageSize": MAX_ITEMS,
        "pageToken": "",
        "searchSessionId": f"monitor_{int(time.time())}",
        "indexRouting": "INDEX_ROUTING_UNSPECIFIED",
        "thumbnailTypes": [],
        "searchCondition": {
            "keyword": keyword,
            "excludeKeyword": "",
            "sort": "SORT_CREATED_TIME",
            "order": "ORDER_DESC",
            "status": ["STATUS_ON_SALE"],
            "categoryId": [],
            "brandId": [],
            "sellerId": [],
            "priceMin": min_price,
            "priceMax": max_price,
            "itemConditionId": [],
            "shippingPayerId": [],
            "shippingFromArea": [],
            "shippingMethod": [],
            "colorId": [],
            "hasCoupon": False,
            "attributes": [],
            "itemTypes": [],
            "skuIds": [],
        },
        "defaultDatasets": ["DATASET_TYPE_MERCARI", "DATASET_TYPE_BEYOND"],
        "serviceFrom": "suruga",
        "withItemBrand": True,
        "withItemSize": False,
        "withItemPromotions": False,
        "withItemSizes": False,
        "trackingId": "",
    }

def fetch_items(keyword: str, min_price: int = 0, max_price: int = 0) -> list[dict]:
    try:
        resp = requests.post(
            MERCARI_API,
            headers=HEADERS,
            json=build_payload(keyword, min_price, max_price),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        return [
            {
                "id":    item.get("id", ""),
                "name":  item.get("name", "(無標題)"),
                "price": item.get("price", 0),
                "image": (item.get("thumbnails") or [""])[0],
                "url":   f"https://jp.mercari.com/item/{item.get('id', '')}",
            }
            for item in items
        ]
    except Exception as e:
        log.error(f"抓取失敗（{keyword}）：{e}")
        return []

# ─── Telegram ─────────────────────────────────────────────────────────────────

def send_telegram(text: str, photo_url: str = "") -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("尚未設定 TELEGRAM_TOKEN 或 TELEGRAM_CHAT_ID，跳過通知")
        return False
    try:
        if photo_url:
            api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "photo": photo_url,
                "caption": text,
                "parse_mode": "HTML",
            }
        else:
            api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            }
        r = requests.post(api, json=payload, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Telegram 發送失敗：{e}")
        return False

def notify_new_items(keyword: str, new_items: list[dict]) -> None:
    log.info(f"🔔 [{keyword}] 發現 {len(new_items)} 件新商品，發送通知")
    for item in new_items[:5]:   # 每批最多通知 5 件，避免洗版
        price_str = f"¥{item['price']:,}"
        text = (
            f"🛍 <b>Mercari 新商品</b>\n"
            f"🔍 關鍵字：<code>{keyword}</code>\n\n"
            f"📦 {item['name']}\n"
            f"💴 <b>{price_str}</b>\n"
            f"🔗 <a href=\"{item['url']}\">點此查看商品</a>"
        )
        send_telegram(text, photo_url=item.get("image", ""))
        time.sleep(0.5)   # 避免打太快

    if len(new_items) > 5:
        send_telegram(
            f"⚡ <b>[{keyword}]</b> 還有 {len(new_items) - 5} 件新商品，請至 Mercari 查看！"
        )

# ─── 主迴圈 ───────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=== Mercari Monitor 啟動 ===")
    if not TELEGRAM_TOKEN:
        log.warning("⚠ 未設定 TELEGRAM_TOKEN，將不發送通知（僅印出日誌）")

    # 記錄已見過的商品 ID
    seen: dict[str, set] = {kw["keyword"]: set() for kw in KEYWORDS}

    # 第一輪：先暖身，把現有商品記起來，不發通知
    log.info("初始化：讀取現有商品（不發通知）…")
    for cfg in KEYWORDS:
        kw = cfg["keyword"]
        items = fetch_items(kw, cfg.get("min_price", 0), cfg.get("max_price", 0))
        seen[kw] = {item["id"] for item in items}
        log.info(f"  [{kw}] 已記錄 {len(seen[kw])} 件現有商品")
        time.sleep(2)

    log.info(f"開始監測，每 {CHECK_INTERVAL} 秒查詢一次")
    send_telegram("✅ <b>Mercari Monitor 已啟動</b>\n正在監測：\n" +
                  "\n".join(f"• {kw['keyword']}" for kw in KEYWORDS))

    while True:
        time.sleep(CHECK_INTERVAL)
        for cfg in KEYWORDS:
            kw       = cfg["keyword"]
            min_p    = cfg.get("min_price", 0)
            max_p    = cfg.get("max_price", 0)
            log.info(f"查詢中：{kw}")
            items    = fetch_items(kw, min_p, max_p)
            new_items = [i for i in items if i["id"] not in seen[kw]]
            if new_items:
                notify_new_items(kw, new_items)
                seen[kw].update(i["id"] for i in new_items)
            else:
                log.info(f"  [{kw}] 無新商品")
            time.sleep(2)   # 各關鍵字間隔 2 秒

if __name__ == "__main__":
    main()
