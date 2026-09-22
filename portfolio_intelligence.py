import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parent
BENCHMARK = "FWRA.SW"
BENCHMARK_LABEL = "FTSE All-World · FWRA"
PERIODS = {"1d": 2, "1w": 6, "1m": 22, "3m": 66, "6m": 132, "1y": 252}


def load_json(name: str) -> Dict[str, Any]:
    with (ROOT / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def finite(value: Any) -> Optional[float]:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def round_n(value: Any, digits: int = 2) -> Optional[float]:
    x = finite(value)
    return round(x, digits) if x is not None else None


def pct(first: Any, last: Any) -> Optional[float]:
    a, b = finite(first), finite(last)
    if a in (None, 0) or b is None:
        return None
    return round((b / a - 1) * 100, 2)


def is_cash(asset: Dict[str, Any]) -> bool:
    ticker = str(asset.get("ticker") or "").upper()
    return (
        str(asset.get("asset_class", "")).lower() == "cash"
        or str(asset.get("sleeve", "")).lower() == "cash"
        or ticker.startswith("CASH")
    )


def history_for(symbol: str, period: str = "1y") -> pd.Series:
    hist = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=True)
    if hist.empty or "Close" not in hist:
        return pd.Series(dtype=float)
    s = hist["Close"].dropna().astype(float)
    if isinstance(s.index, pd.DatetimeIndex):
        s.index = s.index.tz_localize(None) if s.index.tz is not None else s.index
    return s


def fx_symbol(currency: str) -> Optional[str]:
    c = str(currency or "CHF").upper()
    if c == "CHF":
        return None
    return f"{c}CHF=X"


def fx_series(currency: str) -> pd.Series:
    symbol = fx_symbol(currency)
    if not symbol:
        return pd.Series(1.0, index=pd.date_range(end=pd.Timestamp.today().normalize(), periods=370, freq="D"))
    return history_for(symbol, "1y")


def fx_last(currency: str) -> Tuple[Optional[float], Optional[str]]:
    symbol = fx_symbol(currency)
    if not symbol:
        return 1.0, None
    try:
        info = yf.Ticker(symbol).fast_info
        value = finite(info.get("last_price"))
        if value is None:
            s = history_for(symbol, "5d")
            value = finite(s.iloc[-1]) if not s.empty else None
        return value, symbol
    except Exception as exc:
        return None, f"{symbol}: {type(exc).__name__}: {exc}"


def instrument_snapshot(symbol: str, label: str, configured_currency: Optional[str] = None) -> Dict[str, Any]:
    result: Dict[str, Any] = {"ticker": symbol, "label": label}
    try:
        ticker = yf.Ticker(symbol)
        fi = ticker.fast_info
        currency = str(fi.get("currency") or configured_currency or "").upper() or None
        hist = history_for(symbol, "1y")
        last = finite(fi.get("last_price"))
        previous = finite(fi.get("previous_close"))
        if last is None and not hist.empty:
            last = finite(hist.iloc[-1])
        if previous is None and len(hist) >= 2:
            previous = finite(hist.iloc[-2])

        result.update({
            "currency": currency,
            "exchange": fi.get("exchange"),
            "last": round_n(last, 4),
            "previous_close": round_n(previous, 4),
            "day_change_pct": pct(previous, last),
        })

        if not hist.empty:
            for key, n in PERIODS.items():
                if key == "1d":
                    continue
                subset = hist.tail(min(n, len(hist)))
                result[f"return_{key}_pct"] = pct(subset.iloc[0], subset.iloc[-1]) if len(subset) >= 2 else None

            high = finite(hist.max())
            result["distance_52w_high_pct"] = pct(high, last) if high not in (None, 0) and last is not None else None

            daily = hist.pct_change().dropna()
            recent = daily.tail(60)
            if len(recent) >= 20:
                result["volatility_60d_ann_pct"] = round(float(recent.std()) * math.sqrt(252) * 100, 2)

            running_max = hist.cummax()
            drawdown = hist / running_max - 1
            result["max_drawdown_1y_pct"] = round(float(drawdown.min()) * 100, 2)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def aligned_position_series(symbol: str, quantity: float, currency: str) -> Tuple[pd.Series, List[str]]:
    errors: List[str] = []
    prices = history_for(symbol, "1y")
    if prices.empty:
        return pd.Series(dtype=float), [f"{symbol}: historique indisponible"]

    if currency.upper() == "CHF":
        fx = pd.Series(1.0, index=prices.index)
    else:
        fx = fx_series(currency)
        if fx.empty:
            return pd.Series(dtype=float), [f"{symbol}: FX {currency}/CHF indisponible"]
        fx = fx.reindex(prices.index.union(fx.index)).sort_index().ffill().reindex(prices.index)

    values = prices * fx * quantity
    return values.dropna(), errors


def period_returns(series: pd.Series) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {}
    for key, n in PERIODS.items():
        if len(series) >= 2:
            sub = series.tail(min(n, len(series)))
            out[key] = pct(sub.iloc[0], sub.iloc[-1]) if len(sub) >= 2 else None
        else:
            out[key] = None
    return out


def downsample(series: pd.Series, max_points: int = 120) -> List[Dict[str, Any]]:
    if series.empty:
        return []
    step = max(1, math.ceil(len(series) / max_points))
    sampled = series.iloc[::step]
    if sampled.index[-1] != series.index[-1]:
        sampled = pd.concat([sampled, series.iloc[[-1]]])
    first = finite(sampled.iloc[0])
    if first in (None, 0):
        return []
    return [
        {"date": idx.strftime("%Y-%m-%d"), "value": round(float(value / first * 100), 2)}
        for idx, value in sampled.items()
    ]


def compute_portfolio(portfolio: Dict[str, Any]) -> Dict[str, Any]:
    errors: List[str] = []
    rows: List[Dict[str, Any]] = []
    position_series: List[pd.Series] = []
    total_cash = 0.0

    for asset in portfolio.get("actifs_actuels", []):
        if is_cash(asset):
            total_cash += finite(asset.get("valeur_marche_chf")) or 0.0
            continue

        symbol = str(asset.get("ticker") or "").strip()
        if not symbol:
            continue
        quantity = finite(asset.get("quantite")) or 0.0
        configured_currency = str(asset.get("currency") or "CHF").upper()
        snap = instrument_snapshot(symbol, asset.get("nom") or symbol, configured_currency)
        if snap.get("error"):
            errors.append(f"{symbol}: {snap['error']}")

        quote_currency = str(snap.get("currency") or configured_currency).upper()
        fx, fx_error = fx_last(quote_currency)
        if fx_error:
            errors.append(fx_error)
        last = finite(snap.get("last"))
        current_value = quantity * last * fx if all(x is not None for x in (last, fx)) else None
        previous = finite(snap.get("previous_close"))
        day_pnl = quantity * (last - previous) * fx if all(x is not None for x in (last, previous, fx)) else None

        series, series_errors = aligned_position_series(symbol, quantity, quote_currency)
        errors.extend(series_errors)
        if not series.empty:
            series.name = symbol
            position_series.append(series)

        rows.append({
            **snap,
            "quantity": quantity,
            "source_snapshot_value_chf": round_n(asset.get("valeur_marche_chf")),
            "configured_currency": configured_currency,
            "fx_to_chf": round_n(fx, 6),
            "estimated_value_chf": round_n(current_value),
            "estimated_day_pnl_chf": round_n(day_pnl),
            "sleeve": asset.get("sleeve"),
            "asset_class": asset.get("asset_class"),
        })

    estimated_securities = sum(finite(x.get("estimated_value_chf")) or 0.0 for x in rows)
    estimated_total = estimated_securities + total_cash
    day_pnl = sum(finite(x.get("estimated_day_pnl_chf")) or 0.0 for x in rows)

    for row in rows:
        value = finite(row.get("estimated_value_chf")) or 0.0
        row["current_weight_pct"] = round(value / estimated_total * 100, 2) if estimated_total else None

    weights = [finite(x.get("current_weight_pct")) or 0.0 for x in rows]
    cash_weight = total_cash / estimated_total * 100 if estimated_total else 0.0
    normalized = [w / 100 for w in weights if w > 0] + ([cash_weight / 100] if cash_weight > 0 else [])
    hhi = sum(w * w for w in normalized)
    effective = 1 / hhi if hhi > 0 else None
    largest = max(rows, key=lambda x: finite(x.get("current_weight_pct")) or 0.0, default=None)

    combined = pd.Series(dtype=float)
    correlations: List[Dict[str, Any]] = []
    if position_series:
        frame = pd.concat(position_series, axis=1, sort=False).sort_index().ffill().dropna(how="all")
        combined = frame.sum(axis=1)
        returns = frame.pct_change().dropna()
        if len(returns) >= 20 and len(returns.columns) > 1:
            corr = returns.corr()
            for i, a in enumerate(corr.columns):
                for b in corr.columns[i + 1:]:
                    correlations.append({"a": a, "b": b, "correlation": round(float(corr.loc[a, b]), 2)})

    portfolio_returns = period_returns(combined)
    portfolio_vol = None
    portfolio_drawdown = None
    if len(combined) >= 20:
        daily = combined.pct_change().dropna().tail(60)
        if len(daily) >= 20:
            portfolio_vol = round(float(daily.std()) * math.sqrt(252) * 100, 2)
        dd = combined / combined.cummax() - 1
        portfolio_drawdown = round(float(dd.min()) * 100, 2)

    benchmark = instrument_snapshot(BENCHMARK, BENCHMARK_LABEL, "CHF")
    bench_series = history_for(BENCHMARK, "1y")
    benchmark_returns = period_returns(bench_series)

    comparison = {}
    for key in PERIODS:
        pr = portfolio_returns.get(key)
        br = benchmark_returns.get(key)
        comparison[key] = {
            "portfolio_pct": pr,
            "benchmark_pct": br,
            "difference_pp": round(pr - br, 2) if pr is not None and br is not None else None,
        }

    return {
        "positions": rows,
        "cash_chf": round(total_cash, 2),
        "estimated_securities_value_chf": round(estimated_securities, 2),
        "estimated_total_value_chf": round(estimated_total, 2),
        "estimated_day_pnl_chf": round(day_pnl, 2),
        "estimated_day_pnl_pct": round(day_pnl / (estimated_total - day_pnl) * 100, 2) if estimated_total and (estimated_total - day_pnl) else None,
        "risk": {
            "largest_position": largest.get("ticker") if largest else None,
            "largest_position_weight_pct": largest.get("current_weight_pct") if largest else None,
            "cash_weight_pct": round(cash_weight, 2),
            "effective_positions": round(effective, 2) if effective is not None else None,
            "hhi": round(hhi, 4),
            "volatility_60d_ann_pct": portfolio_vol,
            "max_drawdown_1y_pct": portfolio_drawdown,
        },
        "performance_reconstructed": portfolio_returns,
        "benchmark": {
            "ticker": BENCHMARK,
            "label": BENCHMARK_LABEL,
            "snapshot": benchmark,
            "returns": benchmark_returns,
        },
        "comparison": comparison,
        "series": {
            "portfolio": downsample(combined),
            "benchmark": downsample(bench_series),
        },
        "correlations": correlations,
        "errors": errors,
    }


def watchlist_snapshot(watchlist: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    rows: List[Dict[str, Any]] = []
    errors: List[str] = []
    for item in watchlist.get("items", []):
        symbol = str(item.get("ticker") or "").strip()
        if not symbol:
            continue
        row = instrument_snapshot(symbol, item.get("label") or symbol)
        row["theme"] = item.get("theme")
        row["note"] = item.get("note")
        tags: List[str] = []
        distance = finite(row.get("distance_52w_high_pct"))
        r3 = finite(row.get("return_3m_pct"))
        r1y = finite(row.get("return_1y_pct"))
        if distance is not None:
            if distance >= -5:
                tags.append("près du plus haut 52 sem.")
            elif distance <= -25:
                tags.append("correction > 25 %")
            elif distance <= -15:
                tags.append("correction > 15 %")
        if r3 is not None and r3 >= 15:
            tags.append("hausse 3 mois > 15 %")
        elif r3 is not None and r3 <= -15:
            tags.append("baisse 3 mois > 15 %")
        if r1y is not None and r1y >= 30:
            tags.append("hausse 1 an > 30 %")
        row["tags"] = tags
        if row.get("error"):
            errors.append(f"{symbol}: {row['error']}")
        rows.append(row)
    return rows, errors


def deterministic_insights(port: Dict[str, Any], watch: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    positions = port.get("positions", [])

    if positions:
        largest = max(positions, key=lambda x: finite(x.get("current_weight_pct")) or 0.0)
        items.append({
            "title": "Concentration",
            "text": f"{largest.get('ticker')} représente {largest.get('current_weight_pct')} % de la valeur estimée actuelle."
        })

        mover = max(positions, key=lambda x: abs(finite(x.get("day_change_pct")) or 0.0))
        if finite(mover.get("day_change_pct")) is not None:
            items.append({
                "title": "Plus fort mouvement du jour",
                "text": f"{mover.get('ticker')} : {mover.get('day_change_pct'):+.2f} %."
            })

    comp = port.get("comparison", {}).get("3m", {})
    if finite(comp.get("difference_pp")) is not None:
        diff = finite(comp["difference_pp"])
        items.append({
            "title": "Écart au benchmark · 3 mois",
            "text": f"Le portefeuille reconstruit est à {diff:+.2f} point(s) de pourcentage de {BENCHMARK_LABEL}."
        })

    corrected = [x for x in watch if (finite(x.get("distance_52w_high_pct")) or 0) <= -20]
    if corrected:
        names = ", ".join(x.get("ticker") for x in corrected[:4])
        items.append({
            "title": "Watchlist en correction",
            "text": f"{names} se situent à au moins 20 % sous leur plus haut des 52 dernières semaines."
        })

    return items


def main() -> None:
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    portfolio_cfg = load_json("portfolio.json")
    watchlist_cfg = load_json("watchlist.json")

    portfolio = compute_portfolio(portfolio_cfg)
    watchlist, watch_errors = watchlist_snapshot(watchlist_cfg)
    errors = [*portfolio.get("errors", []), *watch_errors]

    payload = {
        "schema_version": 4,
        "status": "ok" if portfolio.get("positions") else "degraded",
        "generated_at": generated_at,
        "sources": {
            "positions": {
                "provider": portfolio_cfg.get("snapshot_source", "portfolio.json"),
                "snapshot_date": portfolio_cfg.get("snapshot_date"),
                "mode": "manual_snapshot" if portfolio_cfg.get("market_values_are_manual_snapshot") else "configured_positions",
            },
            "prices": {
                "provider": "Yahoo Finance via yfinance",
                "generated_at": generated_at,
                "note": "Cotations indicatives; elles peuvent être retardées ou temporairement indisponibles.",
            },
            "performance": {
                "method": "Reconstruction à quantités actuelles constantes",
                "note": "Ce n'est PAS la performance réelle du compte IBKR : les achats, ventes, apports et retraits historiques ne sont pas connus.",
            },
        },
        "portfolio": portfolio,
        "watchlist": watchlist,
        "insights": deterministic_insights(portfolio, watchlist),
        "errors": errors,
    }

    with (ROOT / "data.json").open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    print(f"Portfolio Intelligence — {payload['status']} — {len(portfolio.get('positions', []))} positions / {len(watchlist)} watchlist")
    if errors:
        print(f"{len(errors)} avertissement(s) de données — le déploiement reste valide.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        fallback = {
            "schema_version": 4,
            "status": "degraded",
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "sources": {},
            "portfolio": {"positions": []},
            "watchlist": [],
            "insights": [],
            "errors": [f"{type(exc).__name__}: {exc}"],
        }
        with (ROOT / "data.json").open("w", encoding="utf-8") as handle:
            json.dump(fallback, handle, ensure_ascii=False, indent=2)
        print(f"Mode dégradé sans échec du workflow: {type(exc).__name__}: {exc}")
