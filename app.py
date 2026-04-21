import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np
import os
import glob
import time
from logic import get_stock_data, calculate_indicators, check_trend_strict, check_reversal_strict, check_wave_strict

st.set_page_config(page_title="玉米的大噴射台股 💦", layout="wide", page_icon="💦")

# 側邊欄導覽
st.sidebar.title("💦 玉米的大噴射台股")
page = st.sidebar.radio("切換功能", ["📊 歷史報表預覽 (Reports)", "🎯 個股高階圖表分析 (Charts)", "📰 精選動態新聞 (News)"])

if page == "📊 歷史報表預覽 (Reports)":
    st.title(" 歷史選股報表預覽")
    st.write("預覽系統每日/盤中產出的自動化選股 Excel 報表。")
    
    with st.expander("🛠️ 手動執行全自動掃描器 (約需 1~2 分鐘)"):
        st.write("點擊下方按鈕將立刻動用主機資源，對全台股 1900+ 檔股票進行技術線型掃描：")
        col1, col2 = st.columns(2)
        if col1.button("🚀 執行盤後高防禦掃描 (EOD)", use_container_width=True):
            with st.spinner("正在執行最嚴格的盤後防禦檢查，這將花費 1~3 分鐘，請耐心等候..."):
                import scanner
                scanner.run_scanner()
                st.success("✅ 盤後掃描完成！最新報表已自動產出。")
                st.balloons()
                time.sleep(2)
                st.rerun()
                
        if col2.button("⚡ 執行盤中即時狙擊 (Intraday)", use_container_width=True):
            with st.spinner("正在執行盤中狙擊，強制回補 twstock 最新即時報價，請等候..."):
                import intraday_scanner
                intraday_scanner.run_scanner()
                st.success("✅ 盤中狙擊完成！最新報表已自動產出。")
                st.balloons()
                time.sleep(2)
                st.rerun()
    st.markdown("---")
    
    if not os.path.exists('Reports'):
        st.warning("⚠️ 尚未找到 Reports 資料夾，您可以點擊上方的按鈕立即執行掃描。")
    else:
        # 列出所有 xlsx
        files = glob.glob('Reports/*.xlsx')
        if not files:
            st.info("💡 尚未產出任何報表。")
        else:
            files.sort(reverse=True) # 用時間倒序排序
            file_options = {os.path.basename(f): f for f in files}
            selected_file = st.selectbox("選擇要檢視的報表", list(file_options.keys()))
            
            if selected_file:
                target_path = file_options[selected_file]
                try:
                    # 讀取 Excel 的所有 sheet
                    xl = pd.ExcelFile(target_path)
                    sheet_names = xl.sheet_names
                    
                    selected_sheet = st.radio("選擇策略分頁", sheet_names, horizontal=True)
                    df_sheet = pd.read_excel(target_path, sheet_name=selected_sheet)
                    
                    st.dataframe(df_sheet, use_container_width=True, hide_index=True)
                except Exception as e:
                    st.error(f"❌ 無法讀取該報表: {str(e)}")

elif page == "🎯 個股高階圖表分析 (Charts)":
    st.title("🎯 個股高階圖表分析")
    st.write("輸入台股代碼，系統將自動套用三大策略進行診斷，並畫出關鍵技術指標。")
    
    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        ticker = st.text_input("輸入股票代號 (如: 2330)", "2330")
    with col2:
        period = st.selectbox("圖表歷史區間", ["6mo", "1y", "2y"], index=1)
    
    if st.button("🚀 執行深度分析", type="primary"):
        with st.spinner(f"正在分析 {ticker}..."):
            df_raw = get_stock_data(ticker, period=period)
            
            if df_raw is None or df_raw.empty:
                st.error("❌ 找不到該股票資料，請確認代號是否正確。")
            else:
                df = calculate_indicators(df_raw)
                
                # 套用三大策略
                is_trend, note_trend, p_t, sl_t = check_trend_strict(df)
                is_rev, note_rev, p_r, sl_r = check_reversal_strict(df)
                is_wave, note_wave, p_w, sl_w = check_wave_strict(df)
                
                st.subheader("🤖 策略診斷結果 (以最後一日為準)")
                c1, c2, c3 = st.columns(3)
                
                c1.info(f"**A. 順勢突破**\n\n狀態: {'✅ 符合' if is_trend else '❌ 不符'}\n\n原因: {note_trend}")
                c2.warning(f"**B. 逆勢抄底**\n\n狀態: {'✅ 符合' if is_rev else '❌ 不符'}\n\n原因: {note_rev}")
                c3.success(f"**C. 波段蓄勢**\n\n狀態: {'✅ 符合' if is_wave else '❌ 不符'}\n\n原因: {note_wave}")
                
                st.markdown("---")
                
                # 繪圖
                st.subheader("📈 技術線圖")
                fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], vertical_spacing=0.03)
                
                # 1. K線圖
                fig.add_trace(go.Candlestick(
                    x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], 
                    name='K線', increasing_line_color='red', decreasing_line_color='green'
                ), row=1, col=1)
                
                # 2. 均線
                fig.add_trace(go.Scatter(x=df.index, y=df['MA20'], line=dict(color='orange', width=1.5), name='20MA (月線)'), row=1, col=1)
                fig.add_trace(go.Scatter(x=df.index, y=df['MA60'], line=dict(color='blue', width=1.5), name='60MA (季線)'), row=1, col=1)
                fig.add_trace(go.Scatter(x=df.index, y=df['MA200'], line=dict(color='purple', width=2, dash='dash'), name='200MA (年線)'), row=1, col=1)
                
                # 3. SuperTrend 三線 (如果欄位存在)
                if 'ST1_Line' in df.columns:
                    st1_c = ['green' if t==1 else 'red' for t in df['ST1_Trend']]
                    fig.add_trace(go.Scatter(x=df.index, y=df['ST1_Line'], mode='markers', marker=dict(color=st1_c, size=3), name='ST(10,1)'), row=1, col=1)
                if 'ST2_Line' in df.columns:
                    st2_c = ['green' if t==1 else 'red' for t in df['ST2_Trend']]
                    fig.add_trace(go.Scatter(x=df.index, y=df['ST2_Line'], mode='markers', marker=dict(color=st2_c, size=3), name='ST(11,2)'), row=1, col=1)
                if 'ST3_Line' in df.columns:
                    st3_c = ['green' if t==1 else 'red' for t in df['ST3_Trend']]
                    fig.add_trace(go.Scatter(x=df.index, y=df['ST3_Line'], mode='markers', marker=dict(color=st3_c, size=3), name='ST(12,3)'), row=1, col=1)

                # 4. 成交量
                colors = ['green' if c < o else 'red' for o, c in zip(df['Open'], df['Close'])]
                fig.add_trace(go.Bar(x=df.index, y=df['Volume'], marker_color=colors, name='成交量'), row=2, col=1)
                if 'Vol_MA20' in df.columns:
                    fig.add_trace(go.Scatter(x=df.index, y=df['Vol_MA20'], line=dict(color='orange', width=2), name='20日均量'), row=2, col=1)
                
                fig.update_layout(
                    height=700, 
                    xaxis_rangeslider_visible=False,
                    margin=dict(l=0, r=0, t=30, b=0),
                    plot_bgcolor='white',
                    paper_bgcolor='white',
                    hovermode='x unified'
                )
                
                # Update axes to look modern
                fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor='LightGray')
                fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor='LightGray')
                
                st.plotly_chart(fig, use_container_width=True)

elif page == "📰 精選動態新聞 (News)":
    st.title("📰 精選動態新聞")
    st.write("自動抓取最新一份選股名單的相關財經新聞，助你快速掌握基本面脈動與市場風向。")
    
    if not os.path.exists('Reports'):
        st.warning("⚠️ 尚未找到 Reports 資料夾，請先執行掃描。")
    else:
        files = glob.glob('Reports/*.xlsx')
        if not files:
            st.info("💡 尚未產出任何報表。")
        else:
            latest_file = max(files, key=os.path.getctime)
            st.success(f"📂 目前顯示基準報表：{os.path.basename(latest_file)}")
            
            try:
                from llm_agent import fetch_google_news
                xl = pd.ExcelFile(latest_file)
                
                for sheet in xl.sheet_names:
                    df = pd.read_excel(latest_file, sheet_name=sheet)
                    if df.empty: continue
                    
                    st.subheader(f"📌 策略：{sheet}")
                    # 依序抓取清單上所有股票的新聞
                    for _, row in df.iterrows():
                        code = str(row['代號'])
                        name = row['名稱']
                        
                        with st.expander(f"🏭 {name} ({code}) - 近期焦點新聞", expanded=False):
                            news_list = fetch_google_news(f"{code} {name}", limit=5)
                            if news_list:
                                for news in news_list:
                                    st.markdown(news)
                            else:
                                st.write("近期無重大相關新聞。")
                    st.markdown("---")
            except Exception as e:
                st.error(f"❌ 讀取發生錯誤: {e}")