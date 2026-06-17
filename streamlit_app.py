import streamlit as st
import pandas as pd
import requests
import sqlite3
import json
from pathlib import Path
from datetime import datetime, time, timedelta
import plotly.express as px
import plotly.graph_objects as go
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# =========================================================
# 基本設定
# =========================================================
st.set_page_config(
    page_title="STP 模擬交易平台 | 台股版",
    layout="wide",
    initial_sidebar_state="collapsed"
)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = str(BASE_DIR / "stp_local.db")

INITIAL_CAPITAL = 200000000
FEE_RATE = 0.0004
TAX_RATE_STOCK = 0.003
TAX_RATE_ETF = 0.001
COST_LIMIT_PER_TICKER = 40000000
MIN_PORTFOLIO_COST = 20000000
TOTAL_LOSS_LIMIT = 20000000
PHASE_LOSS_LIMIT = 10000000

TWSE_LIVE_URL = "https://openapi.twse.com.tw/api/v2/RealTimeQuote"
TWSE_DAILY_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_LIVE_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
FINMIND_BASE = "https://api.finmindtrade.com/api/v4/data"

DEFAULT_SYMBOLS = ["2330", "2317", "0050", "0056", "2603", "2881", "6121", "6208"]

# =========================================================
# Session state
# =========================================================
defaults = {
    "group": "股票投資組",
    "cash": INITIAL_CAPITAL,
    "realized_pnl": 0,
    "positions": {},
    "orders": [],
    "fills": [],
    "daily_history": [],
    "watchlist": DEFAULT_SYMBOLS[:],
    "selected_symbol": "2330",
    "market_prices": {},
    "daily_quotes": {},
    "last_refresh": None,
    "source_mode": "AUTO",
    "finmind_token": "",
    "show_five_level": True,
}

for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# =========================================================
# DB
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
    CREATE TABLE IF NOT EXISTS watchlist (
        symbol TEXT PRIMARY KEY,
        note TEXT DEFAULT ''
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS leaderboard_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_date TEXT,
        group_name TEXT,
        cash REAL,
        equity REAL,
        pnl REAL,
        return_pct REAL
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

def db_upsert_many(table, rows, pk_cols):
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
    return symbol.startswith("00") or symbol.endswith("B")

def is_twse(symbol):
    return symbol.isdigit() and len(symbol) == 4 and not symbol.startswith(("6", "8", "9"))

def request_json(url, params=None, headers=None, timeout=12):
    headers = headers or {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, params=params, headers=headers, timeout=timeout, verify=False)
    r.raise_for_status()
    return r.json()

def get_equity():
    stock_val = 0
    for sym, pos in st.session_state.positions.items():
        cur_price = st.session_state.market_prices.get(sym, {}).get("price", pos["avg_cost"])
        stock_val += cur_price * pos["qty"]
    return st.session_state.cash + stock_val

def get_unrealized_pnl():
    total = 0
    for sym, pos in st.session_state.positions.items():
        cur_price = st.session_state.market_prices.get(sym, {}).get("price", pos["avg_cost"])
        total += (cur_price - pos["avg_cost"]) * pos["qty"]
    return total

def get_pos_cost():
    return sum(pos["avg_cost"] * pos["qty"] for pos in st.session_state.positions.values())

# =========================================================
# 資料抓取
# =========================================================
@st.cache_data(ttl=15)
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
            change = last - prev if prev else 0
            pct = (change / prev * 100) if prev else 0
            rows.append({
                "symbol": sym,
                "last_price": last,
                "prev_close": prev,
                "change_val": change,
                "change_pct": pct,
                "volume": vol,
                "quote_time": datetime.now().isoformat(timespec="seconds"),
                "market_source": "TWSE",
                "raw_json": json.dumps(item, ensure_ascii=False)
            })
    return rows

@st.cache_data(ttl=15)
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
            change = last - prev if prev else 0
            pct = (change / prev * 100) if prev else 0
            rows.append({
                "symbol": sym,
                "last_price": last,
                "prev_close": prev,
                "change_val": change,
                "change_pct": pct,
                "volume": vol,
                "quote_time": datetime.now().isoformat(timespec="seconds"),
                "market_source": "TPEx",
                "raw_json": json.dumps(item, ensure_ascii=False)
            })
    return rows

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

@st.cache_data(ttl=3600)
def fetch_finmind_chip(symbols, token):
    if not token:
        return {}
    chip_map = {}
    for sym in symbols:
        try:
            params = {
                "dataset": "TaiwanStockInstitutionalInvestorsBuySell",
                "data_id": sym,
                "start_date": (datetime.now() - timedelta(days=10)).date().isoformat(),
                "end_date": datetime.now().date().isoformat(),
                "token": token
            }
            data = request_json(FINMIND_BASE, params=params, timeout=20)
            ds = data.get("data", [])
            if ds:
                chip_map[sym] = ds[-1]
        except:
            pass
    return chip_map

@st.cache_data(ttl=86400)
def fetch_mops_actions(symbols):
    today = datetime.now().date().isoformat()
    return [{
        "symbol": sym,
        "action_type": "公告",
        "announcement_date": today,
        "effective_date": today,
        "amount": None,
        "raw_json": json.dumps({"note": "MOPS 先保留資料結構，後續可接真實解析"}, ensure_ascii=False)
    } for sym in symbols]

def market_price_map(rows):
    mp = {}
    for r in rows:
        mp[r["symbol"]] = {
            "name": "",
            "price": r.get("last_price", 0.0),
            "prev_close": r.get("prev_close", 0.0),
            "source": r.get("market_source", ""),
            "quote_time": r.get("quote_time", "")
        }
    return mp

def load_market_data():
    finmind_token = st.session_state.finmind_token.strip()
    symbols = st.session_state.watchlist

    live_rows = fetch_twse_live(symbols)
    if not live_rows:
        live_rows = fetch_tpex_live(symbols)

    daily_rows = fetch_finmind_daily(symbols, finmind_token)
    chip_map = fetch_finmind_chip(symbols, finmind_token)
    action_rows = fetch_mops_actions(symbols)

    if live_rows:
        st.session_state.market_prices = market_price_map(live_rows)
        db_upsert_many("quotes_live", live_rows, ["symbol", "quote_time"])

    if daily_rows:
        for r in daily_rows:
            if r["symbol"] in chip_map:
                r["chip_json"] = json.dumps(chip_map[r["symbol"]], ensure_ascii=False)
        st.session_state.daily_quotes = {r["symbol"]: r for r in daily_rows}
        db_upsert_many("prices_daily", daily_rows, ["symbol", "trade_date"])

    if action_rows:
        db_upsert_many("corporate_actions", action_rows, ["symbol", "action_type", "announcement_date", "effective_date", "amount"])

    st.session_state.last_refresh = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return len(live_rows), len(daily_rows), len(action_rows)

# =========================================================
# 初始化
# =========================================================
init_db()
if not st.session_state.market_prices:
    load_market_data()

# =========================================================
# 側邊欄
# =========================================================
with st.sidebar:
    st.title("台股模擬交易")
    st.caption("simTrade 風格 V1")

    new_group = st.radio("交易組別", ["股票投資組", "ETF投資組"])
    if new_group != st.session_state.group:
        st.session_state.group = new_group
        st.rerun()

    st.session_state.source_mode = st.selectbox("資料來源模式", ["AUTO", "TWSE", "TPEx"], index=["AUTO", "TWSE", "TPEx"].index(st.session_state.source_mode))
    st.session_state.finmind_token = st.text_input("FinMind Token", type="password", value=st.session_state.finmind_token)

    watchlist_text = st.text_input("自選股代號", ",".join(st.session_state.watchlist))
    watchlist_list = [x.strip().upper() for x in watchlist_text.split(",") if x.strip()]
    if watchlist_list:
        st.session_state.watchlist = watchlist_list

    if st.button("更新報價", use_container_width=True):
        load_market_data()
        st.success("更新完成")
        st.rerun()

    st.divider()
    st.subheader("本機資料庫")
    conn = get_conn()
    try:
        qdf = safe_read_table(conn, "quotes_live", "quote_time")
        ddf = safe_read_table(conn, "prices_daily", "trade_date")
        odf = safe_read_table(conn, "orders", "created_at")
        fdf = safe_read_table(conn, "fills", "filled_at")
    finally:
        conn.close()

    st.download_button("匯出即時報價", qdf.to_csv(index=False).encode("utf-8-sig"), "quotes_live.csv", use_container_width=True)
    st.download_button("匯出盤後資料", ddf.to_csv(index=False).encode("utf-8-sig"), "prices_daily.csv", use_container_width=True)
    st.download_button("匯出委託單", odf.to_csv(index=False).encode("utf-8-sig"), "orders.csv", use_container_width=True)
    st.download_button("匯出成交單", fdf.to_csv(index=False).encode("utf-8-sig"), "fills.csv", use_container_width=True)

    st.divider()
    up_file = st.file_uploader("載入存檔 JSON", type="json")
    if up_file:
        data = json.load(up_file)
        for k, v in data.items():
            if k in st.session_state:
                st.session_state[k] = v
        st.success("讀檔成功")

    st.download_button(
        "儲存存檔 JSON",
        data=json.dumps({k: v for k, v in st.session_state.items() if k not in ["market_prices", "daily_quotes"]}, ensure_ascii=False),
        file_name=f"STP_Save_{datetime.now().strftime('%m%d')}.json",
        use_container_width=True
    )

# =========================================================
# 標題與摘要
# =========================================================
st.title(f"📈 STP 模擬交易平台｜{st.session_state.group}")

equity = get_equity()
unrealized = get_unrealized_pnl()
total_pnl = unrealized + st.session_state.realized_pnl
total_cost = get_pos_cost()

is_halted = False
if total_pnl <= -TOTAL_LOSS_LIMIT:
    st.error("🚨 總虧損已達兩千萬上限，系統暫停交易")
    is_halted = True
elif total_pnl <= -PHASE_LOSS_LIMIT:
    st.warning("🚨 階段虧損已達一千萬，系統暫停交易")
    is_halted = True

m1, m2, m3, m4 = st.columns(4)
m1.metric("帳戶總淨值", f"${equity:,.0f}")
m2.metric("可用現金", f"${st.session_state.cash:,.0f}")
m3.metric("總損益", f"${total_pnl:,.0f}")
m4.metric("持股成本", f"${total_cost:,.0f}")

if 0 < total_cost < MIN_PORTFOLIO_COST:
    st.warning("⚠️ 持股總成本低於兩千萬門檻")

if st.session_state.last_refresh:
    st.caption(f"最後更新時間：{st.session_state.last_refresh}")

st.markdown("---")

# =========================================================
# 圖表區
# =========================================================
left, right = st.columns([1, 2])

with left:
    st.subheader("資產配置")
    asset_map = {"現金": st.session_state.cash, "股票": 0, "ETF": 0}
    for sym, pos in st.session_state.positions.items():
        cur_price = st.session_state.market_prices.get(sym, {}).get("price", pos["avg_cost"])
        key = "ETF" if is_etf(sym) else "股票"
        asset_map[key] += cur_price * pos["qty"]

    pie = px.pie(names=list(asset_map.keys()), values=list(asset_map.values()), hole=0.45)
    pie.update_layout(height=300, margin=dict(t=10, b=10, l=10, r=10))
    st.plotly_chart(pie, use_container_width=True)

with right:
    st.subheader("淨值走勢")
    if st.button("記錄今日績效", use_container_width=True):
        today = datetime.now().strftime("%m/%d")
        st.session_state.daily_history = [x for x in st.session_state.daily_history if x["日期"] != today]
        st.session_state.daily_history.append({
            "日期": today,
            "投資總成本": total_cost,
            "投資總市值": equity - st.session_state.cash,
            "未實現損益": unrealized,
            "已實現損益": st.session_state.realized_pnl,
            "損益合計": total_pnl,
            "帳戶總淨值": equity
        })
        st.success("已記錄")
        st.rerun()

    if st.session_state.daily_history:
        hd = pd.DataFrame(st.session_state.daily_history)
        line = px.line(hd, x="日期", y="帳戶總淨值", markers=True)
        line.update_layout(height=280, margin=dict(t=10, b=10, l=10, r=10))
        st.plotly_chart(line, use_container_width=True)

st.markdown("---")

# =========================================================
# 主要介面
# =========================================================
tab1, tab2, tab3, tab4 = st.tabs(["交易終端", "持股與委託", "交易紀錄", "排行榜"])

# ---------------------------------------------------------
# 交易終端
# ---------------------------------------------------------
with tab1:
    col_a, col_b = st.columns([1, 2])

    with col_a:
        st.subheader("下單區")
        if is_halted:
            st.error("系統目前暫停交易")
        else:
            with st.form("order_form", clear_on_submit=True):
                symbol = st.text_input("股票代號").strip().upper()
                info = st.session_state.market_prices.get(symbol, {})
                ref_price = info.get("price", 0.0)

                st.caption(f"參考價：{ref_price}")
                side = st.radio("買賣方向", ["買進", "賣出"], horizontal=True)
                order_type = st.radio("委託方式", ["限價單", "市價單"], horizontal=True)
                price = st.number_input("委託價格", min_value=0.0, value=float(ref_price), step=0.01)
                qty = st.number_input("股數", min_value=1, value=1000, step=1000)
                reason = st.text_area("交易理由")
                submit = st.form_submit_button("送出委託", use_container_width=True)

                if submit:
                    if not symbol:
                        st.error("請輸入股票代號")
                    elif not reason:
                        st.error("請填寫交易理由")
                    else:
                        is_valid_group = (st.session_state.group == "ETF投資組" and is_etf(symbol)) or (st.session_state.group == "股票投資組" and not is_etf(symbol))
                        if not is_valid_group:
                            st.error("標的不符合目前組別")
                        else:
                            if order_type == "市價單":
                                price = ref_price if ref_price > 0 else price
                            order = {
                                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "symbol": symbol,
                                "symbol_name": symbol,
                                "side": side,
                                "order_type": order_type,
                                "price": price,
                                "qty": int(qty),
                                "status": "已送出",
                                "reason": reason,
                                "filled_qty": 0,
                                "remaining_qty": int(qty),
                                "avg_fill_price": 0.0
                            }
                            conn = get_conn()
                            cur = conn.cursor()
                            cur.execute("""
                                INSERT INTO orders (created_at, symbol, symbol_name, side, order_type, price, qty, status, reason, filled_qty, remaining_qty, avg_fill_price)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                order["created_at"], order["symbol"], order["symbol_name"], order["side"], order["order_type"],
                                order["price"], order["qty"], order["status"], order["reason"], 0, int(qty), 0.0
                            ))
                            order_id = cur.lastrowid
                            conn.commit()
                            conn.close()

                            st.session_state.orders.append({**order, "order_id": order_id})
                            st.success(f"委託已送出，委託單號：{order_id}")

    with col_b:
        st.subheader("五檔 / 圖表 / 即時報價")
        if st.session_state.watchlist:
            selected = st.selectbox("選擇標的", st.session_state.watchlist, index=max(0, st.session_state.watchlist.index(st.session_state.selected_symbol)) if st.session_state.selected_symbol in st.session_state.watchlist else 0)
            st.session_state.selected_symbol = selected
        else:
            selected = st.text_input("選擇標的", st.session_state.selected_symbol).strip().upper()
            st.session_state.selected_symbol = selected

        q = st.session_state.market_prices.get(selected, {})
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("現價", f"{q.get('price', 0):,.2f}")
        c2.metric("漲跌", f"{q.get('price', 0) - q.get('prev_close', 0):,.2f}")
        c3.metric("漲跌幅", f"{((q.get('price', 0) - q.get('prev_close', 0)) / q.get('prev_close', 1) * 100):,.2f}%")
        c4.metric("來源", q.get("source", "-"))

        if selected in st.session_state.daily_quotes:
            d = st.session_state.daily_quotes[selected]
            fig = go.Figure()
            fig.add_trace(go.Candlestick(
                x=[d["trade_date"]],
                open=[d["open"]],
                high=[d["high"]],
                low=[d["low"]],
                close=[d["close"]],
                name="日K"
            ))
            fig.update_layout(height=420, margin=dict(t=20, b=20, l=10, r=10))
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("尚無盤後資料，先更新資料或輸入 FinMind Token")

        if st.session_state.show_five_level:
            st.subheader("五檔模擬")
            bid_prices = []
            ask_prices = []
            base = q.get("price", 0) or 100
            for i in range(5):
                bid_prices.append(round(base * (1 - 0.001 * (i + 1)), 2))
                ask_prices.append(round(base * (1 + 0.001 * (i + 1)), 2))
            five_df = pd.DataFrame({
                "買價": bid_prices,
                "買量": [1000 * (5 - i) for i in range(5)],
                "賣價": ask_prices,
                "賣量": [1000 * (i + 1) for i in range(5)],
            })
            st.dataframe(five_df, use_container_width=True, hide_index=True)

# ---------------------------------------------------------
# 持股與委託
# ---------------------------------------------------------
with tab2:
    pcol, ocol = st.columns([1.2, 1])

    with pcol:
        st.subheader("持股庫存")
        if st.session_state.positions:
            rows = []
            for sym, pos in st.session_state.positions.items():
                cur = st.session_state.market_prices.get(sym, {})
                cur_price = cur.get("price", pos["avg_cost"])
                cost = pos["avg_cost"] * pos["qty"]
                value = cur_price * pos["qty"]
                pnl = value - cost
                rtn = (cur_price / pos["avg_cost"] - 1) if pos["avg_cost"] > 0 else 0
                rows.append({
                    "股票代號": sym,
                    "股票名稱": cur.get("name", ""),
                    "持股股數": pos["qty"],
                    "平均成本": round(pos["avg_cost"], 2),
                    "現價": round(cur_price, 2),
                    "持股成本": round(cost),
                    "持股市值": round(value),
                    "未實現損益": round(pnl),
                    "報酬率": f"{rtn:.2%}"
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("目前沒有持股")

    with ocol:
        st.subheader("委託單")
        conn = get_conn()
        try:
            odf = safe_read_table(conn, "orders", "created_at")
        finally:
            conn.close()

        if not odf.empty:
            st.dataframe(odf, use_container_width=True, hide_index=True)
        else:
            st.info("目前沒有委託單")

# ---------------------------------------------------------
# 交易紀錄
# ---------------------------------------------------------
with tab3:
    st.subheader("成交紀錄")
    conn = get_conn()
    try:
        fdf = safe_read_table(conn, "fills", "filled_at")
    finally:
        conn.close()

    if not fdf.empty:
        st.dataframe(fdf, use_container_width=True, hide_index=True)
    else:
        st.info("目前沒有成交紀錄")

    if st.session_state.daily_history:
        st.subheader("每日績效")
        hist_df = pd.DataFrame(st.session_state.daily_history)
        st.dataframe(hist_df, use_container_width=True, hide_index=True)

# ---------------------------------------------------------
# 排行榜
# ---------------------------------------------------------
with tab4:
    st.subheader("績效排行榜")
    conn = get_conn()
    try:
        ldf = safe_read_table(conn, "leaderboard_snapshots", "snapshot_date")
    finally:
        conn.close()

    if not ldf.empty:
        st.dataframe(ldf, use_container_width=True, hide_index=True)
    else:
        st.info("尚未建立排行榜資料")

    if st.button("建立今日績效快照", use_container_width=True):
        eq = get_equity()
        pnl = eq - INITIAL_CAPITAL
        ret = pnl / INITIAL_CAPITAL
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO leaderboard_snapshots (snapshot_date, group_name, cash, equity, pnl, return_pct)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            datetime.now().strftime("%Y-%m-%d"),
            st.session_state.group,
            st.session_state.cash,
            eq,
            pnl,
            ret
        ))
        conn.commit()
        conn.close()
        st.success("已建立快照")
        st.rerun()

# =========================================================
# 底部功能
# =========================================================
st.markdown("---")
a, b, c = st.columns(3)

with a:
    if st.button("重新整理畫面", use_container_width=True):
        st.rerun()

with b:
    if st.button("手動更新資料", use_container_width=True):
        load_market_data()
        st.success("更新完成")
        st.rerun()

with c:
    if st.button("清空示範資料", use_container_width=True):
        st.session_state.positions = {}
        st.session_state.orders = []
        st.session_state.fills = []
        st.session_state.daily_history = []
        st.session_state.realized_pnl = 0
        st.session_state.cash = INITIAL_CAPITAL
        st.success("已清空")
        st.rerun()
