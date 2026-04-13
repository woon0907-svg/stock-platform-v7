# -*- coding: utf-8 -*-
"""
api_kr.py — KRX Open API + DART Open API 통합 모듈

우선순위:
  OHLCV:   ① KRX Open API → ② 네이버 차트 → ③ yfinance → ④ 샘플
  재무:     ① DART Open API → ② 네이버 금융 → ③ yfinance → ④ 샘플

인증키 설정 (환경변수 또는 직접 입력):
  KRX_API_KEY  = data.krx.co.kr (공공데이터포털) 인증키
  DART_API_KEY = opendart.fss.or.kr 인증키

환경변수로 설정하는 방법:
  export KRX_API_KEY="발급받은키"
  export DART_API_KEY="발급받은키"
  
  또는 Render 대시보드 → Environment Variables 에 등록
"""
import os, json, time, datetime
import requests

# ── 인증키 (환경변수 우선, 없으면 직접 기입) ────────────────────────
KRX_API_KEY  = os.environ.get("KRX_API_KEY",  "YOUR_KRX_API_KEY")
DART_API_KEY = os.environ.get("DART_API_KEY", "YOUR_DART_API_KEY")

# ── 유효성 체크 ─────────────────────────────────────────────────────
def _krx_available():
    return KRX_API_KEY and KRX_API_KEY != "YOUR_KRX_API_KEY"

def _dart_available():
    return DART_API_KEY and DART_API_KEY != "YOUR_DART_API_KEY"


# ══════════════════════════════════════════════════════════════════════
# KRX Open API — OHLCV + 거래대금
# data.krx.co.kr (공공데이터포털 서비스)
# ══════════════════════════════════════════════════════════════════════

# 종목 코드 → KRX short code 변환 (005930.KS → 005930)
def _short_code(ticker: str) -> str:
    return ticker.replace(".KS", "").replace(".KQ", "").strip()

# 날짜 범위 생성 (거래일 기준 약 1년치)
def _date_range(days: int = 365):
    end   = datetime.date.today()
    start = end - datetime.timedelta(days=days)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def fetch_krx_ohlcv(ticker: str, period: int = 365) -> dict | None:
    """
    KRX Open API (공공데이터포털) — 일별 주식 시세
    https://data.krx.co.kr/
    
    반환: data_fetcher.py 의 fetch_realtime() 형식과 동일
    """
    if not _krx_available():
        return None

    code  = _short_code(ticker)
    start, end = _date_range(period)

    # KRX 일별 시세 API (isuCd = 종목코드 6자리)
    url = "https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo"
    params = {
        "serviceKey": KRX_API_KEY,
        "numOfRows":  500,
        "pageNo":     1,
        "resultType": "json",
        "beginBasDt": start,
        "endBasDt":   end,
        "likeSrtnCd": code,
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        items = (data.get("response", {})
                     .get("body", {})
                     .get("items", {})
                     .get("item", []))
        if not items:
            return None

        # 날짜 오름차순 정렬
        items = sorted(items, key=lambda x: x.get("basDt", ""))

        from datetime import datetime as _dt
        closes  = [float(x["clpr"])   for x in items]
        opens   = [float(x["mkp"])    for x in items]
        highs   = [float(x["hipr"])   for x in items]
        lows    = [float(x["lopr"])   for x in items]
        volumes = [int(x["trqu"])     for x in items]
        dates   = [_dt.strptime(x["basDt"], "%Y%m%d") for x in items]

        return dict(
            closes=closes, opens=opens, highs=highs,
            lows=lows, volumes=volumes, dates=dates,
            realtime=True, source="krx",
        )
    except Exception as e:
        print(f"[KRX OHLCV] {ticker}: {e}")
        return None


def fetch_krx_trading_value(ticker: str) -> float | None:
    """
    KRX에서 최근 20일 평균 거래대금 (억원) 조회
    """
    if not _krx_available():
        return None
    try:
        d = fetch_krx_ohlcv(ticker, period=60)
        if not d:
            return None
        vols   = d.get("volumes", [])[-20:]
        closes = d.get("closes",  [])[-20:]
        if not vols or not closes:
            return None
        avg_tv = sum(v * c for v, c in zip(vols, closes)) / max(len(vols), 1)
        return avg_tv / 1e8  # 억원
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════
# DART Open API — 재무제표
# opendart.fss.or.kr
# ══════════════════════════════════════════════════════════════════════

# DART 종목코드 캐시 (종목코드 6자리 → DART corp_code)
_dart_corp_cache: dict[str, str] = {}
_dart_corp_list_loaded = False


def _load_dart_corp_list():
    """DART 전체 기업 목록 로드 (최초 1회)"""
    global _dart_corp_list_loaded
    if _dart_corp_list_loaded or not _dart_available():
        return

    try:
        import zipfile, io
        url  = "https://opendart.fss.or.kr/api/corpCode.xml"
        resp = requests.get(url,
                            params={"crtfc_key": DART_API_KEY},
                            timeout=15)
        zf   = zipfile.ZipFile(io.BytesIO(resp.content))
        xml_data = zf.read("CORPCODE.xml").decode("utf-8")

        # 간단 XML 파싱 (lxml 없어도 동작)
        import re
        stocks = re.findall(
            r"<corp_code>(.*?)</corp_code>.*?"
            r"<stock_code>(.*?)</stock_code>",
            xml_data, re.DOTALL
        )
        for corp_code, stock_code in stocks:
            stock_code = stock_code.strip()
            if stock_code and len(stock_code) == 6:
                _dart_corp_cache[stock_code] = corp_code.strip()
        _dart_corp_list_loaded = True
        print(f"[DART] 기업목록 로드 완료: {len(_dart_corp_cache)}개")
    except Exception as e:
        print(f"[DART] 기업목록 로드 실패: {e}")


def _get_dart_corp_code(ticker: str) -> str | None:
    """종목코드 6자리 → DART corp_code 변환"""
    _load_dart_corp_list()
    code = _short_code(ticker)
    return _dart_corp_cache.get(code)


def _fetch_dart_financials_raw(corp_code: str, year: int, report_code: str) -> list:
    """DART 단일회사 전체 재무제표 조회"""
    url = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
    params = {
        "crtfc_key":  DART_API_KEY,
        "corp_code":  corp_code,
        "bsns_year":  str(year),
        "reprt_code": report_code,   # 11011=사업보고서, 11012=반기, 11013=1분기, 11014=3분기
        "fs_div":     "CFS",         # CFS=연결, OFS=별도
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        d = resp.json()
        if d.get("status") == "000":
            return d.get("list", [])
        return []
    except Exception:
        return []


def fetch_dart_financials(ticker: str) -> dict | None:
    """
    DART Open API — 재무 데이터 조회
    반환: data_fetcher.py 의 _get_financial_indicators() 형식과 동일
    {
      eps_growth:  연간 EPS 성장률 (%)
      earn_growth: 분기 영업이익 성장률 (%)
      rev_growth:  매출 성장률 (%)
      op_growth:   영업이익 성장률 (%)
      roe:         ROE (%)
      per:         PER
      pbr:         PBR
      mktcap:      시가총액 (억원)
      debt_ratio:  부채비율 (%)
    }
    """
    if not _dart_available():
        return None

    corp_code = _get_dart_corp_code(ticker)
    if not corp_code:
        return None

    today = datetime.date.today()
    year  = today.year
    # 가장 최근 보고서 순서로 시도
    reports = [
        ("11014", year,    "3분기"),
        ("11012", year,    "반기"),
        ("11013", year,    "1분기"),
        ("11011", year-1,  "사업보고서"),
        ("11011", year-2,  "사업보고서(전전년)"),
    ]

    def _get_val(items, acnt_nm, sj_div="IS"):
        """계정명으로 금액 추출"""
        for item in items:
            if (item.get("sj_div") == sj_div
                    and item.get("account_nm", "") == acnt_nm):
                v = item.get("thstrm_amount", "0") or "0"
                try:
                    return float(v.replace(",", ""))
                except Exception:
                    return None
        return None

    # ── 최근 2개년 데이터 수집 ──────────────────────────────────────
    curr_items = []
    prev_items = []

    for report_code, yr, label in reports:
        items = _fetch_dart_financials_raw(corp_code, yr, report_code)
        if items:
            curr_items = items
            # 전년도 동일 보고서
            prev_items = _fetch_dart_financials_raw(corp_code, yr - 1, report_code)
            break

    if not curr_items:
        return None

    result = {}

    # 영업이익 성장률
    op_curr = _get_val(curr_items, "영업이익")
    op_prev = _get_val(prev_items, "영업이익") if prev_items else None
    if op_curr is not None and op_prev and op_prev != 0:
        result["op_growth"]   = (op_curr - op_prev) / abs(op_prev) * 100
        result["earn_growth"] = result["op_growth"]   # 분기 영업이익 성장

    # 매출 성장률
    rev_curr = _get_val(curr_items, "매출액") or _get_val(curr_items, "수익(매출액)")
    rev_prev = (_get_val(prev_items, "매출액") or
                _get_val(prev_items, "수익(매출액)")) if prev_items else None
    if rev_curr is not None and rev_prev and rev_prev != 0:
        result["rev_growth"] = (rev_curr - rev_prev) / abs(rev_prev) * 100

    # 당기순이익 → EPS 성장 대리
    net_curr = _get_val(curr_items, "당기순이익")
    net_prev = _get_val(prev_items, "당기순이익") if prev_items else None
    if net_curr is not None and net_prev and net_prev != 0:
        result["eps_growth"] = (net_curr - net_prev) / abs(net_prev) * 100

    # ROE = 당기순이익 / 자본총계 × 100
    equity = _get_val(curr_items, "자본총계", sj_div="BS")
    if net_curr and equity and equity > 0:
        result["roe"] = net_curr / equity * 100

    # 부채비율 = 부채총계 / 자본총계 × 100
    debt = _get_val(curr_items, "부채총계", sj_div="BS")
    if debt is not None and equity and equity > 0:
        result["debt_ratio"] = debt / equity * 100

    result["source"] = "dart"
    return result if len(result) > 1 else None


# ══════════════════════════════════════════════════════════════════════
# DART — 공시 검색 (EP 재료 확인용)
# ══════════════════════════════════════════════════════════════════════

def fetch_dart_disclosures(ticker: str, days: int = 30) -> list:
    """
    최근 N일 내 공시 목록 조회 (수주·실적·자사주 등)
    반환: [{"date": "2024-01-15", "type": "수주공시", "title": "..."}]
    """
    if not _dart_available():
        return []

    corp_code = _get_dart_corp_code(ticker)
    if not corp_code:
        return []

    end   = datetime.date.today()
    start = end - datetime.timedelta(days=days)

    url = "https://opendart.fss.or.kr/api/list.json"
    params = {
        "crtfc_key":  DART_API_KEY,
        "corp_code":  corp_code,
        "bgn_de":     start.strftime("%Y%m%d"),
        "end_de":     end.strftime("%Y%m%d"),
        "page_count": 20,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        d = resp.json()
        items = d.get("list", [])
        result = []
        for item in items:
            result.append({
                "date":  item.get("rcept_dt", ""),
                "type":  item.get("pblntf_ty_nm", ""),
                "title": item.get("report_nm", ""),
            })
        return result
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════════
# DART — 종목코드로 기업명 조회
# ══════════════════════════════════════════════════════════════════════

def lookup_company_name(stock_code_6: str) -> str | None:
    """6자리 종목코드 → 기업명 (DART 기업목록 기반)"""
    _load_dart_corp_list()
    # 역방향 조회
    corp_code = _dart_corp_cache.get(stock_code_6.strip())
    if not corp_code or not _dart_available():
        return None

    url = "https://opendart.fss.or.kr/api/company.json"
    params = {"crtfc_key": DART_API_KEY, "corp_code": corp_code}
    try:
        resp = requests.get(url, params=params, timeout=8)
        d = resp.json()
        return d.get("corp_name")
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════
# 상태 확인 함수
# ══════════════════════════════════════════════════════════════════════

def api_status() -> dict:
    """현재 API 키 설정 상태 반환"""
    return {
        "krx_key_set":  _krx_available(),
        "dart_key_set": _dart_available(),
        "dart_corps_loaded": _dart_corp_list_loaded,
        "dart_corps_count":  len(_dart_corp_cache),
    }
