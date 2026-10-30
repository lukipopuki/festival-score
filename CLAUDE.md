# festival-score

Bewertet die Lineups großer deutscher Festivals (aktuelles/nächstes Jahr) anhand von Streaming-Kennzahlen.
Ergebnis: statische Website auf GitHub Pages mit Rankings (gesamt, pro Tag, pro Euro).

## Architektur

```
festivalplaner.de ──> scraper ──> Festival/Act ─┐
Spotify / Last.fm ──> sources ──> ArtistMetrics ─┼─> scoring ──> data/processed/*.json ──> web/
```

- `src/festival_score/models.py` – gemeinsames Datenmodell (`Festival`, `Act`, `ArtistMetrics`). Nicht ohne Absprache ändern.
- `src/festival_score/cache.py` – JSON-Cache pro Quelle (`MetricsCache`), `normalize_name()` für Namensvergleiche.
- `src/festival_score/sources/base.py` – `MetricSource`-Protokoll. Jede Quelle ist ein Modul in `sources/`.
- `src/festival_score/scraper.py` – Lineups, Termine, Preise von festivalplaner.de.
- `config/festivals.yaml` – Liste der Festival-Slugs.

## Konventionen

- Python ≥ 3.11, Typannotationen, `from __future__ import annotations`.
- HTTP nur über `httpx`; immer `festival_score.USER_AGENT` als User-Agent, Timeout setzen, zwischen Requests an dieselbe Domain mindestens 1 s Pause.
- Funktionen, die HTTP machen, nehmen optional einen `httpx.Client` entgegen, damit Tests einen `httpx.MockTransport` einsetzen können.
- Tests ohne Netzwerk: echte Antworten als Fixture-Dateien unter `tests/fixtures/` ablegen.
- Secrets nur aus Umgebungsvariablen / `.env` (siehe `.env.example`), nie im Code oder in Fixtures.
- Kommentare und Docstrings auf Deutsch, knapp.

## Befehle

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"   # Setup (Windows)
.venv/Scripts/python -m pytest                                          # Tests
```
