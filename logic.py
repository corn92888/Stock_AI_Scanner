import yfinance as yf
import pandas as pd
import numpy as np

def get_stock_data(ticker, suffix=None, period="2y"):
    """下載股價數據 (強制使用真實收盤價)"""
    ticker = str(ticker).strip().upper().replace('.TW', '').replace('.TWO', '')
    targets = [f"{ticker}.{suffix}"] if suffix else [f"{ticker}.TW", f"{ticker}.TWO"]
    for target in targets:
        try:
            # auto_adjust=False 確保與券商 App 報價一致
            df = yf.download(target, period=period, progress=False, auto_adjust=False)
            if not df.empty:
                if isinstance(df.columns, pd.MultiIndex): 
                    df.columns = [col[0] for col in df.columns]
                
                df = df.loc[:, ~df.columns.duplicated()]
                for c in ['Open', 'High', 'Low', 'Close', 'Volume']:
                    if c in df.columns: df[c] = pd.to_numeric(df[c], errors='coerce')
                
                df.dropna(subset=['Close'], inplace=True)
                return df
        except:
            pass
    return None

# --- 技術指標計算 ---

def calculate_supertrend_manual(df, period, multiplier):
    if len(df) < period: return None
    high, low, close = df['High'], df['Low'], df['Close']
    tr = pd.concat([high - low, abs(high - close.shift(1)), abs(low - close.shift(1))], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    hl2 = (high + low) / 2
    final_upper, final_lower, trend = [0.0] * len(df), [0.0] * len(df), [1] * len(df)
    cl, bu, bl = close.values, (hl2 + multiplier * atr).values, (hl2 - multiplier * atr).values
    
    for i in range(period, len(df)):
        final_upper[i] = bu[i] if bu[i] < final_upper[i-1] or cl[i-1] > final_upper[i-1] else final_upper[i-1]
        final_lower[i] = bl[i] if bl[i] > final_lower[i-1] or cl[i-1] < final_lower[i-1] else final_lower[i-1]
        trend[i] = -1 if trend[i-1]==1 and cl[i] < final_lower[i-1] else (1 if trend[i-1]==-1 and cl[i] > final_upper[i-1] else trend[i-1])
    line = [final_lower[i] if trend[i] == 1 else final_upper[i] for i in range(len(df))]
    return pd.DataFrame({'Trend': trend, 'Line': line}, index=df.index)

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_indicators(df):
    if df is None or len(df) < 200: return None
    data = df.copy()
    try:
        data['MA20'] = data['Close'].rolling(20).mean()
        data['MA60'] = data['Close'].rolling(60).mean()
        data['MA200'] = data['Close'].rolling(200).mean()
        data['BBM'] = data['MA20']
        data['Vol_MA5'] = data['Volume'].rolling(5).mean()
        data['Vol_MA20'] = data['Volume'].rolling(20).mean()
        
        st1, st2, st3 = calculate_supertrend_manual(data, 10, 1), calculate_supertrend_manual(data, 11, 2), calculate_supertrend_manual(data, 12, 3)
        if st1 is None: return None
        data['ST1'], data['ST2'], data['ST3'] = st1['Trend'], st2['Trend'], st3['Trend']
        data['ST1_Trend'], data['ST2_Trend'], data['ST3_Trend'] = st1['Trend'], st2['Trend'], st3['Trend']
        data['ST1_Line'], data['ST2_Line'], data['ST3_Line'] = st1['Line'], st2['Line'], st3['Line']
        
        data['RSI'] = calculate_rsi(data['Close'])
        exp1 = data['Close'].ewm(span=12, adjust=False).mean()
        exp2 = data['Close'].ewm(span=26, adjust=False).mean()
        data['MACD'] = exp1 - exp2
        data['Signal'] = data['MACD'].ewm(span=9, adjust=False).mean()
        data['MACD_hist'] = data['MACD'] - data['Signal']
        
        tr = pd.concat([data['High'] - data['Low'], abs(data['High'] - data['Close'].shift(1)), abs(data['Low'] - data['Close'].shift(1))], axis=1).max(axis=1)
        data['ATR'] = tr.rolling(14).mean()
        
        data['High60'], data['Low60'] = data['High'].rolling(60).max(), data['Low'].rolling(60).min()
        data['High20'], data['Low20'] = data['High'].rolling(20).max(), data['Low'].rolling(20).min()
        data['High120'], data['Low120'] = data['High'].rolling(120).max(), data['Low'].rolling(120).min()
        
        data.dropna(inplace=True)
        return data
    except: return None

# --- 策略 A: 順勢突破 ---
def check_trend_strict(df):
    try:
        last, prev = df.iloc[-1], df.iloc[-2]
        pct_change = (last['Close'] - prev['Close']) / prev['Close'] * 100
        st_score = (1 if last['ST1']==1 else 0) + (1 if last['ST2']==1 else 0) + (1 if last['ST3']==1 else 0)
        
        cond_st = (st_score >= 2)
        cond_bb_break = (prev['Close'] <= prev['BBM'] or prev['Open'] < prev['BBM']) and (last['Close'] > last['BBM'])
        cond_vol = last['Volume'] > last['Vol_MA5']
        cond_slope = last['MA20'] >= prev['MA20']
        cond_bias = (last['Close'] - last['MA20']) / last['MA20'] <= 0.10
        cond_liq = last['Volume'] > 500000 # 至少 500 張
        
        match = cond_st and cond_bb_break and cond_vol and cond_slope and cond_bias and cond_liq
        stop_loss = last['Close'] - (1.5 * last['ATR'])
        
        return match, f"{st_score}線綠+未過熱", pct_change, stop_loss
    except: return False, "", 0.0, 0.0

# --- 策略 B: 逆勢抄底 ---
def check_reversal_strict(df):
    try:
        if len(df) < 60: return False, "", 0.0, 0.0
        last, prev = df.iloc[-1], df.iloc[-2]
        pct_change = (last['Close'] - prev['Close']) / prev['Close'] * 100
        
        range_60 = last['High60'] - last['Low60']
        position_pct = (last['Close'] - last['Low60']) / range_60 * 100
        cond_low_pos = position_pct <= 35 
        cond_liq = last['Volume'] > 500000 # 修正過濾：需大於 500 張 (50萬股)
        
        support_line = df['Low'].iloc[-25:-5].min()
        break_days_count = sum(df['Low'].iloc[-5:-1] < support_line)
        cond_break_bottom = break_days_count >= 2  
        cond_reclaim_support = last['Close'] > support_line
        # 放寬條件：三天內把跌破的綠棒(黑K)吞噬，或今天反彈 >= 3%
        cond_engulfing = (last['Close'] > df['High'].iloc[-4:-1].max()) or (last['Close'] > prev['High']) or (pct_change >= 3.0)
        cond_macd_improve = last['MACD_hist'] > prev['MACD_hist']
        
        match = cond_low_pos and cond_liq and cond_break_bottom and cond_reclaim_support and cond_engulfing and cond_macd_improve
        stop_loss = support_line * 0.99
        
        return match, f"破底翻+MACD收斂(位階{int(position_pct)}%)", pct_change, stop_loss
    except: return False, "", 0.0, 0.0

# --- 策略 C: 波段蓄勢 (VCP) ---
def check_wave_strict(df):
    try:
        last, prev = df.iloc[-1], df.iloc[-2]
        pct_change = (last['Close'] - prev['Close']) / prev['Close'] * 100
        
        cond_ma_align = (last['Close'] > last['MA20']) and (last['MA20'] > last['MA60'])
        volatility = (last['High20'] - last['Low20']) / last['Close']
        cond_vcp = volatility < 0.15 
        cond_vol_dry = last['Volume'] < last['Vol_MA20']
        cond_liq = last['Volume'] > 500000 # 修正過濾：需大於 500 張 (50萬股)
        cond_above_200ma = last['Close'] > last['MA200']
        cond_runup = (last['High120'] / last['Low120']) >= 1.20
        
        match = cond_ma_align and cond_vcp and cond_vol_dry and cond_liq and cond_above_200ma and cond_runup
        stop_loss = last['Low20']
        
        return match, f"VCP({int(volatility*100)}%)活魚量縮", pct_change, stop_loss
    except: return False, "", 0.0, 0.0
