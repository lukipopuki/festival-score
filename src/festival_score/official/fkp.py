"""Parser für die TYPO3-Seiten von FKP Scorpio (Hurricane, Southside, Deichbrand, Highfield).

Zwei Templates:
- Hurricane/Southside (vermutlich auch Highfield): `a.m0132_lineupv2__show` mit `h4.m0132_lineupv2__artist`,
  Tagesabschnitte `div.m0132_lineupv2__day` (Überschrift z. B. "Freitag, 18.06.2027").
- Deichbrand: `a.m-lineup__card` (`data-lineup-item`) mit `div.m-lineup__artistName`, Tag in `data-day`.
- Fallback: Links nach `/line-up/` mit Text "<Name> Konzert".

Schreibweise: Die Seiten setzen Namen in VERSALIEN. Reihenfolge der Quellen für den Namen:
1. Originalschreibweise aus `title`/`alt` des Bilds (bzw. Deichbrand `alt`), wenn sie – ohne Groß-/Kleinschreibung
   und Sonderzeichen verglichen – zum Anzeigenamen passt und Kleinbuchstaben enthält ("Photo: X (Credit: …)"
   wird auf "X" gekürzt).
2. Sonst Title-Case je Wort; unverändert bleiben Wörter mit Ziffer, Punkt, Schrägstrich oder ohne Vokal
   ("MGK", "AC/DC", "K.I.Z", "BLEECH 9:3", "RAUM27"); kleine Füllwörter (and, the, of, de, …)
   werden mitten im Namen klein geschrieben; Bindestrich-Teile einzeln ("ALT-J" -> "Alt-J").
Namen mit Kleinbuchstaben auf der Seite bleiben unverändert. Weiche Trennzeichen (U+00AD) werden entfernt.

Headliner: Die Seiten heben keine Acts hervor (gleiche Klassen/Größen für alle Karten) -> leere Liste.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator

from bs4 import BeautifulSoup, Tag

from festival_score.official import OfficialLineup

_SMALL_WORDS = {"and", "the", "of", "de", "von", "van", "in", "a", "an", "at", "la", "le"}
_VOWELS = set("AEIOUYÄÖÜÀÁÂÈÉÊÌÍÎÒÓÔÙÚÛÏËÆØÅ")
_PHOTO_RE = re.compile(r"^(?:\[Translate[^\]]*\]\s*)?Photo:\s*(.+?)\s*(?:\((?:No )?Credit[^)]*\))?\s*$", re.I)
_YEAR_RE = re.compile(r"\b(20\d{2})\b")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("­", "")).strip()


def _key(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.casefold())


def _title_word(word: str, first: bool) -> str:
    if any(c.isdigit() or c in "./&" for c in word):
        return word
    letters = [c for c in word if c.isalpha()]
    if not letters:
        return word
    if not any(c.upper() in _VOWELS for c in letters) and len(letters) > 1:
        return word  # Kurzform wie MGK
    if "-" in word:
        return "-".join(_title_word(p, first and i == 0) if p else p for i, p in enumerate(word.split("-")))
    low = word.lower()
    if not first and low in _SMALL_WORDS:
        return low
    return low[:1].upper() + low[1:]


def smart_case(name: str) -> str:
    """Versalien-Namen in sinnvolle Schreibweise bringen (siehe Moduldoku)."""
    if any(c.islower() for c in name):
        return name
    return " ".join(_title_word(w, i == 0) for i, w in enumerate(name.split(" ")))


def _original(name: str, candidates: list[str]) -> str:
    """Originalschreibweise aus Bild-Attributen, sonst `smart_case`."""
    for cand in candidates:
        cand = _clean(cand)
        m = _PHOTO_RE.match(cand)
        if m:
            cand = m.group(1)
        if any(c.islower() for c in cand) and _key(cand) == _key(name):
            return cand
    return smart_case(name)


def _year(soup: BeautifulSoup) -> int | None:
    # Überschrift "LINE-UP 2027", dann Termine/Tage, dann ld+json, dann Titel
    for sel in ("h1", ".m-smallStage__headline"):
        for el in soup.select(sel):
            m = _YEAR_RE.search(el.get_text(" ", strip=True))
            if m:
                return int(m.group(1))
    for el in soup.select(".m0132_lineupv2__daytitle, .m-smallStage__metaText"):
        m = _YEAR_RE.search(el.get_text(" ", strip=True))
        if m:
            return int(m.group(1))
    for el in soup.select("[data-day]"):
        m = re.match(r"(20\d{2})-\d\d-\d\d", el.get("data-day", ""))
        if m:
            return int(m.group(1))
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "")
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("startDate"):
                m = _YEAR_RE.search(str(item["startDate"]))
                if m:
                    return int(m.group(1))
    if soup.title and soup.title.string:
        m = _YEAR_RE.search(soup.title.string)
        if m:
            return int(m.group(1))
    return None


def _img_texts(a: Tag) -> list[str]:
    img = a.find("img")
    if not isinstance(img, Tag):
        return []
    return [str(img.get(k)) for k in ("title", "alt") if img.get(k)]


def _entries(soup: BeautifulSoup) -> Iterator[tuple[str, str, list[str]]]:
    """(Anzeigename, Href, Kandidaten für Originalschreibweise) in Seitenreihenfolge."""
    cards = soup.select("a.m0132_lineupv2__show")
    for a in cards:
        el = a.select_one(".m0132_lineupv2__artist")
        if el:
            yield _clean(el.get_text(" ")), str(a.get("href", "")), _img_texts(a)
    cards = soup.select("a.m-lineup__card, a[data-lineup-item]")
    for a in cards:
        el = a.select_one(".m-lineup__artistName")
        if el:
            cand = _img_texts(a) + ([str(a["data-search"])] if a.get("data-search") else [])
            yield _clean(el.get_text(" ")), str(a.get("href", "")), cand
    if soup.select_one("a.m0132_lineupv2__show, a.m-lineup__card, a[data-lineup-item]"):
        return
    # Fallback: "<NAME> Konzert"-Links
    for a in soup.find_all("a", href=True):
        text = _clean(a.get_text(" "))
        if "/line-up/" in str(a["href"]) and text.endswith(" Konzert"):
            yield text[: -len(" Konzert")].strip(), str(a["href"]), _img_texts(a)


def parse(html: str, url: str) -> OfficialLineup | None:
    soup = BeautifulSoup(html, "html.parser")
    acts: list[str] = []
    seen: set[str] = set()
    for name, href, cands in _entries(soup):
        if not name:
            continue
        name = re.sub(r"\s+Konzert$", "", name)
        # Deichbrand verlinkt einige Acts mit Query-Href, deshalb zählt der Name als Schlüssel
        keys = {_key(name)}
        if href:
            keys.add(href)
        if seen & keys:
            continue
        seen |= keys
        acts.append(_original(name, cands))
    if not acts:
        return None
    return OfficialLineup(acts=acts, headliners=[], year=_year(soup), url=url)
