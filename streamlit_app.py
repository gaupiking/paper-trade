import streamlit as st
import pandas as pd
import time
from playwright.sync_api import sync_playwright

# --- 網頁基本設定 ---
st.set_page_config(page_title="1688 快速比價系統", page_icon="🛒", layout="wide")

def scrape_1688_data(keyword, max_items=10):
    """
    使用 Playwright 同步抓取 1688 資料
    """
    results = []
    
    # 啟動 Playwright
    with sync_playwright() as p:
        # headless=False 讓你可以看到真實瀏覽器，如果遇到滑塊或登入可以手動處理
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        try:
            url = f"https://s.1688.com/selloffer/offer_search.htm?keywords={keyword}"
            page.goto(url, wait_until="domcontentloaded")
            
            # 給予足夠的時間讓 JavaScript 渲染，或讓你手動處理驗證碼
            time.sleep(5) 
            
            # 抓取商品卡片
            products = page.query_selector_all('.sm-offer-item')
            
            if not products:
                st.error("⚠️ 找不到商品，可能觸發了登入牆或滑塊驗證！請在彈出的瀏覽器中手動處理後再試。")
                return []

            for i, product in enumerate(products[:max_items]):
                # 抓取標題
                title_el = product.query_selector('.sm-offer-title')
                title = title_el.inner_text().strip() if title_el else "無標題"
                
                # 抓取價格 (並過濾掉 '¥' 符號轉為浮點數方便比價)
                price_el = product.query_selector('.sm-offer-priceNum')
                price_str = price_el.inner_text().replace('¥', '').strip() if price_el else "0"
                try:
                    price = float(price_str)
                except ValueError:
                    price = 0.0
                
                # 抓取公司名稱
                company_el = product.query_selector('.sm-offer-companyName')
                company = company_el.inner_text().strip() if company_el else "無公司名稱"
                
                # 抓取商品連結
                link_el = product.query_selector('a.sm-offer-photoLink')
                link = link_el.get_attribute('href') if link_el else ""
                if link and not link.startswith('http'):
                    link = "https:" + link

                results.append({
                    "商品名稱": title,
                    "價格 (RMB)": price,
                    "供應商": company,
                    "商品連結": link
                })
                
        except Exception as e:
            st.error(f"抓取過程中發生錯誤: {e}")
        finally:
            browser.close()
            
    return results

# --- Streamlit UI 介面 ---
st.title("🛒 1688 快速比價與資料抓取系統")
st.markdown("輸入關鍵字後，系統將呼叫本地瀏覽器抓取最新商品資訊，並自動進行價格排序。")

st.divider()

# 側邊欄：搜尋條件設定
with st.sidebar:
    st.header("🔍 搜尋設定")
    keyword = st.text_input("搜尋關鍵字", value="保溫杯")
    max_items = st.slider("最大抓取數量", min_value=5, max_value=40, value=15, step=5)
    start_search = st.button("開始抓取與比價", type="primary")

# 主畫面：顯示結果
if start_search:
    if not keyword:
        st.warning("請輸入搜尋關鍵字！")
    else:
        with st.spinner(f"正在啟動瀏覽器前往 1688 搜尋「{keyword}」... 請不要關閉彈出的瀏覽器視窗！"):
            data = scrape_1688_data(keyword, max_items)
            
        if data:
            st.success(f"✅ 成功抓取 {len(data)} 筆商品資料！")
            
            # 將資料轉換為 Pandas DataFrame 以利分析
            df = pd.DataFrame(data)
            
            # 依價格由低到高排序
            df_sorted = df.sort_values(by="價格 (RMB)", ascending=True).reset_index(drop=True)
            
            # 建立三個統計指標
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("最低價格", f"¥ {df_sorted['價格 (RMB)'].min():.2f}")
            with col2:
                st.metric("平均價格", f"¥ {df_sorted['價格 (RMB)'].mean():.2f}")
            with col3:
                st.metric("最高價格", f"¥ {df_sorted['價格 (RMB)'].max():.2f}")
            
            st.subheader("📊 比價結果清單 (由低至高)")
            
            # 使用 Streamlit 原生的 dataframe 顯示，可以自訂欄位格式
            st.dataframe(
                df_sorted,
                column_config={
                    "商品連結": st.column_config.LinkColumn("點擊前往"),
                    "價格 (RMB)": st.column_config.NumberColumn("價格 (RMB)", format="¥ %.2f")
                },
                use_container_width=True,
                hide_index=True
            )
            
            # 提供 CSV 下載功能
            csv = df_sorted.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="📥 下載比價結果 (CSV)",
                data=csv,
                file_name=f'1688_{keyword}_比價結果.csv',
                mime='text/csv',
            )
