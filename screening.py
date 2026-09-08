import math
from collections import defaultdict
from typing import Any, Dict, List, Optional

import yfinance as yf
from yfinance import EquityQuery


def finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def pct(value: Any) -> Optional[float]:
    number = finite(value)
    return round(number * 100, 2) if number is not None else None


def round_n(value: Any, digits: int = 2) -> Optional[float]:
    number = finite(value)
    return round(number, digits) if number is not None else None


def change_pct(first: Any, last: Any) -> Optional[float]:
    first_n, last_n = finite(first), finite(last)
    if first_n in (None, 0) or last_n is None:
        return None
    return round((last_n / first_n - 1) * 100, 2)


def response_quotes(response: Any) -> List[Dict[str, Any]]:
    if isinstance(response, dict):
        quotes = response.get("quotes")
        return quotes if isinstance(quotes, list) else []
    return []


def discover_candidates(config: Dict[str, Any]) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    max_candidates = int(config.get("max_candidates_enriched", 36))
    sources = config.get("discovery_sources", [])
    source_results: List[tuple[str, List[Dict[str, Any]]]] = []
    errors: List[str] = []

    for source in sources:
        source_type = source.get("type")
        try:
            if source_type == "predefined":
                query_name = str(source.get("query"))
                response = yf.screen(query_name, count=int(source.get("count", 30)))
                quotes = response_quotes(response)
                source_results.append((query_name, quotes))
            elif source_type == "regions":
                regions = [str(x) for x in source.get("regions", [])]
                if not regions:
                    continue
                query = EquityQuery("and", [
                    EquityQuery("is-in", ["region", *regions]),
                    EquityQuery("gte", ["intradaymarketcap", float(config.get("minimum_market_cap", 2_000_000_000))]),
                    EquityQuery("gte", ["intradayprice", 5]),
                ])
                response = yf.screen(
                    query,
                    size=int(source.get("size", 60)),
                    sortField="intradaymarketcap",
                    sortAsc=False,
                )
                source_results.append(("europe_large_mid_caps", response_quotes(response)))
        except Exception as exc:
            errors.append(f"{source_type}: {type(exc).__name__}: {exc}")

    selected: Dict[str, Dict[str, Any]] = {}
    source_map: Dict[str, set[str]] = defaultdict(set)
    per_source = max(5, math.ceil(max_candidates / max(1, len(source_results))))

    def add_quote(source_name: str, quote: Dict[str, Any]) -> None:
        symbol = str(quote.get("symbol") or "").strip()
        if not symbol:
            return
        quote_type = str(quote.get("quoteType") or "").upper()
        if quote_type and quote_type not in {"EQUITY"}:
            return
        market_cap = finite(quote.get("marketCap") or quote.get("intradaymarketcap"))
        if market_cap is not None and market_cap < float(config.get("minimum_market_cap", 2_000_000_000)):
            return
        source_map[symbol].add(source_name)
        if symbol not in selected:
            selected[symbol] = quote

    for source_name, quotes in source_results:
        added = 0
        for quote in quotes:
            before = len(selected)
            add_quote(source_name, quote)
            if len(selected) > before:
                added += 1
            if added >= per_source or len(selected) >= max_candidates:
                break
        if len(selected) >= max_candidates:
            break

    if len(selected) < max_candidates:
        for source_name, quotes in source_results:
            for quote in quotes:
                add_quote(source_name, quote)
                if len(selected) >= max_candidates:
                    break
            if len(selected) >= max_candidates:
                break

    candidates = []
    for symbol, quote in selected.items():
        candidates.append({
            "ticker": symbol,
            "discovery_sources": sorted(source_map[symbol]),
            "preliminary_name": quote.get("shortName") or quote.get("longName") or symbol,
            "preliminary_market_cap": quote.get("marketCap") or quote.get("intradaymarketcap"),
            "preliminary_price": quote.get("regularMarketPrice") or quote.get("intradayprice"),
        })

    return candidates, {
        "sources_attempted": len(sources),
        "sources_succeeded": len(source_results),
        "discovered": len(selected),
        "errors": errors,
    }


def history_metrics(ticker: yf.Ticker) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    try:
        hist = ticker.history(period="1y", auto_adjust=True)
        if hist.empty:
            return result
        closes = hist["Close"].dropna()
        if len(closes) < 2:
            return result

        result["last_price"] = round_n(closes.iloc[-1], 4)
        windows = {
            "momentum_1m_pct": 22,
            "momentum_3m_pct": 66,
            "momentum_6m_pct": 132,
            "momentum_12m_pct": len(closes),
        }
        for key, size in windows.items():
            subset = closes.tail(min(size, len(closes)))
            result[key] = change_pct(subset.iloc[0], subset.iloc[-1])

        returns = closes.pct_change().dropna()
        recent = returns.tail(min(66, len(returns)))
        if len(recent) >= 10:
            result["volatility_pct"] = round(float(recent.std()) * math.sqrt(252) * 100, 2)

        running_max = closes.cummax()
        drawdowns = closes / running_max - 1
        result["max_drawdown_1y_pct"] = round(float(drawdowns.min()) * 100, 2)
    except Exception as exc:
        result["history_error"] = f"{type(exc).__name__}: {exc}"
    return result


def enrich_candidate(candidate: Dict[str, Any]) -> Dict[str, Any]:
    ticker_symbol = candidate["ticker"]
    record = dict(candidate)
    try:
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.info or {}
        revenue = finite(info.get("totalRevenue"))
        free_cash_flow = finite(info.get("freeCashflow"))

        record.update({
            "name": info.get("shortName") or info.get("longName") or candidate.get("preliminary_name") or ticker_symbol,
            "sector": info.get("sector") or "Unknown",
            "industry": info.get("industry"),
            "currency": info.get("currency"),
            "market_cap": finite(info.get("marketCap")) or finite(candidate.get("preliminary_market_cap")),
            "forward_pe": round_n(info.get("forwardPE"), 2),
            "trailing_pe": round_n(info.get("trailingPE"), 2),
            "ev_to_ebitda": round_n(info.get("enterpriseToEbitda"), 2),
            "price_to_book": round_n(info.get("priceToBook"), 2),
            "revenue_growth_pct": pct(info.get("revenueGrowth")),
            "earnings_growth_pct": pct(info.get("earningsGrowth")),
            "net_margin_pct": pct(info.get("profitMargins")),
            "operating_margin_pct": pct(info.get("operatingMargins")),
            "roe_pct": pct(info.get("returnOnEquity")),
            "roa_pct": pct(info.get("returnOnAssets")),
            "debt_to_equity": round_n(info.get("debtToEquity"), 2),
            "current_ratio": round_n(info.get("currentRatio"), 2),
            "free_cash_flow": round_n(free_cash_flow, 0),
            "free_cash_flow_margin_pct": round((free_cash_flow / revenue) * 100, 2) if free_cash_flow is not None and revenue not in (None, 0) else None,
            "beta": round_n(info.get("beta"), 2),
        })
        record.update(history_metrics(ticker))
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
    return record


def percentile_score(value: Optional[float], peers: List[float], higher_better: bool = True) -> Optional[float]:
    if value is None or not peers:
        return None
    clean = sorted(v for v in peers if v is not None and math.isfinite(v))
    if not clean:
        return None
    if len(clean) == 1:
        return 50.0
    below = sum(1 for v in clean if v < value)
    equal = sum(1 for v in clean if v == value)
    percentile = (below + max(0, equal - 1) / 2) / (len(clean) - 1) * 100
    score = percentile if higher_better else 100 - percentile
    return max(0.0, min(100.0, score))


def peer_values(records: List[Dict[str, Any]], record: Dict[str, Any], key: str) -> List[float]:
    sector = record.get("sector")
    sector_values = [
        finite(x.get(key)) for x in records
        if x.get("sector") == sector and finite(x.get(key)) is not None
    ]
    if len(sector_values) >= 4:
        return [x for x in sector_values if x is not None]
    return [finite(x.get(key)) for x in records if finite(x.get(key)) is not None]


def avg_score(values: List[Optional[float]], default: float = 50.0) -> float:
    clean = [v for v in values if v is not None]
    return sum(clean) / len(clean) if clean else default


def absolute_cashflow_score(record: Dict[str, Any]) -> float:
    fcf = finite(record.get("free_cash_flow"))
    margin = finite(record.get("free_cash_flow_margin_pct"))
    if fcf is None:
        return 45
    if fcf < 0:
        return 15
    if margin is None:
        return 60
    if margin >= 20:
        return 90
    if margin >= 10:
        return 78
    if margin >= 5:
        return 68
    return 55


def momentum_score(record: Dict[str, Any], anti_chase: Dict[str, Any]) -> tuple[float, bool]:
    m1 = finite(record.get("momentum_1m_pct"))
    m3 = finite(record.get("momentum_3m_pct"))
    m6 = finite(record.get("momentum_6m_pct"))
    m12 = finite(record.get("momentum_12m_pct"))

    def leg(value: Optional[float], ideal_low: float, ideal_high: float) -> float:
        if value is None:
            return 50
        if value < -25:
            return 15
        if value < 0:
            return 30 + max(-20, value) / 20 * 15
        if value <= ideal_low:
            return 50 + value / max(ideal_low, 1) * 20
        if value <= ideal_high:
            return 70 + (value - ideal_low) / max(ideal_high - ideal_low, 1) * 20
        if value <= ideal_high * 1.6:
            return 90 - (value - ideal_high) / max(ideal_high * 0.6, 1) * 20
        return 55

    base = 0.45 * leg(m6, 8, 30) + 0.45 * leg(m12, 12, 55) + 0.10 * leg(m3, 4, 18)
    overheated = (
        (m1 is not None and m1 > float(anti_chase.get("max_1m_pct", 22)))
        or (m3 is not None and m3 > float(anti_chase.get("max_3m_pct", 40)))
    )
    if overheated:
        base -= 22
    return max(0, min(100, base)), overheated


def score_records(records: List[Dict[str, Any]], config: Dict[str, Any]) -> List[Dict[str, Any]]:
    weights = config.get("weights", {})
    anti_chase = config.get("anti_chase", {})
    weight_sum = sum(float(x) for x in weights.values()) or 100

    for record in records:
        margin = finite(record.get("net_margin_pct"))
        roe = finite(record.get("roe_pct"))
        roa = finite(record.get("roa_pct"))
        rev_growth = finite(record.get("revenue_growth_pct"))
        eps_growth = finite(record.get("earnings_growth_pct"))
        pe = finite(record.get("forward_pe"))
        ev_ebitda = finite(record.get("ev_to_ebitda"))
        de = finite(record.get("debt_to_equity"))
        current = finite(record.get("current_ratio"))
        vol = finite(record.get("volatility_pct"))
        beta = finite(record.get("beta"))

        quality = avg_score([
            percentile_score(margin, peer_values(records, record, "net_margin_pct"), True),
            percentile_score(roe, peer_values(records, record, "roe_pct"), True),
            percentile_score(roa, peer_values(records, record, "roa_pct"), True),
            absolute_cashflow_score(record),
        ])
        growth = avg_score([
            percentile_score(rev_growth, peer_values(records, record, "revenue_growth_pct"), True),
            percentile_score(eps_growth, peer_values(records, record, "earnings_growth_pct"), True),
        ], 45)

        pe_score = None if pe is None or pe <= 0 else percentile_score(pe, [x for x in peer_values(records, record, "forward_pe") if x > 0], False)
        ev_score = None if ev_ebitda is None or ev_ebitda <= 0 else percentile_score(ev_ebitda, [x for x in peer_values(records, record, "ev_to_ebitda") if x > 0], False)
        valuation = avg_score([pe_score, ev_score], 45)

        if record.get("sector") == "Financial Services":
            balance = avg_score([
                percentile_score(current, peer_values(records, record, "current_ratio"), True),
                absolute_cashflow_score(record),
            ], 50)
        else:
            balance = avg_score([
                percentile_score(de, peer_values(records, record, "debt_to_equity"), False),
                percentile_score(current, peer_values(records, record, "current_ratio"), True),
                absolute_cashflow_score(record),
            ], 50)

        mom, overheated = momentum_score(record, anti_chase)
        beta_penalty = 50 if beta is None else max(0, 100 - abs(beta - 1) * 55)
        risk = avg_score([
            percentile_score(vol, peer_values(records, record, "volatility_pct"), False),
            beta_penalty,
        ], 50)

        components = {
            "quality": round(quality, 1),
            "growth": round(growth, 1),
            "valuation": round(valuation, 1),
            "balance_sheet": round(balance, 1),
            "momentum": round(mom, 1),
            "risk": round(risk, 1),
        }
        total = sum(components[k] * float(weights.get(k, 0)) for k in components) / weight_sum

        required = [
            "forward_pe", "revenue_growth_pct", "earnings_growth_pct", "net_margin_pct",
            "roe_pct", "debt_to_equity", "free_cash_flow", "momentum_6m_pct",
            "volatility_pct",
        ]
        present = sum(1 for key in required if finite(record.get(key)) is not None)
        completeness = round(present / len(required) * 100, 0)

        if completeness < 55:
            total -= 8
        if overheated:
            total -= 6
        if finite(record.get("market_cap")) is not None and finite(record.get("market_cap")) < float(config.get("minimum_market_cap", 2_000_000_000)):
            total -= 12

        total = max(0, min(100, total))
        relay = total >= 63 and quality >= 58 and growth >= 50 and not overheated and completeness >= 55

        why: List[str] = []
        risks: List[str] = []
        if quality >= 70:
            why.append("qualité opérationnelle élevée")
        if growth >= 70:
            why.append("croissance supérieure aux pairs")
        if valuation >= 65:
            why.append("valorisation relativement attractive")
        if balance >= 70:
            why.append("bilan / cash-flow solide")
        if mom >= 65:
            why.append("tendance de marché constructive")
        if not why:
            why.append("profil équilibré sans facteur dominant")

        if overheated:
            risks.append("momentum récent trop tendu: ne pas courir après le cours")
        if valuation < 35:
            risks.append("valorisation exigeante")
        if balance < 35:
            risks.append("bilan / génération de cash à surveiller")
        if growth < 35:
            risks.append("croissance récente faible ou négative")
        if completeness < 70:
            risks.append("données fondamentales incomplètes")

        record["scores"] = components
        record["score"] = round(total, 1)
        record["data_completeness_pct"] = completeness
        record["overheated"] = overheated
        record["relay_candidate"] = relay
        record["why"] = " · ".join(why)
        record["risk_flags"] = risks

    return sorted(records, key=lambda x: (x.get("relay_candidate", False), x.get("score", 0)), reverse=True)


def eligible_growth_relay(record: Dict[str, Any], config: Dict[str, Any]) -> bool:
    ticker = str(record.get("ticker") or "")
    industry = str(record.get("industry") or "")
    market_cap = finite(record.get("market_cap"))
    rev_growth = finite(record.get("revenue_growth_pct"))
    eps_growth = finite(record.get("earnings_growth_pct"))
    margin = finite(record.get("net_margin_pct"))

    if market_cap is not None and market_cap < float(config.get("minimum_market_cap", 5_000_000_000)):
        return False
    if any(fragment in ticker for fragment in config.get("excluded_symbol_fragments", [])):
        return False
    if any(keyword.lower() in industry.lower() for keyword in config.get("excluded_industry_keywords", [])):
        return False

    growth_ok = (
        (rev_growth is not None and rev_growth >= float(config.get("minimum_revenue_growth_pct", 4)))
        or (eps_growth is not None and eps_growth >= float(config.get("minimum_earnings_growth_pct", 8)))
    )
    if not growth_ok:
        return False

    if record.get("sector") != "Financial Services" and margin is not None and margin <= 0:
        return False
    if float(record.get("data_completeness_pct") or 0) < 55:
        return False
    return True


def deduplicate_companies(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Dict[str, Dict[str, Any]] = {}
    for record in records:
        name = str(record.get("name") or record.get("ticker") or "").lower()
        normalized = "".join(ch for ch in name if ch.isalnum())
        for suffix in ("limited", "ltd", "plc", "inc", "corporation", "corp", "sa", "ag", "nv"):
            if normalized.endswith(suffix):
                normalized = normalized[:-len(suffix)]
        key = normalized or str(record.get("ticker"))
        current = seen.get(key)
        if current is None or (finite(record.get("market_cap")) or 0) > (finite(current.get("market_cap")) or 0):
            seen[key] = record
    return list(seen.values())


def run_dynamic_screener(config: Dict[str, Any]) -> Dict[str, Any]:
    candidates, discovery = discover_candidates(config)
    records = [enrich_candidate(candidate) for candidate in candidates]
    records = [r for r in records if not r.get("error") or len(r) > 8]
    ranked = score_records(records, config)
    ranked = deduplicate_companies(ranked)

    eligible = [r for r in ranked if eligible_growth_relay(r, config)]
    eligible.sort(key=lambda x: (x.get("relay_candidate", False), x.get("score", 0)), reverse=True)

    max_results = int(config.get("max_results", 12))
    results = eligible[:max_results]
    if len(results) < max_results:
        chosen = {r.get("ticker") for r in results}
        backfill = [
            r for r in ranked
            if r.get("ticker") not in chosen
            and not r.get("overheated")
            and float(r.get("score") or 0) >= 52
        ]
        results.extend(backfill[: max_results - len(results)])

    return {
        "generated_from_dynamic_universe": True,
        "method": "Yahoo Finance/yfinance dynamic discovery + sector-relative multi-factor scoring",
        "objective": "Relais de croissance rentable, de qualité, à valorisation raisonnable; éviter de courir après les envolées.",
        "discovery": discovery,
        "weights": config.get("weights", {}),
        "anti_chase": config.get("anti_chase", {}),
        "filters": {
            "minimum_market_cap": config.get("minimum_market_cap"),
            "minimum_revenue_growth_pct": config.get("minimum_revenue_growth_pct"),
            "minimum_earnings_growth_pct": config.get("minimum_earnings_growth_pct"),
        },
        "results": results,
        "candidates_scored": len(ranked),
        "growth_eligible": len(eligible),
    }


def run_watchlist_analysis(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    for item in items:
        ticker_symbol = str(item.get("ticker") or "").strip()
        if not ticker_symbol:
            continue
        record = dict(item)
        try:
            ticker = yf.Ticker(ticker_symbol)
            info = ticker.info or {}
            record.update({
                "name": info.get("shortName") or item.get("label") or ticker_symbol,
                "sector": info.get("sector"),
                "currency": info.get("currency"),
                "forward_pe": round_n(info.get("forwardPE"), 2),
                "revenue_growth_pct": pct(info.get("revenueGrowth")),
                "earnings_growth_pct": pct(info.get("earningsGrowth")),
                "net_margin_pct": pct(info.get("profitMargins")),
                "roe_pct": pct(info.get("returnOnEquity")),
            })
            record.update(history_metrics(ticker))
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
        output.append(record)
    return output
