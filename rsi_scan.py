"""
RSI Alert Bot cho Binance -> Telegram (bản chạy trên GitHub Actions)
--------------------------------------------------------------------
Quét TẤT CẢ 1 LẦN rồi thoát (GitHub Actions sẽ tự chạy lại theo lịch).
Token/Chat ID được đọc từ biến môi trường (GitHub Secrets), KHÔNG lưu
trực tiếp trong code để tránh lộ thông tin khi đẩy code lên GitHub công khai.
"""

import os
import time
import requests

BINANCE_BASE = "https://data-api.binance.vision"

# Đọc từ GitHub Secrets (biến môi trường)
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

INTERVAL = "1h"
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 80
RSI_PERIOD = 14
QUOTE_ASSET = "USDT"
REQUEST_DELAY = 0.25


def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        r = requests.post(url, data=payload, timeout=10)
        if r.status_code != 200:
            print(f"[Telegram] Lỗi gửi tin nhắn: {r.status_code} - {r.text}")
    except Exception as e:
        print(f"[Telegram] Exception: {e}")


def get_all_usdt_symbols():
    url = f"{BINANCE_BASE}/api/v3/exchangeInfo"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    data = r.json()
    symbols = []
    for s in data["symbols"]:
        if (
            s["quoteAsset"] == QUOTE_ASSET
            and s["status"] == "TRADING"
            and s["isSpotTradingAllowed"]
        ):
            symbols.append(s["symbol"])
    return sorted(symbols)


def get_klines(symbol: str, interval: str, limit: int = 100):
    url = f"{BINANCE_BASE}/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def calculate_rsi(closes, period: int = 14):
    if len(closes) < period + 1:
        return None
    deltas = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def main():
    print("Bắt đầu quét (1 lần)...")
    symbols = get_all_usdt_symbols()
    print(f"Tổng số cặp USDT: {len(symbols)}")

    alerts_sent = 0
    for symbol in symbols:
        try:
            klines = get_klines(symbol, INTERVAL, limit=RSI_PERIOD * 3)
            closes = [float(k[4]) for k in klines]
            rsi = calculate_rsi(closes, RSI_PERIOD)
            if rsi is None:
                continue

            if rsi <= RSI_OVERSOLD:
                msg = (
                    f"🟢 <b>{symbol}</b> - RSI(14) khung {INTERVAL}: <b>{rsi}</b>\n"
                    f"Vùng QUÁ BÁN (oversold)"
                )
                send_telegram_message(msg)
                alerts_sent += 1
                print(f"  -> {symbol} RSI={rsi} (oversold)")
            elif rsi >= RSI_OVERBOUGHT:
                msg = (
                    f"🔴 <b>{symbol}</b> - RSI(14) khung {INTERVAL}: <b>{rsi}</b>\n"
                    f"Vùng QUÁ MUA (overbought)"
                )
                send_telegram_message(msg)
                alerts_sent += 1
                print(f"  -> {symbol} RSI={rsi} (overbought)")

        except Exception as e:
            print(f"Lỗi khi xử lý {symbol}: {e}")

        time.sleep(REQUEST_DELAY)

    print(f"Xong. Đã gửi {alerts_sent} cảnh báo.")


if __name__ == "__main__":
    main()
