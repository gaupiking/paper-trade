# Paper Trade Platform - Streamlit 版

這是一個以 Python + Streamlit 製作的 paper trade 模擬交易平台，支援：

- 股票投資組
- ETF 投資組
- 委託單建立
- CSV 匯入
- 交易日誌
- EOD 每日收盤處理
- 每日績效與持股報表
- 警示與停權狀態檢視

---

## 1. 專案結構

```text
paper-trade-streamlit/
├─ streamlit_app.py
├─ requirements.txt
├─ README.md
├─ data/
│  ├─ securities_template.csv
│  ├─ prices_template.csv
│  └─ orders_template.csv
└─ uploads/
   └─ .gitkeep
