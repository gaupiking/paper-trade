import streamlit as st
import pandas as pd
import requests
import json
import urllib3
from datetime import datetime, time
import plotly.express as px
import plotly.graph_objects as go

# 隱藏 SSL 警告訊息
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==========================================
# 1. 頁面配置與專業 UI 樣式 (完美保留你的設計)
# ==========================================
st.set_page_config(page_title="STP 操盤模擬平台 | Royal Life", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
    /* 隱藏預設元件 */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    
    /* 數據卡片美化 */
    div[data-testid="metric-container"] {
        background-color: #1e1e1e; border: 1px solid #333; padding: 15px; border-radius: 10px; border-left: 5px solid #ffb703;
    }
    
    /* 強化輸入框顯示 (防止 iOS Safari 縮放) */
    .stTextInput input, .stNumberInput input, .stTextArea textarea { font-size: 16px !important; }

    /* 金黃色浮動說明按鈕 */
    .help-float-btn {
        position: fixed; bottom: 30px; right: 30px; background-color: #ffb703; color: #000; width: 60px; height: 60px;
        border-radius: 50%; box-shadow: 0 4px 15px rgba(255, 183, 3, 0.6); font-size: 1rem; font-weight: 900;
        z-index: 9999; display: flex; justify-content: center; align-items: center; border: 3px solid #fff;
        cursor: pointer; text-decoration: none; transition: transform 0.2s ease;
    }
    .help-float-btn:hover { transform: scale(1.1); background-color: #ff9f1c; }
    
    /* 圖表容器高度限制 */
    .chart-container-box { height: 280px; overflow: hidden; margin-bottom: 20px; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. 系統常數與狀態初始化 (依據 STP 提案書 1.3)
# ==========================================
INITIAL_CAPITAL = 200000000       # 初始資金 2 億
COST_LIMIT_PER_TICKER = 40000000  # 單一標的成本上限 4,000 萬
MIN_PORTFOLIO_COST = 20000000     # 持股最低水位 2,000 萬
FEE_RATE = 0.0004                 # 法人手續費 0.04%
TOTAL_LOSS_LIMIT = 20000000       # 總累積虧損上限 2,000 萬
PHASE_LOSS_LIMIT = 10000000       # 階段性虧損上限 1,000 萬

# 初始化 Session State
if 'group' not in st.session_state: st.session_state.group = "股票投資組"
if 'cash' not in st.session_state: st.session_state.cash = INITIAL_CAPITAL
if 'realized_pnl' not in st.session_state: st.session_state.realized_pnl = 0
if 'trades' not in st.session_state: st.session_state.trades = []
if 'positions' not in st.session_state: st.session_state.positions = {}
if 'daily_equity_history' not in st.session_state: st.session_state.daily_equity_history = []
if 'market_prices' not in st.session_state: st.session_state.market_prices = {}

# ==========================================
# 3. 核心數據抓取 (解決 SSL 錯誤)
# ==========================================
@st.cache_data(ttl=600)
def fetch_market_data():
    market_info = {}
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        # 使用 verify=False 解決 SSL 憑證驗證失敗問題
        twse_url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
        twse_resp = requests.get(twse_url, headers=headers, timeout=10, verify=False)
        if twse_resp.status_code == 200:
            for i in twse_resp.json():
                raw_p = str(i.get('ClosingPrice', '0')).replace(',', '')
                price = float(raw_p) if raw_p and raw_p not in ['-', ''] else 0.0
                market_info[i['Code']] = {'name': i['Name'], 'price': price, 'is_etf': i['Code'].startswith('00')}
        
        tpex_url = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
        tpex_resp = requests.get(tpex_url, headers=headers, timeout=10, verify=False)
        if tpex_resp.status_code == 200:
            for i in tpex_resp.json():
                raw_p = str(i.get('Close', '0')).replace(',', '')
                price = float(raw_p) if raw_p and raw_p not in ['-', ''] else 0.0
                market_info[i['SecuritiesCompanyCode']] = {'name': i['CompanyName'], 'price': price, 'is_etf': i['SecuritiesCompanyCode'].startswith('00')}
    except Exception as e:
        st.error(f"報價抓取失敗: {e}")
    return market_info

def get_equity():
    stock_val = sum((st.session_state.market_prices.get(t, {}).get('price', p['avg_cost']) * p['quantity']) 
                    for t, p in st.session_state.positions.items())
    return st.session_state.cash + stock_val

@st.dialog("📈 STP 模擬交易標準流程與風控")
def show_help_dialog():
    st.markdown(f"""
    #### 1. [span_0](start_span)買進與持股限制[span_0](end_span)
    * 單一標的總成本上限：**4,000 萬元**。
    * 持股最低成本限制：**2,000 萬元**。
    
    #### 2. [span_1](start_span)成交價計價規則[span_1](end_span)
    * **13:30 前**下單：以**當日收盤價**計算。
    * **13:30 後**下單：以**次日收盤價**計算。
    
    #### 3. [span_2](start_span)階段性停損規範[span_2](end_span)
    * 總累積虧損達 2,000 萬，或階段性虧損達 1,000 萬，強制停止交易。
    * 單一標的損失達 **30%**，須於次日強制出清。
    """)

# ==========================================
# 4. 側邊欄：進度與組別管理
# ==========================================
with st.sidebar:
    st.header("⚙️ 系統管理")
    new_group = st.radio("當前操作組別：", ["股票投資組", "ETF投資組"])
    if new_group != st.session_state.group:
        st.session_state.group = new_group
        st.rerun()

    if st.button("🔄 更新全市場收盤價", use_container_width=True):
        new_prices = fetch_market_data()
        if new_prices:
            st.session_state.market_prices = new_prices
            st.success("報價更新完成")
            st.rerun()
            
    st.divider()
    up_file = st.file_uploader("📂 載入進度 (.json)", type="json")
    if up_file:
        data = json.load(up_file)
        st.session_state.update(data)
        st.success("讀檔成功")
        
    st.download_button("💾 儲存進度檔 (JSON)", 
        data=json.dumps({k: v for k, v in st.session_state.items() if k != 'market_prices'}, ensure_ascii=False),
        file_name=f"STP_Save_{datetime.now().strftime('%m%d')}.json", use_container_width=True)

# ==========================================
# 5. 儀表板與風控偵測
# ==========================================
st.title(f"📈 STP 模擬交易平台 - {st.session_state.group}")

eq = get_equity()
unrealized = sum(((st.session_state.market_prices.get(t, {}).get('price', p['avg_cost']) - p['avg_cost']) * p['quantity']) 
                 for t, p in st.session_state.positions.items())
total_pnl = unrealized + st.session_state.realized_pnl
total_cost = sum(p['avg_cost'] * p['quantity'] for p in st.session_state.positions.values())

# 風控鎖定判斷
now = datetime.now()
is_halted = False
if total_pnl <= -TOTAL_LOSS_LIMIT:
    [span_3](start_span)st.error("🚨 警告：總累積虧損已達 2,000 萬上限，強制停止交易！[span_3](end_span)")
    is_halted = True
elif (datetime(2026, 6, 22) <= now <= datetime(2026, 7, 31)) and total_pnl <= -PHASE_LOSS_LIMIT:
    [span_4](start_span)st.warning("🚨 警告：階段性虧損已達 1,000 萬，依規定須停止交易！[span_4](end_span)")
    is_halted = True

m1, m2, m3, m4 = st.columns(4)
m1.metric("帳戶總淨值", f"${eq:,.0f}")
m2.metric("可用現金", f"${st.session_state.cash:,.0f}")
m3.metric("總損益 (PnL)", f"${total_pnl:,.0f}", delta=f"{total_pnl:,.0f}")
m4.metric("當前持股成本", f"${total_cost:,.0f}")

if 0 < total_cost < MIN_PORTFOLIO_COST:
    [span_5](start_span)st.warning(f"⚠️ 提醒：持股總成本目前低於規範之 2,000 萬水位。[span_5](end_span)")

st.markdown("---")

# ==========================================
# 6. 圖表分析區
# ==========================================
c1, c2 = st.columns([1, 2])
with c1:
    st.subheader("資產配置")
    val_map = {"現金": st.session_state.cash, "股票": 0, "一般型 ETF": 0, "債券型 ETF": 0}
    for t, p in st.session_state.positions.items():
        cur_p = st.session_state.market_prices.get(t, {}).get('price', p['avg_cost'])
        val_map[p.get('type', '股票')] += (cur_p * p['quantity'])
    
    fig = px.pie(names=list(val_map.keys()), values=list(val_map.values()), hole=0.5, 
                 color_discrete_sequence=['#4a4e69', '#ef476f', '#06d6a0', '#118ab2'])
    fig.update_layout(height=280, margin=dict(t=10, b=10, l=10, r=10), showlegend=True, legend=dict(orientation="h", y=-0.2))
    st.plotly_chart(fig, use_container_width=True)

with c2:
    st.subheader("淨值紀錄走勢")
    if st.button("📥 結算今日淨值", use_container_width=True):
        today = datetime.now().strftime('%m/%d')
        st.session_state.daily_equity_history = [h for h in st.session_state.daily_equity_history if h['date'] != today]
        st.session_state.daily_equity_history.append({"date": today, "equity": eq})
        st.success(f"已記錄今日淨值：${eq:,.0f}")

    h_df = pd.DataFrame(st.session_state.daily_equity_history + [{"date": "即時", "equity": eq}])
    fig_l = px.line(h_df, x='date', y='equity', markers=True, template="plotly_dark")
    fig_l.update_layout(height=250, margin=dict(t=10, b=10, l=10, r=10), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
    st.plotly_chart(fig_l, use_container_width=True)

st.markdown("---")

# ==========================================
# 7. 交易執行 (整合名稱、理由、13:30 計價)
# ==========================================
t_col, l_col = st.columns([1, 2])

with t_col:
    st.subheader("執行下單委託")
    if is_halted:
        st.error("系統交易權限已暫鎖")
    else:
        with st.form("trade_form", clear_on_submit=True):
            ticker = st.text_input("標的代號").strip().upper()
            
            info = st.session_state.market_prices.get(ticker, {})
            s_name = info.get('name', "請輸入代號查詢")
            ref_price = info.get('price', 0.0)
            is_etf = info.get('is_etf', False)
            
            st.caption(f"🔍 標的: {s_name} | 收盤參考價: {ref_price}")
            
            price = st.number_input("成交單價 (依 13:30 規則計算)", min_value=0.0, value=float(ref_price), step=0.01)
            qty = st.number_input("成交數量 (股)", min_value=1, step=1000, value=1000)
            [span_6](start_span)reason = st.text_area("買進/賣出理由 (必填)[span_6](end_span)")
            
            b1, b2 = st.columns(2)
            buy_btn = b1.form_submit_button("🟩 買進", use_container_width=True)
            sell_btn = b2.form_submit_button("🟥 賣出", use_container_width=True)

            if buy_btn or sell_btn:
                # 組別與標的限制判斷
                group_valid = (st.session_state.group == "ETF投資組" and is_etf) or \
                              (st.session_state.group == "股票投資組" and not is_etf)

                if not ticker or price <= 0: st.error("請確認代號與單價")
                [span_7](start_span)elif not reason: st.error("❌ 依規範必須填寫交易理由！[span_7](end_span)")
                [span_8](start_span)elif not group_valid: st.error(f"❌ 標的不符組別規範！[span_8](end_span)")
                else:
                    exec_rule = "今日收盤價" if datetime.now().time() <= time(13, 30) else "次日收盤價"
                    
                    if is_etf: a_type, t_rate = ('債券型 ETF', 0.0) if ticker.endswith('B') else ('一般型 ETF', 0.001)
                    else: a_type, t_rate = '股票', 0.003
                    
                    base = int(price * qty)
                    fee = max(20, int(base * FEE_RATE))
                    
                    if buy_btn:
                        net_cost = base + fee
                        cur_t_cost = st.session_state.positions.get(ticker, {}).get('avg_cost', 0) * st.session_state.positions.get(ticker, {}).get('quantity', 0)
                        
                        if (cur_t_cost + net_cost) > COST_LIMIT_PER_TICKER:
                            [span_9](start_span)st.error(f"❌ 違反單一標的 4,000 萬限額！[span_9](end_span)")
                        elif net_cost > st.session_state.cash:
                            st.error("❌ 現金不足")
                        else:
                            st.session_state.cash -= net_cost
                            pos = st.session_state.positions.get(ticker, {'quantity': 0, 'avg_cost': 0, 'type': a_type})
                            new_q = pos['quantity'] + qty
                            pos['avg_cost'] = ((pos['avg_cost'] * pos['quantity']) + net_cost) / new_q
                            pos['quantity'] = new_q
                            st.session_state.positions[ticker] = pos
                            st.session_state.trades.append({"時間": datetime.now().strftime("%m/%d %H:%M"), "動作": "買進", "代號": ticker, "名稱": s_name, "單價": price, "數量": qty, "理由": reason, "規則": exec_rule})
                            st.rerun()

                    if sell_btn:
                        if ticker not in st.session_state.positions or st.session_state.positions[ticker]['quantity'] < qty: 
                            st.error("❌ 庫存不足")
                        else:
                            tax = int(base * t_rate)
                            net_recv = base - fee - tax
                            st.session_state.cash += net_recv
                            st.session_state.realized_pnl += (net_recv - (st.session_state.positions[ticker]['avg_cost'] * qty))
                            st.session_state.positions[ticker]['quantity'] -= qty
                            if st.session_state.positions[ticker]['quantity'] == 0: 
                                del st.session_state.positions[ticker]
                            st.session_state.trades.append({"時間": datetime.now().strftime("%m/%d %H:%M"), "動作": "賣出", "代號": ticker, "名稱": s_name, "單價": price, "數量": qty, "理由": reason, "規則": exec_rule})
                            st.rerun()

# ==========================================
# 8. 庫存展示與 30% 停損警示
# ==========================================
with l_col:
    tab1, tab2 = st.tabs(["📊 當前持股庫存", "📝 交易日誌紀錄"])
    with tab1:
        if st.session_state.positions:
            disp_p = []
            for t, p in st.session_state.positions.items():
                cur_p = st.session_state.market_prices.get(t, {}).get('price', p['avg_cost'])
                ratio = (cur_p / p['avg_cost']) - 1 if p['avg_cost'] > 0 else 0
                status = "🚨 30%強制停損" if ratio <= -0.3 else "正常"
                disp_p.append({
                    "標的": t, "名稱": st.session_state.market_prices.get(t, {}).get('name', 'N/A'),
                    "數量": p['quantity'], "均價": round(p['avg_cost'], 2), 
                    "現價": cur_p, "報酬率": f"{ratio:.2%}", "狀態": status
                })
            st.dataframe(pd.DataFrame(disp_p), use_container_width=True, hide_index=True)
            if any("停損" in str(x) for x in disp_p):
                [span_10](start_span)st.error("🚨 注意：已有標的損失達 30% 成本，依規須於次日強制出清！[span_10](end_span)")
                
    with tab2:
        if st.session_state.trades:
            st.dataframe(pd.DataFrame(st.session_state.trades)[::-1], use_container_width=True, hide_index=True)

# ==========================================
# 9. 浮動說明按鈕
# ==========================================
components_html = """
<script>
function triggerHelp() {
    const buttons = window.parent.document.querySelectorAll('button');
    for (let i = 0; i < buttons.length; i++) {
        if (buttons[i].innerText.includes('隱藏說明按鈕')) {
            buttons[i].click();
            break;
        }
    }
}
</script>
<a href="javascript:triggerHelp();" class="help-float-btn">說明</a>
"""
st.components.v1.html(components_html, height=0)

if st.button("隱藏說明按鈕", key="hidden_help"):
    show_help_dialog()

st.markdown("""
<style>
    button[kind="secondary"]:has(div[data-testid="stMarkdownContainer"] > p:contains("隱藏說明按鈕")) {
        display: none;
    }
</style>
""", unsafe_allow_html=True)
