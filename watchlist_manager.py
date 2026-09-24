"""Owner-only helper used by the GitHub issue workflow to edit watchlist.json."""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PATH = ROOT / "watchlist.json"
TITLE_PATTERN = re.compile(r"^\[Company Lab\]\s+(ADD|REMOVE)\s+([A-Za-z0-9.\^=\-]{1,24})\s*$", re.I)


def write_output(key: str, value: str) -> None:
    target = os.getenv("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(f"{key}={value}\n")


def main() -> None:
    title = os.getenv("ISSUE_TITLE", "")
    match = TITLE_PATTERN.fullmatch(title.strip())
    if not match:
        write_output("valid", "false")
        write_output("changed", "false")
        print("Titre invalide")
        return

    action, ticker = match.group(1).upper(), match.group(2).upper()
    data = json.loads(PATH.read_text(encoding="utf-8"))
    items = data.setdefault("items", [])
    before = len(items)
    if action == "ADD":
        if not any(str(item.get("ticker", "")).upper() == ticker for item in items):
            items.append({
                "ticker": ticker,
                "label": ticker,
                "theme": "À qualifier",
                "note": "Ajouté depuis Company Lab",
            })
    else:
        data["items"] = [item for item in items if str(item.get("ticker", "")).upper() != ticker]

    changed = len(data["items"]) != before
    data["updated_at"] = date.today().isoformat()
    if changed:
        PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    write_output("valid", "true")
    write_output("changed", "true" if changed else "false")
    write_output("action", action.lower())
    write_output("ticker", ticker)
    print(f"{action} {ticker}: {'modifié' if changed else 'déjà à jour'}")


if __name__ == "__main__":
    main()
