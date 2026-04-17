import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import numpy as np
from logic import get_stock_data, calculate_indicators, check_strategy

st.set_page_config(page_title="台股 SuperTrend 戰情室", layout="wide")
st.title("📈 台股 SuperTrend 戰情室 (三線版)")

# 側邊欄
ticker = st.sidebar.text_input("輸入代號", "2330")
period = st.sidebar.selectbox("期間", ["1y", "2y", "5y"], index=0)
btn = st.sidebar.button("分析")

if btn:
    with st.spinner("正在分析..."):
        df_raw = get_stock_data(ticker, period=period)
        
        if df_raw is not None:
            df = calculate_indicators(df_raw)
            match, det = check_strategy(df)
            
            # 顯示結果
            c1, c2, c3 = st.columns(3)
            c1.metric("SuperTrend 三線", "全多頭 (綠)" if det['Three_Green'] else "未全多")
            c2.metric("布林中線突破", "Yes" if det['BB_Break'] else "No")
            c3.metric("攻擊量", "Yes" if det['Volume_Up'] else "No")
            
            # 繪圖
            fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3])
            
            # K線
            fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name='K線'), row=1, col=1)
            
            # 布林中線
            fig.add_trace(go.Scatter(x=df.index, y=df['BBM'], line=dict(color='orange', width=1, dash='dash'), name='20MA'), row=1, col=1)
            
            # SuperTrend 三線
            # ST1 短 (點狀)
            st1_c = ['green' if t==1 else 'red' for t in df['ST1_Trend']]
            fig.add_trace(go.Scatter(x=df.index, y=df['ST1_Line'], mode='markers', marker=dict(color=st1_c, size=3), name='ST短(10,1)'), row=1, col=1)
            
            # ST2 中 (實線)
            st2_c = ['green' if t==1 else 'red' for t in df['ST2_Trend']]
            fig.add_trace(go.Scatter(x=df.index, y=df['ST2_Line'], mode='markers+lines', marker=dict(color=st2_c, size=1), line=dict(width=1), name='ST中(11,2)'), row=1, col=1)

            # ST3 長 (粗線)
            # 為了美觀，這邊簡化處理，直接畫出線條
            st3_c = ['rgba(0,255,0,0.5)' if t==1 else 'rgba(255,0,0,0.5)' for t in df['ST3_Trend']]
            fig.add_trace(go.Scatter(x=df.index, y=df['ST3_Line'], mode='markers', marker=dict(color=st3_c, size=5), name='ST長(12,3)'), row=1, col=1)
            
            # 成交量
            colors = ['red' if c < o else 'green' for o, c in zip(df['Open'], df['Close'])]
            fig.add_trace(go.Bar(x=df.index, y=df['Volume'], marker_color=colors, name='Volume'), row=2, col=1)
            
            fig.update_layout(height=800, xaxis_rangeslider_visible=False)
            st.plotly_chart(fig, use_container_width=True)
            
        else:
            st.error("找不到股票，請確認代號。")
            