import streamlit as st
import pandas as pd
import requests
import sqlite3
import json
import time as time_module
from pathlib import Path
from datetime import datetime, time, timedelta
import plotly.express as px
import plotly.graph_objects as go
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

st.set_page_config(
    page_title="STP 操盤模擬平台 | Royal Life",
    layout="wide",
    initial_sidebar_state="collapsed"
)

INITIAL_CAPITAL = 200000000
COST_LIMIT_PER_TICKER = 40000000
MIN_PORTFOLIO_COST = 20000000
FEE_RATE = 0.0004
TOTAL_LOSS_LIMIT = 20000000
PHASE_LOSS_LIMIT = 10000000

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = str(BASE_DIR / "stp_local.db")

TWSE_LIVE_URL = "https://openapi.twse.com.tw/api/v2/RealTimeQuote"
TWSE_DAILY_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_LIVE_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
TPEX_DAILY_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
FINMIND_BASE = "https://api.finmindtrade.com/api/v4/data"
DEFAULT_SYMBOLS = ["2330", "2317", "0050", "0056", "2603", "2881", "6121", "6208"]

defaults = {
    "group": "股票投資組",
    "cash": INITIAL_CAPITAL,
    "realized_pnl": 0,
    "trades": [],
    "positions": {},
    "daily_equity_history": [],
    "market_prices": {},
    "live_quotes_cache": {},
    "daily_quotes_cache": {},
    "actions_cache": {},
    "chip_cache": {},
    "last_refresh": None,
    "selected_symbols": DEFAULT_SYMBOLS[:],
    "source_mode": "AUTO",
}

for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

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
    CREATE TABLE IF NOT EXISTS corporate_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        action_type TEXT,
        announcement_date TEXT,
        effective_date TEXT,
        amount REAL,
        raw_json TEXT,
        UNIQUE(symbol, action_type, announcement_date, effective_date, amount)
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

def is_tpex(symbol):
    return symbol.isdigit() and (symbol.startswith("6") or symbol.startswith("8") or symbol.startswith("9"))

def request_json(url, params=None, headers=None, timeout=12):
    headers = headers or {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, params=params, headers=headers, timeout=timeout, verify=False)
    r.raise_for_status()
    return r.json()

@st.cache_data(ttl=15)
def fetch_twse_live(symbols):
    try:
        data = request_json(TWSE_LIVE_URL, timeout=15)
    except:
        return []
    rows = []
    symbol_set = set(symbols)
    if isinstance(data, list):
        for item in data:
            sym = str(item.get("Code", "")).strip()
            if sym not in symbol_set:
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
                "raw_json": json.dumps(item, ensure_ascii=False),
            })
    return rows

@st.cache_data(ttl=15)
def fetch_tpex_live(symbols):
    try:
        data = request_json(TPEX_LIVE_URL, timeout=15)
    except:
        return []
    rows = []
    symbol_set = set(symbols)
    if isinstance(data, list):
        for item in data:
            sym = str(item.get("SecuritiesCompanyCode", "")).strip()
            if sym not in symbol_set:
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
                "raw_json": json.dumps(item, ensure_ascii=False),
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
                "token": token,
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
                "raw_json": json.dumps(latest, ensure_ascii=False),
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
                "token": token,
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
        "action_type": "INFO",
        "announcement_date": today,
        "effective_date": today,
        "amount": None,
        "raw_json": json.dumps({"note": "MOPS placeholder for local test"}, ensure_ascii=False),
    } for sym in symbols]

def market_data_router(symbols, finmind_token="", source_mode="AUTO"):
    live_rows = []
    if source_mode == "AUTO":
        live_rows = fetch_twse_live(symbols)
        if not live_rows:
            live_rows = fetch_tpex_live(symbols)
    elif source_mode == "TWSE":
        live_rows = fetch_twse_live(symbols)
    elif source_mode == "TPEx":
        live_rows = fetch_tpex_live(symbols)

    daily_rows = fetch_finmind_daily(symbols, finmind_token)
    chip_map = fetch_finmind_chip(symbols, finmind_token)
    action_rows = fetch_mops_actions(symbols)
    return live_rows, daily_rows, action_rows, chip_map

def market_price_map(rows):
    mp = {}
    for r in rows:
        mp[r["symbol"]] = {
            "name": "",
            "price": r.get("last_price", 0.0),
            "prev_close": r.get("prev_close", 0.0),
            "source": r.get("market_source", ""),
            "quote_time": r.get("quote_time", ""),
        }
    return mp

def get_equity():
    stock_val = 0
    for t, p in st.session_state.positions.items():
        cur_p = st.session_state.market_prices.get(t, {}).get("price", p["avg_cost"])
        stock_val += cur_p * p["quantity"]
    return st.session_state.cash + stock_val

def calc_unrealized():
    total = 0
    for t, p in st.session_state.positions.items():
        cur_p = st.session_state.market_prices.get(t, {}).get("price", p["avg_cost"])
        total += (cur_p - p["avg_cost"]) * p["quantity"]
    return total

def load_market_data():
    finmind_token = st.session_state.get("finmind_token", "")
    live_rows, daily_rows, action_rows, chip_map = market_data_router(
        st.session_state.selected_symbols,
        finmind_token=finmind_token,
        source_mode=st.session_state.source_mode
    )

    if live_rows:
        st.session_state.market_prices = market_price_map(live_rows)
        st.session_state.live_quotes_cache = {r["symbol"]: r for r in live_rows}
        db_upsert_many("quotes_live", live_rows, ["symbol", "quote_time"])

    if daily_rows:
        for r in daily_rows:
            if r["symbol"] in chip_map:
                r["chip_json"] = json.dumps(chip_map[r["symbol"]], ensure_ascii=False)
        st.session_state.daily_quotes_cache = {r["symbol"]: r for r in daily_rows}
        db_upsert_many("prices_daily", daily_rows, ["symbol", "trade_date"])

    if action_rows:
        st.session_state.actions_cache = {r["symbol"]: r for r in action_rows}
        db_upsert_many("corporate_actions", action_rows, ["symbol", "action_type", "announcement_date", "effective_date", "amount"])

    st.session_state.last_refresh = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return len(live_rows), len(daily_rows), len(action_rows)

def show_help_dialog():
    st.markdown("""
#### 1. 買進與持股限制
* 單一標的總成本上限：**4,000 萬元**。
* 持股最低成本限制：**2,000 萬元**。

#### 2. 成交價計價規則
* **13:30 前**下單：以即時價或當日收盤參考價。
* **13:30 後**下單：以次日盤後定版價。

#### 3. 階段性停損規範
* 總累積虧損達兩千萬，或階段性虧損達一千萬，強制停止交易。
* 單一標的損失達 **30%**，須於次日強制出清。
""")

init_db()

if "finmind_token" not in st.session_state:
    st.session_state.finmind_token = ""

with st.sidebar:
    st.header("⚙️ 系統管理")

    new_group = st.radio("當前操作組別：", ["股票投資組", "ETF投資組"])
    if new_group != st.session_state.group:
        st.session_state.group = new_group
        st.rerun()

    st.session_state.source_mode = st.selectbox("資料源模式", ["AUTO", "TWSE", "TPEx"], index=["AUTO", "TWSE", "TPEx"].index(st.session_state.source_mode))

    st.session_state.finmind_token = st.text_input("FinMind Token", type="password", value=st.session_state.finmind_token)

    symbols_text = st.text_input("追蹤標的（逗號分隔）", ",".join(st.session_state.selected_symbols))
    parsed_symbols = [s.strip().upper() for s in symbols_text.split(",") if s.strip()]
    if parsed_symbols:
        st.session_state.selected_symbols = parsed_symbols

    if st.button("🔄 更新資料", use_container_width=True):
        load_market_data()
        st.success("更新完成")
        st.rerun()

    st.divider()
    st.subheader("📥 匯出資料")

    conn = get_conn()
    try:
        qdf = safe_read_table(conn, "quotes_live", "quote_time")
        ddf = safe_read_table(conn, "prices_daily", "trade_date")
        adf = safe_read_table(conn, "corporate_actions", "announcement_date")
    finally:
        conn.close()

    st.download_button(
        "匯出 即時報價 CSV",
        data=qdf.to_csv(index=False).encode("utf-8-sig"),
        file_name="quotes_live.csv",
        use_container_width=True
    )
    st.download_button(
        "匯出 盤後資料 CSV",
        data=ddf.to_csv(index=False).encode("utf-8-sig"),
        file_name="prices_daily.csv",
        use_container_width=True
    )
    st.download_button(
        "匯出 公司行動 CSV",
        data=adf.to_csv(index=False).encode("utf-8-sig"),
        file_name="corporate_actions.csv",
        use_container_width=True
    )

    st.divider()
    up_file = st.file_uploader("📂 載入進度 (.json)", type="json")
    if up_file:
        data = json.load(up_file)
        for k, v in data.items():
            if k in st.session_state:
                st.session_state[k] = v
        st.success("讀檔成功")

    st.download_button(
        "💾 儲存進度檔 (JSON)",
        data=json.dumps({k: v for k, v in st.session_state.items() if k not in ["market_prices", "live_quotes_cache", "daily_quotes_cache", "actions_cache"]}, ensure_ascii=False),
        file_name=f"STP_Save_{datetime.now().strftime('%m%d')}.json",
        use_container_width=True
    )

if not st.session_state.market_prices:
    load_market_data()

st.title(f"📈 STP 模擬交易平台 - {st.session_state.group}")

eq = get_equity()
unrealized = calc_unrealized()
total_pnl = unrealized + st.session_state.realized_pnl
total_cost = sum(p["avg_cost"] * p["quantity"] for p in st.session_state.positions.values())

is_halted = False
if total_pnl <= -TOTAL_LOSS_LIMIT:
    st.error("🚨 警告：總虧損已達兩千萬上限，依規定強制停止交易！")
    is_halted = True
elif total_pnl <= -PHASE_LOSS_LIMIT:
    st.warning("🚨 警告：階段虧損已達一千萬，系統鎖定並暫停交易！")
    is_halted = True

m1, m2, m3, m4 = st.columns(4)
m1.metric("帳戶總淨值", f"${eq:,.0f}")
m2.metric("可用現金", f"${st.session_state.cash:,.0f}")
m3.metric("總損益合計數", f"${total_pnl:,.0f}")
m4.metric("投資組合總成本", f"${total_cost:,.0f}")

if 0 < total_cost < MIN_PORTFOLIO_COST:
    st.warning("⚠️ 提醒：持股總成本目前低於規範之兩千萬水位。")

if st.session_state.last_refresh:
    st.caption(f"最後更新時間：{st.session_state.last_refresh}")

st.markdown("---")

c1, c2 = st.columns([1, 2])

with c1:
    st.subheader("資產配置")
    val_map = {"現金": st.session_state.cash, "股票": 0, "一般型 ETF": 0, "債券型 ETF": 0}
    for t, p in st.session_state.positions.items():
        cur_p = st.session_state.market_prices.get(t, {}).get("price", p["avg_cost"])
        kind = p.get("type", "股票")
        if kind not in val_map:
            val_map[kind] = 0
        val_map[kind] += cur_p * p["quantity"]
    fig = px.pie(names=list(val_map.keys()), values=list(val_map.values()), hole=0.5)
    fig.update_layout(height=280, margin=dict(t=10, b=10, l=10, r=10))
    st.plotly_chart(fig, use_container_width=True)

with c2:
    st.subheader("淨值紀錄走勢")
    if st.button("📥 結算今日交易績效總表", use_container_width=True):
        today = datetime.now().strftime("%m/%d")
        st.session_state.daily_equity_history = [h for h in st.session_state.daily_equity_history if h["日期"] != today]
        st.session_state.daily_equity_history.append({
            "日期": today,
            "投資總成本": total_cost,
            "投資總市值": eq - st.session_state.cash,
            "未實現損益": unrealized,
            "已實現損益": st.session_state.realized_pnl,
            "損益合計數": total_pnl,
            "帳戶總淨值": eq
        })
        st.success("已記錄今日績效！")
        st.rerun()

    if st.session_state.daily_equity_history:
        h_df = pd.DataFrame(st.session_state.daily_equity_history)
        fig_l = px.line(h_df, x="日期", y="帳戶總淨值", markers=True, template="plotly_dark")
        fig_l.update_layout(height=250, margin=dict(t=10, b=10, l=10, r=10))
        st.plotly_chart(fig_l, use_container_width=True)

st.markdown("---")

t_col, l_col = st.columns([1, 2])

with t_col:
    st.subheader("執行下單委託")
    if is_halted:
        st.error("系統交易權限已暫鎖")
    else:
        with st.form("trade_form", clear_on_submit=True):
            ticker = st.text_input("標的代號").strip().upper()
            info = st.session_state.market_prices.get(ticker, {})
            s_name = info.get("name", "請輸入代號查詢")
            ref_price = info.get("price", 0.0)
            is_etf_flag = is_etf(ticker)

            st.caption(f"🔍 標的: {ticker} | 參考價: {ref_price}")

            price = st.number_input("成交價格", min_value=0.0, value=float(ref_price), step=0.01)
            qty = st.number_input("數量 (股)", min_value=1, step=1000, value=1000)
            reason = st.text_area("買進/賣出理由 (會記錄到日報表)")

            b1, b2 = st.columns(2)
            buy_btn = b1.form_submit_button("🟩 買進", use_container_width=True)
            sell_btn = b2.form_submit_button("🟥 賣出", use_container_width=True)

            if buy_btn or sell_btn:
                group_valid = (st.session_state.group == "ETF投資組" and is_etf_flag) or (st.session_state.group == "股票投資組" and not is_etf_flag)
                if not ticker or price <= 0:
                    st.error("請確認代號與單價")
                elif not reason:
                    st.error("❌ 依規範必須填寫交易理由！")
                elif not group_valid:
                    st.error("❌ 標的不符組別規範！")
                else:
                    action = "買進" if buy_btn else "賣出"
                    exec_rule = "今日收盤價" if datetime.now().time() <= time(13, 30) else "次日收盤價"
                    asset_type = "債券型 ETF" if is_etf_flag and ticker.endswith("B") else ("一般型 ETF" if is_etf_flag else "股票")
                    tax_rate = 0.0 if is_etf_flag and ticker.endswith("B") else (0.001 if is_etf_flag else 0.003)
                    base = int(price * qty)
                    fee = max(20, int(base * FEE_RATE))

                    if buy_btn:
                        net_cost = base + fee
                        cur_pos = st.session_state.positions.get(ticker, {"quantity": 0, "avg_cost": 0, "type": asset_type})
                        cur_total_cost = cur_pos["avg_cost"] * cur_pos["quantity"]
                        if (cur_total_cost + net_cost) > COST_LIMIT_PER_TICKER:
                            st.error("❌ 違反單一標的四千萬限額！")
                        elif net_cost > st.session_state.cash:
                            st.error("❌ 現金不足")
                        else:
                            st.session_state.cash -= net_cost
                            new_q = cur_pos["quantity"] + qty
                            cur_pos["avg_cost"] = ((cur_pos["avg_cost"] * cur_pos["quantity"]) + net_cost) / new_q
                            cur_pos["quantity"] = new_q
                            cur_pos["type"] = asset_type
                            st.session_state.positions[ticker] = cur_pos
                            st.session_state.trades.append({
                                "交易日期": datetime.now().strftime("%Y-%m-%d %H:%M"),
                                "交易標的": f"{ticker} {s_name}",
                                "買賣方向": action,
                                "成交價格": price,
                                "數量": qty,
                                "理由與日誌": reason,
                                "計價規則": exec_rule
                            })
                            st.success("買進完成")
                            st.rerun()

                    if sell_btn:
                        if ticker not in st.session_state.positions or st.session_state.positions[ticker]["quantity"] < qty:
                            st.error("❌ 庫存不足")
                        else:
                            tax = int(base * tax_rate)
                            net_recv = base - fee - tax
                            avg_cost = st.session_state.positions[ticker]["avg_cost"]
                            st.session_state.cash += net_recv
                            st.session_state.realized_pnl += (net_recv - (avg_cost * qty))
                            st.session_state.positions[ticker]["quantity"] -= qty
                            if st.session_state.positions[ticker]["quantity"] == 0:
                                del st.session_state.positions[ticker]
                            st.session_state.trades.append({
                                "交易日期": datetime.now().strftime("%Y-%m-%d %H:%M"),
                                "交易標的": f"{ticker} {s_name}",
                                "買賣方向": action,
                                "成交價格": price,
                                "數量": qty,
                                "理由與日誌": reason,
                                "計價規則": exec_rule
                            })
                            st.success("賣出完成")
                            st.rerun()

with l_col:
    tab1, tab2, tab3 = st.tabs(["📊 即時部位", "📝 交易日誌", "🗃️ 盤後資料"])

    with tab1:
        if st.session_state.positions:
            disp_p = []
            for t, p in st.session_state.positions.items():
                cur_p = st.session_state.market_prices.get(t, {}).get("price", p["avg_cost"])
                cost_basis = p["avg_cost"] * p["quantity"]
                mkt_value = cur_p * p["quantity"]
                un_pnl = mkt_value - cost_basis
                ratio = (cur_p / p["avg_cost"]) - 1 if p["avg_cost"] > 0 else 0
                status = "🚨 30%強制停損" if ratio <= -0.3 else "正常"
                disp_p.append({
                    "交易標的": f"{t}",
                    "標的名稱": st.session_state.market_prices.get(t, {}).get("name", ""),
                    "成本": round(cost_basis),
                    "市值": round(mkt_value),
                    "未實現損益": round(un_pnl),
                    "報酬率": f"{ratio:.2%}",
                    "狀態": status
                })
            st.dataframe(pd.DataFrame(disp_p), use_container_width=True, hide_index=True)
        else:
            st.info("目前沒有持股")

    with tab2:
        if st.session_state.trades:
            st.dataframe(pd.DataFrame(st.session_state.trades)[::-1], use_container_width=True, hide_index=True)
        else:
            st.info("目前沒有交易紀錄")

    with tab3:
        if st.session_state.daily_quotes_cache:
            st.dataframe(pd.DataFrame(list(st.session_state.daily_quotes_cache.values())), use_container_width=True, hide_index=True)
        else:
            st.info("尚未載入盤後資料")

col_a, col_b, col_c = st.columns(3)
with col_a:
    if st.button("🔁 重新整理畫面", use_container_width=True):
        st.rerun()
with col_b:
    if st.button("📥 手動更新資料", use_container_width=True):
        load_market_data()
        st.success("更新完成")
        st.rerun()
with col_c:
    if st.button("📘 查看規則", use_container_width=True):
        show_help_dialog()
