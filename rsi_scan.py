import os
import time
import requests
import asyncio

BINANCE_FUTURES_BASE = "https://fapi.binance.com"

# Đọc từ GitHub Secrets (không lưu trực tiếp)
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

RSI_PERIOD = 14
RSI_OVERSOLD = 25
RSI_OVERBOUGHT = 75
REQUEST_DELAY = 0.25
FRESH_WINDOW_MINUTES = 25

def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        r = requests.post(url, data=payload, timeout=10)
        if r.status_code != 200:
            print(f"[Telegram] Lỗi: {r.status_code} - {r.text}")
    except Exception as e:
        print(f"[Telegram] Exception: {e}")

def is_excluded_symbol(symbol: str) -> bool:
    """Không đụng (vẫn giữ để tương lai mở rộng)"""
    return False  # Dễ dàng bật lại nếu cần

def get_all_futures_usdt_symbols():
    url = f"{BINANCE_FUTURES_BASE}/fapi/v1/exchangeInfo"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        symbols = []
        for s in data["symbols"]:
            if (
                s["quoteAsset"] == "USDT"
                and s["status"] == "TRADING"
                and s["contractType"] == "PERPETUAL"
                and not is_excluded_symbol(s["symbol"])
            ):
                symbols.append(s["symbol"])
        # Giới hạn để tránh rate limit (chạy được ~30-40 coin)
        return sorted(symbols[:40])   # <--- thêm dòng này
    except Exception as e:
        print(f"[Futures] Không lấy được danh sách Futures: {e}")
        return []

def get_klines(base_url: str, path: str, symbol: str, interval: str, limit: int = 100):
    url = f"{base_url}{path}"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    return r.json()

# ================== RSI 14 (Wilder) - CHỈ RSI ==================
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

# ================== RSI 14 REAL-TIME ==================
async def scan_rsi_realtime():
    print("🔄 Bắt đầu quét RSI 14 realtime Futures USDT...")
    futures_symbols = get_all_futures_usdt_symbols()
    if not futures_symbols:
        print("[Futures] Không lấy được danh sách Futures, bỏ qua realtime.")
        return

    print(f"[Futures] Tìm thấy {len(futures_symbols)} cặp USDT Perpetual (chạy 40 coin)")

    alerts = []
    for i, symbol in enumerate(futures_symbols):
        try:
            klines = get_klines(BINANCE_FUTURES_BASE, "/fapi/v1/klines",
                               symbol, "5m", limit=50)
            closes = [float(k[4]) for k in klines]
            rsi_value = calculate_rsi(closes, RSI_PERIOD)

            if rsi_value is not None:
                if rsi_value <= RSI_OVERSOLD:
                    signal = "🟡 RSI 14 LOW"
                elif rsi_value >= RSI_OVERBOUGHT:
                    signal = "🔴 RSI 14 HIGH"
                else:
                    signal = "🟢 NEUTRAL"

                link = f"https://fapi.binance.com/en/futures/{symbol.replace('USDT', '')}?positionSide=LONG"
                alerts.append(f"**{symbol}** | RSI: `{rsi_value:.1f}` | {signal}\n{link}")

            # Thêm sleep để tránh rate limit
            await asyncio.sleep(0.3)

        except Exception as e:
            if "429" in str(e) or "rate limit" in str(e).lower():
                print(f"[RATE LIMIT] Binance limit reached cho {symbol}. Dừng quét để tránh block.")
                break
            print(f"Lỗi {symbol}: {e}")

    if alerts:
        message = "📊 **RSI 14 REAL-TIME FUTURES**\n\n" + "\n\n".join(alerts)
        send_telegram_message(message)
        print(f"✅ Đã gửi {len(alerts)} tín hiệu realtime RSI 14")
