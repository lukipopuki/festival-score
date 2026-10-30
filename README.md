# festival-score

Wie stark ist das Lineup großer deutscher Festivals? Dieses Hobbyprojekt bewertet die Lineups von Rock am Ring, Rock im Park, Hurricane, Southside, Wacken, Lollapalooza Berlin, Parookaville, Deichbrand und Highfield anhand der monatlichen Spotify-Hörer der Acts – für die kommende Ausgabe und die letzten drei Jahre.

**Website:** https://lukipopuki.github.io/festival-score/

## Bewertung

- **Punkte pro Act:** `log10(monatliche Hörer) − 4` (10.000 Hörer = 0, 100.000 = 1, 1 Mio. = 2, 10 Mio. = 3, 100 Mio. = 4). Fehlen Spotify-Zahlen, wird aus Last.fm geschätzt.
- **Top 30:** Summe der Punkte der 30 stärksten Acts – fairer Vergleich unterschiedlich großer Lineups.
- **Headliner-Stärke:** Mittelwert der 5 stärksten Acts. **Breite:** Median der übrigen.
- **Pro Tag / pro Euro:** Gesamtpunkte geteilt durch Festivaltage bzw. Ticketpreis.
- **Vollständigkeit:** angekündigte Acts im Verhältnis zur Vorjahresausgabe.

Details zum Datenformat: [docs/data-format.md](docs/data-format.md).

## Datenquellen

- Lineups, Termine, Preise der kommenden Ausgaben: [festivalplaner.de](https://www.festivalplaner.de), ersatzweise [openairguide.net](https://www.openairguide.net)
- Lineups vergangener Ausgaben: [openairguide.net](https://www.openairguide.net)
- Preise und Besucherzahlen vergangener Jahre: Wikipedia (CC BY-SA 4.0)
- Hörerzahlen: Spotify (Web-API und öffentliche Künstlerseiten), [Last.fm](https://www.last.fm/api)

Die Hörerzahlen sind immer die von heute – auch für vergangene Jahre. Keine Gewähr.

## Selbst ausführen

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"      # unter Linux/macOS: .venv/bin/python
cp .env.example .env                                  # Spotify- und Last.fm-Keys eintragen
.venv/Scripts/python -m festival_score.pipeline       # schreibt web/data/festivals.json
.venv/Scripts/python -m http.server 8000 --directory web
```

Tests: `.venv/Scripts/python -m pytest`
