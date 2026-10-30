# Ausgabeformat `web/data/festivals.json`

Diese Datei ist der Vertrag zwischen Pipeline und Website. Änderungen nur mit Absprache.

```jsonc
{
  "generated_at": "2026-10-30T12:00:00+00:00",
  "scoring": { "points_zero_listeners": 10000, "top_n": 5, "top_depth": 30 },   // Parameter, damit die Website sie anzeigen kann
  "festivals": [
    {
      "slug": "hurricane",
      "name": "Hurricane",
      "location": "Scheeßel",
      "editions": [                         // neueste zuerst
        {
          "year": 2027,
          "status": "upcoming",             // "upcoming" (Datum in der Zukunft) | "past"
          "source": "festivalplaner",
          "start_date": "2027-06-18", "end_date": "2027-06-20", "days": 3,
          "price_eur": 199.0, "capacity": 78001, "visitors": null,
          "act_count": 44,                  // Acts mit Daten in dieser Datei
          "reported_act_count": null,       // offizielle Anzahl laut Quelle, falls bekannt
          "completeness": 0.51,             // nur bei "upcoming": act_count / Act-Anzahl der letzten vergangenen Ausgabe; sonst oder wenn unbekannt null; max 1.0
          "strength_vs_previous": 1.01,     // nur bei "upcoming": scores.top30 / top30 der letzten vergangenen Ausgabe; sonst null
          "scores": {
            "total": 61.3,                  // Summe der Punkte
            "top30": 52.8,                  // Summe der top_depth besten Acts (fairer Vergleich verschieden großer Lineups)
            "headliner": 3.1,               // Mittelwert der top_n Acts nach Punkten
            "depth": 1.2,                   // Median der übrigen Acts (null bei <= top_n Acts)
            "per_day": 20.4,                // total / days
            "per_euro": 0.31                // total / price_eur (null ohne Preis)
          },
          "acts": [                         // sortiert nach position
            {
              "name": "Muse", "position": 0, "headliner": true,
              "spotify_id": "12Chz98pHFMPJEknJQMWvI",
              "monthly_listeners": 22300000,          // Spotify, null wenn unbekannt
              "lastfm_listeners": 6589278,            // null wenn unbekannt
              "listeners_estimated": false,           // true, wenn monthly_listeners aus Last.fm geschätzt wurde
              "points": 3.35                          // null, wenn gar keine Kennzahl vorhanden
            }
          ]
        }
      ]
    }
  ]
}
```

## Punkte pro Act

`points = max(0, log10(monthly_listeners) - log10(10_000))` → 10k = 0, 100k = 1, 1 Mio = 2, 10 Mio = 3, 100 Mio = 4.

Fehlen Spotify-Hörer, aber Last.fm-Hörer sind da, wird geschätzt:
`monthly_listeners ≈ lastfm_listeners × r`, wobei `r` der Median von `monthly_listeners / lastfm_listeners` über alle Acts mit beiden Werten im aktuellen Lauf ist (`listeners_estimated = true`). Acts ohne jede Kennzahl: `points = null`, zählen nicht in die Scores, aber in `act_count`.
