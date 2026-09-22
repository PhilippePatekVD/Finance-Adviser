import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / ".research_state"
STATE_PATH = STATE_DIR / "state.json"
OUTPUT = ROOT / "research.json"

NEGATIVE_TERMS = [
    "cancel", "cancelled", "canceled", "delay", "delayed", "postpone", "postponed",
    "terminate", "terminated", "lower guidance", "cuts guidance", "reduce outlook",
    "withdraw guidance", "impairment", "shutdown", "plant closure", "layoff"
]

GENERIC_ENTITY_WORDS = {
    "The","This","That","New","How","Why","What","When","Where","Robot","Robots","Robotics",
    "AI","Physical","Industrial","Humanoid","Humanoids","Automation","Machine","Vision","Warehouse",
    "Market","Markets","Global","Future","Inside","Could","Can","Will","Company","Companies","Stock",
    "Stocks","Shares","Technology","Technologies","Industry","Industries","Report","Reports","News",
    "World","Today","Latest","Top","Best","Rise","Growth","Demand","Platform","System","Systems"
}

COMPONENTS = {
    "humanoids": ["actuators","precision reducers","motors","force/torque sensing","vision","compute","control software"],
    "industrial": ["robot arms","cobots","motion control","PLC/control","safety","machine vision","end effectors"],
    "logistics": ["AMR","AS/RS","warehouse software","mobile robots","sorting","vision","fleet control"],
    "motion": ["servo motors","drives","precision reducers","bearings","linear motion","actuators","encoders"],
    "vision": ["cameras","3D vision","machine vision","sensors","edge inference","inspection software"],
    "physical_ai": ["simulation","foundation models","edge compute","robot learning","digital twins","control stacks"]
}

OPEN_QUESTION_MAP = {
    "orders_backlog": "Le signal se traduit-il par une croissance durable du backlog et des revenus ?",
    "capacity_capex": "La nouvelle capacité répond-elle à des commandes identifiées ou anticipe-t-elle seulement la demande ?",
    "partnerships": "Le partenariat contient-il des volumes, des exclusivités ou des jalons commerciaux concrets ?",
    "product_launch": "Le produit passe-t-il du lancement à l'adoption commerciale et à la production en volume ?",
    "guidance": "Quelle part de la guidance est réellement attribuable au thème robotique ?",
    "customer_adoption": "Le pilote ou design win devient-il un déploiement à grande échelle ?",
    "government_defense": "Le contrat public est-il récurrent et économiquement matériel ?",
    "robotics_exposure": "L'exposition robotique est-elle financièrement matérielle ou surtout narrative ?"
}


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def days_since(value: Any) -> Optional[int]:
    d = parse_dt(value)
    if not d:
        return None
    return max(0, int((datetime.now(timezone.utc) - d).total_seconds() // 86400))


def norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def source_key(signal: Dict[str, Any]) -> str:
    return norm(signal.get("source_name") or "unknown")


def company_key(signal: Dict[str, Any]) -> str:
    return str(signal.get("company_ticker") or "").upper()


def categories(signal: Dict[str, Any]) -> set:
    return set(signal.get("categories") or [])


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, norm(a)[:2000], norm(b)[:2000]).ratio()


def load_state() -> Dict[str, Any]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state = load_json(STATE_PATH, {})
    state.setdefault("version", 1)
    state.setdefault("created_at", now_iso())
    state.setdefault("signals", {})
    state.setdefault("runs", [])
    state.setdefault("theme_history", {})
    return state


def classify_change(signal: Dict[str, Any], previous_signals: List[Dict[str, Any]]) -> Tuple[str, List[str]]:
    reasons = []
    text = norm((signal.get("title") or "") + " " + (signal.get("snippet") or ""))
    cats = categories(signal)
    prior_cats = set()
    prior_same_company = []
    for prev in previous_signals:
        if company_key(prev) == company_key(signal) and company_key(signal):
            prior_same_company.append(prev)
            prior_cats |= categories(prev)

    if any(term in text for term in NEGATIVE_TERMS):
        return "invalidation", ["vocabulaire de retard, baisse ou annulation détecté"]

    new_cats = cats - prior_cats
    if "customer_adoption" in cats and any(
        ("product_launch" in categories(p) or "partnerships" in categories(p))
        for p in prior_same_company
    ):
        return "materialisation", ["adoption client après un signal antérieur de produit/partenariat"]

    if signal.get("id") not in {p.get("id") for p in previous_signals}:
        if signal.get("primary_source") and new_cats:
            reasons.append("nouvelle catégorie dans une source primaire")
            return "new", reasons
        if new_cats:
            reasons.append("nouvelle catégorie de catalyseur")
            return "new", reasons

        recent_same = [
            p for p in prior_same_company
            if days_since(p.get("last_seen") or p.get("date")) is not None
            and days_since(p.get("last_seen") or p.get("date")) <= 30
            and (categories(p) & cats)
        ]
        if len(recent_same) >= 2:
            return "acceleration", [f"{len(recent_same)+1} signaux proches sur le même thème"]
        if prior_same_company:
            return "confirmation", ["nouvelle preuve sur une exposition déjà observée"]
        return "new", ["premier signal observé pour cette société"]

    return "existing", ["signal déjà connu"]


def financial_materiality(signal: Dict[str, Any]) -> Dict[str, Any]:
    text = (signal.get("title") or "") + " " + (signal.get("snippet") or "")
    amount_patterns = [
        r"(?:\$|€|£|CHF\s?)\s?\d+(?:[.,]\d+)?\s?(?:million|billion|m|bn)?",
        r"\b\d+(?:[.,]\d+)?\s?%\b",
        r"\b\d+(?:[.,]\d+)?\s?(?:million|billion)\b"
    ]
    matches = []
    for pattern in amount_patterns:
        matches.extend(re.findall(pattern, text, flags=re.I))
    return {
        "quantified_in_excerpt": bool(matches),
        "markers": list(dict.fromkeys(matches))[:5],
        "note": "chiffré dans l'extrait" if matches else "impact financier non chiffré dans l'extrait collecté"
    }


def evidence_pack(signal: Dict[str, Any], all_signals: List[Dict[str, Any]], change: Dict[str, Any]) -> Dict[str, Any]:
    ticker = company_key(signal)
    cats = categories(signal)
    related = [
        s for s in all_signals
        if s.get("id") != signal.get("id")
        and company_key(s) == ticker
        and (categories(s) & cats)
    ]
    related.sort(key=lambda s: (0 if s.get("primary_source") else 1, s.get("age_days") if s.get("age_days") is not None else 999))
    proofs = [signal] + related[:4]
    open_questions = [OPEN_QUESTION_MAP[c] for c in cats if c in OPEN_QUESTION_MAP][:4]
    if not open_questions:
        open_questions = ["Quel élément observable permettrait de confirmer que ce signal devient économiquement matériel ?"]

    invalidators = [
        "absence de confirmation dans les prochaines publications",
        "retard, annulation ou baisse de guidance",
        "signal restant narratif sans commandes, capacité ou adoption mesurable"
    ]

    return {
        "id": "pack-" + str(signal.get("id")),
        "company_ticker": ticker or None,
        "company_name": signal.get("company_name"),
        "headline": signal.get("title"),
        "date": signal.get("date"),
        "priority": (signal.get("priority") or {}).get("level", "monitor"),
        "change_type": change.get("type"),
        "what_changed": change.get("reasons", []),
        "categories": sorted(cats),
        "primary_source": bool(signal.get("primary_source")),
        "financial_materiality": financial_materiality(signal),
        "proofs": [{
            "title": p.get("title"),
            "source": p.get("source_name"),
            "date": p.get("date"),
            "url": p.get("url"),
            "primary": bool(p.get("primary_source"))
        } for p in proofs],
        "open_questions": open_questions,
        "invalidators": invalidators
    }


def build_graph(config: Dict[str, Any], radar: Dict[str, Any]) -> Dict[str, Any]:
    nodes = []
    edges = []
    seen = set()

    def add_node(node_id: str, label: str, kind: str, **extra: Any) -> None:
        if node_id in seen:
            return
        seen.add(node_id)
        nodes.append({"id": node_id, "label": label, "kind": kind, **extra})

    add_node("theme:robotics", "Robotique & Physical AI", "theme")

    subtheme_labels = {x["id"]: x["label"] for x in config["theme"]["subthemes"]}
    for sid, label in subtheme_labels.items():
        add_node("subtheme:" + sid, label, "subtheme")
        edges.append({"source": "theme:robotics", "target": "subtheme:" + sid, "type": "contains"})
        for comp in COMPONENTS.get(sid, []):
            cid = "component:" + re.sub(r"[^a-z0-9]+", "-", comp.lower()).strip("-")
            add_node(cid, comp, "component")
            edges.append({"source": "subtheme:" + sid, "target": cid, "type": "uses"})

    company_signal_counts = Counter(s.get("company_ticker") for s in radar.get("signals", []) if s.get("company_ticker"))
    for company in config.get("companies", []):
        cid = "company:" + company["ticker"]
        add_node(cid, company["name"], "company", ticker=company["ticker"], signals=company_signal_counts.get(company["ticker"], 0))
        for sid in company.get("subthemes", []):
            edges.append({"source": cid, "target": "subtheme:" + sid, "type": "exposed_to"})

    return {"nodes": nodes, "edges": edges}


def candidate_entities(discovery: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counts = Counter()
    examples = defaultdict(list)
    themes = defaultdict(set)
    pattern = re.compile(r"\b([A-Z][A-Za-z0-9&.'-]+(?:\s+[A-Z][A-Za-z0-9&.'-]+){1,3})\b")
    for item in discovery:
        title = str(item.get("title") or "")
        for match in pattern.findall(title):
            words = match.split()
            if any(w in GENERIC_ENTITY_WORDS for w in words):
                continue
            if len(match) < 5 or len(match) > 60:
                continue
            counts[match] += 1
            if len(examples[match]) < 3:
                examples[match].append({"title": title, "url": item.get("url"), "source": item.get("source_name")})
            themes[match].update(item.get("subthemes") or [])
    rows = []
    for entity, count in counts.most_common(30):
        rows.append({
            "entity": entity,
            "mentions": count,
            "subthemes": sorted(themes[entity]),
            "examples": examples[entity],
            "confidence": "candidate",
            "note": "Entité extraite automatiquement de titres ; à vérifier avant de la considérer comme une société cotée."
        })
    return rows


def theme_metrics(radar: Dict[str, Any], state: Dict[str, Any]) -> List[Dict[str, Any]]:
    current = []
    prior_runs = state.get("runs", [])
    last = prior_runs[-1] if prior_runs else {}
    last_themes = {x["id"]: x for x in last.get("themes", [])}

    for st in radar.get("subthemes", []):
        sid = st["id"]
        sigs = [s for s in radar.get("signals", []) if sid in (s.get("subthemes") or [])]
        discovery = [s for s in radar.get("discovery", []) if sid in (s.get("subthemes") or [])]
        total = len(sigs) + len(discovery)
        recent7 = sum(1 for s in sigs + discovery if (s.get("age_days") is not None and s.get("age_days") <= 7))
        companies = len({s.get("company_ticker") for s in sigs if s.get("company_ticker")})
        sources = len({source_key(s) for s in sigs + discovery if source_key(s)})
        prev = last_themes.get(sid, {})
        prev_total = prev.get("signal_count")
        delta = None if prev_total in (None, 0) else round((total / prev_total - 1) * 100, 1)

        if total >= 45:
            saturation = "high"
        elif total >= 18:
            saturation = "medium"
        else:
            saturation = "low"

        if delta is not None and delta >= 35 and saturation != "high":
            emergence = "accelerating"
        elif recent7 >= max(4, total * 0.35) and saturation == "low":
            emergence = "early"
        elif delta is not None and delta <= -25:
            emergence = "cooling"
        else:
            emergence = "steady"

        current.append({
            "id": sid,
            "label": st.get("label"),
            "signal_count": total,
            "recent_7d": recent7,
            "company_breadth": companies,
            "source_diversity": sources,
            "delta_vs_previous_run_pct": delta,
            "media_saturation": saturation,
            "emergence": emergence
        })
    return current


def portfolio_links(data: Dict[str, Any], radar: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    held = {str(x.get("ticker") or "").upper(): x for x in data.get("portfolio", {}).get("positions", [])}
    watched = {str(x.get("ticker") or "").upper() for x in data.get("watchlist", [])}
    companies = {str(c.get("ticker") or "").upper(): c for c in config.get("companies", [])}
    direct = []
    watch = []
    for ticker, company in companies.items():
        summary = next((x for x in radar.get("companies", []) if str(x.get("ticker") or "").upper() == ticker), None)
        if ticker in held:
            direct.append({
                "ticker": ticker, "name": company.get("name"), "weight_pct": held[ticker].get("current_weight_pct"),
                "priority": (summary or {}).get("priority"), "signals_30d": (summary or {}).get("signal_count_30d", 0)
            })
        if ticker in watched:
            watch.append({
                "ticker": ticker, "name": company.get("name"), "priority": (summary or {}).get("priority"),
                "signals_30d": (summary or {}).get("signal_count_30d", 0)
            })
    return {
        "direct_positions": direct,
        "watchlist_links": watch,
        "lookthrough_status": "not_available",
        "note": "Les expositions indirectes à travers les ETF ne sont pas inférées sans données de composition fiables."
    }


def dossier_for(company: Dict[str, Any], radar: Dict[str, Any], changes: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    ticker = company["ticker"]
    sigs = [s for s in radar.get("signals", []) if str(s.get("company_ticker") or "") == ticker]
    sigs.sort(key=lambda s: s.get("date") or "", reverse=True)
    cats = Counter(c for s in sigs for c in s.get("categories", []) if c != "robotics_exposure")
    questions = []
    for c, _ in cats.most_common(4):
        q = OPEN_QUESTION_MAP.get(c)
        if q and q not in questions:
            questions.append(q)
    if not questions:
        questions.append("Quelle part du chiffre d'affaires ou du backlog est réellement liée à la robotique ?")

    change_types = Counter(changes.get(s.get("id"), {}).get("type") for s in sigs)
    evidence = sum(1 for s in sigs if s.get("primary_source"))
    hypothesis = (
        f"Suivre si {company['name']} transforme son exposition à "
        + ", ".join(company.get("subthemes", [])[:3])
        + " en commandes, capacité, adoption client ou guidance mesurable."
    )
    counter = "Le thème peut rester narratif ou marginal sans contribution financière identifiable."

    return {
        "ticker": ticker,
        "name": company["name"],
        "subthemes": company.get("subthemes", []),
        "signal_count": len(sigs),
        "primary_evidence_count": evidence,
        "latest_signal": sigs[0].get("date") if sigs else None,
        "top_categories": [{"category": c, "count": n} for c, n in cats.most_common(5)],
        "change_types": {k: v for k, v in change_types.items() if k},
        "working_hypothesis": hypothesis,
        "counter_hypothesis": counter,
        "open_questions": questions[:5],
        "next_watch": "Prochaine publication, guidance, backlog, capex et annonces client/production.",
        "recent_signal_ids": [s.get("id") for s in sigs[:10]]
    }


def audit_signals(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    stored = list(state.get("signals", {}).values())
    rows = []
    for s in stored:
        first = parse_dt(s.get("first_seen"))
        if not first:
            continue
        age = days_since(s.get("first_seen")) or 0
        if age < 30:
            continue
        ticker = str(s.get("company_ticker") or "")
        cats = set(s.get("categories") or [])
        later = [
            x for x in stored
            if x.get("id") != s.get("id")
            and str(x.get("company_ticker") or "") == ticker
            and parse_dt(x.get("first_seen")) and parse_dt(x.get("first_seen")) > first
            and set(x.get("categories") or []) & cats
        ]
        if len(later) >= 2:
            outcome = "confirmed"
        elif age >= 90 and not later:
            outcome = "unconfirmed"
        else:
            outcome = "pending"
        rows.append({
            "id": s.get("id"),
            "company_ticker": ticker or None,
            "title": s.get("title"),
            "first_seen": s.get("first_seen"),
            "age_days": age,
            "follow_up_signals": len(later),
            "outcome": outcome
        })
    rows.sort(key=lambda x: ({"unconfirmed": 0, "confirmed": 1, "pending": 2}.get(x["outcome"], 9), -x["age_days"]))
    return rows[:100]


def update_state(state: Dict[str, Any], radar: Dict[str, Any], themes: List[Dict[str, Any]]) -> None:
    ts = now_iso()
    for s in radar.get("signals", []):
        sid = s.get("id")
        if not sid:
            continue
        existing = state["signals"].get(sid)
        if existing:
            existing["last_seen"] = ts
            existing["seen_count"] = int(existing.get("seen_count", 1)) + 1
        else:
            state["signals"][sid] = {
                "id": sid,
                "title": s.get("title"),
                "company_ticker": s.get("company_ticker"),
                "company_name": s.get("company_name"),
                "categories": s.get("categories", []),
                "source_type": s.get("source_type"),
                "source_name": s.get("source_name"),
                "date": s.get("date"),
                "first_seen": ts,
                "last_seen": ts,
                "seen_count": 1,
                "snippet": (s.get("snippet") or "")[:1200],
                "primary_source": bool(s.get("primary_source"))
            }

    state["runs"].append({
        "generated_at": radar.get("generated_at") or ts,
        "themes": themes,
        "signal_count": len(radar.get("signals", [])),
        "discovery_count": len(radar.get("discovery", []))
    })
    state["runs"] = state["runs"][-120:]

    if len(state["signals"]) > 1200:
        ordered = sorted(state["signals"].values(), key=lambda x: x.get("last_seen") or "", reverse=True)[:1200]
        state["signals"] = {x["id"]: x for x in ordered}

    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    radar = load_json(ROOT / "radar.json", {})
    data = load_json(ROOT / "data.json", {})
    config = load_json(ROOT / "research_config.json", {})
    state = load_state()

    previous = list(state.get("signals", {}).values())
    changes: Dict[str, Dict[str, Any]] = {}
    for signal in radar.get("signals", []):
        kind, reasons = classify_change(signal, previous)
        changes[signal.get("id")] = {"type": kind, "reasons": reasons}

    priority_order = {"high": 0, "medium": 1, "monitor": 2}
    pack_candidates = sorted(
        radar.get("signals", []),
        key=lambda s: (
            priority_order.get((s.get("priority") or {}).get("level", "monitor"), 9),
            s.get("age_days") if s.get("age_days") is not None else 999,
            0 if s.get("primary_source") else 1
        )
    )
    packs = []
    seen_company_cat = set()
    for signal in pack_candidates:
        key = (company_key(signal), tuple(sorted(categories(signal))))
        if key in seen_company_cat and len(packs) >= 12:
            continue
        seen_company_cat.add(key)
        packs.append(evidence_pack(signal, radar.get("signals", []), changes.get(signal.get("id"), {})))
        if len(packs) >= 24:
            break

    themes = theme_metrics(radar, state)
    dossiers = [
        dossier_for(c, radar, changes)
        for c in config.get("companies", [])
    ]
    dossiers.sort(key=lambda x: (-x["signal_count"], x["name"]))

    new_changes = []
    for signal in radar.get("signals", []):
        change = changes.get(signal.get("id"), {})
        if change.get("type") in {"new", "confirmation", "acceleration", "materialisation", "invalidation"}:
            new_changes.append({
                "signal_id": signal.get("id"),
                "company_ticker": signal.get("company_ticker"),
                "company_name": signal.get("company_name"),
                "title": signal.get("title"),
                "date": signal.get("date"),
                "source": signal.get("source_name"),
                "url": signal.get("url"),
                "change_type": change.get("type"),
                "reasons": change.get("reasons", []),
                "priority": (signal.get("priority") or {}).get("level", "monitor"),
                "categories": signal.get("categories", [])
            })
    new_changes = new_changes[:80]

    providers = radar.get("provider_status") or {}
    gdelt = providers.get("gdelt") or {}
    google = providers.get("google_news") or {}
    sec = providers.get("sec") or {}

    source_coverage = [
        {
            "source": "SEC EDGAR",
            "status": sec.get("status", "unknown"),
            "purpose": "filings primaires et détection de changements",
            "note": (
                f"CIK embarqués · {sec.get('network_successes', 0)}/{sec.get('network_attempts', 0)} appels réseau réussis · "
                f"{sec.get('fresh_cache_hits', 0)} cache frais · circuit breaker {'ouvert' if sec.get('circuit_breaker_open') else 'fermé'}"
            )
        },
        {
            "source": "GDELT",
            "status": gdelt.get("status", "unknown"),
            "purpose": "actualité mondiale et corroboration",
            "note": (
                f"{gdelt.get('successful_batches', 0)}/{gdelt.get('requests', 0)} lots réussis · "
                f"{gdelt.get('cache_fallbacks', 0)} cache · {gdelt.get('google_fallbacks', 0)} fallback Google"
            )
        },
        {
            "source": "Google News RSS",
            "status": google.get("status", "unknown"),
            "purpose": "fallback média et signaux alternatifs",
            "note": (
                f"{google.get('successful_batches', 0)}/{google.get('requests', 0)} lots alternatifs réussis · "
                f"{google.get('cache_fallbacks', 0)} cache"
            )
        },
        {"source": "Yahoo Finance / yfinance", "status": "active", "purpose": "prix et historique marché", "note": "cotations indicatives"},
        {"source": "Job postings", "status": "proxy_only", "purpose": "proxy via presse/RSS", "note": "pas de source directe"},
        {"source": "Patents", "status": "proxy_only", "purpose": "proxy via presse/RSS", "note": "pas de registre direct connecté"},
        {"source": "Tenders / grants", "status": "proxy_only", "purpose": "proxy via presse/filings", "note": "pas de registre universel direct"}
    ]

    result = {
        "schema_version": 1,
        "generated_at": now_iso(),
        "memory": {
            "state_created_at": state.get("created_at"),
            "known_signal_count": len(state.get("signals", {})),
            "historical_run_count": len(state.get("runs", [])),
            "storage": "GitHub Actions cache",
            "note": "La mémoire machine persiste entre les runs sans être commitée dans le dépôt."
        },
        "changes": new_changes,
        "evidence_packs": packs,
        "themes": themes,
        "knowledge_graph": build_graph(config, radar),
        "unknown_candidates": candidate_entities(radar.get("discovery", [])),
        "portfolio_links": portfolio_links(data, radar, config),
        "dossiers": dossiers,
        "signal_audit": audit_signals(state),
        "source_coverage": source_coverage,
        "method_note": "Les classements décrivent la priorité de recherche et la nouveauté des preuves. Ils ne constituent ni une recommandation, ni une prévision de rendement."
    }

    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    update_state(state, radar, themes)
    print(
        f"Research OS — {len(result['changes'])} changements · {len(packs)} evidence packs · "
        f"{len(dossiers)} dossiers · {len(result['unknown_candidates'])} candidats hors univers"
    )


if __name__ == "__main__":
    main()
