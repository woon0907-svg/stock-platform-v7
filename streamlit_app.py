# -*- coding: utf-8 -*-
"""
stock_platform_v7 — Streamlit 웹 버전
개인용 모바일/PC 주식 투자 플랫폼
"""
import streamlit as st
import pandas as pd
import time

# ── 페이지 설정 (가장 먼저) ──────────────────────────────────────────
st.set_page_config(
    page_title="주식 투자 플랫폼 v7",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── 다크 테마 CSS ─────────────────────────────────────────────────────
st.markdown("""
<style>
/* 전체 배경 */
.stApp { background-color: #0d1117; color: #e6edf3; }
[data-testid="stSidebar"] { background-color: #161b22; }

/* 탭 스타일 */
.stTabs [data-baseweb="tab-list"] {
    background-color: #161b22;
    border-radius: 8px;
    padding: 4px;
}
.stTabs [data-baseweb="tab"] {
    color: #8b949e;
    font-size: 13px;
    padding: 6px 12px;
}
.stTabs [aria-selected="true"] {
    background-color: #21262d;
    color: #58a6ff;
    border-radius: 6px;
}

/* 버튼 */
.stButton button {
    background-color: #21262d;
    color: #e6edf3;
    border: 1px solid #30363d;
    border-radius: 6px;
}
.stButton button:hover { background-color: #30363d; }

/* 테이블 */
.stDataFrame { background-color: #161b22; }
[data-testid="stDataFrame"] td { color: #e6edf3; }

/* 지표 카드 */
.metric-card {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 10px 14px;
    margin: 4px 0;
    font-size: 13px;
}
.sig-buy  { color: #2ea043; font-weight: 600; }
.sig-sell { color: #cf222e; font-weight: 600; }
.sig-hold { color: #e6edf3; }
.sig-wait { color: #8b949e; }

/* 종목 결과 카드 */
.result-card {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 12px 16px;
    margin: 6px 0;
}
.grade-a { border-left: 3px solid #2ea043; }
.grade-b { border-left: 3px solid #d29922; }
.grade-c { border-left: 3px solid #8b949e; }

/* 입력창 */
.stTextInput input, .stSelectbox div {
    background-color: #21262d;
    color: #e6edf3;
    border-color: #30363d;
}
/* 모바일 최적화 */
@media (max-width: 768px) {
    .stTabs [data-baseweb="tab"] { font-size: 11px; padding: 5px 8px; }
    h1 { font-size: 1.3rem !important; }
    h2 { font-size: 1.1rem !important; }
}
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════
# 핵심 모듈 임포트 (기존 v7 로직 그대로)
# ══════════════════════════════════════════════════════════════════════
@st.cache_resource(ttl=3600)
def _load_modules():
    from strategies import STRATEGY_SCORE, STRATEGY_DESC, STRATEGY_THRESHOLD
    from strategy_params import STRATEGY_PARAMS_DEFAULT, get_param
    from config import KOSPI_FALLBACK, KOSDAQ_FALLBACK
    from data_fetcher import fetch_realtime, _is_kr_ticker
    from indicators import (sma, ema, rsi_calc, macd_calc, bollinger,
                             adx as calc_adx, find_pivot_points, cluster_levels)
    return {
        "STRATEGY_SCORE":    STRATEGY_SCORE,
        "STRATEGY_DESC":     STRATEGY_DESC,
        "STRATEGY_THRESHOLD":STRATEGY_THRESHOLD,
        "STRATEGY_PARAMS":   STRATEGY_PARAMS_DEFAULT,
        "get_param":         get_param,
        "KOSPI":             KOSPI_FALLBACK,
        "KOSDAQ":            KOSDAQ_FALLBACK,
        "fetch_realtime":    fetch_realtime,
        "is_kr":             _is_kr_ticker,
        "sma": sma, "ema": ema, "rsi": rsi_calc,
        "macd": macd_calc, "bb": bollinger, "adx": calc_adx,
        "pivot": find_pivot_points, "cluster": cluster_levels,
    }

try:
    M = _load_modules()
except Exception as e:
    st.error(f"모듈 로딩 실패: {e}")
    st.stop()

STRATEGIES  = list(M["STRATEGY_SCORE"].keys())
ALL_TICKERS = M["KOSPI"] + M["KOSDAQ"]


# ══════════════════════════════════════════════════════════════════════
# 세션 상태 초기화
# ══════════════════════════════════════════════════════════════════════
if "watchlist" not in st.session_state:
    # [(name, ticker, currency), ...]
    st.session_state.watchlist = [
        ("삼성전자",   "005930.KS", "KRW"),
        ("SK하이닉스", "000660.KS", "KRW"),
        ("현대차",     "005380.KS", "KRW"),
    ]
if "scan_results" not in st.session_state:
    st.session_state.scan_results = {}
if "scan_meta" not in st.session_state:
    st.session_state.scan_meta = {}
if "chart_ticker" not in st.session_state:
    st.session_state.chart_ticker = None
if "chart_name" not in st.session_state:
    st.session_state.chart_name = ""


# ══════════════════════════════════════════════════════════════════════
# 헬퍼 함수
# ══════════════════════════════════════════════════════════════════════
def _analyze_single(name: str, ticker: str) -> dict:
    """투자종목 탭 단일 종목 판정 엔진"""
    import gc
    try:
        d = M["fetch_realtime"](ticker)
        cl = d.get("closes", [])
        vols = d.get("volumes", [])
        hi = d.get("highs", [])
        lo = d.get("lows", [])
        op = d.get("opens", [])
        if not cl:
            return {"error": "데이터없음"}

        cur = cl[-1]; n = len(cl)
        is_kr = M["is_kr"](ticker)
        pf = (lambda v: f"₩{v:,.0f}") if is_kr else (lambda v: f"${v:.2f}")

        def _last(v, default=cur):
            return next((x for x in reversed(v) if x is not None), default)

        ema10 = _last(M["ema"](cl, 10))
        ema20 = _last(M["ema"](cl, 20))
        ma20  = _last(M["sma"](cl, 20))
        ma50  = _last(M["sma"](cl, 50))
        ma200 = _last(M["sma"](cl, 200))

        rsi_v = M["rsi"](cl, 14)
        rsi_c = _last(rsi_v, 50)
        rsi_pv = next((v for i, v in enumerate(reversed(rsi_v))
                       if v is not None and i >= 1), rsi_c)
        rsi_rising = rsi_c > rsi_pv

        macd_l, sig_l, hist_l = M["macd"](cl)
        macd_c = _last(macd_l, 0); sig_c = _last(sig_l, 0)
        hist_v = [v for v in hist_l if v is not None]
        mgx = macd_c > sig_c
        hup = len(hist_v) >= 2 and hist_v[-1] > hist_v[-2]

        bbu2, _, bbl2 = M["bb"](cl, 20, 2)
        bbu = _last(bbu2, cur); bbl = _last(bbl2, cur)
        bbp = (cur - bbl) / max(bbu - bbl, 1) * 100

        adx_v, pdi_v, ndi_v = M["adx"](hi if hi else cl, lo if lo else cl, cl, 14)
        adx_c = _last(adx_v, 0); pdi_c = _last(pdi_v, 0); ndi_c = _last(ndi_v, 0)
        adx_up = pdi_c > ndi_c
        adx_trend = ("강한추세" if adx_c >= 40 else "추세있음" if adx_c >= 25 else "추세약함")

        vol20 = sum(vols[-20:]) / 20 if len(vols) >= 20 else 0
        vol_ratio = vols[-1] / max(vol20, 1) if vols and vol20 > 0 else 1.0

        hi52 = max(cl[-min(n,252):]); lo52 = min(cl[-min(n,252):])
        from_hi = (cur - hi52) / max(hi52, 1) * 100
        disp20 = (cur / max(ma20, 1) * 100) if ma20 > 0 else 100

        full_align  = (cur>ema10>ema20>ma50>ma200) if (ema10>0 and ema20>0 and ma50>0 and ma200>0) else False
        part_align  = (cur>ema20>ma50) if (ema20>0 and ma50>0) else False
        above_ma200 = (cur > ma200) if ma200 > 0 else True

        if full_align:     ma_txt, ma_col = "완전정배열", "🟢"
        elif part_align:   ma_txt, ma_col = "정배열", "🔵"
        elif above_ma200:  ma_txt, ma_col = "MA200위", "🟡"
        else:              ma_txt, ma_col = "역배열", "🔴"

        # 매수 신호
        buy_s = []
        if adx_c >= 40 and adx_up and part_align: buy_s.append(f"ADX강세{adx_c:.0f}")
        elif adx_c >= 25 and adx_up and part_align: buy_s.append(f"ADX추세{adx_c:.0f}")
        if full_align and vol_ratio >= 1.3: buy_s.append("정배열+물량")
        elif part_align and rsi_c < 60: buy_s.append("정배열")
        if ma20 > 0 and abs(cur-ema20)/max(ema20,1)*100 <= 2.5 and rsi_c < 55: buy_s.append("EMA20눌림목")
        elif ma50 > 0 and abs(cur-ma50)/max(ma50,1)*100 <= 2.5 and rsi_c < 55: buy_s.append("MA50눌림목")
        if rsi_c <= 38: buy_s.append(f"RSI{rsi_c:.0f}↓")
        elif rsi_c <= 45 and rsi_rising: buy_s.append("RSI반등중")
        if bbp <= 15: buy_s.append("BB하단")
        if mgx and hup: buy_s.append("MACD골든↑")
        if from_hi >= -3 and vol_ratio >= 1.5: buy_s.append("신고가돌파!")
        buy_s = buy_s[:3]

        # 매도 신호
        sell_s = []
        if adx_c >= 25 and not adx_up: sell_s.append(f"ADX하락{adx_c:.0f}")
        if adx_c < 20 and vol_ratio < 1.0: sell_s.append("ADX추세약화")
        if ma20 > 0 and cl[-1] < ma20*0.995 and (len(cl)<2 or cl[-1]<cl[-2]):
            sell_s.append("MA20이탈")
        if not above_ma200 and ma200 > 0: sell_s.append("MA200아래")
        if rsi_c >= 70: sell_s.append(f"RSI{rsi_c:.0f}↑")
        if bbp >= 85: sell_s.append("BB상단과열")
        if mgx and not hup and len(hist_v) >= 2 and hist_v[-1] < hist_v[-2]:
            sell_s.append("MACD약화")
        if disp20 >= 115: sell_s.append(f"이격{disp20:.0f}%")
        sell_s = sell_s[:3]

        # 종합 점수
        score = 50
        if full_align:    score += 20
        elif part_align:  score += 10
        if mgx and hup:   score += 10
        elif mgx:         score += 5
        if 40 <= rsi_c <= 65: score += 8
        elif rsi_c < 40:      score += 5
        if bbp <= 40:     score += 5
        if from_hi >= -10: score += 7
        if vol_ratio >= 1.5: score += 5
        if adx_c >= 40 and adx_up:   score += 10
        elif adx_c >= 25 and adx_up:  score += 5
        elif adx_c >= 25 and not adx_up: score -= 5
        if not above_ma200: score -= 20
        if rsi_c >= 70:  score -= 10
        if disp20 >= 115: score -= 8
        score = max(0, min(100, score))

        # 판정
        gate_fail = (not above_ma200 and ma200 > 0) or rsi_c >= 75 or from_hi < -40
        if gate_fail or not buy_s:
            if not above_ma200: judgment = "🔴 비매수"
            elif not part_align: judgment = "⚪ 역배열 대기"
            else:               judgment = "⚪ 관망"
        elif score >= 80 and len(buy_s) >= 2: judgment = "🟢 강한매수"
        elif score >= 65:                     judgment = "🟡 매수가능"
        else:                                 judgment = "⚪ 관망"

        if sell_s:
            hold = "⚠️ 매도검토"
        elif full_align and mgx:
            hold = "✅ 보유적합"
        elif part_align:
            hold = "✅ 보유가능"
        else:
            hold = "👀 모니터링"

        # 손절
        atr14 = cur * 0.02
        if len(cl) >= 15 and hi and lo:
            tr_list = [max(hi[i]-lo[i], abs(hi[i]-cl[i-1]), abs(lo[i]-cl[i-1]))
                       for i in range(1, min(15, n))]
            atr14 = sum(tr_list) / len(tr_list) if tr_list else atr14
        stop  = max(ema20 * 0.995 if ema20 > 0 else 0,
                    (lo[-1] * 0.995) if lo else 0,
                    cur * 0.93)
        stop_pct = (stop - cur) / cur * 100

        gc.collect()
        return {
            "price": pf(cur),
            "rsi":   f"{rsi_c:.0f}{'↑' if rsi_rising else '↓'}",
            "macd":  "골든+↑" if mgx and hup else "골든" if mgx else "히스↑" if hup else "하락",
            "bb":    f"{bbp:.0f}%",
            "ma":    f"{ma_col} {ma_txt}",
            "adx":   f"{adx_c:.0f} {adx_trend} {'▲' if adx_up else '▼'}",
            "buy":   " / ".join(buy_s) if buy_s else ("추세OK·신호대기" if part_align else "역배열대기"),
            "sell":  " / ".join(sell_s) if sell_s else ("정배열보유적합" if full_align and mgx else "신호없음"),
            "judgment": judgment,
            "hold":  hold,
            "score": score,
            "stop":  f"{pf(stop)} ({stop_pct:.1f}%)",
            "tp1":   pf(cur * 1.10),
            "tp2":   pf(cur * 1.20),
        }
    except Exception as e:
        return {"error": str(e)[:40]}


# ══════════════════════════════════════════════════════════════════════
# 헤더
# ══════════════════════════════════════════════════════════════════════
st.markdown("## 📈 주식 투자 플랫폼 v7")

col_hd1, col_hd2 = st.columns([3, 1])
with col_hd2:
    if st.button("🔄 새로고침", width='stretch'):
        st.cache_data.clear()
        st.rerun()

# ══════════════════════════════════════════════════════════════════════
# 메인 탭
# ══════════════════════════════════════════════════════════════════════
TAB_NAMES = ["🔍 전략 스캐너", "📋 투자종목", "📊 차트", "⚙️ 설정"]
tab_scan, tab_watch, tab_chart, tab_cfg = st.tabs(TAB_NAMES)


# ══════════════════════════════════════════════════════════════════════
# TAB1: 전략 스캐너
# ══════════════════════════════════════════════════════════════════════
with tab_scan:
    st.markdown("### 🔍 전략별 종목 찾기")

    # ── 전략 선택 ────────────────────────────────────────────────────
    col_s1, col_s2 = st.columns([3, 2])
    with col_s1:
        selected_strategy = st.selectbox(
            "전략 선택",
            STRATEGIES,
            format_func=lambda x: f"{x}  —  {M['STRATEGY_DESC'].get(x,'')[:35]}",
        )

    # ── 시장 + 종목 수 선택 (2단계) ──────────────────────────────────
    st.markdown("**🏦 스캔 대상 설정**")
    mkt_col, cap_col = st.columns(2)
    with mkt_col:
        market_filter = st.radio(
            "시장",
            ["KOSPI", "KOSDAQ", "KOSPI+KOSDAQ"],
            horizontal=True,
        )
    with cap_col:
        cap_opt = st.radio(
            "시가총액 기준 (순위)",
            ["상위 200개", "상위 500개", "전체"],
            horizontal=True,
        )

    if st.button("🚀 스캔 시작", type="primary", width="stretch"):
        import gc

        # ── 대상 종목 결정 ────────────────────────────────────────────
        try:
            from data_fetcher import get_all_tickers
            if market_filter == "KOSPI":
                all_target = get_all_tickers("KOSPI")
            elif market_filter == "KOSDAQ":
                all_target = get_all_tickers("KOSDAQ")
            else:
                all_target = get_all_tickers("ALL")
        except Exception:
            if market_filter == "KOSPI":
                all_target = M["KOSPI"]
            elif market_filter == "KOSDAQ":
                all_target = M["KOSDAQ"]
            else:
                all_target = M["KOSPI"] + M["KOSDAQ"]

        cap_limit = {"상위 200개": 200, "상위 500개": 500, "전체": len(all_target)}
        target = all_target[:cap_limit[cap_opt]]

        score_fn  = M["STRATEGY_SCORE"][selected_strategy]
        threshold = M["STRATEGY_THRESHOLD"].get(selected_strategy, 0)

        results = []
        total   = len(target)
        mkt_lbl = f"{market_filter} 시가총액 {cap_opt} ({total}종목)"
        prog    = st.progress(0, text=f"0 / {total} 분석 중 ({mkt_lbl})...")

        # ── 배치 처리 (20개씩 나눠서 메모리 관리) ─────────────────────
        BATCH = 20
        for batch_start in range(0, total, BATCH):
            batch = target[batch_start: batch_start + BATCH]

            for b_idx, (name, ticker) in enumerate(batch):
                global_idx = batch_start + b_idx
                try:
                    sc, detail = score_fn(name, ticker)
                    if not detail.get("_filtered", False) and sc > threshold:
                        # detail에서 꼭 필요한 정보만 압축 저장 (메모리 절약)
                        slim_detail = {
                            k: v for k, v in list(detail.items())[:8]
                            if not str(k).startswith("━━") and k != "데이터"
                        }
                        results.append({
                            "name":   name,
                            "ticker": ticker,
                            "score":  round(sc, 1),
                            "detail": slim_detail,
                        })
                    # ✅ 즉시 메모리 해제
                    del sc, detail
                except Exception:
                    pass

                if (global_idx + 1) % 5 == 0 or global_idx == total - 1:
                    prog.progress(
                        (global_idx + 1) / total,
                        text=f"{global_idx+1} / {total}  통과: {len(results)}개  ({mkt_lbl})"
                    )

            # ✅ 배치 완료 후 가비지 컬렉터 강제 실행
            gc.collect()

        prog.empty()
        results.sort(key=lambda x: -x["score"])
        st.session_state.scan_results[selected_strategy] = results
        st.session_state.scan_meta[selected_strategy] = {
            "market": market_filter, "cap": cap_opt,
            "total": total, "passed": len(results)
        }
        # ✅ 스캔 완료 후 전체 캐시 정리
        gc.collect()

    # ── 결과 표시 (상위 30개만) ───────────────────────────────────────
    key = selected_strategy
    if key in st.session_state.scan_results:
        results = st.session_state.scan_results[key]
        meta    = st.session_state.scan_meta.get(key, {})

        mkt_disp = meta.get("market", "")
        cap_disp = meta.get("cap", "")
        scanned  = meta.get("total", len(results))
        passed   = meta.get("passed", len(results))

        # 상위 30위까지만 표시
        top30 = results[:30]

        st.markdown(
            f"**{key}** — "
            f"{mkt_disp} {cap_disp} 스캔 ({scanned}종목 중 {passed}개 통과) "
            f"**상위 {len(top30)}위 표시**"
        )

        if not top30:
            st.info("조건을 만족하는 종목이 없습니다.")
        else:
            # 순위 포함 테이블
            rows = []
            for rank, r in enumerate(top30, 1):
                sc = r["score"]
                if sc >= 85:   grade = "🏆 A"
                elif sc >= 70: grade = "⭐ B"
                else:           grade = "🔍 C"

                d = r["detail"]
                summary = "  |  ".join(
                    f"{k}: {str(v)[:20]}" for k, v in list(d.items())[:4]
                    if not str(k).startswith("━━") and v and str(v).strip()
                )[:80]

                rows.append({
                    "순위": rank,
                    "등급": grade,
                    "종목명": r["name"],
                    "코드":   r["ticker"],
                    "점수":   sc,
                    "요약":   summary,
                })

            df = pd.DataFrame(rows)
            st.dataframe(
                df,
                hide_index=True,
                width="stretch",
                column_config={
                    "순위":  st.column_config.NumberColumn(width="small"),
                    "등급":  st.column_config.TextColumn(width="small"),
                    "종목명": st.column_config.TextColumn(width="medium"),
                    "코드":  st.column_config.TextColumn(width="small"),
                    "점수":  st.column_config.ProgressColumn(
                        min_value=0, max_value=100, format="%.1f"
                    ),
                    "요약":  st.column_config.TextColumn(width="large"),
                }
            )

            # 상세 보기 + 차트/투자종목 연동
            with st.expander("📋 종목 상세 보기 및 액션"):
                sel_name = st.selectbox(
                    "종목 선택",
                    [f"{i+1}위  {r['name']}" for i, r in enumerate(top30)],
                    key=f"detail_sel_{key}"
                )
                sel_idx  = int(sel_name.split("위")[0]) - 1
                sel_r    = top30[sel_idx]

                # 상세 정보
                d = sel_r["detail"]
                for k, v in d.items():
                    if k == "데이터": continue
                    if str(k).startswith("━━"):
                        st.markdown(f"**{k.replace('━━','').strip()}**")
                    else:
                        col_k, col_v = st.columns([2, 3])
                        col_k.markdown(
                            f"<small style='color:#8b949e'>{k}</small>",
                            unsafe_allow_html=True
                        )
                        col_v.markdown(str(v))

                act1, act2 = st.columns(2)
                with act1:
                    if st.button(f"📊 {sel_r['name']} 차트",
                                  key=f"chart_{key}_{sel_idx}"):
                        st.session_state.chart_ticker = sel_r["ticker"]
                        st.session_state.chart_name   = sel_r["name"]
                        st.info("💡 상단 '차트' 탭으로 이동하세요.")
                with act2:
                    if st.button(f"➕ 투자종목에 추가",
                                  key=f"add_{key}_{sel_idx}"):
                        wl = st.session_state.watchlist
                        if not any(t == sel_r["ticker"] for _, t, _ in wl):
                            wl.append((sel_r["name"], sel_r["ticker"], "KRW"))
                            st.success(f"✅ {sel_r['name']} 투자종목에 추가!")
                        else:
                            st.warning("이미 등록된 종목입니다.")


# ══════════════════════════════════════════════════════════════════════
# TAB2: 투자종목 (Watchlist)
# ══════════════════════════════════════════════════════════════════════
with tab_watch:
    st.markdown("### 📋 투자종목 판정 엔진")

    # ── 종목 추가 폼 ──────────────────────────────────────────────────
    with st.form("wl_add_form", clear_on_submit=True):
        st.markdown("**➕ 종목 추가** — 종목명 또는 코드 중 하나만 입력해도 됩니다")
        fa1, fa2, fa3, fa4 = st.columns([3, 3, 1.5, 1.5])
        with fa1:
            add_name = st.text_input("종목명", placeholder="예: 삼성전자",
                                      key="form_add_name")
        with fa2:
            add_code = st.text_input(
                "티커 코드",
                placeholder="예: 005930  또는  005930.KS  또는  AAPL",
                key="form_add_code"
            )
        with fa3:
            add_curr = st.selectbox("통화", ["KRW", "USD"], key="form_add_curr")
        with fa4:
            st.markdown("<br>", unsafe_allow_html=True)
            submitted = st.form_submit_button("➕ 추가", type="primary",
                                              width="stretch")

        if submitted:
            import re as _re
            raw      = (add_code or "").strip()
            nm_input = (add_name or "").strip()
            cur_sel  = add_curr

            if not raw and not nm_input:
                st.error("종목명 또는 티커 코드를 입력해주세요.")
            else:
                ticker_add = ""
                nm_add     = ""

                if nm_input and not raw:
                    # 종목명 → 코드 자동 조회
                    found_tk2 = next(
                        (t for n, t in (M["KOSPI"] + M["KOSDAQ"]) if n == nm_input), None
                    )
                    if found_tk2:
                        ticker_add = found_tk2
                        nm_add     = nm_input
                    else:
                        st.error(f"'{nm_input}' 종목을 찾을 수 없습니다. 코드를 직접 입력해주세요.")
                else:
                    # 코드 정규화
                    if _re.match(r"^\d{6}$", raw):
                        ticker_add = raw + ".KS"
                    elif "." not in raw and _re.match(r"^\d+$", raw):
                        ticker_add = raw + ".KS"
                    else:
                        ticker_add = raw.upper() if cur_sel == "USD" else raw

                    # 코드 → 종목명 자동 조회
                    if not nm_input:
                        found_nm2 = next(
                            (n for n, t in (M["KOSPI"] + M["KOSDAQ"])
                             if t == ticker_add or t.startswith(raw.split(".")[0])), None
                        )
                        nm_add = found_nm2 if found_nm2 else ticker_add
                        if not found_nm2:
                            try:
                                from api_kr import lookup_company_name, _short_code
                                dart_nm = lookup_company_name(_short_code(ticker_add))
                                if dart_nm:
                                    nm_add = dart_nm
                            except Exception:
                                pass
                    else:
                        nm_add = nm_input

                if ticker_add:
                    wl = st.session_state.watchlist
                    if not any(t == ticker_add for _, t, _ in wl):
                        wl.append((nm_add, ticker_add, cur_sel))
                        st.success(f"✅ **{nm_add}** (`{ticker_add}`) 추가!")
                        st.rerun()
                    else:
                        st.warning(f"⚠️ **{nm_add}**는 이미 등록된 종목입니다.")

    # ── 전체 분석 버튼 ────────────────────────────────────────────────
    col_wa, col_wb = st.columns([2, 1])
    with col_wa:
        run_all = st.button("⚡ 전체 분석", type="primary", width="stretch")
    with col_wb:
        if st.button("🗑️ 목록 초기화", width="stretch"):
            st.session_state.watchlist = []
            if hasattr(st.session_state, "wl_analysis"):
                del st.session_state.wl_analysis
            st.rerun()

    wl = st.session_state.watchlist
    if not wl:
        st.info("위 폼에서 종목을 추가해 주세요.")
    else:
        # 분석 실행
        if run_all:
            cache_new = {}
            prog2 = st.progress(0, text="분석 중...")
            for i, (nm, tk, curr) in enumerate(wl):
                cache_new[tk] = _analyze_single(nm, tk)
                prog2.progress((i + 1) / len(wl), text=f"{nm} 분석 중...")
            prog2.empty()
            st.session_state.wl_analysis = cache_new

        cached = getattr(st.session_state, "wl_analysis", {})

        for idx, (nm, tk, curr) in enumerate(wl):
            res = cached.get(tk)
            with st.container():
                h1c, _, h4c = st.columns([4, 2, 1])
                with h1c:
                    st.markdown(f"**{nm}** `{tk}`")
                with h4c:
                    if st.button("🗑️", key=f"del_{idx}", help="삭제"):
                        st.session_state.watchlist.pop(idx)
                        if tk in getattr(st.session_state, "wl_analysis", {}):
                            del st.session_state.wl_analysis[tk]
                        st.rerun()

                if res and "error" not in res:
                    c1, c2, c3, c4, c5 = st.columns(5)
                    c1.metric("현재가", res["price"])
                    c2.metric("RSI↕",   res["rsi"])
                    c3.metric("MACD",   res["macd"])
                    c4.metric("BB%",    res["bb"])
                    c5.metric("ADX",    res["adx"])

                    s1, s2, s3, s4 = st.columns([2, 2, 1, 1])
                    buy_txt  = res["buy"]
                    sell_txt = res["sell"]
                    buy_cls  = "sig-buy"  if any(x in buy_txt  for x in ["정배열","ADX강","골든","반등","돌파"]) else "sig-wait"
                    sell_cls = "sig-sell" if any(x in sell_txt for x in ["이탈","과열","하락","약화"]) else "sig-hold"
                    s1.markdown(f"<div class='{buy_cls}'>📈 {buy_txt}</div>",  unsafe_allow_html=True)
                    s2.markdown(f"<div class='{sell_cls}'>📉 {sell_txt}</div>", unsafe_allow_html=True)
                    s3.markdown(res["judgment"])
                    s4.markdown(res["hold"])

                    m1, m2, m3, m4 = st.columns(4)
                    m1.markdown(f"**MA:** {res['ma']}")
                    m2.markdown(f"**손절:** {res['stop']}")
                    m3.markdown(f"**1차목표:** {res['tp1']}")
                    m4.markdown(f"**2차목표:** {res['tp2']}")

                    if st.button(f"📊 {nm} 차트 보기", key=f"wchart_{idx}"):
                        st.session_state.chart_ticker = tk
                        st.session_state.chart_name   = nm
                        st.info("💡 상단 '차트' 탭으로 이동하세요.")

                elif res and "error" in res:
                    st.error(f"❌ {res['error']}")
                else:
                    st.caption("분석 전 상태 — '전체 분석' 버튼을 눌러주세요.")

                st.divider()


# ══════════════════════════════════════════════════════════════════════
# TAB3: 차트
# ══════════════════════════════════════════════════════════════════════
with tab_chart:
    st.markdown("### 📊 인터랙티브 차트")

    c1, c2, c3 = st.columns([2, 2, 1])
    with c1:
        chart_name_input = st.text_input(
            "종목명 또는 티커",
            value=st.session_state.chart_name or "",
            placeholder="예: 삼성전자 또는 005930.KS 또는 AAPL",
            key="chart_input"
        )
    with c2:
        wl_names = ["직접 입력"] + [n for n, t, c in st.session_state.watchlist]
        quick_sel = st.selectbox("투자종목에서 선택", wl_names, key="chart_quick")
    with c3:
        st.markdown("<br>", unsafe_allow_html=True)
        show_chart = st.button("📈 차트 표시", type="primary", width="stretch")

    if show_chart or st.session_state.chart_ticker:
        # 종목 결정
        if quick_sel != "직접 입력":
            sel_wl = next(((n, t, c) for n, t, c in st.session_state.watchlist
                           if n == quick_sel), None)
            if sel_wl:
                chart_name_use, chart_tk_use, chart_curr = sel_wl
            else:
                chart_name_use = st.session_state.chart_name or chart_name_input
                chart_tk_use   = st.session_state.chart_ticker or chart_name_input
                chart_curr     = "KRW"
        else:
            import re as _re2
            inp = chart_name_input.strip()
            if inp:
                if _re2.match(r"^\d{6}$", inp):
                    chart_tk_use = inp + ".KS"
                else:
                    found = next(((n, t) for n, t in (M["KOSPI"] + M["KOSDAQ"])
                                  if n == inp or t == inp), None)
                    if found:
                        chart_name_use, chart_tk_use = found
                    else:
                        chart_tk_use = inp
                chart_name_use = inp
                chart_curr     = "KRW"
            else:
                chart_name_use = st.session_state.chart_name or ""
                chart_tk_use   = st.session_state.chart_ticker or ""
                chart_curr     = "KRW"

        if chart_tk_use:
            with st.spinner(f"{chart_name_use} 차트 로딩 중..."):
                try:
                    d = M["fetch_realtime"](chart_tk_use)
                    if d.get("closes"):
                        from web_charts import draw_ohlcv_chart
                        fig = draw_ohlcv_chart(chart_name_use, chart_tk_use, d, chart_curr)
                        st.plotly_chart(fig, width="stretch",
                                        config={"displayModeBar": True,
                                                "scrollZoom": True,
                                                "modeBarButtonsToRemove": ["select2d","lasso2d"],
                                                "toImageButtonOptions": {"format":"png","filename": chart_name_use}})

                        res = _analyze_single(chart_name_use, chart_tk_use)
                        if "error" not in res:
                            st.markdown("#### 📋 현재 지표 요약")
                            mc1,mc2,mc3,mc4,mc5,mc6 = st.columns(6)
                            mc1.metric("현재가", res["price"])
                            mc2.metric("RSI",    res["rsi"])
                            mc3.metric("MACD",   res["macd"])
                            mc4.metric("BB%",    res["bb"])
                            mc5.metric("ADX",    res["adx"])
                            mc6.metric("점수",   str(res["score"]))
                            col_j1, col_j2 = st.columns(2)
                            col_j1.success(f"📈 매수신호: {res['buy']}")
                            col_j2.error(  f"📉 매도신호: {res['sell']}")
                            st.info(f"판정: {res['judgment']}  |  보유: {res['hold']}  |  손절: {res['stop']}")
                    else:
                        st.error(f"데이터를 가져올 수 없습니다: {chart_tk_use}")
                except Exception as e:
                    st.error(f"차트 오류: {e}")
        else:
            st.info("종목명 또는 티커를 입력하거나 투자종목에서 선택하세요.")


# ══════════════════════════════════════════════════════════════════════
# TAB4: 설정
# ══════════════════════════════════════════════════════════════════════
with tab_cfg:
    st.markdown("### ⚙️ 설정")

    # ── API 키 설정 ──────────────────────────────────────────────────
    st.markdown("#### 🔑 API 키 설정")
    st.markdown(
        "KRX·DART 인증키를 설정하면 더 정확한 데이터를 사용합니다. "
        "**Render 배포 시 환경변수**(`KRX_API_KEY`, `DART_API_KEY`)로 설정하는 것을 권장합니다."
    )

    with st.form("api_key_form"):
        fk1, fk2 = st.columns(2)
        with fk1:
            krx_key_input = st.text_input(
                "KRX Open API 키",
                type="password",
                placeholder="data.go.kr 에서 발급",
                help="공공데이터포털(data.go.kr) → KRX 주식 시세 API 신청"
            )
        with fk2:
            dart_key_input = st.text_input(
                "DART Open API 키",
                type="password",
                placeholder="opendart.fss.or.kr 에서 발급",
                help="DART 전자공시시스템 → 인증키 신청·관리"
            )
        if st.form_submit_button("💾 키 저장 (세션 동안 유효)", type="primary"):
            import os
            if krx_key_input.strip():
                os.environ["KRX_API_KEY"]  = krx_key_input.strip()
            if dart_key_input.strip():
                os.environ["DART_API_KEY"] = dart_key_input.strip()
            st.success("✅ API 키 저장됨. (앱 재시작 시 초기화 — 영구 설정은 Render 환경변수 사용)")

    # API 상태
    try:
        from api_kr import api_status
        status = api_status()
        c_s1, c_s2 = st.columns(2)
        c_s1.metric("KRX API",  "✅ 활성" if status["krx_key_set"]  else "❌ 미설정")
        c_s2.metric("DART API", "✅ 활성" if status["dart_key_set"] else "❌ 미설정")
        if status["dart_key_set"] and status["dart_corps_loaded"]:
            st.caption(f"DART 기업 목록 {status['dart_corps_count']:,}개 로드됨")
    except Exception:
        pass

    st.markdown("---")
    st.markdown("#### 📊 전략 파라미터")
    sel_strat = st.selectbox("전략 선택", STRATEGIES, key="cfg_strat")
    params = M["STRATEGY_PARAMS"].get(sel_strat, {})
    if params:
        with st.expander(f"{sel_strat} 파라미터 보기"):
            for key, spec in params.items():
                if not isinstance(spec, dict): continue
                if spec.get("fmt") == "section":
                    st.markdown(f"**{spec.get('label', key)}**")
                    continue
                label = spec.get("label", key)
                val   = spec.get("val")
                st.markdown(
                    f"<small style='color:#8b949e'>{label}: "
                    f"<b style='color:#e6edf3'>{val}</b></small>",
                    unsafe_allow_html=True
                )

    st.markdown("---")
    st.markdown("#### ℹ️ 앱 정보")
    st.markdown("""
| 항목 | 내용 |
|------|------|
| 버전 | v7.0 Web |
| 전략 수 | 9개 |
| 데이터 우선순위 | KRX API → 네이버 → yfinance → 샘플 |
| 재무 우선순위 | DART API → 네이버 → yfinance → 샘플 |
| 차트 | Plotly (터치·핀치줌 지원) |
| 배포 | Render Free Plan |
""")
    st.markdown("**사용 팁**")
    st.markdown("""
- 한국 6자리 코드 자동 변환: `005930` → `005930.KS`
- 종목명만 입력해도 코드 자동 조회
- 스캔 결과 상위 30위만 표시 (전체 통과 수는 헤더 확인)
- Render 무료: 15분 미사용 시 슬립 → 첫 접속 30초 대기
""")
