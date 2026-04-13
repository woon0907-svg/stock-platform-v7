#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
strategies.py — 전략별 종목 스코어링
각 전략의 투자 목적:
  1. 조엘 그린블라트 : 자본수익률(ROC) + 이익수익률(EY) 합산 등수 → 저평가 우량주
  2. 미너비니 SEPA   : 트렌드 템플릿 + VCP → Stage2 주도주 돌파
  3. 윌리엄 오닐     : CAN SLIM 7요소 → 실적+수급+기술 동시 충족
  4. 쿨라매기        : 모멘텀 강한 주도주 + 에피소딕피벗 후 횡보 돌파
  5. 오닐+미너비니   : CAN SLIM × VCP 교집합 → 최강 선별
  6. 로스 카메론     : 갭앤고 당일 모멘텀 단타
  7. 스윙 투자       : 재무방어 + BB수축 에너지 응축 → 2일~2주 보유
"""
import threading
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from config import PARALLEL_WORKERS, HAS_YF, yf
from data_fetcher import fetch_realtime, _get_financial_indicators
from indicators import (_get_indicators, sma, ema, bollinger, macd_calc, rsi_calc,
                         calc_rs_score, calc_rs_percentile,
                         find_pivot_points, cluster_levels, calc_trading_value,
                         calc_weekly_indicators)
from data_fetcher import _indicator_cache
from strategy_params import get_param, STRATEGY_PARAMS_DEFAULT

# RS 백분위 캐시 — find_stocks_ranked에서 전종목 스캔 후 계산하여 저장
_rs_percentile_cache = {}   # {ticker: percentile_1_99}


# ─────────────────────────────────────────────────────────
# 공통 헬퍼
# ─────────────────────────────────────────────────────────
def _g(ind, key, default=0.0):
    """ind dict 안전 읽기."""
    v = ind.get(key)
    if v is None:
        return float(default)
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)

def _fmt_price(val, ticker):
    """가격을 통화에 맞게 포맷."""
    if _is_kr(ticker):
        return f"₩{val:,.0f}"
    return f"${val:,.2f}"

def _fmt_mktcap(val, ticker):
    """시가총액 포맷 (KRW=억원, USD=M$)."""
    if _is_kr(ticker):
        return f"{val:,.0f}억원"
    # val은 억원 단위 → M$로 변환 (1억원 ≈ 0.077 M$ 기준, 대략 1300원/$)
    return f"${val/130:,.1f}M"

def _price_unit(ticker):
    return "원" if _is_kr(ticker) else "$"



def _calc_adr(highs, lows, n=20):
    """20일 평균 일일 변동폭 %."""
    if len(highs) < n or len(lows) < n:
        return 0.0
    adrs = [(h - l) / l * 100 for h, l in zip(highs[-n:], lows[-n:]) if l > 0]
    return sum(adrs) / len(adrs) if adrs else 0.0


def _is_kr(ticker):
    return ticker.endswith(".KS") or ticker.endswith(".KQ")


# ── 공통 품질 필터 (모든 전략에서 호출) ──────────────────────────────
def _apply_quality_filters(ind, fi, ticker, strategy_name):
    """
    거래대금·이익의 질·주봉 확인 공통 필터.
    반환: (pass: bool, reason: str)
    pass=False 이면 탈락 사유와 함께 반환.
    """
    p = lambda k: get_param(strategy_name, k)

    # ── 거래정지 필터 (최우선) ────────────────────────────────────────
    try:
        d = fetch_realtime(ticker)
        _halted, _halt_reason = is_trading_halted(d)
        if _halted:
            return False, f"거래정지: {_halt_reason}"
    except Exception:
        pass   # 판별 실패 시 통과

    # ── 거래대금 필터 ─────────────────────────────────────────────
    min_tv = float(p("min_trading_value") or 0)
    if min_tv > 0:
        tv = ind.get("trading_value_20d", 0)
        is_usd = not _is_kr(ticker)
        # KRW: 억원 단위 비교, USD: M$ 단위 비교
        if is_usd:
            # tv는 달러 단위, min_tv는 M$ 단위
            tv_m = tv / 1_000_000
            if tv_m < min_tv:
                return False, f"거래대금 ${tv_m:.1f}M < 최소 ${min_tv:.1f}M"
        else:
            # tv는 원 단위, min_tv는 억원 단위
            tv_eok = tv / 1e8
            if tv_eok < min_tv:
                return False, f"거래대금 {tv_eok:.1f}억 < 최소 {min_tv:.1f}억"

    # ── 이익의 질 필터 ────────────────────────────────────────────
    min_cf = float(p("min_cf_quality") or 0)
    if min_cf > 0 and fi is not None:
        # 영업CF / 순이익 비율 — 100% 이하이면 이익의 질 낮음
        # yfinance: fi에 operating_cf가 없으면 스킵
        op_cf  = fi.get("operating_cf")   # 영업현금흐름 (있으면)
        net_ni = fi.get("earn_growth")     # 순이익 성장률 대체 사용
        # 직접적인 CF 데이터가 없으면 EPS 성장 vs 매출 성장 괴리로 대체
        eps_g = fi.get("eps_growth")
        rev_g = fi.get("rev_growth")
        if eps_g is not None and rev_g is not None and rev_g > 0:
            # EPS 성장이 매출 성장의 min_cf% 이상이어야 함 (이익 레버리지 확인)
            quality_ratio = eps_g / rev_g * 100 if rev_g > 0 else 100
            if quality_ratio < min_cf and eps_g < 0:
                return False, f"이익의 질 낮음 (EPS성장/매출성장={quality_ratio:.0f}%)"

    # ── 멀티 타임프레임 (주봉) 확인 — 전전략 적용 ──────────────────
    weekly_check = get_param(strategy_name, "weekly_filter")
    if weekly_check:
        w_ma10      = ind.get("w_ma10", 0)
        w_ma30      = ind.get("w_ma30", 0)
        w_above_10  = ind.get("w_above_ma10", True)
        w_above_30  = ind.get("w_above_ma30", True)
        w_slope     = ind.get("w_ma10_slope", 0)
        w_rsi       = ind.get("w_rsi", 50)
        cur         = ind.get("cur", 0)
        fails = []
        # 조건1: 현재가 > 주봉 MA10 (≈ 50일선 위)
        if w_ma10 > 0 and not w_above_10:
            fails.append(f"주봉MA10({w_ma10:,.0f})아래")
        # 조건2: 주봉 RSI 30 미만이면 하락 추세
        if w_rsi < 30:
            fails.append(f"주봉RSI({w_rsi:.0f})<30")
        if fails:
            return False, "주봉확인실패: " + " / ".join(fails)

    return True, ""


def _get_rs_with_percentile(ticker, ind):
    """캐시된 RS 백분위 반환. 캐시 없으면 추정 RS 반환."""
    if ticker in _rs_percentile_cache:
        return _rs_percentile_cache[ticker]
    return ind.get("rs", 50)


# ─────────────────────────────────────────────────────────
# VCP 패턴 분석
# ─────────────────────────────────────────────────────────
def _find_local_extrema(highs, lows, window=5):
    peaks, troughs = [], []
    n = len(highs)
    for i in range(window, n - window):
        if all(highs[i] >= highs[j] for j in range(i - window, i + window + 1) if j != i):
            peaks.append((i, highs[i]))
        if all(lows[i] <= lows[j] for j in range(i - window, i + window + 1) if j != i):
            troughs.append((i, lows[i]))
    return peaks, troughs


def _calc_vcp(highs, lows, closes, volumes, min_waves=2, tightness_pct=20.0, vdu_ratio=0.9):
    """
    VCP 분석 — 실용적 완화 버전
    · 수축 파동 min_waves개 이상
    · 수축도 순차 감소 (C1>C2>C3)
    · Higher Lows (저점 상승)
    · Tightness: 마지막 수축도 < tightness_pct%
    · VDU: 최근 5일 거래량 < 20일 평균 × vdu_ratio
    """
    empty = {"vcp_ok": False, "contractions": [], "higher_lows": False,
             "time_contract": False, "tightness_ok": False, "vdu_ok": False,
             "last_contraction": 999.0, "vol_ratio_last": 1.0,
             "pivot_price": closes[-1] if closes else 0, "c_count": 0,
             "summary": "데이터부족"}

    if len(closes) < 40:
        return empty

    look = min(150, len(closes))
    h = highs[-look:]; l = lows[-look:]; c = closes[-look:]
    v = volumes[-look:] if volumes and len(volumes) >= look else []

    peaks, troughs = _find_local_extrema(h, l, window=4)
    if len(peaks) < 2 or len(troughs) < 1:
        empty["summary"] = f"파동부족(고점{len(peaks)},저점{len(troughs)})"
        return empty

    sp = sorted(peaks,   key=lambda x: x[0])
    st = sorted(troughs, key=lambda x: x[0])

    contractions, trough_prices, durations = [], [], []
    for idx, (pi, pp) in enumerate(sp[:-1]):
        npi = sp[idx + 1][0]
        tr_in = [(ti, tp) for ti, tp in st if pi < ti < npi]
        if not tr_in:
            continue
        ti, tp = min(tr_in, key=lambda x: x[1])
        pct = (pp - tp) / pp * 100
        contractions.append(pct)
        trough_prices.append(tp)
        durations.append(max(1, ti - pi))
        if len(contractions) >= 5:
            break

    c_count = len(contractions)
    if c_count < min_waves:
        empty["c_count"] = c_count
        empty["contractions"] = [round(x, 1) for x in contractions]
        empty["summary"] = f"파동{c_count}개(최소{min_waves}개필요)"
        return empty

    dec = all(contractions[i] > contractions[i + 1] for i in range(len(contractions) - 1))
    hl  = all(trough_prices[i] <= trough_prices[i + 1] for i in range(len(trough_prices) - 1))
    tc  = all(durations[i] >= durations[i + 1] for i in range(len(durations) - 1))
    last = contractions[-1]
    tight_ok = last < tightness_pct

    vdu_ok = False
    vol_ratio = 1.0
    if v and len(v) >= 20:
        avg20 = sum(v[-20:]) / 20
        avg5  = sum(v[-5:]) / 5 if len(v) >= 5 else avg20
        if avg20 > 0:
            vol_ratio = avg5 / avg20
            vdu_ok = vol_ratio <= vdu_ratio

    pivot = sp[-1][1]
    core_ok = dec and hl and c_count >= min_waves
    vcp_ok  = core_ok and tight_ok

    tag = "🏆VCP완성" if vcp_ok else ("⚠부분형성" if core_ok else "❌미형성")
    return {
        "vcp_ok": vcp_ok, "contractions": [round(x, 1) for x in contractions],
        "higher_lows": hl, "time_contract": tc,
        "tightness_ok": tight_ok, "vdu_ok": vdu_ok,
        "last_contraction": round(last, 2), "vol_ratio_last": round(vol_ratio, 2),
        "pivot_price": pivot, "c_count": c_count,
        "summary": f"{tag} 수축:{'>'.join(f'{x:.0f}%' for x in contractions)}"
                   f" HL={'✅' if hl else '❌'} TI={'✅' if tight_ok else '❌'}"
                   f"({last:.1f}%) VDU={'✅' if vdu_ok else '❌'}({vol_ratio:.2f}배)"
    }


# ═══════════════════════════════════════════════════════════
# 1. 조엘 그린블라트 — 마법공식 (ROC + EY 합산 등수)
# ═══════════════════════════════════════════════════════════
def _fetch_greenblatt_data(name, ticker):
    """
    ROC(자본수익률) + EY(이익수익률) 수집.
    필터: RSI 하한, 3개월 하한, 시가총액, 금융업 제외 (파라미터)
    데이터 없으면 추정값 사용 → 실데이터 있을 때만 ROC/EY 필터 적용
    """
    p = lambda k: get_param("조엘 그린블라트", k)
    try:
        ind = _get_indicators(ticker)
        fi  = _get_financial_indicators(ticker)
    except Exception:
        return None

    # ── 거래정지 필터 (가장 먼저 적용) ──
    try:
        d_chk = fetch_realtime(ticker)
        _h, _hr = is_trading_halted(d_chk)
        if _h:
            return None   # 거래정지 종목 제외
    except Exception:
        pass

    rsi  = _g(ind, "rsi", 50)
    mom3 = _g(ind, "change_3m", 0)
    cur  = _g(ind, "cur", 1)

    # ── ROC 계산 ──────────────────────────────────────────
    if fi and fi.get("roic") is not None and float(fi["roic"]) > 0:
        ROC = float(fi["roic"]) / 100.0; roc_src = "ROIC"
    elif fi and fi.get("roe") is not None and float(fi["roe"]) > 0:
        ROC = float(fi["roe"]) / 100.0 * 0.85; roc_src = "ROE근사"
    else:
        ROC = max(0.001, 0.08 + (rsi - 50) * 0.002); roc_src = "추정"

    # ── EY 계산 ──────────────────────────────────────────
    ey_raw = fi.get("ey")  if fi else None
    pe_raw = fi.get("pe")  if fi else None
    if ey_raw is not None and float(ey_raw) > 0:
        EY = float(ey_raw) / 100.0; ey_src = "1/PER"
    elif pe_raw is not None and 0 < float(pe_raw) < 500:
        EY = 1.0 / float(pe_raw); ey_src = "PER역수"
    else:
        EY = max(0.001, 0.06 + mom3 * 0.001); ey_src = "추정"

    # ── 금융·유틸리티 제외 ─────────────────────────────
    if p("exclude_finance"):
        fin_kw = ["은행","보험","증권","카드","저축","캐피탈","손해","생명","전력","가스","한전","지주"]
        if any(kw in name for kw in fin_kw):
            return None

    # ── 시가총액 필터 (억원 단위) ──────────────────────
    mktcap = fi.get("mktcap") if fi else None
    min_mc = float(p("min_mktcap_billion") or 0)
    if mktcap and min_mc > 0 and mktcap < min_mc:
        return None

    # ── 선제 필터 (RSI·3개월수익률) ────────────────────
    min_rsi  = float(p("min_rsi")       or 20)
    min_mom3 = float(p("min_mom3m_pct") or -50)
    if rsi > 0 and rsi < min_rsi:
        return None
    if mom3 < min_mom3:
        return None

    # ── ROC/EY 필터 — 실데이터일 때만 적용 ────────────
    min_roc = float(p("min_roc_pct") or 0) / 100.0
    min_ey  = float(p("min_ey_pct")  or 0) / 100.0
    if roc_src != "추정" and min_roc > 0 and ROC < min_roc:
        return None
    if ey_src  != "추정" and min_ey  > 0 and EY  < min_ey:
        return None

    return {"name": name, "ticker": ticker,
            "ROC": ROC, "EY": EY,
            "roc_src": roc_src, "ey_src": ey_src,
            "realtime": ind.get("realtime", False),
            "rsi": rsi, "mom3": mom3, "cur": cur}


def find_greenblatt_ranked(tickers, top_n=30, progress_cb=None,
                           workers=PARALLEL_WORKERS, use_cache=False,
                           stop_event=None):
    """ROC·EY 상대 등수 합산 → 낮을수록 우수."""
    p = lambda k: get_param("조엘 그린블라트", k)
    if not use_cache:
        _indicator_cache.clear()

    total = len(tickers); raw = []; done = [0]; lock = threading.Lock()

    def _one(args):
        if stop_event and stop_event.is_set(): return None
        try: return _fetch_greenblatt_data(*args)
        except: return None

    def _cb(res):
        with lock:
            if res: raw.append(res)
            done[0] += 1
            if progress_cb and (done[0] % 10 == 0 or done[0] == total):
                progress_cb(done[0], total)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_one, t): t for t in tickers}
        for fut in as_completed(futs):
            if stop_event and stop_event.is_set():
                [f.cancel() for f in futs]; break
            try: _cb(fut.result())
            except:
                with lock: done[0] += 1

    if not raw: return []
    N = len(raw)

    # ROC 등수 (높을수록 1위)
    for rank, item in enumerate(sorted(raw, key=lambda x: x["ROC"], reverse=True), 1):
        item["roc_rank"] = rank
    # EY 등수 (높을수록 1위)
    for rank, item in enumerate(sorted(raw, key=lambda x: x["EY"],  reverse=True), 1):
        item["ey_rank"] = rank

    # 가중치 비율로 정렬, 표시는 단순 평균 등수
    w_roc = float(p("weight_roc") or 50)
    w_ey  = float(p("weight_ey")  or 50)
    w_tot = max(w_roc + w_ey, 1)
    for item in raw:
        item["sort_score"] = (item["roc_rank"] * w_roc + item["ey_rank"] * w_ey) / w_tot

    raw.sort(key=lambda x: x["sort_score"])
    results = []
    for item in raw[:top_n]:
        avg = (item["roc_rank"] + item["ey_rank"]) / 2.0
        detail = {
            "━━ 마법공식 선제필터 통과": f"RSI>{p('min_rsi')}  3개월>{p('min_mom3m_pct')}%",
            "RSI(14)":          f"{item['rsi']:.1f}",
            "3개월 수익률":     f"{item['mom3']:+.1f}%",
            f"ROC ({item['roc_src']})":  f"{item['ROC']*100:.1f}%  →  {N:,}종목 중 {item['roc_rank']}위",
            f"EY  ({item['ey_src']})":   f"{item['EY']*100:.1f}%  →  {N:,}종목 중 {item['ey_rank']}위",
            "합산 평균 등수":   f"▶ {avg:.1f}위  (가중치 ROC×{w_roc:.0f} EY×{w_ey:.0f}  /  필터통과 {N:,}종목)",
            "데이터": "🟢 실시간" if item["realtime"] else "🟡 샘플",
        }
        results.append((item["name"], item["ticker"], avg, detail))
    return results


def score_greenblatt(name, ticker):
    """단일 종목 팝업용."""
    data = _fetch_greenblatt_data(name, ticker)
    if not data:
        return 0.0, {"결과": "❌ 필터 탈락"}
    score = round(min(1, data["ROC"]/0.30)*50 + min(1, data["EY"]/0.15)*50, 2)
    return score, {f"ROC({data['roc_src']})": f"{data['ROC']*100:.1f}%",
                   f"EY({data['ey_src']})":   f"{data['EY']*100:.1f}%",
                   "데이터": "🟢 실시간" if data["realtime"] else "🟡 샘플"}


# ═══════════════════════════════════════════════════════════
# 2. 미너비니 SEPA — 트렌드 템플릿 + VCP
# ═══════════════════════════════════════════════════════════
def score_minervini(name, ticker):
    """
    미너비니 SEPA — 한국 시장 강화판
    구조: 잡주제거 → 추세템플릿 → RS점수 → VCP패턴 → 돌파직전 탐지
    """
    p = lambda k: get_param("미너비니", k)
    try:
        ind = _get_indicators(ticker)
        d   = fetch_realtime(ticker)
        fi  = _get_financial_indicators(ticker)
    except Exception:
        return 0.0, {"_filtered": True, "탈락": "데이터 오류"}

    # 거래정지 즉시 제외
    try:
        from data_fetcher import is_trading_halted
        _h, _hr = is_trading_halted(d)
        if _h:
            return 0.0, {"_filtered": True, "탈락": f"거래정지: {_hr}"}
    except Exception:
        pass

    closes  = d.get("closes",  [])
    highs   = d.get("highs",   [])
    lows    = d.get("lows",    [])
    volumes = d.get("volumes", [])
    opens   = d.get("opens",   [])
    n = len(closes)

    if n < 120:
        return 0.0, {"_filtered": True, "탈락": f"데이터 부족 ({n}봉)"}

    cur   = _g(ind, "cur", closes[-1] if closes else 0)
    is_kr = _is_kr(ticker)
    pf    = (lambda v: f"₩{v:,.0f}") if is_kr else (lambda v: f"${v:.2f}")

    # ── 공통 품질 필터 (거래대금) ──
    _qok, _qreason = _apply_quality_filters(ind, fi, ticker, "미너비니")
    if not _qok:
        return 0.0, {"_filtered": True, "탈락": _qreason,
                     "데이터": "🟢 실시간" if ind.get("realtime") else "🟡 샘플"}

    # 스팩/우선주 제외
    if any(kw in name for kw in ["스팩","SPAC"]):
        return 0.0, {"_filtered": True, "탈락": f"스팩 제외"}

    # ── 이평선 계산 ──────────────────────────────────────────────────
    ma50_v  = sma(closes, 50)
    ma150_v = sma(closes, 150)
    ma200_v = sma(closes, 200)

    def _last(v, default=cur):
        return next((x for x in reversed(v) if x is not None), default)
    def _nth(v, n_back, default=None):
        cnt = 0
        for x in reversed(v):
            if x is not None:
                cnt += 1
                if cnt == n_back: return x
        return default

    ma50  = _last(ma50_v)
    ma150 = _last(ma150_v)
    ma200 = _last(ma200_v)
    ma200_20ago = _nth(ma200_v, 20, ma200)
    ma200_rising = (ma200 > ma200_20ago * 0.998) if ma200_20ago else True

    hi52  = max(closes[-min(n,252):])
    lo52  = min(closes[-min(n,252):])
    from_hi = (cur - hi52) / max(hi52, 1) * 100
    from_lo = (cur - lo52) / max(lo52, 1) * 100

    has_ma = (ma50 > 0 and ma150 > 0 and ma200 > 0)

    # ── 트렌드 템플릿 8조건 (모두 필수) ─────────────────────────────
    min_rs_v  = float(p("min_rs")   or 50)
    min_52l   = float(p("min_from_52l_pct") or 30)   # 저점 +30% 이상 (문서 기준)
    max_52h   = float(p("max_from_52h_pct") or 25)   # 고점 -25% 이내 (문서 기준)
    rs        = _get_rs_with_percentile(ticker, ind)

    filters = {
        "F1 현재가>50일선":      (cur > ma50)             if ma50 >  0 else True,
        "F2 현재가>150일선":     (cur > ma150)            if ma150 > 0 else True,
        "F3 현재가>200일선":     (cur > ma200)            if ma200 > 0 else True,
        "F4 50>150>200 정배열":  (ma50>ma150>ma200)       if has_ma    else True,
        "F5 200일선 20일 상승":  ma200_rising,
        f"F6 52주저점+{min_52l:.0f}%이상": from_lo >= min_52l,
        f"F7 52주고점-{max_52h:.0f}%이내": from_hi >= -max_52h,
        f"F8 RS≥{min_rs_v:.0f}": (rs >= min_rs_v)        if rs > 0    else True,
    }
    has_eps = fi is not None and fi.get("eps_growth") is not None
    has_rev = fi is not None and fi.get("rev_growth") is not None
    eps_g   = fi.get("eps_growth") if has_eps else None
    rev_g   = fi.get("rev_growth") if has_rev else None
    min_eps = float(p("min_eps_growth") or 0)
    min_rev = float(p("min_rev_growth") or 0)
    if min_eps > 0 and has_eps:
        filters[f"F9 EPS≥{min_eps:.0f}%"] = (eps_g >= min_eps)
    if min_rev > 0 and has_rev:
        filters[f"F10 매출≥{min_rev:.0f}%"] = (rev_g >= min_rev)

    failed = [k for k, v in filters.items() if not v]
    if failed:
        return 0.0, {
            "_filtered": True,
            "탈락": " / ".join(f[:25] for f in failed),
            "MA50": pf(ma50), "MA150": pf(ma150), "MA200": pf(ma200),
            "52주고": f"{from_hi:.1f}%", "52주저": f"+{from_lo:.1f}%",
            "RS": f"{rs:.0f}%",
            "데이터": "🟢 실시간" if ind.get("realtime") else "🟡 샘플",
        }

    # ── VCP 패턴 분석 ────────────────────────────────────────────────
    min_w   = int(p("vcp_min_waves")      or 2)
    tight   = float(p("vcp_tightness_pct") or 20.0)
    vdu_r   = float(p("vdu_ratio")         or 0.85)
    vcp_req = p("vcp_required")

    vcp = _calc_vcp(highs, lows, closes, volumes,
                    min_waves=min_w, tightness_pct=tight, vdu_ratio=vdu_r)

    if vcp_req:
        vcp_fails = []
        if vcp["c_count"] < min_w:
            vcp_fails.append(f"수축파동 {vcp['c_count']}/{min_w}개")
        if not vcp["tightness_ok"]:
            vcp_fails.append(f"Tightness {vcp['last_contraction']:.1f}%>{tight:.0f}%")
        if vcp_fails:
            return 0.0, {"_filtered": True,
                         "탈락(VCP)": " / ".join(vcp_fails),
                         "VCP": vcp["summary"],
                         "데이터": "🟢 실시간" if ind.get("realtime") else "🟡 샘플"}

    # ── RS 복합 점수 (문서 권장: 20·60·120·250일 가중) ───────────────
    # RS 백분위 기본값 + 기간별 가중 모멘텀
    rs_base = rs  # 기존 RS 백분위
    mom_20  = (closes[-1]-closes[-min(21,n-1)]) /max(closes[-min(21,n-1)],1)*100 if n>=5 else 0
    mom_60  = (closes[-1]-closes[-min(63,n-1)]) /max(closes[-min(63,n-1)],1)*100 if n>=5 else 0
    mom_120 = (closes[-1]-closes[-min(121,n-1)])/max(closes[-min(121,n-1)],1)*100 if n>=5 else 0
    mom_250 = (closes[-1]-closes[-min(251,n-1)])/max(closes[-min(251,n-1)],1)*100 if n>=5 else 0
    # 가중합 (문서: 60일×40 + 120일×30 + 20일×20 + 250일×10)
    mom_composite = (mom_60*0.4 + mom_120*0.3 + mom_20*0.2 + mom_250*0.1)

    # ── 점수 계산 ────────────────────────────────────────────────────
    w_rs  = float(p("weight_rs")  or 45)
    w_eps = float(p("weight_eps") or 30)
    w_rev = float(p("weight_rev") or 15)
    w_vol = float(p("weight_vol") or 10)

    # RS 점수: 기본 RS + 복합 모멘텀 보너스
    rs_s = (rs_base - min_rs_v) / max(99 - min_rs_v, 1) * w_rs
    mom_bonus = min(10, max(0, mom_composite / 5))  # 모멘텀 최대 10점 보너스
    rs_s = min(w_rs + 10, rs_s + mom_bonus)

    eps_s = min(w_eps, max(0, (eps_g - min_eps) / 2)) if (has_eps and eps_g is not None) else w_eps * 0.5
    rev_s = min(w_rev, max(0, (rev_g or 0) / 3)) if rev_g is not None else w_rev * 0.4

    # VCP 품질 보너스
    vcp_bonus = 0
    if vcp["tightness_ok"] and vcp["higher_lows"] and vcp["vdu_ok"]:
        vcp_bonus = w_vol
    elif vcp["tightness_ok"] and vcp["higher_lows"]:
        vcp_bonus = w_vol * 0.7
    elif vcp["higher_lows"]:
        vcp_bonus = w_vol * 0.4

    # 돌파 직전 보너스 (+5점): 박스권 -3%~+1%
    vol20_avg = sum(volumes[-20:])/20 if len(volumes)>=20 else 0
    box_hi    = max(closes[-min(30,n):])
    at_pivot  = (box_hi - cur) / max(box_hi, 1) * 100
    vol_ratio = volumes[-1] / max(vol20_avg, 1) if volumes and vol20_avg > 0 else 1.0
    breakout_bonus = 0
    if 0 <= at_pivot <= 3:      # 피벗 직전 (-3%~0%)
        breakout_bonus = 5
    if vol_ratio >= 1.5 and closes[-1] > box_hi:  # 돌파 + 거래량 급증
        breakout_bonus = 8

    rank_val = rs_s + eps_s + rev_s + vcp_bonus + breakout_bonus

    # ── 결과 상세 ─────────────────────────────────────────────────────
    c_str = "→".join(f"{c:.0f}%" for c in vcp["contractions"]) or "파동미형성"
    detail = {
        "━━ 트렌드 템플릿 (7조건)": "✅ 통과",
        "이평선 배열":      f"✅ {pf(cur)}>{pf(ma50)}>{pf(ma150)}>{pf(ma200)}" if has_ma else "✅(데이터부족)",
        "200일선 기울기":   f"{'✅ 상승' if ma200_rising else '❌ 평탄·하락'} ({(ma200-ma200_20ago)/max(ma200_20ago,1)*100:+.2f}%)",
        "52주 포지션":      f"고점{from_hi:.1f}%  저점+{from_lo:.1f}%",
        "RS 백분위":        f"✅ {rs:.0f}점",
        "━━ RS 복합 모멘텀": f"20일:{mom_20:+.1f}%  60일:{mom_60:+.1f}%  120일:{mom_120:+.1f}%",
        "모멘텀 복합":      f"{mom_composite:+.1f}% (가중평균)  보너스:+{mom_bonus:.0f}점",
        "EPS 성장":         f"{'✅' if eps_g and eps_g>0 else '—'} {f'{eps_g:+.0f}%' if eps_g is not None else '데이터없음'}",
        "매출 성장":        f"{'✅' if rev_g and rev_g>0 else '—'} {f'{rev_g:+.0f}%' if rev_g is not None else '데이터없음'}",
        "━━ VCP 패턴":      vcp["summary"],
        "수축 파동":        c_str,
        "Higher Lows":      f"{'✅' if vcp['higher_lows'] else '❌'}",
        f"Tightness":        f"{'✅' if vcp['tightness_ok'] else '❌'} {vcp['last_contraction']:.1f}%",
        "VDU (거래량감소)": f"{'✅' if vcp['vdu_ok'] else '❌'} {vcp['vol_ratio_last']:.2f}배",
        "피벗 가격":        pf(vcp["pivot_price"]),
        "돌파 여부":        f"{'🚀 돌파!' if breakout_bonus>=8 else '⏳ 직전' if breakout_bonus==5 else '대기'} (고점-{at_pivot:.1f}%  거래량{vol_ratio:.1f}배)",
        "━━ 점수":          f"RS:{rs_s:.0f} + EPS:{eps_s:.0f} + 매출:{rev_s:.0f} + VCP:{vcp_bonus:.0f} + 돌파:{breakout_bonus:.0f} = {rank_val:.1f}",
        "데이터":           "🟢 실시간" if ind.get("realtime") else "🟡 샘플",
    }
    return rank_val, detail


# ═══════════════════════════════════════════════════════════
# 3. 윌리엄 오닐 — CAN SLIM 한국형 완성판
# ═══════════════════════════════════════════════════════════
def score_oneil(name, ticker):
    """
    윌리엄 오닐 CAN SLIM — 한국시장 실전 완성판 (문서 기반)

    ══ 구조 ════════════════════════════════════════════════════════════
    1단계: 잡주 제거 (스팩·ETF·저유동성·거래정지·적자지속)
    2단계: 이평선 정배열 하드 필터 (cur>MA50>MA150>MA200 + 기울기 + 52주)
    3단계: CAN SLIM 7요소 100점 점수화
    4단계: 베이스 패턴 탐지 + 오닐 매수 타이밍 판별

    ══ 이평선 정배열 하드 필터 (오닐 트렌드 템플릿) ══════════════════
      cur > MA50 > MA150 > MA200
      MA200 기울기 ≥ 0 (장기 추세 상승)
      52주 저점 대비 +30% 이상
      52주 고점 대비 -25% 이내

    ══ CAN SLIM 점수 (100점 만점) ══════════════════════════════════════
    C(20) + A(15) + N(15) + S(15) + L(20) + I(5) + M(10)
    80↑ 최우선 / 70~79 관찰 / 69↓ 제외
    """
    p = lambda k: get_param("윌리엄오닐", k)
    try:
        ind     = _get_indicators(ticker)
        fi      = _get_financial_indicators(ticker)
        d       = fetch_realtime(ticker)
        closes  = d.get("closes",  [])
        highs   = d.get("highs",   [])
        lows    = d.get("lows",    [])
        volumes = d.get("volumes", [])
        opens   = d.get("opens",   [])
    except Exception:
        return 0.0, {"_filtered": True, "탈락": "데이터 오류"}

    if len(closes) < 60:
        return 0.0, {"_filtered": True, "탈락": f"데이터 부족 ({len(closes)}봉)"}

    # 거래정지 즉시 제외
    try:
        from data_fetcher import is_trading_halted
        _h, _hr = is_trading_halted(d)
        if _h:
            return 0.0, {"_filtered": True, "탈락": f"거래정지: {_hr}"}
    except Exception:
        pass

    # 공통 품질 필터
    _qok, _qreason = _apply_quality_filters(ind, fi, ticker, "윌리엄오닐")
    if not _qok:
        return 0.0, {"_filtered": True, "탈락": _qreason,
                     "데이터": "🟢 실시간" if ind.get("realtime") else "🟡 샘플"}

    # 스팩·ETF 제외
    if any(kw in name for kw in ["스팩","SPAC","ETF","ETN"]):
        return 0.0, {"_filtered": True, "탈락": f"제외 대상: {name}"}

    cur   = _g(ind, "cur", closes[-1])
    is_kr = _is_kr(ticker)
    pf    = (lambda v: f"₩{v:,.0f}") if is_kr else (lambda v: f"${v:.2f}")
    n     = len(closes)

    # ── 이평선 계산 ──────────────────────────────────────────────────
    ma20_v  = sma(closes, 20)
    ma50_v  = sma(closes, 50)
    ma150_v = sma(closes, 150)
    ma200_v = sma(closes, 200)

    def _last(v, default=cur):
        return next((x for x in reversed(v) if x is not None), default)

    ma20  = _last(ma20_v)
    ma50  = _last(ma50_v)
    ma150 = _last(ma150_v)
    ma200 = _last(ma200_v)
    ma200_20ago = next((x for i,x in enumerate(reversed(ma200_v))
                        if x is not None and i >= 20), ma200)
    ma200_rising = (ma200 >= ma200_20ago * 0.998)
    ma200_slope  = (ma200 - ma200_20ago) / max(ma200_20ago, 1) * 100
    has_ma = (ma50 > 0 and ma150 > 0 and ma200 > 0)

    # 52주 고저
    hi52 = max(closes[-min(n,252):])
    lo52 = min(closes[-min(n,252):])
    from_hi52 = (cur - hi52) / max(hi52, 1) * 100
    from_lo52 = (cur - lo52) / max(lo52, 1) * 100

    # ══════════════════════════════════════════════════════════════════
    # 2단계: 이평선 정배열 하드 필터 (7조건 — 모두 통과해야 함)
    # ══════════════════════════════════════════════════════════════════
    min_52l = float(p("min_from_52l_pct") or 30)
    max_52h = float(p("max_from_52h_pct") or 25)

    trend_checks = {
        "T1 현재가>MA50":       (cur > ma50)           if ma50  > 0 else True,
        "T2 현재가>MA150":      (cur > ma150)          if ma150 > 0 else True,
        "T3 현재가>MA200":      (cur > ma200)          if ma200 > 0 else True,
        "T4 MA50>MA150>MA200":  (ma50>ma150>ma200)     if has_ma    else True,
        "T5 MA200 기울기 상승":  ma200_rising,
        f"T6 52주저점+{min_52l:.0f}%이상": from_lo52 >= min_52l,
        f"T7 52주고점-{max_52h:.0f}%이내": from_hi52 >= -max_52h,
    }
    failed_trend = [k for k, v in trend_checks.items() if not v]
    if failed_trend:
        return 0.0, {
            "_filtered": True,
            "탈락(이평선 필터)": " / ".join(f[:28] for f in failed_trend),
            "MA50":  pf(ma50), "MA150": pf(ma150), "MA200": pf(ma200),
            "MA200 기울기": f"{ma200_slope:+.2f}%",
            "52주고": f"{from_hi52:.1f}%",  "52주저": f"+{from_lo52:.1f}%",
            "데이터": "🟢 실시간" if ind.get("realtime") else "🟡 샘플",
        }

    # 재무 데이터 수집
    eps_g  = fi.get("earn_growth") if fi else None   # 분기 EPS/영업이익 성장
    ann_g  = fi.get("eps_growth")  if fi else None   # 연간 EPS 성장
    roe    = fi.get("roe")         if fi else None
    rev_g  = fi.get("rev_growth")  if fi else None
    op_g   = fi.get("op_growth")   if fi else None   # 영업이익 성장
    has_fi = (fi is not None and (eps_g is not None or ann_g is not None))

    # 거래량 계산
    vol20_avg = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 0
    vol5_avg  = sum(volumes[-5:])  / 5  if len(volumes) >= 5  else vol20_avg
    vr5       = vol5_avg / max(vol20_avg, 1)
    vol_today = volumes[-1] if volumes else 0
    vol_ratio = vol_today / max(vol20_avg, 1)
    tv_b      = cur * vol_today / 1e8 if is_kr else cur * vol_today / 1e6
    tv_str    = f"₩{tv_b:.0f}억" if is_kr else f"${tv_b:.1f}M"

    # 모멘텀 (RS 대체 보조용)
    mom3m  = (closes[-1]-closes[-min(63,n-1)]) /max(closes[-min(63,n-1)],1)*100 if n>=5 else 0
    mom6m  = (closes[-1]-closes[-min(126,n-1)])/max(closes[-min(126,n-1)],1)*100 if n>=5 else 0
    mom12m = (closes[-1]-closes[-min(252,n-1)])/max(closes[-min(252,n-1)],1)*100 if n>=5 else 0

    # ══════════════════════════════════════════════════════════════════
    # L: 상대강도 — 최소 RS 필터 (오닐: 상위 20% = RS ≥ 80)
    # ══════════════════════════════════════════════════════════════════
    rs = _get_rs_with_percentile(ticker, ind)
    min_rs_v = float(p("min_rs") or 70)
    if rs > 0 and rs < min_rs_v:
        return 0.0, {
            "_filtered": True,
            "탈락(L 상대강도)": f"RS {rs:.0f}% < {min_rs_v:.0f}% — 오닐: 싼 종목이 아닌 강한 종목",
            "RS": f"{rs:.0f}%",
            "3개월 수익률": f"{mom3m:+.0f}%",
            "데이터": "🟢 실시간" if ind.get("realtime") else "🟡 샘플",
        }

    # ══════════════════════════════════════════════════════════════════
    # C: 최근 분기 실적 강한가? (20점)
    # 핵심: 영업이익 YoY +25%↑, 매출 YoY +20%↑, EPS 흑자전환
    # ══════════════════════════════════════════════════════════════════
    c_score = 0; c_labels = []

    # 영업이익 YoY (우선순위 1 — 한국 시장에서 가장 안정적)
    op_src = op_g if op_g is not None else eps_g  # 영업이익 없으면 EPS로 대체
    if op_src is not None:
        if op_src >= 50:
            c_score += 10; c_labels.append(f"영업이익/EPS+{op_src:.0f}%★")
        elif op_src >= 25:
            c_score += 8;  c_labels.append(f"영업이익+{op_src:.0f}%")
        elif op_src >= 10:
            c_score += 5;  c_labels.append(f"영업이익+{op_src:.0f}%")
        elif op_src > 0:
            c_score += 2;  c_labels.append(f"영업이익+{op_src:.0f}%")
        # 흑자 전환 특별 가점
        if fi and fi.get("profit_turnover"):
            c_score += 4;  c_labels.append("흑자전환")
    else:
        # 실적 데이터 없음 → 3개월 가격 모멘텀으로 대체 (최대 5점)
        if mom3m >= 30:   c_score += 5; c_labels.append(f"가격모멘텀+{mom3m:.0f}%(실적대체)")
        elif mom3m >= 15: c_score += 3; c_labels.append(f"가격모멘텀+{mom3m:.0f}%")
        else:             c_score += 1

    # 매출 YoY +20% 이상 (문서 기준)
    if rev_g is not None:
        if rev_g >= 20:
            c_score += 10; c_labels.append(f"매출+{rev_g:.0f}%")
        elif rev_g >= 10:
            c_score += 6;  c_labels.append(f"매출+{rev_g:.0f}%")
        elif rev_g > 0:
            c_score += 3;  c_labels.append(f"매출+{rev_g:.0f}%")
    else:
        # 6개월 모멘텀 대체 (최대 4점)
        if mom6m >= 20:   c_score += 4
        elif mom6m >= 10: c_score += 2
        else:             c_score += 1

    c_score = min(20, c_score)

    # ══════════════════════════════════════════════════════════════════
    # A: 연간 실적 꾸준히 성장? (15점)
    # 핵심: 3년 EPS CAGR>15%, 영업이익 CAGR>15%, ROE>12~17%
    # ══════════════════════════════════════════════════════════════════
    a_score = 0; a_labels = []

    if ann_g is not None:
        if ann_g >= 20:
            a_score += 8;  a_labels.append(f"연간EPS+{ann_g:.0f}%")
        elif ann_g >= 15:
            a_score += 6;  a_labels.append(f"연간EPS+{ann_g:.0f}%")
        elif ann_g > 0:
            a_score += 3;  a_labels.append(f"연간EPS+{ann_g:.0f}%")
    else:
        # 12개월 모멘텀 대체
        if mom12m >= 20:   a_score += 5; a_labels.append(f"12개월+{mom12m:.0f}%(대체)")
        elif mom12m >= 10: a_score += 3
        else:              a_score += 1

    # ROE > 12~17% (문서 기준)
    if roe is not None:
        if roe >= 17:    a_score += 7; a_labels.append(f"ROE{roe:.0f}%★")
        elif roe >= 12:  a_score += 5; a_labels.append(f"ROE{roe:.0f}%")
        elif roe >= 8:   a_score += 3; a_labels.append(f"ROE{roe:.0f}%")
    else:
        a_score += 2  # 데이터 없음 중립

    a_score = min(15, a_score)

    # ══════════════════════════════════════════════════════════════════
    # N: 새로운 모멘텀 (15점)
    # 핵심: 52주 신고가 근처 + 베이스 돌파 + 거래량 동반
    # 패턴: Flat Base / Tight Consolidation / Cup base 탐지
    # ══════════════════════════════════════════════════════════════════
    n_score = 0; n_labels = []

    # 52주 신고가 근처 (핵심 N 조건)
    if from_hi52 >= -3:
        n_score += 8; n_labels.append(f"52주신고가({from_hi52:.0f}%)")
    elif from_hi52 >= -8:
        n_score += 6; n_labels.append(f"신고가근접({from_hi52:.0f}%)")
    elif from_hi52 >= -15:
        n_score += 3; n_labels.append(f"고점권내({from_hi52:.0f}%)")

    # 베이스 패턴 탐지
    # ① Tight Consolidation (좁은 횡보 — 5~8주)
    box35 = min(35, n-1)
    box_hi35 = max((highs or closes)[-box35:])
    box_lo35 = min((lows  or closes)[-box35:])
    tight_pct = (box_hi35 - box_lo35) / max(box_lo35, 1) * 100
    tight_base = tight_pct < float(p("flat_base_pct") or 15)  # 15% 이내 횡보

    # ② Flat Base (고점 대비 조정폭 작음)
    flat_base = tight_base and from_hi52 >= -15

    # ③ 박스 상단 돌파 (피벗 돌파)
    box_hi20 = max((highs or closes)[-min(21,n):-(1)]) if n > 1 else cur
    pivot_break   = (cur > box_hi20 * 0.998 and vol_ratio >= 1.5)
    pivot_nearby  = (cur >= box_hi20 * 0.95)

    if pivot_break:
        n_score += 7; n_labels.append(f"피벗돌파★(거래량{vol_ratio:.1f}배)")
    elif flat_base and pivot_nearby:
        n_score += 5; n_labels.append(f"FlatBase돌파직전({tight_pct:.0f}%)")
    elif tight_base:
        n_score += 3; n_labels.append(f"수렴횡보({tight_pct:.0f}%)")
    elif pivot_nearby:
        n_score += 2; n_labels.append("고점근접")

    n_score = min(15, n_score)

    # ══════════════════════════════════════════════════════════════════
    # S: 수급 / 거래량 (15점)
    # 핵심: 거래량 1.5~2배↑, 기관·외국인 순매수(대체: 5일>20일 거래량)
    # ══════════════════════════════════════════════════════════════════
    s_score = 0; s_labels = []
    min_vr = float(p("min_vol_ratio") or 1.5)

    # 당일 거래량 배율
    if vol_ratio >= min_vr * 1.3:
        s_score += 8; s_labels.append(f"거래량폭증{vol_ratio:.1f}배")
    elif vol_ratio >= min_vr:
        s_score += 6; s_labels.append(f"거래량급증{vol_ratio:.1f}배")
    elif vr5 >= 1.3:
        s_score += 4; s_labels.append(f"5일평균증가{vr5:.1f}배")
    elif vol_ratio >= 1.0:
        s_score += 2; s_labels.append(f"거래량보통{vol_ratio:.1f}배")

    # 거래대금 (유동성·기관 관심 대리)
    min_tv = float(p("min_trading_value") or 20)
    if tv_b >= min_tv * 5:
        s_score += 7; s_labels.append(f"거래대금{tv_b:.0f}억(풍부)")
    elif tv_b >= min_tv * 2:
        s_score += 5; s_labels.append(f"거래대금{tv_b:.0f}억")
    elif tv_b >= min_tv:
        s_score += 3

    s_score = min(15, s_score)

    # ══════════════════════════════════════════════════════════════════
    # L: 상대강도 — 주도주인가? (20점)
    # 오닐: RS 상위 80~90%, 업종 내 1~2위, 12개월 초과수익
    # ══════════════════════════════════════════════════════════════════
    l_score = 0
    if rs > 0:
        if rs >= 90:
            l_score = 20
        elif rs >= 80:
            l_score = 16
        elif rs >= min_rs_v:
            l_score = max(6, int((rs - min_rs_v) / max(90 - min_rs_v, 1) * 16))
    else:
        # RS 데이터 없을 때: 복합 모멘텀 점수로 대체
        mom_composite = mom3m * 0.4 + mom6m * 0.35 + mom12m * 0.25
        if mom_composite >= 20:   l_score = 14
        elif mom_composite >= 10: l_score = 10
        elif mom_composite >= 5:  l_score = 7
        else:                     l_score = 4
    l_score = min(20, l_score)

    # ══════════════════════════════════════════════════════════════════
    # I: 기관·외국인 수급 (5점)
    # 한국: 완벽한 기관 데이터 없음 → 거래량 추이 + 모멘텀으로 대체
    # ══════════════════════════════════════════════════════════════════
    i_score = 2  # 기본 2점
    # 최근 5일 거래량이 20일 평균보다 꾸준히 높으면 기관 관심 시그널
    if vr5 >= 1.5 and vol_ratio >= 1.2:
        i_score = 5   # 거래량 지속 증가 = 기관 매집 가능성
    elif vr5 >= 1.2:
        i_score = 4
    elif vol_ratio >= 1.5:
        i_score = 4

    # ══════════════════════════════════════════════════════════════════
    # M: 시장 방향 (10점)
    # 핵심: KOSPI/KOSDAQ 50일선·200일선 위 → 매수 허용
    # ══════════════════════════════════════════════════════════════════
    m_score = 5  # 기본: 데이터 없을 때 중립
    m_label = "시장데이터미확인(중립)"
    try:
        # 대형주 복수종목의 이평선 위치로 시장 방향 추정
        # KOSPI 주요 구성 종목 이평선 위 비율
        from indicators import sma as _sma
        _mkt_tickers = ["005930.KS","000660.KS","005490.KS","005380.KS","051910.KS"]
        above50 = 0; total_mkt = 0
        for _t in _mkt_tickers:
            try:
                from data_fetcher import fetch_realtime as _fr
                _d = _fr(_t)
                _cl = _d.get("closes",[])
                if len(_cl) >= 50:
                    _ma50 = next((v for v in reversed(_sma(_cl,50)) if v), 0)
                    if _cl[-1] > _ma50: above50 += 1
                    total_mkt += 1
            except:
                pass
        if total_mkt > 0:
            ratio = above50 / total_mkt
            if ratio >= 0.6:   m_score = 10; m_label = f"상승장({above50}/{total_mkt}종목 MA50↑)"
            elif ratio >= 0.4: m_score = 5;  m_label = f"혼조장({above50}/{total_mkt}종목 MA50↑)"
            else:              m_score = 0;  m_label = f"하락장({above50}/{total_mkt}종목 MA50↑)"
    except Exception:
        try:
            from charts import calc_market_regime
            mkt_lbl, _, _ = calc_market_regime("KOSPI")
            if "상승" in mkt_lbl:   m_score = 10; m_label = mkt_lbl
            elif "횡보" in mkt_lbl: m_score = 5;  m_label = mkt_lbl
            elif "하락" in mkt_lbl: m_score = 0;  m_label = mkt_lbl
        except:
            pass

    # ══════════════════════════════════════════════════════════════════
    # 최종 점수 합산
    # ══════════════════════════════════════════════════════════════════
    total = c_score + a_score + n_score + s_score + l_score + i_score + m_score

    # 등급
    if total >= 80:   grade = "🏆 A급 최우선"
    elif total >= 70: grade = "⭐ B급 관찰"
    elif total >= 60: grade = "🔍 C급"
    else:             grade = "📌 D급"

    # 4단계: 오닐 매수 타이밍 탐지
    pivot_price = box_hi20
    buy_zone_hi = pivot_price * 1.05   # 피벗 +5% 이내 = 이상적 매수 구간
    today_strong = (opens and closes[-1] >= highs[-1]*0.97 if highs and opens else False)

    if cur > box_hi20 * 0.998 and vol_ratio >= 1.5 and today_strong:
        buy_timing = "🚀 베이스 돌파 — 즉시 매수 후보"
    elif cur >= box_hi20 * 0.97 and cur <= buy_zone_hi and tight_base:
        buy_timing = "⏳ 피벗 근접 + 베이스 형성 — 진입 대기"
    elif flat_base:
        buy_timing = "🔍 Flat Base 형성 — 모니터링"
    elif pivot_nearby:
        buy_timing = "🔍 고점 근처 — 돌파 확인 후 진입"
    else:
        buy_timing = "대기 — 베이스 형성 기다릴 것"

    # 손절 기준 (-7~-8%)
    stop_price = cur * (1 - float(p("stop_loss_pct") or 8) / 100)

    detail = {
        "━━ CAN SLIM 종합":          f"{grade}  ({total}/100점)",
        "매수 타이밍":                buy_timing,
        "━━ 이평선 정배열 (하드필터 통과)": "",
        "MA 배열":        (f"✅ {pf(cur)} > {pf(ma50)} > {pf(ma150)} > {pf(ma200)}"
                           if has_ma else "✅(이평 데이터부족)"),
        "MA200 기울기":   f"✅ {ma200_slope:+.2f}% (20일전 대비)",
        "52주 포지션":    f"고점{from_hi52:.1f}%  저점+{from_lo52:.1f}%",
        "━━ CAN SLIM 세부 점수":      "",
        f"C 분기실적 ({c_score:>2}/20)": (
            " / ".join(c_labels) if c_labels else
            "실적데이터부족 — 가격모멘텀 대체"),
        f"A 연간실적 ({a_score:>2}/15)": (
            " / ".join(a_labels) if a_labels else
            "연간데이터부족 — 모멘텀 대체"),
        f"N 신규모멘텀 ({n_score:>2}/15)": (
            " / ".join(n_labels) if n_labels else
            "신고가 멀거나 베이스 없음"),
        f"S 수급거래량 ({s_score:>2}/15)": (
            f"{' / '.join(s_labels)} | {tv_str}/일" if s_labels else
            f"거래량 부족 | {tv_str}/일"),
        f"L 상대강도  ({l_score:>2}/20)": (
            f"RS {rs:.0f}% (기준≥{min_rs_v:.0f}%) | 3개월{mom3m:+.0f}% 6개월{mom6m:+.0f}%"),
        f"I 기관수급  ({i_score:>2}/ 5)": (
            f"5일거래량{vr5:.1f}배 | 당일{vol_ratio:.1f}배 (기관관심 추정)"),
        f"M 시장방향  ({m_score:>2}/10)": m_label,
        "━━ 베이스 패턴":             "",
        "Tight/Flat Base":   (f"{'✅' if flat_base else '🟡' if tight_base else '❌'} "
                               f"35일변동폭:{tight_pct:.0f}% (기준<{float(p('flat_base_pct') or 15):.0f}%)"),
        "피벗 가격":        pf(pivot_price),
        "매수 가능 구간":   f"{pf(pivot_price)} ~ {pf(buy_zone_hi)} (피벗 +5% 이내)",
        "━━ 리스크":                  "",
        "손절 기준":        f"{pf(stop_price)} ({-float(p('stop_loss_pct') or 8):.0f}%)  오닐 원칙: -7~-8%",
        "데이터":           "🟢 실시간" if ind.get("realtime") else "🟡 샘플",
    }
    return total, detail


# ═══════════════════════════════════════════════════════════
# 4. 쿨라매기 — 브레이크아웃 + EP 통합 스캐너
# ═══════════════════════════════════════════════════════════
def score_kullamagi(name, ticker):
    """
    Kristjan Kullamägi (Qullamaggie) 방식 — 한국시장 실전판

    ══ 핵심 셋업 2가지 ════════════════════════════════════════════════
    A. Breakout (브레이크아웃):
       최근 강한 상승 → 변동성 압축(박스권) → 거래량 동반 돌파
       핵심: 최근 60일 +25%↑ + 10~30일 박스권 + 20/50일선 위 + 52주 신고가 근처

    B. EP (Episodic Pivot):
       예상 밖 호재(실적서프라이즈·수주·공시) + 갭상승 + 거래량 폭증
       핵심: 갭상승 +5%↑ + 거래량 3배↑ + 재료 확인

    ══ 구조 ════════════════════════════════════════════════════════════
    1단계: 잡주 제거 (관리종목·거래정지·저유동성·스팩)
    2단계: 셋업 판별 (Breakout/EP)
    3단계: 100점 점수화 (추세30 + 압축돌파25 + 수급거래량25 + 재료20)
    4단계: 진입가·손절가 자동 산출

    ══ 점수 기준 ════════════════════════════════════════════════════════
    85↑ → 즉시 관심  /  75~84 → 관찰  /  74↓ → 제외
    """
    p = lambda k: get_param("쿨라매기", k)
    try:
        ind     = _get_indicators(ticker)
        fi      = _get_financial_indicators(ticker)
        d       = fetch_realtime(ticker)
        closes  = d.get("closes",  [])
        highs   = d.get("highs",   [])
        lows    = d.get("lows",    [])
        volumes = d.get("volumes", [])
        opens   = d.get("opens",   [])
    except Exception:
        return 0.0, {"_filtered": True, "탈락": "데이터 오류"}

    if len(closes) < 60:
        return 0.0, {"_filtered": True, "탈락": f"데이터 부족 ({len(closes)}봉)"}

    # 거래정지 즉시 제외
    try:
        from data_fetcher import is_trading_halted
        _h, _hr = is_trading_halted(d)
        if _h:
            return 0.0, {"_filtered": True, "탈락": f"거래정지: {_hr}"}
    except Exception:
        pass

    # 공통 품질 필터
    _qok, _qreason = _apply_quality_filters(ind, fi, ticker, "쿨라매기")
    if not _qok:
        return 0.0, {"_filtered": True, "탈락": _qreason,
                     "데이터": "🟢 실시간" if ind.get("realtime") else "🟡 샘플"}

    cur   = _g(ind, "cur", closes[-1])
    is_kr = _is_kr(ticker)
    pf    = (lambda v: f"₩{v:,.0f}") if is_kr else (lambda v: f"${v:.2f}")
    n     = len(closes)

    # 스팩·ETF·ETN 제외
    excl_kw = ["스팩","SPAC","ETF","ETN"]
    if any(kw in name for kw in excl_kw):
        return 0.0, {"_filtered": True, "탈락": f"제외 대상: {name}"}

    # 최소 주가
    min_price = float(p("min_price") or (5000 if is_kr else 5.0))
    if cur < min_price:
        return 0.0, {"_filtered": True, "탈락": f"저가주 ({pf(cur)} < {pf(min_price)})"}

    # ── 이평선 계산 (쿨라매기 핵심 3선: 10EMA · 20EMA · 50SMA) ─────
    # EMA 계산 함수
    def _calc_ema(prices, period):
        if len(prices) < period:
            return [None]*len(prices)
        result = [None]*(period-1)
        seed = sum(prices[:period])/period
        result.append(seed)
        k = 2/(period+1)
        for p_val in prices[period:]:
            seed = p_val*k + seed*(1-k)
            result.append(seed)
        return result

    ema10_v = _calc_ema(closes, 10)
    ema20_v = _calc_ema(closes, 20)
    ma50_v  = sma(closes, 50)
    ma200_v = sma(closes, 200)
    ma10_v  = sma(closes, 10)   # 단순 10일 MA (보조)

    def _last(v, default=cur):
        return next((x for x in reversed(v) if x is not None), default)

    ema10 = _last(ema10_v)
    ema20 = _last(ema20_v)
    ma50  = _last(ma50_v)
    ma200 = _last(ma200_v)
    ma20  = _last(sma(closes, 20))  # SMA20 (호환용)

    # 50SMA 기울기 (20봉 전 대비)
    ma50_20ago = next((x for i,x in enumerate(reversed(ma50_v)) if x is not None and i>=20), ma50)
    ma50_rising = (ma50 > ma50_20ago * 0.998) if ma50_20ago else True

    # 거래량
    vol20_avg = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 0
    vol_today = volumes[-1] if volumes else 0
    vol_ratio = vol_today / max(vol20_avg, 1) if vol20_avg > 0 else 1.0

    # 거래대금
    tv_val = cur * vol_today
    tv_b   = tv_val / 1e8 if is_kr else tv_val / 1e6
    min_tv = float(p("min_trading_value") or 50)  # 50억 기본

    tv_ok = (tv_b >= min_tv) if is_kr else (tv_b >= min_tv / 30)
    if not tv_ok:
        tv_str = f"₩{tv_b:.0f}억" if is_kr else f"${tv_b:.1f}M"
        return 0.0, {"_filtered": True,
                     "탈락": f"거래대금 부족 {tv_str} < {'₩'+str(int(min_tv))+'억' if is_kr else '$'+str(int(min_tv/30))+'M'}"}

    # 52주 고저
    hi52 = max(closes[-min(n, 252):])
    lo52 = min(closes[-min(n, 252):])
    from_hi52 = (cur - hi52) / max(hi52, 1) * 100
    from_lo52 = (cur - lo52) / max(lo52, 1) * 100

    # 수익률
    mom3m = (closes[-1]-closes[-min(63,n-1)]) / max(closes[-min(63,n-1)],1)*100 if n>=5 else 0
    mom1m = (closes[-1]-closes[-min(21,n-1)]) / max(closes[-min(21,n-1)],1)*100 if n>=5 else 0
    mom60d = (closes[-1]-closes[-min(60,n-1)]) / max(closes[-min(60,n-1)],1)*100 if n>=5 else 0

    # ══════════════════════════════════════════════════════════════════
    # 2단계: 셋업 판별
    # ══════════════════════════════════════════════════════════════════

    # ── A. Breakout 셋업 판별 ─────────────────────────────────────────
    # 최근 15~30일 박스권 변동폭
    box_range = min(20, n-1)
    recent_hi = max((highs or closes)[-box_range:]) if (highs or closes) else cur
    recent_lo = min((lows  or closes)[-box_range:]) if (lows  or closes) else cur
    box_pct   = (recent_hi - recent_lo) / max(recent_lo, 1) * 100
    box_compressed = box_pct < float(p("vcp_tightness_pct") or 15)  # 박스권 압축

    # 60일 내 상승 이력 (하락/조정 시장 반영: 기준 낮춤)
    min_base = float(p("min_mom3m_pct") or 10)  # 파라미터 기본 10%로 조정
    strong_base = mom60d >= min_base  # 60일 +10% 이상

    # ── 쿨라매기 핵심 이평선 조건 (하드 필터) ──────────────────────
    # 현재가 > 10EMA > 20EMA > 50SMA (완전 정배열)
    above_ema10 = (cur   > ema10) if ema10 > 0 else True
    above_ema20 = (cur   > ema20) if ema20 > 0 else True
    above_ma50  = (cur   > ma50)  if ma50  > 0 else True
    ema10_above_ema20 = (ema10 > ema20) if (ema10 > 0 and ema20 > 0) else True
    ema20_above_ma50  = (ema20 > ma50)  if (ema20 > 0 and ma50  > 0) else True
    above_ma20  = (cur   > ma20)  if ma20  > 0 else True  # 호환용

    # 완전 정배열: cur > 10EMA > 20EMA > 50SMA
    full_ma_align = (above_ema10 and above_ema20 and above_ma50
                     and ema10_above_ema20 and ema20_above_ma50)

    # 부분 정배열: cur > 20EMA > 50SMA (10EMA 제외)
    partial_ma_align = (above_ema20 and above_ma50 and ema20_above_ma50)

    # 이평선 정배열 파라미터 (필수 여부)
    req_ma_align = p("require_ma_align")

    if req_ma_align and not partial_ma_align:
        return 0.0, {
            "_filtered": True,
            "탈락(이평선필터)": f"쿨라매기 이평선 정배열 미충족 (현재가>20EMA>50SMA 필수)",
            "현재가":  pf(cur),
            "10EMA":   pf(ema10),
            "20EMA":   pf(ema20),
            "50SMA":   pf(ma50),
            "정배열":  f"{'✅' if full_ma_align else '❌'} 완전 / {'✅' if partial_ma_align else '❌'} 부분",
            "50SMA 기울기": f"{'✅ 상승' if ma50_rising else '❌ 하락/횡보'}",
            "데이터":  "🟢 실시간" if ind.get("realtime") else "🟡 샘플",
        }

    # 52주 신고가 근처 (파라미터 기준: 기본 -25%)
    max_52h_drop = float(p("max_from_52h_pct") or 25)
    near_52h = from_hi52 >= -max_52h_drop

    # 박스 상단 돌파
    box_break = (cur >= recent_hi * 0.998)
    vol_break = (vol_ratio >= float(p("min_vol_ratio") or 1.5))

    # Breakout: 쿨라매기 이평 정배열 + 기반 + 고점 범위
    # 완전 정배열이면 가장 강한 신호, 부분 정배열이어도 OK
    breakout_ok = (strong_base
                   and (full_ma_align or partial_ma_align)  # 이평 정배열 (핵심!)
                   and (near_52h or from_hi52 >= -30))      # 52주 -30% 이내
    vol_break = (vol_ratio >= float(p("min_vol_ratio") or 1.5))
    breakout_strong = breakout_ok and (box_break or vol_break)

    # ── B. EP (Episodic Pivot) 셋업 판별 ─────────────────────────────
    # 갭상승 확인
    min_gap = float(p("min_gap_pct") or 3.0)   # 기본 +3% (한국 실정)
    gap_pct = 0.0
    if opens and len(opens) >= 1 and len(closes) >= 2:
        gap_pct = (opens[-1] - closes[-2]) / max(closes[-2], 1) * 100
    elif len(closes) >= 2:
        gap_pct = (closes[-1] - closes[-2]) / max(closes[-2], 1) * 100

    gap_up    = gap_pct >= min_gap
    vol_ep    = vol_ratio >= float(p("min_vol_ep_mult") or 2.0)  # 거래량 2배

    # 갭업 후 유지 (갭 채우지 않음)
    gap_hold = True
    if opens and len(opens) >= 1 and lows:
        gap_hold = lows[-1] >= closes[-2] * 0.98 if len(closes) >= 2 else True

    # EP: 갭업 + 거래량 + 이평선 위 종가 유지
    ep_ma_ok = above_ema20  # EP는 20EMA 위면 충분 (갭으로 이평 뛰어넘기도 함)
    ep_ok = gap_up and vol_ep and gap_hold and ep_ma_ok

    # 두 셋업 모두 미충족이면 탈락
    if not breakout_ok and not ep_ok:
        fail_reason = []
        if not strong_base:       fail_reason.append(f"기반부족(60일{mom60d:.0f}%<{min_base:.0f}%)")
        if not partial_ma_align:  fail_reason.append(f"이평역배열(cur>{pf(ema20)}>MA50 불충족)")
        if not (near_52h or from_hi52 >= -30): fail_reason.append(f"52주고점{from_hi52:.0f}%<-30%")
        if not gap_up:            fail_reason.append(f"EP갭부족({gap_pct:.1f}%<{min_gap:.0f}%)")
        return 0.0, {
            "_filtered": True,
            "탈락": "Breakout/EP 셋업 없음: " + " / ".join(fail_reason[:2]),
            "Breakout 조건": f"{'✅' if breakout_ok else '❌'}",
            "EP 조건":       f"{'✅' if ep_ok else '❌'}",
            "60일 수익률":   f"{mom60d:+.1f}%",
            "데이터": "🟢 실시간" if ind.get("realtime") else "🟡 샘플",
        }

    # 셋업 유형 결정
    if breakout_strong and ep_ok:
        setup_type = "🚀 Breakout + EP"
    elif ep_ok:
        setup_type = "⚡ EP (Episodic Pivot)"
    elif breakout_strong:
        setup_type = "📈 Breakout (강)"
    elif breakout_ok:
        setup_type = "🔍 Breakout (대기)"
    else:
        setup_type = "⚡ EP"

    # ══════════════════════════════════════════════════════════════════
    # 3단계: 100점 점수화
    # ══════════════════════════════════════════════════════════════════

    # ── A. 추세 (30점) — 쿨라매기 이평 정배열 반영 ─────────────────────
    trend_sc = 0
    # 완전 정배열: cur > 10EMA > 20EMA > 50SMA (15점)
    if full_ma_align:        trend_sc += 15
    elif partial_ma_align:   trend_sc += 10
    elif above_ema20:        trend_sc += 5
    # 50SMA 기울기 상승 (5점) — 쿨라매기는 50SMA 방향을 중시
    if ma50_rising:          trend_sc += 5
    # 52주 고점 근접 (10점)
    if from_hi52 >= -5:      trend_sc += 10
    elif from_hi52 >= -15:   trend_sc += 7
    elif from_hi52 >= -25:   trend_sc += 4
    trend_sc = min(30, trend_sc)

    # ── B. 압축·브레이크아웃 (25점) ──────────────────────────────────
    comp_sc = 0
    if box_compressed:                            comp_sc += 10  # 변동성 축소
    if box_break:
        comp_sc += 15                                              # 박스 상단 돌파
    elif cur >= recent_hi * 0.97:
        comp_sc += 8                                               # 근접
    if strong_base:
        comp_sc += min(5, int(mom60d / 10))                       # 모멘텀 강도 보너스
    if ep_ok:
        comp_sc += 5                                               # EP = 압축 대신 갭
    comp_sc = min(25, comp_sc)

    # ── C. 수급·거래량 (25점) ─────────────────────────────────────────
    vol_sc = 0
    if vol_ratio >= float(p("min_vol_ep_mult") or 3.0):
        vol_sc += 15   # EP급 폭증
    elif vol_ratio >= float(p("min_vol_ratio") or 1.5):
        vol_sc += 10   # 브레이크아웃 수준

    tv_ratio = tv_b / max(min_tv, 1)
    if tv_ratio >= 5:    vol_sc += 10
    elif tv_ratio >= 3:  vol_sc += 8
    elif tv_ratio >= 1:  vol_sc += 5
    vol_sc = min(25, vol_sc)

    # ── D. 재료/촉매 (20점) ───────────────────────────────────────────
    mat_sc = 0
    mat_labels = []

    # 실적 성장 확인
    eps_g  = fi.get("earn_growth") if fi else None
    rev_g  = fi.get("rev_growth")  if fi else None
    if eps_g is not None:
        if eps_g >= 25:   mat_sc += 10; mat_labels.append(f"EPS서프라이즈+{eps_g:.0f}%")
        elif eps_g >= 10: mat_sc += 6;  mat_labels.append(f"EPS성장+{eps_g:.0f}%")
        elif eps_g > 0:   mat_sc += 3;  mat_labels.append(f"EPS증가")
    if rev_g is not None and rev_g >= 15:
        mat_sc += 5; mat_labels.append(f"매출+{rev_g:.0f}%")

    # EP = 강한 재료 가능성 높음
    if ep_ok:
        mat_sc = max(mat_sc, 15)
        if not mat_labels: mat_labels.append(f"EP갭업+{gap_pct:.1f}%+거래량{vol_ratio:.1f}배")

    # 모멘텀 강도가 높으면 재료 있을 가능성 (대리 지표)
    if mat_sc == 0 and mom3m >= 30:
        mat_sc = 8; mat_labels.append(f"모멘텀강(3개월+{mom3m:.0f}%)")
    elif mat_sc == 0:
        mat_sc = 3; mat_labels.append("재료 미확인")

    mat_sc = min(20, mat_sc)

    # ── RS 상대강도 (보너스) ──────────────────────────────────────────
    rs = _get_rs_with_percentile(ticker, ind)
    min_rs_val = float(p("min_rs") or 60)
    if rs < min_rs_val and rs > 0:
        return 0.0, {
            "_filtered": True,
            "탈락(RS)": f"RS {rs:.0f}% < 최소 {min_rs_val:.0f}% (시장 주도주 아님)",
            "데이터": "🟢 실시간" if ind.get("realtime") else "🟡 샘플",
        }
    rs_bonus = min(10, max(0, (rs - min_rs_val) / (100 - min_rs_val) * 10)) if rs > 0 else 5

    # ── 최종 점수 ──────────────────────────────────────────────────────
    total = trend_sc + comp_sc + vol_sc + mat_sc + rs_bonus

    # ── 등급 및 진입/손절가 산출 ──────────────────────────────────────
    if total >= 85:    grade = "🏆 A급 즉시 관심"
    elif total >= 75:  grade = "⭐ B급 관찰"
    elif total >= 65:  grade = "🔍 C급"
    else:              grade = "📌 D급"

    # 진입가: 현재가 (브레이크아웃) 또는 갭상승 후 첫 고점 (EP)
    entry_price = cur
    # 손절가: EP=당일저가, Breakout=최근 박스 하단 OR -5~7%
    if ep_ok and lows:
        stop_price = min(lows[-1], cur * 0.95)
    else:
        stop_price = max(recent_lo * 0.995, cur * 0.93)
    stop_pct = (stop_price - cur) / cur * 100

    tv_str = f"₩{tv_b:.0f}억" if is_kr else f"${tv_b:.1f}M"
    detail = {
        "━━ 쿨라매기 셋업":     f"{setup_type}",
        "등급":                  f"{grade}  ({total:.0f}/100점)",
        "━━ 셋업 분석":          "",
        "Breakout":             (f"{'✅ 강' if breakout_strong else '✅ 대기' if breakout_ok else '❌'} "
                                  f"60일+{mom60d:.0f}% / 박스{box_pct:.0f}% / "
                                  f"{'돌파!' if box_break else '근접'} / 거래량{vol_ratio:.1f}배"),
        "EP (Episodic Pivot)": (f"{'✅' if ep_ok else '❌'} "
                                  f"갭+{gap_pct:.1f}% / 거래량{vol_ratio:.1f}배"
                                  + (f" / 갭유지✅" if gap_hold else " / 갭밀림❌")),
        "━━ 추세 (30점)":        f"{trend_sc}/30",
        "이평 정배열":           (f"{'🏆 완전' if full_ma_align else '🟡 부분' if partial_ma_align else '❌ 역배열'}"
                                  f"  cur({pf(cur)}) > 10EMA({pf(ema10)}) > 20EMA({pf(ema20)}) > 50SMA({pf(ma50)})"),
        "50SMA 기울기":          f"{'✅ 상승' if ma50_rising else '❌ 하락'} (20봉 대비 {(ma50/max(ma50_20ago,1)-1)*100:+.2f}%)",
        "52주 포지션":           f"{from_hi52:.1f}% (고점:{pf(hi52)})  저점+{from_lo52:.1f}%",
        "━━ 압축·돌파 (25점)":   f"{comp_sc}/25",
        "박스권 압축":           f"{'✅' if box_compressed else '—'} ({box_pct:.1f}% {'압축' if box_compressed else '넓음'})",
        "박스 상단 돌파":        f"{'✅' if box_break else '—'} 상단:{pf(recent_hi)}  현재:{pf(cur)}",
        "60일 모멘텀":           f"{mom60d:+.1f}%  /  3개월:{mom3m:+.1f}%  /  1개월:{mom1m:+.1f}%",
        "━━ 수급·거래량 (25점)": f"{vol_sc}/25",
        "거래량 배율":           f"{vol_ratio:.1f}배 (20일평균 대비)",
        "거래대금":              f"{tv_str}/일",
        "━━ 재료·촉매 (20점)":   f"{mat_sc}/20  {' / '.join(mat_labels)}",
        "━━ RS 보너스":          f"+{rs_bonus:.0f}점  RS {rs:.0f}%",
        "━━ 진입/손절 기준":     "",
        "진입가":                f"▶ {pf(entry_price)} (현재가 기준)",
        "손절가":                f"{pf(stop_price)} ({stop_pct:.1f}%)",
        "손절 근거":             f"{'당일 저가' if ep_ok else '박스하단'} / 20EMA({pf(ema20)}) 이탈 시 청산",
        "쿨라매기 MA 손절 원칙": f"EP: 당일저가({pf(lows[-1] if lows else cur)}) / Breakout: 20EMA({pf(ema20)}) 종가이탈",
        "데이터":                "🟢 실시간" if ind.get("realtime") else "🟡 샘플",
    }
    return total, detail


# ═══════════════════════════════════════════════════════════
# 5. 오닐+미너비니 — CAN SLIM + 트렌드 템플릿 합산
# ═══════════════════════════════════════════════════════════
def score_oneil_minervini(name, ticker):
    """
    오닐+미너비니 — 두 전략 교집합 (더 엄격한 필터)
    1단계: 이평선 정배열 (오닐 7조건 = 미너비니 트렌드 템플릿)
    2단계: CAN SLIM + RS 복합 점수화
    3단계: VCP 패턴 추가 확인
    두 전략 모두 충족해야 하므로 통과 종목 수는 적지만 신뢰도 최고.
    """
    p = lambda k: get_param("오닐+미너비니", k)
    try:
        ind     = _get_indicators(ticker)
        fi      = _get_financial_indicators(ticker)
        d       = fetch_realtime(ticker)
        closes  = d.get("closes",  [])
        highs   = d.get("highs",   [])
        lows    = d.get("lows",    [])
        volumes = d.get("volumes", [])
    except Exception:
        return 0.0, {"_filtered": True, "탈락": "데이터 오류"}

    if len(closes) < 60:
        return 0.0, {"_filtered": True, "탈락": f"데이터 부족"}

    try:
        from data_fetcher import is_trading_halted
        _h, _hr = is_trading_halted(d)
        if _h: return 0.0, {"_filtered": True, "탈락": f"거래정지: {_hr}"}
    except: pass

    _qok, _qreason = _apply_quality_filters(ind, fi, ticker, "오닐+미너비니")
    if not _qok:
        return 0.0, {"_filtered": True, "탈락": _qreason,
                     "데이터": "🟢 실시간" if ind.get("realtime") else "🟡 샘플"}

    if any(kw in name for kw in ["스팩","SPAC","ETF","ETN"]):
        return 0.0, {"_filtered": True, "탈락": f"제외: {name}"}

    cur   = _g(ind, "cur", closes[-1])
    is_kr = _is_kr(ticker)
    pf    = (lambda v: f"₩{v:,.0f}") if is_kr else (lambda v: f"${v:.2f}")
    n     = len(closes)

    # 이평선
    ma50_v  = sma(closes, 50);  ma150_v = sma(closes, 150); ma200_v = sma(closes, 200)
    def _last(v, default=cur): return next((x for x in reversed(v) if x is not None), default)
    ma50 = _last(ma50_v); ma150 = _last(ma150_v); ma200 = _last(ma200_v)
    ma200_20ago = next((x for i,x in enumerate(reversed(ma200_v)) if x is not None and i>=20), ma200)
    ma200_rising= (ma200 >= ma200_20ago * 0.998)
    ma200_slope = (ma200 - ma200_20ago) / max(ma200_20ago, 1) * 100
    has_ma = ma50 > 0 and ma150 > 0 and ma200 > 0

    hi52 = max(closes[-min(n,252):]); lo52 = min(closes[-min(n,252):])
    from_hi52 = (cur-hi52)/max(hi52,1)*100; from_lo52 = (cur-lo52)/max(lo52,1)*100

    # ── 이평선 정배열 하드 필터 (오닐 + 미너비니 공통) ──────────────
    min_52l = float(p("min_from_52l_pct") or 30)
    max_52h = float(p("max_from_52h_pct") or 25)
    trend_checks = {
        "현재가>MA50":      (cur > ma50)          if ma50 > 0  else True,
        "현재가>MA150":     (cur > ma150)         if ma150 > 0 else True,
        "현재가>MA200":     (cur > ma200)         if ma200 > 0 else True,
        "MA50>MA150>MA200": (ma50>ma150>ma200)    if has_ma    else True,
        "MA200 기울기 상승": ma200_rising,
        f"52주저점+{min_52l:.0f}%": from_lo52 >= min_52l,
        f"52주고점-{max_52h:.0f}%": from_hi52 >= -max_52h,
    }
    failed = [k for k,v in trend_checks.items() if not v]
    if failed:
        return 0.0, {
            "_filtered": True,
            "탈락(이평선)": " / ".join(f[:25] for f in failed),
            "MA50": pf(ma50), "MA200": pf(ma200),
            "52주고": f"{from_hi52:.0f}%", "52주저": f"+{from_lo52:.0f}%",
            "데이터": "🟢 실시간" if ind.get("realtime") else "🟡 샘플",
        }

    # RS 하드 필터
    rs = _get_rs_with_percentile(ticker, ind)
    min_rs = float(p("min_rs") or 75)
    if rs > 0 and rs < min_rs:
        return 0.0, {"_filtered": True, "탈락": f"RS {rs:.0f}% < {min_rs:.0f}%",
                     "데이터": "🟢 실시간" if ind.get("realtime") else "🟡 샘플"}

    # VCP 패턴 (오닐+미너비니 교집합 핵심)
    min_w = int(p("vcp_min_waves") or 2)
    tight = float(p("vcp_tightness_pct") or 20.0)
    vdu_r = float(p("vdu_ratio") or 0.9)
    vcp = _calc_vcp(highs, lows, closes, volumes, min_waves=min_w, tightness_pct=tight, vdu_ratio=vdu_r)
    vcp_req = p("vcp_required")
    if vcp_req:
        vcp_fails = []
        if vcp["c_count"] < min_w:       vcp_fails.append(f"수축파동 {vcp['c_count']}/{min_w}개")
        if not vcp["tightness_ok"]:       vcp_fails.append(f"Tightness {vcp['last_contraction']:.0f}%")
        if vcp_fails:
            return 0.0, {"_filtered": True, "탈락(VCP)": " / ".join(vcp_fails),
                         "데이터": "🟢 실시간" if ind.get("realtime") else "🟡 샘플"}

    # 재무 데이터
    eps_g = fi.get("earn_growth") if fi else None
    ann_g = fi.get("eps_growth")  if fi else None
    roe   = fi.get("roe")         if fi else None
    rev_g = fi.get("rev_growth")  if fi else None
    op_g  = fi.get("op_growth")   if fi else None

    vol20_avg = sum(volumes[-20:])/20 if len(volumes)>=20 else 0
    vol_today = volumes[-1] if volumes else 0
    vol_ratio = vol_today/max(vol20_avg,1)

    # ── 점수 합산 (오닐+미너비니 동시 만족한 프리미엄 종목) ──────────
    w_rs  = float(p("weight_rs")  or 30)
    w_eps = float(p("weight_eps") or 25)
    w_ma  = float(p("weight_ma")  or 25)
    w_vol = float(p("weight_vol") or 10)
    w_vcp = float(p("weight_vcp") or 10)

    rs_s = (rs - min_rs) / max(99-min_rs,1) * w_rs if rs>0 else w_rs*0.5
    op_src = op_g if op_g is not None else eps_g
    eps_s = min(w_eps, max(0, (op_src or 0)/2)) if op_src is not None else w_eps*0.4
    rev_s = min(w_vol*0.8, max(0, (rev_g or 0)/3)) if rev_g is not None else 3
    if has_ma and cur > ma50 > ma150 > ma200: ma_s = w_ma
    elif has_ma and cur > ma50 > ma200:       ma_s = w_ma*0.7
    else:                                      ma_s = w_ma*0.4
    vcp_s = (w_vcp if (vcp["tightness_ok"] and vcp["higher_lows"] and vcp["vdu_ok"])
             else w_vcp*0.6 if vcp["tightness_ok"] else w_vcp*0.3)

    # 돌파 직전 보너스
    box_hi = max(closes[-min(30,n):])
    at_piv = (box_hi - cur)/max(box_hi,1)*100
    brk_bonus = 8 if (vol_ratio>=1.5 and closes[-1]>box_hi) else 5 if (0<=at_piv<=3) else 0

    # RS 복합 모멘텀 보너스
    mom3m  = (closes[-1]-closes[-min(63,n-1)])/max(closes[-min(63,n-1)],1)*100 if n>=5 else 0
    mom6m  = (closes[-1]-closes[-min(126,n-1)])/max(closes[-min(126,n-1)],1)*100 if n>=5 else 0
    mom_composite = mom3m*0.4 + mom6m*0.6
    mom_bonus = min(8, max(0, mom_composite/5))

    rank_val = rs_s + eps_s + rev_s + ma_s + vcp_s + brk_bonus + mom_bonus

    c_str = "→".join(f"{c:.0f}%" for c in vcp["contractions"]) or "파동미형성"
    detail = {
        "━━ 오닐+미너비니 교집합": f"(통과 — 최고 신뢰도)",
        "━━ 이평선 정배열 (통과)": f"✅ {pf(cur)}>{pf(ma50)}>{pf(ma150)}>{pf(ma200)}" if has_ma else "✅(부족)",
        "MA200 기울기":  f"✅ {ma200_slope:+.2f}%",
        "52주 포지션":   f"고점{from_hi52:.0f}%  저점+{from_lo52:.0f}%",
        "RS 백분위":     f"✅ {rs:.0f}% (기준≥{min_rs:.0f}%)",
        "EPS/영업이익":  f"{'✅' if op_src and op_src>0 else '—'} {f'{op_src:+.0f}%' if op_src else '없음'}",
        "━━ VCP 패턴":   vcp["summary"],
        "수축 파동":     c_str,
        "Higher Lows":   f"{'✅' if vcp['higher_lows'] else '❌'}",
        "Tightness":     f"{'✅' if vcp['tightness_ok'] else '❌'} {vcp['last_contraction']:.0f}%",
        "피벗 가격":     pf(vcp["pivot_price"]),
        "돌파/직전":     f"{'🚀돌파' if brk_bonus>=8 else '⏳직전' if brk_bonus==5 else '대기'} (고점-{at_piv:.1f}%  거래량{vol_ratio:.1f}배)",
        "━━ 점수":       f"RS:{rs_s:.0f}+실적:{eps_s:.0f}+매출:{rev_s:.0f}+이평:{ma_s:.0f}+VCP:{vcp_s:.0f}+돌파:{brk_bonus:.0f}+모멘텀:{mom_bonus:.0f} = {rank_val:.1f}",
        "데이터":        "🟢 실시간" if ind.get("realtime") else "🟡 샘플",
    }
    return rank_val, detail


# ═══════════════════════════════════════════════════════════
# 6. RSI+MACD+BB — 3지표 합치 반전·스윙 전략
# ═══════════════════════════════════════════════════════════
def score_rmb(name, ticker):
    """
    RSI + MACD + 볼린저밴드 — 한국시장 실전 스캐너 (문서 기반 완성판)

    전략: "추세가 살아있는 종목 중 눌림목 후 재상승 직전 종목"
         = 단순 바닥잡기보다 '이미 강한 종목이 다시 출발하는 자리' 탐색

    ══ 구조 ════════════════════════════════════════════════════════════
    1단계: 잡주 제거 (스팩·ETF·ETN·저유동성·거래정지)
    2단계: 기본 추세 필터 (MA20>MA60, 현재가>MA20)
    3단계: 3지표 동시 점수화 (RSI+MACD+BB)
    4단계: 거래량/거래대금 확인
    5단계: 최신고가 근접도 보너스 (너무 약한 종목 제외)

    ══ 점수 100점 만점 ══════════════════════════════════════════════════
    추세    : 25점 (MA20>MA60·현재가>MA20)
    RSI     : 20점 (45~65 이내·상승 중)
    MACD    : 25점 (골든크로스·히스토그램 증가)
    볼린저  : 20점 (중심선 위·상단 접근·폭 상태)
    거래량  : 10점 (1.2배↑·거래대금)
    """
    p = lambda k: get_param("RSI+MACD+BB", k)
    try:
        ind     = _get_indicators(ticker)
        fi      = _get_financial_indicators(ticker)
        d       = fetch_realtime(ticker)
        closes  = d.get("closes",  [])
        volumes = d.get("volumes", [])
        highs   = d.get("highs",   [])
        lows    = d.get("lows",    [])
        opens   = d.get("opens",   [])
    except Exception:
        return 0.0, {"_filtered": True, "탈락": "데이터 오류"}

    if len(closes) < 60:
        return 0.0, {"_filtered": True, "탈락": f"데이터 부족 ({len(closes)}봉 < 60봉)"}

    # ── 거래정지 즉시 제외 ─────────────────────────────────────────
    try:
        from data_fetcher import is_trading_halted
        _h, _hr = is_trading_halted(d)
        if _h:
            return 0.0, {"_filtered": True, "탈락": f"거래정지: {_hr}"}
    except Exception:
        pass

    cur   = _g(ind, "cur", closes[-1])
    n     = len(closes)
    is_kr = _is_kr(ticker)
    pf    = (lambda v: f"₩{v:,.0f}") if is_kr else (lambda v: f"${v:.2f}")

    # ══════════════════════════════════════════════════════════════════
    # 1단계: 잡주 제거 필터
    # ══════════════════════════════════════════════════════════════════
    # 스팩·ETF·ETN 제외
    excl_kw = ["스팩","SPAC","ETF","ETN"]
    if any(kw in name for kw in excl_kw):
        return 0.0, {"_filtered": True, "탈락": f"제외 대상: {name}"}

    # 최소 가격 (KR: 3,000원, US: $3)
    min_price = float(p("min_price") or (3000 if is_kr else 3.0))
    if cur < min_price:
        return 0.0, {"_filtered": True, "탈락": f"저가주 제외 ({pf(cur)} < {pf(min_price)})"}

    # 공통 품질 필터 (거래대금)
    _qok, _qreason = _apply_quality_filters(ind, fi, ticker, "RSI+MACD+BB")
    if not _qok:
        return 0.0, {"_filtered": True, "탈락": _qreason,
                     "데이터": "🟢 실시간" if ind.get("realtime") else "🟡 샘플"}

    # ══════════════════════════════════════════════════════════════════
    # 2단계: 기본 추세 필터 (MA20 > MA60, 현재가 > MA20)
    # ══════════════════════════════════════════════════════════════════
    ma20_v  = sma(closes, 20)
    ma60_v  = sma(closes, 60)
    ma200_v = sma(closes, 200)

    def _last(v, default=cur):
        return next((x for x in reversed(v) if x is not None), default)

    ma20  = _last(ma20_v)
    ma60  = _last(ma60_v)
    ma200 = _last(ma200_v)
    ma20_prev = next((x for i,x in enumerate(reversed(ma20_v)) if x is not None and i>=5), ma20)

    trend_up   = (ma20 > ma60)      if (ma20 > 0 and ma60 > 0) else True
    above_ma20 = (cur  > ma20)      if ma20 > 0 else True
    above_ma200= (cur  > ma200)     if ma200 > 0 else True
    ma200_gap  = (cur - ma200) / max(ma200, 1) * 100 if ma200 > 0 else 0

    req_trend = p("require_trend")
    if req_trend and not (trend_up and above_ma20):
        return 0.0, {
            "_filtered": True,
            "탈락(추세)": f"MA20({'↑' if trend_up else '↓'}) > MA60 + 현재가>MA20 미충족",
            "MA20":  pf(ma20), "MA60": pf(ma60),
            "데이터": "🟢 실시간" if ind.get("realtime") else "🟡 샘플",
        }

    # 추세 점수 (25점)
    trend_sc = 0
    if trend_up:         trend_sc += 15  # MA20>MA60
    if above_ma20:       trend_sc += 10  # 현재가>MA20
    # 추가 보너스: MA20 기울기 상승
    ma20_slope = (ma20 - ma20_prev) / max(ma20_prev, 1) * 100
    if ma20_slope > 0.5: trend_sc = min(25, trend_sc + 3)

    # ══════════════════════════════════════════════════════════════════
    # 3단계: RSI 점수화 (20점)
    # 조건: RSI 45~65 사이 + 전일 대비 상승
    # ══════════════════════════════════════════════════════════════════
    rsi_period = int(p("rsi_period") or 14)
    rsi_v      = rsi_calc(closes, rsi_period)
    rsi_cur    = _last(rsi_v, 50)
    rsi_prev   = next((x for i,x in enumerate(reversed(rsi_v)) if x is not None and i>=1), rsi_cur)
    rsi_prev3  = next((x for i,x in enumerate(reversed(rsi_v)) if x is not None and i>=3), rsi_cur)

    rsi_low   = float(p("rsi_low")  or 45)   # 기본 45 (너무 약한 종목 제외)
    rsi_high  = float(p("rsi_high") or 65)   # 기본 65 (너무 과열 제외)
    rsi_os    = float(p("rsi_oversold_max") or 38)  # 과매도 기준 (예외적 허용)

    rsi_in_range = (rsi_low <= rsi_cur <= rsi_high)
    rsi_rising   = (rsi_cur > rsi_prev)
    rsi_oversold = (rsi_cur <= rsi_os)   # 과매도 특례 (예외 허용)
    rsi_50cross  = (rsi_prev < 50 <= rsi_cur)  # RSI 50 돌파 (강한 신호)

    rsi_sc = 0
    if rsi_50cross:
        rsi_sc = 20; rsi_lbl = f"✅ RSI 50 돌파({rsi_cur:.0f})"
    elif rsi_in_range and rsi_rising:
        rsi_sc = 20; rsi_lbl = f"✅ 이상구간+상승({rsi_cur:.0f}↑)"
    elif rsi_in_range:
        rsi_sc = 13; rsi_lbl = f"🟡 이상구간({rsi_cur:.0f})"
    elif rsi_rising and rsi_cur > rsi_low - 5:
        rsi_sc = 8;  rsi_lbl = f"🟡 상승중({rsi_cur:.0f}↑)"
    elif rsi_oversold:
        rsi_sc = 10; rsi_lbl = f"⚡ 과매도반등({rsi_cur:.0f})"
    else:
        rsi_sc = 0;  rsi_lbl = f"❌ RSI:{rsi_cur:.0f} (범위외)"

    # ══════════════════════════════════════════════════════════════════
    # 4단계: MACD 점수화 (25점)
    # 조건: MACD > Signal + 히스토그램 전일 대비 증가
    # ══════════════════════════════════════════════════════════════════
    macd_l, sig_l, hist_l = macd_calc(closes)
    macd_v = [v for v in macd_l  if v is not None]
    sig_v  = [v for v in sig_l   if v is not None]
    hist_v = [v for v in hist_l  if v is not None]

    macd_cur  = macd_v[-1]           if macd_v  else 0
    macd_prev = macd_v[-2]           if len(macd_v) >= 2  else macd_cur
    sig_cur   = sig_v[-1]            if sig_v   else 0
    sig_prev  = sig_v[-2]            if len(sig_v)  >= 2  else sig_cur
    hist_cur  = hist_v[-1]           if hist_v  else 0
    hist_prev = hist_v[-2]           if len(hist_v) >= 2  else hist_cur

    golden_cross  = (macd_prev < sig_prev) and (macd_cur >= sig_cur)  # 이번에 골든크로스
    macd_above    = macd_cur > sig_cur                                  # 이미 골든크로스 상태
    hist_rising   = hist_cur > hist_prev                                # 히스토그램 증가
    hist_turning  = hist_cur < 0 and hist_rising                        # 음수에서 전환
    macd_above_0  = macd_cur > 0                                        # 0선 위

    req_cross = p("macd_require_cross")

    macd_sc = 0
    if golden_cross and hist_rising:
        macd_sc = 25; macd_lbl = "✅ 골든크로스+히스토↑ (최강)"
    elif golden_cross:
        macd_sc = 20; macd_lbl = "✅ 당일 골든크로스"
    elif macd_above and hist_rising and macd_above_0:
        macd_sc = 25; macd_lbl = "✅ 골든+히스토↑+0선위"
    elif macd_above and hist_rising:
        macd_sc = 20; macd_lbl = "✅ 골든크로스 유지+히스토↑"
    elif macd_above:
        macd_sc = 12; macd_lbl = "🟡 골든크로스 유지"
    elif hist_rising:
        macd_sc = 10 if not req_cross else 0
        macd_lbl = f"{'🟡' if not req_cross else '❌'} 히스토그램↑(크로스 전)"
    elif hist_turning:
        macd_sc = 8 if not req_cross else 0
        macd_lbl = f"{'🟡' if not req_cross else '❌'} 히스토 전환중"
    else:
        macd_sc = 0; macd_lbl = "❌ MACD 방향 부정적"

    # ══════════════════════════════════════════════════════════════════
    # 5단계: 볼린저밴드 점수화 (20점)
    # 조건: 종가 > 중심선, 종가 < 상단밴드, 상단밴드 0~5% 이내
    # ══════════════════════════════════════════════════════════════════
    bb_period = int(p("bb_period") or 20)
    bb_std    = float(p("bb_std")  or 2.0)
    bb_u_v, bb_m_v, bb_l_v = bollinger(closes, bb_period, bb_std)
    bb_upper = _last(bb_u_v, cur)
    bb_mid   = _last(bb_m_v, cur)
    bb_lower = _last(bb_l_v, cur)
    bw       = (bb_upper - bb_lower) / max(bb_mid, 1) * 100
    bb_pct   = (cur - bb_lower) / max(bb_upper - bb_lower, 0.001) * 100  # 0=하단 100=상단
    to_upper = (bb_upper - cur) / max(bb_upper, 1) * 100  # 상단까지 거리 %

    # 이전 볼린저 (중심선 재돌파 확인)
    bb_m_prev = next((x for i,x in enumerate(reversed(bb_m_v)) if x is not None and i>=1), bb_mid)
    mid_cross = (closes[-2] < bb_m_prev) and (closes[-1] >= bb_mid) if len(closes) >= 2 else False

    bb_sc = 0
    if mid_cross:
        bb_sc = 20; bb_lbl = f"✅ BB 중심선 재돌파({bb_pct:.0f}%)"
    elif 50 <= bb_pct <= 90 and to_upper <= 5:
        bb_sc = 20; bb_lbl = f"✅ 중심선위+상단근접({to_upper:.1f}%)"
    elif above_ma20 and bb_pct >= 50:
        bb_sc = 15; bb_lbl = f"✅ 중심선 위({bb_pct:.0f}%)"
    elif 30 <= bb_pct < 50:
        bb_sc = 8;  bb_lbl = f"🟡 중심선 근접({bb_pct:.0f}%)"
    elif bb_pct < 20:
        # 하단 근처 — 반등 신호 (과매도 반등형)
        bb_sc = 12; bb_lbl = f"⚡ BB하단근접({bb_pct:.0f}%)"
    else:
        bb_sc = 5;  bb_lbl = f"—({bb_pct:.0f}%)"

    # ══════════════════════════════════════════════════════════════════
    # 6단계: 거래량/거래대금 점수화 (10점)
    # 조건: 거래량 > 20일 평균 × 1.2, 거래대금 30억 이상
    # ══════════════════════════════════════════════════════════════════
    vol20_avg = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 0
    vol_today = volumes[-1] if volumes else 0
    vol_ratio = vol_today / max(vol20_avg, 1) if vol20_avg > 0 else 1.0
    tv_val    = cur * vol_today
    tv_b      = tv_val / 1e8 if is_kr else tv_val / 1e6

    min_vr_buy = float(p("min_vol_ratio_buy") or 1.2)
    min_tv_b   = float(p("min_trading_value_rmb") or 30)  # 30억 기본

    vol_ok = vol_ratio >= min_vr_buy
    tv_ok  = tv_b >= min_tv_b if is_kr else tv_b >= (min_tv_b / 30)

    vol_sc = 0
    if vol_ratio >= 2.0 and tv_ok:
        vol_sc = 10; vol_lbl = f"✅ 거래량폭발({vol_ratio:.1f}배)+거래대금OK"
    elif vol_ratio >= 1.5:
        vol_sc = 8;  vol_lbl = f"✅ 거래량증가({vol_ratio:.1f}배)"
    elif vol_ok:
        vol_sc = 6;  vol_lbl = f"🟡 거래량충분({vol_ratio:.1f}배)"
    elif vol_ratio >= 1.0:
        vol_sc = 3;  vol_lbl = f"— 거래량보통({vol_ratio:.1f}배)"
    else:
        vol_sc = 0;  vol_lbl = f"❌ 거래량부족({vol_ratio:.1f}배)"

    # ══════════════════════════════════════════════════════════════════
    # 7단계: 보너스 (최근 60일 신고가 근접도 + 완전 3조건 달성)
    # 문서: "최근 60일 신고가 대비 -15% 이내 → 너무 약한 종목 제외"
    # ══════════════════════════════════════════════════════════════════
    hi60 = max(closes[-min(60, n):])
    from_hi60 = (cur - hi60) / max(hi60, 1) * 100
    hi60_ok = from_hi60 >= -15   # -15% 이내

    if not hi60_ok:
        return 0.0, {
            "_filtered": True,
            "탈락(고점근접)": f"최근 60일 고점 대비 {from_hi60:.1f}% (기준: -15% 이내)",
            "설명": "너무 약한 종목 제외. 강한 종목이 눌림목 후 재상승하는 자리를 찾는 전략",
            "데이터": "🟢 실시간" if ind.get("realtime") else "🟡 샘플",
        }

    hi60_bonus = 0
    if from_hi60 >= -3:    hi60_bonus = 8; hi60_lbl = f"✅ 신고가 근접({from_hi60:.1f}%)"
    elif from_hi60 >= -7:  hi60_bonus = 5; hi60_lbl = f"✅ 고점 근처({from_hi60:.1f}%)"
    elif from_hi60 >= -15: hi60_bonus = 2; hi60_lbl = f"🟡 고점 허용범위({from_hi60:.1f}%)"
    else:                  hi60_bonus = 0; hi60_lbl = f"—"

    # MA200 위 보너스
    ma200_bonus = 0
    if above_ma200:
        ma200_bonus = 5    # 장기 상승 추세 = 반전 신뢰도 상승

    # 완전 3조건 달성 보너스 (RSI✅ + MACD✅ + BB✅)
    full3 = (rsi_sc >= 13) and (macd_sc >= 12) and (bb_sc >= 12)
    full3_bonus = 10 if full3 else 0

    # ══════════════════════════════════════════════════════════════════
    # 최종 점수 합산 및 최소 통과 기준
    # ══════════════════════════════════════════════════════════════════
    # 3지표 중 최소 2개 조건 충족 필요
    three_conds = sum([rsi_sc >= 8, macd_sc >= 8, bb_sc >= 8])
    if three_conds < 2:
        return 0.0, {
            "_filtered": True,
            "탈락": f"3지표 중 {three_conds}개 충족 (최소 2개 필요)",
            "RSI":   rsi_lbl,
            "MACD":  macd_lbl,
            "BB":    bb_lbl,
            "데이터": "🟢 실시간" if ind.get("realtime") else "🟡 샘플",
        }

    rank_val = (trend_sc + rsi_sc + macd_sc + bb_sc + vol_sc
                + hi60_bonus + ma200_bonus + full3_bonus)

    # ── 결과 상세 ────────────────────────────────────────────────────
    tv_str = f"₩{tv_b:.0f}억" if is_kr else f"${tv_b:.1f}M"
    detail = {
        "━━ 기본 추세 필터": f"({trend_sc}/25점)",
        "MA20>MA60":    f"{'✅' if trend_up    else '❌'} MA20:{pf(ma20)} > MA60:{pf(ma60)}",
        "현재가>MA20":  f"{'✅' if above_ma20  else '❌'} {pf(cur)} > {pf(ma20)}",
        "MA200 위치":   f"{'✅ 위' if above_ma200 else '❌ 아래'} ({ma200_gap:+.1f}%)",
        "━━ RSI":       f"({rsi_sc}/20점) {rsi_lbl}",
        "RSI(14) 현재": f"{rsi_cur:.1f} (기준: {rsi_low:.0f}~{rsi_high:.0f})  전일:{rsi_prev:.1f}",
        "RSI 추이":     f"{'↑상승' if rsi_rising else '↓하락'}  50 돌파:{'✅' if rsi_50cross else '—'}",
        "━━ MACD":      f"({macd_sc}/25점) {macd_lbl}",
        "MACD vs 시그널":f"{'골든' if macd_above else '데드'} ({macd_cur:+.4f} vs {sig_cur:+.4f})",
        "히스토그램":   f"{'↑증가' if hist_rising else '↓감소'} ({hist_cur:+.4f})  0선:{'위' if macd_above_0 else '아래'}",
        "━━ 볼린저밴드": f"({bb_sc}/20점) {bb_lbl}",
        "BB 위치":      f"{bb_pct:.0f}% (중심:{pf(bb_mid)}  상단:{pf(bb_upper)}  하단:{pf(bb_lower)})",
        "밴드폭(BW)":   f"{bw:.1f}% ({'수축' if bw<15 else '보통' if bw<25 else '확장'})  상단까지:{to_upper:.1f}%",
        "━━ 거래량":    f"({vol_sc}/10점) {vol_lbl}",
        "거래대금":     f"{tv_str}/일  {'✅' if tv_ok else '❌'}",
        "━━ 보너스":    f"+{hi60_bonus+ma200_bonus+full3_bonus}점",
        "60일 고점 근접":f"{hi60_lbl}",
        "MA200 위":     f"{'✅ +5점' if above_ma200 else '—'}",
        "3지표 완전매칭":f"{'✅ +10점' if full3 else '—'}",
        "━━ 최종 점수": f"▶ {rank_val:.1f}점",
        "현재가":       pf(cur),
        "데이터":       "🟢 실시간" if ind.get("realtime") else "🟡 샘플",
    }
    return rank_val, detail



# ═══════════════════════════════════════════════════════════
# 7. 스윙 투자 — BB수축 에너지 응축 후 돌파
# ═══════════════════════════════════════════════════════════
def score_swing(name, ticker):
    """
    재무 방어 + 정배열 추세 + BB수축(에너지응축) + 거래량바닥.
    RSI 45~65 = 과매수·과매도 아닌 에너지 충전 구간.
    MACD 골든크로스 = 단기 반등 신호.
    """
    p = lambda k: get_param("스윙 투자", k)
    try:
        ind     = _get_indicators(ticker)
        fi      = _get_financial_indicators(ticker)
        d       = fetch_realtime(ticker)
        closes  = d.get("closes",  [])
        volumes = d.get("volumes", [])
    except Exception:
        return 0.0, {"_filtered": True, "탈락": "데이터 오류"}

    # ── 공통 품질 필터 ──
    _qok, _qreason = _apply_quality_filters(ind, fi, ticker, "스윙 투자")
    if not _qok:
        return 0.0, {"_filtered": True, "탈락": _qreason,
                     "데이터": "🟢 실시간" if ind.get("realtime") else "🟡 샘플"}

    cur  = _g(ind, "cur")
    rsi  = _g(ind, "rsi", 50)
    p52h = _g(ind, "prev52h", cur or 1)
    from_52h = (cur - p52h) / max(p52h, 1) * 100

    roe  = fi.get("roe")        if fi else None
    debt = fi.get("debt_ratio") if fi else None
    pbr  = fi.get("pbr")        if fi else None

    min_roe   = float(p("min_roe")          or 0)
    max_debt  = float(p("max_debt_ratio")   or 300)
    max_pbr   = float(p("max_pbr")          or 10)
    max_52h   = float(p("max_from_52h_pct") or 25)
    rsi_min   = float(p("rsi_min")          or 35)
    rsi_max   = float(p("rsi_max")          or 70)
    bb_pct    = float(p("bb_pct_rank")      or 30)
    vdu_r     = float(p("vdu_ratio")        or 0.7)

    # 재무 — 없으면 통과
    roe_ok  = (roe  is None) or (min_roe <= 0) or (roe  >= min_roe)
    debt_ok = (debt is None) or (debt <= max_debt)
    pbr_ok  = (pbr  is None) or (max_pbr <= 0) or (pbr <= max_pbr)

    # 이평선 정배열
    ma20_v  = sma(closes, 20); ma60_v = sma(closes, 60); ma120_v = sma(closes, 120)
    ma20  = next((v for v in reversed(ma20_v)  if v is not None), 0)
    ma60  = next((v for v in reversed(ma60_v)  if v is not None), 0)
    ma120 = next((v for v in reversed(ma120_v) if v is not None), 0)
    trend_ok = (cur > ma20 > ma60 > ma120) if (ma20 > 0 and ma60 > 0 and ma120 > 0) else True

    # ── 이평선 정배열 하드 필터 (스윙투자 핵심) ─────────────────────
    # MA20>MA60>MA120 = 상승 추세 배경 필수 (BB수축 에너지는 상승 추세 안에서만 의미)
    req_ma_align = p("require_ma_align")
    if req_ma_align and not trend_ok and (ma20 > 0 and ma60 > 0 and ma120 > 0):
        return 0.0, {
            "_filtered": True,
            "탈락(이평선 정배열)": f"MA20({_pfmt_sw(ma20)}) > MA60({_pfmt_sw(ma60)}) > MA120({_pfmt_sw(ma120)}) 미충족",
            "설명": "스윙투자: BB수축 에너지는 상승 추세 안에서만 유효. 역배열 종목 제외",
            "데이터": "🟢 실시간" if ind.get("realtime") else "🟡 샘플",
        }

    # 볼린저밴드 수축
    bb_u, bb_m, bb_l = bollinger(closes, 20, 2)
    bw = [(u - l) / m * 100 for u, m, l in zip(bb_u, bb_m, bb_l)
          if u is not None and m is not None and m > 0 and l is not None]
    cur_bw = bw[-1] if bw else 999
    if len(bw) >= 10:
        threshold = sorted(bw)[max(0, int(len(bw) * bb_pct / 100) - 1)]
        bb_ok = cur_bw <= threshold
    else:
        bb_ok = True   # 데이터 부족 → 통과

    # RSI 구간
    rsi_ok = rsi_min <= rsi <= rsi_max

    # MACD — 골든크로스 OR 히스토그램 증가 중 하나면 OK
    macd_l, sig_l, hist_l2 = macd_calc(closes)
    mv = [v for v in macd_l  if v is not None]
    sv = [v for v in sig_l   if v is not None]
    hv = [v for v in hist_l2 if v is not None]
    macd_golden  = len(mv)>=1 and len(sv)>=1 and mv[-1] > sv[-1]
    hist_rising2 = len(hv)>=2 and hv[-1] > hv[-2]
    macd_ok = macd_golden or hist_rising2  # 둘 중 하나면 OK

    # 거래량 바닥 OR 최근 거래량 증가 중
    if len(volumes) >= 20:
        avg20 = sum(volumes[-20:]) / 20
        avg3  = sum(volumes[-3:]) / 3 if len(volumes) >= 3 else avg20
        vol_ok   = avg20 > 0 and (avg3 / avg20) <= vdu_r          # 바닥 (감소)
        vol_surge= avg20 > 0 and (avg3 / avg20) >= 1.2            # 급증 (돌파 후)
        vol_pass = vol_ok or vol_surge                             # 둘 중 하나면 OK
    else:
        vol_ok = vol_surge = vol_pass = True

    # 필수 필터 (탈락 조건)
    filters = {
        f"F1 ROE≥{min_roe:.0f}%":        roe_ok,
        f"F2 부채비율≤{max_debt:.0f}%":   debt_ok,
        f"F3 PBR≤{max_pbr:.1f}":          pbr_ok,
        f"F5 52주고점-{max_52h:.0f}%이내": from_52h >= -max_52h,
        f"F7 RSI {rsi_min:.0f}~{rsi_max:.0f}": rsi_ok,
    }
    # BB수축, MACD, 거래량, 정배열은 둘 중 2개 이상 충족
    bonus_cnt = sum([bb_ok, macd_ok, vol_pass, trend_ok])
    if bonus_cnt < 2:
        filters[f"F6/F8/F9 기술조건(BB/MACD/거래량) 2개이상"] = False

    failed = [k for k, v in filters.items() if not v]
    if failed:
        return 0.0, {"_filtered": True,
                     "탈락": " / ".join(f[:30] for f in failed),
                     "BB수축": f"{'✅' if bb_ok else '❌'} {cur_bw:.1f}%",
                     "MACD": f"{'✅' if macd_ok else '❌'} (GX:{'✅' if macd_golden else '❌'} Hist:{'↑' if hist_rising2 else '↓'})",
                     "거래량": f"{'✅' if vol_pass else '❌'} (바닥:{'✅' if vol_ok else '❌'} 급증:{'✅' if vol_surge else '❌'})",
                     "데이터": "🟢 실시간" if ind.get("realtime") else "🟡 샘플"}

    # ── 다차원 점수화 시스템 (합계 100점 만점) ──────────────────────
    # [A] BB수축 점수 (30점) — 현재 BW가 최근 120봉 중 얼마나 좁은지
    bw_all = bw[-120:] if len(bw) >= 120 else bw
    if len(bw_all) >= 5:
        bw_rank = sorted(bw_all).index(
            min(bw_all, key=lambda x: abs(x - cur_bw))
        ) / max(len(bw_all) - 1, 1)   # 0=최저, 1=최고
        bb_score = (1 - bw_rank) * 30   # 좁을수록 높은 점수
    else:
        bb_score = 15  # 데이터 부족 → 기본값

    # [B] RSI 점수 (25점) — RSI 45~55 중심, 극단으로 갈수록 감점
    rsi_ideal = 50
    rsi_deviation = abs(rsi - rsi_ideal)
    rsi_score = max(0, 25 - rsi_deviation * 0.6)

    # [C] MACD 점수 (25점)
    if macd_golden and hist_rising2:
        macd_score = 25   # 골든크로스 + 히스토그램 증가
    elif macd_golden:
        macd_score = 20
    elif hist_rising2:
        macd_score = 15
    else:
        macd_score = 5

    # [D] 거래량 점수 (10점)
    if vol_ok:
        vol_score = 10   # 바닥 = 매도 압력 소진
    elif vol_surge:
        vol_score = 8    # 급증 = 돌파 시작
    else:
        vol_score = 3

    # [E] 추세·재무 보너스 (10점)
    bonus = 0
    if trend_ok:    bonus += 5  # 이평선 정배열
    if roe and roe >= (float(p("min_roe") or 0)):  bonus += 3
    if from_52h >= -15: bonus += 2  # 52주 고점 -15% 이내 (신고가 근처)

    score = bb_score + rsi_score + macd_score + vol_score + bonus

    _pfmt_sw = (lambda v: f"₩{v:,.0f}") if _is_kr(ticker) else (lambda v: f"${v:,.2f}")
    detail = {
        "━━ 스윙 투자 전체통과": f"기술조건 {bonus_cnt}/4 충족",
        "F1 ROE":         f"✅ {roe:.1f}%" if roe else "✅(없음-통과)",
        "F2 부채비율":    f"✅ {debt:.0f}%" if debt else "✅(없음-통과)",
        "F3 PBR":         f"✅ {pbr:.2f}" if pbr else "✅(없음-통과)",
        "F4 정배열(20>60>120)": f"{'✅' if trend_ok else '—'} {_pfmt_sw(cur)}>{_pfmt_sw(ma20)}>{_pfmt_sw(ma60)}" if ma20>0 else "—(없음-통과)",
        "F5 52주고점":    f"✅ {from_52h:.1f}%",
        f"F6 BB수축(BW)": f"{'✅' if bb_ok else '—'} {cur_bw:.1f}%  ({int((1-bw_rank)*100) if 'bw_rank' in dir() else '?'}분위)",
        "F7 RSI":         f"✅ {rsi:.1f}  (이상: 50 중심, 범위 {rsi_min:.0f}~{rsi_max:.0f})",
        "F8 MACD":        f"{'✅' if macd_ok else '—'} {'골든크로스' if macd_golden else '히스토그램↑' if hist_rising2 else '—'}",
        "F9 거래량":      f"{'✅' if vol_pass else '—'} {'바닥(매도소진)' if vol_ok else '급증(돌파시작)' if vol_surge else '—'}",
        "━━ 점수 분해":  f"BB:{bb_score:.0f}+RSI:{rsi_score:.0f}+MACD:{macd_score:.0f}+거래량:{vol_score:.0f}+보너스:{bonus:.0f} = {score:.1f}",
        "데이터":         "🟢 실시간" if ind.get("realtime") else "🟡 샘플",
    }
    return score, detail


# ═══════════════════════════════════════════════════════════
# 전략 맵 & 스캐너
# ═══════════════════════════════════════════════════════════

def score_sw(name, ticker):
    """
    SW — 한국형 미너비니 실전 스캐너

    ══ 구조 ════════════════════════════════════════════════════
    1단계: 잡주 제거 필터 (거래대금·거래정지·관리종목)
    2단계: 미너비니 추세 템플릿 7조건 (Stage2 확인)
    3단계: 상대강도(RS) + 실적 점수화
    4단계: 매수 진입 트리거 (눌림목 반등 or 박스권 돌파)
    5단계: 리스크 평가 (ATR 손절폭)

    점수 100점 만점:
      추세 조건 30점 + RS 강도 20점 + 거래대금·유동성 15점
      실적 성장 15점 + 베이스 품질 15점 + 시장 적합도 5점
    """
    p = lambda k: get_param("SW", k)
    try:
        ind = _get_indicators(ticker)
        fi  = _get_financial_indicators(ticker)
        d   = fetch_realtime(ticker)
        closes  = d.get("closes",  [])
        highs   = d.get("highs",   [])
        lows    = d.get("lows",    [])
        volumes = d.get("volumes", [])
        opens   = d.get("opens",   [])
    except Exception:
        return 0.0, {"_filtered": True, "탈락": "데이터 오류"}

    if len(closes) < 120:
        return 0.0, {"_filtered": True, "탈락": f"데이터 부족 ({len(closes)}봉 < 120봉)"}

    # ── 거래정지 즉시 제외 ─────────────────────────────────────────
    try:
        from data_fetcher import is_trading_halted
        _h, _hr = is_trading_halted(d)
        if _h:
            return 0.0, {"_filtered": True, "탈락": f"거래정지: {_hr}"}
    except Exception:
        pass

    cur   = _g(ind, "cur", closes[-1])
    is_kr = _is_kr(ticker)
    pf    = (lambda v: f"₩{v:,.0f}") if is_kr else (lambda v: f"${v:.2f}")
    n     = len(closes)

    # ══════════════════════════════════════════════════════════════════
    # 1단계: 잡주 제거 필터 (C. 제외 필터)
    # ══════════════════════════════════════════════════════════════════
    # 거래대금 필터
    min_tv_kr = float(p("min_trading_value") or 50)   # 50억원 기본
    min_tv_us = float(p("min_trading_value_usd") or 1.0)
    vol20_avg = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 0
    tv_val    = vol20_avg * cur
    tv_ok = ((tv_val >= min_tv_kr * 1e8) if is_kr else (tv_val >= min_tv_us * 1e6))
    if not tv_ok:
        tv_str = f"₩{tv_val/1e8:.0f}억" if is_kr else f"${tv_val/1e6:.1f}M"
        return 0.0, {"_filtered": True,
                     "탈락": f"거래대금 부족 {tv_str} < {'₩'+str(int(min_tv_kr))+'억' if is_kr else '$'+str(min_tv_us)+'M'}"}

    # 스팩/우선주 이름 필터
    excl_kw = ["스팩", "SPAC", "우선주", "2우B", "3우B"]
    if any(kw in name for kw in excl_kw):
        return 0.0, {"_filtered": True, "탈락": f"제외 대상 종목 ({name})"}

    # ══════════════════════════════════════════════════════════════════
    # 2단계: 미너비니 추세 템플릿 7조건 (A. 장기 추세 필터)
    # ══════════════════════════════════════════════════════════════════
    ma50_v  = sma(closes, 50);  ma150_v = sma(closes, 150); ma200_v = sma(closes, 200)

    def _last(v, default=cur):
        return next((x for x in reversed(v) if x is not None), default)

    ma50  = _last(ma50_v);  ma150 = _last(ma150_v);  ma200 = _last(ma200_v)

    # MA200 20일 전값 (기울기 확인)
    ma200_20ago = next((x for i,x in enumerate(reversed(ma200_v))
                        if x is not None and i >= 20), ma200)
    ma200_rising = (ma200 > ma200_20ago * 0.998)

    hi52 = max(closes[-min(n,252):]) if n >= 5 else cur
    lo52 = min(closes[-min(n,252):]) if n >= 5 else cur
    from_hi = (cur - hi52) / max(hi52,1) * 100   # 52주 고점 대비 (음수)
    from_lo = (cur - lo52) / max(lo52,1) * 100   # 52주 저점 대비 (양수)

    has_ma = (ma50 > 0 and ma150 > 0 and ma200 > 0)

    trend_conds = {
        "T1 현재가>50일선":            (cur > ma50)   if ma50  > 0 else True,
        "T2 현재가>150일선":           (cur > ma150)  if ma150 > 0 else True,
        "T3 현재가>200일선":           (cur > ma200)  if ma200 > 0 else True,
        "T4 50>150>200 정배열":        (ma50>ma150>ma200) if has_ma else True,
        "T5 200일선 20일 상승":        ma200_rising,
        "T6 52주고점 -25% 이내":       from_hi >= -25,
        "T7 52주저점 +30% 이상":       from_lo >= 30,
    }

    failed_trend = [k for k,v in trend_conds.items() if not v]
    if failed_trend:
        return 0.0, {
            "_filtered": True,
            "탈락(2단계 추세)": " / ".join(f[:28] for f in failed_trend),
            "MA50":  pf(ma50), "MA150": pf(ma150), "MA200": pf(ma200),
            "52주고": f"{from_hi:.1f}%", "52주저": f"{from_lo:.1f}%",
            "데이터": "🟢 실시간" if ind.get("realtime") else "🟡 샘플",
        }

    # ══════════════════════════════════════════════════════════════════
    # 3단계: 점수화 (100점 만점)
    # ══════════════════════════════════════════════════════════════════
    score = 0.0

    # ── A. 추세 조건 점수 (30점) ─────────────────────────────────────
    trend_sc = 0
    # 이격도: MA50 위, MA150 위, MA200 위 각 5점
    if cur > ma50:   trend_sc += 5
    if cur > ma150:  trend_sc += 5
    if cur > ma200:  trend_sc += 5
    # MA 정배열: 50>150>200 완전 배열
    if has_ma and ma50 > ma150 > ma200:  trend_sc += 5
    # MA200 기울기 강도 (10점)
    ma200_slope_pct = (ma200 - ma200_20ago) / max(ma200_20ago, 1) * 100
    trend_sc += min(10, max(0, ma200_slope_pct * 10))
    # 52주 고점 근접도 (가까울수록 높은 점수)
    proximity = max(0, 25 + from_hi) / 25   # 0~1
    trend_sc += proximity * 5   # 최대 5점 (고점 근접)
    trend_sc = min(30, trend_sc)
    score += trend_sc

    # ── B. RS 강도 (20점) ────────────────────────────────────────────
    rs = _get_rs_with_percentile(ticker, ind)
    min_rs_pct = float(p("min_rs_pct") or 60)
    if rs < min_rs_pct:
        return 0.0, {
            "_filtered": True,
            "탈락(RS 미달)": f"RS {rs:.0f}% < 최소 {min_rs_pct:.0f}%",
            "설명": "상대강도 상위 40% 이내 종목만 선별",
            "데이터": "🟢 실시간" if ind.get("realtime") else "🟡 샘플",
        }
    rs_sc = (rs - min_rs_pct) / max(100 - min_rs_pct, 1) * 20
    rs_sc = min(20, rs_sc)
    score += rs_sc

    # ── C. 거래대금·유동성 (15점) ────────────────────────────────────
    tv_score = 0
    tv_b = tv_val / 1e8 if is_kr else tv_val / 1e6  # 억원 또는 M$
    baseline = min_tv_kr if is_kr else min_tv_us
    tv_ratio = tv_b / max(baseline, 1)
    if tv_ratio >= 5:    tv_score = 15
    elif tv_ratio >= 3:  tv_score = 12
    elif tv_ratio >= 2:  tv_score = 9
    elif tv_ratio >= 1:  tv_score = 6
    else:                tv_score = 3
    score += tv_score

    # ── D. 실적 성장 (15점) ──────────────────────────────────────────
    fi_score = 0
    eps_g  = fi.get("earn_growth") if fi else None
    rev_g  = fi.get("rev_growth")  if fi else None
    if eps_g is not None:
        if eps_g >= 25:   fi_score += 10
        elif eps_g >= 15: fi_score += 7
        elif eps_g >= 5:  fi_score += 4
        elif eps_g > 0:   fi_score += 2
    else:
        fi_score += 5   # 데이터 없으면 중립
    if rev_g is not None:
        if rev_g >= 20:   fi_score += 5
        elif rev_g >= 10: fi_score += 3
        elif rev_g > 0:   fi_score += 1
    else:
        fi_score += 3   # 중립
    fi_score = min(15, fi_score)
    score += fi_score

    # ── E. 베이스 품질 (15점): 눌림목·박스권·VCP ────────────────────
    base_score = 0
    base_labels = []

    # 최근 20~30일 박스권 고점
    box_range = min(30, n-1)
    recent_hi = max(closes[-box_range:])
    recent_lo = min(closes[-box_range:])
    box_pct   = (recent_hi - recent_lo) / max(recent_lo, 1) * 100
    at_pivot  = (recent_hi - cur) / max(recent_hi, 1) * 100  # 박스 고점과의 차이

    # 박스권 돌파 직전 (최근 고점 -3%~+1% 위치)
    near_breakout = (-3 <= at_pivot <= 1)

    # MA20/MA50 눌림목
    ma20_v   = sma(closes, 20)
    ma20     = _last(ma20_v)
    pullback_20  = (abs(cur - ma20)  / max(ma20,  1) * 100 <= 3.0)  # MA20 ±3%
    pullback_50  = (abs(cur - ma50)  / max(ma50,  1) * 100 <= 3.0)  # MA50 ±3%

    # 양봉 전환 확인 (오늘 양봉)
    today_bullish = (opens and len(opens) > 0 and closes[-1] > opens[-1])

    # 거래량 증가 (눌림목 반등 신호)
    vol_ratio = volumes[-1] / max(vol20_avg, 1) if volumes and vol20_avg > 0 else 1.0

    # ① 눌림목 반등 신호
    if (pullback_20 or pullback_50) and today_bullish and vol_ratio > 1.1:
        pullback_type = "MA20" if pullback_20 else "MA50"
        base_score += 12
        base_labels.append(f"눌림목({pullback_type})+양봉+거래량↑")
    elif pullback_20 or pullback_50:
        base_score += 6
        base_labels.append(f"눌림목({'MA20' if pullback_20 else 'MA50'}) 근접")

    # ② 박스권 돌파 직전
    if near_breakout and box_pct < 20:  # 좁은 박스권 + 고점 근처
        base_score += 8
        base_labels.append(f"박스권돌파직전({at_pivot:.1f}%)")
    elif near_breakout:
        base_score += 4
        base_labels.append(f"고점근접")

    # ③ 거래량 급증 돌파 (박스 고점 돌파)
    box_hi_break = (closes[-1] > recent_hi * 0.995 and vol_ratio >= 1.5)
    if box_hi_break:
        base_score += 10
        base_labels.append(f"박스돌파+거래량{vol_ratio:.1f}배")

    # ④ VCP 패턴 (간이 체크)
    if n >= 60 and highs and lows:
        def _rng(start, end):
            h = max((highs or closes)[start:end]); l = min((lows or closes)[start:end])
            return (h-l)/max((h+l)/2, 1)*100
        r1=_rng(-60,-40); r2=_rng(-40,-20); r3=_rng(-20,None)
        if r1>r2*0.9 and r2>r3*0.9 and r3<r1*0.8 and r3<20:
            base_score += 8
            base_labels.append(f"VCP({r1:.0f}%→{r2:.0f}%→{r3:.0f}%)")

    base_score = min(15, base_score)
    score += base_score

    # ── F. 시장 적합도 (5점) ─────────────────────────────────────────
    mkt_score = 3  # 기본 3점
    mom3m = (closes[-1]-closes[-min(63,n-1)]) / max(closes[-min(63,n-1)], 1)*100 if n >= 5 else 0
    if mom3m >= 15:   mkt_score = 5
    elif mom3m >= 5:  mkt_score = 4
    elif mom3m < -10: mkt_score = 1
    score += mkt_score

    # ══════════════════════════════════════════════════════════════════
    # 4단계: 매수 진입 트리거 확인 (타이밍 레이블)
    # ══════════════════════════════════════════════════════════════════
    trigger_label = "대기"
    trigger_detail = ""

    if box_hi_break:
        trigger_label = "🚀 박스권 돌파"
        trigger_detail = f"20일고점({pf(recent_hi)}) 돌파 + 거래량{vol_ratio:.1f}배"
    elif near_breakout and box_pct < 15:
        trigger_label = "⏳ 돌파 직전"
        trigger_detail = f"박스상단 {abs(at_pivot):.1f}% 이내 (BW:{box_pct:.1f}%)"
    elif pullback_20 and today_bullish:
        trigger_label = "📍 MA20 눌림목"
        trigger_detail = f"MA20({pf(ma20)}) 근접 양봉 전환"
    elif pullback_50 and today_bullish:
        trigger_label = "📍 MA50 눌림목"
        trigger_detail = f"MA50({pf(ma50)}) 근접 양봉 전환"
    elif near_breakout:
        trigger_label = "🔍 고점 모니터링"
        trigger_detail = f"최근 고점({pf(recent_hi)}) {abs(at_pivot):.1f}% 아래"

    # ── 5단계: 리스크 평가 (ATR 손절폭) ─────────────────────────────
    if n >= 15 and highs and lows:
        atr_list = []
        for i in range(1, min(15, n)):
            tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
            atr_list.append(tr)
        atr14 = sum(atr_list) / len(atr_list) if atr_list else cur * 0.02
        stop_price = cur - 1.5 * atr14
        stop_pct   = (stop_price - cur) / cur * 100
        atr_ok     = abs(stop_pct) <= 8.0   # 손절폭 -8% 이내
    else:
        atr14 = cur * 0.02; stop_price = cur*0.95; stop_pct=-5.0; atr_ok=True

    if not atr_ok:
        return 0.0, {
            "_filtered": True,
            "탈락(리스크)": f"손절폭 {stop_pct:.1f}% > -8% (ATR기준)",
            "ATR(14)": pf(atr14),
            "데이터": "🟢 실시간" if ind.get("realtime") else "🟡 샘플",
        }

    # ── 최종 상세 ────────────────────────────────────────────────────
    tv_str = f"₩{tv_b:.0f}억/일" if is_kr else f"${tv_b:.1f}M/일"
    detail = {
        "━━ 1단계 잡주 제거": "✅ 통과",
        "거래대금(20일평균)": f"✅ {tv_str}",
        "━━ 2단계 추세 템플릿": "✅ 7조건 통과",
        f"MA50({pf(ma50)})":  f"✅ 현재가 {pf(cur)} > MA50",
        f"MA150({pf(ma150)})":f"✅ 현재가 > MA150",
        f"MA200({pf(ma200)})":f"✅ 현재가 > MA200  기울기:{ma200_slope_pct:+.2f}%",
        "MA 정배열":         f"✅ 50>150>200",
        "52주 포지션":       f"고점{from_hi:.1f}%  저점+{from_lo:.1f}%",
        "━━ 3단계 점수화":   f"합계 {score:.1f}/100점",
        "추세 조건":         f"{trend_sc:.0f}/30점  (RS:{rs:.0f}%→{rs_sc:.0f}점)",
        "거래대금·유동성":   f"{tv_score}/15점",
        "실적 성장":         (f"{fi_score}/15점  EPS:{eps_g:+.0f}%  매출:{rev_g:+.0f}%"
                              if (eps_g is not None and rev_g is not None)
                              else f"{fi_score}/15점 (실적 데이터 부족)"),
        "베이스 품질":       f"{base_score}/15점  {' / '.join(base_labels) if base_labels else '패턴 없음'}",
        "시장 적합도":       f"{mkt_score}/5점  3개월:{mom3m:+.1f}%",
        "━━ 4단계 매수 트리거": trigger_label,
        "진입 신호":         trigger_detail or "—",
        "3개월 수익률":      f"{mom3m:+.1f}%",
        "거래량 배율":       f"{vol_ratio:.1f}배",
        "━━ 리스크":         f"ATR({pf(atr14)})  손절가:{pf(stop_price)} ({stop_pct:.1f}%)",
        "데이터":            "🟢 실시간" if ind.get("realtime") else "🟡 샘플",
    }
    return score, detail



STRATEGY_SCORE = {
    "조엘 그린블라트": score_greenblatt,
    "미너비니":       score_minervini,
    "윌리엄오닐":     score_oneil,
    "쿨라매기":       score_kullamagi,
    "오닐+미너비니":  score_oneil_minervini,
    "RSI+MACD+BB":    score_rmb,
    "스윙 투자":      score_swing,
    "SW":            score_sw,
}
STRATEGY_DESC = {
    "조엘 그린블라트": "마법공식  ROC+EY 합산등수 — 저평가 우량주 장기보유",
    "미너비니":       "SEPA  트렌드템플릿+VCP — Stage2 주도주 돌파",
    "윌리엄오닐":     "CAN SLIM  실적+수급+기술 동시충족 — 성장주",
    "쿨라매기":       "모멘텀 주도주  3개월강상승+정배열+횡보돌파",
    "오닐+미너비니":  "CAN SLIM × VCP 교집합 — 최고 선별기준",
    "RSI+MACD+BB":    "BB하단+RSI과매도+MACD골든  3지표 합치 스윙",
    "스윙 투자":      "BB수축 에너지응축 후 돌파 — 2일~2주 보유",
    "SW":            "한국형 미너비니  잡주제거→추세템플릿→RS→베이스→돌파 4단계 스캐너",
}
STRATEGY_THRESHOLD = {k: 0 for k in STRATEGY_SCORE}



def prefetch_us_batch(tickers, workers=10):
    """미국 주식 yfinance 배치 프리패치."""
    if not HAS_YF: return
    missing = [(n, t) for n, t in tickers if t not in _indicator_cache]
    if not missing: return
    try:
        syms = [t for _, t in missing[:50]]
        end  = datetime.today()
        start = end - timedelta(days=400)
        raw = yf.download(syms, start=start.strftime("%Y-%m-%d"),
                          end=end.strftime("%Y-%m-%d"),
                          group_by="ticker", auto_adjust=True,
                          progress=False, threads=True)
        if raw is None or raw.empty: return
        for sym in syms:
            try:
                df = raw if len(syms) == 1 else (raw[sym] if sym in raw.columns.get_level_values(0) else None)
                if df is None or df.empty: continue
                df = df.dropna(subset=["Close"])
                if len(df) < 50: continue
                _indicator_cache[f"__raw_{sym}"] = dict(
                    dates=[d.to_pydatetime() for d in df.index],
                    opens=df["Open"].tolist(), highs=df["High"].tolist(),
                    lows=df["Low"].tolist(), closes=df["Close"].tolist(),
                    volumes=df["Volume"].fillna(0).astype(int).tolist(),
                    realtime=True, source="yfinance_batch")
            except Exception: continue
    except Exception: pass


def find_stocks_ranked(strategy, tickers, top_n=30, progress_cb=None,
                       workers=PARALLEL_WORKERS, use_cache=False, stop_event=None):
    """병렬 스캔 → 필터 통과 종목 정규화 점수 반환.
    v8 개선:
      · 스캔 전 RS 원점수 전계산 → 백분위로 변환 → _rs_percentile_cache에 저장
      · 각 전략에서 _get_rs_with_percentile() 호출 시 정확한 RS 사용
    """
    global _rs_percentile_cache

    if strategy == "조엘 그린블라트":
        return find_greenblatt_ranked(tickers, top_n=top_n,
                                      progress_cb=progress_cb, workers=workers,
                                      use_cache=use_cache, stop_event=stop_event)
    if not use_cache:
        _indicator_cache.clear()
        _rs_percentile_cache.clear()

    # ── RS 백분위 사전 계산 (전종목) ──────────────────────────────────
    # 첫 번째 패스: 모든 종목의 RS 원점수 계산
    if not _rs_percentile_cache and len(tickers) > 10:
        rs_raw_dict = {}
        def _calc_rs_one(args):
            nm, tk = args
            try:
                ind = _get_indicators(tk)
                return tk, ind.get("rs_raw", calc_rs_score(ind.get("closes", [])))
            except Exception:
                return tk, 0.0
        with ThreadPoolExecutor(max_workers=min(workers, 20)) as ex:
            for tk, raw in ex.map(_calc_rs_one, tickers):
                rs_raw_dict[tk] = raw
        _rs_percentile_cache = calc_rs_percentile(rs_raw_dict)

    func = STRATEGY_SCORE[strategy]
    total = len(tickers); scored = []; done = [0]; lock = threading.Lock()

    def _one(args):
        if stop_event and stop_event.is_set(): return None
        name, ticker = args
        try:    sc, det = func(name, ticker)
        except Exception as e: sc, det = 0.0, {"_filtered": True, "오류": str(e)}
        return (name, ticker, sc, det)

    def _cb(res):
        if res is None: return
        with lock:
            if not res[3].get("_filtered", False):
                scored.append(res)
            done[0] += 1
            if progress_cb and (done[0] % 10 == 0 or done[0] == total):
                progress_cb(done[0], total)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_one, t): t for t in tickers}
        for fut in as_completed(futs):
            if stop_event and stop_event.is_set():
                [f.cancel() for f in futs]; break
            try: _cb(fut.result())
            except:
                with lock: done[0] += 1

    if not scored: return []
    scored.sort(key=lambda x: x[2], reverse=True)
    mx = scored[0][2]; mn = scored[-1][2]; sp = max(mx - mn, 0.001)
    result = []
    for name, ticker, raw_s, det in scored[:top_n]:
        norm = round((raw_s - mn) / sp * 100, 2)
        det["━━ 필터통과"] = f"{len(scored):,}종목 / {total:,}종목 분석"
        det["상대순위점수"] = f"{norm:.1f}점"
        result.append((name, ticker, norm, det))
    return result


# ═══════════════════════════════════════════════════════════
# 8. SW (Swing Wave) — 모멘텀 합치 + 눌림목 진입
# ═══════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════
# 9. 시부야 다카오 — 5요소 합치 전략
# ═══════════════════════════════════════════════════════════
def score_shibuya(name, ticker):
    """
    시부야 다카오 — 한국시장 실전 완성판

    ══ 핵심 철학 ════════════════════════════════════════════════════════
    "정배열 + 박스권 압축 + 거래량 동반 돌파 + 강한 캔들" 종목을 점수화
    브레이크아웃 즉시 매수형 + 돌파 후 눌림목 매수형 2개 시나리오 탐지

    ══ 구조 ════════════════════════════════════════════════════════════
    1단계: 잡주 제거 (스팩·ETF·우선주·저유동성)
    2단계: 추세 필터 (MA20>MA60>MA120, 현재가>MA20, 60일선 상승)
    3단계: 5요소 점수화 (이평선·돌파구조·거래량·캔들강도·추세선)
    4단계: 매수 시나리오 판별 (브레이크아웃 즉시형 or 눌림목형)
    5단계: 손절/익절 기준 자동 산출

    ══ 점수 100점 만점 ══════════════════════════════════════════════════
    추세    (30점): MA20>MA60>MA120 정배열 + 60일선 상승 + 현재가>MA20
    돌파구조(30점): 최근 20일/60일 고점 돌파 + 박스권 압축
    거래량  (20점): 1.8배↑ + 거래대금 100억↑
    캔들강도(20점): 장대양봉·윗꼬리짧음·종가강함 + 반전캔들
    """
    p = lambda k: get_param("시부야 다카오", k)
    try:
        ind    = _get_indicators(ticker)
        fi     = _get_financial_indicators(ticker)
        d      = fetch_realtime(ticker)
        closes = d.get("closes",  [])
        opens  = d.get("opens",   [])
        highs  = d.get("highs",   [])
        lows   = d.get("lows",    [])
        volumes= d.get("volumes", [])
    except Exception:
        return 0.0, {"_filtered": True, "탈락": "데이터 오류"}

    if len(closes) < 60:
        return 0.0, {"_filtered": True, "탈락": f"데이터 부족 ({len(closes)}봉 < 60봉)"}

    # 거래정지 즉시 제외
    try:
        from data_fetcher import is_trading_halted
        _h, _hr = is_trading_halted(d)
        if _h:
            return 0.0, {"_filtered": True, "탈락": f"거래정지: {_hr}"}
    except Exception:
        pass

    cur   = closes[-1]
    n     = len(closes)
    is_kr = _is_kr(ticker)
    pf    = (lambda v: f"₩{v:,.0f}") if is_kr else (lambda v: f"${v:.2f}")

    # ══════════════════════════════════════════════════════════════════
    # 1단계: 잡주 제거
    # ══════════════════════════════════════════════════════════════════
    excl_kw = ["스팩","SPAC","ETF","ETN"]
    if any(kw in name for kw in excl_kw):
        return 0.0, {"_filtered": True, "탈락": f"제외 대상: {name}"}

    # 공통 품질 필터 (거래대금 최소 기준)
    _qok, _qreason = _apply_quality_filters(ind, fi, ticker, "시부야 다카오")
    if not _qok:
        return 0.0, {"_filtered": True, "탈락": _qreason,
                     "데이터": "🟢 실시간" if ind.get("realtime") else "🟡 샘플"}

    # 최소 가격
    min_price = float(p("min_price") or (3000 if is_kr else 3.0))
    if cur < min_price:
        return 0.0, {"_filtered": True, "탈락": f"저가주 ({pf(cur)} < {pf(min_price)})"}

    # ══════════════════════════════════════════════════════════════════
    # 2단계: 추세 필터 — 핵심 (MA20>MA60>MA120, 60일선 상승)
    # ══════════════════════════════════════════════════════════════════
    ma5_v   = sma(closes, 5)
    ma20_v  = sma(closes, 20)
    ma60_v  = sma(closes, 60)
    ma120_v = sma(closes, 120)

    def _last(v, default=cur):
        return next((x for x in reversed(v) if x is not None), default)
    def _nth(v, n_back, default=None):
        cnt = 0
        for x in reversed(v):
            if x is not None:
                cnt += 1
                if cnt == n_back: return x
        return default

    ma5   = _last(ma5_v)
    ma20  = _last(ma20_v)
    ma60  = _last(ma60_v)
    ma120 = _last(ma120_v)
    ma60_prev  = _nth(ma60_v,  10, ma60)
    ma120_prev = _nth(ma120_v, 10, ma120)

    trend_ok   = (ma20 > ma60 > ma120) if (ma20 > 0 and ma60 > 0 and ma120 > 0) else True
    above_ma20 = (cur > ma20)           if ma20 > 0 else True
    ma60_rising = (ma60 > ma60_prev * 0.999) if ma60_prev else True
    ma120_rising= (ma120 >= ma120_prev * 0.998) if ma120_prev else True

    req_trend = p("require_ma_align")
    if req_trend and not (trend_ok and above_ma20):
        return 0.0, {
            "_filtered": True,
            "탈락(추세)": f"MA20({pf(ma20)})>MA60({pf(ma60)})>MA120({pf(ma120)}) 또는 현재가>MA20 미충족",
            "데이터": "🟢 실시간" if ind.get("realtime") else "🟡 샘플",
        }

    # ══════════════════════════════════════════════════════════════════
    # 3단계: 5요소 점수화
    # ══════════════════════════════════════════════════════════════════

    # ── A. 추세 (30점) ────────────────────────────────────────────────
    trend_sc = 0
    # MA 정배열 (10점)
    if ma20 > ma60 > ma120:   trend_sc += 10
    elif ma20 > ma60:          trend_sc += 6
    elif cur > ma60:           trend_sc += 3
    # 60일선 상승 (10점)
    if ma60_rising:            trend_sc += 10
    elif ma120_rising:         trend_sc += 5
    # 현재가>MA20 (10점)
    if cur > ma20:             trend_sc += 10
    elif cur > ma60:           trend_sc += 5
    trend_sc = min(30, trend_sc)

    # ── B. 돌파 구조 (30점) ──────────────────────────────────────────
    # 최근 20일/60일 고점 (전일까지)
    hi20 = max((highs or closes)[-(min(21,n)):-(1)] ) if n > 1 else cur
    hi60 = max((highs or closes)[-(min(61,n)):-(1)] ) if n > 1 else cur
    vol20_avg = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 0
    vol_today = volumes[-1] if volumes else 0
    vol_ratio = vol_today / max(vol20_avg, 1)

    # 10일 변동폭 (박스권 압축)
    hi10 = max(closes[-min(10,n):])
    lo10 = min(closes[-min(10,n):])
    range10 = (hi10 - lo10) / max(lo10, 1) * 100
    box_compressed = range10 < float(p("box_compress_pct") or 10)  # 10% 미만

    # 최근 10일 내 돌파 이력 (눌림목 매수형 확인용)
    breakout_10d = False
    for i in range(-min(10,n), -1):
        past_hi = max((highs or closes)[max(0, n+i-20):n+i]) if n+i > 20 else hi20
        if closes[i] > past_hi * 0.998:
            breakout_10d = True; break

    brk_sc = 0
    brk_labels = []
    # 즉시 돌파
    if cur > hi20 * 0.998:
        brk_sc += 15; brk_labels.append(f"20일고점돌파({pf(hi20)})")
    elif cur >= hi20 * 0.95:
        brk_sc += 8;  brk_labels.append(f"20일고점근접({(cur/hi20*100):.0f}%)")

    if cur > hi60 * 0.998:
        brk_sc += 10; brk_labels.append(f"60일고점돌파({pf(hi60)})")
    elif cur >= hi60 * 0.95:
        brk_sc += 5;  brk_labels.append(f"60일고점근접")

    if box_compressed:
        brk_sc += 5; brk_labels.append(f"박스압축({range10:.1f}%)")

    # 눌림목 형 보너스: 최근 돌파 후 조정 + 5일선~20일선 사이
    pullback_zone = (ma5 <= cur <= ma20 * 1.03) if (ma5 > 0 and ma20 > 0) else False
    if breakout_10d and pullback_zone:
        brk_sc += 8; brk_labels.append("돌파후눌림목")
    elif breakout_10d:
        brk_sc += 3; brk_labels.append("최근돌파이력")

    brk_sc = min(30, brk_sc)

    # ── C. 거래량/거래대금 (20점) ─────────────────────────────────────
    tv_val = cur * vol_today
    tv_b   = tv_val / 1e8 if is_kr else tv_val / 1e6
    min_vol_mult = float(p("vol_mult") or 1.8)
    min_tv_b     = float(p("min_tv_hundred_m") or 20)  # 20억 기본 (100억으로 조정 가능)

    vol_sc = 0
    vol_labels = []
    if vol_ratio >= min_vol_mult:
        vol_sc += 10; vol_labels.append(f"거래량{vol_ratio:.1f}배(기준{min_vol_mult:.1f}배)")
    elif vol_ratio >= min_vol_mult * 0.7:
        vol_sc += 5;  vol_labels.append(f"거래량부족({vol_ratio:.1f}배)")

    tv_str = f"₩{tv_b:.0f}억" if is_kr else f"${tv_b:.1f}M"
    min_tv_str = f"₩{min_tv_b:.0f}억" if is_kr else f"${min_tv_b:.0f}M"
    if tv_b >= min_tv_b:
        vol_sc += 10; vol_labels.append(f"거래대금{tv_str}")
    elif tv_b >= min_tv_b * 0.5:
        vol_sc += 5;  vol_labels.append(f"거래대금부족({tv_str})")

    vol_sc = min(20, vol_sc)

    # ── D. 캔들 강도 (20점) ──────────────────────────────────────────
    # 문서 기준: 장대양봉 + 윗꼬리 짧음 + 종가 고가 근처
    can_sc = 0
    can_labels = []

    if opens and highs and lows and len(closes) >= 1:
        o = opens[-1]; h = highs[-1]; l = lows[-1]; c2 = closes[-1]
        rng  = max(h - l, 0.001)
        body = abs(c2 - o)
        ls   = (min(o,c2) - l) / rng
        us   = (h - max(o,c2)) / rng
        br   = body / rng
        bull = c2 >= o

        # 장대양봉: 몸통 3%↑ 이상
        body_pct = (c2 - o) / max(o, 1) * 100
        if body_pct >= 3.0 and bull:
            can_sc += 10; can_labels.append(f"장대양봉({body_pct:.1f}%)")
        elif body_pct >= 1.0 and bull:
            can_sc += 5;  can_labels.append(f"양봉({body_pct:.1f}%)")

        # 윗꼬리 짧음: 윗꼬리 1.5% 이하
        upper_wick_pct = (h - c2) / max(c2, 1) * 100
        if upper_wick_pct <= 1.5:
            can_sc += 5; can_labels.append("윗꼬리짧음")

        # 종가 고가 근처: 고가의 97% 이상
        if c2 >= h * 0.97:
            can_sc += 5; can_labels.append("종가강함(고가근처)")

        # 반전 캔들 추가 탐색 (최근 3봉)
        if not can_labels:
            for ci in range(-min(3,n), 0):
                try:
                    o2=opens[ci]; h2=highs[ci]; l2=lows[ci]; c3=closes[ci]
                    rng2=max(h2-l2,0.001); body2=abs(c3-o2)
                    ls2=(min(o2,c3)-l2)/rng2; us2=(h2-max(o2,c3))/rng2; br2=body2/rng2
                    if ls2>0.55 and br2<0.35 and c3>=o2:
                        can_sc=max(can_sc,8); can_labels.append("망치형"); break
                    elif br2>0.75 and c3>o2:
                        can_sc=max(can_sc,8); can_labels.append("장대양봉패턴"); break
                    # 샛별형 (3봉)
                    if ci==-1 and len(closes)>=3 and opens and len(opens)>=3:
                        c_3=closes[-3]; c_2=closes[-2]
                        if c_3<opens[-3] and abs(c_2-opens[-2])/rng2<0.1 and closes[-1]>(c_3+opens[-3])/2:
                            can_sc=max(can_sc,10); can_labels.append("샛별형(강반전)"); break
                except:
                    pass

    can_sc = min(20, can_sc)

    # ── E. 추세선 돌파 (보너스 +10점) ────────────────────────────────
    from indicators import find_pivot_points, cluster_levels, calc_trendlines
    _h2 = highs if highs else closes
    _l2 = lows  if lows  else closes
    ph, pl = find_pivot_points(_h2, _l2, closes, window=3, lookback=120)
    tl = calc_trendlines(pl, ph, n)
    trend_break = False; trend_desc = "—"

    if tl.get("down") and len(closes) >= 2:
        sl2, in2, x1, _ = tl["down"]
        r_now  = sl2*(n-1)+in2; r_prev = sl2*(n-2)+in2
        if closes[-2] < r_prev * 1.002 and closes[-1] > r_now:
            trend_break = True
            trend_desc  = f"저항추세선 돌파 ({pf(r_now)})"

    # 지지선 근접 (시부야 원본 요소)
    sup_pct = float(p("support_touch_pct") or 2.5)
    sup_levels = cluster_levels(pl, tolerance_pct=2.0, min_touches=1)
    support_hit   = False; support_price = 0
    for lvl, cnt, _ in sorted(sup_levels, key=lambda x: x[0], reverse=True):
        if cur * (1 - sup_pct/100) <= lvl <= cur * (1 + sup_pct/100):
            support_hit = True; support_price = lvl; break

    extra_bonus = 0
    extra_labels = []
    if trend_break:
        extra_bonus += 10; extra_labels.append(f"저항추세선돌파")
    if support_hit:
        extra_bonus += 5;  extra_labels.append(f"지지선근접({pf(support_price)})")

    # ══════════════════════════════════════════════════════════════════
    # 4단계: 매수 시나리오 판별
    # ══════════════════════════════════════════════════════════════════
    scenario = "대기"
    scenario_detail = ""
    # 시나리오 A — 브레이크아웃 즉시 매수형
    breakout_now = (cur > hi20 * 0.998 and vol_ratio >= min_vol_mult and cur >= highs[-1]*0.97 if highs else False)
    # 시나리오 B — 돌파 후 눌림목 매수형
    pullback_ok = (breakout_10d and pullback_zone and vol_ratio >= 1.0
                   and len(closes) >= 2 and closes[-1] > closes[-2])  # 당일 양봉 전환

    if breakout_now:
        scenario = "🚀 A형: 브레이크아웃 즉시 매수"
        scenario_detail = f"20일고점({pf(hi20)}) 돌파 + 거래량{vol_ratio:.1f}배 + 종가강함"
    elif pullback_ok:
        scenario = "📍 B형: 눌림목 매수"
        scenario_detail = f"최근 돌파 후 MA5~MA20 사이 양봉 전환"
    elif brk_sc >= 15:
        scenario = "⏳ 돌파 준비"
        scenario_detail = f"고점 근접 대기 중"

    # ══════════════════════════════════════════════════════════════════
    # 5단계: 손절/익절 기준 산출
    # ══════════════════════════════════════════════════════════════════
    # 손절: 돌파일 저가 이탈 OR 20일선 이탈 OR -5~7%
    stop_ma20   = ma20 * 0.995  # 20일선 이탈
    stop_low    = lows[-1] * 0.995 if lows else cur * 0.95  # 당일 저가
    stop_pct5   = cur * 0.93   # -7%
    stop_price  = max(stop_ma20, stop_low, stop_pct5)  # 가장 가까운 손절 (작은 손실)
    stop_pct    = (stop_price - cur) / cur * 100

    # 익절: 1차 +10~15%, 5일선 이탈 시 잔량 정리
    tp1 = cur * 1.10  # 1차 익절 +10%
    tp2 = cur * 1.15  # 2차 익절 +15%

    # 최소 통과: 5요소 중 3개 이상 + 총점 30점 이상
    sig_cnt = sum([trend_sc >= 20, brk_sc >= 10, vol_sc >= 10, can_sc >= 8, extra_bonus >= 5])
    total_raw = trend_sc + brk_sc + vol_sc + can_sc + extra_bonus
    if sig_cnt < 2 or total_raw < 30:
        return 0.0, {
            "_filtered": True,
            "탈락": f"요소 {sig_cnt}/5 충족·총점 {total_raw:.0f} < 30 (최소 기준 미달)",
            "추세":   f"{trend_sc}/30", "돌파":  f"{brk_sc}/30",
            "거래량": f"{vol_sc}/20",  "캔들":  f"{can_sc}/20",
            "추세선+지지선": f"+{extra_bonus}",
            "데이터": "🟢 실시간" if ind.get("realtime") else "🟡 샘플",
        }

    rank_val = total_raw

    # ── 등급 산출 ────────────────────────────────────────────────────
    if rank_val >= 85:   grade = "🏆 A급 (즉시 관심)"
    elif rank_val >= 75: grade = "⭐ B급 (눌림목 대기)"
    elif rank_val >= 65: grade = "🔍 C급 (관찰)"
    else:                grade = "📌 D급 (하단 후보)"

    detail = {
        "━━ 시부야 다카오 — 실전 스캐너": f"{grade}  ({rank_val:.0f}/100점)",
        "매수 시나리오":  f"{scenario}",
        "진입 근거":      scenario_detail or "—",
        "━━ ① 추세 (30점)": f"{trend_sc}/30",
        "MA 정배열":      f"{'✅' if ma20>ma60>ma120 else '—'} MA20:{pf(ma20)} > MA60:{pf(ma60)} > MA120:{pf(ma120)}",
        "60일선 상승":    f"{'✅' if ma60_rising else '❌'} ({(ma60/max(ma60_prev,1)-1)*100:+.2f}%)" if ma60_prev else "—",
        "현재가>MA20":    f"{'✅' if cur>ma20 else '❌'} {pf(cur)} vs {pf(ma20)}",
        "━━ ② 돌파 구조 (30점)": f"{brk_sc}/30  {' / '.join(brk_labels) if brk_labels else '없음'}",
        "20일 고점":      f"{pf(hi20)}  현재{(cur/max(hi20,1)*100):.0f}%",
        "60일 고점":      f"{pf(hi60)}  현재{(cur/max(hi60,1)*100):.0f}%",
        "박스권 압축":    f"{'✅' if box_compressed else '—'} 10일변동폭:{range10:.1f}%",
        "━━ ③ 거래량 (20점)": f"{vol_sc}/20  {' / '.join(vol_labels) if vol_labels else '없음'}",
        "거래량 배율":    f"{vol_ratio:.1f}배 (기준:{min_vol_mult:.1f}배)",
        "거래대금":       f"{tv_str} (기준:{min_tv_str})",
        "━━ ④ 캔들 강도 (20점)": f"{can_sc}/20  {' / '.join(can_labels) if can_labels else '없음'}",
        "봉 형태":        (f"몸통{(closes[-1]-opens[-1])/max(opens[-1],1)*100:.1f}%  윗꼬리{(highs[-1]-closes[-1])/max(closes[-1],1)*100:.1f}%  아래꼬리{(opens[-1]-lows[-1])/max(opens[-1],1)*100:.1f}%"
                           if opens and highs and lows else "—"),
        "━━ ⑤ 추세선+지지선 (보너스)": f"+{extra_bonus}  {' / '.join(extra_labels) if extra_labels else '없음'}",
        "━━ 손절/익절 기준": "",
        "손절가":         f"{pf(stop_price)} ({stop_pct:.1f}%) — 20일선이탈·당일저가·-7% 중 가까운 것",
        "1차 익절":       f"{pf(tp1)} (+10%)  2차 익절: {pf(tp2)} (+15%)",
        "5일선 이탈 시":  f"잔량 정리 (MA5: {pf(ma5)})",
        "데이터":         "🟢 실시간" if ind.get("realtime") else "🟡 샘플",
    }
    return rank_val, detail



# ── 늦은 등록: score_shibuya는 파일 끝에 정의되므로 여기서 추가 ──
STRATEGY_SCORE["시부야 다카오"] = score_shibuya
STRATEGY_DESC["시부야 다카오"] = "시부야 다카오  정배열+돌파구조+거래량동반+캔들강도+A형/B형 매수시나리오"
STRATEGY_THRESHOLD["시부야 다카오"] = 0
