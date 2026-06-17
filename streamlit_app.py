import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
import pandas as pd
from datetime import datetime, timedelta

# 1. 網頁基本設定
st.set_page_config(page_title="AI 股票分析儀表板", layout="wide")
import streamlit as st

st.set_page_config(page_title="專業投資儀表板", layout="wide")

# 隱藏 Streamlit 官方選單與右下角浮水印的 CSS
hide_style = """
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    .viewerBadge_container__1QS1h {display: none !important;} /* 隱藏 Manage app */
    stDecoration {display: none !important;}
    </style>
"""
st.markdown(hide_style, unsafe_allow_html=True)

st.title("📈 互動式股票分析與回測工具")
st.write("這是一個利用 Streamlit 快速建立的股票分析與可視化工具。")

# 2. 側邊欄控制項 (Sidebar)
st.sidebar.header("⚙️ 參數設定")
ticker = st.sidebar.text_input("輸入股票代號 (美股)", value="AAPL").upper()

# 日期選擇（預設看過去一年）
start_date = st.sidebar.date_input("開始日期", datetime.now() - timedelta(days=365))
end_date = st.sidebar.date_input("結束日期", datetime.now())

# 技術指標參數
ma_fast = st.sidebar.slider("短均線 (MA) 天數", min_value=5, max_value=50, value=20)
ma_slow = st.sidebar.slider("長均線 (MA) 天數", min_value=10, max_value=200, value=60)

# 3. 抓取資料 (加上快取機制以提升效能)
@st.cache_data
def load_data(stock_ticker, start, end):
    data = yf.download(stock_ticker, start=start, end=end)
    return data

try:
    with st.spinner("資料載入中..."):
        df = load_data(ticker, start_date, end_date)
    
    if df.empty:
        st.error("找不到該股票資料，請檢查代號是否正確。")
    else:
        # 【修正】處理新版 yfinance 的多重索引欄位問題
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        
        # 確保關鍵欄位都是乾淨的數字
        for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        # 計算移動平均線
        df['MA_Fast'] = df['Close'].rolling(window=ma_fast).mean()
        df['MA_Slow'] = df['Close'].rolling(window=ma_slow).mean()

        # 4. 頂部關鍵指標區塊 (st.columns & st.metric)
        last_row = df.iloc[-1]
        prev_row = df.iloc[-2]
        
        last_price = float(last_row['Close'])
        prev_price = float(prev_row['Close'])
        price_change = last_price - prev_price
        price_change_pct = (price_change / prev_price) * 100
        
        max_price = float(df['High'].max())
        min_price = float(df['Low'].min())
        last_volume = int(last_row['Volume'])

        col1, col2, col3, col4 = st.columns(4)
        col1.metric(label=f"{ticker} 最新收盤價", value=f"${last_price:.2f}", delta=f"{price_change:.2f} ({price_change_pct:.2f}%)")
        col2.metric(label="期間最高價", value=f"${max_price:.2f}")
        col3.metric(label="期間最低價", value=f"${min_price:.2f}")
        col4.metric(label="當日成交量", value=f"{last_volume:,}")

        st.markdown("---")

        # 5. 分頁設計 (st.tabs)
        tab1, tab2, tab3 = st.tabs(["📊 互動式 K 線圖", "📋 歷史數據摘要", "🤖 策略簡單回測"])

        with tab1:
            st.subheader("技術分析 K 線圖 (Candlestick)")
            
            # 使用 Plotly 繪製 K 線圖與均線
            fig = go.Figure()
            # K線
            fig.add_trace(go.Candlestick(
                x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name='K線'
            ))
            # 短均線
            fig.add_trace(go.Scatter(x=df.index, y=df['MA_Fast'], mode='lines', name=f'{ma_fast} MA', line=dict(color='orange')))
            # 長均線
            fig.add_trace(go.Scatter(x=df.index, y=df['MA_Slow'], mode='lines', name=f'{ma_slow} MA', line=dict(color='blue')))
            
            fig.update_layout(xaxis_rangeslider_visible=False, height=600, template="plotly_dark")
            st.plotly_chart(fig, use_container_width=True)

        with tab2:
            st.subheader("原始數據檢視")
            st.write("你可以點擊表格欄位進行排序，或點擊右上角下載為 CSV。")
            
            # 依日期最新到最舊排序顯示
            df_display = df.sort_index(ascending=False)
            st.dataframe(df_display, use_container_width=True)

        with tab3:
            st.subheader("黃金交叉 / 死亡交叉 策略提示")
            # 簡單的均線邏輯判斷
            if df['MA_Fast'].iloc[-1] > df['MA_Slow'].iloc[-1]:
                st.success(f"💡 當前狀態：短天期均線 ({ma_fast}MA) **高於** 長天期均線 ({ma_slow}MA)。技術面上屬於**多頭趨勢 (黃金交叉)**。")
            else:
                st.warning(f"⚠️ 當前狀態：短天期均線 ({ma_fast}MA) **低於** 長天期均線 ({ma_slow}MA)。技術面上屬於**空頭趨勢 (死亡交叉)**。")
                
            st.info("註：此工具僅供學術與技術展示，不構成任何投資建議。")

except Exception as e:
    st.sidebar.error(f"發生錯誤: {e}")
    st.write("請在左側輸入有效的美股代號（如：AAPL, TSLA, NVDA, MSFT）。")
