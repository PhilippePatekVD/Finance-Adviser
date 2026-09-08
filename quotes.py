import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yfinance as yf

ROOT = Path(__file__).resolve().parent


def load_json(filename: str) -> Dict[str, Any]:
    with (ROOT / filename).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def quote_snapshot(ticker_symbol: str, label: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {"ticker": ticker_symbol, "label": label}
    try:
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.fast_info
        last = finite(info.get("last_price"))
        previous = finite(info.get("previous_close"))

        if last is None:
            hist = ticker.history(period="2d", interval="5m", auto_adjust=True)
            if not hist.empty:
                closes = hist["Close"].dropna()
                if not closes.empty:
                    last = finite(closes.iloc[-1])

        if previous is None:
            daily = ticker.history(period="5d", interval="1d", auto_adjust=True)
            closes = daily["Close"].dropna() if not daily.empty else []
            if len(closes) >= 2:
                previous = finite(closes.iloc[-2])

        change = None
        if last is not None and previous not in (None, 0):
            change = (last / previous - 1) * 100

        result.update({
            "last": round(last, 4) if last is not None else None,
            "previous_close": round(previous, 4) if previous is not None else None,
            "day_change_pct": round(change, 2) if change is not None else None,
            "currency": info.get("currency"),
            "exchange": info.get("exchange"),
            "timezone": info.get("timezone"),
        })
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def main() -> None:
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    portfolio = load_json("portfolio.json")
    watchlist = load_json("watchlist.json")

    portfolio_quotes: List[Dict[str, Any]] = []
    watchlist_quotes: List[Dict[str, Any]] = []
    errors: List[str] = []

    estimated_securities_chf = 0.0
    estimated_day_pnl_chf = 0.0
    cash_chf = 0.0

    for asset in portfolio.get("actifs_actuels", []):
        ticker_symbol = str(asset.get("ticker") or "")
        is_cash = str(asset.get("asset_class", "")).lower() == "cash" or str(asset.get("sleeve", "")).lower() == "cash" or ticker_symbol.upper().startswith("CASH")
        if is_cash:
            cash_chf += finite(asset.get("valeur_marche_chf")) or 0
            continue
        if not ticker_symbol:
            continue

        quote = quote_snapshot(ticker_symbol, asset.get("nom") or ticker_symbol)
        quantity = finite(asset.get("quantite")) or 0
        quote["quantity"] = quantity
        quote["snapshot_value_chf"] = finite(asset.get("valeur_marche_chf"))
        quote["portfolio_currency"] = asset.get("currency", "CHF")

        last = finite(quote.get("last"))
        previous = finite(quote.get("previous_close"))
        if last is not None and str(asset.get("currency", "CHF")).upper() == "CHF":
            estimated_value = quantity * last
            quote["estimated_value_chf"] = round(estimated_value, 2)
            estimated_securities_chf += estimated_value
            if previous is not None:
                estimated_day_pnl_chf += quantity * (last - previous)
        else:
            quote["estimated_value_chf"] = None

        if quote.get("error"):
            errors.append(f"{ticker_symbol}: {quote['error']}")
        portfolio_quotes.append(quote)

    for item in watchlist.get("items", []):
        ticker_symbol = str(item.get("ticker") or "")
        if not ticker_symbol:
            continue
        quote = quote_snapshot(ticker_symbol, item.get("label") or ticker_symbol)
        quote["theme"] = item.get("theme")
        quote["note"] = item.get("note")
        if quote.get("error"):
            errors.append(f"{ticker_symbol}: {quote['error']}")
        watchlist_quotes.append(quote)

    status = "ok" if portfolio_quotes or watchlist_quotes else "degraded"
    payload = {
        "schema_version": 1,
        "status": status,
        "date_generation": generated_at,
        "refresh_type": "market_quotes_no_ai",
        "portfolio": {
            "quotes": portfolio_quotes,
            "estimated_securities_value_chf": round(estimated_securities_chf, 2),
            "cash_chf": round(cash_chf, 2),
            "estimated_total_value_chf": round(estimated_securities_chf + cash_chf, 2),
            "estimated_day_pnl_chf": round(estimated_day_pnl_chf, 2),
        },
        "watchlist": watchlist_quotes,
        "errors": errors,
        "note": "Cotations indicatives via Yahoo Finance/yfinance. Les données peuvent être retardées et ne remplacent pas Interactive Brokers.",
    }

    with (ROOT / "quotes.json").open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)

    print(f"quotes.json généré — {status} — {len(portfolio_quotes)} positions / {len(watchlist_quotes)} watchlist")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        fallback = {
            "schema_version": 1,
            "status": "degraded",
            "date_generation": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "refresh_type": "market_quotes_no_ai",
            "portfolio": {"quotes": []},
            "watchlist": [],
            "errors": [f"{type(exc).__name__}: {exc}"],
        }
        with (ROOT / "quotes.json").open("w", encoding="utf-8") as handle:
            json.dump(fallback, handle, indent=2, ensure_ascii=False)
        print(f"Mode dégradé quotes: {exc}")
