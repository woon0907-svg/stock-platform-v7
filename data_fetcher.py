#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
data_fetcher.py — 데이터 수집 모듈
  · 로컬 파일 캐시 (JSON)
  · 한국 종목 목록: pykrx → 네이버 WICS → 네이버 시가총액 → KRX API → 캐시 → 폴백
  · 미국 종목 목록: GitHub CSV → pandas Wikipedia → 캐시 → 폴백
  · HTTP 공용 요청 (_http_get / _naver_fetch)
  · 네이버 금융 OHLCV / 실시간 / 투자지표
  · yfinance OHLCV (미국 + 한국 폴백)
  · fetch_realtime / _get_financial_indicators
"""
import os
import re
import csv
import io
import json
import time
import threading
import pathlib
import random
import math
import ssl as _ssl
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import (
    HAS_PD, HAS_YF, HAS_PYKRX,
    HAS_MPL,
    _NAVER_UA,
    KOSPI_FALLBACK, KOSDAQ_FALLBACK,
    SP500_FALLBACK, NASDAQ_FALLBACK,
    pykrx_stock, yf, pd,
)
import config as _config   # _config._KRX_API_KEY 는 매번 _config._KRX_API_KEY 로 읽어야 키 갱신 반영

_CACHE_DIR = pathlib.Path.home() / ".stock_platform"

def _cache_path(key):
    return _CACHE_DIR / f"{key}.json"

def _save_cache(key, pairs):
    """종목 리스트를 JSON 파일로 저장 (성공할 때마다 갱신)."""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "date":    datetime.today().strftime("%Y-%m-%d %H:%M"),
            "count":   len(pairs),
            "tickers": [[n, t] for n, t in pairs],
        }
        with open(_cache_path(key), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _load_cache(key):
    """저장된 캐시 반환. → (pairs, date_str) or (None, None)"""
    try:
        path = _cache_path(key)
        if not path.exists():
            return None, None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        pairs = [tuple(x) for x in data.get("tickers", [])]
        date  = data.get("date", "날짜 불명")
        return (pairs, date) if pairs else (None, None)
    except Exception:
        return None, None

def _dedup(pairs):


    """티커 기준 중복 제거."""
    seen, unique = set(), []
    for n, t in pairs:
        if t not in seen:
            seen.add(t); unique.append((n, t))
    return unique


# ──────────────────────────────────────────────
#  전체 종목 로딩 (한국)
#  1순위: pykrx
#  2순위: KRX 오픈 API (urllib)
#  3순위: 로컬 파일 캐시 (마지막 성공 데이터)
#  4순위: 내장 하드코딩 폴백
# ──────────────────────────────────────────────
_ticker_cache = {}

def _fetch_kr_tickers_via_krx_key(market_code, status_cb=None):
    """
    KRX Open API (인증키 방식) — https://openapi.krx.co.kr
    market_code: 'STK'(코스피) | 'KSQ'(코스닥)
    _config._KRX_API_KEY 가 설정된 경우에만 호출됨
    """
    if not _config._KRX_API_KEY:
        return []
    mkt_name = "코스피" if market_code == "STK" else "코스닥"
    if status_cb:
        status_cb(f"🔑 KRX Open API 키 → {mkt_name} 전 종목 조회 중...")
    try:
        import urllib.parse
        url  = "https://openapi.krx.co.kr/contents/COM/GenerateOTP.jspx"
        form = urllib.parse.urlencode({
            "bld":   "COM/thema/tpGroup/thema01",
            "name":  "fileDown",
            "url":   "dbms/MDC/STAT/standard/MDCSTAT01901",
            "params": (f"mktId={market_code}"
                       f"&locale=ko_KR&share=1&money=1&csvxls_isNo=false"),
        }).encode("utf-8")
        # OTP 발급
        otp_raw = _http_get(url, data=form, extra_headers={
            "Content-Type":   "application/x-www-form-urlencoded",
            "AUTH_KEY":       _config._KRX_API_KEY,
            "Referer":        "https://openapi.krx.co.kr",
        })
        otp = otp_raw.strip()
        if not otp:
            return []
        # 실제 데이터 요청
        data_url  = "https://openapi.krx.co.kr/contents/COM/UseDeployedServiceOTP.jspx"
        data_form = urllib.parse.urlencode({
            "code": otp, "name": "fileDown"}).encode("utf-8")
        raw = _http_get(data_url, data=data_form, extra_headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "AUTH_KEY":     _config._KRX_API_KEY,
        })
        result = json.loads(raw)
        suffix = ".KS" if market_code == "STK" else ".KQ"
        pairs  = []
        for item in result.get("OutBlock_1", []):
            code = str(item.get("ISU_SRT_CD", "")).strip()
            name = str(item.get("ISU_ABBRV",  "")).strip()
            if code and name:
                pairs.append((name, code + suffix))
        if pairs and status_cb:
            status_cb(f"✅ KRX API 키 → {mkt_name} {len(pairs):,}개")
        return pairs
    except Exception as e:
        if status_cb:
            status_cb(f"⚠️ KRX API 키 실패: {e}")
        return []


def _fetch_kr_tickers_via_krx(market_code, status_cb=None, extra_params=None):
    """KRX 오픈 API — form-encoded POST (인증키 불필요, 공개 엔드포인트)."""
    import urllib.parse
    url    = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
    params = extra_params or {
        "bld":         "dbms/MDC/STAT/standard/MDCSTAT01901",
        "locale":      "ko_KR",
        "mktId":       market_code,
        "share":       "1",
        "money":       "1",
        "csvxls_isNo": "false",
    }
    form = urllib.parse.urlencode(params).encode("utf-8")
    mkt_name = "코스피" if market_code == "STK" else "코스닥"
    if status_cb:
        status_cb(f"📡 KRX API → {mkt_name} 종목 목록 수신 중...")
    raw  = _http_get(url, data=form, extra_headers={
        "Content-Type":     "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer":          "http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/"
                            "index.cmd?menuId=MDC0201020201",
        "X-Requested-With": "XMLHttpRequest",
    })
    data   = json.loads(raw)
    suffix = ".KS" if market_code == "STK" else ".KQ"
    pairs  = []
    for item in data.get("OutBlock_1", []):
        code = str(item.get("ISU_SRT_CD", "")).strip()
        name = str(item.get("ISU_ABBRV",  "")).strip()
        if code and name:
            pairs.append((name, code + suffix))
    return pairs


_etf_blacklist: set = set()   # ETF 종목코드 블랙리스트

def _load_etf_blacklist(status_cb=None):
    """네이버 ETF 목록 페이지에서 ETF 코드 수집 (실패 시 조용히 스킵)."""
    global _etf_blacklist
    if _etf_blacklist:
        return
    try:
        import requests as rq, urllib3
        urllib3.disable_warnings()
        headers = {"User-Agent": _NAVER_UA, "Referer": "https://finance.naver.com"}
        base    = "https://finance.naver.com/fund/etfItemList.naver"
        resp    = rq.get(base, headers=headers, verify=False, timeout=15)
        html1   = resp.content.decode("euc-kr", errors="replace")
        pages_m = re.search(r'class="pgRR"[^>]*>.*?page=(\d+)', html1, re.S)
        total   = int(pages_m.group(1)) if pages_m else 1
        codes   = set(re.findall(r'code=(\d{6})', html1))
        for page in range(2, total + 1):
            try:
                resp = rq.get(f"{base}?page={page}", headers=headers,
                              verify=False, timeout=15)
                codes.update(re.findall(
                    r'code=(\d{6})',
                    resp.content.decode("euc-kr", errors="replace")))
                time.sleep(0.05)
            except Exception:
                continue
        if codes:
            _etf_blacklist = codes
            if status_cb:
                status_cb(f"📋 ETF 블랙리스트 {len(codes):,}개 로딩")
    except Exception:
        pass   # 실패해도 키워드 필터로 대체


def _is_common_stock(code, name):
    """
    상장 보통주만 통과 — ETF/ETN/스팩/우선주/리츠 모두 제외.
    """
    if code in _etf_blacklist:
        return False
    nu = name.upper().replace(" ", "")
    for kw in ["ETF","ETN","KODEX","TIGER","KINDEX","ARIRANG","KOSEF",
               "HANARO","KBSTAR","TIMEFOLIO","TREX","ACE","SOL","PLUS",
               "KTOP","FOCUS","NHPLUS","IBK","POWER","WOORI","WON",
               "MASTER","SMART","MIRAE","SAMSUNG","KB","HANA","NH"]:
        if kw in nu:
            return False
    kr_excl = [
        "레버리지","인버스","2X","3X","선물","옵션",
        "채권","국채","통안","단기채","중기채","장기채","회사채","만기매칭",
        "액티브","배당귀족","배당다우","고배당","우량채","커버드콜",
        "나스닥","다우존스","차이나","베트남","인도","일본","미국","글로벌",
        "신흥국","유럽","러시아","브라질","대만",
        "골드","금선물","원유","구리","은선물",
        "리츠","인프라","부동산","데이터센터",
        "코스피200","코스닥150","코스피100","코스피50","코스닥50",
        "혼합","멀티","TDF","스팩",
        "투자회사","사모","공모","펀드","신탁",
    ]
    for kw in kr_excl:
        if kw in name:
            return False
    # 스팩 영문 패턴
    import re as _re
    if _re.search(r"(?i)spac", name):  # noqa
        return False
     # 우선주 포함 (보통주와 동일하게 투자 대상)
    return True




def _fetch_kr_tickers_via_naver_wics(sosok, status_cb=None):
    """
    네이버 WICS 업종 분류 페이지 — ETF 없이 일반주식만 포함.
    sosok: 0=코스피, 1=코스닥
    업종 코드: G10(에너지)~G55(유틸리티) 순회
    """
    import requests as rq, urllib3
    urllib3.disable_warnings()

    suffix   = ".KS" if sosok == 0 else ".KQ"
    mkt_name = "코스피" if sosok == 0 else "코스닥"
    market   = "KOSPI" if sosok == 0 else "KOSDAQ"

    # WICS 대분류 (10개)
    wics_sectors = ["G10","G15","G20","G25","G30","G35","G40","G45","G50","G55"]
    pattern = (r'href="/item/main\.naver\?code=(\d{6})"'
               r'[^>]*class="tltle">([^<]+)<')
    headers = {"User-Agent": _NAVER_UA, "Referer": "https://finance.naver.com"}

    all_pairs = []
    seen      = set()

    for i, wics in enumerate(wics_sectors):
        try:
            # 1페이지로 총 페이지 수 파악
            url1 = (f"https://finance.naver.com/sise/sise_wics.naver"
                    f"?wics={wics}&sosok={sosok}&page=1")
            resp = rq.get(url1, headers=headers, verify=False, timeout=15)
            html1 = resp.content.decode("euc-kr", errors="replace")

            pages_m = re.search(r'class="pgRR"[^>]*>.*?page=(\d+)', html1, re.S)
            total   = int(pages_m.group(1)) if pages_m else 1

            for page in range(1, total + 1):
                if page == 1:
                    html = html1
                else:
                    url  = (f"https://finance.naver.com/sise/sise_wics.naver"
                            f"?wics={wics}&sosok={sosok}&page={page}")
                    resp = rq.get(url, headers=headers, verify=False, timeout=15)
                    html = resp.content.decode("euc-kr", errors="replace")

                for code, name in re.findall(pattern, html):
                    t = code + suffix
                    if t not in seen:
                        seen.add(t); all_pairs.append((name.strip(), t))
                time.sleep(0.05)

            if status_cb and (i + 1) % 3 == 0:
                status_cb(f"📡 네이버 WICS {mkt_name} {i+1}/{len(wics_sectors)}업종 "
                          f"({len(all_pairs):,}개)...")
        except Exception:
            continue

    return all_pairs


def _fetch_kr_tickers_via_naver(sosok, status_cb=None):
    """
    네이버 금융 시가총액 페이지 전체 스크래핑.
    sosok: 0=코스피, 1=코스닥
    진단 확인: requests + euc-kr 디코딩으로 1페이지 50개 정상 추출됨
    """
    import requests as rq, urllib3
    urllib3.disable_warnings()

    suffix   = ".KS" if sosok == 0 else ".KQ"
    mkt_name = "코스피" if sosok == 0 else "코스닥"
    base_url = "https://finance.naver.com/sise/sise_market_sum.naver"
    headers  = {
        "User-Agent":      _NAVER_UA,
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Referer":         "https://finance.naver.com",
    }
    pattern = (r'href="/item/main\.naver\?code=(\d{6})"'
               r'[^>]*class="tltle">([^<]+)<')

    def _get(page):
        resp = rq.get(f"{base_url}?sosok={sosok}&page={page}",
                      headers=headers, verify=False, timeout=15)
        return resp.content.decode("euc-kr", errors="replace")

    def _parse(html):
        return re.findall(pattern, html)

    def _total(html):
        m = re.search(r'class="pgRR"[^>]*>.*?page=(\d+)', html, re.S)
        if m: return int(m.group(1))
        nums = re.findall(r'page=(\d+)', html)
        return max(int(n) for n in nums) if nums else 1

    if status_cb:
        status_cb(f"📡 네이버 {mkt_name} 1페이지 요청...")

    try:
        html1       = _get(1)
        page1_pairs = _parse(html1)
        if not page1_pairs:
            if status_cb:
                status_cb(f"⚠️ 네이버 {mkt_name}: 1페이지 종목 0개")
            return []

        total     = _total(html1)
        all_pairs = []
        seen      = set()

        if status_cb:
            status_cb(f"📡 네이버 {mkt_name} — {total}p ({len(page1_pairs)}개/p) 수집 중...")

        for code, name in page1_pairs:
            t = code + suffix
            if t not in seen and _is_common_stock(code, name):
                seen.add(t); all_pairs.append((name.strip(), t))

        for page in range(2, total + 1):
            try:
                html = _get(page)
                for code, name in _parse(html):
                    t = code + suffix
                    if t not in seen and _is_common_stock(code, name):
                        seen.add(t); all_pairs.append((name.strip(), t))
                if status_cb and page % 10 == 0:
                    status_cb(f"📡 네이버 {mkt_name} {page}/{total}p ({len(all_pairs):,}개)")
                time.sleep(0.05)
            except Exception:
                continue

        return all_pairs

    except Exception as e:
        import traceback
        err_msg = f"⚠️ 네이버 {mkt_name} 오류: {type(e).__name__}: {e}"
        print(err_msg, flush=True)          # 터미널에 직접 출력
        traceback.print_exc()               # 전체 스택 출력
        if status_cb:
            status_cb(err_msg)
        return []


def get_all_tickers(market="ALL", status_cb=None):
    """
    한국 전 종목 반환.
    0순위: KRX Open API 키  (키 입력 시)
    1순위: pykrx
    2순위: 네이버 금융 시가총액 페이지
    3순위: KRX 구 API
    4순위: 로컬 파일 캐시
    5순위: 내장 폴백
    """
    global _ticker_cache

    def _cb(msg):
        if status_cb: status_cb(msg)

    _FALLBACK = {
        "KOSPI":  KOSPI_FALLBACK,
        "KOSDAQ": KOSDAQ_FALLBACK,
        "ALL":    KOSPI_FALLBACK + KOSDAQ_FALLBACK,
    }
    mkts_to_load = ["KOSPI", "KOSDAQ"] if market == "ALL" else [market]

    # ETF 블랙리스트 사전 로딩 (미로딩 시 한번만)
    if not _etf_blacklist:
        _load_etf_blacklist(status_cb=_cb)

    # ─── 0순위: KRX Open API 키 ───
    if _config._KRX_API_KEY:
        _cb("🔑 KRX Open API 키 → 전 종목 조회 중...")
        mkt_code_map = {"KOSPI": "STK", "KOSDAQ": "KSQ"}
        result = []
        for mkt in mkts_to_load:
            if mkt in _ticker_cache:
                result.extend(_ticker_cache[mkt])
                continue
            pairs = _fetch_kr_tickers_via_krx_key(mkt_code_map[mkt], status_cb=_cb)
            if pairs:
                _ticker_cache[mkt] = pairs
                _save_cache(f"kr_{mkt}", pairs)
            result.extend(_ticker_cache.get(mkt, []))
        if result:
            return _dedup(result)

    # ─── 1순위: pykrx ───
    if HAS_PYKRX:
        _cb("📦 pykrx → 전 종목 목록 로딩 중...")
        today = datetime.today()
        # 오늘 포함 최근 14일 중 데이터 있는 날짜 자동 탐색
        # (당일 마감 전이거나 공휴일이면 0개 반환 → 더 이전 날짜로)
        found_date = None
        for offset in range(1, 15):   # 오늘 제외, 전일부터 탐색
            d = today - timedelta(days=offset)
            if d.weekday() >= 5:
                continue
            date_str = d.strftime("%Y%m%d")
            try:
                test = pykrx_stock.get_market_ticker_list(date_str, market="KOSPI")
                if test and len(test) > 100:   # 100개 이상이면 유효
                    found_date = date_str
                    break
            except Exception:
                continue
        if found_date:
            try:
                result = []
                for mkt in mkts_to_load:
                    if mkt not in _ticker_cache:
                        mkt_name = "코스피" if mkt == "KOSPI" else "코스닥"
                        _cb(f"📦 pykrx → {mkt_name} ({found_date}) 조회 중...")
                        raw = pykrx_stock.get_market_ticker_list(found_date, market=mkt)
                        _cb(f"📦 pykrx → {mkt_name} {len(raw):,}개 종목명 조회 중...")
                        suffix = ".KS" if mkt == "KOSPI" else ".KQ"
                        pairs  = []
                        for t in raw:
                            try:
                                name = pykrx_stock.get_market_ticker_name(t)
                                if not name: name = t
                            except Exception:
                                name = t
                            pairs.append((name, t + suffix))
                        if pairs:
                            _ticker_cache[mkt] = pairs
                            _save_cache(f"kr_{mkt}", pairs)
                    result.extend(_ticker_cache.get(mkt, []))
                if result:
                    _cb(f"✅ pykrx 완료 — {len(result):,}개 (기준일 {found_date})")
                    return result
            except Exception as e:
                _cb(f"⚠️ pykrx 실패: {e}  →  KRX API 시도")
        else:
            _cb("⚠️ pykrx → 최근 14일 데이터 없음  →  KRX API 시도")

    # ─── 2순위: 네이버 WICS 업종 분류 (일반주식만) ───
    _cb("📡 네이버 WICS → 일반주식 전 종목 수집 중...")
    try:
        result    = []
        sosok_map = {"KOSPI": 0, "KOSDAQ": 1}
        wics_ok   = False
        for mkt in mkts_to_load:
            if mkt in _ticker_cache:
                result.extend(_ticker_cache[mkt])
                continue
            pairs = _fetch_kr_tickers_via_naver_wics(sosok_map[mkt], status_cb=_cb)
            if len(pairs) > 100:   # 100개 미만이면 파싱 실패로 간주
                mkt_name = "코스피" if mkt == "KOSPI" else "코스닥"
                _cb(f"✅ 네이버 WICS → {mkt_name} {len(pairs):,}개 (일반주식)")
                _ticker_cache[mkt] = pairs
                _save_cache(f"kr_{mkt}", pairs)
                wics_ok = True
            result.extend(_ticker_cache.get(mkt, []))
        if wics_ok and result:
            return _dedup(result)
    except Exception as e:
        _cb(f"⚠️ WICS 수집 실패: {e}")

    # ─── 3순위: 네이버 시가총액 페이지 (ETF 포함, 필터 적용) ───
    _cb("📡 네이버 시가총액 → 전 종목 수집 중...")
    try:
        result    = []
        sosok_map = {"KOSPI": 0, "KOSDAQ": 1}
        for mkt in mkts_to_load:
            if mkt in _ticker_cache:
                result.extend(_ticker_cache[mkt])
                continue
            pairs = _fetch_kr_tickers_via_naver(sosok_map[mkt], status_cb=_cb)
            if pairs:
                mkt_name = "코스피" if mkt == "KOSPI" else "코스닥"
                _cb(f"✅ 네이버 → {mkt_name} {len(pairs):,}개")
                _ticker_cache[mkt] = pairs
                _save_cache(f"kr_{mkt}", pairs)
            result.extend(_ticker_cache.get(mkt, []))
        if result:
            return _dedup(result)
    except Exception as e:
        _cb(f"⚠️ 네이버 수집 실패: {e}")
    _cb("📡 KRX 오픈 API → 전 종목 로딩 시도 중...")
    try:
        result = []
        mkt_code_map = {"KOSPI": "STK", "KOSDAQ": "KSQ"}
        for mkt in mkts_to_load:
            if mkt in _ticker_cache:
                result.extend(_ticker_cache[mkt])
                continue
            mkt_code = mkt_code_map[mkt]
            pairs = []
            for params in [
                {"bld": "dbms/MDC/STAT/standard/MDCSTAT01901", "locale": "ko_KR",
                 "mktId": mkt_code, "share": "1", "money": "1", "csvxls_isNo": "false"},
                {"bld": "dbms/MDC/STAT/standard/MDCSTAT01901", "locale": "ko_KR",
                 "mktId": mkt_code, "segTpCd": "ALL",
                 "share": "1", "money": "1", "csvxls_isNo": "false"},
            ]:
                try:
                    pairs = _fetch_kr_tickers_via_krx(mkt_code, status_cb=_cb,
                                                      extra_params=params)
                    if pairs: break
                except Exception:
                    continue
            if pairs:
                mkt_name = "코스피" if mkt == "KOSPI" else "코스닥"
                _cb(f"✅ KRX API → {mkt_name} {len(pairs):,}개")
                _ticker_cache[mkt] = pairs
                _save_cache(f"kr_{mkt}", pairs)
            result.extend(_ticker_cache.get(mkt, []))
        if result:
            return _dedup(result)
    except Exception as e:
        _cb(f"⚠️ KRX API 실패: {e}")

    # ─── 4순위: 로컬 파일 캐시 ───
    _cb("💾 로컬 캐시 확인 중...")
    file_result = []
    for mkt in mkts_to_load:
        cached, date = _load_cache(f"kr_{mkt}")
        if cached:
            mkt_name = "코스피" if mkt == "KOSPI" else "코스닥"
            _cb(f"💾 캐시 로드 — {mkt_name} {len(cached):,}개  (저장일: {date})")
            _ticker_cache[mkt] = cached
            file_result.extend(cached)
    if file_result:
        return _dedup(file_result)

    # ─── 5순위: 내장 폴백 ───
    result = _dedup(_FALLBACK[market])
    _cb(f"📋 내장 폴백 — {len(result)}개  "
        f"(네이버 금융 접속 성공 시 전 종목 자동 저장)")
    return result


def get_ticker_count_label(market="ALL"):
    """UI용 종목 수 안내 (파일 캐시 날짜 포함)."""
    cached_kospi  = len(_ticker_cache.get("KOSPI",  []))
    cached_kosdaq = len(_ticker_cache.get("KOSDAQ", []))
    if market == "ALL":
        total = cached_kospi + cached_kosdaq
        if total > 300:
            return f"코스피+코스닥 전 종목 ({total:,}개)"
        _, d_kp = _load_cache("kr_KOSPI")
        _, d_kd = _load_cache("kr_KOSDAQ")
        if d_kp or d_kd:
            return f"코스피+코스닥 캐시 ({d_kp or d_kd} 기준)"
        return f"코스피+코스닥 {len(KOSPI_FALLBACK)+len(KOSDAQ_FALLBACK)}개 (내장 폴백)"
    elif market == "KOSPI":
        if cached_kospi: return f"코스피 전 종목 ({cached_kospi:,}개)"
        c, d = _load_cache("kr_KOSPI")
        return f"코스피 캐시 {len(c):,}개 ({d})" if c else f"코스피 {len(KOSPI_FALLBACK)}개 (내장 폴백)"
    else:
        if cached_kosdaq: return f"코스닥 전 종목 ({cached_kosdaq:,}개)"
        c, d = _load_cache("kr_KOSDAQ")
        return f"코스닥 캐시 {len(c):,}개 ({d})" if c else f"코스닥 {len(KOSDAQ_FALLBACK)}개 (내장 폴백)"


# ──────────────────────────────────────────────
#  전체 종목 로딩 (미국)
#  1순위: urllib GitHub CSV / Wikipedia HTML
#  2순위: pandas Wikipedia
#  3순위: 로컬 파일 캐시
#  4순위: 내장 폴백
# ──────────────────────────────────────────────


_us_ticker_cache = {}

def _fetch_sp500_via_github(status_cb=None):
    if status_cb: status_cb("📡 GitHub CSV → S&P500 다운로드 중...")
    raw = _http_get(
        "https://raw.githubusercontent.com/datasets/"
        "s-and-p-500-companies/main/data/constituents.csv"
    )
    pairs = []
    for row in csv.DictReader(io.StringIO(raw)):
        sym  = row.get("Symbol", "").strip().replace(".", "-")
        name = row.get("Security", row.get("Name", sym)).strip()
        if sym: pairs.append((name, sym))
    return pairs


def _fetch_nasdaq100_via_wikipedia(status_cb=None):
    if status_cb: status_cb("📡 Wikipedia HTML → NASDAQ-100 파싱 중...")
    import html.parser as hp
    html_src = _http_get("https://en.wikipedia.org/wiki/Nasdaq-100")
    pairs = []

    class TP(hp.HTMLParser):
        def __init__(self):
            super().__init__()
            self.in_t=False; self.cells=[]; self.cur=""; self.rows=[]
        def handle_starttag(self, tag, attrs):
            d=dict(attrs)
            if tag=="table" and "wikitable" in d.get("class",""):
                self.in_t=True
            if self.in_t:
                if tag=="tr": self.cells=[]
                if tag in("td","th"): self.cur=""
        def handle_endtag(self, tag):
            if tag=="table": self.in_t=False
            if self.in_t and tag in("td","th"): self.cells.append(self.cur.strip())
            if self.in_t and tag=="tr" and self.cells:
                self.rows.append(self.cells[:]); self.cells=[]
        def handle_data(self, data):
            if self.in_t: self.cur+=data

    p=TP(); p.feed(html_src)
    for row in p.rows:
        if len(row)>=2:
            for i,cell in enumerate(row):
                if cell.isupper() and 1<=len(cell)<=5:
                    pairs.append((row[0] if i>0 else cell, cell)); break
    return _dedup([(n,t) for n,t in pairs if t.isalpha()])


def get_us_tickers(market="US_ALL", status_cb=None):
    """
    미국 전 종목 반환.
    1순위: urllib          → 성공 시 파일 저장
    2순위: pandas          → 성공 시 파일 저장
    3순위: 로컬 파일 캐시  → 마지막 성공일 표시
    4순위: 내장 폴백
    """
    global _us_ticker_cache

    def _cb(msg):
        if status_cb: status_cb(msg)

    _FALLBACK = {
        "SP500":  SP500_FALLBACK,
        "NASDAQ": NASDAQ_FALLBACK,
        "US_ALL": SP500_FALLBACK + NASDAQ_FALLBACK,
    }
    mkts = ["SP500", "NASDAQ"] if market == "US_ALL" else [market]

    for mkt in mkts:
        if mkt in _us_ticker_cache:
            continue
        pairs = []

        # ─── 1순위: urllib ───
        try:
            pairs = (_fetch_sp500_via_github(status_cb=_cb) if mkt=="SP500"
                     else _fetch_nasdaq100_via_wikipedia(status_cb=_cb))
            if pairs: _cb(f"✅ urllib → {mkt} {len(pairs):,}개 수신")
        except Exception as e:
            _cb(f"⚠️ urllib 실패({mkt}): {e}  →  pandas 시도")

        # ─── 2순위: pandas ───
        if not pairs and HAS_PD:
            try:
                _cb(f"📦 pandas Wikipedia → {mkt} 파싱 중...")
                if mkt == "SP500":
                    df = pd.read_html("https://en.wikipedia.org/wiki/"
                                      "List_of_S%26P_500_companies",
                                      attrs={"id":"constituents"})[0]
                    sc = "Symbol"   if "Symbol"   in df.columns else df.columns[0]
                    nc = "Security" if "Security" in df.columns else df.columns[1]
                    for _, row in df.iterrows():
                        sym = str(row[sc]).strip().replace(".","-")
                        if sym and sym!="nan": pairs.append((str(row[nc]).strip(), sym))
                else:
                    for tbl in pd.read_html("https://en.wikipedia.org/wiki/Nasdaq-100"):
                        cols = [str(c).lower() for c in tbl.columns]
                        tc = next((tbl.columns[i] for i,c in enumerate(cols)
                                    if "ticker" in c or "symbol" in c), None)
                        nc = next((tbl.columns[i] for i,c in enumerate(cols)
                                    if "company" in c or "security" in c), None)
                        if tc and len(tbl)>80:
                            for _, row in tbl.iterrows():
                                sym = str(row[tc]).strip().replace(".","-")
                                if sym and sym!="nan" and len(sym)<=6:
                                    pairs.append((str(row[nc]).strip() if nc else sym, sym))
                            break
                if pairs: _cb(f"✅ pandas → {mkt} {len(pairs):,}개 수신")
            except Exception as e:
                _cb(f"⚠️ pandas 실패({mkt}): {e}")

        if pairs:
            pairs = _dedup(pairs)
            _us_ticker_cache[mkt] = pairs
            _save_cache(f"us_{mkt}", pairs)                   # ← 파일 저장
        else:
            # ─── 3순위: 로컬 파일 캐시 ───
            cached, date = _load_cache(f"us_{mkt}")
            if cached:
                _cb(f"💾 캐시 로드 — {mkt} {len(cached):,}개  (저장일: {date})")
                _us_ticker_cache[mkt] = cached
            else:
                # ─── 4순위: 내장 폴백 ───
                fb = _dedup(_FALLBACK[mkt])
                _cb(f"📋 내장 폴백 — {mkt} {len(fb)}개  (인터넷 연결 시 자동 저장)")

    result = _dedup([t for mkt in mkts
                     for t in _us_ticker_cache.get(mkt, _dedup(_FALLBACK[mkt]))])
    return result or _dedup(_FALLBACK[market])


def get_us_ticker_count_label(market="US_ALL"):
    n_sp = len(_us_ticker_cache.get("SP500",  []))
    n_nd = len(_us_ticker_cache.get("NASDAQ", []))
    def fi(key):
        c, d = _load_cache(key); return (len(c), d) if c else (0, None)
    if market == "US_ALL":
        if n_sp+n_nd: return f"S&P500+NASDAQ ({n_sp+n_nd:,}개)"
        a, da = fi("us_SP500"); b, db = fi("us_NASDAQ")
        if a or b: return f"S&P500+NASDAQ 캐시 {a+b:,}개  ({da or db})"
        return f"S&P500+NASDAQ {len(SP500_FALLBACK)+len(NASDAQ_FALLBACK)}개 (내장 폴백)"
    elif market == "SP500":
        if n_sp: return f"S&P500 ({n_sp:,}개)"
        n, d = fi("us_SP500")
        return f"S&P500 캐시 {n:,}개 ({d})" if n else f"S&P500 {len(SP500_FALLBACK)}개 (내장 폴백)"
    else:
        if n_nd: return f"NASDAQ-100 ({n_nd:,}개)"
        n, d = fi("us_NASDAQ")
        return f"NASDAQ 캐시 {n:,}개 ({d})" if n else f"NASDAQ {len(NASDAQ_FALLBACK)}개 (내장 폴백)"




# ══ 공용 SSL 컨텍스트 ═════════════════════════════════════════
import ssl as _ssl_mod

def _make_ssl_ctx():
    try:
        import certifi
        ctx = _ssl_mod.create_default_context(cafile=certifi.where())
        return ctx
    except Exception:
        pass
    try:
        ctx = _ssl_mod._create_unverified_context()
        ctx.check_hostname = False
        ctx.verify_mode    = _ssl_mod.CERT_NONE
        return ctx
    except Exception:
        pass
    try:
        ctx = _ssl_mod.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode    = _ssl_mod.CERT_NONE
        return ctx
    except Exception:
        pass
    return None

_SSL_CTX = _make_ssl_ctx()

def _http_get(url, data=None, extra_headers=None, encoding="utf-8", timeout=15):
    """
    공용 HTTP 요청 함수.
    data 있으면 POST, 없으면 GET.
    SSL: certifi → 우회 → requests → http 폴백
    """
    headers = {
        "User-Agent":      _NAVER_UA,
        "Accept":          "text/html,application/json,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "Accept-Encoding": "identity",   # gzip 비활성화 → 순수 bytes 수신
        "Connection":      "keep-alive",
    }
    if extra_headers:
        headers.update(extra_headers)

    req = urllib.request.Request(url, data=data, headers=headers)

    # 시도 1: SSL 컨텍스트 (certifi or 우회)
    if _SSL_CTX:
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
                raw = r.read()
                # Content-Encoding 확인 후 압축 해제
                ce = r.info().get("Content-Encoding", "")
                if ce == "gzip":
                    import gzip; raw = gzip.decompress(raw)
                elif ce == "br":
                    try:
                        import brotli; raw = brotli.decompress(raw)
                    except ImportError:
                        pass
                return raw.decode(encoding, errors="replace")
        except _ssl.SSLError:
            pass
        except Exception as e:
            if "SSL" not in str(e) and "certificate" not in str(e).lower():
                raise

    # 시도 2: requests (다른 SSL 엔진)
    try:
        import requests as _req, urllib3 as _u3
        _u3.disable_warnings(_u3.exceptions.InsecureRequestWarning)
        if data:
            resp = _req.post(url, data=data, headers=headers, verify=False, timeout=timeout)
        else:
            resp = _req.get(url, headers=headers, verify=False, timeout=timeout)
        # resp.content는 이미 압축 해제된 raw bytes
        return resp.content.decode(encoding, errors="replace")
    except ImportError:
        pass

    # 시도 3: http 폴백 (SSL 없음)
    http_url = url.replace("https://", "http://")
    if http_url != url:
        req2 = urllib.request.Request(http_url, data=data, headers=headers)
        with urllib.request.urlopen(req2, timeout=timeout) as r:
            raw = r.read()
            ce  = r.info().get("Content-Encoding", "")
            if ce == "gzip":
                import gzip; raw = gzip.decompress(raw)
            return raw.decode(encoding, errors="replace")

    raise ConnectionError(f"모든 연결 방법 실패: {url}")


def _naver_fetch(url, encoding="utf-8"):
    """네이버 금융 전용 요청 (Referer 포함)."""
    return _http_get(url, encoding=encoding, extra_headers={
        "Referer":                   "https://finance.naver.com",
        "Upgrade-Insecure-Requests": "1",
    })


def _is_kr_ticker(ticker):
    """한국 주식 티커 판별  (.KS / .KQ 또는 6자리 숫자)."""
    return ticker.endswith(".KS") or ticker.endswith(".KQ") or            bool(re.match(r"^\d{6}$", ticker))


def _kr_code(ticker):
    """티커에서 6자리 종목코드 추출."""
    return ticker.replace(".KS", "").replace(".KQ", "").strip()



def _fetch_naver_ohlcv(ticker, count=220):
    """
    네이버 차트 API → 일봉 OHLCV.
    반환: dict {dates, opens, highs, lows, closes, volumes, realtime=True}
    """
    code = _kr_code(ticker)
    url  = (f"https://fchart.stock.naver.com/sise.nhn"
            f"?symbol={code}&timeframe=day&count={count}&requestType=0")
    raw   = _naver_fetch(url)
    items = re.findall(r'<item data="([^"]+)"', raw)
    if not items:
        return None

    dates, opens, highs, lows, closes, volumes = [], [], [], [], [], []
    for item in items:
        parts = item.split("|")
        if len(parts) < 6:
            continue
        try:
            d = datetime.strptime(parts[0], "%Y%m%d")
            dates.append(d)
            opens.append(float(parts[1]))
            highs.append(float(parts[2]))
            lows.append(float(parts[3]))
            closes.append(float(parts[4]))
            volumes.append(int(parts[5]))
        except (ValueError, IndexError):
            continue

    if len(closes) < 50:
        return None

    return dict(dates=dates, opens=opens, highs=highs,
                lows=lows, closes=closes, volumes=volumes,
                realtime=True, source="naver")


def _fetch_naver_realtime(ticker):
    """
    네이버 Polling API → 현재가 / 당일 시고저.
    반환: dict {cur, open, high, low, volume, change_rate} or None
    """
    code = _kr_code(ticker)
    url  = (f"https://polling.finance.naver.com/api/realtime"
            f"?query=SERVICE_ITEM:{code}")
    try:
        raw  = _naver_fetch(url)
        data = json.loads(raw)
        item = (data.get("result", {})
                    .get("areas", [{}])[0]
                    .get("datas", [{}])[0])
        if not item.get("nv"):
            return None
        return dict(
            cur         = float(item.get("nv", 0)),
            open        = float(item.get("sv", 0)),
            high        = float(item.get("hv", 0)),
            low         = float(item.get("lv", 0)),
            volume      = int(item.get("aq", 0)),
            change_rate = float(item.get("cr", 0)),
            change_val  = float(item.get("cv", 0)),
        )
    except Exception:
        return None


def _fetch_naver_indicators(ticker):
    """
    네이버 금융 → 투자지표 파싱 (실제 HTML 검증 기반).

    1차: main.naver      → _per, _eps, _pbr, _dvr, _market_sum, cop_analysis(ROE)
    2차: finsum_more     → 52주고저, 외국인소진율, 목표주가
    """
    code = _kr_code(ticker)

    def _em_id(eid, html):
        m = re.search(r'id="' + re.escape(eid) + r'"[^>]*>\s*([\d,\.]+)', html)
        if m:
            try: return float(m.group(1).replace(",", ""))
            except: pass
        return None

    def _mktcap(html):
        mc = re.search(r'id="_market_sum"[^>]*>([\s\S]*?)억원', html)
        if mc:
            inner = re.sub(r'<[^>]+>', ' ', mc.group(1))
            jo_m  = re.search(r'([\d,]+)조', inner)
            uk_m  = re.search(r'조\s*([\d,]+)', inner)
            if jo_m:
                jo = int(jo_m.group(1).replace(",", "")) * 10000
                uk = int(uk_m.group(1).replace(",", "")) if uk_m else 0
                return jo + uk
            only = re.search(r'([\d,]+)', inner)
            if only: return int(only.group(1).replace(",", ""))
        return None

    def _cop_map(html):
        """th_cop_analN 클래스 → 해당 행 첫 번째 td class="" 값."""
        result = {}
        cop = re.search(r'cop_analysis[\s\S]+?</table>', html, re.S)
        if not cop: return result
        for m in re.finditer(
            r'th_cop_anal(\d+)[^>]*>[\s\S]*?<strong>[^<]+</strong>'
            r'[\s\S]*?<td[^>]*class=""[^>]*>\s*([\d\.,\-]+)\s*</td>',
            cop.group(), re.S
        ):
            try: result[int(m.group(1))] = float(m.group(2).replace(",", ""))
            except: pass
        return result

    try:
        # ── 1차: main.naver ──────────────────────────────
        main_raw = _naver_fetch(
            f"https://finance.naver.com/item/main.naver?code={code}",
            encoding="utf-8"
        )
        per     = _em_id("_per",  main_raw)
        eps     = _em_id("_eps",  main_raw)
        cns_per = _em_id("_cns_per", main_raw)
        cns_eps = _em_id("_cns_eps", main_raw)
        pbr     = _em_id("_pbr",  main_raw)
        dvr     = _em_id("_dvr",  main_raw)
        mktcap  = _mktcap(main_raw)   # main에선 JS 동적 설정 → None 가능
        cop     = _cop_map(main_raw)
        roe     = cop.get(13)

        # ── 2차: finsum_more (시가총액·52주·외국인·목표주가) ──────
        hi52 = lo52 = frgn = target_price = None
        try:
            fin_raw = _naver_fetch(
                f"https://finance.naver.com/item/coinfo.naver"
                f"?code={code}&target=finsum_more",
                encoding="utf-8"
            )
            # 시가총액: finsum_more의 _market_sum (실제값 포함)
            if not mktcap:
                mktcap = _mktcap(fin_raw)
            # 52주 최고/최저
            w52 = re.search(
                r'52주최고[\s\S]*?최저[\s\S]*?<td[^>]*>([\s\S]*?)</td>',
                fin_raw, re.S)
            if w52:
                ems = re.findall(r'<em>([\d,]+)</em>', w52.group(1))
                if len(ems) >= 2:
                    try:
                        hi52 = float(ems[0].replace(",", ""))
                        lo52 = float(ems[1].replace(",", ""))
                    except: pass

            # 외국인소진율 (B/A)
            ba = re.search(
                r'\(B/A\)[\s\S]*?</th>[\s\S]*?<td[^>]*>([\s\S]*?)</td>',
                fin_raw, re.S)
            if ba:
                f_m = re.search(r'([\d\.]+)%?',
                                 re.sub(r'<[^>]+>', '', ba.group(1)))
                if f_m:
                    try: frgn = float(f_m.group(1))
                    except: pass

            # 목표주가
            tp = re.search(
                r'투자의견[\s\S]*?목표주가[\s\S]*?'
                r'<em>[\d\.]+</em>[\s\S]*?<em>([\d,]+)</em>',
                fin_raw, re.S)
            if tp:
                try: target_price = float(tp.group(1).replace(",", ""))
                except: pass
        except Exception:
            pass   # finsum 실패해도 main 데이터는 반환

        final_per = per or cop.get(20)
        final_pbr = pbr or cop.get(21)
        final_ey  = (1.0 / final_per * 100) if final_per and final_per > 0 else None
        # 시가총액 최종 폴백: cop anal5 (억원 단위)
        final_mktcap = mktcap or (int(cop[5]) if cop.get(5) else None)

        if not any([final_per, final_pbr, final_mktcap]):
            return None

        return dict(
            per=final_per,            # PER
            eps=eps or cop.get(17),   # EPS (원)
            cns_per=cns_per,          # 추정PER
            cns_eps=cns_eps,          # 추정EPS
            pbr=final_pbr,            # PBR
            dvr=dvr,                  # 배당수익률 (%)
            roe=roe,                  # ROE (%)
            debt_ratio=cop.get(15),   # 부채비율 (%)
            ey=final_ey,              # 이익수익률 1/PER*100
            mktcap=final_mktcap,      # 시가총액 (억원)
            foreign_ratio=frgn,       # 외국인소진율 (%)
            hi52=hi52,                # 52주 최고
            lo52=lo52,                # 52주 최저
            target_price=target_price,# 목표주가
            source="naver",
        )
    except Exception:
        return None


def _fetch_naver_financials(ticker):
    """
    네이버 금융 cop_analysis 테이블 → 최근 분기 재무 지표.
    cop_analysis 지표 번호:
      9=매출액, 10=영업이익, 11=영업이익률%, 12=순이익률%
      13=ROE%, 15=부채비율%, 16=당기순이익BPS, 17=EPS원
    """
    code = _kr_code(ticker)
    try:
        raw = _naver_fetch(
            f"https://finance.naver.com/item/main.naver?code={code}",
            encoding="utf-8"
        )

        cop_section = re.search(r'cop_analysis[\s\S]+?</section>', raw, re.S)
        if not cop_section:
            return None
        section = cop_section.group()

        # 분기 헤더
        periods = re.findall(r'<strong>(\d{4}\.\d{2})</strong>', section)

        # 각 지표행 td 값
        rows_by_anal = {}
        for m in re.finditer(
            r'th_cop_anal(\d+)[^>]*>[\s\S]*?</th>'
            r'((?:[\s\S]*?<td[^>]*class=""[^>]*>[\s\S]*?</td>)+)',
            section, re.S
        ):
            num = int(m.group(1))
            tds = re.findall(r'<td[^>]*class=""[^>]*>\s*([\d\.,\-]+)\s*</td>', m.group(2))
            vals = []
            for v in tds:
                try: vals.append(float(v.replace(",", "")))
                except: vals.append(None)
            rows_by_anal[num] = vals

        if not rows_by_anal or not periods:
            return None

        rows = []
        n = min(len(periods), 4)
        for i in range(n):
            period = periods[i]
            yr = int(period[:4])

            def _v(anal_num, idx=i):
                vals = rows_by_anal.get(anal_num, [])
                v = vals[idx] if idx < len(vals) else None
                return round(v, 1) if v is not None else 0

            rows.append(dict(
                연도=yr,
                기간=period,
                매출액=_v(9),
                영업이익=_v(10),
                영업이익률=_v(11),
                당기순이익=_v(16),
                EPS=_v(17),
                ROE=_v(13),
            ))

        return rows if rows else None
    except Exception:
        return None


def fetch_realtime(ticker, period="10mo"):
    """
    OHLCV 데이터 수집.
    한국주식: ① KRX Open API → ② 네이버 차트 API → ③ yfinance → ④ 샘플
    미국주식: ① 배치 프리패치 캐시 → ② yfinance → ③ 샘플
    ⚡ v5.0: prefetch_us_batch()가 미리 적재한 __raw_{ticker} 캐시 우선 사용
    """
    # ⚡ v5.0 배치 프리패치 캐시 확인 (미국주식 고속 경로)
    raw_key = f"__raw_{ticker}"
    if raw_key in _indicator_cache:
        return _indicator_cache[raw_key]

    # ── 한국 주식: KRX Open API 최우선 ──
    if _is_kr_ticker(ticker):
        try:
            from api_kr import fetch_krx_ohlcv, _krx_available
            if _krx_available():
                result = fetch_krx_ohlcv(ticker, period=365)
                if result and result.get("closes"):
                    return result
        except Exception:
            pass

    # ── 한국 주식: 네이버 차트 API 우선 ──
    if _is_kr_ticker(ticker):
        try:
            result = _fetch_naver_ohlcv(ticker)
            if result:
                return result
        except Exception:
            pass

    # ── yfinance ──
    if HAS_YF:
        try:
            obj = yf.Ticker(ticker)
            df  = obj.history(period=period, interval="1d", auto_adjust=True)
            if df is not None and len(df) > 50:
                # NaN/0 제거: 종가가 유효한 행만 사용
                import math as _math
                valid_mask = [
                    (not _math.isnan(c)) and c > 0 and
                    (not _math.isnan(o)) and o > 0
                    for c, o in zip(df["Close"], df["Open"])
                ]
                if sum(valid_mask) < 30:
                    pass  # 유효 데이터 부족 → 다음 방법으로
                else:
                    df_v = df[valid_mask]
                    return dict(
                        dates   = [d.to_pydatetime() for d in df_v.index],
                        opens   = df_v["Open"].tolist(),
                        highs   = df_v["High"].tolist(),
                        lows    = df_v["Low"].tolist(),
                        closes  = df_v["Close"].tolist(),
                        volumes = df_v["Volume"].tolist(),
                        realtime=True,
                        source  ="yfinance",
                    )
        except Exception:
            pass

    return _sample_ohlcv(ticker)



def is_trading_halted(d):
    """
    OHLCV 딕셔너리를 받아 거래정지 여부 판단.
    반환: (is_halted: bool, reason: str)
    판단 기준:
      ① 최신 데이터가 14일 이상 전 (데이터 단절) — 실시간 데이터만 적용
      ② 최근 5일 거래량 모두 0
      ③ 최근 5일 가격이 완전히 고정 (고저동일)
    """
    from datetime import datetime as _dt
    closes  = d.get("closes",  [])
    volumes = d.get("volumes", [])
    dates   = d.get("dates",   [])
    if not closes or not volumes or not dates:
        return True, "데이터 없음"

    # ① 최신 날짜 확인 — 실시간 데이터만 체크 (샘플 데이터는 스킵)
    is_realtime = d.get("realtime", False)
    if is_realtime:
        last_dt = dates[-1]
        if hasattr(last_dt, "date"):
            last_dt = last_dt.date()
        today = _dt.today().date()
        gap = (today - last_dt).days
        if gap > 14:
            return True, f"마지막 데이터 {gap}일 전 ({last_dt}) — 거래정지 의심"

    # ② 최근 5일 거래량 = 0
    rv = volumes[-5:]
    if rv and all(v == 0 for v in rv):
        return True, "최근 5일 거래량 0 — 거래정지"

    # ③ 최근 5일 가격 완전 고정
    if len(closes) >= 5:
        rc = [round(c) for c in closes[-5:]]
        if len(set(rc)) == 1:
            return True, f"최근 5일 가격 고정 ({closes[-1]:,.0f}) — 거래정지"

    return False, ""

def _sample_ohlcv(ticker, n=260):
    random.seed(abs(hash(ticker)) % 99999)
    is_us = not _is_kr_ticker(ticker)
    base  = random.uniform(20.0, 500.0) if is_us else random.uniform(10000, 300000)
    trend = random.uniform(-0.0005, 0.0012)
    vol   = random.uniform(0.012, 0.025)
    closes = [base]
    for _ in range(n - 1):
        floor = 1.0 if is_us else 500
        closes.append(max(floor, closes[-1] * (1 + random.gauss(trend, vol))))
    opens   = [c * random.uniform(0.985, 1.015) for c in closes]
    highs   = [max(o, c) * random.uniform(1.002, 1.025) for o, c in zip(opens, closes)]
    lows    = [min(o, c) * random.uniform(0.975, 0.998) for o, c in zip(opens, closes)]
    volumes = [int(random.uniform(50000, 2000000)) for _ in range(n)]
    today   = datetime.today()
    dates   = []
    d = today - timedelta(days=int(n * 1.5))
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    return dict(dates=dates[-n:], opens=opens, highs=highs,
                lows=lows, closes=closes, volumes=volumes,
                realtime=False, source="sample")


def fetch_financials(ticker, name):
    """
    재무제표 수집.
    한국주식: ① DART Open API → ② 네이버 금융 → ③ yfinance → ④ 샘플
    미국주식: ① yfinance → ② 샘플
    """
    # ── 한국 주식: DART Open API 최우선 ──
    if _is_kr_ticker(ticker):
        try:
            from api_kr import fetch_dart_financials, _dart_available
            if _dart_available():
                dart_result = fetch_dart_financials(ticker)
                if dart_result and len(dart_result) > 2:
                    return dart_result
        except Exception:
            pass

    # ── 한국 주식: 네이버 금융 ──
    if _is_kr_ticker(ticker):
        try:
            rows = _fetch_naver_financials(ticker)
            if rows and len(rows) >= 2:
                return rows
        except Exception:
            pass

    # ── yfinance ──
    if HAS_YF:
        try:
            obj = yf.Ticker(ticker)
            inc = obj.financials
            bal = obj.balance_sheet
            if inc is not None and not inc.empty:
                years = sorted(inc.columns, reverse=True)[:5]
                rows  = []
                for col in years:
                    yr  = col.year if hasattr(col, "year") else int(str(col)[:4])
                    rev = inc.loc["Total Revenue",    col] / 1e8 if "Total Revenue"    in inc.index else None
                    op  = inc.loc["Operating Income", col] / 1e8 if "Operating Income" in inc.index else None
                    ni  = inc.loc["Net Income",       col] / 1e8 if "Net Income"       in inc.index else None
                    eps = inc.loc["Basic EPS",        col]        if "Basic EPS"        in inc.index else None
                    roe = None
                    if bal is not None and col in bal.columns:
                        eq = bal.loc["Stockholders Equity", col] if "Stockholders Equity" in bal.index else None
                        if eq and ni and eq != 0:
                            roe = round(ni * 1e8 / eq * 100, 1)
                    rows.append(dict(연도=yr,
                                     매출액=round(rev, 0) if rev else 0,
                                     영업이익=round(op, 0) if op else 0,
                                     당기순이익=round(ni, 0) if ni else 0,
                                     EPS=round(eps, 0) if eps else 0,
                                     ROE=roe if roe else 0))
                if rows:
                    return rows
        except Exception:
            pass

    return _sample_financials(name)



def fetch_quarterly_financials(ticker):
    """
    분기별 재무제표 — 최근 16분기(4년) 수집.
    반환: list of {기간, 매출액, 영업이익, 영업이익률, 당기순이익, EPS}
    QoQ(전기대비), YoY(전년동기대비) 증감률 포함.
    """
    code = _kr_code(ticker)

    def _pct(new, old):
        if old and old != 0 and new is not None:
            return round((new - old) / abs(old) * 100, 1)
        return None

    # ── 한국주식: 네이버 금융 분기 데이터 ────────────────────────────────
    if _is_kr_ticker(ticker):
        try:
            # 분기별 재무 페이지
            raw = _naver_fetch(
                f"https://finance.naver.com/item/coinfo.naver"
                f"?code={code}&target=finsum_more",
                encoding="utf-8"
            )
            # 연간/분기 재무 테이블 파싱
            # cop_analysis 분기탭 접근
            raw2 = _naver_fetch(
                f"https://finance.naver.com/item/main.naver?code={code}",
                encoding="utf-8"
            )
            cop = re.search(r'cop_analysis[\s\S]+?</section>', raw2, re.S)
            if not cop:
                raise ValueError("cop_analysis 없음")
            section = cop.group()
            periods = re.findall(r'<strong>(\d{4}\.\d{2})</strong>', section)
            rows_by_anal = {}
            for m in re.finditer(
                r'th_cop_anal(\d+)[^>]*>[\s\S]*?</th>'
                r'((?:[\s\S]*?<td[^>]*class=""[^>]*>[\s\S]*?</td>)+)',
                section, re.S
            ):
                num = int(m.group(1))
                tds = re.findall(r'<td[^>]*class=""[^>]*>\s*([\d\.,\-]+)\s*</td>', m.group(2))
                rows_by_anal[num] = []
                for v in tds:
                    try: rows_by_anal[num].append(float(v.replace(",","")))
                    except: rows_by_anal[num].append(None)

            if not periods or not rows_by_anal:
                raise ValueError("파싱 실패")

            def _v(anal_num, idx):
                vals = rows_by_anal.get(anal_num, [])
                v = vals[idx] if idx < len(vals) else None
                return round(v, 1) if v is not None else None

            raw_rows = []
            for i, period in enumerate(periods[:16]):
                raw_rows.append({
                    "기간": period,
                    "매출액": _v(9, i),
                    "영업이익": _v(10, i),
                    "영업이익률": _v(11, i),
                    "당기순이익": _v(16, i),
                    "EPS": _v(17, i),
                })

            # QoQ / YoY 계산
            for i, row in enumerate(raw_rows):
                row["QoQ매출"] = _pct(row["매출액"], raw_rows[i-1]["매출액"]) if i >= 1 else None
                row["YoY매출"] = _pct(row["매출액"], raw_rows[i-4]["매출액"]) if i >= 4 else None
                row["QoQ영업"] = _pct(row["영업이익"], raw_rows[i-1]["영업이익"]) if i >= 1 else None
                row["YoY영업"] = _pct(row["영업이익"], raw_rows[i-4]["영업이익"]) if i >= 4 else None
                row["QoQ순이익"] = _pct(row["당기순이익"], raw_rows[i-1]["당기순이익"]) if i >= 1 else None
                row["YoY순이익"] = _pct(row["당기순이익"], raw_rows[i-4]["당기순이익"]) if i >= 4 else None

            return raw_rows if raw_rows else None
        except Exception:
            pass

    # ── yfinance 분기 데이터 ──────────────────────────────────────────────
    if HAS_YF:
        try:
            obj = yf.Ticker(ticker)
            inc_q = obj.quarterly_financials
            if inc_q is not None and not inc_q.empty:
                cols = sorted(inc_q.columns, reverse=True)[:16]
                raw_rows = []
                for col in cols:
                    period = col.strftime("%Y.%m") if hasattr(col, "strftime") else str(col)[:7]
                    rev = (inc_q.loc["Total Revenue",    col] / 1e8) if "Total Revenue"    in inc_q.index else None
                    op  = (inc_q.loc["Operating Income", col] / 1e8) if "Operating Income" in inc_q.index else None
                    ni  = (inc_q.loc["Net Income",       col] / 1e8) if "Net Income"       in inc_q.index else None
                    op_margin = round(op / rev * 100, 1) if (rev and op and rev != 0) else None
                    raw_rows.append({
                        "기간": period,
                        "매출액": round(rev, 1) if rev else None,
                        "영업이익": round(op, 1) if op else None,
                        "영업이익률": op_margin,
                        "당기순이익": round(ni, 1) if ni else None,
                        "EPS": None,
                    })
                # QoQ / YoY
                for i, row in enumerate(raw_rows):
                    row["QoQ매출"] = _pct(row["매출액"], raw_rows[i-1]["매출액"]) if i >= 1 else None
                    row["YoY매출"] = _pct(row["매출액"], raw_rows[i-4]["매출액"]) if i >= 4 else None
                    row["QoQ영업"] = _pct(row["영업이익"], raw_rows[i-1]["영업이익"]) if i >= 1 else None
                    row["YoY영업"] = _pct(row["영업이익"], raw_rows[i-4]["영업이익"]) if i >= 4 else None
                    row["QoQ순이익"] = _pct(row["당기순이익"], raw_rows[i-1]["당기순이익"]) if i >= 1 else None
                    row["YoY순이익"] = _pct(row["당기순이익"], raw_rows[i-4]["당기순이익"]) if i >= 4 else None
                return raw_rows
        except Exception:
            pass

    # ── 샘플 ─────────────────────────────────────────────────────────────
    return _sample_quarterly(ticker)


def _sample_quarterly(ticker):
    random.seed(abs(hash(ticker + "qfin")) % 77777)
    base = random.uniform(500, 20000)
    g    = random.uniform(0.02, 0.15)
    rows = []
    today = datetime.today()
    for q in range(15, -1, -1):
        mo = today.month - (q % 4) * 3
        yr = today.year  - q // 4
        while mo <= 0: mo += 12; yr -= 1
        period = f"{yr}.{mo:02d}"
        rev = base * (1 + g) ** (15 - q) * random.uniform(0.85, 1.15)
        op  = rev * random.uniform(0.08, 0.22)
        ni  = op  * random.uniform(0.55, 0.90)
        rows.append({
            "기간": period,
            "매출액": round(rev, 1),
            "영업이익": round(op, 1),
            "영업이익률": round(op / rev * 100, 1),
            "당기순이익": round(ni, 1),
            "EPS": round(random.uniform(100, 5000), 0),
            "QoQ매출": None, "YoY매출": None,
            "QoQ영업": None, "YoY영업": None,
            "QoQ순이익": None, "YoY순이익": None,
        })
    # 증감률 계산
    def _pct(a, b):
        if a and b and b != 0: return round((a - b) / abs(b) * 100, 1)
        return None
    for i, row in enumerate(rows):
        row["QoQ매출"] = _pct(row["매출액"], rows[i-1]["매출액"]) if i >= 1 else None
        row["YoY매출"] = _pct(row["매출액"], rows[i-4]["매출액"]) if i >= 4 else None
        row["QoQ영업"] = _pct(row["영업이익"], rows[i-1]["영업이익"]) if i >= 1 else None
        row["YoY영업"] = _pct(row["영업이익"], rows[i-4]["영업이익"]) if i >= 4 else None
        row["QoQ순이익"] = _pct(row["당기순이익"], rows[i-1]["당기순이익"]) if i >= 1 else None
        row["YoY순이익"] = _pct(row["당기순이익"], rows[i-4]["당기순이익"]) if i >= 4 else None
    return rows

def _sample_financials(name):
    random.seed(abs(hash(name + "fin")) % 88888)
    base_rev = random.uniform(2000, 80000)
    base_op  = base_rev * random.uniform(0.06, 0.22)
    base_ni  = base_op  * random.uniform(0.55, 0.90)
    base_eps = random.uniform(500, 15000)
    rows = []
    for i, yr in enumerate([2020, 2021, 2022, 2023, 2024]):
        g = random.uniform(-0.05, 0.30)
        rev = base_rev * (1 + g) ** i
        op  = base_op  * (1 + g * random.uniform(0.8, 1.6)) ** i
        ni  = base_ni  * (1 + g * random.uniform(0.7, 1.4)) ** i
        eps = base_eps * (1 + g * 0.8) ** i
        roe = ni / (rev * random.uniform(0.25, 0.55)) * 100
        rows.append(dict(연도=yr, 매출액=round(rev, 0),
                         영업이익=round(op, 0), 당기순이익=round(ni, 0),
                         EPS=round(eps, 0), ROE=round(roe, 1)))
    return rows


# ──────────────────────────────────────────────
#  실제 시장 데이터 기반 지표 계산
#  yfinance 설치 시 실데이터, 미설치 시 샘플 폴백
# ──────────────────────────────────────────────

# 종목별 지표 캐시 (같은 실행 내 중복 API 호출 방지)
_indicator_cache = {}


# _get_indicators 는 indicators.py 에 정의됨 — 여기서는 임포트 불필요
# (sma/bollinger/rsi_calc 등을 사용하므로 indicators.py에 위치)

def _get_financial_indicators(ticker):
    """
    재무 지표 조회.
    한국주식: ① 네이버 금융 메인 (PER/PBR/ROE/EPS/EY) → ② yfinance
    미국주식: ① yfinance
    실패 시 None 반환 → 호출측에서 모멘텀 폴백 처리.
    """
    # ── 한국 주식: 네이버 금융 우선 ──
    if _is_kr_ticker(ticker):
        try:
            nv = _fetch_naver_indicators(ticker)
            if nv:
                per = nv.get("per")
                roe = nv.get("roe")
                ey  = nv.get("ey")
                # EPS 성장률은 네이버에서 직접 제공 안 됨 → 모멘텀으로 대체
                return dict(
                    pe=per,
                    eps_ttm=nv.get("eps"),
                    eps_fwd=None,
                    eps_growth=None,          # 별도 조회 필요
                    ey=ey,
                    roe=roe,
                    roic=roe,                 # ROE를 ROIC 근사치로 사용
                    rev_growth=None,
                    earn_growth=None,
                    inst_pct=nv.get("foreign_ratio"),  # 외국인비율 대체
                    hi52=nv.get("hi52"),
                    lo52=nv.get("lo52"),
                    mktcap=nv.get("mktcap"),
                    source="naver",
                )
        except Exception:
            pass

    # ── yfinance (한국 폴백 + 미국 주식) ──
    if not HAS_YF:
        return None
    try:
        obj  = yf.Ticker(ticker)
        info = obj.info
        if not info:
            return None

        pe          = info.get("trailingPE")
        eps_ttm     = info.get("trailingEps")
        eps_fwd     = info.get("forwardEps")
        eps_growth  = ((eps_fwd - eps_ttm) / abs(eps_ttm) * 100
                       if eps_fwd and eps_ttm and eps_ttm != 0 else None)
        ey          = (1.0 / pe * 100) if pe and pe > 0 else None
        roe         = info.get("returnOnEquity")
        roic        = info.get("returnOnAssets")
        rev_growth  = info.get("revenueGrowth")
        earn_growth = info.get("earningsGrowth")
        inst_pct    = (info.get("institutionalOwnershipPercentage") or
                       info.get("institutionHoldingsPercentage") or
                       info.get("heldPercentInstitutions"))
        return dict(
            pe=pe, eps_ttm=eps_ttm, eps_fwd=eps_fwd,
            eps_growth=eps_growth,
            ey=ey,
            roe=roe * 100 if roe else None,
            roic=roic * 100 if roic else None,
            rev_growth=rev_growth * 100 if rev_growth else None,
            earn_growth=earn_growth * 100 if earn_growth else None,
            inst_pct=inst_pct * 100 if inst_pct else None,
            source="yfinance",
        )
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────
# 1. 조엘 그린블라트 마법공식 (Magic Formula)
# ──────────────────────────────────────────────────────────────
# ═══ [A] _fetch_greenblatt_data 는 아래 "전략 v4" 섹션에 파라미터 기반으로 정의됨 ═══

