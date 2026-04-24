import streamlit as st
import pandas as pd
import requests
import json
import numpy as np
from datetime import datetime, time
import plotly.express as px

# ==========================================
# 1. 系統常數 (依據 STP 專案提案書 1.3)
# ==========================================
st.set_page_config(page_title="STP 模擬交易平台", layout="wide")

[span_1](start_span)INITIAL_CAPITAL = 200_000_000        # 初始資金 2 億[span_1](end_span)
[span_2](start_span)COST_LIMIT_PER_TICKER = 40_000_000   # 單一標的成本上限 4,000 萬[span_2](end_span)
[span_3](start_span)MIN_PORTFOLIO_COST = 20_000_000      # 持股最低成本 2,000 萬[span_3](end_span)
[span_4](start_span)FEE_RATE = 0.0004                    # 法人手續費 0.04%[span_4](end_span)
[span_5](start_span)TOTAL_LOSS_LIMIT = 20_000_000        # 總虧損上限 2,000 萬[span_5](end_span)
[span_6](start_span)PHASE_LOSS_LIMIT = 10_000_000        # 階段虧損上限 1,000 萬[span_6](end_span)

# ==========================================
# 2. 初始化 Session State
# ==========================================
if 'initialized' not in st.session_state:
    [span_7](start_span)st.session_state.group = "股票投資組" #[span_7](end_span)
    st.session_state.cash = INITIAL_CAPITAL
    st.session_state.realized_pnl = 0
    st.session_state.trades = []
    st.session_state.positions = {}
    st.session_state.daily_history = []
    st.session_state.market_prices = {}
    st.session_state.initialized = True

# ==========================================
# 3. 資料抓取
# ==========================================
@st.cache_data(ttl=3600)
def fetch_twse_data():
    market_info = {}
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        twse = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=10).json()
        for i in twse:
            raw_p = str(i.get('ClosingPrice', '0')).replace(',', '')
            price = float(raw_p) if raw_p and raw_p not in ['-', ''] else 0.0
            market_info[i['Code']] = {'name': i['Name'], 'price': price, 'is_etf': i['Code'].startswith('00')}
        
        tpex = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", timeout=10).json()
        for i in tpex:
            raw_p = str(i.get('Close', '0')).replace(',', '')
            price = float(raw_p) if raw_p and raw_p not in ['-', ''] else 0.0
            market_info[i['SecuritiesCompanyCode']] = {'name': i['CompanyName'], 'price': price, 'is_etf': i['SecuritiesCompanyCode'].startswith('00')}
    except:
        pass
    return market_info

# ==========================================
# 4. UI 介面
# ==========================================
with st.sidebar:
    st.title("🛡️ STP 系統管理")
    [span_8](start_span)st.session_state.group = st.radio("當前組別：", ["股票投資組", "ETF投資組"]) #[span_8](end_span)
    if st.button("🔄 同步全市場報價"):
        st.session_state.market_prices = fetch_twse_data()
        st.success("報價已更新")

st.title(f"📈 STP 模擬交易平台 - {st.session_state.group}")

# 計算當前數據
current_equity = st.session_state.cash + sum(
    st.session_state.market_prices.get(t, {}).get('price', p['avg_cost']) * p['quantity'] 
    for t, p in st.session_state.positions.items()
)
total_pnl = st.session_state.realized_pnl + sum(
    (st.session_state.market_prices.get(t, {}).get('price', p['avg_cost']) - p['avg_cost']) * p['quantity']
    for t, p in st.session_state.positions.items()
)

# 顯示關鍵指標
m1, m2, m3 = st.columns(3)
m1.metric("帳戶總淨值", f"${current_equity:,.0f}")
m2.metric("總損益 (PnL)", f"${total_pnl:,.0f}", delta=f"{total_pnl:,.0f}")
m3.metric("可用現金", f"${st.session_state.cash:,.0f}")

# [span_9](start_span)風控監測：總損益與 30% 個別停損[span_9](end_span)
if total_pnl <= -TOTAL_LOSS_LIMIT:
    st.error("🚨 警告：總虧損達 2,000 萬，強制停止交易！")
elif total_pnl <= -PHASE_LOSS_LIMIT:
    st.warning("🚨 警告：階段虧損達 1,000 萬，請依規定停止交易！")

# ==========================================
# 5. [span_10](start_span)交易模組 (13:30 規則[span_10](end_span))
# ==========================================
st.divider()
t_col, l_col = st.columns([1, 2])

with t_col:
    with st.form("trade_form", clear_on_submit=True):
        ticker = st.text_input("標號 (例如 2330)").strip().upper()
        info = st.session_state.market_prices.get(ticker, {})
        st.caption(f"🔍 名稱: {info.get('name', '請輸入代號')} | 價格: {info.get('price', 0.0)}")
        
        price = st.number_input("成交單價", value=float(info.get('price', 0.0)), step=0.01)
        qty = st.number_input("數量 (股)", min_value=1, step=1000)
        [span_11](start_span)reason = st.text_area("交易理由 (必填)[span_11](end_span)")
        
        b1, b2 = st.columns(2)
        if b1.form_submit_button("🟩 買進"):
            is_etf = info.get('is_etf', False)
            [span_12](start_span)valid = (st.session_state.group == "ETF投資組" and is_etf) or (st.session_state.group == "股票投資組" and not is_etf) #[span_12](end_span)
            
            if not reason: st.error("請輸入理由")
            elif not valid: st.error("❌ 標的不符組別規範")
            else:
                cost = (price * qty) + max(20, int(price * qty * FEE_RATE))
                curr_t_cost = st.session_state.positions.get(ticker, {}).get('avg_cost', 0) * st.session_state.positions.get(ticker, {}).get('quantity', 0)
                
                [span_13](start_span)if (curr_t_cost + cost) > COST_LIMIT_PER_TICKER: #[span_13](end_span)
                    st.error("❌ 違反 4,000 萬限額")
                elif cost > st.session_state.cash: st.error("❌ 現金不足")
                else:
                    st.session_state.cash -= cost
                    pos = st.session_state.positions.get(ticker, {'quantity': 0, 'avg_cost': 0})
                    new_q = pos['quantity'] + qty
                    pos['avg_cost'] = ((pos['avg_cost'] * pos['quantity']) + cost) / new_q
                    pos['quantity'] = new_q
                    st.session_state.positions[ticker] = pos
                    st.session_state.trades.append({"動作": "買進", "代號": ticker, "價格": price, "數量": qty, "理由": reason})
                    st.rerun()

        if b2.form_submit_button("🟥 賣出"):
            if ticker in st.session_state.positions and st.session_state.positions[ticker]['quantity'] >= qty:
                # [span_14](start_span)稅率[span_14](end_span)
                tax_r = 0.003 if not info.get('is_etf') else (0.0 if ticker.endswith('B') else 0.001)
                base = price * qty
                recv = base - max(20, int(base * FEE_RATE)) - int(base * tax_r)
                st.session_state.cash += recv
                st.session_state.realized_pnl += (recv - (st.session_state.positions[ticker]['avg_cost'] * qty))
                st.session_state.positions[ticker]['quantity'] -= qty
                if st.session_state.positions[ticker]['quantity'] == 0: del st.session_state.positions[ticker]
                st.session_state.trades.append({"動作": "賣出", "代號": ticker, "價格": price, "數量": qty, "理由": reason})
                st.rerun()
            else: st.error("❌ 庫存不足")

with l_col:
    tab1, tab2 = st.tabs(["📊 庫存", "📝 日誌"])
    with tab1:
        if st.session_state.positions:
            disp = []
            for t, p in st.session_state.positions.items():
                cur_p = st.session_state.market_prices.get(t, {}).get('price', p['avg_cost'])
                ratio = (cur_p / p['avg_cost']) - 1
                [span_15](start_span)status = "🚨 30% 停損" if ratio <= -0.3 else "正常" #[span_15](end_span)
                disp.append({"標的": t, "均價": round(p['avg_cost'], 2), "報酬": f"{ratio:.2%}", "狀態": status})
            st.table(disp)
    with tab2:
        if st.session_state.trades:
            st.dataframe(pd.DataFrame(st.session_state.trades)[::-1], use_container_width=True)
