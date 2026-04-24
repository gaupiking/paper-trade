import streamlit as st
import pandas as pd
import requests
import json
from datetime import datetime
import plotly.express as px

# ==========================================
# 1. 頁面配置與專業 UI 樣式
# ==========================================
st.set_page_config(page_title="STP 操盤模擬平台 | Royal Life", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    
    /* 數據卡片美化 */
    div[data-testid="metric-container"] {
        background-color: #1e1e1e; border: 1px solid #333; padding: 15px; border-radius: 10px; border-left: 5px solid #ffb703;
    }
    
    /* 輸入框強化 */
    .stTextInput input, .stNumberInput input { font-size: 16px !important; }

    /* 金黃色浮動說明按鈕 */
    .help-float-btn {
        position: fixed; bottom: 30px; right: 30px; background-color: #ffb703; color: #000; width: 60px; height: 60px;
        border-radius: 50%; box-shadow: 0 4px 15px rgba(255, 183, 3, 0.6); font-size: 1rem; font-weight: 900;
        z-index: 9999; display: flex; justify-content: center; align-items: center; border: 3px solid #fff;
        cursor: pointer; text-decoration: none; transition: transform 0.2s ease;
    }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. 系統常數與狀態初始化
# ==========================================
INITIAL_CAPITAL = 200000000       # 初始資金 2 億
COST_LIMIT_PER_TICKER = 40000000  # 單一標的成本上限 4,000 萬
FEE_RATE = 0.0004                 # 法人單手續費率 0.04%

# 初始化 Session State
state_keys = {
    'cash': INITIAL_CAPITAL,
    'realized_pnl': 0,
    'trades': [],
    'positions': {},
    'daily_equity_history': [],
    'market_prices': {}  # 格式: { '代號': {'name': '名稱', 'price': 0.0} }
}
for key, value in state_keys.items():
    if key not in st.session_state:
        st.session_state[key] = value

# ==========================================
# 3. 核心數據抓取與清洗 (解決報價抓不到的問題)
# ==========================================
@st.cache_data(ttl=3600)
def fetch_market_data():
    market_info = {}
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    # 抓取上市 (TWSE)
    try:
        twse_url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
        twse_data = requests.get(twse_url, headers=headers, timeout=10).json()
        for i in twse_data:
            # 清洗價格：移除逗號並處理 "-", "" 等異常值
            raw_p = str(i.get('ClosingPrice', '0')).replace(',', '')
            price = float(raw_p) if raw_p and raw_p not in ['-', ''] else 0.0
            market_info[i['Code']] = {'name': i['Name'], 'price': price}
    except Exception as e:
        st.warning(f"上市報價抓取失敗: {e}")

    # 抓取上櫃 (TPEx)
    try:
        tpex_url = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
        tpex_data = requests.get(tpex_url, headers=headers, timeout=10).json()
        for i in tpex_data:
            raw_p = str(i.get('Close', '0')).replace(',', '')
            price = float(raw_p) if raw_p and raw_p not in ['-', ''] else 0.0
            market_info[i['SecuritiesCompanyCode']] = {'name': i['CompanyName'], 'price': price}
    except Exception as e:
        st.warning(f"上櫃報價抓取失敗: {e}")

    return market_info

def get_equity():
    """計算當前總淨值 (現金 + 庫存市值)"""
    stock_val = 0
    for t, p in st.session_state.positions.items():
        # 取得最新市價，若抓不到則暫以均價計算
        cur_p = st.session_state.market_prices.get(t, {}).get('price', p['avg_cost'])
        stock_val += cur_p * p['quantity']
    return st.session_state.cash + stock_val

# ==========================================
# 4. 側邊欄與存檔管理
# ==========================================
with st.sidebar:
    st.header("⚙️ 系統進度管理")
    if st.button("🔄 更新全市場收盤價", use_container_width=True):
        new_prices = fetch_market_data()
        if new_prices:
            st.session_state.market_prices = new_prices
            st.success(f"已同步 {len(new_prices)} 檔標的")
            st.rerun()
    
    st.divider()
    up_file = st.file_uploader("📂 載入進度 (.json)", type="json")
    if up_file:
        data = json.load(up_file)
        st.session_state.update(data)
        st.success("讀檔成功")
    
    # 存檔需排除大型報價字典以縮減體積
    save_data = {k: v for k, v in st.session_state.items() if k != 'market_prices'}
    st.download_button("💾 儲存進度檔 (JSON)", 
        data=json.dumps(save_data, ensure_ascii=False),
        file_name=f"STP_Save_{datetime.now().strftime('%m%d')}.json", use_container_width=True)

# ==========================================
# 5. 儀表板數據呈現
# ==========================================
st.title("📈 STP 操盤手模擬訓練平台")

eq = get_equity()
unrealized = sum(((st.session_state.market_prices.get(t, {}).get('price', p['avg_cost']) - p['avg_cost']) * p['quantity']) 
                 for t, p in st.session_state.positions.items())
total_pnl = unrealized + st.session_state.realized_pnl

m1, m2, m3, m4 = st.columns(4)
m1.metric("帳戶總淨值 (NAV)", f"${eq:,.0f}")
m2.metric("可用現金", f"${st.session_state.cash:,.0f}")
m3.metric("總損益 (Total PnL)", f"${total_pnl:,.0f}", delta=f"{total_pnl:,.0f}")
m4.metric("已實現損益", f"${st.session_state.realized_pnl:,.0f}")

st.divider()

# ==========================================
# 6. 圖表分析區
# ==========================================
c1, c2 = st.columns([1, 2])
with c1:
    st.subheader("資產配置")
    val_map = {"現金": st.session_state.cash, "股票": 0, "一般型 ETF": 0, "債券型 ETF": 0}
    for t, p in st.session_state.positions.items():
        cur_p = st.session_state.market_prices.get(t, {}).get('price', p['avg_cost'])
        val_map[p['type']] += (cur_p * p['quantity'])
    
    fig = px.pie(names=list(val_map.keys()), values=list(val_map.values()), hole=0.5, 
                 color_discrete_sequence=['#4a4e69', '#ef476f', '#06d6a0', '#118ab2'])
    fig.update_layout(height=300, margin=dict(t=20, b=20, l=0, r=0))
    st.plotly_chart(fig, use_container_width=True)

with c2:
    st.subheader("淨值走勢與結算")
    if st.button("📥 結算今日淨值紀錄", use_container_width=True):
        today = datetime.now().strftime('%m/%d')
        st.session_state.daily_equity_history = [h for h in st.session_state.daily_equity_history if h['date'] != today]
        st.session_state.daily_equity_history.append({"date": today, "equity": eq})
        st.success(f"今日 ({today}) 淨值已存檔")

    if st.session_state.daily_equity_history:
        h_df = pd.DataFrame(st.session_state.daily_equity_history)
        fig_l = px.line(h_df, x='date', y='equity', markers=True, template="plotly_dark")
        fig_l.update_layout(height=250, margin=dict(t=10, b=10, l=10, r=10))
        st.plotly_chart(fig_l, use_container_width=True)

# ==========================================
# 7. 交易執行模組 (含自動名稱偵測)
# ==========================================
st.divider()
t_col, l_col = st.columns([1, 2])

with t_col:
    st.subheader("執行交易")
    with st.form("trade_form", clear_on_submit=True):
        ticker = st.text_input("輸入標的代號").strip().upper()
        
        # 動態顯示標的資訊
        stock_info = st.session_state.market_prices.get(ticker, {})
        s_name = stock_info.get('name', "請輸入代號")
        m_price = stock_info.get('price', 0.0)
        st.caption(f"🔍 標的：{s_name} | 收盤參考：{m_price}")
        
        price = st.number_input("成交單價", min_value=0.0, value=float(m_price), step=0.01)
        qty = st.number_input("成交數量 (股)", min_value=1, step=1000, value=1000)
        note = st.text_input("交易筆記")
        
        b1, b2 = st.columns(2)
        buy_btn = b1.form_submit_button("🟩 買進", use_container_width=True)
        sell_btn = b2.form_submit_button("🟥 賣出", use_container_width=True)

        if buy_btn or sell_btn:
            if not ticker or price <= 0:
                st.error("請確認代號與單價是否正確")
            else:
                # 判定資產類型與稅率
                if ticker.startswith('00'):
                    a_type, t_rate = ('債券型 ETF', 0.0) if ticker.endswith('B') else ('一般型 ETF', 0.001)
                else:
                    a_type, t_rate = '股票', 0.003
                
                base_val = price * qty
                fee = max(20, int(base_val * FEE_RATE))
                
                if buy_btn:
                    net_cost = base_val + fee
                    current_pos_val = st.session_state.positions.get(ticker, {}).get('avg_cost', 0) * st.session_state.positions.get(ticker, {}).get('quantity', 0)
                    
                    if (current_pos_val + net_cost) > COST_LIMIT_PER_TICKER:
                        st.error(f"❌ 違反風控：單一標的總成本上限為 4,000 萬！")
                    elif net_cost > st.session_state.cash:
                        st.error("❌ 現金不足")
                    else:
                        st.session_state.cash -= net_cost
                        pos = st.session_state.positions.get(ticker, {'quantity': 0, 'avg_cost': 0, 'type': a_type})
                        new_qty = pos['quantity'] + qty
                        pos['avg_cost'] = ((pos['avg_cost'] * pos['quantity']) + net_cost) / new_qty
                        pos['quantity'] = new_qty
                        st.session_state.positions[ticker] = pos
                        st.session_state.trades.append({
                            "時間": datetime.now().strftime("%H:%M"), "動作": "買進", 
                            "代號": ticker, "名稱": s_name, "價格": price, "數量": qty, "損益影響": -net_cost, "備註": note
                        })
                        st.rerun()

                if sell_btn:
                    if ticker not in st.session_state.positions or st.session_state.positions[ticker]['quantity'] < qty:
                        st.error("❌ 庫存不足")
                    else:
                        tax = int(base_val * t_rate)
                        net_recv = base_val - fee - tax
                        cost_basis = st.session_state.positions[ticker]['avg_cost'] * qty
                        st.session_state.cash += net_recv
                        st.session_state.realized_pnl += (net_recv - cost_basis)
                        st.session_state.positions[ticker]['quantity'] -= qty
                        if st.session_state.positions[ticker]['quantity'] == 0:
                            del st.session_state.positions[ticker]
                        st.session_state.trades.append({
                            "時間": datetime.now().strftime("%H:%M"), "動作": "賣出", 
                            "代號": ticker, "名稱": s_name, "價格": price, "數量": qty, "損益影響": net_recv, "備註": note
                        })
                        st.rerun()

with l_col:
    tab1, tab2 = st.tabs(["📊 當前庫存", "📝 歷史明細"])
    with tab1:
        if st.session_state.positions:
            disp_p = []
            for t, p in st.session_state.positions.items():
                cur_p = st.session_state.market_prices.get(t, {}).get('price', p['avg_cost'])
                un_pnl = (cur_p - p['avg_cost']) * p['quantity']
                disp_p.append({
                    "標的": t, "名稱": st.session_state.market_prices.get(t, {}).get('name', 'N/A'),
                    "數量": p['quantity'], "均價": round(p['avg_cost'], 2), "現價": cur_p, "未實現損益": round(un_pnl)
                })
            st.dataframe(pd.DataFrame(disp_p), use_container_width=True, hide_index=True)
    with tab2:
        if st.session_state.trades:
            st.dataframe(pd.DataFrame(st.session_state.trades)[::-1], use_container_width=True, hide_index=True)

# ==========================================
# 8. 浮動說明按鈕
# ==========================================
st.markdown('<a href="https://github.com" target="_blank" class="help-float-btn">Help</a>', unsafe_allow_html=True)
