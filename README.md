# 📈 STP 青年投資行為模擬平台 (Paper Trade)

[span_1](start_span)這是一個專為「STP 種子人才培訓計畫」打造的專屬金融模擬交易平台。以法人級交易室的規格，協助學員驗證股票與 ETF 的投資行為、風險承擔與交易紀律[span_1](end_span)。

## 🌟 系統核心特色

1. **法人級風控 (Pre-trade Risk Control)**
   - [span_2](start_span)內建下單攔截機制：單一標的買進成本嚴格控管於上限 **4,000 萬元**以內[span_2](end_span)。
   - [span_3](start_span)強制停損警示：系統自動監測庫存，當單一標的未實現損失達 30% 時，發出強制出清警告[span_3](end_span)。
   - [span_4](start_span)帳戶破產防線：累計虧損達 2,000 萬元時提示停止交易[span_4](end_span)。
2. **智能稅費精算**
   - [span_5](start_span)採用銀行法人單手續費率 (0.04%)[span_5](end_span)。
   - [span_6](start_span)依據標的代號自動判斷並計算證交稅：台灣上市櫃股票 0.3%、一般上市櫃(非債券型) ETF 0.1%、債券型 ETF 暫停課徵 (0%)[span_6](end_span)。
3. **操盤手行為視覺化分析**
   - **資產配置比例**：即時追蹤現金、股票、一般型 ETF 與債券型 ETF 的持倉水位。
   - **帳戶淨值走勢**：支援每日收盤淨值結算，繪製專屬的 Equity Curve (歷史每日收盤走勢)。
4. **極簡存讀檔架構 (無資料庫設計)**
   - 採用 JSON 檔案進行進度存檔與載入，學員可自主掌握交易紀錄，保障資料隱私。

## 🚀 部署與執行方式

本系統基於 Python 與 [Streamlit](https://streamlit.io/) 框架開發，支援全雲端運行。

### 雲端部署 (Streamlit Community Cloud)
1. 將本專案同步至 GitHub 儲存庫。
2. 前往 Streamlit Community Cloud 綁定 GitHub 帳號。
3. 建立 New App，選擇本儲存庫的 `streamlit_app.py`，點擊 Deploy 即可一鍵上線。

### 本機端單機測試
1. 安裝環境依賴：
   ```bash
   pip install -r requirements.txt
