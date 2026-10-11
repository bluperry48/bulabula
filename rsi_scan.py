"""
RSI Alert Bot cho Binance -> Telegram (bản chạy trên GitHub Actions)
--------------------------------------------------------------------
Quét TẤT CẢ 1 LẦN rồi thoát (GitHub Actions sẽ tự chạy lại theo lịch).
Token/Chat ID được đọc từ biến môi trường (GitHub Secrets), KHÔNG lưu
trực tiếp trong code để tránh lộ thông tin khi đẩy code lên GitHub công khai.
"""

import os
import json
import time
import collections
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

EMA_FAST = 50
EMA_SLOW = 200

# Số nến tải về mỗi lần - EMA200 cần rất nhiều dữ liệu lịch sử để tính chính xác
HISTORY_LIMIT = 500

# --- Danh sách bStocks (token cổ phiếu hóa của Binance) cần LOẠI KHỎI việc quét ---
# Chỉ ghi phần tên coin gốc (không cần đuôi USDT), code sẽ tự ghép thành XXXUSDT.
# Lưu ý: Binance liên tục list thêm bStocks mới, nếu thấy cặp nào mới xuất hiện
# trong Telegram thì chỉ cần thêm tên coin gốc của nó vào danh sách này.
BSTOCK_BASES = {
    # Đợt 6/2026
    "CRCLB", "MUB", "NVDAB", "SNDKB", "TSLAB", "BNCB",
    # Đợt 29/7/2026
    "AAPLB", "AMATB", "AMZNB", "BEB", "DELLB", "FLNCB", "GSB", "PYPLB", "SMHB", "SOXSB",
    # Đợt 30/9/2026
    "ADBEB", "FWDIB", "HPEB", "PDDB", "SHAZB", "WENB", "ZMB",
    # Đợt 7/10/2026
    "JPMB", "LLYB", "SECZB", "USDEB",
    # Bổ sung theo báo cáo thực tế từ Telegram
    "MRNAB", "PLTRB",
}
EXCLUDED_SYMBOLS = {base + QUOTE_ASSET for base in BSTOCK_BASES}


def send_telegram_message(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        r = requests.post(url, data=payload, timeout=10)
        if r.status_code != 200:
            print(f"[Telegram] Lỗi gửi tin nhắn: {r.status_code} - {r.text}")
    except Exception as e:
        print(f"[Telegram] Exception: {e}")


# --- Tự động nhận diện bStocks MỚI (không cần sửa tay khi Binance list thêm) ---
# Cách hoạt động: mỗi lần chạy, bot lấy danh sách bStocks đã biết (BSTOCK_BASES) làm "mẫu",
# so sánh các thuộc tính quyền giao dịch của chúng với coin thường. Nếu tìm được thuộc
# tính mà bStocks gần như đều giống nhau còn coin thường hầu như không có, bot dùng nó
# (kết hợp điều kiện tên kết thúc bằng chữ B) để loại luôn các bStocks mới chưa có trong danh sách.
SIGNATURE_FIELDS = [
    "permissionSets", "permissions", "isMarginTradingAllowed", "icebergAllowed",
    "ocoAllowed", "otoAllowed", "allowTrailingStop", "cancelReplaceAllowed",
    "quoteOrderQtyMarketAllowed", "orderTypes", "defaultSelfTradePreventionMode",
    "allowedSelfTradePreventionModes",
]
AUTO_MIN_SAMPLE = 5          # cần ít nhất 5 bStocks mẫu có mặt trên sàn mới tự học
AUTO_MIN_KNOWN_SHARE = 0.9   # >= 90% bStocks mẫu phải có cùng giá trị thuộc tính
AUTO_MAX_OTHER_SHARE = 0.02  # <= 2% coin thường được phép có cùng giá trị đó
AUTO_MAX_EXCLUDE = 80        # chốt an toàn: nếu tự phát hiện quá 80 cặp thì bỏ qua (nghi nhận diện sai)


def _field_value(symbol_info, field):
    value = symbol_info.get(field)
    if field in ("orderTypes", "allowedSelfTradePreventionModes") and isinstance(value, list):
        value = sorted(value)
    return json.dumps(value, sort_keys=True)


def detect_new_bstocks(usdt_symbols):
    """Trả về set các symbol bStocks MỚI (chưa có trong danh sách tay) được tự phát hiện."""
    known = [s for s in usdt_symbols if s["baseAsset"] in BSTOCK_BASES]
    others = [s for s in usdt_symbols if s["baseAsset"] not in BSTOCK_BASES]

    if len(known) < AUTO_MIN_SAMPLE or len(others) < 50:
        print("[bStocks] Không đủ mẫu để tự học dấu hiệu nhận biết, chỉ dùng danh sách tay.")
        return set()

    rules = {}
    for field in SIGNATURE_FIELDS:
        counts = collections.Counter(_field_value(s, field) for s in known)
        value, n = counts.most_common(1)[0]
        if n / len(known) < AUTO_MIN_KNOWN_SHARE:
            continue
        share_others = sum(1 for s in others if _field_value(s, field) == value) / len(others)
        if share_others <= AUTO_MAX_OTHER_SHARE:
            rules[field] = value

    if not rules:
        print("[bStocks] Không tìm thấy dấu hiệu đặc trưng của bStocks, chỉ dùng danh sách tay.")
        return set()

    print(f"[bStocks] Dấu hiệu nhận biết tự học được: {rules}")

    detected = set()
    for s in others:
        if not s["baseAsset"].endswith("B"):
            continue  # bStocks luôn có tên kết thúc bằng B -> thêm lớp bảo vệ chống loại nhầm
        if all(_field_value(s, f) == v for f, v in rules.items()):
            detected.add(s["symbol"])

    if len(detected) > AUTO_MAX_EXCLUDE:
        print(f"[bStocks] Tự phát hiện {len(detected)} cặp (quá nhiều, nghi nhận diện sai) -> bỏ qua.")
        return set()

    return detected


def get_all_spot_usdt_symbols():
    """
    Lấy danh sách cặp Spot USDT đang giao dịch, TỰ CẬP NHẬT mỗi lần chạy:
    cặp mới list sẽ được quét, cặp bị hủy niêm yết (không còn TRADING) tự biến mất.
    Đồng thời loại bỏ bStocks (danh sách tay + tự phát hiện).
    """
    url = f"{BINANCE_SPOT_BASE}/api/v3/exchangeInfo"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()

    usdt_symbols = [
        s for s in data["symbols"]
        if s["quoteAsset"] == QUOTE_ASSET
        and s["status"] == "TRADING"
        and s.get("isSpotTradingAllowed", True)
    ]

    auto_detected = detect_new_bstocks(usdt_symbols)
    if auto_detected:
        print(f"[bStocks] Tự phát hiện thêm {len(auto_detected)} cặp bStocks mới: {sorted(auto_detected)}")

    excluded = EXCLUDED_SYMBOLS | auto_detected
    symbols = [s["symbol"] for s in usdt_symbols if s["symbol"] not in excluded]

    # Gợi ý để bạn kiểm tra: coin tên kết thúc bằng B nhưng vẫn chưa bị loại
    # (thường chỉ là coin thật như BNB; nếu thấy bStocks mới lọt vào đây thì thêm vào BSTOCK_BASES)
    suspects = sorted(
        s["baseAsset"] for s in usdt_symbols
        if s["baseAsset"].endswith("B") and s["symbol"] not in excluded
    )
    if suspects:
        print(f"[bStocks] Coin tên kết thúc bằng B chưa bị loại (kiểm tra thủ công nếu cần): {suspects}")

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


def calculate_ema_series(values, period: int):
    """Trả về list EMA, ema[0] tương ứng values[period-1] (điểm đầu tiên đủ dữ liệu)."""
    if len(values) < period:
        return []
    ema = [sum(values[:period]) / period]  # SMA làm giá trị khởi tạo
    multiplier = 2 / (period + 1)
    for price in values[period:]:
        ema.append((price - ema[-1]) * multiplier + ema[-1])
    return ema


def detect_ema_cross(closes, fast=EMA_FAST, slow=EMA_SLOW):
    """
    Trả về "up" nếu EMA nhanh vừa cắt LÊN EMA chậm (Golden Cross),
    "down" nếu vừa cắt XUỐNG (Death Cross), None nếu không có tín hiệu.
    """
    ema_fast = calculate_ema_series(closes, fast)
    ema_slow = calculate_ema_series(closes, slow)
    if not ema_fast or not ema_slow:
        return None

    offset = slow - fast  # ema_fast đi trước ema_slow "offset" điểm dữ liệu
    diff_series = []
    for j in range(len(ema_slow)):
        if j + offset >= len(ema_fast):
            break
        diff_series.append(ema_fast[j + offset] - ema_slow[j])

    if len(diff_series) < 2:
        return None

    prev, curr = diff_series[-2], diff_series[-1]
    if prev < 0 <= curr:
        return "up"
    elif prev >= 0 > curr:
        return "down"
    return None


def scan_market(market_label, base_url, klines_path, symbols):
    """Quét 1 danh sách symbol của 1 thị trường (Spot hoặc Futures), trả về số cảnh báo đã gửi."""
    alerts_sent = 0
    for symbol in symbols:
        triggered_rsi_1h = None  # None = không trigger, số = giá trị RSI đã trigger
        triggered_rsi_4h = None
        ema_cross_1h = None  # None = không trigger, "up"/"down" = hướng vừa cắt
        ema_cross_4h = None
        ema_cross_30m = None

        # --- Khung 1h: RSI + EMA50/200 (dùng chung 1 lần tải dữ liệu) ---
        try:
            klines = get_klines(base_url, klines_path, symbol, INTERVAL, limit=HISTORY_LIMIT)
            closes = [float(k[4]) for k in klines]

            rsi_1h = calculate_rsi(closes, RSI_PERIOD)
            if rsi_1h is not None and (rsi_1h <= RSI_OVERSOLD or rsi_1h >= RSI_OVERBOUGHT):
                triggered_rsi_1h = rsi_1h
                print(f"  -> [{market_label}] {symbol} RSI({INTERVAL})={rsi_1h}")

            ema_cross_1h = detect_ema_cross(closes)
            if ema_cross_1h:
                print(f"  -> [{market_label}] {symbol} EMA50/200({INTERVAL}) cắt {ema_cross_1h}")

        except Exception as e:
            print(f"Lỗi khi xử lý [{market_label}] {symbol} khung {INTERVAL}: {e}")

        time.sleep(REQUEST_DELAY)

        # --- Khung 4h: RSI + EMA50/200 ---
        try:
            klines_4h = get_klines(base_url, klines_path, symbol, INTERVAL_4H, limit=HISTORY_LIMIT)
            closes_4h = [float(k[4]) for k in klines_4h]

            rsi_4h = calculate_rsi(closes_4h, RSI_PERIOD)
            if rsi_4h is not None and (rsi_4h <= RSI_OVERSOLD_4H or rsi_4h >= RSI_OVERBOUGHT_4H):
                triggered_rsi_4h = rsi_4h
                print(f"  -> [{market_label}] {symbol} RSI({INTERVAL_4H})={rsi_4h}")

            ema_cross_4h = detect_ema_cross(closes_4h)
            if ema_cross_4h:
                print(f"  -> [{market_label}] {symbol} EMA50/200({INTERVAL_4H}) cắt {ema_cross_4h}")

        except Exception as e:
            print(f"Lỗi khi xử lý [{market_label}] {symbol} khung {INTERVAL_4H}: {e}")

        time.sleep(REQUEST_DELAY)

        # --- Khung 30m: chỉ EMA50/200 (không cần RSI khung này) ---
        try:
            klines_30m = get_klines(base_url, klines_path, symbol, INTERVAL_30M, limit=HISTORY_LIMIT)
            closes_30m = [float(k[4]) for k in klines_30m]

            ema_cross_30m = detect_ema_cross(closes_30m)
            if ema_cross_30m:
                print(f"  -> [{market_label}] {symbol} EMA50/200({INTERVAL_30M}) cắt {ema_cross_30m}")

        except Exception as e:
            print(f"Lỗi khi xử lý [{market_label}] {symbol} khung {INTERVAL_30M}: {e}")

        time.sleep(REQUEST_DELAY)

        # --- Chỉ hiện tín hiệu nào THỰC SỰ trigger (RSI hoặc EMA cross), bỏ qua phần chưa đạt ---
        has_signal = (
            triggered_rsi_1h is not None
            or triggered_rsi_4h is not None
            or ema_cross_1h is not None
            or ema_cross_4h is not None
            or ema_cross_30m is not None
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
            if ema_cross_30m is not None:
                label = "EMA50 cắt lên EMA200 (Golden Cross)" if ema_cross_30m == "up" else "EMA50 cắt xuống EMA200 (Death Cross)"
                parts.append(f"[30M] {label}")
                emojis.append("🟢" if ema_cross_30m == "up" else "🔴")
            if ema_cross_1h is not None:
                label = "EMA50 cắt lên EMA200 (Golden Cross)" if ema_cross_1h == "up" else "EMA50 cắt xuống EMA200 (Death Cross)"
                parts.append(f"[1H] {label}")
                emojis.append("🟢" if ema_cross_1h == "up" else "🔴")
            if ema_cross_4h is not None:
                label = "EMA50 cắt lên EMA200 (Golden Cross)" if ema_cross_4h == "up" else "EMA50 cắt xuống EMA200 (Death Cross)"
                parts.append(f"[4H] {label}")
                emojis.append("🟢" if ema_cross_4h == "up" else "🔴")

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
