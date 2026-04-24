import streamlit as st
import pandas as pd
import requests
import json
from datetime import datetime
import plotly.express as px
import plotly.graph_objects as go

# ==========================================
# 1. 頁面配置與 CSS 樣式
# ==========================================
st.set_page_config(page_title="STP 操盤手模擬平台", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    div[data-testid="metric-container"] {
        background-color: #1e1e1e; border: 1px solid #333; padding: 15px; border-radius: 8px; border-left: 5px solid #3a86ff;
    }
    .stTextInput input, .stNumberInput input { font-size: 16px !important; }
    .help-float-btn {
        position: fixed; bottom: 30px; right: 30px; background-color: #ffb703; color: #000; width: 65px; height: 65px;
        border-radius: 50%; box-shadow: 0 4px 15px rgba(255, 183, 3, 0.5); font-size: 1.1rem; font-weight: 900;
        z-index: 9999; display: flex; justify-content: center; align-items: center; border: 3px solid #fff;
        cursor: pointer; text-decoration: none; transition: transform 0.2s ease;
    }
    .help-float-btn:hover { transform: scale(1.1); background-color: #ff9f1c; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. 系統狀態與常數 (依據 STP 提案書)
# ==========================================
INITIAL_CAPITAL = 200000000 
COST_LIMIT_PER_TICKER = 40000000 

if 'cash' not in st.session_state: st.session_state.cash = INITIAL_CAPITAL
if 'realized_pnl' not in st.session_state: st.session_state.realized_pnl = 0
if 'trades' not in st.session_state: st.session_state.trades = []
if 'positions' not in st.session_state: st.session_state.positions = {}
if 'daily_equity_history' not in st.session_state: st.session_state.daily_equity_history = []
if 'market_prices' not in st.session_state: st.session_state.market_prices = {}

# ==========================================
# 3. 核心功能
# ==========================================
@st.cache_data(ttl=600)
def fetch_market_data():
    prices = {}
    try:
        twse = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=10).json()
        tpex = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", timeout=10).json()
        for i in twse: prices[i['Code']] = float(i['ClosingPrice']) if i['ClosingPrice'] else 0
        for i in tpex: prices[i['SecuritiesCompanyCode']] = float(i['Close']) if i['Close'] else 0
        return prices
    except: return None

def get_equity():
    stock_val = sum((st.session_state.market_prices.get(t, p['avg_cost']) * p['quantity']) for t, p in st.session_state.positions.items())
    return st.session_state.cash + stock_val

@st.dialog("📈 STP 模擬交易 SOP 與風控規範")
def show_help():
    st.markdown(f"""
    #### 1. 買進限制 (Pre-trade Risk Control)
    * **單一標的上限**：持有單一標的之總成本（含本次下單）不得超過 **{COST_LIMIT_PER_TICKER/10000:,.0f} 萬元**。
    * **手續費率**：採用法人單標準 **0.04%**。
    
    #### 2. ETF 種類與稅率判定
    * **股票**：代號非 00 開頭，賣出課徵 **0.3%**。
    * **一般型 ETF**：00 開頭 (如市值型、高股息型、槓桿型等)，賣出課徵 **0.1%**。
    * **債券型 ETF**：00 開頭且 B 結尾，目前 **免徵證交稅 (0%)**。
    
    #### 3. 結算流程
    * 每日收盤後更新報價並點擊 **[📥 結算今日淨值]**。
    """)

# ==========================================
# 4. 側邊欄與管理
# ==========================================
with st.sidebar:
    st.header("⚙️ 系統管理")
    if st.button("🔄 更新全市場收盤價", use_container_width=True):
        new_prices = fetch_market_data()
        if new_prices:
            st.session_state.market_prices = new_prices
            st.success("報價更新完成"); st.rerun()
    st.divider()
    up_file = st.file_uploader("📂 載入進度 (.json)", type="json")
    if up_file:
        st.session_state.update(json.load(up_file))
        st.success("讀檔成功")
    st.download_button("💾 儲存進度檔 (JSON)", 
        data=json.dumps({k: v for k, v in st.session_state.items() if k != 'market_prices'}, ensure_ascii=False),
        file_name=f"STP_Save_{datetime.now().strftime('%m%d')}.json", use_container_width=True)

# ==========================================
# 5. 主儀表板
# ==========================================
st.title("📈 STP 青年投資行為模擬平台")

# 強制停損監測
for t, p in st.session_state.positions.items():
    cur_p = st.session_state.market_prices.get(t, p['avg_cost'])
    if p['avg_cost'] > 0 and (cur_p / p['avg_cost'] - 1) <= -0.3:
        st.error(f"🚨 強制停損警告：{t} 損失已達 30%，依規定須於次日強制出清！")

eq = get_equity()
unrealized = sum(((st.session_state.market_prices.get(t, p['avg_cost']) - p['avg_cost']) * p['quantity']) for t, p in st.session_state.positions.items())
trader_pnl = unrealized + st.session_state.realized_pnl

m1, m2, m3, m4 = st.columns(4)
m1.metric("帳戶總市值", f"${eq:,.0f}")
m2.metric("可用現金", f"${st.session_state.cash:,.0f}")
m3.metric("交易員損益 (Total)", f"${trader_pnl:,.0f}", delta=f"{trader_pnl:,.0f}")
m4.metric("已實現損益", f"${st.session_state.realized_pnl:,.0f}")

st.markdown("---")

# 圖表區
c1, c2 = st.columns([1, 2])
with c1:
    st.subheader("資產配置比例")
    # 計算各類別市值
    val_map = {"現金": st.session_state.cash, "股票": 0, "一般型 ETF": 0, "債券型 ETF": 0}
    for t, p in st.session_state.positions.items():
        val_map[p['type']] += (st.session_state.market_prices.get(t, p['avg_cost']) * p['quantity'])
    
    fig = px.pie(names=list(val_map.keys()), values=list(val_map.values()), hole=0.5, 
                 color_discrete_sequence=['#4a4e69', '#ef476f', '#06d6a0', '#118ab2'])
    fig.update_layout(height=280, margin=dict(t=0, b=0, l=0, r=0), showlegend=True, legend=dict(orientation="h", y=-0.1))
    st.plotly_chart(fig, use_container_width=True)

with c2:
    header_col, btn_col = st.columns([2, 1])
    header_col.subheader("帳戶淨值走勢")
    if btn_col.button("📥 結算今日淨值", use_container_width=True):
        today = datetime.now().strftime('%m/%d')
        st.session_state.daily_equity_history = [h for h in st.session_state.daily_equity_history if h['date'] != today]
        st.session_state.daily_equity_history.append({"date": today, "equity": eq})
        st.rerun()

    h_df = pd.DataFrame(st.session_state.daily_equity_history + [{"date": "即時", "equity": eq}])
    fig_l = px.line(h_df, x='date', y='equity', markers=True)
    fig_l.update_layout(height=280, margin=dict(t=0, b=0, l=0, r=0), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
    st.plotly_chart(fig_l, use_container_width=True)

# ==========================================
# 6. 交易執行 (分類與稅費邏輯修正)
# ==========================================
st.markdown("---")
t_col, l_col = st.columns([1, 2])

with t_col:
    st.subheader("執行交易")
    with st.form("trade_form", clear_on_submit=True):
        ticker = st.text_input("標的代號").strip().upper()
        price = st.number_input(f"成交單價 (市價參考: {st.session_state.market_prices.get(ticker, 0)})", min_value=0.0, step=0.1)
        qty = st.number_input("數量 (股)", min_value=1, step=1000, value=1000)
        note = st.text_input("日誌/理由")
        
        b1, b2 = st.columns(2)
        do_buy = b1.form_submit_button("🟩 買進", use_container_width=True)
        do_sell = b2.form_submit_button("🟥 賣出", use_container_width=True)

        if do_buy or do_sell:
            # --- 核心分類邏輯修正 ---
            if ticker.startswith('00'):
                if ticker.endswith('B'):
                    a_type, t_rate = '債券型 ETF', 0.0
                else:
                    a_type, t_rate = '一般型 ETF', 0.001
            else:
                a_type, t_rate = '股票', 0.003
            
            base = int(price * qty)
            fee = max(20, int(base * 0.0004)) # 法人費率 0.04%
            
            if do_buy:
                net_cost = base + fee
                existing_cost = st.session_state.positions.get(ticker, {}).get('avg_cost', 0) * st.session_state.positions.get(ticker, {}).get('quantity', 0)
                if (existing_cost + net_cost) > COST_LIMIT_PER_TICKER:
                    st.error(f"❌ 攔截：總成本將達 {existing_cost + net_cost:,.0f}，超過 4,000 萬上限！")
                elif net_cost > st.session_state.cash: st.error("資金不足")
                else:
                    st.session_state.cash -= net_cost
                    pos = st.session_state.positions.get(ticker, {'quantity': 0, 'avg_cost': 0, 'type': a_type})
                    new_qty = pos['quantity'] + qty
                    pos['avg_cost'] = ((pos['avg_cost'] * pos['quantity']) + net_cost) / new_qty
                    pos['quantity'] = new_qty
                    st.session_state.positions[ticker] = pos
                    st.session_state.trades.append({"time": datetime.now().strftime("%H:%M:%S"), "action": "買進", "ticker": ticker, "price": price, "qty": qty, "net": -net_cost, "note": note})
                    st.rerun()

            if do_sell:
                if ticker not in st.session_state.positions or st.session_state.positions[ticker]['quantity'] < qty: st.error("庫存不足")
                else:
                    tax = int(base * t_rate)
                    net_recv = base - fee - tax
                    st.session_state.cash += net_recv
                    st.session_state.realized_pnl += (net_recv - (st.session_state.positions[ticker]['avg_cost'] * qty))
                    st.session_state.positions[ticker]['quantity'] -= qty
                    if st.session_state.positions[ticker]['quantity'] == 0: del st.session_state.positions[ticker]
                    st.session_state.trades.append({"time": datetime.now().strftime("%H:%M:%S"), "action": "賣出", "ticker": ticker, "price": price, "qty": qty, "net": net_recv, "note": note})
                    st.rerun()

with l_col:
    t1, t2 = st.tabs(["庫存部位", "交易明細"])
    with t1:
        if st.session_state.positions:
            df_p = pd.DataFrame([{"標的": t, "類型": p['type'], "數量": p['quantity'], "均價": round(p['avg_cost'], 1), 
                                  "市價": st.session_state.market_prices.get(t, p['avg_cost']),
                                  "未實現損益": round((st.session_state.market_prices.get(t, p['avg_cost']) - p['avg_cost']) * p['quantity'])} 
                                 for t, p in st.session_state.positions.items()])
            st.dataframe(df_p, use_container_width=True, hide_index=True)
    with t2:
        if st.session_state.trades:
            st.dataframe(pd.DataFrame(st.session_state.trades)[::-1], use_container_width=True, hide_index=True)

# 圓形浮動按鈕
st.markdown('<a href="javascript:document.getElementsByClassName(\'stButton\')[3].click();" class="help-float-btn">說明</a>', unsafe_allow_html=True)
if st.button("說明", key="invisible_help", help="點擊查看 SOP"): show_help()
