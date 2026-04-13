#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
indicators.py — 기술 지표 계산 모듈 v8
  · sma / ema / macd_calc / bollinger / rsi_calc / adr_calc
  · RS 실계산 (IBD 방식 모멘텀 백분위)
  · 지지선 / 저항선 / 추세선 계산
  · 주봉 지표 계산
  · 거래대금 계산
  · _get_indicators (개별 종목 지표 종합)
"""
import math
import threading
from data_fetcher import fetch_realtime, _is_kr_ticker
from data_fetcher import _indicator_cache

# ══ 기본 지표 함수 ════════════════════════════════════════════════════

def sma(data, n):
    result = []
    for i in range(len(data)):
        if i < n - 1:
            result.append(None)
        else:
            result.append(sum(data[i-n+1:i+1]) / n)
    return result

def ema(data, n):
    result = []
    k = 2 / (n + 1)
    prev = None
    for i, v in enumerate(data):
        if v is None:
            result.append(None)
            continue
        if prev is None:
            if i >= n - 1:
                vals = [x for x in data[max(0,i-n+1):i+1] if x is not None]
                if len(vals) == n:
                    prev = sum(vals) / n
                    result.append(prev)
                else:
                    result.append(None)
            else:
                result.append(None)
        else:
            prev = v * k + prev * (1 - k)
            result.append(prev)
    return result

def macd_calc(closes):
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    macd_line = [
        (a - b) if (a is not None and b is not None) else None
        for a, b in zip(ema12, ema26)
    ]
    non_none = [(i, v) for i, v in enumerate(macd_line) if v is not None]
    sig_full = [None] * len(macd_line)
    sig_vals = ema([v for _, v in non_none], 9)
    for j, (i, _) in enumerate(non_none):
        sig_full[i] = sig_vals[j]
    hist = [
        (m - s) if (m is not None and s is not None) else None
        for m, s in zip(macd_line, sig_full)
    ]
    return macd_line, sig_full, hist

def bollinger(closes, n=20, k=2):
    mid = sma(closes, n)
    upper, lower = [], []
    for i in range(len(closes)):
        if mid[i] is None:
            upper.append(None); lower.append(None)
        else:
            window = closes[i-n+1:i+1]
            mean = sum(window) / n
            std = math.sqrt(sum((x - mean)**2 for x in window) / n)
            upper.append(mid[i] + k * std)
            lower.append(mid[i] - k * std)
    return upper, mid, lower

def rsi_calc(closes, n=14):
    result = [None] * n
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    if len(gains) < n:
        return result
    avg_gain = sum(gains[:n]) / n
    avg_loss = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        if avg_loss == 0:
            result.append(100.0)
        else:
            rs = avg_gain / avg_loss
            result.append(100 - 100 / (1 + rs))
        avg_gain = (avg_gain * (n-1) + gains[i]) / n
        avg_loss = (avg_loss * (n-1) + losses[i]) / n
    result.append(100 - 100 / (1 + avg_gain/avg_loss) if avg_loss else 100)
    return result

def adr_calc(highs, lows):
    """ADR = 평균(고가/저가) 14일 이동평균"""
    ratios = [h/l if l > 0 else 1.0 for h, l in zip(highs, lows)]
    return sma(ratios, 14), (ratios[-1] if ratios else 1.0)


# ══ RS 실계산 (IBD 방식) ═════════════════════════════════════════════

def calc_rs_score(closes):
    """
    IBD 방식 RS 원점수 계산.
    가중치: 최근3개월(40%) + 6개월(20%) + 9개월(20%) + 12개월(20%)
    반환: 원점수 (백분위 변환은 find_stocks_ranked에서 전종목 대상으로 수행)
    """
    n = len(closes)
    if n < 21:
        return 0.0
    def _ret(p):
        if n >= p:
            return (closes[-1] - closes[-p]) / max(closes[-p], 1) * 100
        return (closes[-1] - closes[0]) / max(closes[0], 1) * 100

    r3m  = _ret(63)
    r6m  = _ret(126)
    r9m  = _ret(189)
    r12m = _ret(252)
    return r3m * 0.40 + r6m * 0.20 + r9m * 0.20 + r12m * 0.20


def calc_rs_percentile(scores_dict):
    """
    전종목 RS 원점수 딕셔너리 → 백분위(1~99) 딕셔너리.
    scores_dict: {ticker: raw_score}
    반환: {ticker: percentile_1_99}
    """
    if not scores_dict:
        return {}
    items = sorted(scores_dict.items(), key=lambda x: x[1])
    n = len(items)
    result = {}
    for rank, (ticker, score) in enumerate(items, 1):
        pct = max(1, min(99, int(rank / n * 99)))
        result[ticker] = pct
    return result


# ══ 지지선 / 저항선 / 추세선 ═════════════════════════════════════════

def find_pivot_points(highs, lows, closes, window=5, lookback=120):
    """
    로컬 고점(저항) / 저점(지지) 피벗 포인트 탐색.
    window: 양쪽 n봉보다 높으면 고점, 낮으면 저점
    lookback: 최근 N봉만 분석
    반환: (pivot_highs, pivot_lows)
      pivot_highs: [(index, price), ...]  — 저항선 후보
      pivot_lows:  [(index, price), ...]  — 지지선 후보
    """
    n = len(closes)
    lb = min(lookback, n)
    offset = n - lb  # 전체 인덱스 기준 오프셋

    h_slice = highs[-lb:]
    l_slice = lows[-lb:]

    pivot_highs = []
    pivot_lows  = []

    for i in range(window, lb - window):
        # 로컬 고점: 양쪽 window봉보다 높음
        if all(h_slice[i] >= h_slice[j]
               for j in range(i-window, i+window+1) if j != i):
            pivot_highs.append((offset + i, h_slice[i]))
        # 로컬 저점: 양쪽 window봉보다 낮음
        if all(l_slice[i] <= l_slice[j]
               for j in range(i-window, i+window+1) if j != i):
            pivot_lows.append((offset + i, l_slice[i]))

    return pivot_highs, pivot_lows


def cluster_levels(pivots, tolerance_pct=1.5, min_touches=2):
    """
    피벗 포인트를 클러스터링하여 주요 지지/저항 레벨 추출.
    tolerance_pct: 같은 레벨로 묶는 가격 허용 오차 (%)
    min_touches: 최소 n번 접촉해야 유효 레벨
    반환: [(level_price, touch_count, last_index), ...]
    """
    if not pivots:
        return []

    sorted_p = sorted(pivots, key=lambda x: x[1])
    clusters = []

    for idx, price in sorted_p:
        merged = False
        for cl in clusters:
            ref = cl["center"]
            if abs(price - ref) / max(ref, 1) * 100 <= tolerance_pct:
                cl["prices"].append(price)
                cl["indices"].append(idx)
                cl["center"] = sum(cl["prices"]) / len(cl["prices"])
                merged = True
                break
        if not merged:
            clusters.append({"center": price, "prices": [price], "indices": [idx]})

    result = []
    for cl in clusters:
        if len(cl["prices"]) >= min_touches:
            result.append((
                cl["center"],
                len(cl["prices"]),
                max(cl["indices"])
            ))

    # 현재가 기준 근접 순 정렬
    return sorted(result, key=lambda x: abs(x[2] - len(sorted_p)), reverse=False)


def calc_trendlines(pivot_lows, pivot_highs, n_total):
    """
    추세선 계산 — 파형 기반 단기/중기/장기 3종.

    상승추세선: Swing Low(파형 저점)들 중 점점 높아지는 저점들의 외곽선
      → 저점1 < 저점2 < 저점3 순서로 증가하는 봉의 저가를 연결
      → 가격이 이 선 위에 있을 때 지지 역할

    하락추세선: Swing High(파형 고점)들 중 점점 낮아지는 고점들의 외곽선
      → 고점1 > 고점2 > 고점3 순서로 감소하는 봉의 고가를 연결
      → 가격이 이 선 아래에 있을 때 저항 역할

    반환: {
        "up":       단기 상승추세선,   "down":      단기 하락추세선,
        "up_mid":   중기 상승추세선,   "down_mid":  중기 하락추세선,
        "up_long":  장기 상승추세선,   "down_long": 장기 하락추세선,
    }
    각 값: (slope, intercept, x1, x2) or None
    """

    def _rising_trendline(lows_pts, n_recent, n_total):
        """
        상승추세선: 가장 최근 n_recent개 파형 저점 중
        점점 높아지는(Higher Lows) 패턴에서 가장 잘 맞는 직선.
        직선은 모든 저점이 선 위 또는 선 위에 닿아야 함.
        """
        pts = sorted(lows_pts, key=lambda x: x[0])[-n_recent:]
        if len(pts) < 2:
            return None

        best = None
        best_score = -1

        for i in range(len(pts)):
            xi, yi = pts[i]
            for j in range(i+1, len(pts)):
                xj, yj = pts[j]
                if xj <= xi or yj <= yi:  # 반드시 Higher Low
                    continue
                slope = (yj - yi) / (xj - xi)
                intercept = yi - slope * xi

                # 이 직선이 모든 저점 아래에 있는지 확인 (모든 저점이 선 위에)
                valid = True
                touches = 0
                for xk, yk in pts:
                    line_y = slope * xk + intercept
                    if yk < line_y * 0.995:  # 저점이 선 아래로 가면 무효
                        valid = False; break
                    if abs(yk - line_y) / max(line_y, 1) < 0.02:
                        touches += 1

                if valid and touches >= 2:
                    score = touches + (xj - xi) / n_total
                    if score > best_score:
                        best_score = score
                        best = (slope, intercept, xi, n_total - 1)

        return best

    def _falling_trendline(highs_pts, n_recent, n_total):
        """
        하락추세선: 가장 최근 n_recent개 파형 고점 중
        점점 낮아지는(Lower Highs) 패턴에서 가장 잘 맞는 직선.
        직선은 모든 고점이 선 아래 또는 선에 닿아야 함.
        """
        pts = sorted(highs_pts, key=lambda x: x[0])[-n_recent:]
        if len(pts) < 2:
            return None

        best = None
        best_score = -1

        for i in range(len(pts)):
            xi, yi = pts[i]
            for j in range(i+1, len(pts)):
                xj, yj = pts[j]
                if xj <= xi or yj >= yi:  # 반드시 Lower High
                    continue
                slope = (yj - yi) / (xj - xi)
                intercept = yi - slope * xi

                valid = True
                touches = 0
                for xk, yk in pts:
                    line_y = slope * xk + intercept
                    if yk > line_y * 1.005:  # 고점이 선 위로 가면 무효
                        valid = False; break
                    if abs(yk - line_y) / max(line_y, 1) < 0.02:
                        touches += 1

                if valid and touches >= 2:
                    score = touches + (xj - xi) / n_total
                    if score > best_score:
                        best_score = score
                        best = (slope, intercept, xi, n_total - 1)

        return best

    all_lows  = sorted(pivot_lows,  key=lambda x: x[0])
    all_highs = sorted(pivot_highs, key=lambda x: x[0])

    # ── 기간별 피벗 범위 한정 (단기: 최근 30%, 중기: 60%, 장기: 전체) ──
    # "최근" 기준을 봉 인덱스로 제한하여 현재와 괴리를 방지
    cutoff_s = max(0, int(n_total * 0.70))   # 단기: 최근 30% 구간
    cutoff_m = max(0, int(n_total * 0.40))   # 중기: 최근 60% 구간

    lows_s  = [p for p in all_lows  if p[0] >= cutoff_s]
    highs_s = [p for p in all_highs if p[0] >= cutoff_s]
    lows_m  = [p for p in all_lows  if p[0] >= cutoff_m]
    highs_m = [p for p in all_highs if p[0] >= cutoff_m]

    # 최근 구간에 피벗이 너무 적으면 범위 확장
    if len(lows_s)  < 3: lows_s  = all_lows [-6:]
    if len(highs_s) < 3: highs_s = all_highs[-6:]
    if len(lows_m)  < 4: lows_m  = all_lows [-10:]
    if len(highs_m) < 4: highs_m = all_highs[-10:]

    up_s  = _rising_trendline(lows_s,   len(lows_s),  n_total)
    dn_s  = _falling_trendline(highs_s, len(highs_s), n_total)
    up_m  = _rising_trendline(lows_m,   len(lows_m),  n_total)
    dn_m  = _falling_trendline(highs_m, len(highs_m), n_total)
    up_l  = _rising_trendline(all_lows,  len(all_lows),  n_total)
    dn_l  = _falling_trendline(all_highs, len(all_highs), n_total)

    # 동일한 추세선 중복 제거
    def _same(a, b):
        if a is None or b is None: return False
        return abs(a[0]-b[0]) < 1e-7 and abs(a[1]-b[1]) < 1e-3

    if _same(up_s, up_m):  up_m = None
    if _same(up_m, up_l):  up_l = None
    if _same(up_s, up_l):  up_l = None
    if _same(dn_s, dn_m):  dn_m = None
    if _same(dn_m, dn_l):  dn_l = None
    if _same(dn_s, dn_l):  dn_l = None

    return {
        "up":        up_s,  "down":      dn_s,
        "up_mid":    up_m,  "down_mid":  dn_m,
        "up_long":   up_l,  "down_long": dn_l,
    }



# ══ 주봉 지표 ═════════════════════════════════════════════════════════

def calc_weekly_indicators(closes, highs, lows):
    """
    일봉 → 주봉 지표 근사 계산 (멀티 타임프레임).
    주봉 MA = 일봉 MA × 5 근사:
      주봉 MA10 ≈ 일봉 MA50   (10주)
      주봉 MA30 ≈ 일봉 MA150  (30주)
    반환:
      w_ma10        : 주봉 MA10 현재값
      w_ma30        : 주봉 MA30 현재값
      w_high52      : 52주 고점
      w_low52       : 52주 저점
      w_from_52h    : 52주 고점 대비 %
      w_above_ma10  : 현재가 > 주봉MA10 여부
      w_above_ma30  : 현재가 > 주봉MA30 여부
      w_ma10_slope  : 주봉MA10 기울기 (5봉 전 대비 %)
      w_trend_ok    : 주봉 기준 정배열 여부 (cur>MA10>MA30)
      w_rsi         : 주봉 RSI 근사 (일봉 RSI 70일 기준)
    """
    if len(closes) < 50:
        return {
            "w_ma10": 0, "w_ma30": 0, "w_high52": 0, "w_low52": 0,
            "w_from_52h": 0, "w_above_ma10": True, "w_above_ma30": True,
            "w_ma10_slope": 0, "w_trend_ok": True, "w_rsi": 50,
        }

    cur = closes[-1]

    # 주봉 MA (일봉 MA로 근사)
    ma50_v  = sma(closes, 50)    # 주봉 MA10
    ma150_v = sma(closes, 150)   # 주봉 MA30
    w_ma10  = next((v for v in reversed(ma50_v)  if v is not None), cur)
    w_ma30  = next((v for v in reversed(ma150_v) if v is not None), cur)

    # 주봉 MA10 기울기 (25봉 전 = 약 5주 전 대비)
    ma50_hist = [v for v in ma50_v if v is not None]
    if len(ma50_hist) >= 6:
        w_ma10_slope = (ma50_hist[-1] - ma50_hist[-6]) / max(ma50_hist[-6], 1) * 100
    else:
        w_ma10_slope = 0

    # 52주 고저
    n252 = min(252, len(closes))
    w_high52   = max(highs[-n252:])
    w_low52    = min(lows[-n252:])
    w_from_52h = (cur - w_high52) / max(w_high52, 1) * 100

    # 주봉 RSI 근사: 일봉 RSI(70) ≈ 주봉 RSI(14)
    rsi70 = rsi_calc(closes, 70)
    w_rsi = next((v for v in reversed(rsi70) if v is not None), 50)

    # 정배열 판단
    w_above_ma10  = cur > w_ma10  if w_ma10  > 0 else True
    w_above_ma30  = cur > w_ma30  if w_ma30  > 0 else True
    w_trend_ok    = (w_ma10 > 0 and w_ma30 > 0 and cur > w_ma10 > w_ma30)

    return {
        "w_ma10":       w_ma10,
        "w_ma30":       w_ma30,
        "w_high52":     w_high52,
        "w_low52":      w_low52,
        "w_from_52h":   w_from_52h,
        "w_above_ma10": w_above_ma10,
        "w_above_ma30": w_above_ma30,
        "w_ma10_slope": w_ma10_slope,
        "w_trend_ok":   w_trend_ok,
        "w_rsi":        w_rsi,
    }


# ══ 거래대금 계산 ═════════════════════════════════════════════════════

def calc_trading_value(closes, volumes, n=20):
    """
    평균 거래대금 계산 (가격 × 거래량).
    반환: 20일 평균 거래대금 (원 또는 달러)
    """
    if not closes or not volumes or len(closes) < 2:
        return 0
    n_use = min(n, len(closes), len(volumes))
    tv = [c * v for c, v in zip(closes[-n_use:], volumes[-n_use:])]
    return sum(tv) / len(tv) if tv else 0


# ══ VCP 수축도 ═══════════════════════════════════════════════════════

def _vcp_score(closes, highs, lows):
    if len(closes) < 60:
        return 0.5
    n = min(len(closes), 60)
    seg = n // 4
    ranges = []
    for i in range(4):
        s = i * seg
        e = s + seg
        h_seg = highs[-(n - s): -(n - e) if e < n else None]
        l_seg = lows[-(n - s): -(n - e) if e < n else None]
        if h_seg and l_seg:
            rng = (max(h_seg) - min(l_seg)) / (min(l_seg) + 1e-9)
            ranges.append(rng)
    if len(ranges) < 3:
        return 0.5
    contracting = sum(1 for i in range(1, len(ranges)) if ranges[i] < ranges[i-1])
    return contracting / (len(ranges) - 1)


# ══ 종합 지표 계산 ════════════════════════════════════════════════════

def _get_indicators(ticker):
    """
    실제 데이터로 기술적 지표 종합 계산.
    v8 추가:
      · trading_value_20d: 20일 평균 거래대금
      · rs_raw: IBD 방식 RS 원점수 (백분위는 find_stocks_ranked에서 계산)
      · ema20: 20일 EMA 현재값
      · w_*: 주봉 지표
      · pivot_highs/lows: 지지/저항 피벗 포인트
    """
    if ticker in _indicator_cache:
        return _indicator_cache[ticker]

    d = fetch_realtime(ticker)
    c = d["closes"]
    h = d["highs"]
    l = d["lows"]
    v = d["volumes"]

    if not c:
        result = dict(
            closes=[], highs=[], lows=[], volumes=[],
            realtime=False, cur=1,
            prev52h=0, prev52l=0,
            ma50=0, ma150=0, ma200=0,
            ma50_slope=0, ma150_slope=0,
            rsi=50, macd_hist=0, macd_rising=False,
            vol_ratio=1, vol_avg=1,
            change_1m=0, change_3m=0, change_6m=0,
            vcp=0.5, rs=50, rs_raw=0,
            ema20=0, trading_value_20d=0,
            w_ma10=0, w_ma30=0, w_high52=0, w_low52=0,
            w_from_52h=0, w_above_ma10=True,
        )
        _indicator_cache[ticker] = result
        return result

    # 이평선
    ma50_raw  = sma(c, 50)
    ma150_raw = sma(c, 150)
    ma200_raw = sma(c, 200)
    ema20_raw = ema(c, 20)

    ma50_vals  = [x for x in ma50_raw  if x is not None]
    ma150_vals = [x for x in ma150_raw if x is not None]
    ma200_vals = [x for x in ma200_raw if x is not None]

    # RSI
    rsi_raw  = rsi_calc(c)
    rsi_vals = [x for x in rsi_raw if x is not None]

    # MACD
    _, _, hist_l = macd_calc(c)
    macd_v = [x for x in hist_l if x is not None]

    # 거래량 평균
    vol_avg = sum(v[-20:]) / 20 if len(v) >= 20 else (sum(v) / len(v) if v else 1)

    # 거래대금
    tv20 = calc_trading_value(c, v, 20)

    # RS 원점수 (IBD 방식)
    rs_raw = calc_rs_score(c)
    # 추정 RS (단일 종목, 백분위 변환 전): 50 중심으로 단순 정규화
    rs_est = max(1, min(99, 50 + rs_raw * 0.8))

    # EMA20
    ema20_cur = next((x for x in reversed(ema20_raw) if x is not None), c[-1] if c else 0)

    # VCP
    vcp = _vcp_score(c, h, l)

    # 주봉 지표
    weekly = calc_weekly_indicators(c, h, l)

    result = dict(
        closes=c, highs=h, lows=l, volumes=v,
        realtime=d.get("realtime", False),
        cur=c[-1] if c else 1,
        prev52h=max(c[-252:] if len(c) >= 252 else c) if c else 0,
        prev52l=min(c[-252:] if len(c) >= 252 else c) if c else 0,
        ma50=ma50_vals[-1]   if ma50_vals  else 0,
        ma150=ma150_vals[-1] if ma150_vals else 0,
        ma200=ma200_vals[-1] if ma200_vals else 0,
        ma50_slope=(ma50_vals[-1]-ma50_vals[-5])/ma50_vals[-5]*100   if len(ma50_vals)>=5  else 0,
        ma150_slope=(ma150_vals[-1]-ma150_vals[-5])/ma150_vals[-5]*100 if len(ma150_vals)>=5 else 0,
        rsi=rsi_vals[-1] if rsi_vals else 50,
        macd_hist=macd_v[-1] if macd_v else 0,
        macd_rising=(macd_v[-1] > macd_v[-2]) if len(macd_v) >= 2 else False,
        vol_ratio=v[-1]/vol_avg if vol_avg > 0 else 1,
        vol_avg=vol_avg,
        change_1m=(c[-1]-c[-21])/c[-21]*100   if len(c) >= 21  else 0,
        change_3m=(c[-1]-c[-63])/c[-63]*100   if len(c) >= 63  else 0,
        change_6m=(c[-1]-c[-126])/c[-126]*100 if len(c) >= 126 else 0,
        vcp=vcp,
        rs=rs_est,
        rs_raw=rs_raw,
        ema20=ema20_cur,
        trading_value_20d=tv20,
        # 주봉 지표
        **weekly,
    )
    _indicator_cache[ticker] = result
    return result


def adx(highs, lows, closes, period=14):
    """
    ADX (Average Directional Index) 계산.
    반환: (adx_line, plus_di, minus_di) — 각각 길이=len(closes)의 리스트(None 포함)
    ADX: 추세 강도 (방향 무관) — 25 이상이면 추세 있음, 40 이상이면 강한 추세
    +DI > -DI: 상승 추세 / -DI > +DI: 하락 추세
    """
    n = len(closes)
    if n < period + 2 or not highs or not lows:
        return [None]*n, [None]*n, [None]*n

    tr_list, pdm_list, ndm_list = [], [], []
    for i in range(1, n):
        h, l, pc = highs[i], lows[i], closes[i-1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        pdm = max(highs[i] - highs[i-1], 0) if (highs[i] - highs[i-1]) > (lows[i-1] - lows[i]) else 0
        ndm = max(lows[i-1] - lows[i], 0) if (lows[i-1] - lows[i]) > (highs[i] - highs[i-1]) else 0
        tr_list.append(tr); pdm_list.append(pdm); ndm_list.append(ndm)

    def _smooth(arr, p):
        """Wilder 스무딩 (ADX 표준): 첫 값은 단순평균, 이후 지수 스무딩"""
        result = [None] * len(arr)
        if len(arr) < p: return result
        seed = sum(arr[:p]) / p   # 첫 값 = 단순평균
        result[p-1] = seed
        for i in range(p, len(arr)):
            seed = (seed * (p-1) + arr[i]) / p   # Wilder 스무딩 공식
            result[i] = seed
        return result

    atr_s  = _smooth(tr_list,  period)
    pdm_s  = _smooth(pdm_list, period)
    ndm_s  = _smooth(ndm_list, period)

    pdi_arr, ndi_arr, dx_arr = [], [], []
    for atr_v, pdm_v, ndm_v in zip(atr_s, pdm_s, ndm_s):
        if atr_v is None or atr_v == 0:
            pdi_arr.append(None); ndi_arr.append(None); dx_arr.append(None)
        else:
            pdi = pdm_v / atr_v * 100
            ndi = ndm_v / atr_v * 100
            dx  = abs(pdi - ndi) / max(pdi + ndi, 0.001) * 100
            pdi_arr.append(pdi); ndi_arr.append(ndi); dx_arr.append(dx)

    # ADX = Wilder 스무딩된 DX
    dx_valid = [v for v in dx_arr if v is not None]
    adx_arr  = [None] * len(dx_arr)
    offset   = next((i for i,v in enumerate(dx_arr) if v is not None), None)
    if offset is None or len(dx_valid) < period:
        return [None]*n, [None]*n, [None]*n

    # dx_arr의 첫 유효값부터 스무딩
    adx_smooth = _smooth(dx_valid, period)
    j = 0
    for i in range(len(dx_arr)):
        if dx_arr[i] is not None:
            adx_arr[i] = adx_smooth[j]
            j += 1

    # 앞에 None 1개 붙여서 closes와 길이 맞추기
    pad = [None]
    return pad + adx_arr, pad + pdi_arr, pad + ndi_arr
