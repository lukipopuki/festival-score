"""Lineups von den offiziellen Festival-Websites (genaueste Quelle für kommende Ausgaben).

Jeder Parser ist ein Modul in diesem Paket mit
    def parse(html: str, url: str) -> OfficialLineup | None
`config/official.yaml` ordnet jedem Festival-Slug Modul und URL zu.
"""

from __future__ import annotations

import importlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import yaml

from festival_score import USER_AGENT

CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "official.yaml"
TIMEOUT_S = 20.0
REQUEST_PAUSE_S = 1.0

log = logging.getLogger(__name__)
_last_request = 0.0


@dataclass
class OfficialLineup:
    acts: list[str]  # Reihenfolge wie auf der Seite; Namen bereinigt (ohne "Konzert", ohne Bühne/Tag)
    headliners: list[str] = field(default_factory=list)  # nur, wenn die Seite sie erkennbar hervorhebt
    year: int | None = None  # Ausgabe, falls auf der Seite erkennbar
    url: str = ""


def load_config(path: Path = CONFIG_PATH) -> dict[str, dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(k): v for k, v in (data.get("festivals") or {}).items()}


def get_html(url: str, client: httpx.Client | None = None) -> str | None:
    """Seite holen (höflich, mit Pause); 404 -> None."""
    global _last_request
    wait = _last_request + REQUEST_PAUSE_S - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    own = client is None
    client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S, follow_redirects=True)
    try:
        resp = client.get(url)
        _last_request = time.monotonic()
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.text
    finally:
        if own:
            client.close()


def fetch_official(slug: str, client: httpx.Client | None = None) -> OfficialLineup | None:
    """Offizielles Lineup eines Festivals; None, wenn nicht konfiguriert, nicht vorhanden oder leer."""
    cfg = load_config().get(slug)
    if not cfg:
        return None
    try:
        parser = importlib.import_module(f"festival_score.official.{cfg['parser']}")
    except ImportError:
        log.warning("%s: Parser %r fehlt", slug, cfg["parser"])
        return None
    html = get_html(cfg["url"], client)
    if html is None:
        return None
    lineup = parser.parse(html, cfg["url"])
    if lineup is None or not lineup.acts:
        return None
    lineup.url = lineup.url or cfg["url"]
    return lineup


def main() -> None:
    """`python -m festival_score.official [slug …]` – Übersicht der offiziellen Lineups."""
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for slug in sys.argv[1:] or list(load_config()):
        try:
            lu = fetch_official(slug)
        except Exception:  # noqa: BLE001
            log.exception("%s fehlgeschlagen", slug)
            continue
        if lu is None:
            print(f"{slug}: kein offizielles Lineup")
        else:
            print(f"{slug}: {len(lu.acts)} Acts, Jahr {lu.year}, Headliner: {', '.join(lu.headliners) or '-'}")
            print("   " + ", ".join(lu.acts[:12]) + (" …" if len(lu.acts) > 12 else ""))


if __name__ == "__main__":
    main()
