import requests
import json

SUPABASE_URL = "https://murynwlbdgxkimfgunfx.supabase.co"
ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im11cnlud2xiZGd4a2ltZmd1bmZ4Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzk4Mzc4MDAsImV4cCI6MjA5NTQxMzgwMH0.Zhz9S4wUEaoQhMNke_EPoGqfdw21yvshNBFx4GUNfuI"

HEADERS = {
    "Authorization": f"Bearer {ANON_KEY}",
    "Content-Type": "application/json"
}

def call_router(payload: dict) -> dict:
    resp = requests.post(
        f"{SUPABASE_URL}/functions/v1/quote-router-v9",
        headers=HEADERS,
        json=payload,
        timeout=30
    )
    return resp.json()

def validate_result(symbol: str, data: dict):
    errors = []

    # 1. 必填欄位
    required_fields = ["symbol", "last_price", "prev_close", "status", "data_freshness", "source_key"]
    for f in required_fields:
        if f not in data:
            errors.append(f"缺少欄位: {f}")

    # 2. 非開盤時間 → 應為 daily 模式
    freshness = data.get("data_freshness", "")
    if freshness not in ("daily", "delayed_20m+"):
        errors.append(f"非交易時段但 data_freshness={freshness}，預期 daily")

    # 3. 價格合理性（台股一般 1~10000 元）
    price = data.get("last_price")
    if price is not None and not (1 <= price <= 10000):
        errors.append(f"價格異常: {price}")

    # 4. 漲跌幅合理性（漲跌停 ±10%，留些容錯空間）
    change_pct = data.get("change_pct")
    if change_pct is not None and abs(change_pct) > 15:
        errors.append(f"漲跌幅異常: {change_pct}%")

    # 5. status 應為 ok 或 ok_fallback
    status = data.get("status", "")
    if not status.startswith("ok"):
        errors.append(f"status 非預期: {status}")

    if errors:
        print(f"  ❌ {symbol}: {errors}")
    else:
        print(f"  ✅ {symbol}: price={price}, freshness={freshness}, source={data.get('source_key')}")

# ─── 測試案例 ───────────────────────────────────
TEST_CASES = [
    # (說明, payload)
    ("一般測試 auto 模式",         {"symbols": ["2330", "2454", "0050"]}),
    ("強制 daily 模式",            {"symbols": ["2330"], "mode": "daily"}),
    ("強制 realtime（預期 fallback）", {"symbols": ["2330"], "mode": "realtime"}),
    ("上櫃股票",                   {"symbols": ["6547"]}),
    ("ETF",                        {"symbols": ["00878", "00720B"]}),
    ("空請求（預設清單）",          {}),
]

for desc, payload in TEST_CASES:
    print(f"\n【{desc}】")
    result = call_router(payload)

    # result 可能是 list 或 dict
    items = result if isinstance(result, list) else result.get("data", [result])
    for item in items:
        sym = item.get("symbol", "?")
        validate_result(sym, item)
