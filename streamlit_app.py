import streamlit as st
import pandas as pd
import requests
import json
import numpy as np
from datetime import datetime, time
import plotly.express as px

# ==========================================
# 1. [span_0](start_span)系統配置與 STP 規範常數[span_0](end_span)
# ==========================================
st.set_page_config(page_title="STP 法人級模擬交易平台", layout="wide")

[span_1](start_span)INITIAL_CAPITAL = 200_000_000        # 初始資金 2 億[span_1](end_span)
[span_2](start_span)COST_LIMIT_PER_TICKER = 40_000_000   # 單一標的成本上限 4,000 萬[span_2](end_span)
[span_3](start_span)MIN_PORTFOLIO_COST = 20_000_000      # 每組持股最低成本限制 2,000 萬[span_3](end_span)
[span_4](start_span)FEE_RATE = 0.0004                    # 法人單手續費率 0.04%[span_4](end_span)
[span_5](start_span)TOTAL_LOSS_LIMIT = 20_000_000        # 總累積虧損上限 2,000 萬[span_5](end_span)
[span_6](start_span)PHASE_LOSS_LIMIT = 10_000_000        # 階段性虧損上限 1,000 萬[span_6](end_span)

# CSS 樣式
st.markdown("""
<style>
    .metric-card { background-color: #1e1e1e; border: 1px solid #333; padding: 15px; border-radius: 10px; }
    .stButton>button { width: 100%; }
    .risk-warning { color: #ff4b4b; font-weight: bold; border: 1px solid #ff4b4b; padding: 10px; border-radius: 5px; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. 初始化 Session State
# ==========================================
if 'initialized' not in st.session_state:
    [span_7](start_span)st.session_state.group = "股票投資組"  # 預設組別[span_7](end_span)
    st.session_state.cash = INITIAL_CAPITAL
    st.session_state.realized_pnl = 0
    st.session_state.trades = []
    st.session_state.positions = {}
    st.session_state.daily_history = []  # 紀錄每日淨值
    st.session_state.market_prices = {}
    [span_8](start_span)st.session_state.trading_halted = False # 是否因風控停止交易[span_8](end_span)
    st.session_state.initialized = True

# ==========================================
# 3. 核心功能函式
# ==========================================

@st.cache_data(ttl=3600)
def fetch_twse_data():
    """抓取並清洗市場報價數據"""
    market_info = {}
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        # 上市資料
        twse = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=10).json()
        for i in twse:
            raw_p = str(i.get('ClosingPrice', '0')).replace(',', '')
            price = float(raw_p) if raw_p and raw_p not in ['-', ''] else 0.0
            market_info[i['Code']] = {'name': i['Name'], 'price': price, 'is_etf': i['Code'].startswith('00')}
        
        # 上櫃資料
        tpex = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", timeout=10).json()
        for i in tpex:
            raw_p = str(i.get('Close', '0')).replace(',', '')
            price = float(raw_p) if raw_p and raw_p not in ['-', ''] else 0.0
            market_info[i['SecuritiesCompanyCode']] = {'name': i['CompanyName'], 'price': price, 'is_etf': i['SecuritiesCompanyCode'].startswith('00')}
    except:
        pass
    return market_info

def calculate_metrics():
    [span_9](start_span)"""計算 Sharpe Ratio 與 MDD[span_9](end_span)"""
    if not st.session_state.daily_history:
        return 0, 0
    df = pd.DataFrame(st.session_state.daily_history)
    returns = df['equity'].pct_change().dropna()
    sharpe = (returns.mean() / returns.std() * np.sqrt(252)) if len(returns) > 1 and returns.std() != 0 else 0
    
    # MDD
    rolling_max = df['equity'].cummax()
    drawdown = (df['equity'] - rolling_max) / rolling_max
    mdd = drawdown.min()
    return sharpe, mdd

# ==========================================
# 4. UI 佈局：側邊欄 (組別與權限控制)
# ==========================================
with st.sidebar:
    st.title("🛡️ STP 系統管理")
    
    # [span_10](start_span)組別切換[span_10](end_span)
    new_group = st.radio("當前操作組別：", ["股票投資組", "ETF投資組"])
    if new_group != st.session_state.group:
        st.session_state.group = new_group
        st.rerun()
    
    st.divider()
    if st.button("🔄 同步全市場收盤價"):
        st.session_state.market_prices = fetch_twse_data()
        st.success("報價已更新")
    
    # [span_11](start_span)數據導出功能[span_11](end_span)
    st.subheader("📥 數據導出 (期末量化分析用)")
    if st.session_state.trades:
        trade_df = pd.DataFrame(st.session_state.trades)
        st.download_button("匯出交易日報表 (CSV)", trade_df.to_csv(index=False).encode('utf-8-sig'), "trade_report.csv")
    
    if st.session_state.daily_history:
        perf_df = pd.DataFrame(st.session_state.daily_history)
        st.download_button("匯出每日績效總表 (CSV)", perf_df.to_csv(index=False).encode('utf-8-sig'), "perf_report.csv")

# ==========================================
# 5. [span_12](start_span)儀表板與風控狀態偵測[span_12](end_span)
# ==========================================
st.title(f"📈 STP 模擬交易平台 - {st.session_state.group}")

# 計算當前狀態
current_equity = st.session_state.cash + sum(
    st.session_state.market_prices.get(t, {}).get('price', p['avg_cost']) * p['quantity'] 
    for t, p in st.session_state.positions.items()
)
total_unrealized = sum(
    (st.session_state.market_prices.get(t, {}).get('price', p['avg_cost']) - p['avg_cost']) * p['quantity']
    for t, p in st.session_state.positions.items()
)
total_pnl = st.session_state.realized_pnl + total_unrealized
total_cost = sum(p['avg_cost'] * p['quantity'] for p in st.session_state.positions.values())

# [span_13](start_span)風控檢查邏輯[span_13](end_span)
now = datetime.now()
is_phase_1 = (datetime(2026, 6, 22) <= now <= datetime(2026, 6, 30))
is_phase_2 = (datetime(2026, 7, 1) <= now <= datetime(2026, 7, 31))

if total_pnl <= -TOTAL_LOSS_LIMIT:
    st.error("🚨 警告：總虧損已達 2,000 萬，依規定強制停止交易！")
    st.session_state.trading_halted = True
elif (is_phase_1 or is_phase_2) and total_pnl <= -PHASE_LOSS_LIMIT:
    st.error("🚨 警告：本階段虧損已達 1,000 萬，依規定須全數平倉並暫停交易！")
    st.session_state.trading_halted = True

# 顯示關鍵數據
m1, m2, m3, m4 = st.columns(4)
m1.metric("帳戶總淨值", f"${current_equity:,.0f}")
m2.metric("總損益 (PnL)", f"${total_pnl:,.0f}", delta=f"{total_pnl:,.0f}")
m3.metric("當前持股總成本", f"${total_cost:,.0f}")
m4.metric("可用現金", f"${st.session_state.cash:,.0f}")

# [span_14](start_span)風控提示：最低持股成本限制[span_14](end_span)
if total_cost < MIN_PORTFOLIO_COST and total_cost > 0:
    st.warning(f"⚠️ 風控提醒：當前持股成本 ${total_cost:,.0f} 低於規範之 2,000 萬水位！")

# ==========================================
# 6. 交易執行模組 (含 13:30 撮合與權限控制)
# ==========================================
st.divider()
t_col, l_col = st.columns([1, 2])

with t_col:
    st.subheader("📢 交易委託單")
    if st.session_state.trading_halted:
        st.error("交易權限已被鎖定 (風控因素)")
    else:
        with st.form("trade_form", clear_on_submit=True):
            ticker = st.text_input("標的代號").strip().upper()
            
            # 動態獲取資訊
            info = st.session_state.market_prices.get(ticker, {})
            name = info.get('name', "未知標的")
            is_etf = info.get('is_etf', False)
            ref_price = info.get('price', 0.0)
            
            st.caption(f"🔍 名稱: {name} | 市價: {ref_price}")
            
            price = st.number_input("成交單價 (依 13:30 規則計算)", value=float(ref_price), step=0.01)
            qty = st.number_input("數量 (股)", min_value=1, step=1000)
            [span_15](start_span)reason = st.text_area("買進/賣出理由 (必填)[span_15](end_span)", placeholder="請輸入研究觀點...")
            
            b1, b2 = st.columns(2)
            buy = b1.form_submit_button("🟩 買進")
            sell = b2.form_submit_button("🟥 賣出")
            
            if buy or sell:
                # 1. [span_16](start_span)權限檢查[span_16](end_span)
                group_valid = (st.session_state.group == "ETF投資組" and is_etf) or \
                              (st.session_state.group == "股票投資組" and not is_etf)
                
                if not reason:
                    st.error("請輸入交易理由")
                elif ticker not in st.session_state.market_prices:
                    st.error("查無此標的代號")
                elif not group_valid:
                    st.error(f"❌ 違規：{st.session_state.group} 不得操作 {'股票' if is_etf else 'ETF'}！")
                else:
                    # 2. [span_17](start_span)13:30 撮合邏輯判斷[span_17](end_span)
                    current_time = datetime.now().time()
                    trade_date = datetime.now().strftime("%Y-%m-%d")
                    if current_time > time(13, 30):
                        st.info("💡 13:30 後下單，將以下一個交易日收盤價計價。")
                        exec_note = "次日結算"
                    else:
                        exec_note = "今日結算"
                        
                    # 3. [span_18](start_span)計算稅費[span_18](end_span)
                    base = price * qty
                    fee = max(20, int(base * FEE_RATE))
                    
                    if buy:
                        net_cost = base + fee
                        existing_cost = st.session_state.positions.get(ticker, {}).get('avg_cost', 0) * st.session_state.positions.get(ticker, {}).get('quantity', 0)
                        
                        if (existing_cost + net_cost) > COST_LIMIT_PER_TICKER:
                            st.error("❌ 違反風控：單一標的成本上限 4,000 萬！")
                        elif net_cost > st.session_state.cash:
                            st.error("❌ 資金不足")
                        else:
                            st.session_state.cash -= net_cost
                            pos = st.session_state.positions.get(ticker, {'quantity': 0, 'avg_cost': 0, 'type': 'ETF' if is_etf else '股票'})
                            new_q = pos['quantity'] + qty
                            pos['avg_cost'] = ((pos['avg_cost'] * pos['quantity']) + net_cost) / new_q
                            pos['quantity'] = new_q
                            st.session_state.positions[ticker] = pos
                            st.session_state.trades.append({"日期": trade_date, "動作": "買進", "代號": ticker, "名稱": name, "價格": price, "數量": qty, "理由": reason, "計價": exec_note})
                            st.rerun()

                    if sell:
                        if ticker not in st.session_state.positions or st.session_state.positions[ticker]['quantity'] < qty:
                            st.error("❌ 庫存不足")
                        else:
                            # [span_19](start_span)稅率判定[span_19](end_span)
                            if not is_etf: tax_rate = 0.003
                            elif ticker.endswith('B'): tax_rate = 0.0
                            else: tax_rate = 0.001
                            
                            tax = int(base * tax_rate)
                            net_recv = base - fee - tax
                            st.session_state.cash += net_recv
                            st.session_state.realized_pnl += (net_recv - (st.session_state.positions[ticker]['avg_cost'] * qty))
                            st.session_state.positions[ticker]['quantity'] -= qty
                            if st.session_state.positions[ticker]['quantity'] == 0: del st.session_state.positions[ticker]
                            st.session_state.trades.append({"日期": trade_date, "動作": "賣出", "代號": ticker, "名稱": name, "價格": price, "數量": qty, "理由": reason, "計價": exec_note})
                            st.rerun()

# ==========================================
# 7. [span_20](start_span)庫存管理與 30% 強制停損警示[span_20](end_span)
# ==========================================
with l_col:
    tab1, tab2 = st.tabs(["📊 當前庫存部位", "📝 交易日誌紀錄"])
    with tab1:
        if st.session_state.positions:
            pos_data = []
            for t, p in st.session_state.positions.items():
                cur_p = st.session_state.market_prices.get(t, {}).get('price', p['avg_cost'])
                ratio = (cur_p / p['avg_cost']) - 1
                un_pnl = (cur_p - p['avg_cost']) * p['quantity']
                
                # [span_21](start_span)30% 停損警告[span_21](end_span)
                warning = "⚠️ 強制平倉" if ratio <= -0.3 else "正常"
                
                pos_data.append({
                    "代號": t, "名稱": st.session_state.market_prices.get(t, {}).get('name', '-'),
                    "均價": round(p['avg_cost'], 2), "現價": cur_p, "報酬率": f"{ratio:.2%}", "損益": round(un_pnl), "狀態": warning
                })
            st.dataframe(pd.DataFrame(pos_data), use_container_width=True, hide_index=True)
            
            if any(d['狀態'] == "⚠️ 強制平倉" for d in pos_data):
                [span_22](start_span)st.error("🚨 偵測到個別標的損失達 30%，請於下一個交易日執行強制出清！[span_22](end_span)")
                
    with tab2:
        if st.session_state.trades:
            st.dataframe(pd.DataFrame(st.session_state.trades)[::-1], use_container_width=True, hide_index=True)

# ==========================================
# 8. 每日結算按鈕 (計算 Sharpe/MDD)
# ==========================================
st.divider()
if st.button("📥 執行每日結算 (EOD Process)"):
    today = datetime.now().strftime('%m/%d')
    st.session_state.daily_history.append({"date": today, "equity": current_equity})
    sharpe, mdd = calculate_metrics()
    st.success(f"結算完成！ Sharpe Ratio: {sharpe:.2f} | Max Drawdown: {mdd:.2%}")
