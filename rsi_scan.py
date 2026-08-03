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

BINANCE_SPOT_BASE = "https://data-api.binance.vision"
BINANCE_FUTURES_BASE = "https://fapi.binance.com"

# Đọc từ GitHub Secrets (biến môi trường)
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

INTERVAL = "1h"
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

INTERVAL_4H = "4h"
RSI_OVERSOLD_4H = 30
RSI_OVERBOUGHT_4H = 70

RSI_PERIOD = 14
QUOTE_ASSET = "USDT"
REQUEST_DELAY = 0.25

# Số nến tải về mỗi lần
HISTORY_LIMIT = 150


def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        r = requests.post(url, data=payload, timeout=10)
        if r.status_code != 200:
            print(f"[Telegram] Lỗi gửi tin nhắn: {r.status_code} - {r.text}")
    except Exception as e:
        print(f"[Telegram] Exception: {e}")


def get_all_spot_usdt_symbols():
    url = f"{BINANCE_SPOT_BASE}/api/v3/exchangeInfo"
    r = requests.get(url, timeout=15)
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


def get_all_futures_usdt_symbols():
    """Lấy danh sách cặp Futures USDT-M. Trả về [] nếu bị chặn/lỗi."""
    url = f"{BINANCE_FUTURES_BASE}/fapi/v1/exchangeInfo"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        symbols = []
        for s in data["symbols"]:
            if (
                s["quoteAsset"] == QUOTE_ASSET
                and s["status"] == "TRADING"
                and s["contractType"] == "PERPETUAL"
            ):
                symbols.append(s["symbol"])
        return sorted(symbols)
    except Exception as e:
        print(f"[Futures] Không lấy được danh sách Futures: {e}")
        return []


def get_klines(base_url: str, path: str, symbol: str, interval: str, limit: int = 100):
    url = f"{base_url}{path}"
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


def scan_market(market_label, base_url, klines_path, symbols):
    """Quét 1 danh sách symbol của 1 thị trường (Spot hoặc Futures), trả về số cảnh báo đã gửi."""
    alerts_sent = 0
    for symbol in symbols:
        triggered_rsi_1h = None  # None = không trigger, số = giá trị RSI đã trigger
        triggered_rsi_4h = None

        # --- Khung 1h: RSI ---
        try:
            klines = get_klines(base_url, klines_path, symbol, INTERVAL, limit=HISTORY_LIMIT)
            closes = [float(k[4]) for k in klines]

            rsi_1h = calculate_rsi(closes, RSI_PERIOD)
            if rsi_1h is not None and (rsi_1h <= RSI_OVERSOLD or rsi_1h >= RSI_OVERBOUGHT):
                triggered_rsi_1h = rsi_1h
                print(f"  -> [{market_label}] {symbol} RSI({INTERVAL})={rsi_1h}")

        except Exception as e:
            print(f"Lỗi khi xử lý [{market_label}] {symbol} khung {INTERVAL}: {e}")

        time.sleep(REQUEST_DELAY)

        # --- Khung 4h: RSI ---
        try:
            klines_4h = get_klines(base_url, klines_path, symbol, INTERVAL_4H, limit=HISTORY_LIMIT)
            closes_4h = [float(k[4]) for k in klines_4h]

            rsi_4h = calculate_rsi(closes_4h, RSI_PERIOD)
            if rsi_4h is not None and (rsi_4h <= RSI_OVERSOLD_4H or rsi_4h >= RSI_OVERBOUGHT_4H):
                triggered_rsi_4h = rsi_4h
                print(f"  -> [{market_label}] {symbol} RSI({INTERVAL_4H})={rsi_4h}")

        except Exception as e:
            print(f"Lỗi khi xử lý [{market_label}] {symbol} khung {INTERVAL_4H}: {e}")

        time.sleep(REQUEST_DELAY)

        # --- Chỉ hiện khung nào THỰC SỰ trigger, khung chưa đạt ngưỡng thì bỏ qua ---
        if triggered_rsi_1h is not None or triggered_rsi_4h is not None:
            parts = []
            emojis = []

            if triggered_rsi_1h is not None:
                parts.append(f"RSI(1H): {triggered_rsi_1h}")
                emojis.append("🟢" if triggered_rsi_1h <= RSI_OVERSOLD else "🔴")
            if triggered_rsi_4h is not None:
                parts.append(f"RSI(4H): {triggered_rsi_4h}")
                emojis.append("🟢" if triggered_rsi_4h <= RSI_OVERSOLD_4H else "🔴")

            # Bỏ emoji trùng nhau (vd cả 2 khung cùng quá bán thì chỉ hiện 1 dấu)
            unique_emojis = []
            for e in emojis:
                if e not in unique_emojis:
                    unique_emojis.append(e)

            msg = f"{''.join(unique_emojis)} <b>{symbol}</b> [{market_label}] - " + "; ".join(parts)
            send_telegram_message(msg)
            alerts_sent += 1

    return alerts_sent


def main():
    print("Bắt đầu quét (1 lần)...")
    total_alerts = 0

    # --- Futures (USDT-M) - quét trước để biết coin nào đã có ở đây ---
    futures_symbols = get_all_futures_usdt_symbols()
    futures_set = set(futures_symbols)

    if futures_symbols:
        print(f"[Futures] Tổng số cặp USDT-M: {len(futures_symbols)}")
        total_alerts += scan_market(
            "FUTURES", BINANCE_FUTURES_BASE, "/fapi/v1/klines", futures_symbols
        )
    else:
        print("[Futures] Bỏ qua (không lấy được dữ liệu, có thể bị chặn IP).")

    # --- Spot - bỏ qua các cặp đã có bên Futures (ưu tiên Futures, tránh trùng) ---
    spot_symbols_all = get_all_spot_usdt_symbols()
    spot_symbols = [s for s in spot_symbols_all if s not in futures_set]
    skipped = len(spot_symbols_all) - len(spot_symbols)
    print(
        f"[Spot] Tổng số cặp USDT: {len(spot_symbols_all)} "
        f"(bỏ qua {skipped} cặp trùng với Futures, còn quét {len(spot_symbols)})"
    )
    total_alerts += scan_market("SPOT", BINANCE_SPOT_BASE, "/api/v3/klines", spot_symbols)

    print(f"Xong. Đã gửi tổng cộng {total_alerts} cảnh báo.")


if __name__ == "__main__":
    main()
