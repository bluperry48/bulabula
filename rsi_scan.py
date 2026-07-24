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
RSI_OVERSOLD = 25
RSI_OVERBOUGHT = 75

INTERVAL_4H = "4h"
RSI_OVERSOLD_4H = 30
RSI_OVERBOUGHT_4H = 70

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
        rsi_1h = None
        rsi_4h = None
        triggered = False

        # --- Kiểm tra khung 1h ---
        try:
            klines = get_klines(base_url, klines_path, symbol, INTERVAL, limit=RSI_PERIOD * 3)
            closes = [float(k[4]) for k in klines]
            rsi_1h = calculate_rsi(closes, RSI_PERIOD)

            if rsi_1h is not None and (rsi_1h <= RSI_OVERSOLD or rsi_1h >= RSI_OVERBOUGHT):
                triggered = True
                print(f"  -> [{market_label}] {symbol} RSI({INTERVAL})={rsi_1h}")

        except Exception as e:
            print(f"Lỗi khi xử lý [{market_label}] {symbol} khung {INTERVAL}: {e}")

        time.sleep(REQUEST_DELAY)

        # --- Kiểm tra khung 4h ---
        try:
            klines_4h = get_klines(base_url, klines_path, symbol, INTERVAL_4H, limit=RSI_PERIOD * 3)
            closes_4h = [float(k[4]) for k in klines_4h]
            rsi_4h = calculate_rsi(closes_4h, RSI_PERIOD)

            if rsi_4h is not None and (rsi_4h <= RSI_OVERSOLD_4H or rsi_4h >= RSI_OVERBOUGHT_4H):
                triggered = True
                print(f"  -> [{market_label}] {symbol} RSI({INTERVAL_4H})={rsi_4h}")

        except Exception as e:
            print(f"Lỗi khi xử lý [{market_label}] {symbol} khung {INTERVAL_4H}: {e}")

        time.sleep(REQUEST_DELAY)

        # --- Gửi GỘP thành 1 dòng gọn nếu ít nhất 1 khung có tín hiệu ---
        if triggered and rsi_1h is not None and rsi_4h is not None:
            msg = (
                f"<b>{symbol}</b> [{market_label}] - "
                f"RSI(1H): {rsi_1h}; RSI(4H): {rsi_4h}"
            )
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
