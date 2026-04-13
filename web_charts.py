# -*- coding: utf-8 -*-
"""
web_charts.py — Plotly 기반 인터랙티브 차트
모바일 터치·핀치줌 지원, 다크 테마
"""
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import numpy as np

# ── 색상 팔레트 (다크 테마) ─────────────────────────────────────────
BG      = "#0d1117"
BG_CARD = "#161b22"
GREEN   = "#2ea043"
RED     = "#cf222e"
YELLOW  = "#d29922"
TEAL    = "#39d353"
ORANGE  = "#fb8500"
PURPLE  = "#8b5cf6"
ACCENT  = "#58a6ff"
TEXT_W  = "#e6edf3"
TEXT_G  = "#8b949e"
GOLD    = "#FFD700"
LIME    = "#AAFF44"


def draw_ohlcv_chart(name: str, ticker: str, d: dict, currency: str = "KRW") -> go.Figure:
    """
    메인 OHLCV 차트 (캔들 + 이평선 + 볼린저 + 거래량 + MACD + ADX + RSI)
    모바일 친화적 레이아웃
    """
    from indicators import sma, ema, macd_calc, bollinger, rsi_calc, adx as calc_adx

    closes  = d.get("closes",  [])
    opens   = d.get("opens",   [])
    highs   = d.get("highs",   [])
    lows    = d.get("lows",    [])
    volumes = d.get("volumes", [])
    dates   = d.get("dates",   [])

    n = len(closes)
    if n < 10:
        fig = go.Figure()
        fig.add_annotation(text="데이터 부족", x=0.5, y=0.5, showarrow=False,
                           font=dict(color=TEXT_W, size=14))
        return fig

    pf = "₩{:,.0f}" if currency == "KRW" else "${:.2f}"
    xs = list(range(n))

    # ── 지표 계산 ────────────────────────────────────────────────────
    def _last(v, default=closes[-1]):
        return next((x for x in reversed(v) if x is not None), default)

    ema10_v  = ema(closes, 10)
    ema20_v  = ema(closes, 20)
    ma50_v   = sma(closes, 50)
    ma150_v  = sma(closes, 150)
    ma200_v  = sma(closes, 200)
    bb_u, bb_m, bb_l = bollinger(closes, 20, 2)
    macd_l, sig_l, hist_l = macd_calc(closes)
    rsi_v    = rsi_calc(closes, 14)
    adx_v, pdi_v, ndi_v = calc_adx(highs, lows, closes, 14)

    # 날짜 레이블
    def _dt(i):
        if i < len(dates) and dates[i]:
            try:
                return dates[i].strftime("%y.%m.%d")
            except Exception:
                return str(dates[i])[:8]
        return str(i)

    date_labels = [_dt(i) for i in xs]

    # ── Figure (5행: 가격4 + 거래량1.3 + MACD1.3 + ADX1.0 + RSI1.0) ─
    fig = make_subplots(
        rows=5, cols=1,
        shared_xaxes=True,
        row_heights=[4.0, 1.3, 1.3, 1.0, 1.0],
        vertical_spacing=0.02,
        subplot_titles=["", "", "MACD", "ADX(14)", "RSI(14)"]
    )

    # ── Row1: 캔들 ───────────────────────────────────────────────────
    colors_candle = [GREEN if c >= o else RED
                     for c, o in zip(closes, opens if opens else closes)]
    fig.add_trace(go.Candlestick(
        x=date_labels,
        open=opens if opens else closes,
        high=highs  if highs  else closes,
        low=lows    if lows   else closes,
        close=closes,
        increasing_line_color=GREEN,
        decreasing_line_color=RED,
        name="캔들",
        showlegend=False,
    ), row=1, col=1)

    # 볼린저밴드
    bb_xi = [i for i, v in enumerate(bb_u) if v is not None]
    if bb_xi:
        bu_y = [v for v in bb_u if v is not None]
        bm_y = [v for v in bb_m if v is not None]
        bl_y = [v for v in bb_l if v is not None]
        dl   = [date_labels[i] for i in bb_xi]
        fig.add_trace(go.Scatter(x=dl, y=bu_y, line=dict(color=PURPLE, width=0.8, dash="dash"),
                                  name="BB상단", showlegend=False), row=1, col=1)
        fig.add_trace(go.Scatter(x=dl, y=bm_y, line=dict(color=PURPLE, width=0.6, dash="dot"),
                                  name="BB중심", showlegend=False,
                                  fill="tonexty", fillcolor="rgba(139,92,246,0.05)"), row=1, col=1)
        fig.add_trace(go.Scatter(x=dl, y=bl_y, line=dict(color=PURPLE, width=0.8, dash="dash"),
                                  name="BB하단", showlegend=False,
                                  fill="tonexty", fillcolor="rgba(139,92,246,0.05)"), row=1, col=1)

    # 이평선 (EMA10~MA200)
    ma_lines = [
        (ema10_v, LIME,   "EMA10", 1.3),
        (ema20_v, TEAL,   "EMA20", 1.2),
        (ma50_v,  YELLOW, "MA50",  1.1),
        (ma150_v, ORANGE, "MA150", 1.0),
        (ma200_v, ACCENT, "MA200", 1.1),
    ]
    for ma_v, col, lbl, lw in ma_lines:
        xi = [i for i, v in enumerate(ma_v) if v is not None]
        if xi:
            yl = [v for v in ma_v if v is not None]
            dl = [date_labels[i] for i in xi]
            fig.add_trace(go.Scatter(x=dl, y=yl,
                                      line=dict(color=col, width=lw),
                                      name=lbl, showlegend=True), row=1, col=1)

    # ── Row2: 거래량 ─────────────────────────────────────────────────
    vol_colors = [GREEN if c >= (opens[i] if opens else c) else RED
                  for i, c in enumerate(closes)]
    fig.add_trace(go.Bar(x=date_labels, y=volumes,
                          marker_color=vol_colors, opacity=0.7,
                          name="거래량", showlegend=False), row=2, col=1)

    # ── Row3: MACD ───────────────────────────────────────────────────
    hist_xi = [i for i, v in enumerate(hist_l) if v is not None]
    if hist_xi:
        hv = [hist_l[i] for i in hist_xi]
        hdl = [date_labels[i] for i in hist_xi]
        bar_colors = [GREEN if v >= 0 else RED for v in hv]
        fig.add_trace(go.Bar(x=hdl, y=hv, marker_color=bar_colors,
                              opacity=0.7, name="히스토그램", showlegend=False), row=3, col=1)

    macd_xi = [i for i, v in enumerate(macd_l) if v is not None]
    if macd_xi:
        fig.add_trace(go.Scatter(
            x=[date_labels[i] for i in macd_xi],
            y=[macd_l[i] for i in macd_xi],
            line=dict(color=ACCENT, width=0.9), name="MACD"), row=3, col=1)

    sig_xi = [i for i, v in enumerate(sig_l) if v is not None]
    if sig_xi:
        fig.add_trace(go.Scatter(
            x=[date_labels[i] for i in sig_xi],
            y=[sig_l[i] for i in sig_xi],
            line=dict(color=ORANGE, width=0.9), name="Signal"), row=3, col=1)

    # ── Row4: ADX ────────────────────────────────────────────────────
    adx_xi = [i for i, v in enumerate(adx_v) if v is not None]
    pdi_xi = [i for i, v in enumerate(pdi_v) if v is not None]
    ndi_xi = [i for i, v in enumerate(ndi_v) if v is not None]

    if adx_xi:
        fig.add_trace(go.Scatter(
            x=[date_labels[i] for i in adx_xi],
            y=[adx_v[i] for i in adx_xi],
            line=dict(color=GOLD, width=1.2), name="ADX"), row=4, col=1)
    if pdi_xi:
        fig.add_trace(go.Scatter(
            x=[date_labels[i] for i in pdi_xi],
            y=[pdi_v[i] for i in pdi_xi],
            line=dict(color=GREEN, width=0.8), name="+DI"), row=4, col=1)
    if ndi_xi:
        fig.add_trace(go.Scatter(
            x=[date_labels[i] for i in ndi_xi],
            y=[ndi_v[i] for i in ndi_xi],
            line=dict(color=RED, width=0.8), name="-DI"), row=4, col=1)
    # ADX 기준선
    fig.add_hline(y=25, line_dash="dash", line_color=TEXT_G,
                  line_width=0.7, row=4, col=1)
    fig.add_hline(y=40, line_dash="dot",  line_color=GOLD,
                  line_width=0.5, row=4, col=1)

    # ── Row5: RSI ────────────────────────────────────────────────────
    rsi_xi = [i for i, v in enumerate(rsi_v) if v is not None]
    if rsi_xi:
        rsi_yl = [rsi_v[i] for i in rsi_xi]
        rsi_dl = [date_labels[i] for i in rsi_xi]
        # 과매수/과매도 영역 채색
        fig.add_trace(go.Scatter(
            x=rsi_dl, y=rsi_yl,
            line=dict(color=TEAL, width=1.0),
            fill="tonexty", fillcolor="rgba(0,0,0,0)",
            name="RSI(14)"), row=5, col=1)

    # RSI 기준선
    for level, color in [(70, RED), (50, TEXT_G), (30, GREEN)]:
        fig.add_hline(y=level, line_dash="dash", line_color=color,
                      line_width=0.6, opacity=0.7, row=5, col=1)

    # ── 현재가 주석 ──────────────────────────────────────────────────
    cur = closes[-1]
    fig.add_hline(y=cur, line_dash="dot", line_color=TEXT_W,
                  line_width=0.8, row=1, col=1,
                  annotation_text=pf.format(cur),
                  annotation_position="right",
                  annotation_font=dict(color=TEXT_W, size=10))

    # ── 레이아웃 ────────────────────────────────────────────────────
    adx_c   = _last(adx_v, 0)
    pdi_c   = _last(pdi_v, 0)
    ndi_c   = _last(ndi_v, 0)
    rsi_c   = _last(rsi_v, 50)
    macd_c  = _last(macd_l, 0)
    sig_c   = _last(sig_l, 0)
    adx_str = f"ADX {adx_c:.0f}" if adx_c else ""
    dir_str = "▲상승" if pdi_c > ndi_c else "▼하락"
    title   = (f"{name} ({ticker})  |  {pf.format(cur)}  |  "
               f"RSI {rsi_c:.0f}  |  {'MACD골든↑' if macd_c>sig_c else 'MACD하락'}  |  "
               f"{adx_str} {dir_str}")

    fig.update_layout(
        title=dict(text=title, font=dict(color=TEXT_W, size=13), x=0.01),
        paper_bgcolor=BG,
        plot_bgcolor=BG_CARD,
        font=dict(color=TEXT_W, size=10),
        height=700,
        margin=dict(l=50, r=20, t=50, b=30),
        legend=dict(
            orientation="h", yanchor="bottom", y=1.01,
            xanchor="left", x=0,
            bgcolor="rgba(0,0,0,0)", font=dict(size=9)
        ),
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
    )

    # 각 row 배경·그리드
    for row in range(1, 6):
        fig.update_xaxes(
            showgrid=True, gridcolor="#21262d", gridwidth=0.5,
            tickfont=dict(size=9, color=TEXT_G),
            row=row, col=1
        )
        fig.update_yaxes(
            showgrid=True, gridcolor="#21262d", gridwidth=0.5,
            tickfont=dict(size=9, color=TEXT_G),
            row=row, col=1
        )

    fig.update_yaxes(range=[0, 80], row=4, col=1)
    fig.update_yaxes(range=[0, 100], row=5, col=1)
    fig.update_xaxes(showticklabels=True, row=5, col=1)
    for row in range(1, 5):
        fig.update_xaxes(showticklabels=False, row=row, col=1)

    return fig
