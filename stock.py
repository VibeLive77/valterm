import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

FMP_KEY = os.environ.get("FMP_API_KEY", "")
BASE = "https://financialmodelingprep.com/api"

def fmp(path):
    url = f"{BASE}{path}&apikey={FMP_KEY}" if "?" in path else f"{BASE}{path}?apikey={FMP_KEY}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

def resolve_ticker(query):
    """종목명/티커 → FMP 티커 변환"""
    # 한국 종목: 숫자 6자리면 .KS 붙이기
    import re
    if re.match(r"^\d{6}$", query):
        return query + ".KS"
    # 이미 .KS/.KQ 붙어있으면 그대로
    if query.upper().endswith((".KS", ".KQ")):
        return query.upper()
    # 한글이면 FMP 검색으로 티커 찾기
    if re.search(r"[가-힣]", query):
        results = fmp(f"/v3/search?query={urllib.parse.quote(query)}&limit=5&exchange=KSC,KOE")
        if results:
            return results[0]["symbol"]
        return None
    # 영문은 대문자로
    return query.upper()

def get_stock_data(ticker):
    # 기본 정보
    profile = fmp(f"/v3/profile/{ticker}")
    if not profile:
        return {"error": f"종목을 찾을 수 없어요: {ticker}"}
    p = profile[0]

    # 연간 핵심 지표 (PER, PBR, ROE, PSR, PEG, DCF)
    ratios_annual = fmp(f"/v3/ratios/{ticker}?limit=6")
    ratios_quarterly = fmp(f"/v3/ratios/{ticker}?period=quarter&limit=8")

    # DCF
    dcf_data = fmp(f"/v3/historical-discounted-cash-flow-statement/{ticker}?limit=6")
    dcf_map = {d["date"][:4]: d.get("dcf") for d in (dcf_data or [])}
    dcf_q_data = fmp(f"/v3/historical-discounted-cash-flow-statement/{ticker}?period=quarter&limit=8")
    dcf_q_map = {d["date"][:7]: d.get("dcf") for d in (dcf_q_data or [])}

    def build_annual(ratios):
        result = []
        for r in ratios:
            year = r.get("date", "")[:4]
            result.append({
                "period": year,
                "per": r.get("priceEarningsRatio"),
                "pbr": r.get("priceToBookRatio"),
                "psr": r.get("priceToSalesRatio"),
                "roe": round(r["returnOnEquity"] * 100, 2) if r.get("returnOnEquity") else None,
                "peg": r.get("priceEarningsToGrowthRatio"),
                "dcf": dcf_map.get(year),
            })
        return list(reversed(result))

    def build_quarterly(ratios):
        result = []
        for r in ratios:
            date = r.get("date", "")
            year = date[:4]
            month = int(date[5:7]) if len(date) >= 7 else 1
            qtr = (month - 1) // 3 + 1
            label = f"{year}Q{qtr}"
            result.append({
                "period": label,
                "per": r.get("priceEarningsRatio"),
                "pbr": r.get("priceToBookRatio"),
                "psr": r.get("priceToSalesRatio"),
                "roe": round(r["returnOnEquity"] * 100, 2) if r.get("returnOnEquity") else None,
                "peg": r.get("priceEarningsToGrowthRatio"),
                "dcf": dcf_q_map.get(date[:7]),
            })
        return list(reversed(result))

    market_cap = p.get("mktCap", 0)
    if market_cap > 1e12:
        cap_str = f"${market_cap/1e12:.1f}T"
    elif market_cap > 1e9:
        cap_str = f"${market_cap/1e9:.0f}B"
    elif market_cap > 1e8:
        cap_str = f"₩{market_cap/1e8:.0f}억"
    else:
        cap_str = str(market_cap)

    return {
        "company": p.get("companyName", ticker),
        "ticker": ticker,
        "market": "KR" if ticker.endswith(".KS") or ticker.endswith(".KQ") else "US",
        "annual": build_annual(ratios_annual or []),
        "quarterly": build_quarterly(ratios_quarterly or []),
        "summary": {
            "currentPrice": p.get("price"),
            "marketCap": cap_str,
            "sector": p.get("sector", ""),
            "description": p.get("description", "")[:80] if p.get("description") else "",
            "per": p.get("pe"),
            "pbr": None,
            "roe": None,
            "peg": None,
        }
    }

import urllib.parse

class handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        q = params.get("q", [""])[0].strip()
        if not q:
            return self.send_json({"error": "q 파라미터 필요"}, 400)
        if not FMP_KEY:
            return self.send_json({"error": "FMP_API_KEY 환경변수 없음"}, 500)
        try:
            ticker = resolve_ticker(q)
            if not ticker:
                return self.send_json({"error": f"종목을 찾을 수 없어요: {q}"})
            data = get_stock_data(ticker)
            self.send_json(data)
        except Exception as e:
            self.send_json({"error": str(e)}, 500)
