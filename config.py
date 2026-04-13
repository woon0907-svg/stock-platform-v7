#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config.py — 전역 상수 / 색상 / 스캔 설정 / 폴백 티커
수정 시 이 파일만 편집하면 됩니다.
"""
import logging
import threading
import gc

# ══ 선택적 임포트 ══════════════════════════════════════════════
try:
    import numpy as np
    HAS_NP = True
except ImportError:
    HAS_NP = False

try:
    import pandas as pd
    HAS_PD = True
except ImportError:
    HAS_PD = False
    pd = None

try:
    import matplotlib
    matplotlib.use("TkAgg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker
    import matplotlib.gridspec as gridspec
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.patches import FancyBboxPatch
    import warnings
    warnings.filterwarnings("ignore", message="Glyph.*missing from font")
    plt.rcParams["interactive"] = False
    import matplotlib.font_manager as fm
    _kr_font_candidates = [
        "NanumBarunGothic", "NanumGothic", "Malgun Gothic",
        "AppleGothic", "Noto Sans CJK KR", "Gulim",
    ]
    _found_font = None
    for _fn in _kr_font_candidates:
        try:
            if any(_fn.lower() in f.name.lower() for f in fm.fontManager.ttflist):
                _found_font = _fn
                break
        except Exception:
            pass
    if _found_font:
        plt.rcParams["font.family"] = _found_font
    plt.rcParams["axes.unicode_minus"] = False
    HAS_MPL = True
except ImportError:
    HAS_MPL = False
    plt = None
    mticker = None
    gridspec = None
    FigureCanvasTkAgg = None
    FancyBboxPatch = None

try:
    import yfinance as yf
    HAS_YF = True
except ImportError:
    HAS_YF = False
    yf = None

try:
    from pykrx import stock as pykrx_stock
    HAS_PYKRX = True
except ImportError:
    HAS_PYKRX = False
    pykrx_stock = None

# yfinance / urllib3 로그 억제
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)
logging.getLogger("requests").setLevel(logging.CRITICAL)

# ══ API 키 (실행 시 다이얼로그 입력) ══════════════════════════
_KRX_API_KEY  = None
_DART_API_KEY = None
_DATA_SOURCE  = "NAVER"   # "API" = KRX+DART, "NAVER" = 네이버

# ══ 병렬 처리 ══════════════════════════════════════════════════
PARALLEL_WORKERS   = 20
SCAN_RANGE_OPTIONS = [
    ("빠른 (200개)",  200),
    ("보통 (500개)",  500),
    ("전체 스캔",    99999),
]
DEFAULT_SCAN_RANGE = 99999

# ══ 네이버 User-Agent ══════════════════════════════════════════
_NAVER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# ══ 컬러 팔레트 ═══════════════════════════════════════════════
BG_DARK  = "#0d1117"
BG_MID   = "#161b22"
BG_CARD  = "#21262d"
BG_SEL   = "#1f3a5f"
ACCENT   = "#e94560"
ACCENT2  = "#58a6ff"
TEXT_W   = "#e6edf3"
TEXT_G   = "#8b949e"
GREEN    = "#3fb950"
RED_C    = "#f85149"
YELLOW   = "#d29922"
PURPLE   = "#bc8cff"
ORANGE   = "#ffa657"
TEAL     = "#39d353"

KOSPI_FALLBACK = [
    ("삼성전자","005930.KS"),("SK하이닉스","000660.KS"),("LG에너지솔루션","373220.KS"),
    ("삼성바이오로직스","207940.KS"),("현대차","005380.KS"),("기아","000270.KS"),
    ("POSCO홀딩스","005490.KS"),("LG화학","051910.KS"),("삼성SDI","006400.KS"),
    ("카카오","035720.KS"),("NAVER","035420.KS"),("셀트리온","068270.KS"),
    ("두산에너빌리티","034020.KS"),("SK이노베이션","096770.KS"),("현대모비스","012330.KS"),
    ("LG전자","066570.KS"),("KB금융","105560.KS"),("신한지주","055550.KS"),
    ("하나금융지주","086790.KS"),("삼성물산","028260.KS"),("SK","034730.KS"),
    ("한미약품","128940.KS"),("HMM","011200.KS"),("고려아연","010130.KS"),
    ("현대중공업","329180.KS"),("롯데케미칼","011170.KS"),("OCI홀딩스","010060.KS"),
    ("코스맥스","192820.KS"),("한국전력","015760.KS"),("우리금융지주","316140.KS"),
    # 추가 KOSPI
    ("삼성생명","032830.KS"),("삼성화재","000810.KS"),("현대해상","001450.KS"),
    ("DB손해보험","005830.KS"),("메리츠화재","000060.KS"),("한화생명","088350.KS"),
    ("교보증권","030610.KS"),("미래에셋증권","006800.KS"),("키움증권","039490.KS"),
    ("삼성증권","016360.KS"),("NH투자증권","005940.KS"),("한국투자증권","071050.KS"),
    ("현대건설","000720.KS"),("GS건설","006360.KS"),("대우건설","047040.KS"),
    ("삼성엔지니어링","028050.KS"),("현대엔지니어링","267270.KS"),("DL이앤씨","375500.KS"),
    ("롯데쇼핑","023530.KS"),("신세계","004170.KS"),("현대백화점","069960.KS"),
    ("이마트","139480.KS"),("GS리테일","007070.KS"),("BGF리테일","282330.KS"),
    ("CJ제일제당","097950.KS"),("농심","004370.KS"),("오리온","271560.KS"),
    ("하이트진로","000080.KS"),("롯데제과","004990.KS"),("빙그레","005180.KS"),
    ("한국항공우주","047810.KS"),("현대로템","064350.KS"),("LIG넥스원","079550.KS"),
    ("한화에어로스페이스","012450.KS"),("한화시스템","272210.KS"),
    ("SK텔레콤","017670.KS"),("KT","030200.KS"),("LG유플러스","032640.KS"),
    ("KT&G","033780.KS"),("한국가스공사","036460.KS"),("한국조선해양","009540.KS"),
    ("삼성중공업","010140.KS"),("대우조선해양","042660.KS"),("현대미포조선","010620.KS"),
    ("포스코퓨처엠","003670.KS"),("에코프로머티리얼즈","450080.KS"),
    ("SK바이오사이언스","302440.KS"),("한올바이오파마","009420.KS"),
    ("종근당","185750.KS"),("대웅제약","069620.KS"),("유한양행","000100.KS"),
    ("동아에스티","170900.KS"),("광동제약","009290.KS"),
    ("CJ대한통운","000120.KS"),("한진","002320.KS"),("현대글로비스","086280.KS"),
    ("롯데정보통신","286940.KS"),("아모레퍼시픽","090430.KS"),("LG생활건강","051900.KS"),
    ("한국콜마","161890.KS"),("코스맥스비티아이","044820.KS"),
    ("삼성전기","009150.KS"),("LG이노텍","011070.KS"),("자화전자","033240.KS"),
    ("파트론","091700.KS"),("비에이치","090460.KS"),
    ("현대차우","005385.KS"),("삼성전자우","005935.KS"),("LG전자우","066575.KS"),
    ("SKC","011790.KS"),("효성첨단소재","298050.KS"),("태광산업","003240.KS"),
    ("한솔케미칼","014680.KS"),("솔루스첨단소재","336370.KS"),
    ("두산퓨얼셀","336260.KS"),("효성중공업","298040.KS"),("일진머티리얼즈","020890.KS"),
    ("포스코인터내셔널","047050.KS"),("삼성SDS","018260.KS"),("LG CNS","064350.KS"),
    ("SK스퀘어","402340.KS"),("카카오페이","377300.KS"),("크래프톤","259960.KS"),
    ("카카오뱅크","323410.KS"),("케이뱅크","279570.KS"),
    ("롯데웰푸드","280360.KS"),("오뚜기","007310.KS"),("삼양식품","003230.KS"),
    ("CJ","001040.KS"),("LS","006260.KS"),("LS일렉트릭","010120.KS"),
    ("HD현대","267250.KS"),("HD현대일렉트릭","267260.KS"),("HD현대중공업","329180.KS"),
    ("두산밥캣","241560.KS"),("현대두산인프라코어","042670.KS"),
    ("에쓰오일","010950.KS"),("GS칼텍스","078930.KS"),
    ("대한항공","003490.KS"),("제주항공","089590.KS"),("진에어","272450.KS"),
    ("하나투어","039130.KS"),("모두투어","080160.KS"),
    ("GKL","114090.KS"),("파라다이스","034230.KS"),
    ("삼성바이오에피스","207940.KS"),("한미사이언스","008930.KS"),
    ("녹십자","006280.KS"),("일동홀딩스","000230.KS"),
    ("LG","003550.KS"),("LG디스플레이","034220.KS"),
    ("LG헬로비전","037560.KS"),
    ("한국전력","015760.KS"),("KB금융","105560.KS"),
    ("신한지주","055550.KS"),("하나금융지주","086790.KS"),
    ("우리금융지주","316140.KS"),("삼성물산","028260.KS"),
    ("현대모비스","012330.KS"),("HMM","011200.KS"),
    ("KT","030200.KS"),("KT&G","033780.KS"),
    ("한미약품","128940.KS"),("종근당","185750.KS"),
    ("고려아연","010130.KS"),("현대건설","000720.KS"),
    ("SK바이오팜","326030.KS"),("카카오뱅크","323410.KS"),
    ("두산에너빌리티","034020.KS"),("CJ제일제당","097950.KS"),
    ("오리온","271560.KS"),("롯데케미칼","011170.KS"),
]

KOSDAQ_FALLBACK = [
    ("에코프로비엠","247540.KQ"),("에코프로","086520.KQ"),("엘앤에프","066970.KQ"),
    ("셀트리온헬스케어","091990.KQ"),("카카오게임즈","293490.KQ"),("펄어비스","263750.KQ"),
    ("HLB","028300.KQ"),("알테오젠","196170.KQ"),("리노공업","058470.KQ"),
    ("클래시스","214150.KQ"),("레인보우로보틱스","277810.KQ"),("솔브레인","357780.KQ"),
    ("파마리서치","214450.KQ"),("나노신소재","121600.KQ"),("덴티움","145720.KQ"),
    ("오스코텍","039200.KQ"),("케이엠더블유","032500.KQ"),("피엔티","137400.KQ"),
    ("원익IPS","240810.KQ"),("JYP엔터","035900.KQ"),("SM엔터","041510.KQ"),
    ("하이브","352820.KQ"),("씨에스윈드","112610.KQ"),("메디톡스","086900.KQ"),
    ("이오테크닉스","039030.KQ"),("에스티팜","237690.KQ"),("크래프톤","259960.KQ"),
    ("카카오뱅크","323410.KQ"),
    # 추가 KOSDAQ
    ("셀트리온제약","068760.KQ"),("HLB생명과학","067900.KQ"),("HLB제약","022250.KQ"),
    ("에이비엘바이오","298380.KQ"),("지노믹트리","228760.KQ"),("툴젠","199800.KQ"),
    ("코미팜","041960.KQ"),("오스템임플란트","048260.KQ"),("디오","039840.KQ"),
    ("레이","228670.KQ"),("바텍","043150.KQ"),("인바디","041830.KQ"),
    ("뷰웍스","100120.KQ"),("고영","098460.KQ"),("레고켐바이오","141080.KQ"),
    ("에스디바이오센서","137310.KQ"),("씨젠","096530.KQ"),
    ("NICE평가정보","030190.KQ"),("더존비즈온","012510.KQ"),("케어랩스","263700.KQ"),
    ("아이센스","099190.KQ"),("비올","134780.KQ"),("제이시스메디칼","287410.KQ"),
    ("원텍","336570.KQ"),("하이로닉","149040.KQ"),("루트로닉","085370.KQ"),
    ("에스엠코어","007820.KQ"),("와이지엔터테인먼트","122870.KQ"),
    ("큐브엔터","182360.KQ"),("에스엠","041510.KQ"),("하이브","352820.KQ"),
    ("에프엔에프","007570.KQ"),("코윈테크","282880.KQ"),("티씨케이","064760.KQ"),
    ("해성디에스","195870.KQ"),("원익홀딩스","030530.KQ"),
    ("이노션","214320.KQ"),("이엔쎌","257730.KQ"),
    ("셀바스AI","108860.KQ"),("솔트룩스","304100.KQ"),("마인즈랩","214270.KQ"),
    ("코난테크놀로지","402030.KQ"),
    ("에스트래픽","234300.KQ"),("넥스트칩","185490.KQ"),("텔레칩스","054450.KQ"),
    ("아나패스","123860.KQ"),("어보브반도체","102120.KQ"),
    ("수산인더스트리","011300.KQ"),("대덕전자","008060.KQ"),("심텍","222800.KQ"),
    ("이수페타시스","007660.KQ"),("코리아써키트","007810.KQ"),
    ("피에스케이","319660.KQ"),("한솔테크닉스","004710.KQ"),("엔씨소프트","036570.KQ"),
    ("넷마블","251270.KQ"),("넥슨코리아","225570.KQ"),("NHN","181710.KQ"),
    ("카카오게임즈","293490.KQ"),("웹젠","069080.KQ"),("게임빌","063080.KQ"),
    ("컴투스","078340.KQ"),("데브시스터즈","194480.KQ"),("크래프톤","259960.KQ"),
    ("CJ ENM","035760.KQ"),("스튜디오드래곤","253450.KQ"),("키이스트","054780.KQ"),
    ("쇼박스","086980.KQ"),("에이스침대","003650.KQ"),
    ("에코마케팅","230360.KQ"),("나스미디어","089600.KQ"),
    ("메가스터디교육","215200.KQ"),("NE능률","053290.KQ"),
    ("에듀윌","313520.KQ"),("비상교육","100220.KQ"),
    ("신스타임즈","291650.KQ"),("클리오","237880.KQ"),("코스맥스엔비티","222040.KQ"),
    ("지피클럽","016250.KQ"),("브이티","018290.KQ"),("실리콘투","257720.KQ"),
    ("코리안리","003690.KQ"),("메리츠금융지주","138040.KQ"),
    ("SBI저축은행","050890.KQ"),
    ("이수앱지스","086890.KQ"),("종근당바이오","063160.KQ"),
    ("유바이오로직스","179290.KQ"),("샤페론","378800.KQ"),("압타바이오","293780.KQ"),
    ("네오이뮨텍","950190.KQ"),("지아이이노베이션","286940.KQ"),
    ("에이치엘비파워","043260.KQ"),("천보","278280.KQ"),("후성","093370.KQ"),
    ("솔루스첨단소재","336370.KQ"),("나노팀","486050.KQ"),
    ("에코앤드림","101360.KQ"),("엠플러스","259630.KQ"),
    ("코스모신소재","005070.KQ"),("상아프론테크","089980.KQ"),
]

# ──────────────────────────────────────────────
#  미국 종목 폴백
#  S&P500 전체 503종목 + NASDAQ-100 전체 100종목
# ──────────────────────────────────────────────
SP500_FALLBACK = [
    # 정보기술
    ("Apple","AAPL"),("Microsoft","MSFT"),("NVIDIA","NVDA"),("Broadcom","AVGO"),
    ("AMD","AMD"),("Qualcomm","QCOM"),("Texas Instruments","TXN"),("Applied Materials","AMAT"),
    ("Lam Research","LRCX"),("KLA Corp","KLAC"),("Micron","MU"),("Intel","INTC"),
    ("Analog Devices","ADI"),("Marvell","MRVL"),("ON Semiconductor","ON"),
    ("Microchip Tech","MCHP"),("TE Connectivity","TEL"),("Amphenol","APH"),
    ("Corning","GLW"),("Keysight","KEYS"),("Trimble","TRMB"),("Gartner","IT"),
    ("EPAM","EPAM"),("Cognizant","CTSH"),("Infosys","INFY"),("Accenture","ACN"),
    ("IBM","IBM"),("Salesforce","CRM"),("Adobe","ADBE"),("Oracle","ORCL"),
    ("ServiceNow","NOW"),("Workday","WDAY"),("Palo Alto","PANW"),("Fortinet","FTNT"),
    ("CrowdStrike","CRWD"),("Datadog","DDOG"),("Snowflake","SNOW"),("MongoDB","MDB"),
    ("Cloudflare","NET"),("Zscaler","ZS"),("Okta","OKTA"),("Twilio","TWLO"),
    ("Palantir","PLTR"),("ANSYS","ANSS"),("PTC","PTC"),("Synopsys","SNPS"),
    ("Cadence","CDNS"),("F5 Networks","FFIV"),("VeriSign","VRSN"),("Akamai","AKAM"),
    ("CDW","CDW"),("Benchmark Elec","BHE"),("Zebra Tech","ZBRA"),("IPG Photonics","IPGP"),
    ("Teradyne","TER"),("National Instr","NATI"),("Jabil","JBL"),("Flex","FLEX"),
    # 커뮤니케이션
    ("Alphabet A","GOOGL"),("Alphabet C","GOOG"),("Meta","META"),("Netflix","NFLX"),
    ("Disney","DIS"),("Comcast","CMCSA"),("Warner Bros","WBD"),("Paramount","PARA"),
    ("Fox Corp A","FOXA"),("News Corp A","NWSA"),("Omnicom","OMC"),("IPG","IPG"),
    ("Take-Two","TTWO"),("Electronic Arts","EA"),("Activision","ATVI"),
    ("Verizon","VZ"),("AT&T","T"),("T-Mobile","TMUS"),("Lumen","LUMN"),
    ("Interpublic","IPG"),("Twitter","TWTR"),("Pinterest","PINS"),("Snap","SNAP"),
    # 경기소비재
    ("Amazon","AMZN"),("Tesla","TSLA"),("Home Depot","HD"),("McDonald's","MCD"),
    ("Nike","NKE"),("Starbucks","SBUX"),("Booking","BKNG"),("Airbnb","ABNB"),
    ("Marriott","MAR"),("Hilton","HLT"),("Las Vegas Sands","LVS"),("MGM","MGM"),
    ("Wynn","WYNN"),("Norwegian Cruise","NCLH"),("Carnival","CCL"),("Royal Caribbean","RCL"),
    ("Expedia","EXPE"),("TripAdvisor","TRIP"),("Dollar General","DG"),
    ("Dollar Tree","DLTR"),("Ross Stores","ROST"),("TJX","TJX"),("Target","TGT"),
    ("Walmart","WMT"),("Costco","COST"),("Best Buy","BBY"),("AutoZone","AZO"),
    ("O'Reilly Auto","ORLY"),("AutoNation","AN"),("CarMax","KMX"),("Carvana","CVNA"),
    ("Ford","F"),("GM","GM"),("Ferrari","RACE"),("Aptiv","APTV"),
    ("BorgWarner","BWA"),("Genuine Parts","GPC"),("Goodyear","GT"),
    ("Hasbro","HAS"),("Mattel","MAT"),("PVH","PVH"),("Tapestry","TPR"),
    ("Ralph Lauren","RL"),("VF Corp","VFC"),("Hanesbrands","HBI"),("Levis","LEVI"),
    ("Mohawk","MHK"),("Whirlpool","WHR"),("Garmin","GRMN"),("Harman","HAR"),
    ("Viasat","VSAT"),("DISH Network","DISH"),
    # 필수소비재
    ("Procter&Gamble","PG"),("Coca-Cola","KO"),("PepsiCo","PEP"),("Mondelez","MDLZ"),
    ("Philip Morris","PM"),("Altria","MO"),("Colgate","CL"),("Kimberly-Clark","KMB"),
    ("Sysco","SYY"),("Kraft Heinz","KHC"),("General Mills","GIS"),("Kellogg","K"),
    ("Campbell Soup","CPB"),("Hormel","HRL"),("Tyson Foods","TSN"),("McCormick","MKC"),
    ("Constellation Brands","STZ"),("Molson Coors","TAP"),("Brown-Forman","BF-B"),
    ("Church&Dwight","CHD"),("Clorox","CLX"),("Energizer","ENR"),("Reynolds","REY"),
    ("Estee Lauder","EL"),("Bath&Body","BBWI"),("Revlon","REV"),
    ("Kroger","KR"),("Albertsons","ACI"),("Dollar Tree","DLTR"),
    # 헬스케어
    ("UnitedHealth","UNH"),("J&J","JNJ"),("Eli Lilly","LLY"),("AbbVie","ABBV"),
    ("Merck","MRK"),("Pfizer","PFE"),("Amgen","AMGN"),("Gilead","GILD"),
    ("Regeneron","REGN"),("Biogen","BIIB"),("Vertex","VRTX"),("Moderna","MRNA"),
    ("Bristol-Myers","BMY"),("Zoetis","ZTS"),("Cigna","CI"),("CVS Health","CVS"),
    ("Humana","HUM"),("Elevance","ELV"),("Centene","CNC"),("Molina","MOH"),
    ("Baxter","BAX"),("Becton Dickinson","BDX"),("Boston Scientific","BSX"),
    ("Edwards Lifesciences","EW"),("Hologic","HOLX"),("Idexx","IDXX"),
    ("Intuitive Surgical","ISRG"),("Medtronic","MDT"),("Stryker","SYK"),
    ("Zimmer Biomet","ZBH"),("DexCom","DXCM"),("Insulet","PODD"),
    ("Align Technology","ALGN"),("West Pharmaceutical","WST"),
    ("Thermo Fisher","TMO"),("Danaher","DHR"),("Agilent","A"),
    ("Bio-Rad","BIO"),("Charles River","CRL"),("Iqvia","IQV"),("ICON","ICLR"),
    ("Laboratory Corp","LH"),("Quest Diagnostics","DGX"),
    ("HCA Healthcare","HCA"),("Universal Health","UHS"),("Tenet Health","THC"),
    ("McKesson","MCK"),("AmerisourceBergen","ABC"),("Cardinal Health","CAH"),
    # 금융
    ("JPMorgan","JPM"),("BankOfAmerica","BAC"),("Wells Fargo","WFC"),
    ("Goldman Sachs","GS"),("Morgan Stanley","MS"),("Citigroup","C"),
    ("US Bancorp","USB"),("PNC Financial","PNC"),("Truist","TFC"),
    ("Capital One","COF"),("American Express","AXP"),("Discover","DFS"),
    ("Synchrony","SYF"),("Visa","V"),("Mastercard","MA"),("PayPal","PYPL"),
    ("Block","SQ"),("Fiserv","FI"),("Fidelity Natl","FIS"),("FleetCor","FLT"),
    ("Global Payments","GPN"),("Western Union","WU"),("MoneyGram","MGI"),
    ("BlackRock","BLK"),("Schwab","SCHW"),("TD Ameritrade","AMTD"),
    ("Raymond James","RJF"),("LPL Financial","LPLA"),("Stifel","SF"),
    ("Nasdaq Inc","NDAQ"),("CME Group","CME"),("ICE","ICE"),("CBOE","CBOE"),
    ("Moody's","MCO"),("S&P Global","SPGI"),("Verisk","VRSK"),("MSCI","MSCI"),
    ("FactSet","FDS"),("MarketAxess","MKTX"),("Tradeweb","TW"),
    ("Berkshire B","BRK-B"),("Markel","MKL"),("Alleghany","Y"),
    ("Progressive","PGR"),("Allstate","ALL"),("Travelers","TRV"),
    ("Hartford","HIG"),("Chubb","CB"),("Loews","L"),("W.R. Berkley","WRB"),
    ("Lincoln Natl","LNC"),("Unum","UNM"),("Principal","PFG"),
    ("MetLife","MET"),("Prudential","PRU"),("Sun Life","SLF"),("Manulife","MFC"),
    ("Realty Income","O"),("Simon Property","SPG"),("AvalonBay","AVB"),
    ("Equity Residential","EQR"),("Prologis","PLD"),("CBRE","CBRE"),
    ("Weyerhaeuser","WY"),("Iron Mountain","IRM"),("Crown Castle","CCI"),
    ("American Tower","AMT"),("SBA Comm","SBAC"),("Public Storage","PSA"),
    ("Extra Space","EXR"),("Life Storage","LSI"),("National Retail","NNN"),
    # 산업재
    ("Honeywell","HON"),("Caterpillar","CAT"),("Deere","DE"),
    ("Eaton","ETN"),("Emerson","EMR"),("Parker Hannifin","PH"),
    ("Illinois Tool","ITW"),("Dover","DOV"),("Roper Tech","ROP"),
    ("IDEX","IEX"),("Xylem","XYL"),("Watts Water","WTS"),
    ("Rockwell Automation","ROK"),("Allegion","ALLE"),("Masco","MAS"),
    ("Carrier Global","CARR"),("Trane Tech","TT"),("Johnson Controls","JCI"),
    ("A.O. Smith","AOS"),("Watts Water","WTS"),
    ("GE","GE"),("3M","MMM"),("RTX","RTX"),("Lockheed Martin","LMT"),
    ("Boeing","BA"),("Northrop Grumman","NOC"),("L3Harris","LHX"),
    ("Raytheon","RTX"),("General Dynamics","GD"),("TransDigm","TDG"),
    ("Heico","HEI"),("Moog","MOG-A"),
    ("Waste Management","WM"),("Republic Services","RSG"),("Cintas","CTAS"),
    ("Automatic Data","ADP"),("Paychex","PAYX"),("Broadridge","BR"),
    ("Genpact","G"),("ManpowerGroup","MAN"),("Robert Half","RHI"),
    ("FedEx","FDX"),("UPS","UPS"),("Old Dominion","ODFL"),("Werner","WERN"),
    ("JB Hunt","JBHT"),("Saia","SAIA"),("XPO","XPO"),("Yellow","YELL"),
    ("Southwest Airlines","LUV"),("Delta","DAL"),("United Airlines","UAL"),
    ("American Airlines","AAL"),("Alaska Air","ALK"),
    ("Union Pacific","UNP"),("CSX","CSX"),("Norfolk Southern","NSC"),
    ("GATX","GATX"),
    # 에너지
    ("ExxonMobil","XOM"),("Chevron","CVX"),("ConocoPhillips","COP"),
    ("EOG Resources","EOG"),("Pioneer Natural","PXD"),("Devon Energy","DVN"),
    ("Diamondback","FANG"),("Marathon Petroleum","MPC"),("Valero","VLO"),
    ("Phillips 66","PSX"),("HollyFrontier","HFC"),("Schlumberger","SLB"),
    ("Halliburton","HAL"),("Baker Hughes","BKR"),("Kinder Morgan","KMI"),
    ("Williams Cos","WMB"),("Targa Resources","TRGP"),("Enterprise","EPD"),
    ("Enphase","ENPH"),("SolarEdge","SEDG"),("First Solar","FSLR"),
    ("NextEra Energy","NEE"),("AES","AES"),("NRG Energy","NRG"),
    # 유틸리티
    ("Duke Energy","DUK"),("Southern Co","SO"),("American Elec","AEP"),
    ("Dominion Energy","D"),("Exelon","EXC"),("Consolidated Ed","ED"),
    ("Xcel Energy","XEL"),("Evergy","EVRG"),("Entergy","ETR"),
    ("PPL","PPL"),("CMS Energy","CMS"),("Ameren","AEE"),
    ("WEC Energy","WEC"),("Eversource","ES"),("Avangrid","AGR"),
    ("Essential Utils","WTRG"),("American Water","AWK"),
    # 소재
    ("Linde","LIN"),("Air Products","APD"),("Ecolab","ECL"),
    ("Sherwin-Williams","SHW"),("PPG Industries","PPG"),("RPM International","RPM"),
    ("LyondellBasell","LYB"),("Dow","DOW"),("DuPont","DD"),
    ("Celanese","CE"),("Eastman Chem","EMN"),("Huntsman","HUN"),
    ("Westlake","WLK"),("Olin","OLN"),("Freeport-McMoRan","FCX"),
    ("Nucor","NUE"),("Steel Dynamics","STLD"),("Cleveland-Cliffs","CLF"),
    ("Reliance Steel","RS"),("Commercial Metals","CMC"),
    ("Newmont","NEM"),("Barrick","GOLD"),("Agnico Eagle","AEM"),
    ("Ball","BALL"),("Crown Holdings","CCK"),("Sealed Air","SEE"),
    ("International Paper","IP"),("Packaging Corp","PKG"),("Sonoco","SON"),
    ("Weyerhaeuser","WY"),("Rayonier","RYN"),
]

NASDAQ_FALLBACK = [
    # NASDAQ-100 완전판
    ("Apple","AAPL"),("Microsoft","MSFT"),("NVIDIA","NVDA"),("Amazon","AMZN"),
    ("Meta","META"),("Tesla","TSLA"),("Broadcom","AVGO"),("Alphabet A","GOOGL"),
    ("Alphabet C","GOOG"),("Costco","COST"),("Netflix","NFLX"),("AMD","AMD"),
    ("Adobe","ADBE"),("Qualcomm","QCOM"),("Applied Materials","AMAT"),
    ("Marvell","MRVL"),("Intuitive Surgical","ISRG"),("Regeneron","REGN"),
    ("Gilead","GILD"),("Lam Research","LRCX"),("ADP","ADP"),("KLA Corp","KLAC"),
    ("Synopsys","SNPS"),("Cadence","CDNS"),("MercadoLibre","MELI"),
    ("ASML","ASML"),("Palo Alto","PANW"),("Airbnb","ABNB"),
    ("CrowdStrike","CRWD"),("Zscaler","ZS"),("DexCom","DXCM"),
    ("Illumina","ILMN"),("Vertex","VRTX"),("IDEXX","IDXX"),
    ("Fastenal","FAST"),("Paychex","PAYX"),("Ross Stores","ROST"),
    ("Old Dominion","ODFL"),("Cintas","CTAS"),("ANSYS","ANSS"),
    ("Copart","CPRT"),("Verisk","VRSK"),("Dollar Tree","DLTR"),
    ("eBay","EBAY"),("Workday","WDAY"),("Datadog","DDOG"),
    ("Snowflake","SNOW"),("Palantir","PLTR"),
    ("Micron","MU"),("Intel","INTC"),("Analog Devices","ADI"),
    ("PepsiCo","PEP"),("Starbucks","SBUX"),("Comcast","CMCSA"),
    ("Booking","BKNG"),("Mondelez","MDLZ"),("Biogen","BIIB"),
    ("Moderna","MRNA"),("Fortinet","FTNT"),("T-Mobile","TMUS"),
    ("NXP Semi","NXPI"),("ON Semi","ON"),("Microchip","MCHP"),
    ("Texas Instruments","TXN"),("Amgen","AMGN"),("Align Tech","ALGN"),
    ("Take-Two","TTWO"),("Electronic Arts","EA"),("WBA","WBA"),
    ("Expedia","EXPE"),("Marriott","MAR"),("Exelon","EXC"),
    ("Charter Comm","CHTR"),("PayPal","PYPL"),("MongoDB","MDB"),
    ("Cloudflare","NET"),("Okta","OKTA"),("Twilio","TWLO"),
    ("Trade Desk","TTD"),("Atlassian","TEAM"),("Lucid","LCID"),
    ("Rivian","RIVN"),("Warner Bros","WBD"),("Zoom","ZM"),
    ("DocuSign","DOCU"),("Peloton","PTON"),("Robinhood","HOOD"),
    ("Coinbase","COIN"),("Sea Ltd","SE"),("Grab","GRAB"),
    ("JD.com","JD"),("Baidu","BIDU"),("NetEase","NTES"),
    ("Trip.com","TCOM"),("Pinduoduo","PDD"),("Li Auto","LI"),
    ("NIO","NIO"),("XPeng","XPEV"),("BYD","BYDDY"),
    ("JDCY","JD"),("Meituan","MPNGF"),
    ("AstraZeneca","AZN"),("GSK","GSK"),("CSL","CSLLY"),
    ("LVMH","LVMUY"),("SAP","SAP"),("ARM","ARM"),
    ("Roper Tech","ROP"),("FAST","FAST"),("VRSK","VRSK"),
]

# ──────────────────────────────────────────────
#  로컬 파일 캐시 — 최근 성공 데이터 저장/불러오기
#  위치: ~/.stock_platform/
# ──────────────────────────────────────────────
import os
import pathlib


# ── 지수 티커 맵 ──────────────────────────────────────────────────────
_KR_INDEX = {
    "ALL":    "^KS11",
    "KOSPI":  "^KS11",
    "KOSDAQ": "^KQ11",
}
_US_INDEX = {
    "US_ALL": "^GSPC",
    "SP500":  "^GSPC",
    "NASDAQ": "^NDX",
}
