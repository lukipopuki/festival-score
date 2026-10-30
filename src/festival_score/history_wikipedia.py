"""Wikipedia (de): Eckdaten und Headliner vergangener Festivalausgaben aus dem Wikitext.

Die Artikel sind uneinheitlich aufgebaut. Statt pro Artikel Sonderlogik zu haben, werden alle
Wikitabellen anhand ihrer Spaltenüberschriften interpretiert (Jahr/Datum, Besucher, Preis, Bands,
Headliner, Künstler), plus Klappleisten (`{{Klappleiste/Anfang | TITEL = 2024}}`) und
Jahresabschnitte (`=== 2024 ===` mit Aufzählung). Die Ergebnisse werden pro Jahr zusammengeführt.
"""

from __future__ import annotations

import html
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date

import httpx

from festival_score import USER_AGENT

API_URL = "https://de.wikipedia.org/w/api.php"
ARTICLE_URL = "https://de.wikipedia.org/wiki/"
REQUEST_PAUSE_S = 1.0
TIMEOUT_S = 20.0

log = logging.getLogger(__name__)
_last_request = 0.0


class WikipediaError(Exception):
    """Artikel konnte nicht geholt werden."""


@dataclass
class WikiYear:
    year: int
    start_date: date | None = None
    end_date: date | None = None
    visitors: int | None = None
    price_eur: float | None = None
    reported_act_count: int | None = None
    headliners: list[str] = field(default_factory=list)
    lineup: list[str] = field(default_factory=list)  # volle Liste, falls der Artikel sie hat
    cancelled: bool = False


# --- Abruf -------------------------------------------------------------------------------------


def fetch_wikitext(title: str, client: httpx.Client | None = None) -> str:
    """Holt den Wikitext eines Artikels (mind. REQUEST_PAUSE_S Abstand zum letzten Request)."""
    global _last_request
    own = client is None
    if client is None:
        client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S)
    try:
        wait = REQUEST_PAUSE_S - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        try:
            resp = client.get(
                API_URL,
                params={"action": "parse", "page": title, "prop": "wikitext", "format": "json", "redirects": 1},
                headers={"User-Agent": USER_AGENT},
            )
        except httpx.HTTPError as e:
            raise WikipediaError(f"{title}: Netzwerkfehler: {e}") from e
        finally:
            _last_request = time.monotonic()
        if resp.status_code >= 400:
            raise WikipediaError(f"{title}: HTTP {resp.status_code}")
        data = resp.json()
        if "error" in data:
            raise WikipediaError(f"{title}: {data['error'].get('info', data['error'])}")
        return data["parse"]["wikitext"]["*"]
    finally:
        if own:
            client.close()


def fetch_wikipedia_years(
    title: str, client: httpx.Client | None = None, skip_sections: list[str] | tuple[str, ...] = ()
) -> dict[int, WikiYear]:
    return parse_wikitext(fetch_wikitext(title, client), skip_sections)


# --- Text bereinigen ---------------------------------------------------------------------------

_REF_SELF = re.compile(r"<ref[^>]*/>", re.I)
_REF = re.compile(r"<ref[^>]*>.*?</ref>", re.I | re.S)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_STRIKE = re.compile(r"<(s|del|strike)>.*?</\1>", re.I | re.S)
_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
_WIKILINK = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]")
_EXTLINK = re.compile(r"\[https?://\S+(?:\s+([^\]]*))?\]")
_BR = re.compile(r"<br\s*/?>", re.I)
_TAG = re.compile(r"</?[a-zA-Z][^>]*>")

MONTHS = {
    "januar": 1, "jan": 1, "februar": 2, "feb": 2, "märz": 3, "maerz": 3, "mär": 3, "april": 4, "apr": 4,
    "mai": 5, "juni": 6, "jun": 6, "juli": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9,
    "sept": 9, "oktober": 10, "okt": 10, "november": 11, "nov": 11, "dezember": 12, "dez": 12,
}  # fmt: skip


def clean_text(s: str) -> str:
    """Entfernt Refs, Vorlagen, Strike-Text, Wikilinks (`[[Ziel|Text]]` -> Text) und HTML.

    `<br>` wird zu Zeilenumbruch; sonst bleibt die Zeilenstruktur erhalten.
    """
    s = _COMMENT.sub("", s)
    s = _REF_SELF.sub("", s)
    s = _REF.sub("", s)
    s = _STRIKE.sub("", s)
    prev = None
    while prev != s:  # verschachtelte Vorlagen von innen nach außen
        prev = s
        s = _TEMPLATE.sub("", s)
    s = _WIKILINK.sub(r"\1", s)
    s = _EXTLINK.sub(lambda m: m.group(1) or "", s)
    s = _BR.sub("\n", s)
    s = _TAG.sub("", s)
    s = s.replace("'''", "").replace("''", "")
    s = html.unescape(s).replace("\xa0", " ").replace("­", "")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def one_line(s: str) -> str:
    return " ".join(clean_text(s).split())


def parse_number(text: str) -> float | None:
    """Erste Zahl im deutschen Format ("75.000", "231,50", "199")."""
    m = re.search(r"\d[\d.]*(?:,\d+)?", text)
    if not m:
        return None
    s = m.group(0).rstrip(".")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    return float(s)


def parse_visitors(text: str) -> int | None:
    t = one_line(text).lower()
    if not t or "abgesagt" in t or "erwartet" in t or "ausgefallen" in t:
        return None
    n = parse_number(t)
    return int(n) if n and n >= 100 else None


def parse_price(text: str, plain: bool = False) -> float | None:
    """Preis in EUR. "ab 199 €" -> 199, "159 bis 199 €" -> 159 (Minimum), "VVK: 231,50 € + ..." -> 231.5.

    `plain`: Spalte ist bereits als EUR deklariert (Zahl ohne Währungszeichen).
    """
    t = one_line(text)
    if not t:
        return None
    if "€" in t or "eur" in t.lower():
        m = re.search(r"\(\s*([\d.,]+\s*€)", t)
        if "DM" in t and m:
            t = m.group(1)  # "59 DM (30,17 €)"
        return parse_number(t)
    return parse_number(t) if plain else None


def parse_band_count(text: str) -> int | None:
    t = one_line(text)
    m = re.search(r"(\d+)\s*Bands?\b", t) or re.fullmatch(r"\s*(\d+)\s*", t)
    return int(m.group(1)) if m else None


def parse_date_range(text: str, year_hint: int | None = None) -> tuple[date, date] | None:
    """"21. bis 23. Juni", "5.–7. Juni 2026", "21. – 23.06.2024", "17.+18. Juli 2020", "20./21. Juni"."""
    t = one_line(text)
    if not t or len(t) > 80:  # lange Zellen sind Fließtext/Anmerkungen
        return None
    sep = r"(?:[–—-]|\+|/|bis|&|und)"
    cands: list[tuple[int, int, int, int, str | None]] = []  # d1, m1, d2, m2, jahr
    for m in re.finditer(rf"(\d{{1,2}})\.\s*(?:(\d{{1,2}})\.)?\s*{sep}\s*(\d{{1,2}})\.(\d{{1,2}})\.(\d{{4}})?", t):
        d1, m1, d2, m2, y = m.groups()
        cands.append((int(d1), int(m1 or m2), int(d2), int(m2), y))
    word = r"([A-Za-zÄÖÜäöüß]{3,9})"
    for m in re.finditer(rf"(\d{{1,2}})\.\s*(?:{word}\.?\s*)?{sep}\s*(\d{{1,2}})\.\s*{word}\.?(?:\s+(\d{{4}}))?", t):
        d1, mo1, d2, mo2, y = m.groups()
        mm2 = MONTHS.get(mo2.lower())
        mm1 = MONTHS.get(mo1.lower()) if mo1 else mm2
        if mm1 and mm2:
            cands.append((int(d1), mm1, int(d2), mm2, y))
    if not cands:
        for m in re.finditer(rf"(\d{{1,2}})\.\s*{word}\.?(?:\s+(\d{{4}}))?", t):
            d, mo, y = m.groups()
            mm = MONTHS.get(mo.lower())
            if mm:
                cands.append((int(d), mm, int(d), mm, y))
    for d1, m1, d2, m2, y in cands:
        year = int(y) if y else year_hint
        if year is None or (year_hint and year != year_hint):
            continue
        try:
            start, end = date(year, m1, d1), date(year, m2, d2)
        except ValueError:
            continue
        if end >= start:
            return start, end
    return None


# --- Namenslisten ------------------------------------------------------------------------------


def _clean_name(n: str) -> str | None:
    n = re.sub(r"^u\.\s?a\.\s*", "", n.strip(), flags=re.I)
    n = n.strip(" .;")
    n = re.sub(r"\s*\([^)]*\)\s*$", "", n).strip()  # "Casper (Rapper)", "Leoniden (Überraschungsauftritt)"
    return n if n and len(n) <= 80 and n not in {"…", "-", "–"} else None


def split_names(line: str, allow_und: bool = False) -> list[str]:
    """Komma-getrennte, bereits bereinigte Zeile -> Namen. `allow_und`: letztes "A und B" trennen."""
    parts = [p for p in re.split(r",\s*", line) if p.strip()]
    if allow_und and len(parts) >= 2 and " und " in parts[-1]:
        last = parts.pop().rstrip(". ")
        a, _, b = last.partition(" und ")
        parts += [a, b]
    names = [c for p in parts if (c := _clean_name(p))]
    return list(dict.fromkeys(names))


def _headliner_label(label: str) -> bool:
    return label.lower().startswith(("headliner", "co-headliner", "co headliner"))


def _names_from_cell(raw: str) -> tuple[list[str], list[str]]:
    """Künstlerzelle ohne Label -> (Headliner, übrige). Kennt "Headliner: A, B und C" + Folgezeile."""
    # Southside 2023: erster Satz = Headliner, dann ". [[Rest]]"
    split = re.split(r"\]\]\.\s+\[\[", raw, maxsplit=1)
    if len(split) == 2 and split[0].count("[[") <= 12 and "Headliner" not in split[0]:
        return split_names(one_line(split[0] + "]]"), True), split_names(one_line("[[" + split[1]).split("\n")[0])
    lines = clean_text(raw).split("\n")
    if not lines:
        return [], []
    m = re.match(r"headliner\w*:\s*(.*)", lines[0], re.I)
    if m:
        rest = split_names(lines[1]) if len(lines) > 1 else []
        return split_names(m.group(1), True), rest
    return [], split_names(lines[0])


# --- Wikitabellen ------------------------------------------------------------------------------


@dataclass
class _Cell:
    text: str
    header: bool
    rowspan: int = 1
    colspan: int = 1


@dataclass
class Table:
    header: list[str]  # bereinigt, klein geschrieben, nach colspan-Expansion
    rows: list[list[str]]  # roher Wikitext je Zelle, nach row-/colspan-Expansion


def _split_top(s: str, seps: tuple[str, ...]) -> list[str]:
    """Trennt an `seps`, aber nicht innerhalb von [[…]] und {{…}}."""
    out, depth, i, start = [], 0, 0, 0
    while i < len(s):
        two = s[i : i + 2]
        if two in ("[[", "{{"):
            depth += 1
            i += 2
        elif two in ("]]", "}}"):
            depth = max(0, depth - 1)
            i += 2
        else:
            hit = next((sp for sp in seps if depth == 0 and s.startswith(sp, i)), None)
            if hit:
                out.append(s[start:i])
                i += len(hit)
                start = i
            else:
                i += 1
    out.append(s[start:])
    return out


def _make_cell(part: str, header: bool) -> _Cell:
    pieces = _split_top(part, ("|",))
    attrs = ""
    if len(pieces) > 1 and "=" in pieces[0] and "[[" not in pieces[0]:
        attrs, part = pieces[0], "|".join(pieces[1:])
    rs = re.search(r"rowspan\s*=\s*[\"']?(\d+)", attrs)
    cs = re.search(r"colspan\s*=\s*[\"']?(\d+)", attrs)
    return _Cell(part.strip(), header, int(rs.group(1)) if rs else 1, int(cs.group(1)) if cs else 1)


def _unbalanced(s: str) -> bool:
    return s.count("{{") > s.count("}}") or s.count("[[") > s.count("]]")


def _table_bodies(text: str) -> list[list[str]]:
    """Zeilen aller Tabellen der obersten Ebene (verschachtelte Tabellen bleiben im Inhalt)."""
    bodies: list[list[str]] = []
    depth = 0
    cur: list[str] = []
    for line in text.split("\n"):
        st = line.lstrip()
        if st.startswith("{|"):
            depth += 1
            if depth == 1:
                cur = []
                continue
        elif st.startswith("|}") and depth:
            depth -= 1
            if depth == 0:
                bodies.append(cur)
                continue
        if depth:
            cur.append(line)
    return bodies


def _parse_body(lines: list[str]) -> list[list[_Cell]]:
    rows: list[list[_Cell]] = []
    row: list[_Cell] = []
    cell: _Cell | None = None
    nest = 0
    for line in lines:
        st = line.strip()
        if nest or (cell and _unbalanced(cell.text)):
            if st.startswith("{|"):
                nest += 1
            elif st.startswith("|}") and nest:
                nest -= 1
            if cell:
                cell.text += "\n" + line
            continue
        if st.startswith("{|"):
            nest += 1
            if cell:
                cell.text += "\n" + line
        elif st.startswith("|-"):
            if row:
                rows.append(row)
            row, cell = [], None
        elif st.startswith("|+") or st.startswith("|}"):
            continue
        elif st[:1] in ("|", "!"):
            header = st[0] == "!"
            seps = ("!!", "||") if header else ("||",)
            for part in _split_top(st[1:], seps):
                cell = _make_cell(part, header)
                row.append(cell)
        elif cell is not None:
            cell.text += "\n" + line
    if row:
        rows.append(row)
    return rows


def _expand(rows: list[list[_Cell]]) -> list[list[tuple[str, bool]]]:
    """Löst rowspan/colspan in ein rechteckiges Raster (Text, ist_header) auf."""
    grid: list[list[tuple[str, bool]]] = []
    pending: dict[int, list] = {}
    for row in rows:
        out: list[tuple[str, bool]] = []
        col = idx = 0
        while idx < len(row) or col in pending:
            if col in pending:
                rem, txt, hd = pending[col]
                out.append((txt, hd))
                if rem <= 1:
                    del pending[col]
                else:
                    pending[col][0] = rem - 1
                col += 1
                continue
            c = row[idx]
            idx += 1
            for _ in range(c.colspan):
                out.append((c.text, c.header))
                if c.rowspan > 1:
                    pending[col] = [c.rowspan - 1, c.text, c.header]
                col += 1
        grid.append(out)
    return grid


def parse_wikitables(text: str) -> list[Table]:
    tables = []
    for body in _table_bodies(text):
        grid = _expand(_parse_body(body))
        header: list[str] = []
        data: list[list[str]] = []
        for r in grid:
            if not header and r and all(h for _, h in r):
                header = [one_line(t).lower() for t, _ in r]
            elif r:
                data.append([t for t, _ in r])
        tables.append(Table(header, data))
    return tables


def _cols(header: list[str], pred) -> list[int]:  # noqa: ANN001
    return [i for i, h in enumerate(header) if pred(h)]


def _cell(row: list[str], i: int | None) -> str:
    return row[i] if i is not None and i < len(row) else ""


def interpret_table(table: Table) -> list[WikiYear]:
    h = table.header
    if not h:
        return []
    year_col = next(iter(_cols(h, lambda x: x == "jahr")), None)
    date_col = next(iter(_cols(h, lambda x: x.startswith("datum"))), None)
    if year_col is None and date_col is None:
        return []
    vis_p = _cols(h, lambda x: "teilnehmer" in x or "gesamtbesucher" in x)
    vis_s = _cols(h, lambda x: "besucher" in x and "täglich" not in x and "gesamt" not in x)
    price_cols = _cols(h, lambda x: "preis" in x and "dm" not in x)
    price_plain = [i for i in price_cols if "eur" in h[i]]
    band_cols = _cols(h, lambda x: x in ("bands", "sonstiges") or x.startswith("anzahl bands"))
    head_cols = _cols(h, lambda x: x == "headliner")
    name_cols = _cols(h, lambda x: x in ("künstler", "line-up", "lineup"))
    key_col = year_col if year_col is not None else date_col

    # Zeilen mit identischer Schlüsselzelle (rowspan) zu einer Ausgabe gruppieren
    groups: list[list[list[str]]] = []
    for r in table.rows:
        key = one_line(_cell(r, key_col))
        if groups and key and key == one_line(_cell(groups[-1][0], key_col)):
            groups[-1].append(r)
        else:
            groups.append([r])

    result: list[WikiYear] = []
    for g in groups:
        r0 = g[0]
        key_txt = one_line(_cell(r0, key_col))
        dates: tuple[date, date] | None = None
        year: int | None = None
        if year_col is not None:
            m = re.match(r"(\d{4})\b", key_txt)
            year = int(m.group(1)) if m else None
            if year and date_col is not None:
                dates = parse_date_range(_cell(r0, date_col), year)
        else:
            dates = parse_date_range(key_txt)
            if dates:
                year = dates[0].year
            elif re.fullmatch(r"\d{4}", key_txt):
                year = int(key_txt)
        if year is None:
            continue
        wy = WikiYear(year)
        if dates:
            wy.start_date, wy.end_date = dates
        status_cols = {key_col, date_col, *vis_p, *vis_s} - {None}
        status = " ".join(one_line(_cell(r0, i)).lower() for i in status_cols)
        wy.cancelled = "abgesagt" in status or "ausgefallen" in status
        for i in vis_p + vis_s:
            wy.visitors = parse_visitors(_cell(r0, i))
            if wy.visitors:
                break
        for i in price_cols:
            wy.price_eur = parse_price(_cell(r0, i), plain=i in price_plain)
            if wy.price_eur:
                break
        for i in band_cols:
            wy.reported_act_count = parse_band_count(_cell(r0, i))
            if wy.reported_act_count:
                break
        for r in g:
            for i in head_cols:
                first = (clean_text(_cell(r, i)).split("\n") or [""])[0]
                if not re.match(r"geplant|—|–|-$", first, re.I):
                    wy.headliners += [n for n in split_names(first) if n not in wy.headliners]
            if name_cols:
                txts = [_cell(r, i) for i in name_cols]
                single = len(txts) == 1 or txts[0] == txts[-1]
                label, raw = ("", txts[0]) if single else (one_line(txts[0]), txts[-1])
                if label:
                    names = split_names((clean_text(raw).split("\n") or [""])[0])
                    if _headliner_label(label):
                        wy.headliners += [n for n in names if n not in wy.headliners]
                    wy.lineup += names
                else:
                    heads, rest = _names_from_cell(raw)
                    wy.headliners += [n for n in heads if n not in wy.headliners]
                    wy.lineup += heads + rest
        wy.lineup = list(dict.fromkeys(wy.lineup + wy.headliners))
        result.append(wy)
    return result


# --- Klappleisten und Jahresabschnitte ------------------------------------------------------------

_KLAPP = re.compile(
    r"\{\{Klappleiste/Anfang\s*\|\s*TITEL\s*=\s*(\d{4})\s*\}\}(.*?)\{\{Klappleiste/Ende\}\}", re.S | re.I
)


def parse_klappleisten(text: str) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for m in _KLAPP.finditer(text):
        names = split_names(" ".join(clean_text(m.group(2)).split("\n")))
        if names:
            out[int(m.group(1))] = names
    return out


_HEADING = re.compile(r"^(={2,6})\s*(.*?)\s*\1\s*$", re.M)


def parse_year_sections(text: str) -> list[WikiYear]:
    """Abschnitte "=== 2024 ===": Termin aus "vom 18. bis 21. Juli", Namen aus Aufzählungen (`* [[X]]`)."""
    heads = list(_HEADING.finditer(text))
    out = []
    for i, h in enumerate(heads):
        if not re.fullmatch(r"\d{4}", h.group(2)):
            continue
        year = int(h.group(2))
        body = text[h.end() : heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        wy = WikiYear(year)
        snippet = re.search(
            r"vom\s+(\d{1,2}\.\s*(?:[A-Za-zäöü]+\s+)?bis\s+\d{1,2}\.\s*[A-Za-zäöü]+(?:\s+\d{4})?)",
            one_line(body[:1500]),
        )
        if snippet:
            dates = parse_date_range(snippet.group(1), year)
            if dates:
                wy.start_date, wy.end_date = dates
        bullets = [clean_text(m.group(1)) for m in re.finditer(r"^\*\s*(.+)$", body, re.M)]
        names = [n for b in bullets for n in split_names(b.split("\n")[0])]
        if len(names) >= 3:
            wy.lineup = list(dict.fromkeys(names))
        if wy.start_date or wy.lineup:
            out.append(wy)
    return out


# --- Zusammenführen -------------------------------------------------------------------------------


def _merge(years: dict[int, WikiYear], new: WikiYear) -> None:
    cur = years.get(new.year)
    if cur is None:
        years[new.year] = new
        return
    for f in ("start_date", "end_date", "visitors", "price_eur", "reported_act_count"):
        if getattr(cur, f) is None:
            setattr(cur, f, getattr(new, f))
    if not cur.headliners:
        cur.headliners = new.headliners
    if len(new.lineup) > len(cur.lineup):
        cur.lineup = new.lineup
    cur.cancelled = cur.cancelled or new.cancelled


def drop_sections(text: str, names: list[str] | tuple[str, ...]) -> str:
    """Entfernt Abschnitte (samt Unterabschnitten) mit diesen Überschriften, z. B. Nebenveranstaltungen."""
    for name in names:
        m = re.search(rf"^(={{2,6}})\s*{re.escape(name)}\s*\1\s*$", text, re.M)
        if not m:
            continue
        level = len(m.group(1))
        nxt = re.compile(rf"^={{2,{level}}}[^=].*$", re.M).search(text, m.end())
        text = text[: m.start()] + (text[nxt.start() :] if nxt else "")
    return text


def parse_wikitext(text: str, skip_sections: list[str] | tuple[str, ...] = ()) -> dict[int, WikiYear]:
    """Alle Jahre, zu denen im Artikel irgendetwas Verwertbares steht (ausgefallene Jahre markiert).

    `skip_sections`: Überschriften, deren Inhalt ignoriert wird (andere Veranstaltungen im Artikel).
    """
    text = drop_sections(text, skip_sections)
    years: dict[int, WikiYear] = {}
    for table in parse_wikitables(text):
        for wy in interpret_table(table):
            _merge(years, wy)
    for year, names in parse_klappleisten(text).items():
        _merge(years, WikiYear(year, lineup=names))
    for wy in parse_year_sections(text):
        _merge(years, wy)
    return years
