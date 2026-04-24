import streamlit as st
import pandas as pd
import requests
import json
from datetime import datetime, time
import plotly.express as px
import plotly.graph_objects as go

# ==========================================
# 1. 頁面配置與 CSS 樣式注入 (含手機端防跑版與浮動按鈕)
# ==========================================
st.set_page_config(page_title="STP 操盤模擬平台 | Royal Life", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""
<style>
    /* 隱藏預設元件 */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    
    /* 數據卡片美化 */
    div[data-testid="metric-container"] {
        background-color: #1e1e1e; border: 1px solid #333; padding: 12px; border-radius: 8px; border-left: 5px solid #ffb703;
    }
    
    /* 強化輸入框顯示 (防止 iOS Safari 縮放) */
    .stTextInput input, .stNumberInput input, .stTextArea textarea { font-size: 16px !important; }

    /* 金黃色圓形浮動說明按鈕 */
    .help-float-btn {
        position: fixed; bottom: 30px; right: 30px; background-color: #ffb703; color: #000; width: 60px; height: 60px;
        border-radius: 50%; box-shadow: 0 4px 15px rgba(255, 183, 3, 0.6); font-size: 1rem; font-weight: 900;
        z-index: 9999; display: flex; justify-content: center; align-items: center; border: 3px solid #fff;
        cursor: pointer; text-decoration: none; transition: transform 0.2s ease;
    }
    .help-float-btn:hover { transform: scale(1.1); background-color: #ff9f1c; }
    
    /* 強制限制圖表容器高度，防止遮擋 */
    .chart-container-box { height: 280px; overflow: hidden; margin-bottom: 20px; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. 系統狀態與常數 (依據 STP 專案提案書 1.3)
# ==========================================
INITIAL_CAPITAL = 200000000       # 初始資金 2 億
COST_LIMIT_PER_TICKER = 40000000  # 單一標的成本上限 4,000 萬
MIN_PORTFOLIO_COST = 20000000     # 每組持股最低成本限制 2,000 萬
FEE_RATE = 0.0004                 # 法人單手續費率 0.04%
TOTAL_LOSS_LIMIT = 20000000       # 總累積虧損上限 2,000 萬
PHASE_LOSS_LIMIT = 10000000       # 階段性虧損上限 1,000 萬

# 初始化 Session State
state_keys = {
    'group': "股票投資組", # 新增組別區分
    'cash': INITIAL_CAPITAL,
    'realized_pnl': 0,
    'trades': [],
    'positions': {},
    'daily_equity_history': [],
    'market_prices': {}  # 格式: { '代號': {'name': '名稱', 'price': 0.0, 'is_etf': False} }
}
for key, value in state_keys.items():
    if key not in st.session_state:
        st.session_state[key] = value

# ==========================================
# 3. 核心數據抓取與清洗 
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
            raw_p = str(i.get('ClosingPrice', '0')).replace(',', '')
            price = float(raw_p) if raw_p and raw_p not in ['-', ''] else 0.0
            market_info[i['Code']] = {'name': i['Name'], 'price': price, 'is_etf': i['Code'].startswith('00')}
    except Exception as e:
        st.warning(f"上市報價抓取失敗: {e}")

    # 抓取上櫃 (TPEx)
    try:
        tpex_url = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
        tpex_data = requests.get(tpex_url, headers=headers, timeout=10).json()
        for i in tpex_data:
            raw_p = str(i.get('Close', '0')).replace(',', '')
            price = float(raw_p) if raw_p and raw_p not in ['-', ''] else 0.0
            market_info[i['SecuritiesCompanyCode']] = {'name': i['CompanyName'], 'price': price, 'is_etf': i['SecuritiesCompanyCode'].startswith('00')}
    except Exception as e:
        st.warning(f"上櫃報價抓取失敗: {e}")

    return market_info

def get_equity():
    """計算當前總淨值 (現金 + 庫存市值)"""
    stock_val = 0
    for t, p in st.session_state.positions.items():
        cur_p = st.session_state.market_prices.get(t, {}).get('price', p['avg_cost'])
        stock_val += cur_p * p['quantity']
    return st.session_state.cash + stock_val

@st.dialog("📈 STP 模擬交易標準流程與風控")
def show_help_dialog():
    st.markdown(f"""
    #### 1. 買進限制 (Pre-trade Control)
    * 單一標的總成本上限：**{COST_LIMIT_PER_TICKER/10000:,.0f} 萬元**。
    * 持股總成本不得低於：**{MIN_PORTFOLIO_COST/10000:,.0f} 萬元**。
    * 法人級手續費：**0.04%**。
    
    #### 2. 證交稅判斷 (僅賣出收取)
    * **股票**：非 00 開頭，課徵 **0.3%**。
    * **一般型 ETF**：00 開頭 (非 B 結尾)，課徵 **0.1%**。
    * **債券型 ETF**：00 開頭且 B 結尾，**暫停課徵 (0%)**。
    
    #### 3. 階段性停損規範
    * 總累積虧損達 2,000 萬，或階段性虧損達 1,000 萬，強制鎖定交易。
    * 單一標的損失達 30%，須於次日強制出清。
    
    #### 4. 每日結算與存檔
    * 下班前點擊 **[更新全市場收盤價]**。
    * 點擊 **[📥 結算今日淨值紀錄]** 紀錄軌跡。
    * 使用側邊欄 **[儲存進度檔]** 下載 JSON。
    """)

# ==========================================
# 4. UI 佈局：側邊欄 (管理區與組別設定)
# ==========================================
with st.sidebar:
    st.header("⚙️ 系統進度管理")
    
    # 組別切換
    new_group = st.radio("當前操作組別：", ["股票投資組", "ETF投資組"])
    if new_group != st.session_state.group:
        st.session_state.group = new_group
        st.rerun()
        
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
# 5. UI 佈局：頂部儀表板與風控偵測
# ==========================================
st.title(f"📈 STP 操盤手模擬訓練平台 - {st.session_state.group}")

# 風控檢查邏輯 (階段虧損與總虧損)
unrealized = sum(((st.session_state.market_prices.get(t, {}).get('price', p['avg_cost']) - p['avg_cost']) * p['quantity']) 
                 for t, p in st.session_state.positions.items())
total_pnl = unrealized + st.session_state.realized_pnl
total_cost = sum(p['avg_cost'] * p['quantity'] for p in st.session_state.positions.values())

now = datetime.now()
is_phase_halt = False
if total_pnl <= -TOTAL_LOSS_LIMIT:
    st.error("🚨 警告：總虧損已達 2,000 萬，依規定強制停止交易！")
    is_phase_halt = True
elif (datetime(2026, 6, 22) <= now <= datetime(2026, 7, 31)) and total_pnl <= -PHASE_LOSS_LIMIT:
    st.error("🚨 警告：本階段虧損已達 1,000 萬，依規定須全數平倉並停止交易！")
    is_phase_halt = True

# 顯示關鍵數據
eq = get_equity()
m1, m2, m3, m4 = st.columns(4)
m1.metric("帳戶總淨值 (NAV)", f"${eq:,.0f}")
m2.metric("可用現金", f"${st.session_state.cash:,.0f}")
m3.metric("總損益 (Total PnL)", f"${total_pnl:,.0f}", delta=f"{total_pnl:,.0f}")
m4.metric("當前持股總成本", f"${total_cost:,.0f}")

# 持股最低水位警示
if 0 < total_cost < MIN_PORTFOLIO_COST:
    st.warning(f"⚠️ 風控提醒：當前持股成本 ${total_cost:,.0f} 低於規範之 2,000 萬水位！")

st.markdown("---")

# ==========================================
# 6. UI 佈局：中間圖表區 (圓餅圖與折線圖)
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
    fig.update_layout(height=280, margin=dict(t=10, b=10, l=10, r=10), showlegend=True, legend=dict(orientation="h", y=-0.2))
    st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})

with c2:
    h_col, b_col = st.columns([2, 1])
    h_col.subheader("歷史每日收盤走勢")
    if b_col.button("📥 結算今日淨值紀錄", use_container_width=True):
        today = datetime.now().strftime('%m/%d')
        st.session_state.daily_equity_history = [h for h in st.session_state.daily_equity_history if h['date'] != today]
        st.session_state.daily_equity_history.append({"date": today, "equity": eq})
        st.success(f"今日 ({today}) 淨值已存檔")
        st.rerun()

    h_df = pd.DataFrame(st.session_state.daily_equity_history + [{"date": "即時", "equity": eq}])
    fig_l = px.line(h_df, x='date', y='equity', markers=True, template="plotly_dark")
    fig_l.update_layout(height=250, margin=dict(t=10, b=10, l=10, r=10), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
    fig_l.update_yaxes(title=None); fig_l.update_xaxes(title=None)
    st.plotly_chart(fig_l, use_container_width=True, config={'displayModeBar': False})

st.markdown("---")

# ==========================================
# 7. 交易執行模組 (含自動名稱、權限檢查、13:30 規則)
# ==========================================
t_col, l_col = st.columns([1, 2])

with t_col:
    st.subheader("執行交易")
    if is_phase_halt:
        st.error("交易功能已鎖定 (風控觸發)")
    else:
        with st.form("trade_form", clear_on_submit=True):
            ticker = st.text_input("輸入標的代號").strip().upper()
            
            # 動態顯示標的資訊
            stock_info = st.session_state.market_prices.get(ticker, {})
            s_name = stock_info.get('name', "請輸入代號")
            m_price = stock_info.get('price', 0.0)
            is_etf = stock_info.get('is_etf', False)
            st.caption(f"🔍 標的：{s_name} | 收盤參考：{m_price}")
            
            price = st.number_input("成交單價", min_value=0.0, value=float(m_price), step=0.01)
            qty = st.number_input("成交數量 (股)", min_value=1, step=1000, value=1000)
            reason = st.text_area("買進/賣出理由 (必填)", placeholder="依據研究觀點...")
            
            b1, b2 = st.columns(2)
            buy_btn = b1.form_submit_button("🟩 買進", use_container_width=True)
            sell_btn = b2.form_submit_button("🟥 賣出", use_container_width=True)

            if buy_btn or sell_btn:
                # 組別權限檢查
                group_valid = (st.session_state.group == "ETF投資組" and is_etf) or \
                              (st.session_state.group == "股票投資組" and not is_etf)

                if not ticker or price <= 0:
                    st.error("請確認代號與單價是否正確")
                elif not reason:
                    st.error("請輸入交易理由！")
                elif not group_valid:
                    st.error(f"❌ 違規：{st.session_state.group} 不得操作 {'股票' if is_etf else 'ETF'}！")
                else:
                    # 13:30 撮合邏輯判斷
                    exec_note = "今日結算" if datetime.now().time() <= time(13, 30) else "次日結算"
                    
                    # 判定資產類型與稅率
                    if is_etf:
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
                                "代號": ticker, "名稱": s_name, "價格": price, "數量": qty, "損益影響": -net_cost, "備註/理由": reason, "計價": exec_note
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
                                "代號": ticker, "名稱": s_name, "價格": price, "數量": qty, "損益影響": net_recv, "備註/理由": reason, "計價": exec_note
                            })
                            st.rerun()

# ==========================================
# 8. 庫存展示與 30% 強制停損警示
# ==========================================
with l_col:
    tab1, tab2 = st.tabs(["📊 當前庫存", "📝 歷史明細"])
    with tab1:
        if st.session_state.positions:
            disp_p = []
            for t, p in st.session_state.positions.items():
                cur_p = st.session_state.market_prices.get(t, {}).get('price', p['avg_cost'])
                un_pnl = (cur_p - p['avg_cost']) * p['quantity']
                ratio = (cur_p / p['avg_cost']) - 1 if p['avg_cost'] > 0 else 0
                
                # 30% 停損警告
                warning = "🚨 強制平倉" if ratio <= -0.3 else "正常"
                
                disp_p.append({
                    "標的": t, "名稱": st.session_state.market_prices.get(t, {}).get('name', 'N/A'),
                    "類型": p['type'], "數量": p['quantity'], "均價": round(p['avg_cost'], 2), 
                    "現價": cur_p, "未實現損益": round(un_pnl), "狀態": warning
                })
            st.dataframe(pd.DataFrame(disp_p), use_container_width=True, hide_index=True)
            
            # 若有觸發 30% 停損的標的，顯示警告
            if any(d['狀態'] == "🚨 強制平倉" for d in disp_p):
                st.error("🚨 偵測到個別標的損失達 30%，依規定須於次日強制出清！")
                
    with tab2:
        if st.session_state.trades:
            st.dataframe(pd.DataFrame(st.session_state.trades)[::-1], use_container_width=True, hide_index=True)

# ==========================================
# 9. 圓形浮動按鈕渲染 (透過 JS 觸發隱藏的 Streamlit Button)
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
