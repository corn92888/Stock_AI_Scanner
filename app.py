import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import numpy as np
import os
import glob
import time
import twstock
import yfinance as yf
from logic import get_stock_data, calculate_indicators, check_trend_strict, check_reversal_strict, check_wave_strict
from portfolio_store import (
    build_manual_owner_key,
    get_portfolio_backend_status,
    list_all_holdings,
    load_holdings,
    save_holdings,
    save_portfolio_snapshot,
    verify_admin_access,
)

st.set_page_config(page_title="玉米的大噴射台股 💦", layout="wide", page_icon="💦")

STRATEGY_SHEET_NAMES = ["順勢突破", "低檔爆量", "波段蓄勢"]
PORTFOLIO_INPUT_COLUMNS = ["代號", "成本", "股數", "停損價", "目標價", "備註"]


def normalize_code(value):
    if value is None or value != value:
        return ""
    code = str(value).strip().upper().replace(".TW", "").replace(".TWO", "")
    if code.endswith(".0"):
        code = code[:-2]
    return code.zfill(4) if code.isdigit() and len(code) < 4 else code


def normalize_editing_code(value):
    if value is None or value != value:
        return ""
    code = str(value).strip().upper().replace(".TW", "").replace(".TWO", "")
    if code.endswith(".0"):
        code = code[:-2]
    return code


def latest_report(patterns, exclude_keywords=None):
    exclude_keywords = exclude_keywords or []
    files = []
    for pattern in patterns:
        files.extend(glob.glob(pattern))
    files = [
        path
        for path in files
        if all(keyword not in os.path.basename(path) for keyword in exclude_keywords)
    ]
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def list_reports(patterns, exclude_keywords=None):
    exclude_keywords = exclude_keywords or []
    files = []
    for pattern in patterns:
        files.extend(glob.glob(pattern))
    files = [
        path
        for path in files
        if all(keyword not in os.path.basename(path) for keyword in exclude_keywords)
    ]
    return sorted(set(files), key=os.path.getmtime, reverse=True)


@st.cache_data(ttl=300, show_spinner=False)
def load_market_report(path):
    if not path or not os.path.exists(path):
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
    try:
        market = pd.read_excel(path, sheet_name="全市場明細")
        industry = pd.read_excel(path, sheet_name="產業熱度")
        summary = pd.read_excel(path, sheet_name="市場總覽")
        market["代號"] = market["代號"].apply(normalize_code)
        return market, industry, summary
    except Exception:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def load_scan_report(path):
    if not path or not os.path.exists(path):
        return pd.DataFrame()
    try:
        xl = pd.ExcelFile(path)
        frames = []
        for sheet in xl.sheet_names:
            if sheet not in STRATEGY_SHEET_NAMES:
                continue
            df = pd.read_excel(path, sheet_name=sheet)
            if df.empty or "代號" not in df.columns:
                continue
            df = df.copy()
            df["代號"] = df["代號"].apply(normalize_code)
            df["策略"] = sheet
            frames.append(df)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def fetch_daily_quote(code):
    code = normalize_code(code)
    for suffix in ["TW", "TWO"]:
        try:
            raw = yf.download(f"{code}.{suffix}", period="10d", progress=False, auto_adjust=False)
            if raw.empty:
                continue
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = [col[0] for col in raw.columns]
            raw = raw.dropna(subset=["Close"])
            if raw.empty:
                continue
            last = raw.iloc[-1]
            prev = raw.iloc[-2] if len(raw) >= 2 else last
            return {
                "price": float(last["Close"]),
                "prev_close": float(prev["Close"]),
                "open": float(last["Open"]),
                "high": float(last["High"]),
                "low": float(last["Low"]),
                "volume_lots": int(float(last["Volume"]) / 1000),
            }
        except Exception:
            continue
    try:
        realtime = twstock.realtime.get(code)
        if realtime and realtime.get("success"):
            quote = realtime.get("realtime", {})
            price = safe_float(quote.get("latest_trade_price"))
            if price == price and price > 0:
                return {
                    "price": price,
                    "prev_close": np.nan,
                    "open": safe_float(quote.get("open")),
                    "high": safe_float(quote.get("high")),
                    "low": safe_float(quote.get("low")),
                    "volume_lots": int(safe_float(quote.get("accumulate_trade_volume"), 0) or 0),
                }
    except Exception:
        pass
    return {}


@st.cache_data(ttl=3600, show_spinner=False)
def get_stock_name_from_code(code):
    code = normalize_code(code)
    try:
        stock = twstock.codes.get(code)
        if stock and stock.name:
            return stock.name
    except Exception:
        pass
    return ""


def safe_float(value, default=np.nan):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def summarize_strategy_signals(scan_df):
    if scan_df.empty:
        return {}
    summary = {}
    for code, group in scan_df.groupby("代號"):
        strategies = " / ".join(group["策略"].dropna().astype(str).unique())
        conditions = "；".join(group.get("條件", pd.Series(dtype=str)).dropna().astype(str).unique())
        stop_values = pd.to_numeric(group.get("防守價", pd.Series(dtype=float)), errors="coerce").dropna()
        rsi_values = pd.to_numeric(group.get("RSI", pd.Series(dtype=float)), errors="coerce").dropna()
        summary[code] = {
            "名稱": str(group.get("名稱", pd.Series(dtype=str)).dropna().astype(str).iloc[0])
            if "名稱" in group.columns and not group["名稱"].dropna().empty
            else "",
            "策略命中": strategies if strategies else "未命中",
            "策略條件": conditions,
            "策略防守價": float(stop_values.max()) if not stop_values.empty else np.nan,
            "RSI": float(rsi_values.iloc[-1]) if not rsi_values.empty else np.nan,
        }
    return summary


def normalize_portfolio_input(df):
    if df is None or df.empty:
        return pd.DataFrame(columns=PORTFOLIO_INPUT_COLUMNS)

    data = df.copy()
    if "股數" not in data.columns:
        if "張數" in data.columns:
            data["股數"] = pd.to_numeric(data["張數"], errors="coerce").fillna(0) * 1000
        else:
            data["股數"] = 0

    for col in PORTFOLIO_INPUT_COLUMNS:
        if col not in data.columns:
            data[col] = "" if col in ["代號", "備註"] else 0.0

    data["代號"] = data["代號"].apply(normalize_editing_code)
    for col in ["成本", "股數", "停損價", "目標價"]:
        data[col] = pd.to_numeric(data[col], errors="coerce").fillna(0.0)
    data["備註"] = data["備註"].fillna("").astype(str)

    return data[PORTFOLIO_INPUT_COLUMNS].reset_index(drop=True)


def build_holding_decision(row):
    score = 50
    reasons = []
    actions = []

    pnl_pct = row.get("損益率(%)", np.nan)
    pct = row.get("今日漲跌幅(%)", np.nan)
    volume_ratio = row.get("量比20", np.nan)
    close_position = row.get("日內位置", np.nan)
    industry_up_ratio = row.get("產業上漲比例", np.nan)
    industry_avg = row.get("產業平均漲跌幅", np.nan)
    strategy_hit = row.get("策略命中", "未命中") != "未命中"
    stop = row.get("有效停損價", np.nan)
    price = row.get("現價", np.nan)

    if strategy_hit:
        score += 15
        reasons.append("策略訊號仍有支撐")
    if pct == pct:
        if pct >= 3:
            score += 8
            reasons.append("今日股價強於市場")
        elif pct < -3:
            score -= 10
            reasons.append("今日明顯轉弱")
    if volume_ratio == volume_ratio:
        if 1.5 <= volume_ratio <= 8:
            score += 8
            reasons.append("量能放大但尚可控")
        elif volume_ratio > 8:
            score += 2
            reasons.append("量能過熱需盯回落")
    if close_position == close_position:
        if 0.35 <= close_position <= 0.85:
            score += 10
            reasons.append("日內位置健康")
        elif close_position < 0.2:
            score -= 15
            reasons.append("早盤拉高後回落")
        elif close_position > 0.85:
            score += 4
            reasons.append("接近日高但追價風險升高")
    if industry_up_ratio == industry_up_ratio and industry_avg == industry_avg:
        if industry_up_ratio >= 50 and industry_avg > 0:
            score += 8
            reasons.append("族群有擴散")
        elif industry_up_ratio < 25 and industry_avg < 0:
            score -= 10
            reasons.append("族群逆風")
    if pnl_pct == pnl_pct:
        if pnl_pct >= 12 and close_position == close_position and close_position < 0.25:
            score -= 8
            actions.append("已有獲利且日內轉弱，可考慮分批停利")
        elif pnl_pct <= -5:
            score -= 8
            actions.append("虧損擴大，優先檢查停損紀律")
    if stop == stop and price == price and stop > 0:
        if price <= stop:
            score -= 25
            actions.append("現價已觸及或跌破停損")
        elif ((price - stop) / price * 100) < 3:
            score -= 6
            actions.append("距離停損很近，避免加碼")

    score = max(0, min(100, int(round(score))))
    if score >= 75:
        status = "續抱偏強"
        default_action = "續抱，等轉強或回測支撐再決定是否加碼"
    elif score >= 55:
        status = "中性觀察"
        default_action = "續抱觀察，照停損與停利線管理"
    elif score >= 35:
        status = "偏弱控風險"
        default_action = "不加碼，若跌破關鍵價位先降部位"
    else:
        status = "風險優先"
        default_action = "以保護本金或獲利為優先"

    if not actions:
        actions.append(default_action)
    if not reasons:
        reasons.append("資料不足，以成本與停損紀律為主")
    return score, status, "；".join(actions), "、".join(reasons[:4])


def build_ai_brief(analysis_df, market_summary, market_path, scan_path):
    lines = [
        "請以專業投資人的角度分析以下台股持股，重點放在部位風險、是否續抱、停利/停損與加減碼條件。",
        "",
        f"市場監控報表: {os.path.basename(market_path) if market_path else '無'}",
        f"策略掃描報表: {os.path.basename(scan_path) if scan_path else '無'}",
    ]
    if not market_summary.empty:
        summary_map = dict(zip(market_summary["項目"], market_summary["數值"]))
        lines.extend(
            [
                f"市場更新時間: {summary_map.get('更新時間', '')}",
                f"全市場上漲比例: {summary_map.get('上漲比例', '')}",
                f"平均漲跌幅: {summary_map.get('平均漲跌幅', '')}",
                f"最熱產業: {summary_map.get('最熱產業', '')}",
                "",
            ]
        )
    for _, row in analysis_df.iterrows():
        lines.append(
            "- {code} {name}: 成本 {cost:.3f}, 現價 {price:.2f}, 股數 {shares:.0f}, "
            "損益 {pnl_pct:.2f}%, 產業 {industry}, 今日 {pct:.2f}%, 量比20 {vr20:.2f}, "
            "日內位置 {pos:.2f}, 策略 {strategy}, 有效停損 {stop}, 狀態 {status}, 建議 {action}".format(
                code=row["代號"],
                name=row["名稱"],
                cost=row["成本"],
                price=row["現價"],
                shares=row["股數"],
                pnl_pct=row["損益率(%)"],
                industry=row["產業族群"],
                pct=row["今日漲跌幅(%)"],
                vr20=row["量比20"],
                pos=row["日內位置"],
                strategy=row["策略命中"],
                stop=f"{row['有效停損價']:.2f}" if row["有效停損價"] == row["有效停損價"] else "未填",
                status=row["持股狀態"],
                action=row["行動建議"],
            )
        )
    return "\n".join(lines)


def get_logged_in_user_key():
    user = getattr(st, "user", None) or getattr(st, "experimental_user", None)
    if not user or not getattr(user, "is_logged_in", False):
        return "", ""

    getter = user.get if hasattr(user, "get") else lambda key, default=None: getattr(user, key, default)
    email = getter("email", "")
    subject = getter("sub", "")
    name = getter("name", "")
    return (email or subject or ""), (name or email or subject or "")


def streamlit_auth_configured():
    try:
        return "auth" in st.secrets
    except Exception:
        return False


# 側邊欄導覽
st.sidebar.title("💦 玉米的大噴射台股")
page = st.sidebar.radio(
    "切換功能",
    [
        "📊 歷史報表預覽 (Reports)",
        "🎯 個股高階圖表分析 (Charts)",
        "💼 持股可視化分析 (Portfolio)",
        "📰 精選動態新聞 (News)",
    ],
)

if page == "📊 歷史報表預覽 (Reports)":
    st.title(" 歷史選股報表預覽")
    st.write("預覽系統每日/盤中產出的自動化選股 Excel 報表。")
    
    with st.expander("🛠️ 手動執行全自動掃描器 (約需 1~2 分鐘)"):
        st.write("點擊下方按鈕將立刻動用主機資源，對全台股 1900+ 檔股票進行技術線型掃描：")
        col1, col2, col3 = st.columns(3)
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
                intraday_scanner.run_intraday_scanner()
                st.success("✅ 盤中狙擊完成！最新報表已自動產出。")
                st.balloons()
                time.sleep(2)
                st.rerun()

        if col3.button("📡 盤中分析 + Telegram", use_container_width=True):
            with st.spinner("正在執行盤中掃描、發送三策略清單、全市場監控，並整理隔日續漲 Telegram 報告..."):
                from intraday_analysis_report import generate_intraday_analysis_report

                result = generate_intraday_analysis_report(
                    run_scanner=True,
                    run_market_monitor=True,
                    send_telegram=True,
                    send_raw_scanner_telegram=True,
                )
                if result["telegram_sent"]:
                    st.success("✅ 三策略清單與隔日續漲分析報告已同步發送 Telegram。")
                else:
                    st.warning(f"盤中分析報告已產出，但 Telegram 未送出：{result['telegram_message']}")
                st.text_area("隔日續漲分析報告", value=result["text"], height=320)
                st.balloons()
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
                if df is None or df.empty:
                    st.error("❌ 歷史資料不足，無法計算 200MA / SuperTrend 等完整指標。請切換到 2y 區間後再試一次。")
                    st.stop()
                
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

elif page == "💼 持股可視化分析 (Portfolio)":
    st.title("💼 持股可視化分析")
    st.write("輸入股票代號、成本與股數，系統會自動帶入股票名稱，並結合最新市場監控與策略掃描整理部位風險。")

    market_files = list_reports(["Reports/市場監控_*.xlsx"])
    scan_files = list_reports(
        ["Reports/盤中日報_*.xlsx", "Reports/選股日報_*.xlsx"],
        exclude_keywords=["策略市場交叉分析"],
    )

    col_report1, col_report2 = st.columns(2)
    with col_report1:
        selected_market_file = st.selectbox(
            "市場資料來源",
            market_files,
            format_func=os.path.basename,
            index=0 if market_files else None,
            placeholder="尚無市場監控報表",
        ) if market_files else None
    with col_report2:
        selected_scan_file = st.selectbox(
            "策略訊號來源",
            scan_files,
            format_func=os.path.basename,
            index=0 if scan_files else None,
            placeholder="尚無策略掃描報表",
        ) if scan_files else None

    market_df, industry_df, market_summary = load_market_report(selected_market_file)
    scan_df = load_scan_report(selected_scan_file)
    signal_map = summarize_strategy_signals(scan_df)
    if selected_market_file is None:
        st.info("目前沒有市場監控報表，將改用 yfinance/twstock 即時報價備援；產業熱度與量比資料會較少。")

    store_status = get_portfolio_backend_status(st.secrets)
    login_key, login_label = get_logged_in_user_key()
    with st.expander("☁️ 股票倉儲存設定", expanded=True):
        if store_status["is_cloud"]:
            st.success(f"目前儲存後端：{store_status['label']}")
        else:
            st.warning(f"目前儲存後端：{store_status['label']}")
        st.caption(store_status["message"])

        if login_key:
            owner_key = f"oidc::{login_key}"
            owner_label = login_label
            st.session_state["portfolio_owner_key"] = owner_key
            st.session_state["portfolio_owner_label"] = owner_label
            st.write(f"登入身份：{owner_label}")
            if hasattr(st, "logout") and st.button("登出", key="portfolio_logout"):
                st.logout()
        else:
            st.caption("第一次使用請自己設定 Email/名稱與一組私密代碼；之後用同一組資料就能開啟同一個股票倉。")
            owner_col1, owner_col2 = st.columns(2)
            with owner_col1:
                manual_identifier = st.text_input(
                    "Email / 股票倉名稱",
                    value=st.session_state.get("portfolio_manual_identifier", ""),
                    help="用來辨識你的股票倉；建議填 email。",
                    key="portfolio_manual_identifier_input",
                )
            with owner_col2:
                manual_access_code = st.text_input(
                    "自訂私密倉庫代碼",
                    value=st.session_state.get("portfolio_manual_access_code", ""),
                    type="password",
                    help="第一次使用時自己設定；下次輸入同一組代碼即可開啟同一個股票倉。",
                    key="portfolio_manual_access_code_input",
                )
            st.session_state["portfolio_manual_identifier"] = manual_identifier.strip()
            st.session_state["portfolio_manual_access_code"] = manual_access_code.strip()
            owner_key, owner_label = build_manual_owner_key(
                st.session_state["portfolio_manual_identifier"],
                st.session_state["portfolio_manual_access_code"],
            )
            st.session_state["portfolio_owner_key"] = owner_key
            st.session_state["portfolio_owner_label"] = owner_label
            if streamlit_auth_configured() and hasattr(st, "login"):
                if st.button("使用登入系統", key="portfolio_login"):
                    st.login()
            else:
                st.caption("這不是正式帳號註冊；私密代碼由你自己設定並記住，用來區分股票倉。公開部署後建議改用 Streamlit OIDC 登入。")

        if st.button("從儲存區載入股票倉", use_container_width=True, disabled=not owner_key):
            loaded_df, load_info = load_holdings(owner_key, st.secrets)
            if load_info.get("error"):
                st.warning(f"{load_info['backend']} 載入失敗：{load_info['error']}")
            else:
                st.session_state["portfolio_input"] = normalize_portfolio_input(loaded_df)
                st.session_state["portfolio_loaded_owner"] = owner_key
                st.session_state["portfolio_editor_version"] = st.session_state.get("portfolio_editor_version", 0) + 1
                st.success(f"已從 {load_info['backend']} 載入 {load_info['count']} 筆持股。")
                st.rerun()

    auto_admin_ok, auto_admin_label = verify_admin_access(login_key=login_key, secrets=st.secrets)
    if auto_admin_ok and not st.session_state.get("portfolio_admin_auto_paused", False):
        st.session_state["portfolio_admin_ok"] = True
        st.session_state["portfolio_admin_label"] = auto_admin_label

    with st.expander("🛡️ 超級管理員總覽", expanded=st.session_state.get("portfolio_admin_ok", False)):
        st.caption("管理員帳號需在 Secrets 的 [admin] 區塊設定；一般使用者不會看到其他人的股票倉。")

        if not st.session_state.get("portfolio_admin_ok", False):
            admin_col1, admin_col2 = st.columns(2)
            with admin_col1:
                admin_identifier = st.text_input(
                    "管理員 Email",
                    value=st.session_state.get("portfolio_admin_identifier", ""),
                    key="portfolio_admin_identifier_input",
                )
            with admin_col2:
                admin_access_code = st.text_input(
                    "管理員代碼",
                    type="password",
                    key="portfolio_admin_access_code_input",
                )

            if st.button("開啟管理員總覽", use_container_width=True, key="portfolio_admin_login"):
                ok, label_or_error = verify_admin_access(
                    admin_identifier,
                    admin_access_code,
                    login_key,
                    st.secrets,
                )
                if ok:
                    st.session_state["portfolio_admin_ok"] = True
                    st.session_state["portfolio_admin_label"] = label_or_error
                    st.session_state["portfolio_admin_identifier"] = admin_identifier.strip()
                    st.session_state["portfolio_admin_auto_paused"] = False
                    st.rerun()
                else:
                    st.error(label_or_error)
        else:
            admin_label = st.session_state.get("portfolio_admin_label", "admin")
            admin_top_col1, admin_top_col2 = st.columns([2, 1])
            with admin_top_col1:
                st.success(f"管理員模式已開啟：{admin_label}")
            with admin_top_col2:
                if st.button("關閉管理員模式", use_container_width=True, key="portfolio_admin_logout"):
                    st.session_state["portfolio_admin_ok"] = False
                    st.session_state["portfolio_admin_auto_paused"] = True
                    st.session_state.pop("portfolio_admin_label", None)
                    st.rerun()

            admin_df, admin_info = list_all_holdings(st.secrets)
            if admin_info.get("error"):
                st.error(f"{admin_info['backend']} 管理員總覽讀取失敗：{admin_info['error']}")
            elif admin_df.empty:
                st.info("目前還沒有任何使用者儲存持股。")
            else:
                warehouse_count = int(admin_df["倉庫ID"].nunique())
                total_cost_basis = float(admin_df["成本金額"].sum())
                latest_update = str(admin_df["更新時間"].max())
                metric_col1, metric_col2, metric_col3 = st.columns(3)
                metric_col1.metric("股票倉數", f"{warehouse_count}")
                metric_col2.metric("持股筆數", f"{len(admin_df)}")
                metric_col3.metric("總成本金額", f"{total_cost_basis:,.0f}")
                st.caption(f"資料來源：{admin_info['backend']} | 最近更新：{latest_update}")

                warehouse_summary = (
                    admin_df.groupby(["倉庫", "倉庫類型", "倉庫ID"], dropna=False)
                    .agg(持股檔數=("代號", "count"), 成本總額=("成本金額", "sum"), 最後更新=("更新時間", "max"))
                    .reset_index()
                    .sort_values(["最後更新", "成本總額"], ascending=[False, False])
                )
                st.dataframe(
                    warehouse_summary.style.format({"成本總額": "{:,.0f}"}),
                    use_container_width=True,
                    hide_index=True,
                )
                st.dataframe(
                    admin_df.style.format(
                        {
                            "成本": "{:.3f}",
                            "股數": "{:,.0f}",
                            "成本金額": "{:,.0f}",
                            "停損價": "{:.2f}",
                            "目標價": "{:.2f}",
                        }
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
                st.download_button(
                    "下載全部持股 CSV",
                    data=admin_df.to_csv(index=False).encode("utf-8-sig"),
                    file_name="all_portfolio_holdings.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

    owner_key = st.session_state.get("portfolio_owner_key", "").strip()
    owner_label = st.session_state.get("portfolio_owner_label", "").strip()
    if "portfolio_editor_version" not in st.session_state:
        st.session_state["portfolio_editor_version"] = 0

    if owner_key:
        st.caption(f"目前開啟股票倉：{owner_label or '已登入使用者'}")
    else:
        st.info("請先輸入 Email/股票倉名稱與私密倉庫代碼，才能載入或儲存自己的股票倉。")

    if "portfolio_input" not in st.session_state:
        loaded_df, load_info = load_holdings(owner_key, st.secrets) if owner_key else (pd.DataFrame(), {"count": 0})
        st.session_state["portfolio_input"] = normalize_portfolio_input(loaded_df)
        st.session_state["portfolio_loaded_owner"] = owner_key
        if load_info.get("error"):
            st.warning(f"{load_info['backend']} 載入失敗：{load_info['error']}")
        if load_info["count"]:
            st.toast(f"已自動載入 {load_info['count']} 筆雲端/本機持股")

    previous_loaded_owner = st.session_state.get("portfolio_loaded_owner")
    if owner_key and previous_loaded_owner != owner_key:
        loaded_df, load_info = load_holdings(owner_key, st.secrets)
        existing_df = normalize_portfolio_input(st.session_state.get("portfolio_input"))
        if load_info.get("error"):
            st.warning(f"{load_info['backend']} 載入失敗：{load_info['error']}")
        if load_info["count"] or bool(previous_loaded_owner) or existing_df.empty:
            st.session_state["portfolio_input"] = normalize_portfolio_input(loaded_df)
            st.session_state["portfolio_editor_version"] += 1
        st.session_state["portfolio_loaded_owner"] = owner_key

    st.session_state["portfolio_input"] = normalize_portfolio_input(st.session_state["portfolio_input"])

    holdings = st.data_editor(
        st.session_state["portfolio_input"],
        key=f"portfolio_editor_{st.session_state['portfolio_editor_version']}",
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        disabled=not owner_key,
        column_order=PORTFOLIO_INPUT_COLUMNS,
        column_config={
            "代號": st.column_config.TextColumn("代號", width="small"),
            "成本": st.column_config.NumberColumn("成本", min_value=0.0, step=0.001, format="%.3f"),
            "股數": st.column_config.NumberColumn("股數", min_value=0.0, step=1.0, format="%.0f"),
            "停損價": st.column_config.NumberColumn("停損價", min_value=0.0, step=0.1, format="%.2f"),
            "目標價": st.column_config.NumberColumn("目標價", min_value=0.0, step=0.1, format="%.2f"),
            "備註": st.column_config.TextColumn("備註", width="medium"),
        },
    )
    holdings = normalize_portfolio_input(holdings)
    st.session_state["portfolio_input"] = holdings

    save_col1, save_col2 = st.columns([1, 2])
    with save_col1:
        if st.button("儲存股票倉", type="primary", use_container_width=True, disabled=not owner_key):
            try:
                save_info = save_holdings(owner_key, holdings, st.secrets)
                st.success(f"已儲存 {save_info['count']} 筆持股到 {save_info['backend']}。")
            except Exception as exc:
                st.error(f"儲存失敗：{exc}")
    with save_col2:
        st.caption("重新整理後，只要使用同一個登入身份，或同一組 Email/股票倉名稱與私密代碼，就能從儲存區載回持股。")

    if not market_summary.empty:
        summary_map = dict(zip(market_summary["項目"], market_summary["數值"]))
        st.caption(
            f"市場資料時間：{summary_map.get('更新時間', '未知')} | "
            f"上漲比例：{summary_map.get('上漲比例', '未知')} | "
            f"平均漲跌幅：{summary_map.get('平均漲跌幅', '未知')} | "
            f"最熱產業：{summary_map.get('最熱產業', '未知')}"
        )

    analysis_records = []
    missing_codes = []
    market_by_code = market_df.set_index("代號", drop=False) if not market_df.empty else pd.DataFrame()

    for _, holding in holdings.iterrows():
        code = normalize_code(holding.get("代號"))
        if not code:
            continue

        cost = safe_float(holding.get("成本"), 0.0)
        shares = safe_float(holding.get("股數"), 0.0)
        if cost <= 0 or shares <= 0:
            continue

        user_stop = safe_float(holding.get("停損價"))
        target_price = safe_float(holding.get("目標價"))
        note = str(holding.get("備註", "")).strip()

        market_row = market_by_code.loc[code] if not market_by_code.empty and code in market_by_code.index else {}
        if isinstance(market_row, pd.DataFrame):
            market_row = market_row.iloc[0]

        quote = {}
        price = safe_float(market_row.get("現價") if hasattr(market_row, "get") else np.nan)
        if price != price:
            quote = fetch_daily_quote(code)
            price = safe_float(quote.get("price"))

        if price != price:
            missing_codes.append(code)
            continue

        signal = signal_map.get(code, {})
        name = (
            str(market_row.get("名稱", "") if hasattr(market_row, "get") else "")
            or str(signal.get("名稱", ""))
            or get_stock_name_from_code(code)
            or code
        )
        industry = str(market_row.get("產業族群", "") if hasattr(market_row, "get") else "")

        pct_change = safe_float(market_row.get("漲跌幅") if hasattr(market_row, "get") else np.nan)
        if pct_change != pct_change and quote:
            prev_close = safe_float(quote.get("prev_close"))
            pct_change = (price - prev_close) / prev_close * 100 if prev_close > 0 else np.nan

        open_price = safe_float(market_row.get("開盤") if hasattr(market_row, "get") else quote.get("open"))
        high_price = safe_float(market_row.get("最高") if hasattr(market_row, "get") else quote.get("high"))
        low_price = safe_float(market_row.get("最低") if hasattr(market_row, "get") else quote.get("low"))
        close_position = safe_float(market_row.get("收盤位置") if hasattr(market_row, "get") else np.nan)
        if close_position != close_position and high_price > low_price:
            close_position = (price - low_price) / (high_price - low_price)

        volume_ratio5 = safe_float(market_row.get("量比5") if hasattr(market_row, "get") else np.nan)
        volume_ratio20 = safe_float(market_row.get("量比20") if hasattr(market_row, "get") else np.nan)
        turnover = safe_float(market_row.get("成交值(億)") if hasattr(market_row, "get") else np.nan)

        industry_up_ratio = np.nan
        industry_avg = np.nan
        industry_heat = np.nan
        if industry and not industry_df.empty:
            industry_match = industry_df[industry_df["產業族群"] == industry]
            if not industry_match.empty:
                industry_row = industry_match.iloc[0]
                industry_up_ratio = safe_float(industry_row.get("上漲比例"))
                industry_avg = safe_float(industry_row.get("平均漲跌幅"))
                industry_heat = safe_float(industry_row.get("熱度分數"))

        signal_stop = safe_float(signal.get("策略防守價"))
        effective_stop = user_stop if user_stop == user_stop and user_stop > 0 else signal_stop
        cost_amount = cost * shares
        market_value = price * shares
        pnl = market_value - cost_amount
        pnl_pct = pnl / cost_amount * 100 if cost_amount > 0 else np.nan
        distance_stop_pct = (price - effective_stop) / price * 100 if effective_stop == effective_stop and price > 0 else np.nan
        stop_pnl = (effective_stop - cost) * shares if effective_stop == effective_stop else np.nan
        distance_target_pct = (target_price - price) / price * 100 if target_price == target_price and target_price > 0 else np.nan

        record = {
            "代號": code,
            "名稱": name,
            "產業族群": industry or "未知",
            "成本": cost,
            "股數": shares,
            "現價": price,
            "市值": market_value,
            "成本金額": cost_amount,
            "未實現損益": pnl,
            "損益率(%)": pnl_pct,
            "今日漲跌幅(%)": pct_change,
            "開盤": open_price,
            "最高": high_price,
            "最低": low_price,
            "成交值(億)": turnover,
            "量比5": volume_ratio5,
            "量比20": volume_ratio20,
            "日內位置": close_position,
            "產業上漲比例": industry_up_ratio,
            "產業平均漲跌幅": industry_avg,
            "產業熱度分數": industry_heat,
            "策略命中": signal.get("策略命中", "未命中"),
            "策略條件": signal.get("策略條件", ""),
            "策略防守價": signal_stop,
            "使用者停損價": user_stop,
            "有效停損價": effective_stop,
            "距停損(%)": distance_stop_pct,
            "停損後損益": stop_pnl,
            "目標價": target_price,
            "距目標(%)": distance_target_pct,
            "備註": note,
        }
        score, status, action, reasons = build_holding_decision(record)
        record["續抱分數"] = score
        record["持股狀態"] = status
        record["行動建議"] = action
        record["判斷理由"] = reasons
        analysis_records.append(record)

    if missing_codes:
        st.warning(f"以下代號暫時抓不到價格資料：{', '.join(missing_codes)}")

    if not analysis_records:
        st.info("請至少輸入一筆持股：代號、成本與股數都需要大於 0。")
    else:
        analysis_df = pd.DataFrame(analysis_records)
        total_cost = analysis_df["成本金額"].sum()
        total_value = analysis_df["市值"].sum()
        total_pnl = analysis_df["未實現損益"].sum()
        total_return = total_pnl / total_cost * 100 if total_cost > 0 else 0
        winners = int((analysis_df["未實現損益"] > 0).sum())
        risk_hits = int((analysis_df["持股狀態"].isin(["偏弱控風險", "風險優先"])).sum())

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("總市值", f"{total_value:,.0f}")
        c2.metric("未實現損益", f"{total_pnl:,.0f}", f"{total_return:.2f}%")
        c3.metric("獲利持股", f"{winners}/{len(analysis_df)}")
        c4.metric("需控風險", f"{risk_hits} 檔")

        snap_col1, snap_col2 = st.columns([1, 2])
        with snap_col1:
            if st.button("記錄今日持股快照", use_container_width=True, disabled=not owner_key):
                try:
                    snap_info = save_portfolio_snapshot(
                        owner_key,
                        analysis_df,
                        selected_market_file,
                        selected_scan_file,
                        st.secrets,
                    )
                    st.success(f"已寫入 {snap_info['count']} 筆今日快照到 {snap_info['backend']}。")
                except Exception as exc:
                    st.error(f"快照寫入失敗：{exc}")
        with snap_col2:
            st.caption("每日快照會保存當下現價、損益、續抱分數與策略狀態，之後可用來回測持股決策。")

        chart_col1, chart_col2 = st.columns([1, 1])
        with chart_col1:
            alloc_df = analysis_df[analysis_df["市值"] > 0]
            fig_alloc = go.Figure(
                data=[
                    go.Pie(
                        labels=alloc_df["代號"] + " " + alloc_df["名稱"],
                        values=alloc_df["市值"],
                        hole=0.45,
                    )
                ]
            )
            fig_alloc.update_layout(title="部位配置", height=360, margin=dict(l=0, r=0, t=45, b=0))
            st.plotly_chart(fig_alloc, use_container_width=True)

        with chart_col2:
            colors = np.where(analysis_df["未實現損益"] >= 0, "#d62728", "#2ca02c")
            fig_pnl = go.Figure(
                data=[
                    go.Bar(
                        x=analysis_df["代號"] + " " + analysis_df["名稱"],
                        y=analysis_df["未實現損益"],
                        marker_color=colors,
                    )
                ]
            )
            fig_pnl.update_layout(title="各持股未實現損益", height=360, margin=dict(l=0, r=0, t=45, b=0))
            st.plotly_chart(fig_pnl, use_container_width=True)

        scatter_size = (analysis_df["市值"] / analysis_df["市值"].max() * 40 + 12).fillna(16)
        fig_risk = go.Figure(
            data=[
                go.Scatter(
                    x=analysis_df["損益率(%)"],
                    y=analysis_df["續抱分數"],
                    mode="markers+text",
                    text=analysis_df["代號"],
                    textposition="top center",
                    marker=dict(
                        size=scatter_size,
                        color=analysis_df["今日漲跌幅(%)"],
                        colorscale="RdYlGn",
                        showscale=True,
                        colorbar=dict(title="今日%"),
                        line=dict(width=1, color="#333"),
                    ),
                    hovertemplate="%{text}<br>損益=%{x:.2f}%<br>續抱分數=%{y}<extra></extra>",
                )
            ]
        )
        fig_risk.update_layout(
            title="損益率 vs 續抱分數",
            xaxis_title="損益率 (%)",
            yaxis_title="續抱分數",
            height=420,
            margin=dict(l=0, r=0, t=45, b=0),
        )
        st.plotly_chart(fig_risk, use_container_width=True)

        st.subheader("📋 持股診斷表")
        display_cols = [
            "代號",
            "名稱",
            "產業族群",
            "成本",
            "現價",
            "股數",
            "未實現損益",
            "損益率(%)",
            "今日漲跌幅(%)",
            "量比20",
            "日內位置",
            "策略命中",
            "有效停損價",
            "距停損(%)",
            "目標價",
            "距目標(%)",
            "續抱分數",
            "持股狀態",
            "行動建議",
            "判斷理由",
        ]
        st.dataframe(
            analysis_df[display_cols].style.format(
                {
                    "成本": "{:.3f}",
                    "現價": "{:.2f}",
                    "股數": "{:,.0f}",
                    "未實現損益": "{:,.0f}",
                    "損益率(%)": "{:.2f}",
                    "今日漲跌幅(%)": "{:.2f}",
                    "量比20": "{:.2f}",
                    "日內位置": "{:.2f}",
                    "有效停損價": "{:.2f}",
                    "距停損(%)": "{:.2f}",
                    "目標價": "{:.2f}",
                    "距目標(%)": "{:.2f}",
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

        ai_brief = build_ai_brief(analysis_df, market_summary, selected_market_file, selected_scan_file)
        with st.expander("🤖 給 AI / Codex 的持股分析摘要", expanded=False):
            st.text_area("分析摘要", value=ai_brief, height=300)
            st.download_button(
                "下載持股分析 CSV",
                data=analysis_df.to_csv(index=False).encode("utf-8-sig"),
                file_name="portfolio_analysis.csv",
                mime="text/csv",
                use_container_width=True,
            )

        with st.expander("📰 近期新聞快查", expanded=False):
            if st.button("抓取目前持股近期新聞", use_container_width=True):
                from llm_agent import fetch_google_news

                for _, row in analysis_df.iterrows():
                    st.markdown(f"**{row['名稱']} ({row['代號']})**")
                    news_items = fetch_google_news(f"{row['代號']} {row['名稱']}", limit=3)
                    if news_items:
                        for item in news_items:
                            st.markdown(item)
                    else:
                        st.write("近期無明顯新聞。")

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
