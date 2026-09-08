import json
import math
import os
import urllib.error
import urllib.request
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yfinance as yf
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent

# Safety lock: Finance-Adviser is intentionally a zero-cost personal project.
# Only models explicitly verified as available on a provider free tier may be called.
FREE_AI_ALLOWLIST = {
    "gemini": {"gemini-3.7-flash", "gemini-3.6-flash"},
    "groq": {"openai/gpt-oss-120b"},
}


class AIReport(BaseModel):
    executive_summary: str = Field(description="Synthèse décisionnelle en 4 à 8 phrases.")
    market_regime: str = Field(description="Lecture du régime de marché et du contexte macro.")
    portfolio_diagnosis: str = Field(description="Diagnostic critique de l'allocation et des risques.")
    risk_watch: str = Field(description="Risques prioritaires à surveiller.")
    action_plan: List[str] = Field(description="Actions ou décisions à envisager, avec prudence et conditions.")
    opportunities: List[str] = Field(description="Opportunités issues uniquement du screener fourni.")
    what_to_monitor: List[str] = Field(description="Points de contrôle pour les prochaines séances.")


def load_json(filename: str) -> Dict[str, Any]:
    with (ROOT / filename).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def dump_json(filename: str, payload: Dict[str, Any]) -> None:
    with (ROOT / filename).open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def finite_number(value: Any) -> Optional[float]:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def round_or_none(value: Any, digits: int = 2) -> Optional[float]:
    number = finite_number(value)
    return round(number, digits) if number is not None else None


def pct_change(first: Any, last: Any) -> Optional[float]:
    first_n = finite_number(first)
    last_n = finite_number(last)
    if first_n in (None, 0) or last_n is None:
        return None
    return round((last_n / first_n - 1) * 100, 2)


def market_snapshot(ticker_symbol: str, label: Optional[str] = None) -> Dict[str, Any]:
    result: Dict[str, Any] = {"ticker": ticker_symbol, "label": label or ticker_symbol}
    try:
        history = yf.Ticker(ticker_symbol).history(period="1y", auto_adjust=True)
        if history.empty:
            result["error"] = "Aucun historique disponible"
            return result

        closes = history["Close"].dropna()
        if closes.empty:
            result["error"] = "Cours de clôture indisponible"
            return result

        result["last"] = round_or_none(closes.iloc[-1], 4)

        windows = {
            "variation_1j_pct": 2,
            "variation_5j_pct": 6,
            "variation_1m_pct": 22,
            "variation_3m_pct": 66,
            "variation_1y_pct": len(closes),
        }
        for key, size in windows.items():
            if len(closes) >= 2:
                subset = closes.tail(min(size, len(closes)))
                result[key] = pct_change(subset.iloc[0], subset.iloc[-1])
            else:
                result[key] = None

        returns = closes.pct_change().dropna().tail(66)
        if len(returns) >= 10:
            result["volatilite_annualisee_pct"] = round(float(returns.std()) * math.sqrt(252) * 100, 2)
        else:
            result["volatilite_annualisee_pct"] = None
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def fetch_market_block(items: List[Dict[str, str]]) -> Dict[str, Dict[str, Any]]:
    return {
        item["ticker"]: market_snapshot(item["ticker"], item.get("label"))
        for item in items
    }


def fetch_portfolio_market_data(portfolio: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    output: Dict[str, Dict[str, Any]] = {}
    for asset in portfolio.get("actifs_actuels", []):
        ticker = asset.get("ticker")
        asset_class = str(asset.get("asset_class", "")).lower()
        sleeve = str(asset.get("sleeve", "")).lower()
        if asset_class == "cash" or sleeve == "cash" or str(ticker or "").upper().startswith("CASH"):
            continue
        if ticker:
            output[ticker] = market_snapshot(ticker, asset.get("nom"))
    return output


def fetch_financial_news(limit: int = 8) -> List[Dict[str, Any]]:
    stories: List[Dict[str, Any]] = []
    try:
        raw_news = yf.Ticker("SPY").news or []
        for item in raw_news:
            content = item.get("content") or {}
            provider = content.get("provider") or {}
            canonical = content.get("canonicalUrl") or {}
            title = item.get("title") or content.get("title")
            if not title:
                continue
            stories.append({
                "title": title,
                "publisher": item.get("publisher") or provider.get("displayName"),
                "url": item.get("link") or canonical.get("url"),
                "published_at": content.get("pubDate") or item.get("providerPublishTime"),
            })
            if len(stories) >= limit:
                break
    except Exception as exc:
        stories.append({"title": "Actualités indisponibles", "error": f"{type(exc).__name__}: {exc}"})
    return stories


def quality_score(forward_pe: Any, earnings_growth: Any, margin: Any, roe: Any) -> Optional[int]:
    pe = finite_number(forward_pe)
    growth = finite_number(earnings_growth)
    margin_n = finite_number(margin)
    roe_n = finite_number(roe)
    available = [v for v in (pe, growth, margin_n, roe_n) if v is not None]
    if len(available) < 2:
        return None

    score = 50
    if margin_n is not None:
        score += 12 if margin_n >= 0.20 else 6 if margin_n >= 0.10 else -5 if margin_n < 0 else 0
    if roe_n is not None:
        score += 15 if roe_n >= 0.25 else 8 if roe_n >= 0.15 else -5 if roe_n < 0.08 else 0
    if growth is not None:
        score += 15 if growth >= 0.20 else 8 if growth >= 0.10 else -8 if growth < 0 else 0
    if pe is not None:
        score += 6 if pe <= 25 else 0 if pe <= 35 else -8 if pe <= 50 else -15
    return max(0, min(100, int(round(score))))


def run_fundamental_screener(items: List[Dict[str, str]]) -> Dict[str, Dict[str, Any]]:
    output: Dict[str, Dict[str, Any]] = {}
    for item in items:
        ticker_symbol = item["ticker"]
        record: Dict[str, Any] = {
            "ticker": ticker_symbol,
            "label": item.get("label", ticker_symbol),
            "theme": item.get("theme"),
        }
        try:
            info = yf.Ticker(ticker_symbol).info or {}
            pe = info.get("forwardPE")
            growth = info.get("earningsGrowth")
            margin = info.get("profitMargins")
            roe = info.get("returnOnEquity")
            record.update({
                "name": info.get("shortName") or item.get("label") or ticker_symbol,
                "sector": info.get("sector"),
                "forward_pe": round_or_none(pe, 2),
                "earnings_growth_pct": round_or_none(finite_number(growth) * 100 if finite_number(growth) is not None else None, 2),
                "net_margin_pct": round_or_none(finite_number(margin) * 100 if finite_number(margin) is not None else None, 2),
                "roe_pct": round_or_none(finite_number(roe) * 100 if finite_number(roe) is not None else None, 2),
                "market_cap": info.get("marketCap"),
                "currency": info.get("currency"),
                "quality_valuation_score": quality_score(pe, growth, margin, roe),
                "score_note": "Heuristique interne; ne compare pas parfaitement les secteurs entre eux.",
            })
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
        output[ticker_symbol] = record
    return output


def portfolio_diagnostics(portfolio: Dict[str, Any], thresholds: Dict[str, Any]) -> Dict[str, Any]:
    assets = portfolio.get("actifs_actuels", [])
    weights = [finite_number(a.get("poids_pourcentage")) or 0 for a in assets]
    total_weight = sum(weights)
    total_value = sum(finite_number(a.get("valeur_marche_chf")) or 0 for a in assets)
    largest = max(assets, key=lambda a: finite_number(a.get("poids_pourcentage")) or 0, default=None)

    normalized = [(w / total_weight) for w in weights if w > 0] if total_weight else []
    hhi = sum(w * w for w in normalized)
    effective_positions = (1 / hhi) if hhi > 0 else None

    sleeve_weights: Dict[str, float] = {}
    for asset in assets:
        sleeve = asset.get("sleeve", "non_classifie")
        sleeve_weights[sleeve] = sleeve_weights.get(sleeve, 0) + (finite_number(asset.get("poids_pourcentage")) or 0)

    largest_weight = finite_number(largest.get("poids_pourcentage")) if largest else None
    concentration = "modérée"
    if largest_weight is not None:
        if largest_weight >= thresholds.get("single_position_high_pct", 60):
            concentration = "élevée"
        elif largest_weight >= thresholds.get("single_position_watch_pct", 40):
            concentration = "à surveiller"

    flags: List[str] = []
    if largest and largest_weight is not None and concentration != "modérée":
        flags.append(f"Poids dominant: {largest.get('nom', largest.get('ticker'))} à {largest_weight:.2f} %.")
    ai_weight = sleeve_weights.get("satellite_ai", 0)
    if ai_weight >= thresholds.get("thematic_watch_pct", 20):
        flags.append(f"Exposition thématique IA élevée selon l'heuristique: {ai_weight:.2f} %.")
    if effective_positions is not None and effective_positions < thresholds.get("effective_positions_low", 2.0):
        flags.append(f"Diversification effective faible: {effective_positions:.2f} positions équivalentes.")

    account_snapshot = portfolio.get("account_snapshot", {})
    return {
        "total_market_value_chf": round(total_value, 2),
        "reported_net_liquidation_chf": round_or_none(account_snapshot.get("net_liquidation_chf"), 2),
        "reported_securities_market_value_chf": round_or_none(account_snapshot.get("securities_market_value_chf"), 2),
        "reported_cash_chf": round_or_none(account_snapshot.get("cash_chf"), 2),
        "weights_sum_pct": round(total_weight, 2),
        "largest_position": {
            "ticker": largest.get("ticker") if largest else None,
            "name": largest.get("nom") if largest else None,
            "weight_pct": round_or_none(largest_weight, 2),
        },
        "concentration_assessment": concentration,
        "herfindahl_index": round(hhi, 4),
        "effective_positions": round(effective_positions, 2) if effective_positions is not None else None,
        "sleeve_weights_pct": {k: round(v, 2) for k, v in sleeve_weights.items()},
        "diagnostic_flags": flags,
        "note": "Les seuils de concentration sont des heuristiques de diagnostic, pas des limites personnelles d'investissement.",
    }


def build_analysis_prompt(
    portfolio: Dict[str, Any],
    policy: Dict[str, Any],
    diagnostics: Dict[str, Any],
    portfolio_market: Dict[str, Any],
    macro: Dict[str, Any],
    screener: Dict[str, Any],
    news: List[Dict[str, Any]],
) -> str:
    payload = {
        "investment_policy": policy,
        "portfolio": portfolio,
        "portfolio_diagnostics": diagnostics,
        "portfolio_market_data": portfolio_market,
        "macro_market_data": macro,
        "fundamental_screener": screener,
        "financial_news": news,
    }
    return f"""
Tu es le moteur d'analyse d'un tableau de bord financier personnel en CHF.

MISSION
Produis une analyse institutionnelle, sceptique, utile à la décision et strictement ancrée dans les données fournies ci-dessous.

RÈGLES
- Sépare les faits observés, les calculs, les hypothèses et ton jugement.
- N'invente aucune donnée fondamentale, valorisation, prévision ou actualité manquante.
- Signale explicitement les données absentes ou possiblement retardées.
- Respecte la politique d'investissement fournie. Les champs null signifient qu'aucune limite personnelle n'a été définie.
- Les seuils de diagnostic sont des heuristiques techniques, pas des contraintes de l'investisseur.
- Pour les opportunités, utilise uniquement le screener fourni et cite les ratios disponibles.
- Ne recommande jamais une transaction automatique. Formule des décisions à envisager avec conditions, déclencheurs et risques.
- Analyse la concentration, les chevauchements probables, le risque thématique, le risque de change, la valorisation et le contexte macro.
- Écris en français clair, précis et dense. Évite les slogans et les certitudes injustifiées.

DONNÉES
{json.dumps(payload, ensure_ascii=False, indent=2)}
"""


def assert_free_provider(provider: str, model: str, free_only: bool) -> None:
    if not free_only:
        raise RuntimeError(
            "Finance-Adviser refuse de désactiver le mode free_only. "
            "Aucun appel IA payant n'est autorisé."
        )
    allowed = FREE_AI_ALLOWLIST.get(provider, set())
    if model not in allowed:
        raise RuntimeError(
            f"Modèle refusé par la sécurité zéro coût: {provider}/{model}. "
            "Ajoutez-le à la liste blanche uniquement après vérification explicite de son Free Tier."
        )


def build_gemini_report(api_key: str, model: str, prompt: str) -> AIReport:
    client = genai.Client(api_key=api_key)
    last_error: Optional[Exception] = None

    for attempt, delay in enumerate((0, 3, 8), start=1):
        if delay:
            time.sleep(delay)
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=AIReport,
                ),
            )
            if not response.text:
                raise RuntimeError("Réponse Gemini vide")
            return AIReport.model_validate_json(response.text)
        except Exception as exc:
            last_error = exc
            message = str(exc).upper()
            transient = any(code in message for code in ("503", "UNAVAILABLE", "HIGH DEMAND", "429", "RESOURCE_EXHAUSTED"))
            if not transient or attempt >= 3:
                raise

    raise RuntimeError(f"Gemini indisponible après retries: {last_error}")


def build_groq_report(api_key: str, model: str, prompt: str, temperature: float) -> AIReport:
    endpoint = "https://api.groq.com/openai/v1/chat/completions"
    schema = AIReport.model_json_schema()
    request_body = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Réponds uniquement avec un objet JSON valide conforme au schéma fourni. "
                    "Aucun texte hors JSON."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "finance_adviser_report",
                "schema": schema,
                "strict": True,
            },
        },
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(request_body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Groq HTTP {exc.code}: {detail[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Groq réseau: {exc.reason}") from exc

    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Réponse Groq inattendue: {str(payload)[:500]}") from exc
    if not content:
        raise RuntimeError("Réponse Groq vide")
    return AIReport.model_validate_json(content)


def run_ai_provider_chain(
    config: Dict[str, Any],
    prompt: str,
) -> tuple[Optional[AIReport], Optional[str], Optional[str], List[Dict[str, Any]]]:
    ai_config = config.get("ai", {})
    free_only = bool(ai_config.get("free_only", True))
    temperature = float(ai_config.get("temperature", 0.2))
    providers = ai_config.get("providers") or []
    attempts: List[Dict[str, Any]] = []

    if not free_only:
        raise RuntimeError(
            "Configuration refusée: free_only doit rester activé pour ce projet."
        )

    for item in providers:
        if not item.get("enabled", True):
            continue

        provider = str(item.get("name", "")).strip().lower()
        model = str(item.get("model", "")).strip()
        assert_free_provider(provider, model, free_only)

        key_name = {
            "gemini": "GEMINI_API_KEY",
            "groq": "GROQ_API_KEY",
        }.get(provider)
        api_key = os.getenv(key_name or "") if key_name else None

        if not api_key:
            attempts.append({
                "provider": provider,
                "model": model,
                "status": "skipped",
                "reason": f"{key_name or 'API key'} absent",
            })
            continue

        try:
            if provider == "gemini":
                report = build_gemini_report(api_key, model, prompt)
            elif provider == "groq":
                report = build_groq_report(api_key, model, prompt, temperature)
            else:
                raise RuntimeError(f"Provider non supporté: {provider}")

            attempts.append({
                "provider": provider,
                "model": model,
                "status": "success",
            })
            return report, provider, model, attempts
        except Exception as exc:
            attempts.append({
                "provider": provider,
                "model": model,
                "status": "failed",
                "reason": f"{type(exc).__name__}: {exc}",
            })

    return None, None, None, attempts


def main() -> None:
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    config = load_json("config.json")
    portfolio = load_json("portfolio.json")
    policy = load_json("investment_policy.json")

    warnings: List[str] = []
    errors: List[str] = []

    macro = fetch_market_block(config["market"]["macro_tickers"])
    portfolio_market = fetch_portfolio_market_data(portfolio)
    news = fetch_financial_news()
    screener = run_fundamental_screener(config["screener"])
    diagnostics = portfolio_diagnostics(portfolio, config.get("diagnostic_thresholds", {}))

    report: Optional[AIReport] = None
    ai_provider: Optional[str] = None
    ai_model: Optional[str] = None
    ai_attempts: List[Dict[str, Any]] = []

    try:
        prompt = build_analysis_prompt(
            portfolio=portfolio,
            policy=policy,
            diagnostics=diagnostics,
            portfolio_market=portfolio_market,
            macro=macro,
            screener=screener,
            news=news,
        )
        report, ai_provider, ai_model, ai_attempts = run_ai_provider_chain(config, prompt)
    except Exception as exc:
        errors.append(f"Sécurité / configuration IA: {type(exc).__name__}: {exc}")

    if report is None:
        failed = [a for a in ai_attempts if a.get("status") == "failed"]
        skipped = [a for a in ai_attempts if a.get("status") == "skipped"]
        if failed:
            errors.append(
                "Tous les moteurs IA gratuits disponibles ont échoué: "
                + " | ".join(
                    f"{a.get('provider')}/{a.get('model')}: {a.get('reason')}"
                    for a in failed
                )
            )
        elif skipped:
            errors.append(
                "Aucune clé API gratuite disponible pour la chaîne IA: "
                + ", ".join(str(a.get("reason")) for a in skipped)
            )
        else:
            errors.append("Aucun moteur IA gratuit activé.")
    elif len(ai_attempts) > 1 and any(a.get("status") == "failed" for a in ai_attempts[:-1]):
        warnings.append(
            f"Fallback IA utilisé: {ai_provider}/{ai_model} après échec du moteur principal."
        )

    if any("error" in value for value in macro.values()):
        warnings.append("Certaines données macro n'ont pas pu être récupérées.")
    if any("error" in value for value in portfolio_market.values()):
        warnings.append("Certaines données de marché du portefeuille sont indisponibles.")
    if any("error" in value for value in screener.values()):
        warnings.append("Certaines données fondamentales du screener sont indisponibles.")

    status = "ok" if report is not None and not errors else "degraded"

    output: Dict[str, Any] = {
        "schema_version": 3,
        "status": status,
        "date_generation": generated_at,
        "ai_provider": ai_provider,
        "ai_model": ai_model,
        "ai_attempts": ai_attempts,
        "ai_free_only": True,
        "portfolio": {
            "profile": portfolio.get("profil_investisseur", {}),
            "positions": portfolio.get("actifs_actuels", []),
            "diagnostics": diagnostics,
            "snapshot_date": portfolio.get("snapshot_date"),
            "market_values_are_manual_snapshot": portfolio.get("market_values_are_manual_snapshot", False),
            "account_snapshot": portfolio.get("account_snapshot", {}),
            "snapshot_source": portfolio.get("snapshot_source"),
        },
        "market": macro,
        "portfolio_market": portfolio_market,
        "news": news,
        "screener": screener,
        "report": report.model_dump() if report is not None else None,
        "warnings": warnings,
        "errors": errors,
        "methodology": {
            "market_data": "Yahoo Finance via yfinance",
            "ai": (f"{ai_provider} / {ai_model} (free-only)" if ai_provider and ai_model else "IA indisponible (free-only)"),
            "fundamental_score": "Heuristique interne qualité/valorisation; non comparable parfaitement entre secteurs.",
            "disclaimer": "Outil d'aide à la décision. Aucune transaction n'est exécutée automatiquement."
        },
    }
    dump_json("data.json", output)

    print(f"Finance-Adviser terminé — statut: {status}")
    if warnings:
        print("Avertissements:")
        for warning in warnings:
            print(f"- {warning}")
    if errors:
        print("Erreurs:")
        for error in errors:
            print(f"- {error}")


if __name__ == "__main__":
    main()
