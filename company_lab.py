"""Build the free, auditable Company Lab dataset.

Sources are deliberately limited to public endpoints that need no billable key:
Yahoo Finance public endpoints for personal market research and SEC EDGAR for
primary filings. Every computed value carries an as-of date in the output.
"""

from __future__ import annotations

import bisect
import concurrent.futures
import hashlib
import json
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / ".company_state"
CACHE_PATH = STATE_DIR / "cache.json"
OUTPUT_PATH = ROOT / "company_data.json"
ENGINE_VERSION = 3

YAHOO_SEARCH = "https://query1.finance.yahoo.com/v1/finance/search"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_TIMESERIES = "https://query1.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}"
SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"

USER_AGENT = "PortfolioIntelligence/1.0 personal research github.com/PhilippePatekVD/Finance-Adviser"
YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PortfolioIntelligence/1.0)"}
SEC_HEADERS = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"}

PERIODS = {
    "1d": {"label": "1 jour", "trading_days": 1},
    "1w": {"label": "1 semaine", "days": 7},
    "1m": {"label": "1 mois", "months": 1},
    "3m": {"label": "3 mois", "months": 3},
    "6m": {"label": "6 mois", "months": 6},
    "1y": {"label": "1 an", "years": 1},
    "2y": {"label": "2 ans", "years": 2},
    "5y": {"label": "5 ans", "years": 5},
}

VALUATION_TYPES = [
    "trailingMarketCap", "trailingEnterpriseValue", "trailingPeRatio",
    "trailingForwardPeRatio", "trailingPegRatio", "trailingPsRatio",
    "trailingPbRatio", "trailingEnterprisesValueRevenueRatio",
    "trailingEnterprisesValueEBITDARatio",
]

FUNDAMENTAL_TYPES = [
    "trailingTotalRevenue", "trailingGrossProfit", "trailingOperatingIncome",
    "trailingNetIncome", "trailingPretaxIncome", "trailingEBIT",
    "trailingEBITDA", "trailingDilutedEPS", "trailingDilutedAverageShares",
    "trailingOperatingCashFlow", "trailingFreeCashFlow",
    "trailingCapitalExpenditure", "trailingStockBasedCompensation",
    "trailingTaxRateForCalcs",
    "quarterlyTotalAssets", "quarterlyStockholdersEquity", "quarterlyTotalDebt",
    "quarterlyCashCashEquivalentsAndShortTermInvestments",
    "quarterlyCurrentAssets", "quarterlyCurrentLiabilities",
    "quarterlyInvestedCapital", "quarterlyInventory",
    "annualTotalRevenue", "annualGrossProfit", "annualOperatingIncome",
    "annualNetIncome", "annualPretaxIncome", "annualInterestExpense",
    "annualEBITDA", "annualDilutedEPS", "annualDilutedAverageShares",
    "annualOperatingCashFlow", "annualFreeCashFlow", "annualCapitalExpenditure",
    "annualStockBasedCompensation", "annualCommonStockDividendPaid",
    "annualRepurchaseOfCapitalStock", "annualTotalDebt",
    "annualCashCashEquivalentsAndShortTermInvestments",
    "annualStockholdersEquity", "annualCurrentAssets", "annualCurrentLiabilities",
    "annualInvestedCapital",
]

NEWS_CATEGORIES = {
    "Résultats / guidance": ["earnings", "results", "revenue", "profit", "guidance", "forecast", "outlook", "résultats", "prévision"],
    "Produit / innovation": ["launch", "product", "platform", "patent", "approval", "innovation", "lance", "produit", "autorisation"],
    "Contrat / partenariat": ["contract", "order", "partnership", "deal", "customer", "accord", "contrat", "commande", "partenariat"],
    "Capital / M&A": ["acquire", "acquisition", "merger", "buyback", "dividend", "offering", "acquisition", "rachat", "dividende", "fusion"],
    "Réglementaire / juridique": ["regulator", "lawsuit", "antitrust", "investigation", "fda", "regulatory", "justice", "procès", "régulateur"],
    "Direction": ["ceo", "cfo", "chairman", "appoint", "resign", "management", "directeur", "nomme", "démission"],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def round_n(value: Any, digits: int = 4) -> Optional[float]:
    number = finite(value)
    return round(number, digits) if number is not None else None


def ratio(numerator: Any, denominator: Any) -> Optional[float]:
    a, b = finite(numerator), finite(denominator)
    return a / b if a is not None and b not in (None, 0) else None


def pct_change(first: Any, last: Any) -> Optional[float]:
    value = ratio(last, first)
    return (value - 1) * 100 if value is not None else None


def absolute(value: Any) -> Optional[float]:
    number = finite(value)
    return abs(number) if number is not None else None


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def cache_age_hours(item: Dict[str, Any]) -> Optional[float]:
    try:
        stamp = datetime.fromisoformat(str(item["fetched_at"]).replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - stamp).total_seconds() / 3600
    except Exception:
        return None


def request_json(url: str, *, params: Optional[Dict[str, Any]] = None,
                 headers: Optional[Dict[str, str]] = None, timeout: int = 14,
                 attempts: int = 2) -> Dict[str, Any]:
    last_error: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            response = requests.get(url, params=params, headers=headers or YAHOO_HEADERS, timeout=timeout)
            if response.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {response.status_code}", response=response)
            response.raise_for_status()
            value = response.json()
            return value if isinstance(value, dict) else {}
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1.0 + attempt)
    raise last_error or RuntimeError("requête impossible")


def exact_quote(search: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    wanted = symbol.upper()
    for quote in search.get("quotes", []) or []:
        if str(quote.get("symbol") or "").upper() == wanted:
            return quote
    return {}


def classify_news(title: str) -> List[str]:
    low = title.lower()
    found = [label for label, words in NEWS_CATEGORIES.items() if any(word in low for word in words)]
    return found or ["Actualité générale"]


def fetch_search_and_news(symbol: str, fallback_name: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    query = fallback_name if fallback_name and fallback_name.upper() != symbol.upper() else symbol
    data = request_json(YAHOO_SEARCH, params={
        "q": query, "quotesCount": 8, "newsCount": 30,
        "enableFuzzyQuery": "false", "quotesQueryId": "tss_match_phrase_query",
    })
    quote = exact_quote(data, symbol)
    news: List[Dict[str, Any]] = []
    seen = set()
    for article in data.get("news", []) or []:
        related = {str(x).upper() for x in article.get("relatedTickers", []) or []}
        title = clean_text(article.get("title"))
        link = str(article.get("link") or "")
        if related and symbol.upper() not in related:
            continue
        key = hashlib.sha1((title.lower() + link).encode("utf-8")).hexdigest()[:16]
        if not title or not link or key in seen:
            continue
        seen.add(key)
        published = finite(article.get("providerPublishTime"))
        published_at = datetime.fromtimestamp(published, tz=timezone.utc).isoformat().replace("+00:00", "Z") if published else None
        news.append({
            "id": key, "title": title, "publisher": clean_text(article.get("publisher")) or "Source non précisée",
            "url": link, "published_at": published_at, "categories": classify_news(title),
            "related_tickers": sorted(related), "source_type": "media",
        })
    news.sort(key=lambda item: item.get("published_at") or "", reverse=True)
    return quote, news[:16]


def fetch_chart(symbol: str, range_value: str = "10y") -> Dict[str, Any]:
    data = request_json(YAHOO_CHART.format(symbol=requests.utils.quote(symbol, safe="")), params={
        "range": range_value, "interval": "1d", "events": "div,splits",
        "includeAdjustedClose": "true", "includePrePost": "false",
    })
    chart = data.get("chart", {})
    error = chart.get("error")
    if error:
        raise RuntimeError(clean_text(error.get("description")) or "ticker introuvable")
    result = (chart.get("result") or [None])[0]
    if not result:
        raise RuntimeError("historique vide")
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    quote = (indicators.get("quote") or [{}])[0]
    adj = (indicators.get("adjclose") or [{}])[0].get("adjclose") or quote.get("close") or []
    closes = quote.get("close") or []
    rows = []
    for stamp, close, adjusted in zip(timestamps, closes, adj):
        close_value, adjusted_value = finite(close), finite(adjusted)
        if close_value is None:
            continue
        rows.append({
            "date": datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%Y-%m-%d"),
            "close": close_value,
            "adjusted": adjusted_value if adjusted_value is not None else close_value,
        })
    if len(rows) < 2:
        raise RuntimeError("historique insuffisant")
    dividends = []
    for event in ((result.get("events") or {}).get("dividends") or {}).values():
        amount = finite(event.get("amount"))
        stamp = finite(event.get("date"))
        if amount is not None and stamp:
            dividends.append({"date": datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%Y-%m-%d"), "amount": amount})
    return {"meta": result.get("meta") or {}, "rows": rows, "dividends": dividends}


def fetch_timeseries(symbol: str) -> Dict[str, List[Dict[str, Any]]]:
    end = int(time.time() + 86400)
    start = int(time.time() - 7 * 366 * 86400)
    wanted = VALUATION_TYPES + FUNDAMENTAL_TYPES
    output: Dict[str, List[Dict[str, Any]]] = {}
    for offset in range(0, len(wanted), 26):
        chunk = wanted[offset:offset + 26]
        data = request_json(YAHOO_TIMESERIES.format(symbol=requests.utils.quote(symbol, safe="")), params={
            "symbol": symbol, "type": ",".join(chunk), "period1": start, "period2": end,
            "merge": "false",
        })
        for series in (data.get("timeseries") or {}).get("result", []) or []:
            series_type = ((series.get("meta") or {}).get("type") or [None])[0]
            if not series_type:
                continue
            values = []
            for point in series.get(series_type, []) or []:
                raw = finite((point.get("reportedValue") or {}).get("raw"))
                date = point.get("asOfDate")
                if raw is None or not date:
                    continue
                values.append({
                    "date": str(date), "value": raw, "currency": point.get("currencyCode"),
                    "period": point.get("periodType"),
                })
            if values:
                output[series_type] = sorted(values, key=lambda item: item["date"])
    return output


def latest(series: Dict[str, List[Dict[str, Any]]], key: str) -> Optional[float]:
    values = series.get(key) or []
    return finite(values[-1].get("value")) if values else None


def latest_date(series: Dict[str, List[Dict[str, Any]]], keys: Iterable[str]) -> Optional[str]:
    dates = [(series.get(key) or [{}])[-1].get("date") for key in keys if series.get(key)]
    return max((str(x) for x in dates if x), default=None)


def latest_currency(series: Dict[str, List[Dict[str, Any]]], keys: Iterable[str]) -> Optional[str]:
    for key in keys:
        values = series.get(key) or []
        if values and values[-1].get("currency"):
            return str(values[-1]["currency"]).upper()
    return None


def average_latest(series: Dict[str, List[Dict[str, Any]]], key: str, count: int = 2) -> Optional[float]:
    values = [finite(x.get("value")) for x in (series.get(key) or [])[-count:]]
    valid = [x for x in values if x is not None]
    return sum(valid) / len(valid) if valid else None


def period_anchor(last: pd.Timestamp, spec: Dict[str, Any]) -> pd.Timestamp:
    if "days" in spec:
        return last - pd.Timedelta(days=spec["days"])
    if "months" in spec:
        return last - pd.DateOffset(months=spec["months"])
    return last - pd.DateOffset(years=spec.get("years", 0))


def calculate_returns(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    dates = [row["date"] for row in rows]
    last_index = len(rows) - 1
    last = rows[last_index]
    last_stamp = pd.Timestamp(last["date"])
    output = {}
    for key, spec in PERIODS.items():
        if key == "1d":
            start_index = last_index - spec["trading_days"]
        else:
            target = period_anchor(last_stamp, spec).strftime("%Y-%m-%d")
            start_index = bisect.bisect_right(dates, target) - 1
        if start_index < 0 or start_index >= last_index:
            output[key] = {"label": spec["label"], "available": False}
            continue
        start = rows[start_index]
        output[key] = {
            "label": spec["label"], "available": True,
            "start_date": start["date"], "end_date": last["date"],
            "start_price": round_n(start["close"]), "end_price": round_n(last["close"]),
            "price_return_pct": round_n(pct_change(start["close"], last["close"]), 2),
            "total_return_pct": round_n(pct_change(start["adjusted"], last["adjusted"]), 2),
        }
    return output


def price_statistics(rows: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    frame = pd.DataFrame(rows)
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.set_index("date").sort_index()
    adjusted = frame["adjusted"].astype(float)
    close = frame["close"].astype(float)
    daily = adjusted.pct_change().dropna()
    one_year = adjusted.tail(253)
    five_year = adjusted.loc[adjusted.index >= adjusted.index[-1] - pd.DateOffset(years=5)]
    drawdown_1y = one_year / one_year.cummax() - 1 if not one_year.empty else pd.Series(dtype=float)
    drawdown_5y = five_year / five_year.cummax() - 1 if not five_year.empty else pd.Series(dtype=float)
    high = close.tail(253).max() if len(close) else None
    low = close.tail(253).min() if len(close) else None
    current = close.iloc[-1]
    ma50 = close.tail(50).mean() if len(close) >= 20 else None
    ma200 = close.tail(200).mean() if len(close) >= 60 else None
    return {
        "volatility_1y_pct": round_n(daily.tail(252).std() * math.sqrt(252) * 100, 2) if len(daily) >= 30 else None,
        "max_drawdown_1y_pct": round_n(drawdown_1y.min() * 100, 2) if not drawdown_1y.empty else None,
        "max_drawdown_5y_pct": round_n(drawdown_5y.min() * 100, 2) if not drawdown_5y.empty else None,
        "high_52w": round_n(high), "low_52w": round_n(low),
        "distance_52w_high_pct": round_n(pct_change(high, current), 2) if high else None,
        "distance_52w_low_pct": round_n(pct_change(low, current), 2) if low else None,
        "average_50d": round_n(ma50), "average_200d": round_n(ma200),
        "distance_50d_pct": round_n(pct_change(ma50, current), 2) if ma50 else None,
        "distance_200d_pct": round_n(pct_change(ma200, current), 2) if ma200 else None,
    }


def benchmark_for(symbol: str, exchange: str, currency: str) -> Tuple[str, str]:
    upper = symbol.upper()
    exchange_upper = (exchange or "").upper()
    if upper.endswith(".SW") or currency == "CHF" or "SWISS" in exchange_upper:
        return "^SSMI", "SMI"
    if upper.endswith(".PA") or "PARIS" in exchange_upper:
        return "^FCHI", "CAC 40"
    if upper.endswith(".DE") or upper.endswith(".F") or "GER" in exchange_upper:
        return "^GDAXI", "DAX"
    if upper.endswith(".L") or "LONDON" in exchange_upper:
        return "^FTSE", "FTSE 100"
    if upper.endswith(".TO") or "TORONTO" in exchange_upper:
        return "^GSPTSE", "S&P/TSX"
    return "^GSPC", "S&P 500"


def beta_statistics(stock_rows: List[Dict[str, Any]], benchmark_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    stock = pd.Series({pd.Timestamp(x["date"]): x["adjusted"] for x in stock_rows}, dtype=float)
    benchmark = pd.Series({pd.Timestamp(x["date"]): x["adjusted"] for x in benchmark_rows}, dtype=float)
    start = max(stock.index.max(), benchmark.index.max()) - pd.DateOffset(years=2)
    frame = pd.concat(
        [stock.rename("stock"), benchmark.rename("benchmark")], axis=1, sort=False
    ).loc[start:].dropna()
    weekly = frame.resample("W-FRI").last().pct_change().dropna()
    if len(weekly) < 40 or weekly["benchmark"].var() in (0, None):
        return {"beta_2y_weekly": None, "observations": len(weekly)}
    beta = weekly["stock"].cov(weekly["benchmark"]) / weekly["benchmark"].var()
    correlation = weekly["stock"].corr(weekly["benchmark"])
    alpha_weekly = weekly["stock"].mean() - beta * weekly["benchmark"].mean()
    return {
        "beta_2y_weekly": round_n(beta, 3), "correlation_2y_weekly": round_n(correlation, 3),
        "r_squared_2y_weekly": round_n(correlation ** 2, 3),
        "alpha_zero_rate_annual_pct": round_n(alpha_weekly * 52 * 100, 2),
        "observations": len(weekly), "start_date": weekly.index[0].strftime("%Y-%m-%d"),
        "end_date": weekly.index[-1].strftime("%Y-%m-%d"),
    }


def annual_trends(series: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    mapping = {
        "revenue": "annualTotalRevenue", "gross_profit": "annualGrossProfit",
        "operating_income": "annualOperatingIncome", "net_income": "annualNetIncome",
        "eps_diluted": "annualDilutedEPS", "ebitda": "annualEBITDA",
        "operating_cash_flow": "annualOperatingCashFlow", "free_cash_flow": "annualFreeCashFlow",
        "capex": "annualCapitalExpenditure", "stock_compensation": "annualStockBasedCompensation",
        "dividends_paid": "annualCommonStockDividendPaid", "buybacks": "annualRepurchaseOfCapitalStock",
        "debt": "annualTotalDebt", "cash": "annualCashCashEquivalentsAndShortTermInvestments",
        "equity": "annualStockholdersEquity",
    }
    by_date: Dict[str, Dict[str, Any]] = {}
    for output_key, series_key in mapping.items():
        for point in series.get(series_key, []) or []:
            row = by_date.setdefault(point["date"], {"date": point["date"]})
            row[output_key] = round_n(point["value"], 2)
            if point.get("currency") and not row.get("currency"):
                row["currency"] = str(point["currency"]).upper()
    rows = [by_date[key] for key in sorted(by_date) if by_date[key].get("revenue") is not None or by_date[key].get("net_income") is not None][-5:]
    for index, row in enumerate(rows):
        if index:
            row["revenue_growth_pct"] = round_n(pct_change(rows[index - 1].get("revenue"), row.get("revenue")), 2)
            row["eps_growth_pct"] = round_n(pct_change(rows[index - 1].get("eps_diluted"), row.get("eps_diluted")), 2)
            row["fcf_growth_pct"] = round_n(pct_change(rows[index - 1].get("free_cash_flow"), row.get("free_cash_flow")), 2)
    return rows


def build_metrics(series: Dict[str, List[Dict[str, Any]]], chart: Dict[str, Any]) -> Dict[str, Optional[float]]:
    revenue = latest(series, "trailingTotalRevenue")
    gross = latest(series, "trailingGrossProfit")
    operating = latest(series, "trailingOperatingIncome")
    net_income = latest(series, "trailingNetIncome")
    ebit = latest(series, "trailingEBIT")
    ebitda = latest(series, "trailingEBITDA")
    pretax = latest(series, "trailingPretaxIncome")
    cfo = latest(series, "trailingOperatingCashFlow")
    fcf = latest(series, "trailingFreeCashFlow")
    capex = absolute(latest(series, "trailingCapitalExpenditure"))
    sbc = latest(series, "trailingStockBasedCompensation")
    tax_rate = latest(series, "trailingTaxRateForCalcs")
    market_cap = latest(series, "trailingMarketCap")
    debt = latest(series, "quarterlyTotalDebt")
    cash = latest(series, "quarterlyCashCashEquivalentsAndShortTermInvestments")
    equity_avg = average_latest(series, "quarterlyStockholdersEquity")
    assets_avg = average_latest(series, "quarterlyTotalAssets")
    invested_avg = average_latest(series, "quarterlyInvestedCapital")
    current_assets = latest(series, "quarterlyCurrentAssets")
    current_liabilities = latest(series, "quarterlyCurrentLiabilities")
    inventory = latest(series, "quarterlyInventory") or 0
    annual_operating = latest(series, "annualOperatingIncome")
    annual_interest = absolute(latest(series, "annualInterestExpense"))
    latest_annual_net = latest(series, "annualNetIncome")
    dividends_paid = absolute(latest(series, "annualCommonStockDividendPaid"))
    buybacks = absolute(latest(series, "annualRepurchaseOfCapitalStock"))
    trends = annual_trends(series)
    last_growth = trends[-1] if trends else {}
    price = finite(chart["rows"][-1]["close"])
    recent_dividends = sum(
        finite(item.get("amount")) or 0 for item in chart.get("dividends", [])
        if item.get("date") and item["date"] >= (pd.Timestamp(chart["rows"][-1]["date"]) - pd.DateOffset(years=1)).strftime("%Y-%m-%d")
    )
    tax_for_roic = min(0.5, max(0.0, tax_rate)) if tax_rate is not None else 0.21
    net_debt = debt - cash if debt is not None and cash is not None else None
    provider_pe = latest(series, "trailingPeRatio")
    provider_ev_ebitda = latest(series, "trailingEnterprisesValueEBITDARatio")
    provider_price_sales = latest(series, "trailingPsRatio")
    provider_price_book = latest(series, "trailingPbRatio")
    eps = latest(series, "trailingDilutedEPS")
    enterprise_value = latest(series, "trailingEnterpriseValue")
    equity_latest = latest(series, "quarterlyStockholdersEquity")
    quote_currency = str((chart.get("meta") or {}).get("currency") or "").upper() or None
    financial_currency = latest_currency(series, ["trailingTotalRevenue", "annualTotalRevenue"])
    comparable_currency = bool(quote_currency and financial_currency and quote_currency == financial_currency)
    calculated_pe = ratio(price, eps) if comparable_currency and eps is not None and eps > 0 else (provider_pe if eps is not None and eps > 0 else None)
    calculated_ev_ebitda = ratio(enterprise_value, ebitda) if comparable_currency and ebitda is not None and ebitda > 0 and enterprise_value is not None and enterprise_value > 0 else (provider_ev_ebitda if ebitda is not None and ebitda > 0 else None)
    calculated_price_sales = ratio(market_cap, revenue) if comparable_currency and revenue is not None and revenue > 0 else provider_price_sales
    calculated_price_book = ratio(market_cap, equity_latest) if comparable_currency and equity_latest is not None and equity_latest > 0 else provider_price_book
    metrics = {
        "price": price,
        "market_cap": market_cap,
        "enterprise_value": enterprise_value,
        "trailing_pe": calculated_pe,
        "provider_trailing_pe": provider_pe,
        "forward_pe": latest(series, "trailingForwardPeRatio"),
        "peg_ratio": latest(series, "trailingPegRatio"),
        "price_sales": calculated_price_sales,
        "provider_price_sales": provider_price_sales,
        "price_book": calculated_price_book,
        "provider_price_book": provider_price_book,
        "ev_revenue": latest(series, "trailingEnterprisesValueRevenueRatio"),
        "ev_ebitda": calculated_ev_ebitda,
        "provider_ev_ebitda": provider_ev_ebitda,
        "earnings_yield_pct": (100 / calculated_pe) if calculated_pe is not None and calculated_pe > 0 else None,
        "fcf_yield_pct": ratio(fcf, market_cap) * 100 if comparable_currency and ratio(fcf, market_cap) is not None else None,
        "revenue": revenue, "gross_profit": gross, "operating_income": operating,
        "net_income": net_income, "ebitda": ebitda, "eps_diluted": eps,
        "revenue_growth_pct": last_growth.get("revenue_growth_pct"),
        "eps_growth_pct": last_growth.get("eps_growth_pct"),
        "fcf_growth_pct": last_growth.get("fcf_growth_pct"),
        "gross_margin_pct": ratio(gross, revenue) * 100 if ratio(gross, revenue) is not None else None,
        "operating_margin_pct": ratio(operating, revenue) * 100 if ratio(operating, revenue) is not None else None,
        "net_margin_pct": ratio(net_income, revenue) * 100 if ratio(net_income, revenue) is not None else None,
        "fcf_margin_pct": ratio(fcf, revenue) * 100 if ratio(fcf, revenue) is not None else None,
        "roe_pct": ratio(net_income, equity_avg) * 100 if equity_avg is not None and equity_avg > 0 and ratio(net_income, equity_avg) is not None else None,
        "roa_pct": ratio(net_income, assets_avg) * 100 if assets_avg is not None and assets_avg > 0 and ratio(net_income, assets_avg) is not None else None,
        "roic_approx_pct": ratio(ebit * (1 - tax_for_roic) if ebit is not None else None, invested_avg) * 100 if invested_avg is not None and invested_avg > 0 and ratio(ebit * (1 - tax_for_roic) if ebit is not None else None, invested_avg) is not None else None,
        "operating_cash_flow": cfo, "free_cash_flow": fcf, "capex": capex,
        "stock_compensation": sbc,
        "cash_conversion_pct": ratio(cfo, net_income) * 100 if ratio(cfo, net_income) is not None else None,
        "capex_intensity_pct": ratio(capex, revenue) * 100 if ratio(capex, revenue) is not None else None,
        "stock_compensation_revenue_pct": ratio(sbc, revenue) * 100 if ratio(sbc, revenue) is not None else None,
        "cash": cash, "total_debt": debt, "net_debt": net_debt,
        "debt_equity_pct": ratio(debt, equity_avg) * 100 if equity_avg is not None and equity_avg > 0 and ratio(debt, equity_avg) is not None else None,
        "net_debt_ebitda": ratio(net_debt, ebitda) if ebitda is not None and ebitda > 0 else None,
        "current_ratio": ratio(current_assets, current_liabilities),
        "quick_ratio": ratio((current_assets - inventory) if current_assets is not None else None, current_liabilities),
        "interest_coverage": ratio(annual_operating, annual_interest),
        "dividend_yield_pct": ratio(recent_dividends, price) * 100 if ratio(recent_dividends, price) is not None else None,
        "payout_ratio_pct": ratio(dividends_paid, latest_annual_net) * 100 if latest_annual_net is not None and latest_annual_net > 0 and ratio(dividends_paid, latest_annual_net) is not None else None,
        "buyback_yield_pct": ratio(buybacks, market_cap) * 100 if comparable_currency and ratio(buybacks, market_cap) is not None else None,
        "shares_diluted": latest(series, "trailingDilutedAverageShares"),
        "tax_rate_pct": tax_rate * 100 if tax_rate is not None else None,
        "pretax_income": pretax,
    }
    return {key: round_n(value, 4) for key, value in metrics.items()}


def assessment(metric: str, value: Optional[float]) -> Dict[str, str]:
    if value is None:
        return {"status": "missing", "label": "Indisponible"}
    favorable_high = {
        "fcf_yield_pct": (5, 3), "revenue_growth_pct": (10, 0), "eps_growth_pct": (10, 0),
        "gross_margin_pct": (40, 20), "operating_margin_pct": (15, 5), "net_margin_pct": (10, 3),
        "fcf_margin_pct": (10, 3), "roe_pct": (15, 8), "roa_pct": (8, 3),
        "roic_approx_pct": (12, 7), "cash_conversion_pct": (100, 70),
        "current_ratio": (1.2, 1.0), "quick_ratio": (1.0, 0.7), "interest_coverage": (5, 2),
    }
    favorable_low = {
        "trailing_pe": (18, 30), "forward_pe": (18, 30), "peg_ratio": (1.2, 2.0),
        "ev_ebitda": (10, 16), "price_sales": (3, 8), "price_book": (3, 6),
        "net_debt_ebitda": (1.5, 3.0), "debt_equity_pct": (60, 150),
        "stock_compensation_revenue_pct": (3, 8), "payout_ratio_pct": (60, 90),
    }
    if metric in favorable_high:
        good, caution = favorable_high[metric]
        if value >= good:
            return {"status": "favorable", "label": "Repère favorable"}
        if value < caution:
            return {"status": "caution", "label": "À examiner"}
        return {"status": "neutral", "label": "Zone intermédiaire"}
    if metric in favorable_low:
        good, caution = favorable_low[metric]
        if metric in ("net_debt_ebitda", "debt_equity_pct") and value < 0:
            return {"status": "favorable", "label": "Trésorerie nette / faible dette"}
        if value <= good and value > 0:
            return {"status": "favorable", "label": "Repère favorable"}
        if value > caution or value <= 0:
            return {"status": "caution", "label": "À examiner"}
        return {"status": "neutral", "label": "Zone intermédiaire"}
    return {"status": "context", "label": "À lire dans son contexte"}


def make_analysis(metrics: Dict[str, Optional[float]], risk: Dict[str, Any]) -> Dict[str, Any]:
    positives, cautions, questions = [], [], []
    def add(target: List[Dict[str, str]], title: str, text: str, rule: str) -> None:
        target.append({"title": title, "text": text, "rule": rule})
    if (metrics.get("roic_approx_pct") or -999) >= 12:
        add(positives, "Rentabilité du capital élevée", f"ROIC approximatif de {metrics['roic_approx_pct']:.1f} %.", "ROIC approximatif ≥ 12 %")
    if (metrics.get("fcf_margin_pct") or -999) >= 10:
        add(positives, "Bonne génération de trésorerie", f"Marge de FCF de {metrics['fcf_margin_pct']:.1f} %.", "Marge FCF ≥ 10 %")
    if (metrics.get("revenue_growth_pct") or -999) >= 10:
        add(positives, "Croissance du chiffre d’affaires", f"Dernière croissance annuelle disponible : {metrics['revenue_growth_pct']:.1f} %.", "Croissance annuelle ≥ 10 %")
    if metrics.get("net_debt_ebitda") is not None and metrics["net_debt_ebitda"] <= 1.5:
        add(positives, "Bilan contenu", f"Dette nette / EBITDA : {metrics['net_debt_ebitda']:.2f}×.", "Dette nette / EBITDA ≤ 1,5×")
    if (metrics.get("fcf_yield_pct") or -999) >= 5:
        add(positives, "Rendement du FCF significatif", f"FCF yield de {metrics['fcf_yield_pct']:.1f} %.", "FCF yield ≥ 5 %")
    if metrics.get("free_cash_flow") is not None and metrics["free_cash_flow"] <= 0:
        add(cautions, "Flux de trésorerie disponible négatif", "La valorisation par FCF n’est pas interprétable sur cette période.", "FCF ≤ 0")
    if metrics.get("revenue_growth_pct") is not None and metrics["revenue_growth_pct"] < 0:
        add(cautions, "Contraction du chiffre d’affaires", f"Dernière variation annuelle : {metrics['revenue_growth_pct']:.1f} %.", "Croissance annuelle < 0 %")
    if metrics.get("net_debt_ebitda") is not None and metrics["net_debt_ebitda"] > 3:
        add(cautions, "Levier financier élevé", f"Dette nette / EBITDA : {metrics['net_debt_ebitda']:.2f}×.", "Dette nette / EBITDA > 3×")
    if metrics.get("trailing_pe") is not None and metrics["trailing_pe"] > 35:
        add(cautions, "Valorisation exigeante sur les bénéfices", f"P/E LTM de {metrics['trailing_pe']:.1f}×.", "P/E LTM > 35×")
    if (metrics.get("stock_compensation_revenue_pct") or 0) > 8:
        add(cautions, "Rémunération en actions importante", f"SBC / chiffre d’affaires : {metrics['stock_compensation_revenue_pct']:.1f} %.", "SBC / CA > 8 %")
    if risk.get("distance_200d_pct") is not None and risk["distance_200d_pct"] < -15:
        add(cautions, "Tendance de marché affaiblie", f"Cours à {risk['distance_200d_pct']:.1f} % de sa moyenne 200 jours.", "Écart à la MM200 < −15 %")
    if not positives:
        questions.append("Aucun signal favorable ne franchit les repères généraux ; comparer au secteur et aux concurrents.")
    if not cautions:
        questions.append("Aucun drapeau quantitatif majeur selon les repères généraux ; vérifier néanmoins gouvernance, concurrence et cyclicité.")
    questions.extend([
        "La croissance provient-elle des volumes, des prix, des acquisitions ou d’un effet de change ?",
        "Quels éléments pourraient réduire durablement les marges ou le rendement du capital ?",
        "Le prix actuel exige-t-il une croissance supérieure à celle que l’entreprise peut soutenir ?",
    ])
    return {"strengths": positives[:6], "cautions": cautions[:6], "questions": questions[:5]}


def reconcile_ratios(metrics: Dict[str, Optional[float]], quote_currency: Optional[str],
                     financial_currency: Optional[str]) -> List[str]:
    notes: List[str] = []
    provider_pe = metrics.get("provider_trailing_pe")
    calculated_pe = metrics.get("trailing_pe")
    eps = metrics.get("eps_diluted")
    if eps is not None and eps <= 0 and provider_pe is not None:
        notes.append(
            f"Le fournisseur publie un P/E de {provider_pe:.2f}×, mais le BPA LTM est {eps:.2f}. "
            "Le P/E est donc déclaré non interprétable."
        )
    elif quote_currency and financial_currency and quote_currency != financial_currency:
        notes.append(
            f"La cotation est en {quote_currency} et les comptes en {financial_currency}. "
            "Les multiples fournisseur sont conservés lorsque leur recalcul nécessiterait le taux de change et le ratio du titre coté."
        )
    elif provider_pe is not None and calculated_pe is not None and calculated_pe and abs(provider_pe / calculated_pe - 1) > 0.05:
        notes.append(
            f"P/E fournisseur {provider_pe:.2f}× contre {calculated_pe:.2f}× recalculé avec le cours et le BPA LTM disponibles."
        )
    if metrics.get("roic_approx_pct") is not None and abs(metrics["roic_approx_pct"]) > 100:
        notes.append(
            "Le ROIC approximatif dépasse 100 % : le capital investi publié est faible ou négatif à certaines dates. "
            "Vérifier le fonds de roulement, les rachats d’actions et la définition sectorielle avant interprétation."
        )
    return notes


def downsample_chart(stock_rows: List[Dict[str, Any]], benchmark_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    stock = pd.Series({pd.Timestamp(x["date"]): x["adjusted"] for x in stock_rows}, dtype=float)
    benchmark = pd.Series({pd.Timestamp(x["date"]): x["adjusted"] for x in benchmark_rows}, dtype=float)
    end = stock.index.max()
    frame = pd.concat(
        [stock.rename("stock"), benchmark.rename("benchmark")], axis=1, sort=False
    ).loc[end - pd.DateOffset(years=5):].ffill().dropna()
    if frame.empty:
        return []
    weekly = frame.resample("W-FRI").last().dropna()
    base = weekly.iloc[0]
    return [
        {"date": index.strftime("%Y-%m-%d"), "stock": round(float(row.stock / base.stock * 100), 2),
         "benchmark": round(float(row.benchmark / base.benchmark * 100), 2)}
        for index, row in weekly.iterrows()
    ]


def fetch_sec_mapping(cache: Dict[str, Any]) -> Dict[str, str]:
    cached = cache.get("sec_mapping") or {}
    age = cache_age_hours(cached) if cached else None
    if cached and age is not None and age < 168:
        return cached.get("payload") or {}
    try:
        raw = request_json(SEC_TICKERS, headers=SEC_HEADERS, timeout=18)
        mapping = {str(item.get("ticker") or "").upper(): str(item.get("cik_str") or "").zfill(10)
                   for item in raw.values() if item.get("ticker") and item.get("cik_str") is not None}
        cache["sec_mapping"] = {"fetched_at": now_iso(), "payload": mapping}
        return mapping
    except Exception:
        return cached.get("payload") or {}


def fetch_sec_profile(cik: Optional[str]) -> Dict[str, Any]:
    if not cik:
        return {"available": False, "reason": "Aucun CIK SEC associé à cette ligne."}
    data = request_json(SEC_SUBMISSIONS.format(cik=cik), headers=SEC_HEADERS, timeout=14)
    recent = (data.get("filings") or {}).get("recent") or {}
    filings = []
    forms = recent.get("form") or []
    for index, form in enumerate(forms):
        if form not in {"10-K", "10-Q", "8-K", "20-F", "40-F", "6-K"}:
            continue
        try:
            accession = recent["accessionNumber"][index]
            primary = recent["primaryDocument"][index]
            accession_flat = accession.replace("-", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_flat}/{primary}"
            filings.append({
                "form": form, "filed_at": recent["filingDate"][index],
                "period_end": recent.get("reportDate", [None] * len(forms))[index],
                "url": url, "accession": accession,
            })
        except Exception:
            continue
        if len(filings) >= 12:
            break
    address = data.get("addresses") or {}
    return {
        "available": True, "cik": cik, "name": data.get("name"),
        "sic": data.get("sic"), "sic_description": data.get("sicDescription"),
        "fiscal_year_end": data.get("fiscalYearEnd"), "state_of_incorporation": data.get("stateOfIncorporation"),
        "business_address": address.get("business"), "phone": data.get("phone"),
        "filings": filings, "source_url": f"https://www.sec.gov/edgar/browse/?CIK={cik}",
    }


def source_status(errors: List[str], cache_used: bool) -> str:
    if cache_used:
        return "cache"
    return "degraded" if errors else "active"


def make_empty_dossier(item: Dict[str, Any], error: str, cached: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if cached:
        value = dict(cached)
        value["served_from_cache"] = True
        value["refresh_error"] = error
        return value
    return {
        "ticker": item["ticker"], "name": item.get("label") or item["ticker"],
        "theme": item.get("theme"), "note": item.get("note"), "status": "unavailable",
        "error": error, "returns": {}, "metrics": {}, "risk": {}, "news": [], "filings": [],
        "analysis": {"strengths": [], "cautions": [], "questions": []},
    }


def build_dossier(item: Dict[str, Any], sec_mapping: Dict[str, str],
                  benchmark_cache: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    symbol = str(item["ticker"]).strip().upper()
    chart = fetch_chart(symbol)
    fallback_name = str(item.get("label") or symbol)
    quote, news = fetch_search_and_news(symbol, fallback_name)
    meta = chart.get("meta") or {}
    instrument_type = clean_text(
        quote.get("quoteType") or meta.get("instrumentType")
    ).upper()
    non_company_types = {
        "ETF", "INDEX", "MUTUALFUND", "FUTURE", "CURRENCY", "CRYPTOCURRENCY"
    }
    # Market-only instruments have no corporate accounts. Avoid pointless
    # fundamentals calls while retaining prices, returns, risk and news.
    series = {} if instrument_type in non_company_types else fetch_timeseries(symbol)
    currency = str(meta.get("currency") or "")
    exchange = str(meta.get("fullExchangeName") or meta.get("exchangeName") or quote.get("exchDisp") or quote.get("exchange") or "")
    name = clean_text(quote.get("longname") or quote.get("shortname") or meta.get("longName") or meta.get("shortName") or fallback_name)
    benchmark_symbol, benchmark_label = benchmark_for(symbol, exchange, currency)
    if benchmark_symbol not in benchmark_cache:
        benchmark_cache[benchmark_symbol] = fetch_chart(benchmark_symbol)
    benchmark = benchmark_cache[benchmark_symbol]
    returns = calculate_returns(chart["rows"])
    risk = price_statistics(chart["rows"])
    risk.update(beta_statistics(chart["rows"], benchmark["rows"]))
    risk["benchmark_symbol"] = benchmark_symbol
    risk["benchmark_label"] = benchmark_label
    metrics = build_metrics(series, chart)
    risk["provider_beta"] = None
    assessments = {key: assessment(key, value) for key, value in metrics.items()}
    assessment_risk_keys = ["volatility_1y_pct", "max_drawdown_1y_pct", "max_drawdown_5y_pct", "beta_2y_weekly", "distance_52w_high_pct", "distance_200d_pct"]
    assessments.update({key: assessment(key, risk.get(key)) for key in assessment_risk_keys})
    cik = (
        sec_mapping.get(symbol)
        if instrument_type == "EQUITY" and not symbol.startswith("^") and "." not in symbol
        else None
    )
    sec_errors: List[str] = []
    try:
        sec = fetch_sec_profile(cik)
    except Exception as exc:
        sec_errors.append(f"SEC EDGAR: {type(exc).__name__}")
        sec = {"available": False, "reason": "SEC temporairement indisponible.", "filings": []}
    financial_as_of = latest_date(series, FUNDAMENTAL_TYPES)
    financial_currency = latest_currency(series, ["trailingTotalRevenue", "annualTotalRevenue", "trailingNetIncome"])
    coverage_keys = ["market_cap", "trailing_pe", "ev_ebitda", "revenue", "revenue_growth_pct", "operating_margin_pct", "free_cash_flow", "fcf_yield_pct", "roic_approx_pct", "net_debt_ebitda", "current_ratio"]
    coverage = sum(metrics.get(key) is not None for key in coverage_keys) / len(coverage_keys) * 100
    profile = {
        "name": name, "symbol": symbol, "exchange": exchange, "currency": currency,
        "quote_type": instrument_type or None,
        "sector": quote.get("sectorDisp") or quote.get("sector"),
        "industry": quote.get("industryDisp") or quote.get("industry"),
        "financial_currency": financial_currency,
        "first_trade_date": datetime.fromtimestamp(meta["firstTradeDate"], tz=timezone.utc).strftime("%Y-%m-%d") if finite(meta.get("firstTradeDate")) else None,
        "timezone": meta.get("exchangeTimezoneName") or meta.get("timezone"),
        "business_description": None,
    }
    filings = sec.get("filings") or []
    analysis = make_analysis(metrics, risk)
    return {
        "ticker": symbol, "name": name, "theme": item.get("theme"), "note": item.get("note"),
        "status": "ok", "engine_version": ENGINE_VERSION, "generated_at": now_iso(), "served_from_cache": False,
        "profile": profile, "quote": {
            "price": round_n(chart["rows"][-1]["close"]), "currency": currency,
            "market_date": chart["rows"][-1]["date"], "exchange": exchange,
        },
        "returns": returns, "metrics": metrics, "assessments": assessments,
        "risk": risk, "financial_trends": annual_trends(series),
        "chart_5y": downsample_chart(chart["rows"], benchmark["rows"]),
        "news": news, "sec": sec, "filings": filings, "analysis": analysis,
        "reconciliation": reconcile_ratios(metrics, currency or None, financial_currency),
        "data_quality": {
            "coverage_pct": round(coverage, 1), "market_as_of": chart["rows"][-1]["date"],
            "financial_as_of": financial_as_of, "sec_status": "active" if sec.get("available") else "unavailable",
            "quote_currency": currency or None, "financial_currency": financial_currency,
            "ratio_currency_compatible": bool(currency and financial_currency and currency == financial_currency),
            "errors": sec_errors,
        },
        "sources": {
            "market": {"provider": "Yahoo Finance public endpoints", "kind": "secondary", "url": f"https://finance.yahoo.com/quote/{requests.utils.quote(symbol, safe='')}"},
            "financials": {"provider": "Yahoo Finance fundamentals timeseries", "kind": "secondary", "as_of": financial_as_of},
            "filings": {"provider": "SEC EDGAR", "kind": "primary", "url": sec.get("source_url")},
            "news": {"provider": "Yahoo Finance search/news", "kind": "aggregator"},
        },
    }


def normalize_universe() -> List[Dict[str, Any]]:
    watchlist = load_json(ROOT / "watchlist.json", {})
    seen, output = set(), []
    for item in watchlist.get("items", []) or []:
        ticker = str(item.get("ticker") or "").strip().upper()
        if not ticker or ticker in seen or ticker.startswith("CASH"):
            continue
        seen.add(ticker)
        output.append({**item, "ticker": ticker})
    manual = [x.strip().upper() for x in os.getenv("COMPANY_LAB_TICKERS", "").split(",") if x.strip()]
    for ticker in manual:
        if ticker not in seen and re.fullmatch(r"[A-Z0-9.\^=\-]{1,24}", ticker):
            output.append({"ticker": ticker, "label": ticker, "theme": "Analyse ponctuelle", "note": "Ajout manuel"})
            seen.add(ticker)
    # Keep an explicit safety ceiling while allowing the complete personal
    # universe, including equities, ETFs, indices, commodities and crypto.
    return output[:100]


def build() -> Dict[str, Any]:
    cache = load_json(CACHE_PATH, {"version": 1, "dossiers": {}, "sec_mapping": {}})
    cache.setdefault("dossiers", {})
    universe = normalize_universe()
    sec_mapping = fetch_sec_mapping(cache)
    benchmark_cache: Dict[str, Dict[str, Any]] = {}
    results: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []

    def worker(item: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        ticker = item["ticker"]
        cached_item = (cache.get("dossiers") or {}).get(ticker)
        age = cache_age_hours(cached_item) if cached_item else None
        if cached_item and cached_item.get("engine_version") == ENGINE_VERSION and age is not None and age < 4:
            dossier = dict(cached_item.get("payload") or {})
            dossier["served_from_cache"] = True
            return ticker, dossier
        try:
            dossier = build_dossier(item, sec_mapping, benchmark_cache)
            return ticker, dossier
        except Exception as exc:
            message = f"{type(exc).__name__}: {clean_text(exc)}"
            fallback = (cached_item or {}).get("payload")
            return ticker, make_empty_dossier(item, message, fallback)

    # A small pool keeps runtime bounded without hammering free public endpoints.
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(worker, item) for item in universe]
        for future in concurrent.futures.as_completed(futures):
            ticker, dossier = future.result()
            results[ticker] = dossier
            if dossier.get("status") == "unavailable" or dossier.get("refresh_error"):
                errors.append(f"{ticker}: {dossier.get('refresh_error') or dossier.get('error')}")
            if dossier.get("status") == "ok" and not dossier.get("served_from_cache"):
                cache["dossiers"][ticker] = {"engine_version": ENGINE_VERSION, "fetched_at": now_iso(), "payload": dossier}

    ordered = [results[item["ticker"]] for item in universe if item["ticker"] in results]
    available = sum(item.get("status") == "ok" for item in ordered)
    cached_count = sum(bool(item.get("served_from_cache")) for item in ordered)
    output = {
        "schema_version": 1, "generated_at": now_iso(),
        "status": "ok" if available == len(ordered) else ("degraded" if available else "unavailable"),
        "universe_count": len(ordered), "available_count": available, "cached_count": cached_count,
        "periods": PERIODS, "dossiers": ordered, "errors": errors,
        "provider_status": {
            "yahoo": {"status": "active" if available else "unavailable", "cost": "gratuit, sans clé", "usage": "recherche personnelle"},
            "sec": {"status": "active" if sec_mapping else "degraded", "cost": "gratuit, sans clé", "kind": "source primaire"},
        },
        "methodology": {
            "price_returns": "Variation des cours de clôture non ajustés, du dernier jour de bourse antérieur à la dernière clôture disponible.",
            "total_returns": "Variation des cours ajustés Yahoo, approximation avec dividendes et splits réinvestis.",
            "beta": "Covariance / variance sur rendements hebdomadaires ajustés, fenêtre de deux ans, indice local indiqué.",
            "roic": "NOPAT approximatif / capital investi moyen ; taux d’impôt publié ou 21 % par défaut.",
            "limits": "Cours potentiellement retardés. Les seuils sont généraux et doivent être comparés au secteur. Les ETF, indices, matières premières et cryptoactifs n’ont pas de ratios comptables de société. Frais, impôts et change de l’investisseur ne sont pas inclus.",
        },
    }
    cache["last_run"] = output["generated_at"]
    save_json(CACHE_PATH, cache)
    save_json(OUTPUT_PATH, output)
    return output


if __name__ == "__main__":
    result = build()
    print(f"Company Lab: {result['available_count']}/{result['universe_count']} dossiers disponibles · cache {result['cached_count']}")
    for error in result.get("errors", []):
        print("-", error)
