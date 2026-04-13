# 주식 투자 플랫폼 v7 — 웹 버전 (Streamlit)

개인용 주식 투자 플랫폼. 모바일/PC 브라우저에서 동작합니다.

---

## 📁 파일 구성

```
stock_web_v7/
├── streamlit_app.py     ← 메인 앱 (4개 탭 UI + 판정 엔진)
├── web_charts.py        ← Plotly 인터랙티브 차트
├── api_kr.py            ← KRX + DART Open API 통합
├── strategies.py        ← 9개 전략 로직
├── strategy_params.py   ← 전략 파라미터
├── indicators.py        ← 기술적 지표 계산
├── data_fetcher.py      ← 데이터 수집 (KRX→네이버→yfinance)
├── config.py            ← 폴백 종목 목록
├── requirements.txt     ← Python 패키지 의존성
├── runtime.txt          ← Python 3.11.9 버전 고정 (Render용)
├── render.yaml          ← Render 배포 자동 설정
└── .streamlit/
    └── config.toml      ← Streamlit 다크 테마 설정
```

---

## 🚀 로컬 실행

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
# http://localhost:8501 접속
```

---

## 🌐 Render 배포

1. GitHub에 이 폴더 전체를 push
2. render.com → New Web Service → GitHub 저장소 선택
3. render.yaml 설정이 자동 적용됨
4. Create Web Service 클릭 → 5~10분 후 배포 완료

### 환경변수 설정 (선택)
Render → 서비스 → Environment → Add Environment Variable

| Key | Value |
|-----|-------|
| KRX_API_KEY | 공공데이터포털 KRX 인증키 |
| DART_API_KEY | opendart.fss.or.kr 인증키 |

---

## 📊 기능

| 탭 | 기능 |
|----|------|
| 🔍 전략 스캐너 | KOSPI/KOSDAQ/전체 × 시가총액 상위200/500/전체 스캔, 상위30위 표시 |
| 📋 투자종목 | 종목 추가(자동완성) + 매수/매도 판정 엔진 + ADX 신호 |
| 📊 차트 | 캔들+EMA10/20/MA50/150/200+MACD+ADX+RSI (Plotly 인터랙티브) |
| ⚙️ 설정 | KRX/DART API 키 설정 + 전략 파라미터 확인 |

---

## 데이터 우선순위

- **OHLCV**: KRX Open API → 네이버 차트 → yfinance → 샘플
- **재무**: DART Open API → 네이버 금융 → yfinance → 샘플

---

> Render 무료 플랜: 15분 미사용 시 슬립 → 첫 접속 30초 대기
