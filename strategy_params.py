#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
strategy_params.py v8 — 한국/미국 시장 최적화된 전략 파라미터
=====================================================================
설계 원칙:
  · KRW 시장: 유동성이 낮은 중소형주 비중이 높아 필터 완화 필요
    - 시가총액: 500억원 이상 (코스닥 현실 반영)
    - 거래대금: 10억원/일 이상
    - RS 기준: 미국보다 10점 낮게 (시장 비효율성)
  · USD 시장: 효율적이고 유동성 풍부, 더 엄격한 기준 적용 가능
    - 시가총액: 1B$ 이상 (대형주 중심)
    - 거래대금: 5M$/일 이상
    - RS 기준: 70점 이상 (상위 30% 종목만)
  · VCP(미너비니): 보너스가 아닌 하드필터로 적용
    - 3개 이상 수축 파동 + Higher Lows 필수
    - Tightness < 20% 필수
=====================================================================
"""

STRATEGY_PARAMS_DEFAULT = {

    # ══════════════════════════════════════════════════════════════════
    # 1. 조엘 그린블라트 — 마법공식 (ROC + EY)
    # KR: 시총 500억+, ROC 10%+, EY 5%+
    # US: 시총 500M$+, ROC 15%+, EY 4%+
    # ══════════════════════════════════════════════════════════════════
    "조엘 그린블라트": {
        "min_mktcap_billion":  {"label":"최소 시가총액","label_usd":"최소 시가총액 (M$)",
                                 "unit":"억원","unit_usd":"M$",
                                 "val":500,"val_usd":500,"min":0,"max":50000,"step":100,"step_usd":100,"fmt":"float"},
        "min_roc_pct":         {"label":"최소 ROC (%)","val":10,"min":0,"max":50,"step":1,"fmt":"float",
                                 "info":"KR 10% / US 15% 권장"},
        "min_ey_pct":          {"label":"최소 EY % (=1/PER)","val":5,"min":0,"max":30,"step":0.5,"fmt":"float",
                                 "info":"PER 20배 이하 = EY 5%"},
        "min_rsi":             {"label":"최소 RSI (급락 제외)","val":25,"min":10,"max":60,"step":1,"fmt":"int"},
        "min_mom3m_pct":       {"label":"3개월 수익률 하한 (%)","val":-30,"min":-50,"max":0,"step":5,"fmt":"float"},
        "exclude_finance":     {"label":"금융·유틸리티주 제외","val":True,"fmt":"bool"},
        "━━ 공통 품질 필터":  {"label":"── 공통 품질 필터 ──","val":0,"fmt":"section"},
        "require_ma_align":    {"label":"MA20>MA60>MA120 정배열 필수 (하드 필터)",
                                 "val":True,"fmt":"bool",
                                 "info":"True=역배열 종목 즉시 탈락. BB수축은 상승 추세 안에서만 의미"},
        "min_trading_value":   {"label":"최소 거래대금","label_usd":"최소 거래대금 (M$)",
                                 "unit":"억원","unit_usd":"M$",
                                 "val":10,"val_usd":2.0,"min":0,"max":1000,"step":5,"step_usd":0.5,"fmt":"float"},
        "weekly_filter":       {"label":"주봉 MA10 위 확인 (멀티타임프레임)","val":False,"fmt":"bool"},
        "━━ 점수 가중치":     {"label":"── 점수 가중치 ──","val":0,"fmt":"section"},
        "weight_roc":          {"label":"ROC 등수 가중치","val":50,"min":0,"max":100,"step":5,"fmt":"int","ai":50},
        "weight_ey":           {"label":"EY 등수 가중치","val":50,"min":0,"max":100,"step":5,"fmt":"int","ai":50},
    },

    # ══════════════════════════════════════════════════════════════════
    # 2. 미너비니 — SEPA + VCP 하드필터
    # 핵심 변경: VCP는 보너스가 아닌 하드 필터
    # KR: RS 60+, 52주저점+25%+, 고점-25%이내
    # US: RS 75+, 52주저점+30%+, 고점-20%이내
    # VCP: 수축파동 2개+, Tightness<20%, Higher Lows 필수
    # ══════════════════════════════════════════════════════════════════
    "미너비니": {
        "min_rs":              {"label":"최소 RS 점수 (1~99)","val":65,"min":0,"max":99,"step":1,"fmt":"int",
                                 "info":"KR 65 / US 75 권장 (IBD 백분위)"},
        "min_from_52l_pct":    {"label":"52주저점 최소반등 (%)","val":25,"min":10,"max":80,"step":5,"fmt":"float"},
        "max_from_52h_pct":    {"label":"52주고점 최대하락 (%)","val":25,"min":5,"max":60,"step":1,"fmt":"float"},
        "min_eps_growth":      {"label":"최소 분기EPS 성장 (%)","val":15,"min":0,"max":200,"step":5,"fmt":"float",
                                 "info":"0이면 미적용"},
        "min_rev_growth":      {"label":"최소 매출 성장 (%)","val":10,"min":0,"max":100,"step":5,"fmt":"float",
                                 "info":"0이면 미적용"},
        "━━ VCP 하드필터":    {"label":"── VCP 패턴 (필수 통과) ──","val":0,"fmt":"section"},
        "vcp_required":        {"label":"VCP 필수 여부","val":True,"fmt":"bool"},
        "vcp_min_waves":       {"label":"최소 수축 파동 수","val":2,"min":1,"max":5,"step":1,"fmt":"int"},
        "vcp_tightness_pct":   {"label":"최대 Tightness (%)","val":20.0,"min":5,"max":40,"step":1,"fmt":"float"},
        "vdu_required":        {"label":"VDU(거래량 감소) 필수","val":True,"fmt":"bool"},
        "vdu_ratio":           {"label":"VDU 기준 배율","val":0.85,"min":0.3,"max":0.99,"step":0.05,"fmt":"float"},
        "vcp_hl_required":     {"label":"Higher Lows 필수","val":False,"fmt":"bool"},
        "━━ 공통 품질 필터":  {"label":"── 공통 품질 필터 ──","val":0,"fmt":"section"},
        "min_trading_value":   {"label":"최소 거래대금","label_usd":"최소 거래대금 (M$)",
                                 "unit":"억원","unit_usd":"M$",
                                 "val":15,"val_usd":3.0,"min":0,"max":1000,"step":5,"step_usd":0.5,"fmt":"float"},
        "min_cf_quality":      {"label":"이익의 질 최소비율 (0=미적용)","val":0,"min":0,"max":200,"step":5,"fmt":"float"},
        "weekly_filter":       {"label":"주봉 MA10 위 확인 (멀티타임프레임)","val":False,"fmt":"bool"},
        "━━ 점수 가중치":     {"label":"── 점수 가중치 ──","val":0,"fmt":"section"},
        "weight_rs":           {"label":"RS 점수 가중치","val":45,"min":0,"max":100,"step":5,"fmt":"int","ai":45},
        "weight_eps":          {"label":"EPS 점수 가중치","val":30,"min":0,"max":100,"step":5,"fmt":"int","ai":30},
        "weight_rev":          {"label":"매출 점수 가중치","val":15,"min":0,"max":100,"step":5,"fmt":"int","ai":15},
        "weight_vol":          {"label":"VCP 보너스 가중치","val":10,"min":0,"max":100,"step":5,"fmt":"int","ai":10},
    },

    # ══════════════════════════════════════════════════════════════════
    # 3. 윌리엄 오닐 — CAN SLIM
    # KR: 분기EPS 15%+, 연간EPS 20%+, ROE 10%+
    # US: 분기EPS 25%+, 연간EPS 25%+, ROE 15%+
    # ══════════════════════════════════════════════════════════════════
    "윌리엄오닐": {
        "━━ 이평선 정배열 필터":  {"label":"── 이평선 하드 필터 (7조건 모두 필수) ──","val":0,"fmt":"section"},
        "min_from_52l_pct":     {"label":"52주 저점 대비 최소 상승 (%)",
                                 "val":30,"min":0,"max":100,"step":5,"fmt":"float",
                                 "info":"오닐: 52주 저점 대비 +30% 이상 — 바닥 완전 탈출 확인"},
        "max_from_52h_pct":     {"label":"52주 고점 대비 최대 하락 (%)",
                                 "val":25,"min":5,"max":50,"step":5,"fmt":"float",
                                 "info":"오닐: 52주 고점 -25% 이내 — 신고가 권내 확인"},
        "━━ L 상대강도 필터":    {"label":"── L: 상대강도 최소 기준 ──","val":0,"fmt":"section"},
        "min_rs":               {"label":"최소 RS 백분위 (%)",
                                 "val":70,"min":0,"max":99,"step":5,"fmt":"int",
                                 "info":"오닐: 상위 20%(RS≥80) 권장. 현실적 기준 70% 이상"},
        "━━ S 수급 기준":        {"label":"── S: 수급·거래량 ──","val":0,"fmt":"section"},
        "min_vol_ratio":        {"label":"최소 거래량 배율 (20일 평균 대비)",
                                 "val":1.5,"min":0.5,"max":5.0,"step":0.1,"fmt":"float",
                                 "info":"오닐: 1.5~2배 이상 (수급 확인)"},
        "min_trading_value":    {"label":"최소 거래대금 (억원/일)",
                                 "label_usd":"최소 거래대금 (M$)",
                                 "unit":"억원","unit_usd":"M$",
                                 "val":20,"val_usd":1.0,"min":0,"max":500,"step":10,"step_usd":0.5,"fmt":"float",
                                 "info":"문서 권장: 20억~50억 이상 (기관 관심 최소 기준)"},
        "━━ N 베이스 패턴":      {"label":"── N: 베이스 패턴 감지 ──","val":0,"fmt":"section"},
        "flat_base_pct":        {"label":"Flat Base 기준 변동폭 (35일 %)",
                                 "val":15,"min":5,"max":30,"step":1,"fmt":"float",
                                 "info":"35일 고저폭이 이 % 이하면 Flat Base / Tight Consolidation 판단"},
        "━━ 리스크 관리":        {"label":"── 손절 기준 ──","val":0,"fmt":"section"},
        "stop_loss_pct":        {"label":"손절 비율 (%)",
                                 "val":8,"min":3,"max":15,"step":0.5,"fmt":"float",
                                 "info":"오닐 원칙: -7~-8%. 이 이상 하락 시 즉시 손절"},
        "━━ 공통 필터":          {"label":"── 공통 품질 필터 ──","val":0,"fmt":"section"},
        "weekly_filter":        {"label":"주봉 MA10 위 확인 (멀티타임프레임)","val":False,"fmt":"bool"},
    },
    "쿨라매기": {
        "━━ 잡주 제거":        {"label":"── 1단계: 잡주 제거 ──","val":0,"fmt":"section"},
        "min_price":           {"label":"최소 주가 (원/KRW)","unit":"원",
                                "val":5000,"min":0,"max":50000,"step":500,"fmt":"int",
                                "info":"문서 권장: 5,000원 이상 (시가총액·유동성 최소 기준)"},
        "min_trading_value":   {"label":"최소 거래대금 (억원/일)",
                                "label_usd":"최소 거래대금 (M$)",
                                "unit":"억원","unit_usd":"M$",
                                "val":50,"val_usd":2.0,"min":0,"max":500,"step":10,"step_usd":0.5,"fmt":"float",
                                "info":"문서 권장: 50억~100억 이상. 유동성 부족 잡주 제거"},
        "━━ 이평선 필터":      {"label":"── 쿨라매기 핵심: 이동평균선 정배열 ──","val":0,"fmt":"section"},
        "require_ma_align":    {"label":"이평 정배열 필수 (cur > 20EMA > 50SMA)",
                                "val":True,"fmt":"bool",
                                "info":"쿨라매기 핵심. True=역배열 종목 즉시 탈락. False=점수 반영만"},
        "━━ Breakout 설정":    {"label":"── A. Breakout 셋업 ──","val":0,"fmt":"section"},
        "min_mom3m_pct":       {"label":"최소 60일 수익률 (%) — 강한 기반 확인",
                                "val":10,"min":-30,"max":200,"step":5,"fmt":"float",
                                "info":"60일 수익률 기준. 약세장에선 0~10%로 낮춤"},
        "max_from_52h_pct":    {"label":"52주 고점 허용 낙폭 (%) — 이 이상이면 제외",
                                "val":25,"min":5,"max":50,"step":5,"fmt":"float",
                                "info":"기본 -25% 이내. 약세장엔 -30%까지 허용"},
        "vcp_tightness_pct":   {"label":"박스권 압축 기준 (변동폭 %)",
                                "val":15,"min":3,"max":30,"step":1,"fmt":"float",
                                "info":"이 % 이하이면 변동성 압축 상태"},
        "min_vol_ratio":       {"label":"Breakout 최소 거래량 배율 (20일 평균 대비)",
                                "val":1.5,"min":0.5,"max":5.0,"step":0.1,"fmt":"float",
                                "info":"문서 권장: 1.5배 이상"},
        "━━ EP 설정":          {"label":"── B. EP (Episodic Pivot) 셋업 ──","val":0,"fmt":"section"},
        "min_gap_pct":         {"label":"EP 최소 갭상승 (%) ",
                                "val":3.0,"min":1.0,"max":20.0,"step":0.5,"fmt":"float",
                                "info":"기본 +3%. 강한 EP는 +5% 이상 권장"},
        "min_vol_ep_mult":     {"label":"EP 최소 거래량 배율 (20일 평균 대비)",
                                "val":2.0,"min":1.0,"max":10.0,"step":0.5,"fmt":"float",
                                "info":"기본 2배. 강한 EP는 3배 이상 권장"},
        "━━ RS 기준":          {"label":"── 상대강도 필터 ──","val":0,"fmt":"section"},
        "min_rs":              {"label":"최소 RS 백분위 (%)",
                                "val":60,"min":0,"max":99,"step":5,"fmt":"int",
                                "info":"문서: 상위 40% 이내. 시장 주도주만 선별"},
        "━━ 공통 필터":        {"label":"── 공통 품질 필터 ──","val":0,"fmt":"section"},
        "weekly_filter":       {"label":"주봉 MA10 위 확인 (멀티타임프레임)","val":False,"fmt":"bool"},
    },
    "오닐+미너비니": {
        "min_mktcap_billion":  {"label":"최소 시가총액","label_usd":"최소 시가총액 (M$)",
                                 "unit":"억원","unit_usd":"M$",
                                 "val":500,"val_usd":500,"min":0,"max":50000,"step":100,"step_usd":100,"fmt":"float"},
        "min_eps_growth":      {"label":"최소 분기EPS 성장 (%)","val":20,"min":0,"max":200,"step":5,"fmt":"float"},
        "min_ann_growth":      {"label":"최소 연간EPS 성장 (%)","val":20,"min":0,"max":200,"step":5,"fmt":"float"},
        "min_roe":             {"label":"최소 ROE (%)","val":12,"min":0,"max":50,"step":1,"fmt":"float"},
        "min_rs":              {"label":"최소 RS 점수","val":70,"min":0,"max":99,"step":1,"fmt":"int"},
        "min_vol_ratio":       {"label":"최소 거래량 배율","val":1.0,"min":0,"max":5.0,"step":0.1,"fmt":"float"},
        "max_from_52h_pct":    {"label":"52주고점 최대하락 (%)","val":25,"min":5,"max":60,"step":1,"fmt":"float"},
        "min_from_52l_pct":    {"label":"52주저점 최소반등 (%)","val":25,"min":0,"max":80,"step":5,"fmt":"float"},
        "vcp_min_waves":       {"label":"VCP 최소 파동 (보너스용)","val":2,"min":1,"max":5,"step":1,"fmt":"int"},
        "vcp_tightness_pct":   {"label":"VCP Tightness 기준 (%)","val":20,"min":1,"max":50,"step":1,"fmt":"float"},
        "vdu_ratio":           {"label":"VDU 거래량 기준 (배율)","val":0.85,"min":0.1,"max":0.99,"step":0.05,"fmt":"float"},
        "━━ 공통 품질 필터":  {"label":"── 공통 품질 필터 ──","val":0,"fmt":"section"},
        "min_trading_value":   {"label":"최소 거래대금","label_usd":"최소 거래대금 (M$)",
                                 "unit":"억원","unit_usd":"M$",
                                 "val":15,"val_usd":5.0,"min":0,"max":1000,"step":5,"step_usd":0.5,"fmt":"float"},
        "min_cf_quality":      {"label":"이익의 질 최소비율 (0=미적용)","val":0,"min":0,"max":200,"step":5,"fmt":"float"},
        "weekly_filter":       {"label":"주봉 MA10 위 확인 (멀티타임프레임)","val":False,"fmt":"bool"},
        "━━ 점수 가중치":     {"label":"── 점수 가중치 ──","val":0,"fmt":"section"},
        "weight_rs":           {"label":"RS 점수 가중치","val":30,"min":0,"max":100,"step":5,"fmt":"int","ai":30},
        "weight_eps":          {"label":"EPS 점수 가중치","val":25,"min":0,"max":100,"step":5,"fmt":"int","ai":25},
        "weight_ma":           {"label":"이평선배열 가중치","val":25,"min":0,"max":100,"step":5,"fmt":"int","ai":25},
        "weight_vol":          {"label":"거래량 점수 가중치","val":10,"min":0,"max":100,"step":5,"fmt":"int","ai":10},
        "weight_vcp":          {"label":"VCP보너스 가중치","val":10,"min":0,"max":100,"step":5,"fmt":"int","ai":10},
    },

    # ══════════════════════════════════════════════════════════════════
    # 6. RSI+MACD+BB — 3지표 합치 반전·스윙 전략
    # 매수: BB하단 터치 + RSI 과매도 + MACD 골든크로스 + MA200 위
    # 매도: BB상단 + RSI 과매수 + MACD 데드크로스
    # KR: 시총 300억+, RSI 25~38, 거래량 확인
    # US: 시총 300M$+, RSI 25~38, 거래량 확인
    # ══════════════════════════════════════════════════════════════════
    "RSI+MACD+BB": {
        "━━ 잡주 제거":        {"label":"── 1단계: 잡주 제거 ──","val":0,"fmt":"section"},
        "min_price":           {"label":"최소 주가 (원/KRW)",
                                "unit":"원","val":3000,"min":0,"max":50000,"step":500,"fmt":"int"},
        "min_trading_value_rmb":{"label":"최소 거래대금 (억원/일)",
                                 "label_usd":"최소 거래대금 (M$)",
                                 "unit":"억원","unit_usd":"M$",
                                 "val":30,"val_usd":1.0,"min":0,"max":500,"step":10,"step_usd":0.5,"fmt":"float",
                                 "info":"문서 권장: 30억 이상 (저유동성 잡주 제거)"},
        "━━ 추세 필터":        {"label":"── 2단계: 기본 추세 (MA20>MA60) ──","val":0,"fmt":"section"},
        "require_trend":       {"label":"MA20>MA60 + 현재가>MA20 필수","val":True,"fmt":"bool",
                                "info":"True 시 기본 추세 미충족 종목 즉시 탈락"},
        "require_above_ma200": {"label":"MA200 위 권장 (아래이면 점수 감점)",
                                "val":True,"fmt":"bool"},
        "━━ RSI 설정":         {"label":"── 3단계: RSI 조건 ──","val":0,"fmt":"section"},
        "rsi_period":          {"label":"RSI 기간","val":14,"min":5,"max":21,"step":1,"fmt":"int"},
        "rsi_low":             {"label":"RSI 하한 (이하 = 너무 약함)",
                                "val":45.0,"min":30.0,"max":55.0,"step":1.0,"fmt":"float",
                                "info":"문서 권장: RSI 45 이상 (너무 약한 종목 제외)"},
        "rsi_high":            {"label":"RSI 상한 (이상 = 과열)",
                                "val":65.0,"min":55.0,"max":80.0,"step":1.0,"fmt":"float",
                                "info":"문서 권장: RSI 65 이하 (과열 종목 제외)"},
        "rsi_oversold_max":    {"label":"과매도 특례 기준 (이하 = 반등 기대)",
                                "val":38.0,"min":20.0,"max":50.0,"step":1.0,"fmt":"float"},
        "━━ MACD 설정":        {"label":"── 4단계: MACD 조건 ──","val":0,"fmt":"section"},
        "macd_require_cross":  {"label":"골든크로스 필수 여부","val":False,"fmt":"bool",
                                "info":"False = 히스토그램 증가만으로도 신호 인정"},
        "━━ 볼린저밴드":       {"label":"── 5단계: 볼린저밴드 ──","val":0,"fmt":"section"},
        "bb_period":           {"label":"BB 기간","val":20,"min":10,"max":30,"step":1,"fmt":"int"},
        "bb_std":              {"label":"BB 표준편차 배수","val":2.0,"min":1.0,"max":3.0,"step":0.1,"fmt":"float"},
        "━━ 거래량":           {"label":"── 6단계: 거래량/거래대금 ──","val":0,"fmt":"section"},
        "min_vol_ratio_buy":   {"label":"최소 거래량 배율 (20일 평균 대비)",
                                "val":1.2,"min":0.5,"max":5.0,"step":0.1,"fmt":"float",
                                "info":"문서 권장: 1.2배 이상"},
        "━━ 공통 필터":        {"label":"── 공통 ──","val":0,"fmt":"section"},
        "min_trading_value":   {"label":"최소 거래대금 (공통)",
                                "label_usd":"최소 거래대금 (M$)",
                                "unit":"억원","unit_usd":"M$",
                                "val":30,"val_usd":1.0,"min":0,"max":500,"step":10,"step_usd":0.5,"fmt":"float"},
        "weekly_filter":       {"label":"주봉 MA10 위 확인","val":False,"fmt":"bool"},
    },
    
    "스윙 투자": {
        "min_roe":             {"label":"최소 ROE (%)","val":8,"min":0,"max":50,"step":1,"fmt":"float"},
        "max_debt_ratio":      {"label":"최대 부채비율 (%)","val":150,"min":0,"max":500,"step":10,"fmt":"float"},
        "max_pbr":             {"label":"최대 PBR (배)","val":5.0,"min":0,"max":30,"step":0.5,"fmt":"float"},
        "max_from_52h_pct":    {"label":"52주고점 최대하락 (%)","val":30,"min":5,"max":80,"step":1,"fmt":"float",
                                 "info":"신고가 -20%이내"},
        "rsi_min":             {"label":"RSI 최솟값 (에너지충전)","val":35,"min":20,"max":70,"step":1,"fmt":"int"},
        "rsi_max":             {"label":"RSI 최댓값 (과매수경계)","val":70,"min":30,"max":85,"step":1,"fmt":"int"},
        "bb_pct_rank":         {"label":"BB수축 하위 % 기준","val":35,"min":5,"max":60,"step":5,"fmt":"int",
                                 "info":"하위 25%=강한 수축"},
        "vdu_ratio":           {"label":"거래량 바닥 기준 (배율)","val":0.70,"min":0.1,"max":0.95,"step":0.05,"fmt":"float"},
        "━━ 공통 품질 필터":  {"label":"── 공통 품질 필터 ──","val":0,"fmt":"section"},
        "min_trading_value":   {"label":"최소 거래대금","label_usd":"최소 거래대금 (M$)",
                                 "unit":"억원","unit_usd":"M$",
                                 "val":10,"val_usd":2.0,"min":0,"max":1000,"step":5,"step_usd":0.5,"fmt":"float"},
        "min_cf_quality":      {"label":"이익의 질 최소비율 (0=미적용)","val":0,"min":0,"max":200,"step":5,"fmt":"float"},
        "weekly_filter":       {"label":"주봉 MA10 위 확인 (멀티타임프레임)","val":False,"fmt":"bool"},
    },

    # ══════════════════════════════════════════════════════════════════
    # 8. SW (Swing Wave) — 합치(Confluence) 돌파
    # US 전용: $10~$150, 거래량 50만주+, 합치 2개+
    # KR: 5,000~200,000원, 거래량 30만주+
    # ══════════════════════════════════════════════════════════════════
    "SW": {
        "━━ 잡주 제거 필터":    {"label":"── 1단계: 잡주 제거 ──","val":0,"fmt":"section"},
        "min_trading_value":    {"label":"최소 일평균 거래대금 (억원)",
                                 "label_usd":"최소 일평균 거래대금 (M$)",
                                 "unit":"억원","unit_usd":"M$",
                                 "val":50,"val_usd":2.0,
                                 "min":0,"max":500,"step":10,"step_usd":0.5,"fmt":"float",
                                 "info":"문서 권장: 50억~100억 이상. 유동성 부족 종목 제거"},
        "━━ 추세 템플릿":       {"label":"── 2단계: 미너비니 추세 템플릿 ──","val":0,"fmt":"section"},
        "min_rs_pct":           {"label":"최소 RS 백분위 (%)",
                                 "val":60,"min":0,"max":99,"step":5,"fmt":"int",
                                 "info":"문서 권장: 상위 10~20% → 60% 이상"},
        "━━ 점수 임계":         {"label":"── 3단계: 점수화 기준 ──","val":0,"fmt":"section"},
        "score_grade_a":        {"label":"A급 최소 점수","val":90,"min":50,"max":100,"step":5,"fmt":"int"},
        "score_grade_b":        {"label":"B급 최소 점수","val":80,"min":50,"max":100,"step":5,"fmt":"int"},
        "━━ 주봉 필터":         {"label":"── 멀티타임프레임 ──","val":0,"fmt":"section"},
        "weekly_filter":        {"label":"주봉 MA10 위 확인","val":False,"fmt":"bool"},
    },
    "시부야 다카오": {
        "━━ 잡주 제거":         {"label":"── 1단계: 잡주 제거 ──","val":0,"fmt":"section"},
        "min_price":            {"label":"최소 주가 (원/KRW)","unit":"원",
                                 "val":3000,"min":0,"max":50000,"step":500,"fmt":"int"},
        "min_trading_value":    {"label":"최소 거래대금 (억원)",
                                 "label_usd":"최소 거래대금 (M$)",
                                 "unit":"억원","unit_usd":"M$",
                                 "val":20,"val_usd":1.0,"min":0,"max":500,"step":10,"step_usd":0.5,"fmt":"float"},
        "━━ 추세 필터":         {"label":"── 2단계: 추세 필터 ──","val":0,"fmt":"section"},
        "require_ma_align":     {"label":"MA20>MA60>MA120 정배열 필수","val":True,"fmt":"bool",
                                 "info":"True 시 역배열 종목 즉시 탈락"},
        "━━ 돌파 구조":         {"label":"── 3단계: 돌파 구조 ──","val":0,"fmt":"section"},
        "box_compress_pct":     {"label":"박스권 압축 기준 (10일 변동폭 %)",
                                 "val":10.0,"min":3.0,"max":30.0,"step":1.0,"fmt":"float",
                                 "info":"이 % 이하면 에너지 응축 상태로 판단"},
        "━━ 거래량":            {"label":"── ③ 거래량 기준 ──","val":0,"fmt":"section"},
        "vol_mult":             {"label":"최소 거래량 배율 (돌파 시)",
                                 "val":1.8,"min":0.5,"max":5.0,"step":0.1,"fmt":"float",
                                 "info":"문서 권장: 1.8배 이상 (돌파는 반드시 거래량 동반)"},
        "min_tv_hundred_m":     {"label":"최소 거래대금 (억원/일)",
                                 "val":20,"min":0,"max":500,"step":10,"fmt":"int",
                                 "info":"문서 권장: 20억~100억 이상 (잡주 돌파 제거)"},
        "━━ 지지선·추세선":     {"label":"── ⑤ 지지선/추세선 ──","val":0,"fmt":"section"},
        "support_touch_pct":    {"label":"지지선 근접 허용 (±%)",
                                 "val":2.5,"min":0.5,"max":10,"step":0.5,"fmt":"float"},
        "require_trend":        {"label":"추세선 돌파 필수","val":False,"fmt":"bool"},
        "require_candle":       {"label":"반전 캔들 필수","val":False,"fmt":"bool"},
        "━━ 공통":              {"label":"── 공통 ──","val":0,"fmt":"section"},
        "weekly_filter":        {"label":"주봉 MA10 위 확인","val":False,"fmt":"bool"},
    }
}

# ────────────────────────────────────────────────────────────────
#  런타임 파라미터 저장소 (UI에서 수정한 값)
# ────────────────────────────────────────────────────────────────
_PARAMS_RUNTIME: dict = {}   # {strategy_name: {key: value}}
_strategy_params_current = _PARAMS_RUNTIME  # ui_dialogs 호환용 alias

def get_param(strategy: str, key: str):
    """
    UI에서 수정한 런타임 값 우선 반환.
    없으면 STRATEGY_PARAMS_DEFAULT에서 기본값 반환.
    """
    runtime_val = _PARAMS_RUNTIME.get(strategy, {}).get(key)
    if runtime_val is not None:
        return runtime_val
    block = STRATEGY_PARAMS_DEFAULT.get(strategy, {})
    entry = block.get(key)
    if entry is None:
        return None
    if isinstance(entry, dict):
        return entry.get("val")
    return entry

def set_param(strategy: str, key: str, value) -> None:
    """UI 슬라이더/토글에서 값을 저장."""
    if strategy not in _PARAMS_RUNTIME:
        _PARAMS_RUNTIME[strategy] = {}
    _PARAMS_RUNTIME[strategy][key] = value

def reset_params(strategy: str) -> None:
    """해당 전략 파라미터를 기본값으로 초기화."""
    _PARAMS_RUNTIME.pop(strategy, None)

def get_all_params(strategy: str) -> dict:
    """현재 적용 중인 파라미터 전체 반환 (기본값 + 런타임 오버라이드)."""
    block = STRATEGY_PARAMS_DEFAULT.get(strategy, {})
    result = {}
    for k, v in block.items():
        if isinstance(v, dict) and "val" in v:
            result[k] = _PARAMS_RUNTIME.get(strategy, {}).get(k, v["val"])
    return result


def reset_params_to_default(strategy: str) -> None:
    """해당 전략 파라미터를 기본값으로 초기화 (reset_params 의 별칭)."""
    _PARAMS_RUNTIME.pop(strategy, None)

def get_param_label(strategy: str, key: str) -> str:
    """파라미터의 한글 레이블 반환."""
    entry = STRATEGY_PARAMS_DEFAULT.get(strategy, {}).get(key)
    if isinstance(entry, dict):
        return entry.get("label", key)
    return key

def get_param_unit(strategy: str, key: str, currency: str = "KRW") -> str:
    """파라미터의 단위 문자열 반환."""
    entry = STRATEGY_PARAMS_DEFAULT.get(strategy, {}).get(key)
    if isinstance(entry, dict):
        if currency == "USD":
            return entry.get("unit_usd", entry.get("unit", ""))
        return entry.get("unit", "")
    return ""
