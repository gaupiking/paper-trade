import streamlit as st
import pandas as pd
import requests
import sqlite3
import json
import math
from pathlib import Path
from datetime import datetime, timedelta
import plotly.graph_objects as go
import plotly.express as px
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# =========================================================
# Page
# =========================================================
st.set_page_config(
    page_title="STP 模擬交易平台｜台股競賽版",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# =========================================================
# Style
# =========================================================
st.markdown("""
<style>
.block-container { padding-top: 1rem; padding-bottom: 1rem; max-width: 100%; }
div[data-testid="metric-container"] {
    background: #111827;
    border: 1px solid #374151;
    border-radius: 14px;
    padding: 12px 14px;
    box-shadow: 0 6px 16px rgba(0,0,0,0.15);
}
div[data-testid="stDataFrame"] {
    border: 1px solid #2b2f3a;
    border-radius: 12px;
    overflow: hidden;
}
.stButton > button {
    border-radius: 10px;
    font-weight: 700;
}
.section-card {
    border: 1px solid #2b2f3a;
    border-radius: 16px;
    padding: 14px 14px 8px 14px;
    background: linear-gradient(180deg, rgba(17,24,39,0.95), rgba(17,24,39,0.85));
    box-shadow: 0 8px 22px rgba(0,0,0,0.18);
}
hr { margin: 0.6rem 0 0.9rem 0; }
</style>
""", unsafe_allow_html=True)

# =========================================================
# Constants
# =========================================================
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = str(BASE_DIR / "stp_local.db")

INITIAL_CAPITAL = 200000000
FEE_RATE = 0.0004
TAX_STOCK = 0.003
TAX_ETF = 0.001
TOTAL_LOSS_LIMIT = 20000000
PHASE_LOSS_LIMIT = 10000000
DRAWDOWN_LIMIT = 0.10

TWSE_LIVE_URL = "https://openapi.twse.com.tw/api/v2/RealTimeQuote"
TPEX_LIVE_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
FINMIND_BASE = "https://api.finmindtrade.com/api/v4/data"

DEFAULT_WATCH_ETF = ["0050", "0056", "006208", "00878"]
DEFAULT_WATCH_STOCK = ["2330", "2317", "2454", "2881"]

# =========================================================
# Session State
# =========================================================
defaults = {
    "mode": "ETF組",
    "cash_etf": INITIAL_CAPITAL,
    "cash_stock": INITIAL_CAPITAL,
    "realized_etf": 0.0,
    "realized_stock": 0.0,
    "positions_etf": {},
    "positions_stock": {},
    "orders": [],
    "trades": [],
    "performance": [],
    "watch_etf": DEFAULT_WATCH_ETF[:],
    "watch_stock": DEFAULT_WATCH_STOCK[:],
    "selected_symbol": "0050",
    "source_mode": "AUTO",
    "finmind_token": "",
    "yahoo_debug": False,
    "market_prices": {},
    "daily_quotes": {},
    "dividend_preview": [],
    "last_refresh": None,
    "debug_logs": [],
    "max_equity": INITIAL_CAPITAL,
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# =========================================================
# DB helpers
# =========================================================
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS quotes_live (
        symbol TEXT,
        last_price REAL,
        prev_close REAL,
        change_val REAL,
        change_pct REAL,
        volume INTEGER,
        quote_time TEXT,
        market_source TEXT,
        raw_json TEXT,
        PRIMARY KEY (symbol, quote_time)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS prices_daily (
        symbol TEXT,
        trade_date TEXT,
        open REAL,
        high REAL,
        low REAL,
        close REAL,
        volume INTEGER,
        adjusted_close REAL,
        market_source TEXT,
        chip_json TEXT,
        raw_json TEXT,
        PRIMARY KEY (symbol, trade_date)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        order_id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT,
        group_name TEXT,
        symbol TEXT,
        symbol_name TEXT,
        side TEXT,
        order_type TEXT,
        price REAL,
        qty INTEGER,
        status TEXT,
        reason TEXT,
        filled_qty INTEGER DEFAULT 0,
        remaining_qty INTEGER DEFAULT 0,
        avg_fill_price REAL DEFAULT 0
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS fills (
        fill_id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER,
        filled_at TEXT,
        group_name TEXT,
        symbol TEXT,
        side TEXT,
        fill_price REAL,
        fill_qty INTEGER,
        fee REAL,
        tax REAL,
        pnl REAL,
        note TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS dividend_preview (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        symbol_name TEXT,
        ex_date TEXT,
        cash_dividend REAL,
        stock_dividend REAL,
        source TEXT,
        raw_json TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS leaderboard (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_date TEXT,
        group_name TEXT,
        cash REAL,
        equity REAL,
        pnl REAL,
        return_pct REAL,
        drawdown REAL,
        capital_usage REAL,
        sharpe REAL
    )
    """)
    conn.commit()
    conn.close()

def table_exists(conn, table_name):
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
    return cur.fetchone() is not None

def safe_read_table(conn, table_name, order_col=None):
    if not table_exists(conn, table_name):
        return pd.DataFrame()
    sql = f"SELECT * FROM {table_name}"
    if order_col:
        sql += f" ORDER BY {order_col} DESC"
    try:
        return pd.read_sql_query(sql, conn)
    except Exception:
        return pd.DataFrame()

def upsert_many(table, rows, pk_cols):
    if not rows:
        return
    conn = get_conn()
    cur = conn.cursor()
    cols = list(rows[0].keys())
    placeholders = ", ".join(["?"] * len(cols))
    insert_cols = ", ".join(cols)
    update_cols = ", ".join([f"{c}=excluded.{c}" for c in cols if c not in pk_cols])
    sql = f"""
    INSERT INTO {table} ({insert_cols})
    VALUES ({placeholders})
    ON CONFLICT({", ".join(pk_cols)}) DO UPDATE SET
    {update_cols}
    """
    cur.executemany(sql, [[r.get(c) for c in cols] for r in rows])
    conn.commit()
    conn.close()

def save_dividend_preview(rows):
    if not rows:
        return
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM dividend_preview")
    cur.executemany("""
        INSERT INTO dividend_preview
        (symbol, symbol_name, ex_date, cash_dividend, stock_dividend, source, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, [
        (
            r.get("symbol", ""),
            r.get("symbol_name", ""),
            r.get("ex_date", ""),
            r.get("cash_dividend", 0.0),
            r.get("stock_dividend", 0.0),
            r.get("source", ""),
            r.get("raw_json", "")
        )
        for r in rows
    ])
    conn.commit()
    conn.close()

# =========================================================
# Utils
# =========================================================
def safe_float(v, default=0.0):
    try:
        if v in [None, "", "-", "NaN"]:
            return default
        return float(str(v).replace(",", "").strip())
    except:
        return default

def safe_int(v, default=0):
    try:
        if v in [None, "", "-", "NaN"]:
            return default
        return int(float(str(v).replace(",", "").strip()))
    except:
        return default

def is_etf(symbol):
    return symbol.startswith("00") or symbol.startswith("006") or symbol.startswith("008")

def request_json(url, params=None, timeout=12):
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, params=params, headers=headers, timeout=timeout, verify=False)
    r.raise_for_status()
    return r.json()

def fmt_money(x):
    return f"${x:,.0f}"

def group_cash():
    return st.session_state.cash_etf if st.session_state.mode == "ETF組" else st.session_state.cash_stock

def set_group_cash(v):
    if st.session_state.mode == "ETF組":
        st.session_state.cash_etf = v
    else:
        st.session_state.cash_stock = v

def group_realized():
    return st.session_state.realized_etf if st.session_state.mode == "ETF組" else st.session_state.realized_stock

def set_group_realized(v):
    if st.session_state.mode == "ETF組":
        st.session_state.realized_etf = v
    else:
        st.session_state.realized_stock = v

def group_positions():
    return st.session_state.positions_etf if st.session_state.mode == "ETF組" else st.session_state.positions_stock

def set_group_positions(v):
    if st.session_state.mode == "ETF組":
        st.session_state.positions_etf = v
    else:
        st.session_state.positions_stock = v

def watchlist():
    return st.session_state.watch_etf if st.session_state.mode == "ETF組" else st.session_state.watch_stock

def current_positions_value():
    total = 0.0
    for sym, pos in group_positions().items():
        px = st.session_state.market_prices.get(sym, {}).get("price", pos["avg_cost"])
        total += px * pos["qty"]
    return total

def current_unrealized():
    total = 0.0
    for sym, pos in group_positions().items():
        px = st.session_state.market_prices.get(sym, {}).get("price", pos["avg_cost"])
        total += (px - pos["avg_cost"]) * pos["qty"]
    return total

def current_equity():
    return group_cash() + current_positions_value()

def current_total_pnl():
    return current_unrealized() + group_realized()

def capital_usage():
    eq = current_equity()
    return 0 if eq <= 0 else current_positions_value() / eq

def max_drawdown():
    if not st.session_state.performance:
        return 0.0
    eqs = [x["帳戶總淨值"] for x in st.session_state.performance]
    peak = eqs[0]
    mdd = 0.0
    for v in eqs:
        peak = max(peak, v)
        dd = 0 if peak == 0 else (peak - v) / peak
        mdd = max(mdd, dd)
    return mdd

def sharpe_like():
    if len(st.session_state.performance) < 2:
        return 0.0
    vals = [x["帳戶總淨值"] for x in st.session_state.performance]
    rets = []
    for i in range(1, len(vals)):
        prev = vals[i - 1]
        if prev > 0:
            rets.append((vals[i] - prev) / prev)
    if not rets:
        return 0.0
    avg = sum(rets) / len(rets)
    var = sum((x - avg) ** 2 for x in rets) / max(1, len(rets) - 1)
    std = math.sqrt(var)
    return 0.0 if std == 0 else (avg / std) * math.sqrt(252)

# =========================================================
# Data sources
# =========================================================
@st.cache_data(ttl=20)
def fetch_twse_live(symbols):
    try:
        data = request_json(TWSE_LIVE_URL, timeout=15)
    except:
        return []
    rows = []
    sset = set(symbols)
    if isinstance(data, list):
        for item in data:
            sym = str(item.get("Code", "")).strip()
            if sym not in sset:
                continue
            last = safe_float(item.get("Price") or item.get("ClosingPrice"))
            prev = safe_float(item.get("PreviousClosePrice") or item.get("ReferencePrice"))
            vol = safe_int(item.get("TodayVolume"))
            chg = last - prev if prev else 0
            pct = (chg / prev * 100) if prev else 0
            rows.append({
                "symbol": sym,
                "last_price": last,
                "prev_close": prev,
                "change_val": chg,
                "change_pct": pct,
                "volume": vol,
                "quote_time": datetime.now().isoformat(timespec="seconds"),
                "market_source": "TWSE",
                "raw_json": json.dumps(item, ensure_ascii=False)
            })
    return rows

@st.cache_data(ttl=20)
def fetch_tpex_live(symbols):
    try:
        data = request_json(TPEX_LIVE_URL, timeout=15)
    except:
        return []
    rows = []
    sset = set(symbols)
    if isinstance(data, list):
        for item in data:
            sym = str(item.get("SecuritiesCompanyCode", "")).strip()
            if sym not in sset:
                continue
            last = safe_float(item.get("Close"))
            prev = safe_float(item.get("ReferencePrice") or item.get("PreviousClose"))
            vol = safe_int(item.get("TradeVolume"))
            chg = last - prev if prev else 0
            pct = (chg / prev * 100) if prev else 0
            rows.append({
                "symbol": sym,
                "last_price": last,
                "prev_close": prev,
                "change_val": chg,
                "change_pct": pct,
                "volume": vol,
                "quote_time": datetime.now().isoformat(timespec="seconds"),
                "market_source": "TPEx",
                "raw_json": json.dumps(item, ensure_ascii=False)
            })
    return rows

@st.cache_data(ttl=300)
def fetch_yahoo_debug(symbols):
    logs = []
    rows = []
    for sym in symbols:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}.TW"
            data = request_json(url, timeout=12)
            result = data.get("chart", {}).get("result", [None])[0]
            if not result:
                logs.append({"股票代號": sym, "狀態": "錯誤", "訊息": "no result"})
                continue
            meta = result.get("meta", {})
            last = safe_float(meta.get("regularMarketPrice"))
            prev = safe_float(meta.get("previousClose"))
            vol = safe_int(meta.get("regularMarketVolume"))
            chg = last - prev if prev else 0
            pct = (chg / prev * 100) if prev else 0
            rows.append({
                "symbol": sym,
                "last_price": last,
                "prev_close": prev,
                "change_val": chg,
                "change_pct": pct,
                "volume": vol,
                "quote_time": datetime.now().isoformat(timespec="seconds"),
                "market_source": "Yahoo",
                "raw_json": json.dumps(data, ensure_ascii=False)
            })
            logs.append({"股票代號": sym, "狀態": "成功", "訊息": f"價格={last}"})
        except Exception as e:
            logs.append({"股票代號": sym, "狀態": "錯誤", "訊息": str(e)})
    return rows, logs

@st.cache_data(ttl=3600)
def fetch_finmind_daily(symbols, token):
    if not token:
        return []
    rows = []
    for sym in symbols:
        try:
            params = {
                "dataset": "TaiwanStockPrice",
                "data_id": sym,
                "start_date": (datetime.now() - timedelta(days=10)).date().isoformat(),
                "end_date": datetime.now().date().isoformat(),
                "token": token
            }
            data = request_json(FINMIND_BASE, params=params, timeout=20)
            ds = data.get("data", [])
            if not ds:
                continue
            latest = ds[-1]
            rows.append({
                "symbol": sym,
                "trade_date": latest.get("date", datetime.now().date().isoformat()),
                "open": safe_float(latest.get("open")),
                "high": safe_float(latest.get("max")),
                "low": safe_float(latest.get("min")),
                "close": safe_float(latest.get("close")),
                "volume": safe_int(latest.get("Trading_Volume") or latest.get("volume")),
                "adjusted_close": safe_float(latest.get("close")),
                "market_source": "FinMind",
                "chip_json": None,
                "raw_json": json.dumps(latest, ensure_ascii=False)
            })
        except:
            pass
    return rows

@st.cache_data(ttl=86400)
def fetch_dividend_preview(symbols):
    today = datetime.now().date()
    rows = []
    for sym in symbols:
        rows.append({
            "symbol": sym,
            "symbol_name": sym,
            "ex_date": (today + timedelta(days=30)).isoformat(),
            "cash_dividend": 0.0,
            "stock_dividend": 0.0,
            "source": "MOPS/預告",
            "raw_json": json.dumps({"note": "預留除權息預告資料"}, ensure_ascii=False)
        })
    return rows

def market_router(symbols):
    finmind_token = st.session_state.finmind_token.strip()
    debug_logs = []
    if st.session_state.source_mode == "AUTO":
        live_rows = fetch_twse_live(symbols)
        if not live_rows:
            live_rows = fetch_tpex_live(symbols)
        if not live_rows:
            live_rows, debug_logs = fetch_yahoo_debug(symbols)
    elif st.session_state.source_mode == "TWSE":
        live_rows = fetch_twse_live(symbols)
    else:
        live_rows = fetch_tpex_live(symbols)

    daily_rows = fetch_finmind_daily(symbols, finmind_token)
    dividend_rows = fetch_dividend_preview(symbols)
    return live_rows, daily_rows, dividend_rows, debug_logs

# =========================================================
# Trade
# =========================================================
def save_trade(side, symbol, price, qty, reason, order_type):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fee = round(price * qty * FEE_RATE)
    tax = 0
    pnl = 0.0
    pos = group_positions()

    if side == "買入":
        total = price * qty + fee
        if total > group_cash():
            return False, "可用資金不足", None
        set_group_cash(group_cash() - total)
        old = pos.get(symbol, {"qty": 0, "avg_cost": 0.0, "type": "ETF" if is_etf(symbol) else "股票"})
        new_qty = old["qty"] + qty
        new_avg = ((old["avg_cost"] * old["qty"]) + total) / new_qty
        old["qty"] = new_qty
        old["avg_cost"] = new_avg
        old["type"] = "ETF" if is_etf(symbol) else "股票"
        pos[symbol] = old
        set_group_positions(pos)
    else:
        if symbol not in pos or pos[symbol]["qty"] < qty:
            return False, "庫存不足", None
        tax = round(price * qty * (TAX_ETF if is_etf(symbol) else TAX_STOCK))
        pnl = (price - pos[symbol]["avg_cost"]) * qty - fee - tax
        set_group_cash(group_cash() + (price * qty - fee - tax))
        set_group_realized(group_realized() + pnl)
        pos[symbol]["qty"] -= qty
        if pos[symbol]["qty"] <= 0:
            del pos[symbol]
        set_group_positions(pos)

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO orders
        (created_at, group_name, symbol, symbol_name, side, order_type, price, qty, status, reason, filled_qty, remaining_qty, avg_fill_price)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (now, st.session_state.mode, symbol, symbol, side, order_type, price, qty, "已成交", reason, qty, 0, price))
    oid = cur.lastrowid
    cur.execute("""
        INSERT INTO fills
        (order_id, filled_at, group_name, symbol, side, fill_price, fill_qty, fee, tax, pnl, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (oid, now, st.session_state.mode, symbol, side, price, qty, fee, tax, pnl, reason))
    conn.commit()
    conn.close()

    st.session_state.orders.insert(0, {
        "委託時間": now, "組別": st.session_state.mode, "股票代號": symbol,
        "買賣方向": side, "委託方式": order_type, "委託價": price,
        "數量": qty, "狀態": "已成交", "理由": reason
    })
    st.session_state.trades.insert(0, {
        "交易時間": now, "組別": st.session_state.mode, "交易標的": symbol,
        "買賣方向": side, "委託方式": order_type, "成交價": price,
        "數量": qty, "手續費": fee, "證交稅": tax, "理由": reason, "損益": pnl
    })
    return True, "成交完成", oid

# =========================================================
# Init
# =========================================================
init_db()

if not st.session_state.market_prices:
    live_rows, daily_rows, dividend_rows, debug_logs = market_router(st.session_state.watch_etf + st.session_state.watch_stock)
    if live_rows:
        st.session_state.market_prices = {
            r["symbol"]: {
                "price": r["last_price"],
                "prev_close": r["prev_close"],
                "source": r["market_source"],
                "quote_time": r["quote_time"]
            } for r in live_rows
        }
        upsert_many("quotes_live", live_rows, ["symbol", "quote_time"])
    if daily_rows:
        st.session_state.daily_quotes = {r["symbol"]: r for r in daily_rows}
        upsert_many("prices_daily", daily_rows, ["symbol", "trade_date"])
    if dividend_rows:
        st.session_state.dividend_preview = dividend_rows
        save_dividend_preview(dividend_rows)
    st.session_state.debug_logs = debug_logs

# =========================================================
# Header
# =========================================================
st.title("模擬交易控制台")

top = st.container(border=True)
with top:
    c1, c2, c3, c4 = st.columns([1.1, 1.1, 1.4, 1])
    with c1:
        st.session_state.mode = st.radio("交易組別", ["ETF組", "個股組"], horizontal=True)
    with c2:
        st.session_state.source_mode = st.selectbox("資料來源", ["AUTO", "TWSE", "TPEx"], index=["AUTO", "TWSE", "TPEx"].index(st.session_state.source_mode))
    with c3:
        st.session_state.finmind_token = st.text_input("FinMind Token", type="password", value=st.session_state.finmind_token)
    with c4:
        st.session_state.yahoo_debug = st.toggle("Yahoo 除錯面板", value=st.session_state.yahoo_debug)

    w1, w2, w3 = st.columns([1.4, 1, 1])
    with w1:
        current_watch = st.session_state.watch_etf if st.session_state.mode == "ETF組" else st.session_state.watch_stock
        watch_text = st.text_input("自選商品", ",".join(current_watch))
        parsed = [x.strip().upper() for x in watch_text.split(",") if x.strip()]
        if parsed:
            if st.session_state.mode == "ETF組":
                st.session_state.watch_etf = parsed
            else:
                st.session_state.watch_stock = parsed
    with w2:
        if st.button("更新商品報價", use_container_width=True):
            syms = st.session_state.watch_etf + st.session_state.watch_stock
            live_rows, daily_rows, dividend_rows, debug_logs = market_router(syms)
            if live_rows:
                st.session_state.market_prices = {
                    r["symbol"]: {"price": r["last_price"], "prev_close": r["prev_close"], "source": r["market_source"], "quote_time": r["quote_time"]}
                    for r in live_rows
                }
                upsert_many("quotes_live", live_rows, ["symbol", "quote_time"])
            if daily_rows:
                st.session_state.daily_quotes = {r["symbol"]: r for r in daily_rows}
                upsert_many("prices_daily", daily_rows, ["symbol", "trade_date"])
            if dividend_rows:
                st.session_state.dividend_preview = dividend_rows
                save_dividend_preview(dividend_rows)
            st.session_state.debug_logs = debug_logs
            st.session_state.last_refresh = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            st.rerun()
    with w3:
        if st.button("重置比賽", use_container_width=True):
            st.session_state.cash_etf = INITIAL_CAPITAL
            st.session_state.cash_stock = INITIAL_CAPITAL
            st.session_state.realized_etf = 0.0
            st.session_state.realized_stock = 0.0
            st.session_state.positions_etf = {}
            st.session_state.positions_stock = {}
            st.session_state.orders = []
            st.session_state.trades = []
            st.session_state.performance = []
            st.session_state.max_equity = INITIAL_CAPITAL
            st.success("已重置")
            st.rerun()

# =========================================================
# KPI
# =========================================================
equity = current_equity()
st.session_state.max_equity = max(st.session_state.max_equity, equity)
dd = 0 if st.session_state.max_equity <= 0 else (st.session_state.max_equity - equity) / st.session_state.max_equity
usage = capital_usage()
sharpe = sharpe_like()

k1, k2, k3, k4, k5, k6, k7, k8 = st.columns(8)
k1.metric("現金剩餘", fmt_money(group_cash()))
k2.metric("可用資金", fmt_money(group_cash()))
k3.metric("已實現損益", fmt_money(group_realized()))
k4.metric("未實現損益", fmt_money(current_unrealized()))
k5.metric("持股市值", fmt_money(current_positions_value()))
k6.metric("帳戶總淨值", fmt_money(equity))
k7.metric("最大回撤", f"{dd:.2%}")
k8.metric("資金使用率", f"{usage:.2%}")

if current_total_pnl() <= -TOTAL_LOSS_LIMIT:
    st.error("總虧損已達上限，暫停交易")
elif current_total_pnl() <= -PHASE_LOSS_LIMIT:
    st.warning("階段虧損已達上限，請注意風控")

st.markdown("---")

# =========================================================
# Main
# =========================================================
left, mid, right = st.columns([1.35, 1.15, 1])

with left:
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.subheader("商品報價")
    wl = watchlist()
    selected_symbol = st.selectbox("選擇商品", wl if wl else ["0050"], index=0)
    st.session_state.selected_symbol = selected_symbol
    q = st.session_state.market_prices.get(selected_symbol, {})
    a, b, c, d = st.columns(4)
    a.metric("現價", f"{q.get('price', 0):,.2f}")
    b.metric("漲跌", f"{q.get('price', 0) - q.get('prev_close', 0):,.2f}")
    c.metric("漲跌幅", f"{((q.get('price', 0) - q.get('prev_close', 0)) / q.get('prev_close', 1) * 100):,.2f}%")
    d.metric("來源", q.get("source", "-"))
    st.caption(f"最後更新：{q.get('quote_time', '-')}")
    if selected_symbol in st.session_state.daily_quotes:
        dly = st.session_state.daily_quotes[selected_symbol]
        fig = go.Figure(data=[go.Candlestick(
            x=[dly["trade_date"]],
            open=[dly["open"]],
            high=[dly["high"]],
            low=[dly["low"]],
            close=[dly["close"]]
        )])
        fig.update_layout(height=300, margin=dict(t=20, b=20, l=8, r=8))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("尚無盤後資料")
    if st.session_state.yahoo_debug:
        with st.expander("🧪 Yahoo 除錯面板", expanded=False):
            if st.session_state.debug_logs:
                st.dataframe(pd.DataFrame(st.session_state.debug_logs), use_container_width=True, hide_index=True)
            else:
                st.caption("尚未產生除錯資料")
    st.markdown('</div>', unsafe_allow_html=True)

with mid:
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.subheader("下單")
    with st.form("order_form", clear_on_submit=False):
        symbol = st.text_input("股票代號 / ETF 代號", value=st.session_state.selected_symbol).strip().upper()
        side = st.radio("方向", ["買入", "賣出"], horizontal=True)
        order_type = st.radio("委託", ["市價", "限價"], horizontal=True)
        ref = st.session_state.market_prices.get(symbol, {})
        default_price = ref.get("price", 0.0)
        price = st.number_input("委託價格", min_value=0.0, value=float(default_price), step=0.01)
        qty = st.number_input("數量", min_value=1, value=1000, step=1000)
        reason = st.text_area("下單理由")
        c1, c2 = st.columns(2)
        buy_btn = c1.form_submit_button("▲ 買入", use_container_width=True)
        sell_btn = c2.form_submit_button("▼ 賣出", use_container_width=True)
        if buy_btn or sell_btn:
            if not symbol:
                st.error("請輸入標的")
            elif not reason:
                st.error("請填寫理由")
            else:
                valid = (st.session_state.mode == "ETF組" and is_etf(symbol)) or (st.session_state.mode == "個股組" and not is_etf(symbol))
                if not valid:
                    st.error("標的不符合目前組別")
                else:
                    if order_type == "市價":
                        price = default_price if default_price > 0 else price
                    ok, msg, oid = save_trade("買入" if buy_btn else "賣出", symbol, float(price), int(qty), reason, order_type)
                    if ok:
                        st.success(f"{msg}，委託單號：{oid}")
                        st.rerun()
                    else:
                        st.error(msg)
    st.markdown('</div>', unsafe_allow_html=True)

with right:
    st.markdown('<div class="section-card">', unsafe_allow_html=True)
    st.subheader("風控上限設定")
    st.write(f"交易成本彙總：{fmt_money(current_positions_value())}")
    st.write(f"交易成本佔可用資金：{0 if group_cash() == 0 else current_positions_value() / group_cash():.3%}")
    st.write(f"回撤使用率：{dd:.2%} / {DRAWDOWN_LIMIT:.0%}")
    st.write(f"夏普值：{sharpe:.2f}")
    if st.button("建立今日績效快照", use_container_width=True):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO leaderboard
            (snapshot_date, group_name, cash, equity, pnl, return_pct, drawdown, capital_usage, sharpe)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().strftime("%Y-%m-%d"),
            st.session_state.mode,
            group_cash(),
            equity,
            current_total_pnl(),
            0 if INITIAL_CAPITAL == 0 else current_total_pnl() / INITIAL_CAPITAL,
            dd,
            usage,
            sharpe
        ))
        conn.commit()
        conn.close()
        st.success("已建立快照")
        st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

st.markdown("---")

# =========================================================
# Reports
# =========================================================
r1, r2 = st.columns([1.25, 1])

with r1:
    st.subheader("買賣日報表")
    if st.session_state.trades:
        st.dataframe(pd.DataFrame(st.session_state.trades), use_container_width=True, hide_index=True)
    else:
        st.info("目前無成交紀錄")

    st.subheader("績效表")
    perf_data = st.session_state.performance[:]
    perf_data.append({
        "日期": datetime.now().strftime("%m/%d"),
        "組別": st.session_state.mode,
        "投資總成本": current_positions_value(),
        "投資總市值": current_positions_value(),
        "未實現損益": current_unrealized(),
        "已實現損益": group_realized(),
        "損益合計": current_total_pnl(),
        "帳戶總淨值": equity,
        "最大回撤": dd,
        "資金使用率": usage,
        "夏普值": sharpe
    })
    st.dataframe(pd.DataFrame(perf_data), use_container_width=True, hide_index=True)

with r2:
    st.subheader("除權息預告表")
    if st.session_state.dividend_preview:
        st.dataframe(pd.DataFrame(st.session_state.dividend_preview), use_container_width=True, hide_index=True)
    else:
        st.info("目前無未來除權息資料")

    st.subheader("庫存資料")
    pos = group_positions()
    if pos:
        rows = []
        for sym, p in pos.items():
            cur = st.session_state.market_prices.get(sym, {})
            now_px = cur.get("price", p["avg_cost"])
            cost = p["avg_cost"] * p["qty"]
            value = now_px * p["qty"]
            pnl = value - cost
            rtn = 0 if cost == 0 else pnl / cost
            rows.append({
                "代號": sym,
                "類別": p.get("type", "股票"),
                "股數": p["qty"],
                "平均成本": round(p["avg_cost"], 2),
                "現價": round(now_px, 2),
                "持股成本": round(cost),
                "持股市值": round(value),
                "未實現損益": round(pnl),
                "報酬率": f"{rtn:.2%}"
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("目前無持股")

st.markdown("---")

# =========================================================
# Export + NAV
# =========================================================
ex1, ex2, ex3 = st.columns(3)
with ex1:
    conn = get_conn()
    try:
        qdf = safe_read_table(conn, "quotes_live", "quote_time")
    finally:
        conn.close()
    st.download_button("匯出商品報價 CSV", qdf.to_csv(index=False).encode("utf-8-sig"), "quotes_live.csv", use_container_width=True)
with ex2:
    conn = get_conn()
    try:
        odf = safe_read_table(conn, "orders", "created_at")
    finally:
        conn.close()
    st.download_button("匯出委託單 CSV", odf.to_csv(index=False).encode("utf-8-sig"), "orders.csv", use_container_width=True)
with ex3:
    conn = get_conn()
    try:
        fdf = safe_read_table(conn, "fills", "filled_at")
    finally:
        conn.close()
    st.download_button("匯出成交單 CSV", fdf.to_csv(index=False).encode("utf-8-sig"), "fills.csv", use_container_width=True)

st.subheader("NAV 走勢")
nav_rows = st.session_state.performance[:]
nav_rows.append({"日期": datetime.now().strftime("%m/%d"), "帳戶總淨值": equity})
nav_df = pd.DataFrame(nav_rows)
if not nav_df.empty:
    fig_nav = px.line(nav_df, x="日期", y="帳戶總淨值", markers=True)
    fig_nav.update_layout(height=260, margin=dict(t=10, b=10, l=10, r=10))
    st.plotly_chart(fig_nav, use_container_width=True)
else:
    st.caption("尚未設定交易期間")

st.markdown("---")

# =========================================================
# Bottom actions
# =========================================================
b1, b2, b3 = st.columns(3)
with b1:
    if st.button("重新整理畫面", use_container_width=True):
        st.rerun()
with b2:
    if st.button("手動更新資料", use_container_width=True):
        syms = st.session_state.watch_etf + st.session_state.watch_stock
        live_rows, daily_rows, dividend_rows, debug_logs = market_router(syms)
        if live_rows:
            st.session_state.market_prices = {
                r["symbol"]: {"price": r["last_price"], "prev_close": r["prev_close"], "source": r["market_source"], "quote_time": r["quote_time"]}
                for r in live_rows
            }
            upsert_many("quotes_live", live_rows, ["symbol", "quote_time"])
        if daily_rows:
            st.session_state.daily_quotes = {r["symbol"]: r for r in daily_rows}
            upsert_many("prices_daily", daily_rows, ["symbol", "trade_date"])
        if dividend_rows:
            st.session_state.dividend_preview = dividend_rows
            save_dividend_preview(dividend_rows)
        st.session_state.debug_logs = debug_logs
        st.session_state.last_refresh = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.success("更新完成")
        st.rerun()
with b3:
    if st.button("查看規則說明", use_container_width=True):
        st.info("ETF組 / 個股組分開記帳；買賣日報表、績效表、庫存、風控與 NAV 走勢皆為本機 SQLite 保存。")
