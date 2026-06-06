"""
Mercari 日本 商品監測機器人 - Telegram 通知版
改用網頁抓取，避免 API 被擋
"""

import os
import time
import logging
import requests
from bs4 import BeautifulSoup

# ─── 設定區 ──────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

KEYWORDS = [
    {"keyword": "TWICE ミナ", "min_price": 0, "max_price": 5000},
    # {"keyword": "ニンテンドースイッチ", "min_price": 0, "max_price": 0},
]

CHECK_INTERVAL = 120  # 每幾秒查詢一次（建議 120 秒以上）

# ─── 日誌 ─────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── 抓取 Mercari ─────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
    "Accept-Language": "ja-JP,ja;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def build_url(keyword: str, min_price: int = 0, max_price: int = 0) -> str:
    import urllib.parse
    params = {
        "keyword": keyword,
        "status": "on_sale",
        "sort": "created_time",
        "order": "desc",
    }
    if min_price > 0:
        params["price_min"] = min_price
    if max_price > 0:
        params["price_max"] = max_price
    return "https://jp.mercari.com/search?" + urllib.parse.urlencode(params)

def fetch_items(keyword: str, min_price: int = 0, max_price: int = 0) -> list[dict]:
    url = build_url(keyword, min_price, max_price)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        items = []
        # Mercari 商品卡片
        for card in soup.select("li[data-testid='item-cell']")[:30]:
            try:
                a = card.find("a", href=True)
                item_id = a["href"].split("/")[-1] if a else ""
                name_el = card.select_one("[class*='itemName'], [class*='item-name'], p")
                price_el = card.select_one("[class*='price'], [class*='Price']")
                img_el = card.select_one("img")

                name = name_el.get_text(strip=True) if name_el else "(無標題)"
                price_text = price_el.get_text(strip=True) if price_el else "0"
                price = int("".join(filter(str.isdigit, price_text))) if price_text else 0
                image = img_el.get("src", "") if img_el else ""

                if item_id:
                    items.append({
                        "id": item_id,
                        "name": name,
                        "price": price,
                        "image": image,
                        "url": f"https://jp.mercari.com/item/{item_id}",
                    })
            except Exception:
                continue

        log.info(f"  [{keyword}] 抓到 {len(items)} 件商品")
        return items

    except Exception as e:
        log.error(f"抓取失敗（{keyword}）：{e}")
        return []

# ─── Telegram ─────────────────────────────────────────────────────────────────

def send_telegram(text: str, photo_url: str = "") -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("尚未設定 TELEGRAM_TOKEN 或 TELEGRAM_CHAT_ID")
        return False
    try:
        if photo_url and photo_url.startswith("http"):
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
            }
        r = requests.post(api, json=payload, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Telegram 發送失敗：{e}")
        return False

def notify_new_items(keyword: str, new_items: list[dict]) -> None:
    log.info(f"🔔 [{keyword}] 發現 {len(new_items)} 件新商品")
    for item in new_items[:5]:
        price_str = f"¥{item['price']:,}" if item['price'] else "價格不明"
        text = (
            f"🛍 <b>Mercari 新商品</b>\n"
            f"🔍 關鍵字：<code>{keyword}</code>\n\n"
            f"📦 {item['name']}\n"
            f"💴 <b>{price_str}</b>\n"
            f"🔗 <a href=\"{item['url']}\">點此查看商品</a>"
        )
        send_telegram(text, photo_url=item.get("image", ""))
        time.sleep(0.5)

    if len(new_items) > 5:
        send_telegram(f"⚡ <b>[{keyword}]</b> 還有 {len(new_items) - 5} 件新商品！")

# ─── 主迴圈 ───────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=== Mercari Monitor 啟動 ===")

    seen: dict[str, set] = {kw["keyword"]: set() for kw in KEYWORDS}

    log.info("初始化：讀取現有商品（不發通知）…")
    for cfg in KEYWORDS:
        kw = cfg["keyword"]
        items = fetch_items(kw, cfg.get("min_price", 0), cfg.get("max_price", 0))
        seen[kw] = {item["id"] for item in items}
        log.info(f"  [{kw}] 已記錄 {len(seen[kw])} 件現有商品")
        time.sleep(3)

    log.info(f"開始監測，每 {CHECK_INTERVAL} 秒查詢一次")
    send_telegram("✅ <b>Mercari Monitor 已啟動</b>\n正在監測：\n" +
                  "\n".join(f"• {kw['keyword']}" for kw in KEYWORDS))

    while True:
        time.sleep(CHECK_INTERVAL)
        for cfg in KEYWORDS:
            kw    = cfg["keyword"]
            items = fetch_items(kw, cfg.get("min_price", 0), cfg.get("max_price", 0))
            new_items = [i for i in items if i["id"] not in seen[kw]]
            if new_items:
                notify_new_items(kw, new_items)
                seen[kw].update(i["id"] for i in new_items)
            else:
                log.info(f"  [{kw}] 無新商品")
            time.sleep(3)

if __name__ == "__main__":
    main()
