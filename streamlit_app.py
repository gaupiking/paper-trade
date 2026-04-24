import streamlit as st
import pandas as pd
import requests
import json
import numpy as np
from datetime import datetime, time
import plotly.express as px

# ==========================================
# 1. 系統配置與 STP 規範常數
# ==========================================
st.set_page_config(page_title="STP 法人級模擬交易平台", layout="wide")

INITIAL_CAPITAL = 200000000        # 初始資金 2 億
COST_LIMIT_PER_TICKER = 40000000   # 單一標的成本上限 4,000 萬
MIN_PORTFOLIO_COST = 20000000      # 每組持股最低成本限制 2,000 萬
FEE_RATE = 0.0004                  # 法人單手續費率 0.04%
TOTAL_LOSS_LIMIT = 20000000        # 總累積虧損上限 2,000 萬
PHASE_LOSS_LIMIT = 10000000        # 階段性虧損上限 1,000 萬

st.markdown("""
<style>
    .metric-card { background-color: #1e1e1e; border: 1px solid #333; padding: 15px; border-radius: 10px; }
    .stButton>button { width: 100%; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. 初始化 Session State
# ==========================================
if 'initialized' not in st.session_state:
    st.session_state.group = "股票投資組" 
    st.session_state.cash = INITIAL_CAPITAL
    st.session_state.realized_pnl = 0
    st.session_state.trades = []
    st.session_state.positions = {}
    st.session_state.daily_history = []
    st.session_state.market_prices = {}
    st.session_state.initialized = True

# ==========================================
# 3. 核心功能函式
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
# 4. 側邊欄與管理
# ==========================================
with st.sidebar:
    st.title("🛡️ STP 系統管理")
    new_group = st.radio("當前操作組別：", ["股票投資組", "ETF投資組"])
    if new_group != st.session_state.group:
        st.session_state.group = new_group
        st.rerun()
    
    if st.button("🔄 同步全市場收盤價"):
        st.session_state.market_prices = fetch_twse_data()
        st.success("報價已更新")
    
    st.divider()
    if st.session_state.trades:
        trade_df = pd.DataFrame(st.session_state.trades)
        st.download_button("匯出交易日報表 (CSV)", trade_df.to_csv(index=False).encode('utf-8-sig'), "trade_report.csv")

# ==========================================
# 5. 儀表板與風控偵測
# ==========================================
st.title(f"📈 STP 模擬交易平台 - {st.session_state.group}")

current_equity = st.session_state.cash + sum(
    st.session_state.market_prices.get(t, {}).get('price', p['avg_cost']) * p['quantity'] 
    for t, p in st.session_state.positions.items()
)
total_pnl = st.session_state.realized_pnl + sum(
    (st.session_state.market_prices.get(t, {}).get('price', p['avg_cost']) - p['avg_cost']) * p['quantity']
    for t, p in st.session_state.positions.items()
)
total_cost = sum(p['avg_cost'] * p['quantity'] for p in st.session_state.positions.values())

now = datetime.now()
is_phase_halt = False
if total_pnl <= -TOTAL_LOSS_LIMIT:
    st.error("🚨 警告：總虧損已達 2,000 萬，依規定強制停止交易！")
    is_phase_halt = True
elif (datetime(2026, 6, 22) <= now <= datetime(2026, 7, 31)) and total_pnl <= -PHASE_LOSS_LIMIT:
    st.error("🚨 警告：本階段虧損已達 1,000 萬，依規定須停止交易！")
    is_phase_halt = True

m1, m2, m3, m4 = st.columns(4)
m1.metric("帳戶總淨值", f"${current_equity:,.0f}")
m2.metric("總損益 (PnL)", f"${total_pnl:,.0f}", delta=f"{total_pnl:,.0f}")
m3.metric("當前持股總成本", f"${total_cost:,.0f}")
m4.metric("可用現金", f"${st.session_state.cash:,.0f}")

if 0 < total_cost < MIN_PORTFOLIO_COST:
    st.warning(f"⚠️ 風控提醒：持股成本未達 2,000 萬最低水位限制！")

# ==========================================
# 6. 交易執行模組
# ==========================================
st.divider()
t_col, l_col = st.columns([1, 2])

with t_col:
    st.subheader("📢 交易委託單")
    if is_phase_halt:
        st.error("交易功能已鎖定 (風控觸發)")
    else:
        with st.form("trade_form", clear_on_submit=True):
            ticker = st.text_input("標的代號").strip().upper()
            info = st.session_state.market_prices.get(ticker, {})
            name = info.get('name', "未知標的")
            is_etf = info.get('is_etf', False)
            ref_price = info.get('price', 0.0)
            
            st.caption(f"🔍 標的: {name} | 參考價: {ref_price}")
            price = st.number_input("成交單價", value=float(ref_price), step=0.01)
            qty = st.number_input("數量 (股)", min_value=1, step=1000)
            reason = st.text_area("交易理由 (必填)")
            
            b1, b2 = st.columns(2)
            buy = b1.form_submit_button("🟩 買進")
            sell = b2.form_submit_button("🟥 賣出")
            
            if buy or sell:
                group_valid = (st.session_state.group == "ETF投資組" and is_etf) or \
                              (st.session_state.group == "股票投資組" and not is_etf)
                
                if not reason:
                    st.error("請輸入交易理由")
                elif not group_valid:
                    st.error(f"❌ 違規：該組別不得操作此類標的")
                else:
                    exec_time = "今日收盤價" if datetime.now().time() <= time(13, 30) else "次日收盤價"
                    base = price * qty
                    fee = max(20, int(base * FEE_RATE))
                    
                    if buy:
                        net_cost = base + fee
                        curr_ticker_cost = st.session_state.positions.get(ticker, {}).get('avg_cost', 0) * st.session_state.positions.get(ticker, {}).get('quantity', 0)
                        if (curr_ticker_cost + net_cost) > COST_LIMIT_PER_TICKER:
                            st.error("❌ 違反單一標的 4,000 萬成本上限！")
                        elif net_cost > st.session_state.cash:
                            st.error("❌ 現金不足")
                        else:
                            st.session_state.cash -= net_cost
                            pos = st.session_state.positions.get(ticker, {'quantity': 0, 'avg_cost': 0})
                            new_q = pos['quantity'] + qty
                            pos['avg_cost'] = ((pos['avg_cost'] * pos['quantity']) + net_cost) / new_q
                            pos['quantity'] = new_q
                            st.session_state.positions[ticker] = pos
                            st.session_state.trades.append({"動作": "買進", "代號": ticker, "名稱": name, "價格": price, "數量": qty, "理由": reason, "計價規則": exec_time})
                            st.rerun()

                    if sell:
                        if ticker not in st.session_state.positions or st.session_state.positions[ticker]['quantity'] < qty:
                            st.error("❌ 庫存不足")
                        else:
                            tax_rate = 0.003 if not is_etf else (0.0 if ticker.endswith('B') else 0.001)
                            tax = int(base * tax_rate)
                            net_recv = base - fee - tax
                            st.session_state.cash += net_recv
                            st.session_state.realized_pnl += (net_recv - (st.session_state.positions[ticker]['avg_cost'] * qty))
                            st.session_state.positions[ticker]['quantity'] -= qty
                            if st.session_state.positions[ticker]['quantity'] == 0: del st.session_state.positions[ticker]
                            st.session_state.trades.append({"動作": "賣出", "代號": ticker, "名稱": name, "價格": price, "數量": qty, "理由": reason, "計價規則": exec_time})
                            st.rerun()

# ==========================================
# 7. 庫存與 30% 停損監控
# ==========================================
with l_col:
    t1, t2 = st.tabs(["📊 當前庫存", "📝 交易日誌"])
    with t1:
        if st.session_state.positions:
            df_p = []
            for t, p in st.session_state.positions.items():
                cur_p = st.session_state.market_prices.get(t, {}).get('price', p['avg_cost'])
                ratio = (cur_p / p['avg_cost']) - 1
                status = "🚨 強制平倉" if ratio <= -0.3 else "正常"
                df_p.append({"代號": t, "均價": round(p['avg_cost'], 2), "現價": cur_p, "報酬率": f"{ratio:.2%}", "狀態": status})
            st.dataframe(pd.DataFrame(df_p), use_container_width=True)
            if any("強制平倉" in str(x) for x in df_p):
                st.error("🚨 偵測到標的損失達 30%，依規定須於次日強制出清！")
    with t2:
        if st.session_state.trades:
            st.dataframe(pd.DataFrame(st.session_state.trades)[::-1], use_container_width=True)

if st.button("📥 執行今日結算"):
    st.session_state.daily_history.append({"date": datetime.now().strftime('%m/%d'), "equity": current_equity})
    st.success("今日淨值已記錄")
