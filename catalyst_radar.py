import hashlib
import html
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / ".research_state"
CACHE_PATH = STATE_DIR / "source_cache.json"

GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
GOOGLE_NEWS = "https://news.google.com/rss/search"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"

USER_AGENT = "PortfolioIntelligence-ResearchOS/3.0 (https://github.com/PhilippePatekVD/Finance-Adviser)"
SEC_USER_AGENT = "Portfolio Intelligence Research OS https://github.com/PhilippePatekVD/Finance-Adviser"

STRONG_CATEGORIES = {
    "orders_backlog", "capacity_capex", "partnerships", "guidance",
    "customer_adoption", "government_defense", "product_launch"
}
FORMS = {"8-K", "10-Q", "10-K", "6-K", "20-F", "40-F"}

NEWS_BATCHES = [
    {
        "id": "robotics_core",
        "query": '("humanoid robot" OR "industrial robot" OR cobot OR "warehouse automation" OR "autonomous mobile robot" OR robotics)'
    },
    {
        "id": "robotics_enablers",
        "query": '("robot actuator" OR "harmonic drive" OR "precision reducer" OR "servo motor" OR "machine vision" OR "force torque sensor" OR "physical AI" OR "embodied AI")'
    },
]

ALT_BATCHES = [
    {
        "id": "talent_ip",
        "query": 'robotics (hiring OR recruiting OR "robotics engineer" OR patent OR patents OR patented)'
    },
    {
        "id": "commercial_industrial",
        "query": 'robotics ("government contract" OR grant OR subsidy OR tender OR procurement OR "new factory" OR "capacity expansion" OR "mass production")'
    },
]

session = requests.Session()
session.headers.update({
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})

sec_session = requests.Session()
sec_session.headers.update({
    "User-Agent": SEC_USER_AGENT,
    "Accept-Encoding": "gzip, deflate",
    "Accept": "application/json,text/html,application/xhtml+xml",
    "Host": "data.sec.gov",
})


def load_json(name: str) -> Dict[str, Any]:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_date(value: Any) -> Optional[datetime]:
    s = str(value or "").strip()
    if not s:
        return None
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y-%m-%d", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def age_hours(value: Any) -> Optional[float]:
    d = parse_date(value)
    if not d:
        return None
    return max(0.0, (datetime.now(timezone.utc) - d).total_seconds() / 3600)


def age_days(value: Any) -> Optional[int]:
    hours = age_hours(value)
    return int(hours // 24) if hours is not None else None


def clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def signal_id(*parts: Any) -> str:
    raw = "|".join(str(x or "") for x in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:14]


def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def iso_date(value: Any) -> Optional[str]:
    d = parse_date(value)
    return d.isoformat().replace("+00:00", "Z") if d else None


def load_cache() -> Dict[str, Any]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        cache = {}
    cache.setdefault("version", 1)
    cache.setdefault("gdelt", {})
    cache.setdefault("google", {})
    cache.setdefault("sec", {})
    cache.setdefault("meta", {})
    return cache


def save_cache(cache: Dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def cache_get(cache: Dict[str, Any], provider: str, key: str, max_age_hours: float) -> Optional[Any]:
    item = (cache.get(provider) or {}).get(key)
    if not item:
        return None
    hours = age_hours(item.get("fetched_at"))
    if hours is None or hours > max_age_hours:
        return None
    return item.get("payload")


def cache_put(cache: Dict[str, Any], provider: str, key: str, payload: Any) -> None:
    cache.setdefault(provider, {})[key] = {"fetched_at": now_iso(), "payload": payload}


def request_json(sess: requests.Session, url: str, *, params: Optional[Dict[str, Any]] = None,
                 timeout: int = 12, attempts: int = 3) -> Tuple[Dict[str, Any], int]:
    delays = [0.0, 1.5, 4.0]
    last_exc = None
    for i in range(attempts):
        if delays[i]:
            time.sleep(delays[i])
        try:
            r = sess.get(url, params=params, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                last_exc = requests.HTTPError(f"{r.status_code} {r.reason}", response=r)
                continue
            r.raise_for_status()
            data = r.json()
            return data, r.status_code
        except Exception as exc:
            last_exc = exc
    raise last_exc or RuntimeError("request failed")


def request_text(sess: requests.Session, url: str, *, timeout: int = 12,
                 attempts: int = 2, host_header: Optional[str] = None) -> str:
    delays = [0.0, 1.5]
    last_exc = None
    for i in range(attempts):
        if delays[i]:
            time.sleep(delays[i])
        try:
            headers = {"Host": host_header} if host_header else None
            r = sess.get(url, timeout=timeout, headers=headers)
            if r.status_code in (429, 500, 502, 503, 504):
                last_exc = requests.HTTPError(f"{r.status_code} {r.reason}", response=r)
                continue
            r.raise_for_status()
            return r.text
        except Exception as exc:
            last_exc = exc
    raise last_exc or RuntimeError("request failed")


def catalyst_categories(text: str, terms: Dict[str, List[str]]) -> List[str]:
    low = clean_text(text).lower()
    return [cat for cat, needles in terms.items() if any(str(term).lower() in low for term in needles)]


def snippet_around(text: str, needles: List[str], width: int = 360) -> str:
    plain = clean_text(text)
    low = plain.lower()
    pos = [low.find(n.lower()) for n in needles if low.find(n.lower()) >= 0]
    if not pos:
        return plain[:width]
    p = min(pos)
    start = max(0, p - width // 3)
    end = min(len(plain), start + width)
    return ("…" if start else "") + plain[start:end] + ("…" if end < len(plain) else "")


def google_news_rss(query: str, maxrecords: int = 80) -> List[Dict[str, Any]]:
    params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    r = session.get(GOOGLE_NEWS, params=params, timeout=8)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for item in root.findall(".//item")[:maxrecords]:
        title = clean_text(item.findtext("title"))
        link = clean_text(item.findtext("link"))
        pub = clean_text(item.findtext("pubDate"))
        source_el = item.find("source")
        source = clean_text(source_el.text if source_el is not None else "")
        try:
            dt = parsedate_to_datetime(pub)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            seen = dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        except Exception:
            seen = ""
        if title and link:
            out.append({
                "title": title,
                "url": link,
                "seendate": seen,
                "domain": source or "Google News",
                "language": "English",
                "sourcecountry": None,
            })
    return out


def gdelt(query: str, days: int = 30, maxrecords: int = 150) -> List[Dict[str, Any]]:
    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": max(1, min(250, maxrecords)),
        "timespan": f"{days}d",
        "sort": "datedesc",
    }
    data, _ = request_json(session, GDELT, params=params, timeout=8, attempts=2)
    return data.get("articles", []) if isinstance(data, dict) else []


def alias_match(title: str, companies: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    low = title.lower()
    for company in companies:
        aliases = sorted(company.get("aliases", []), key=len, reverse=True)
        for alias in aliases:
            a = alias.lower()
            if len(a) <= 4:
                if re.search(r"\b" + re.escape(a) + r"\b", low):
                    return company
            elif a in low:
                return company
    return None


def subthemes_for_text(text: str, subthemes: List[Dict[str, Any]]) -> List[str]:
    low = clean_text(text).lower()
    found = []
    for st in subthemes:
        if any(str(k).lower() in low for k in st.get("keywords", [])):
            found.append(st["id"])
    return found


def news_signal(article: Dict[str, Any], company: Optional[Dict[str, Any]],
                subthemes: List[str], terms: Dict[str, List[str]],
                source_type: str = "news") -> Dict[str, Any]:
    title = clean_text(article.get("title"))
    url = str(article.get("url") or "")
    date = iso_date(article.get("seendate"))
    cats = catalyst_categories(title, terms)
    return {
        "id": signal_id(source_type, url, title),
        "source_type": source_type,
        "source_name": article.get("domain") or domain_of(url) or "unknown",
        "title": title,
        "url": url,
        "date": date,
        "age_days": age_days(date),
        "company_ticker": company.get("ticker") if company else None,
        "company_name": company.get("name") if company else None,
        "subthemes": sorted(set((company.get("subthemes", []) if company else []) + subthemes)),
        "categories": cats,
        "snippet": "",
        "language": article.get("language"),
        "source_country": article.get("sourcecountry"),
        "primary_source": False,
    }


def collect_news(cfg: Dict[str, Any], cache: Dict[str, Any],
                 provider_status: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    companies = cfg["companies"]
    subthemes = cfg["theme"]["subthemes"]
    terms = cfg["theme"]["catalyst_terms"]
    settings = (cfg.get("providers") or {}).get("gdelt", {})
    stale_hours = float(settings.get("stale_cache_hours", 72))

    known, discovery, errors = [], [], []
    seen = set()

    gdelt_ok = 0
    gdelt_fail = 0
    google_fallbacks = 0
    cache_fallbacks = 0

    for batch in NEWS_BATCHES:
        articles = None
        source_used = None

        try:
            articles = gdelt(batch["query"], 30, 100)
            source_used = "GDELT"
            cache_put(cache, "gdelt", batch["id"], articles)
            gdelt_ok += 1
        except Exception as exc:
            gdelt_fail += 1
            cached = cache_get(cache, "gdelt", batch["id"], stale_hours)
            if cached is not None:
                articles = cached
                source_used = "GDELT cache"
                cache_fallbacks += 1
            else:
                try:
                    articles = google_news_rss(batch["query"], 120)
                    source_used = "Google News RSS"
                    cache_put(cache, "google", "fallback:" + batch["id"], articles)
                    google_fallbacks += 1
                except Exception as rss_exc:
                    cached_google = cache_get(cache, "google", "fallback:" + batch["id"], stale_hours)
                    if cached_google is not None:
                        articles = cached_google
                        source_used = "Google News cache"
                        cache_fallbacks += 1
                    else:
                        errors.append(f"News batch {batch['id']}: GDELT {type(exc).__name__}; Google {type(rss_exc).__name__}")
                        articles = []
                        source_used = "none"

        for article in articles or []:
            title = clean_text(article.get("title"))
            if not title:
                continue
            st_ids = subthemes_for_text(title, subthemes)
            if not st_ids:
                continue
            company = alias_match(title, companies)
            sig = news_signal(article, company, st_ids, terms)
            sig["collection_provider"] = source_used
            if sig["id"] in seen:
                continue
            seen.add(sig["id"])
            if company:
                known.append(sig)
            else:
                cats = set(sig.get("categories") or [])
                if cats & STRONG_CATEGORIES or "robotics_exposure" in cats:
                    discovery.append(sig)

        time.sleep(0.25)

    provider_status["gdelt"] = {
        "status": "active" if gdelt_fail == 0 else ("degraded" if gdelt_ok else "fallback"),
        "requests": len(NEWS_BATCHES),
        "successful_batches": gdelt_ok,
        "failed_batches": gdelt_fail,
        "cache_fallbacks": cache_fallbacks,
        "google_fallbacks": google_fallbacks,
    }
    discovery.sort(key=lambda s: s.get("date") or "", reverse=True)
    return known, discovery[:80], errors


def collect_alternative(cfg: Dict[str, Any], cache: Dict[str, Any],
                        provider_status: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    companies = cfg["companies"]
    subthemes = cfg["theme"]["subthemes"]
    terms = cfg["theme"]["catalyst_terms"]
    stale_hours = float(((cfg.get("providers") or {}).get("google_news") or {}).get("stale_cache_hours", 72))

    known, discovery, errors = [], [], []
    success = 0
    cache_hits = 0

    for batch in ALT_BATCHES:
        try:
            articles = google_news_rss(batch["query"], 90)
            cache_put(cache, "google", "alt:" + batch["id"], articles)
            success += 1
        except Exception as exc:
            cached = cache_get(cache, "google", "alt:" + batch["id"], stale_hours)
            if cached is not None:
                articles = cached
                cache_hits += 1
            else:
                errors.append(f"Alternative {batch['id']}: {type(exc).__name__}: {exc}")
                articles = []

        for article in articles:
            title = clean_text(article.get("title"))
            st_ids = subthemes_for_text(title, subthemes)
            company = alias_match(title, companies)
            sig = news_signal(article, company, st_ids, terms, source_type="alternative_proxy")
            sig["alternative_type"] = batch["id"]
            sig["proxy_note"] = "Signal indirect ; vérifier la source primaire avant toute conclusion."

            low = title.lower()
            extra = []
            if any(x in low for x in ("hiring", "recruit", "robotics engineer", "jobs")):
                extra.append("hiring")
            if "patent" in low:
                extra.append("patent")
            if any(x in low for x in ("government contract", "grant", "subsidy", "tender", "procurement")):
                extra.append("government_defense")
            if any(x in low for x in ("new factory", "new plant", "capacity expansion", "mass production")):
                extra.append("capacity_capex")
            sig["categories"] = list(dict.fromkeys((sig.get("categories") or []) + extra))

            if company:
                known.append(sig)
            else:
                discovery.append(sig)

        time.sleep(0.2)

    provider_status["google_news"] = {
        "status": "active" if success == len(ALT_BATCHES) else ("degraded" if success else "cache"),
        "requests": len(ALT_BATCHES),
        "successful_batches": success,
        "cache_fallbacks": cache_hits,
    }
    return known, discovery[:70], errors


def sec_document_signals(company: Dict[str, Any], days: int,
                         terms: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    cik = str(company.get("sec_cik") or "").zfill(10)
    if not cik.strip("0"):
        return []

    data, _ = request_json(sec_session, SEC_SUBMISSIONS.format(cik=cik), timeout=7, attempts=1)
    recent = (data.get("filings") or {}).get("recent") or {}
    forms = recent.get("form", [])
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    candidates = []

    for i, form in enumerate(forms):
        if form not in FORMS:
            continue
        filed = (recent.get("filingDate") or [""] * len(forms))[i]
        filed_dt = parse_date(filed)
        if not filed_dt or filed_dt < cutoff:
            continue
        accession = (recent.get("accessionNumber") or [""] * len(forms))[i]
        document = (recent.get("primaryDocument") or [""] * len(forms))[i]
        if accession and document:
            candidates.append((filed_dt, form, accession, document))

    candidates.sort(reverse=True)
    results = []
    for filed_dt, form, accession, document in candidates[:2]:
        cik_num = str(int(cik))
        url = SEC_ARCHIVES.format(cik=cik_num, accession=accession.replace("-", ""), document=document)
        text = request_text(sec_archive_session, url, timeout=7, attempts=1)
        cats = catalyst_categories(text[:2_500_000], terms)
        if "robotics_exposure" not in cats:
            time.sleep(0.15)
            continue
        results.append({
            "id": signal_id("sec", accession, document),
            "source_type": "filing",
            "source_name": f"SEC {form}",
            "title": f"{company['name']} · {form} · {filed_dt.date().isoformat()}",
            "url": url,
            "date": filed_dt.isoformat().replace("+00:00", "Z"),
            "age_days": age_days(filed_dt.isoformat()),
            "company_ticker": company.get("ticker"),
            "company_name": company.get("name"),
            "subthemes": list(company.get("subthemes", [])),
            "categories": cats,
            "snippet": snippet_around(text, terms.get("robotics_exposure", [])),
            "primary_source": True,
            "form": form,
            "collection_provider": "SEC EDGAR direct CIK",
        })
        time.sleep(0.18)
    return results


def collect_sec(cfg: Dict[str, Any], cache: Dict[str, Any],
                provider_status: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    settings = (cfg.get("providers") or {}).get("sec", {})
    refresh_hours = float(settings.get("refresh_hours", 20))
    stale_hours = float(settings.get("stale_cache_hours", 168))
    breaker_limit = int(settings.get("circuit_breaker_failures", 2))
    max_network = int(settings.get("max_network_companies_per_run", 6))
    days = int(cfg["theme"].get("lookback_days", 90))
    terms = cfg["theme"]["catalyst_terms"]

    companies = [c for c in cfg["companies"] if c.get("sec_cik")]
    results, errors = [], []
    network_attempts = 0
    network_success = 0
    fresh_cache_hits = 0
    stale_cache_hits = 0
    consecutive_failures = 0
    breaker_open = False

    for company in companies:
        key = str(company["ticker"])
        fresh = cache_get(cache, "sec", key, refresh_hours)
        if fresh is not None:
            results.extend(fresh)
            fresh_cache_hits += 1
            continue

        if breaker_open or network_attempts >= max_network:
            stale = cache_get(cache, "sec", key, stale_hours)
            if stale is not None:
                results.extend(stale)
                stale_cache_hits += 1
            continue

        network_attempts += 1
        try:
            signals = sec_document_signals(company, days, terms)
            cache_put(cache, "sec", key, signals)
            results.extend(signals)
            network_success += 1
            consecutive_failures = 0
        except Exception as exc:
            consecutive_failures += 1
            stale = cache_get(cache, "sec", key, stale_hours)
            if stale is not None:
                results.extend(stale)
                stale_cache_hits += 1
            if consecutive_failures >= breaker_limit:
                breaker_open = True
                errors.append(
                    f"SEC circuit breaker ouvert après {consecutive_failures} échecs consécutifs "
                    f"({type(exc).__name__}: {exc})"
                )
            else:
                errors.append(f"SEC {company['ticker']}: {type(exc).__name__}: {exc}")
        time.sleep(0.22)

    provider_status["sec"] = {
        "status": (
            "active" if network_attempts and network_success == network_attempts and not breaker_open
            else "degraded" if network_success or fresh_cache_hits or stale_cache_hits
            else "unavailable"
        ),
        "configured_companies": len(companies),
        "network_attempts": network_attempts,
        "network_successes": network_success,
        "fresh_cache_hits": fresh_cache_hits,
        "stale_cache_hits": stale_cache_hits,
        "circuit_breaker_open": breaker_open,
        "mapping_request_removed": True,
        "max_network_companies_per_run": max_network,
    }
    return results, errors


def priority(signal: Dict[str, Any], corroboration: int) -> Dict[str, Any]:
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


def enrich_priorities(signals: List[Dict[str, Any]]) -> None:
    by_company_cat: Dict[tuple, set] = defaultdict(set)
    for signal in signals:
        if (signal.get("age_days") or 999) > 30:
            continue
        if signal.get("source_type") not in ("news", "alternative_proxy"):
            continue
        source = signal.get("source_name")
        for cat in set(signal.get("categories") or []) & STRONG_CATEGORIES:
            by_company_cat[(signal.get("company_ticker"), cat)].add(source)

    for signal in signals:
        counts = [
            len(by_company_cat.get((signal.get("company_ticker"), cat), set()))
            for cat in set(signal.get("categories") or []) & STRONG_CATEGORIES
        ]
        corr = max(counts) if counts else 0
        signal["corroboration"] = corr
        signal["priority"] = priority(signal, corr)


def company_summaries(companies: List[Dict[str, Any]], signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    order = {"high": 0, "medium": 1, "monitor": 2}
    out = []
    for company in companies:
        ss = [s for s in signals if s.get("company_ticker") == company.get("ticker")]
        recent = [s for s in ss if (s.get("age_days") or 999) <= 30]
        if not ss:
            out.append({
                **company, "priority": "monitor", "signal_count_30d": 0,
                "latest_date": None, "categories": [],
                "reasons": ["aucun signal récent collecté"]
            })
            continue

        levels = [s.get("priority", {}).get("level", "monitor") for s in ss]
        best = min(levels, key=lambda x: order.get(x, 9))
        cats = sorted({cat for s in recent for cat in s.get("categories", []) if cat != "robotics_exposure"})
        reasons = []
        if any(s.get("primary_source") for s in recent):
            reasons.append("filing primaire récent")
        media_sources = {
            s.get("source_name") for s in recent
            if s.get("source_type") in ("news", "alternative_proxy")
        }
        if len(media_sources) >= 3:
            reasons.append(f"{len(media_sources)} sources distinctes")
        strong = sorted(set(cats) & STRONG_CATEGORIES)
        if strong:
            reasons.append("catalyseurs: " + ", ".join(strong[:4]))
        dates = [s.get("date") for s in ss if s.get("date")]

        out.append({
            **company,
            "priority": best,
            "signal_count_30d": len(recent),
            "latest_date": max(dates) if dates else None,
            "categories": cats,
            "reasons": reasons or ["activité à surveiller"],
        })

    return sorted(out, key=lambda x: (order.get(x["priority"], 9), -(x.get("signal_count_30d") or 0), x["name"]))


def optional_gemini_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    enabled = os.environ.get("ENABLE_GEMINI_FREE", "0") == "1"
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    model = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash").strip()

    if not enabled:
        return {"enabled": False, "model": model, "status": "disabled", "note": "Couche IA désactivée par défaut."}
    if model != "gemini-3.8-flash":
        return {"enabled": False, "model": model, "status": "blocked", "note": "Modèle non autorisé en mode free-only."}
    if not key:
        return {"enabled": False, "model": model, "status": "no_key", "note": "Clé Gemini absente."}

    top = payload.get("signals", [])[:30]
    compact = [{
        "id": s["id"], "company": s.get("company_name"), "title": s.get("title"),
        "date": s.get("date"), "source": s.get("source_name"),
        "categories": s.get("categories"),
        "priority": s.get("priority", {}).get("level"),
        "snippet": s.get("snippet", "")[:500]
    } for s in top]

    prompt = """Analyse uniquement les signaux JSON fournis.
N'invente aucune information. Distingue faits et hypothèses.
Ne donne ni recommandation d'achat ni objectif de cours.
Retourne STRICTEMENT du JSON avec: summary, themes, notable_signal_ids, unknowns, contradictions.
Données:
""" + json.dumps(compact, ensure_ascii=False)

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
            "maxOutputTokens": 2500
        }
    }
    try:
        r = requests.post(url, params={"key": key}, json=body, timeout=90)
        r.raise_for_status()
        raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(raw)
        valid_ids = {s["id"] for s in top}
        parsed["notable_signal_ids"] = [x for x in parsed.get("notable_signal_ids", []) if x in valid_ids]
        return {"enabled": True, "model": model, "status": "ok", "result": parsed}
    except Exception as exc:
        return {"enabled": True, "model": model, "status": "error", "note": f"{type(exc).__name__}: {exc}"}


def main() -> None:
    cfg = load_json("research_config.json")
    theme = cfg["theme"]
    companies = cfg["companies"]
    cache = load_cache()
    provider_status: Dict[str, Any] = {}
    errors: List[str] = []

    news_signals, discovery, news_errors = collect_news(cfg, cache, provider_status)
    alt_signals, alt_discovery, alt_errors = collect_alternative(cfg, cache, provider_status)
    sec_signals, sec_errors = collect_sec(cfg, cache, provider_status)

    signals = news_signals + alt_signals + sec_signals
    discovery = discovery + alt_discovery
    errors.extend(news_errors + alt_errors + sec_errors)

    dedup = {}
    for signal in signals:
        dedup.setdefault(signal["id"], signal)
    signals = list(dedup.values())

    ddisc = {}
    for signal in discovery:
        ddisc.setdefault(signal["id"], signal)
    discovery = list(ddisc.values())
    discovery.sort(key=lambda s: s.get("date") or "", reverse=True)

    enrich_priorities(signals)
    order = {"high": 0, "medium": 1, "monitor": 2}
    signals.sort(key=lambda s: (
        order.get(s.get("priority", {}).get("level", "monitor"), 9),
        s.get("age_days") if s.get("age_days") is not None else 999
    ))

    cache.setdefault("meta", {})["last_run"] = now_iso()
    cache["meta"]["pipeline_revision"] = cfg.get("pipeline_revision")
    save_cache(cache)

    payload = {
        "schema_version": 2,
        "generated_at": now_iso(),
        "theme": {
            "id": theme["id"],
            "label": theme["label"],
            "description": theme["description"]
        },
        "method": {
            "news": "2 requêtes GDELT agrégées + cache persistant + fallback Google News RSS",
            "primary": "SEC EDGAR via CIK embarqués, cache par société et circuit breaker",
            "alternative": "Google News RSS · proxies explicites pour recrutements, brevets, contrats publics et capacité",
            "priority": "Règles transparentes basées sur récence, source primaire, catalyseur et corroboration",
            "note": "Une priorité de recherche n'est ni une recommandation d'achat ni une prévision de performance."
        },
        "provider_status": provider_status,
        "subthemes": theme["subthemes"],
        "companies": company_summaries(companies, signals),
        "signals": signals[:180],
        "discovery": discovery[:120],
        "errors": errors,
    }
    payload["ai"] = optional_gemini_summary(payload)

    last_good_path = STATE_DIR / "last_good_radar.json"
    usable_count = len(payload["signals"]) + len(payload["discovery"])
    if usable_count < 10 and last_good_path.exists():
        previous = json.loads(last_good_path.read_text(encoding="utf-8"))
        previous["generated_at"] = now_iso()
        previous["served_from_previous_success"] = True
        previous["provider_status"] = provider_status
        previous["errors"] = list(dict.fromkeys((previous.get("errors") or []) + errors + [
            "Collecte courante insuffisante : dernière sortie robuste servie depuis le cache persistant."
        ]))
        previous["ai"] = optional_gemini_summary(previous)
        payload = previous
    elif usable_count >= 10:
        payload["served_from_previous_success"] = False
        last_good_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    (ROOT / "radar.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"Catalyst Radar v3 — {len(payload['signals'])} signaux · "
        f"{len(payload['companies'])} sociétés · {len(payload['discovery'])} découvertes · "
        f"{len(errors)} avertissement(s)"
    )
    print("Providers:", json.dumps(provider_status, ensure_ascii=False))
    print("AI:", payload["ai"].get("status"), payload["ai"].get("model"))
    for err in errors:
        print("WARNING:", err)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        fallback = {
            "schema_version": 2,
            "generated_at": now_iso(),
            "theme": {"id": "robotics", "label": "Robotique & Physical AI"},
            "provider_status": {},
            "companies": [],
            "signals": [],
            "discovery": [],
            "ai": {"enabled": False, "status": "error"},
            "errors": [f"{type(exc).__name__}: {exc}"],
        }
        (ROOT / "radar.json").write_text(json.dumps(fallback, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Catalyst Radar degraded: {type(exc).__name__}: {exc}")
