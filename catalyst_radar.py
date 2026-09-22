import hashlib
import html
import json
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parent
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
USER_AGENT = "PortfolioIntelligence/2.0 https://github.com/PhilippePatekVD/Finance-Adviser"
STRONG_CATEGORIES = {"orders_backlog","capacity_capex","partnerships","guidance","customer_adoption","government_defense","product_launch"}
FORMS = {"8-K","10-Q","10-K","6-K","20-F","40-F"}

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})


def load_json(name: str) -> Dict[str, Any]:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_date(value: Any) -> Optional[datetime]:
    s = str(value or "").strip()
    if not s:
        return None
    for fmt in ("%Y%m%dT%H%M%SZ","%Y-%m-%d","%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        d = datetime.fromisoformat(s.replace("Z","+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def iso_date(value: Any) -> Optional[str]:
    d = parse_date(value)
    return d.isoformat().replace("+00:00","Z") if d else None


def age_days(value: Any) -> Optional[int]:
    d = parse_date(value)
    if not d:
        return None
    return max(0, int((datetime.now(timezone.utc) - d).total_seconds() // 86400))


def qphrase(value: str) -> str:
    value = value.replace('"', " ").strip()
    return f'"{value}"' if " " in value or len(value) <= 4 else value


def catalyst_categories(text: str, terms: Dict[str, List[str]]) -> List[str]:
    low = clean_text(text).lower()
    found = []
    for cat, needles in terms.items():
        if any(str(term).lower() in low for term in needles):
            found.append(cat)
    return found


def snippet_around(text: str, needles: List[str], width: int = 300) -> str:
    plain = clean_text(text)
    low = plain.lower()
    positions = [low.find(n.lower()) for n in needles if low.find(n.lower()) >= 0]
    if not positions:
        return plain[:width].strip()
    pos = min(positions)
    start = max(0, pos - width // 3)
    end = min(len(plain), start + width)
    return ("…" if start else "") + plain[start:end].strip() + ("…" if end < len(plain) else "")


def gdelt(query: str, days: int, maxrecords: int = 25) -> List[Dict[str, Any]]:
    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": max(1, min(250, maxrecords)),
        "timespan": f"{days}d",
        "sort": "datedesc",
    }
    r = session.get(GDELT, params=params, timeout=12)
    r.raise_for_status()
    data = r.json()
    return data.get("articles", []) if isinstance(data, dict) else []


def signal_id(*parts: Any) -> str:
    raw = "|".join(str(x or "") for x in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:14]


def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def news_signal(article: Dict[str, Any], company: Optional[Dict[str, Any]], subtheme: Optional[str], terms: Dict[str,List[str]]) -> Dict[str, Any]:
    title = clean_text(article.get("title"))
    url = str(article.get("url") or "")
    date = iso_date(article.get("seendate"))
    cats = catalyst_categories(title, terms)
    return {
        "id": signal_id("news", url, title),
        "source_type": "news",
        "source_name": domain_of(url) or article.get("domain") or "GDELT",
        "title": title,
        "url": url,
        "date": date,
        "age_days": age_days(date),
        "company_ticker": company.get("ticker") if company else None,
        "company_name": company.get("name") if company else None,
        "subthemes": list(company.get("subthemes", [])) if company else ([subtheme] if subtheme else []),
        "categories": cats,
        "snippet": "",
        "language": article.get("language"),
        "source_country": article.get("sourcecountry"),
        "primary_source": False,
    }


def sec_cik_map() -> Dict[str, str]:
    r = session.get(SEC_TICKERS, timeout=12)
    r.raise_for_status()
    data = r.json()
    out = {}
    for item in data.values():
        ticker = str(item.get("ticker") or "").upper()
        cik = item.get("cik_str")
        if ticker and cik is not None:
            out[ticker] = str(cik).zfill(10)
    return out


def recent_sec_signals(company: Dict[str, Any], cik_map: Dict[str,str], days: int, terms: Dict[str,List[str]]) -> List[Dict[str, Any]]:
    ticker = str(company.get("sec_ticker") or "").upper()
    cik = cik_map.get(ticker)
    if not cik:
        return []
    r = session.get(SEC_SUBMISSIONS.format(cik=cik), timeout=12)
    r.raise_for_status()
    recent = (r.json().get("filings") or {}).get("recent") or {}
    forms = recent.get("form", [])
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    results = []
    fetched = 0
    for i, form in enumerate(forms):
        if form not in FORMS:
            continue
        filed = recent.get("filingDate", [""] * len(forms))[i]
        filed_dt = parse_date(filed)
        if not filed_dt or filed_dt < cutoff:
            continue
        accession = recent.get("accessionNumber", [""] * len(forms))[i]
        document = recent.get("primaryDocument", [""] * len(forms))[i]
        if not accession or not document:
            continue
        cik_num = str(int(cik))
        url = SEC_ARCHIVES.format(cik=cik_num, accession=accession.replace("-",""), document=document)
        try:
            fr = session.get(url, timeout=12)
            fr.raise_for_status()
            text = clean_text(fr.text[:2_000_000])
            cats = catalyst_categories(text, terms)
            if "robotics_exposure" not in cats:
                continue
            robot_needles = terms.get("robotics_exposure", [])
            results.append({
                "id": signal_id("sec", accession, document),
                "source_type": "filing",
                "source_name": f"SEC {form}",
                "title": f"{company['name']} · {form} · {filed}",
                "url": url,
                "date": filed_dt.isoformat().replace("+00:00","Z"),
                "age_days": age_days(filed_dt.isoformat()),
                "company_ticker": company.get("ticker"),
                "company_name": company.get("name"),
                "subthemes": list(company.get("subthemes", [])),
                "categories": cats,
                "snippet": snippet_around(text, robot_needles),
                "primary_source": True,
                "form": form,
            })
            fetched += 1
            if fetched >= 3:
                break
        except Exception:
            continue
    return results


def alias_match(title: str, companies: List[Dict[str,Any]]) -> Optional[Dict[str,Any]]:
    low = title.lower()
    for c in companies:
        aliases = sorted(c.get("aliases", []), key=len, reverse=True)
        for alias in aliases:
            a = alias.lower()
            if len(a) <= 4:
                if re.search(r"\b" + re.escape(a) + r"\b", low):
                    return c
            elif a in low:
                return c
    return None


def company_news(company: Dict[str,Any], theme_query: str, days: int, terms: Dict[str,List[str]]) -> List[Dict[str,Any]]:
    aliases = company.get("aliases", [])[:3] or [company["name"]]
    alias_block = "(" + " OR ".join(qphrase(x) for x in aliases) + ")"
    query = f"{alias_block} {theme_query}"
    articles = gdelt(query, days, 18)
    return [news_signal(a, company, None, terms) for a in articles]


def priority(signal: Dict[str,Any], corroboration: int) -> Dict[str,Any]:
    cats = set(signal.get("categories") or [])
    age = signal.get("age_days")
    age = 999 if age is None else age
    strong = sorted(cats & STRONG_CATEGORIES)
    reasons = []
    level = "monitor"

    if signal.get("primary_source"):
        reasons.append("source primaire")
    if age <= 7:
        reasons.append("signal < 7 jours")
    elif age <= 30:
        reasons.append("signal < 30 jours")
    if strong:
        reasons.append("catalyseur: " + ", ".join(strong[:3]))
    if corroboration >= 2:
        reasons.append(f"{corroboration} sources indépendantes")

    if signal.get("primary_source") and strong and age <= 30:
        level = "high"
    elif corroboration >= 2 and strong and age <= 30:
        level = "high"
    elif strong and age <= 30:
        level = "medium"
    elif signal.get("primary_source") and age <= 30:
        level = "medium"

    return {"level": level, "reasons": reasons}


def enrich_priorities(signals: List[Dict[str,Any]]) -> None:
    by_company_cat: Dict[tuple, set] = defaultdict(set)
    for s in signals:
        if s.get("source_type") != "news" or (s.get("age_days") or 999) > 30:
            continue
        domain = s.get("source_name")
        for cat in set(s.get("categories") or []) & STRONG_CATEGORIES:
            by_company_cat[(s.get("company_ticker"), cat)].add(domain)
    for s in signals:
        counts = []
        for cat in set(s.get("categories") or []) & STRONG_CATEGORIES:
            counts.append(len(by_company_cat.get((s.get("company_ticker"), cat), set())))
        corr = max(counts) if counts else 0
        s["corroboration"] = corr
        s["priority"] = priority(s, corr)


def company_summaries(companies: List[Dict[str,Any]], signals: List[Dict[str,Any]]) -> List[Dict[str,Any]]:
    out = []
    order = {"high": 0, "medium": 1, "monitor": 2}
    for c in companies:
        ss = [s for s in signals if s.get("company_ticker") == c.get("ticker")]
        if not ss:
            out.append({**c, "priority":"monitor", "signal_count_30d":0, "latest_date":None, "categories":[], "reasons":["aucun signal récent collecté"]})
            continue
        recent = [s for s in ss if (s.get("age_days") or 999) <= 30]
        levels = [s.get("priority",{}).get("level","monitor") for s in ss]
        best = min(levels, key=lambda x: order.get(x,9))
        cats = sorted({cat for s in recent for cat in s.get("categories",[]) if cat != "robotics_exposure"})
        reasons = []
        if any(s.get("primary_source") for s in recent):
            reasons.append("filing primaire récent")
        domains = {s.get("source_name") for s in recent if s.get("source_type")=="news"}
        if len(domains) >= 3:
            reasons.append(f"{len(domains)} sources média distinctes")
        strong = sorted(set(cats) & STRONG_CATEGORIES)
        if strong:
            reasons.append("catalyseurs: " + ", ".join(strong[:4]))
        dates = [s.get("date") for s in ss if s.get("date")]
        out.append({
            **c,
            "priority": best,
            "signal_count_30d": len(recent),
            "latest_date": max(dates) if dates else None,
            "categories": cats,
            "reasons": reasons or ["activité à surveiller"],
        })
    return sorted(out, key=lambda x:(order.get(x["priority"],9), -(x.get("signal_count_30d") or 0), x["name"]))


def collect_theme_news(subthemes: List[Dict[str,Any]], companies: List[Dict[str,Any]], terms: Dict[str,List[str]]) -> tuple[List[Dict[str,Any]], List[Dict[str,Any]], List[str]]:
    known: List[Dict[str,Any]] = []
    discovery: List[Dict[str,Any]] = []
    errors: List[str] = []
    seen = set()

    for st in subthemes:
        try:
            articles = gdelt(st["query"], 30, 100)
            for a in articles:
                title = clean_text(a.get("title"))
                if not title:
                    continue
                company = alias_match(title, companies)
                s = news_signal(a, company, st["id"], terms)
                if s["id"] in seen:
                    continue
                seen.add(s["id"])
                if company:
                    if st["id"] not in s["subthemes"]:
                        s["subthemes"].append(st["id"])
                    known.append(s)
                else:
                    cats = set(s.get("categories") or [])
                    if cats & STRONG_CATEGORIES or "robotics_exposure" in cats:
                        discovery.append(s)
        except Exception as exc:
            errors.append(f"GDELT {st['id']}: {type(exc).__name__}: {exc}")
        time.sleep(0.12)

    discovery.sort(key=lambda s:s.get("date") or "", reverse=True)
    return known, discovery[:60], errors


def optional_gemini_summary(payload: Dict[str,Any]) -> Dict[str,Any]:
    enabled = os.environ.get("ENABLE_GEMINI_FREE","0") == "1"
    key = os.environ.get("GEMINI_API_KEY","").strip()
    model = os.environ.get("GEMINI_MODEL","gemini-3.8-flash").strip()
    if not enabled:
        return {"enabled":False,"model":model,"status":"disabled","note":"Couche IA désactivée par défaut pour éviter toute facturation API."}
    if model != "gemini-3.8-flash":
        return {"enabled":False,"model":model,"status":"blocked","note":"Seul gemini-3.8-flash est autorisé dans le mode free-only."}
    if not key:
        return {"enabled":False,"model":model,"status":"no_key","note":"Clé Gemini absente."}

    top = payload.get("signals", [])[:30]
    compact = [{
        "id":s["id"],"company":s.get("company_name"),"title":s.get("title"),"date":s.get("date"),
        "source":s.get("source_name"),"categories":s.get("categories"),"priority":s.get("priority",{}).get("level"),
        "snippet":s.get("snippet","")[:500]
    } for s in top]
    prompt = """Tu es un analyste de recherche factuel. Analyse uniquement les signaux JSON fournis.
N'invente aucune information, aucun chiffre, aucun client ni aucune relation fournisseur.
Distingue faits et hypothèses. Ne donne ni recommandation d'achat ni objectif de cours.
Retourne STRICTEMENT du JSON avec: summary (3-5 phrases), themes (liste max 5),
notable_signal_ids (max 8 IDs), unknowns (liste), contradictions (liste).
Les notable_signal_ids doivent exister dans les données.
Données:
""" + json.dumps(compact, ensure_ascii=False)

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "contents":[{"parts":[{"text":prompt}]}],
        "generationConfig":{"temperature":0.1,"responseMimeType":"application/json","maxOutputTokens":2500}
    }
    try:
        r = requests.post(url, params={"key":key}, json=body, timeout=90)
        r.raise_for_status()
        data = r.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
        valid_ids = {s["id"] for s in top}
        parsed["notable_signal_ids"] = [x for x in parsed.get("notable_signal_ids",[]) if x in valid_ids]
        return {"enabled":True,"model":model,"status":"ok","result":parsed}
    except Exception as exc:
        return {"enabled":True,"model":model,"status":"error","note":f"{type(exc).__name__}: {exc}"}


def main() -> None:
    cfg = load_json("research_config.json")
    theme = cfg["theme"]
    companies = cfg["companies"]
    terms = theme["catalyst_terms"]
    days = int(theme.get("lookback_days",90))
    errors = []
    signals: List[Dict[str,Any]] = []

    news_signals, discovery, news_errors = collect_theme_news(theme["subthemes"], companies, terms)
    signals.extend(news_signals)
    errors.extend(news_errors)

    try:
        cik_map = sec_cik_map()
        for company in companies:
            if not company.get("sec_ticker"):
                continue
            try:
                signals.extend(recent_sec_signals(company, cik_map, days, terms))
            except Exception as exc:
                errors.append(f"SEC {company['ticker']}: {type(exc).__name__}: {exc}")
            time.sleep(0.08)
    except Exception as exc:
        errors.append(f"SEC mapping: {type(exc).__name__}: {exc}")

    dedup = {}
    for s in signals:
        key = s["id"]
        if key not in dedup:
            dedup[key] = s
    signals = list(dedup.values())
    enrich_priorities(signals)

    order = {"high":0,"medium":1,"monitor":2}
    signals.sort(key=lambda s:(order.get(s.get("priority",{}).get("level","monitor"),9), s.get("age_days") if s.get("age_days") is not None else 999))

    payload = {
        "schema_version":1,
        "generated_at":datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),
        "theme":{"id":theme["id"],"label":theme["label"],"description":theme["description"]},
        "method":{
            "news":"GDELT DOC 2.0 · titres et métadonnées publiques",
            "primary":"SEC EDGAR · filings récents pour les émetteurs couverts",
            "priority":"Règles transparentes basées sur récence, source primaire, type de catalyseur et corroboration",
            "note":"Une priorité de recherche n'est ni une recommandation d'achat ni une prévision de performance."
        },
        "subthemes":theme["subthemes"],
        "companies":company_summaries(companies, signals),
        "signals":signals[:160],
        "discovery":discovery,
        "errors":errors,
    }
    payload["ai"] = optional_gemini_summary(payload)

    (ROOT/"radar.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"Catalyst Radar — {len(payload['signals'])} signaux · {len(payload['companies'])} sociétés · {len(discovery)} découvertes · {len(errors)} avertissement(s)")
    print("AI:", payload["ai"].get("status"), payload["ai"].get("model"))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        fallback = {
            "schema_version":1,
            "generated_at":datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),
            "theme":{"id":"robotics","label":"Robotique & Physical AI"},
            "companies":[],"signals":[],"discovery":[],
            "ai":{"enabled":False,"status":"error"},
            "errors":[f"{type(exc).__name__}: {exc}"],
        }
        (ROOT/"radar.json").write_text(json.dumps(fallback,ensure_ascii=False,indent=2),encoding="utf-8")
        print(f"Catalyst Radar degraded: {type(exc).__name__}: {exc}")
