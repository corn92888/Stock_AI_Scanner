import yfinance as yf
import pandas as pd
import numpy as np

def get_stock_data(ticker, period="1y"):
    ticker = str(ticker).strip().upper().replace('.TW', '').replace('.TWO', '')
    target = f"{ticker}.TW"
    try:
        df = yf.download(target, period=period, progress=False)
        if df.empty:
            target = f"{ticker}.TWO"
            df = yf.download(target, period=period, progress=False)
        if df.empty: return None
        if isinstance(df.columns, pd.MultiIndex): df.columns = [col[0] for col in df.columns]
        df = df.loc[:, ~df.columns.duplicated()]
        for c in ['Open', 'High', 'Low', 'Close', 'Volume']:
            if c in df.columns: df[c] = pd.to_numeric(df[c], errors='coerce')
        df.dropna(subset=['Close'], inplace=True)
        return df
    except: return None

# --- 指標計算 ---

def calculate_supertrend_manual(df, period, multiplier):
    if len(df) < period: return None
    high, low, close = df['High'], df['Low'], df['Close']
    tr = pd.concat([high - low, abs(high - close.shift(1)), abs(low - close.shift(1))], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    hl2 = (high + low) / 2
    final_upper = [0.0] * len(df)
    final_lower = [0.0] * len(df)
    trend = [1] * len(df)
    cl, bu, bl = close.values, (hl2 + multiplier * atr).values, (hl2 - multiplier * atr).values
    for i in range(period, len(df)):
        final_upper[i] = bu[i] if bu[i] < final_upper[i-1] or cl[i-1] > final_upper[i-1] else final_upper[i-1]
        final_lower[i] = bl[i] if bl[i] > final_lower[i-1] or cl[i-1] < final_lower[i-1] else final_lower[i-1]
        trend[i] = -1 if trend[i-1]==1 and cl[i] < final_lower[i-1] else (1 if trend[i-1]==-1 and cl[i] > final_upper[i-1] else trend[i-1])
    return pd.DataFrame({'Trend': trend}, index=df.index)

def calculate_kd(df, n=9):
    low_min = df['Low'].rolling(n).min()
    high_max = df['High'].rolling(n).max()
    rsv = (df['Close'] - low_min) / (high_max - low_min) * 100
    k, d = [50.0]*len(df), [50.0]*len(df)
    rsv_val = rsv.values
    for i in range(n, len(df)):
        curr = 50.0 if np.isnan(rsv_val[i]) else rsv_val[i]
        k[i] = (2/3)*k[i-1] + (1/3)*curr
        d[i] = (2/3)*d[i-1] + (1/3)*k[i]
    return pd.Series(k, index=df.index), pd.Series(d, index=df.index)

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def check_k_pattern(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]
    body = abs(last['Close'] - last['Open'])
    upper = last['High'] - max(last['Close'], last['Open'])
    lower = min(last['Close'], last['Open']) - last['Low']
    is_hammer = (lower >= body * 1.5) and (upper <= body * 0.8)
    is_engulfing = (prev['Close'] < prev['Open']) and (last['Close'] > last['Open']) and \
                   (last['Open'] <= prev['Close']) and (last['Close'] >= prev['Open'])
    if is_hammer: return True, "🔨錘頭線"
    if is_engulfing: return True, "🕯️陽包陰"
    return False, "無"

def calculate_indicators(df):
    if df is None or len(df) < 65: return None
    data = df.copy()
    try:
        data['MA20'] = data['Close'].rolling(20).mean() # 月線
        data['MA60'] = data['Close'].rolling(60).mean() # 季線
        data['BBM'] = data['MA20'] # 布林中軌就是20MA
        data['Vol_MA5'] = data['Volume'].rolling(5).mean()
        data['Vol_MA20'] = data['Volume'].rolling(20).mean() # 20日均量 (判斷量縮用)
        
        st1 = calculate_supertrend_manual(data, 10, 1)
        st2 = calculate_supertrend_manual(data, 11, 2)
        st3 = calculate_supertrend_manual(data, 12, 3)
        if st1 is None: return None
        data['ST1'], data['ST2'], data['ST3'] = st1['Trend'], st2['Trend'], st3['Trend']
        
        data['K'], data['D'] = calculate_kd(data)
        data['RSI'] = calculate_rsi(data['Close'])
        
        # 計算 60日 高低點 (位階用)
        data['High60'] = data['High'].rolling(60).max()
        data['Low60'] = data['Low'].rolling(60).min()
        
        # 計算 20日 高低點 (VCP 波動率壓縮用)
        data['High20'] = data['High'].rolling(20).max()
        data['Low20'] = data['Low'].rolling(20).min()

        data.dropna(inplace=True)
        return data
    except: return None

# --- 策略 A: 順勢突破 ---
def check_trend_strict(df):
    try:
        last = df.iloc[-1]
        prev = df.iloc[-2]
        pct_change = (last['Close'] - prev['Close']) / prev['Close'] * 100
        
        st_score = (1 if last['ST1']==1 else 0) + (1 if last['ST2']==1 else 0) + (1 if last['ST3']==1 else 0)
        cond_st = (st_score >= 2)
        cond_bb_break = (prev['Close'] <= prev['BBM'] or prev['Open'] < prev['BBM']) and (last['Close'] > last['BBM'])
        cond_vol = last['Volume'] > last['Vol_MA5']
        cond_slope = last['MA20'] >= prev['MA20']
        cond_rsi = last['RSI'] > 50

        match = cond_st and cond_bb_break and cond_vol and cond_slope and cond_rsi
        return match, f"{st_score}線綠+20MA翻揚", pct_change
    except: return False, "", 0.0

# --- 策略 B: 逆勢抄底 (嚴謹破底翻 2-4根K棒確認版) ---
def check_reversal_strict(df):
    try:
        # 資料太少無法計算支撐，直接跳過
        if len(df) < 60: return False, "", 0.0
        
        last = df.iloc[-1]   # 今天
        prev = df.iloc[-2]   # 昨天
        pct_change = (last['Close'] - prev['Close']) / prev['Close'] * 100
        
        # 1. 位階保護：確保是在 60 日內的相對低點 (底部 35% 內)
        range_60 = last['High60'] - last['Low60']
        if range_60 == 0: return False, "", 0.0
        position_pct = (last['Close'] - last['Low60']) / range_60 * 100
        cond_low_pos = position_pct <= 35 
        
        # 2. 流動性保護：不要求爆量，但過濾掉成交量太小的殭屍股
        cond_liquidity = last['Volume'] > 500
        
        # ==========================================
        # 3. 嚴謹破底翻邏輯 (對應您的圖片)
        # ==========================================
        
        # 步驟 A: 定義「前波支撐線」(抓取 5天前 ~ 25天前 的最低點)
        support_line = df['Low'].iloc[-25:-5].min()
        
        # 步驟 B: 確認「破底區間」至少 2~4 根 K 棒
        # 檢查過去 4 天(不含今天)，有幾天的最低價跌破了支撐線
        break_days_count = sum(df['Low'].iloc[-5:-1] < support_line)
        cond_break_bottom = break_days_count >= 2  # 至少要 2 天跌破，確認是有效洗盤
        
        # 步驟 C: 確認「破底翻」站回 (入場點 1)
        # 條件 1：今天收盤價，重新強勢站回前波支撐線之上
        cond_reclaim_support = last['Close'] > support_line
        # 條件 2：今天收盤價，越過昨天的高點 (多方表態)
        cond_engulfing = last['Close'] > prev['High']
        
        is_spring = cond_break_bottom and cond_reclaim_support and cond_engulfing
        
        # 4. 總結條件
        match = cond_low_pos and cond_liquidity and is_spring
        note = f"破底翻站回 (位階{int(position_pct)}%)"
        
        return match, note, pct_change
    except Exception as e: 
        return False, "", 0.0

# --- 策略 C: 波段蓄勢 (VCP + 均線多頭) ---
def check_wave_strict(df):
    try:
        last = df.iloc[-1]
        prev = df.iloc[-2]
        pct_change = (last['Close'] - prev['Close']) / prev['Close'] * 100
        
        # 1. 多頭排列 (Trend Alignment)
        # 收盤 > 20MA > 60MA (確保大方向向上)
        cond_ma_align = (last['Close'] > last['MA20']) and (last['MA20'] > last['MA60'])
        
        # 2. 20MA 趨勢向上 (MA Slope > 0)
        cond_ma_up = last['MA20'] > prev['MA20']
        
        # 3. 波動率壓縮 (VCP)
        # 條件：近20日高低震幅 < 收盤價的 15% (代表進入箱體整理)
        # 邏輯：賣壓耗盡，價格波動變小
        volatility = (last['High20'] - last['Low20']) / last['Close']
        cond_vcp = volatility < 0.15 
        
        # 4. 量能急縮 (Volume Contraction)
        # 條件：今日成交量 < 20日均量 (整理期特色：無量)
        # 邏輯：浮額清洗乾淨
        cond_vol_dry = last['Volume'] < last['Vol_MA20']
        
        # 5. 基本流動性 (>500張即可，整理期量本來就少)
        cond_liq = last['Volume'] > 500000
        
        match = cond_ma_align and cond_ma_up and cond_vcp and cond_vol_dry and cond_liq
        
        # 回傳這檔股票目前壓縮的程度 (越小越好)
        vcp_pct = int(volatility * 100)
        note = f"VCP壓縮({vcp_pct}%) + 量縮"
        
        return match, note, pct_change
    except: return False, "", 0.0