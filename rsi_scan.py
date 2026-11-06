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

INTERVAL_30M = "30m"

RSI_PERIOD = 14
QUOTE_ASSET = "USDT"
REQUEST_DELAY = 0.25

# Số nến tải về mỗi lần (đủ để RSI tính ổn định)
HISTORY_LIMIT = 200

# --- Luật lọc: LOẠI mọi cặp có chữ B ngay trước USDT (bStocks như NVDABUSDT, SOXLBUSDT, STXBUSDT...) ---
# Vì luật này cũng trúng cả coin thật có tên kết thúc bằng B, những coin đó cần giữ lại
# thì ghi vào KEEP_SYMBOLS (hiện giữ BNBUSDT).
EXCLUDE_B_SUFFIX = "B" + QUOTE_ASSET
KEEP_SYMBOLS = {"BNBUSDT"}

# --- Cắt qua mốc 50 của RSI (khung 30m và 1h, tính trên nến ĐÃ ĐÓNG) ---
RSI_MIDLINE = 50
# Chỉ báo nếu nến vừa đóng cách hiện tại không quá số phút này (tránh báo lặp ở các lượt quét sau).
# Nên nhỏ hơn 30 (độ dài nến 30m) và lớn hơn thời gian 1 lượt quét.
FRESH_WINDOW_MINUTES = 25


def is_excluded_symbol(symbol: str) -> bool:
    return symbol.endswith(EXCLUDE_B_SUFFIX) and symbol not in KEEP_SYMBOLS


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
    """
    Lấy danh sách cặp Spot USDT đang giao dịch, TỰ CẬP NHẬT mỗi lần chạy:
    cặp mới list sẽ được quét, cặp bị hủy niêm yết (không còn TRADING) tự biến mất.
    Đồng thời loại các cặp có chữ B trước USDT.
    """
    url = f"{BINANCE_SPOT_BASE}/api/v3/exchangeInfo"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()

    all_symbols = [
        s["symbol"] for s in data["symbols"]
        if s["quoteAsset"] == QUOTE_ASSET
        and s["status"] == "TRADING"
        and s.get("isSpotTradingAllowed", True)
    ]
    excluded = sorted(s for s in all_symbols if is_excluded_symbol(s))
    symbols = [s for s in all_symbols if not is_excluded_symbol(s)]

    print(f"[Lọc] Loại {len(excluded)} cặp có chữ B trước {QUOTE_ASSET}: {excluded}")
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
                and not is_excluded_symbol(s["symbol"])
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


def calculate_rsi_series(closes, period: int = 14):
    """RSI (Wilder) cho từng nến, giá trị cuối = RSI nến mới nhất, kế cuối = RSI nến ngay trước đó."""
    if len(closes) < period + 1:
        return []
    deltas = [closes[i + 1] - closes[i] for i in range(len(closes) - 1)]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def to_rsi(ag, al):
        return 100.0 if al == 0 else 100 - (100 / (1 + ag / al))

    series = [to_rsi(avg_gain, avg_loss)]
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        series.append(to_rsi(avg_gain, avg_loss))
    return series


def detect_rsi_midline_cross(closes, period: int = 14, level: float = RSI_MIDLINE):
    """
    So RSI của 2 nến đã đóng gần nhất. Trả về ("up", rsi_trước, rsi_sau) nếu vừa vượt LÊN trên mức,
    ("down", rsi_trước, rsi_sau) nếu vừa tụt XUỐNG dưới mức, None nếu không có cắt.
    """
    series = calculate_rsi_series(closes, period)
    if len(series) < 2:
        return None
    prev, curr = series[-2], series[-1]
    if prev < level <= curr:
        return ("up", prev, curr)
    if prev >= level > curr:
        return ("down", prev, curr)
    return None


def closed_closes_with_age(klines):
    """
    Bỏ cây nến đang chạy dở (chưa đóng). Trả về (list giá đóng cửa các nến ĐÃ ĐÓNG,
    số phút kể từ lúc nến đóng gần nhất đóng cửa).
    """
    now_ms = time.time() * 1000
    closed = [k for k in klines if k[6] < now_ms]  # k[6] = thời điểm đóng nến (ms)
    if not closed:
        return [], None
    age_minutes = (now_ms - closed[-1][6]) / 60000
    return [float(k[4]) for k in closed], age_minutes


def scan_market(market_label, base_url, klines_path, symbols):
    """Quét 1 danh sách symbol của 1 thị trường (Spot hoặc Futures), trả về số cảnh báo đã gửi."""
    alerts_sent = 0
    for symbol in symbols:
        triggered_rsi_1h = None  # None = không trigger, số = giá trị RSI đã trigger
        triggered_rsi_4h = None
        rsi_cross_30m = None  # None hoặc ("up"/"down", rsi_trước, rsi_sau)
        rsi_cross_1h = None

        # --- Khung 1h: RSI quá mua/quá bán + RSI cắt mốc 50 (dùng chung 1 lần tải dữ liệu) ---
        try:
            klines = get_klines(base_url, klines_path, symbol, INTERVAL, limit=HISTORY_LIMIT)
            closes = [float(k[4]) for k in klines]

            rsi_1h = calculate_rsi(closes, RSI_PERIOD)
            if rsi_1h is not None and (rsi_1h <= RSI_OVERSOLD or rsi_1h >= RSI_OVERBOUGHT):
                triggered_rsi_1h = rsi_1h
                print(f"  -> [{market_label}] {symbol} RSI({INTERVAL})={rsi_1h}")

            # RSI cắt mốc 50 trên nến 1h vừa đóng
            closed_closes_1h, age_1h = closed_closes_with_age(klines)
            if age_1h is not None and age_1h <= FRESH_WINDOW_MINUTES:
                rsi_cross_1h = detect_rsi_midline_cross(closed_closes_1h, RSI_PERIOD)
                if rsi_cross_1h:
                    print(f"  -> [{market_label}] {symbol} RSI(1H) cắt {rsi_cross_1h[0]} mốc {RSI_MIDLINE}")

        except Exception as e:
            print(f"Lỗi khi xử lý [{market_label}] {symbol} khung {INTERVAL}: {e}")

        time.sleep(REQUEST_DELAY)

        # --- Khung 4h: RSI quá mua/quá bán ---
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

        # --- Khung 30m: chỉ RSI cắt mốc 50 (không báo RSI quá mua/quá bán ở khung này) ---
        try:
            klines_30m = get_klines(base_url, klines_path, symbol, INTERVAL_30M, limit=HISTORY_LIMIT)

            # RSI cắt mốc 50 trên nến 30m vừa đóng
            closed_closes_30m, age_30m = closed_closes_with_age(klines_30m)
            if age_30m is not None and age_30m <= FRESH_WINDOW_MINUTES:
                rsi_cross_30m = detect_rsi_midline_cross(closed_closes_30m, RSI_PERIOD)
                if rsi_cross_30m:
                    print(f"  -> [{market_label}] {symbol} RSI(30M) cắt {rsi_cross_30m[0]} mốc {RSI_MIDLINE}")

        except Exception as e:
            print(f"Lỗi khi xử lý [{market_label}] {symbol} khung {INTERVAL_30M}: {e}")

        time.sleep(REQUEST_DELAY)

        # --- Chỉ hiện tín hiệu nào THỰC SỰ trigger, bỏ qua phần chưa đạt ---
        has_signal = (
            triggered_rsi_1h is not None
            or triggered_rsi_4h is not None
            or rsi_cross_30m is not None
            or rsi_cross_1h is not None
        )

        if has_signal:
            parts = []
            emojis = []

            if triggered_rsi_1h is not None:
                parts.append(f"RSI(1H): {triggered_rsi_1h}")
                emojis.append("🟢" if triggered_rsi_1h <= RSI_OVERSOLD else "🔴")
            if triggered_rsi_4h is not None:
                parts.append(f"RSI(4H): {triggered_rsi_4h}")
                emojis.append("🟢" if triggered_rsi_4h <= RSI_OVERSOLD_4H else "🔴")
            if rsi_cross_30m is not None:
                direction, prev_rsi, curr_rsi = rsi_cross_30m
                word = "cắt lên" if direction == "up" else "cắt xuống"
                parts.append(f"RSI(30M) {word} {RSI_MIDLINE}: {prev_rsi:.1f} ➜ {curr_rsi:.1f}")
                emojis.append("🟢" if direction == "up" else "🔴")
            if rsi_cross_1h is not None:
                direction, prev_rsi, curr_rsi = rsi_cross_1h
                word = "cắt lên" if direction == "up" else "cắt xuống"
                parts.append(f"RSI(1H) {word} {RSI_MIDLINE}: {prev_rsi:.1f} ➜ {curr_rsi:.1f}")
                emojis.append("🟢" if direction == "up" else "🔴")

            # Bỏ emoji trùng nhau (vd nhiều tín hiệu cùng chiều thì chỉ hiện 1 dấu)
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
