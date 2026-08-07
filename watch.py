#!/usr/bin/env python3
"""Hlídá rozpis Cinema City a hlásí nově vypsaná představení.

Ve výchozím nastavení: film "Odyssea" v sále, jehož název obsahuje "IMAX".
Data bere z veřejného JSON API cinemacity.cz (bez klíče, bez přihlášení).

Kromě nových termínů hlídá i to, kdy se u známého představení uvolní místo
v zadních řadách — u vyprodaných projekcí je to jediná šance, jak se dostat
dál od plátna než do prvních dvou řad.

Stav (už viděná představení a jejich zadní řady) drží v JSON souboru, takže
při každém běhu hlásí jen to, co se změnilo od minule.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

SITE_ID = "10101"  # cinemacity.cz
BASE = f"https://www.cinemacity.cz/cz/data-api-service/v1/quickbook/{SITE_ID}"
LANG = "cs_CZ"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

FILM_PATTERN = os.environ.get("FILM_PATTERN", "odyss").lower()
AUDITORIUM_PATTERN = os.environ.get("AUDITORIUM_PATTERN", "imax").lower()
HORIZON_DAYS = int(os.environ.get("HORIZON_DAYS", "180"))
# Atribut, podle kterého API umí filtrovat kina — levná nápověda, kde hledat
# IMAX sály. Doplňuje (nenahrazuje) sondu podle názvu sálu.
HINT_ATTR = os.environ.get("HINT_ATTR", "70-mm")
DELAY = float(os.environ.get("REQUEST_DELAY", "0.25"))

# Ticketing API (jiný host i jiný tvar dat než rozpis) — odtud se zjišťuje,
# která konkrétní sedadla jsou volná.
TICKETS = "https://tickets.cinemacity.cz/api"
# Od které řady se místo počítá jako "vzadu". Řada 1 je nejblíž plátnu.
BACK_ROW_MIN = int(os.environ.get("BACK_ROW_MIN", "3"))
CHECK_SEATS = os.environ.get("CHECK_SEATS", "1").lower() not in ("0", "false", "no", "")
# Ticketing API odmítá požadavky bez hlavičky "uuid" chybou 403. Frontend do ní
# posílá hodnotu ze stejnojmenné cookie, serveru ale stačí jakékoli platné UUID
# — nemusí odpovídat žádné existující session, takže si ho vyrobíme sami a
# nemusíme kvůli tomu chodit pro cookie na objednávkovou stránku.
SESSION_UUID = str(uuid.uuid4())

CZ_DAYS = ["po", "út", "st", "čt", "pá", "so", "ne"]

# API vrací eventDateTime bez zóny, v místním čase kina. Runner v GitHub
# Actions jede v UTC, takže by se čas představení porovnával s časem o dvě
# hodiny pozadu — projekce, která právě doběhla, by vypadala jako budoucí
# a při zmizení z rozpisu by se falešně nahlásila jako zrušená.
CINEMA_TZ = ZoneInfo("Europe/Prague")


def now():
    """Aktuální čas v zóně kina, bez tzinfo — porovnatelný s daty z API."""
    return datetime.now(CINEMA_TZ).replace(tzinfo=None)


def api(path):
    """GET na data-api-service; vrací obsah klíče "body"."""
    url = f"{BASE}{path}"
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))["body"]
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(2 ** attempt)
    raise SystemExit(f"API selhalo po 4 pokusech: {url}\n{last}")


def horizon():
    return (date.today() + timedelta(days=HORIZON_DAYS)).isoformat()


def fetch_cinemas():
    body = api(f"/cinemas/with-event/until/{horizon()}?attr=&lang={LANG}")
    return {c["id"]: c for c in body["cinemas"]}


def fetch_dates(cinema_id):
    time.sleep(DELAY)
    return api(f"/dates/in-cinema/{cinema_id}/until/{horizon()}?attr=&lang={LANG}")["dates"]


def fetch_day(cinema_id, day):
    time.sleep(DELAY)
    body = api(f"/film-events/in-cinema/{cinema_id}/at-date/{day}?attr=&lang={LANG}")
    films = {f["id"]: f for f in body.get("films", [])}
    return films, body.get("events", [])


def hint_cinema_ids():
    """Kina, která podle API mají představení s atributem HINT_ATTR."""
    if not HINT_ATTR:
        return set()
    body = api(f"/cinemas/with-event/until/{horizon()}?attr={HINT_ATTR}&lang={LANG}")
    return {c["id"] for c in body["cinemas"]}


def is_target_hall(event):
    return AUDITORIUM_PATTERN in (event.get("auditorium") or "").lower()


def collect():
    """Projde relevantní kina a vrátí {event_id: záznam} pro hlídaná představení.

    Aby se netahal celý rozpis všech kin, běží to dvoufázově: nejdřív se
    zjistí, která kina vůbec mají hlídaný sál (jedna sonda na kino + nápověda
    z API), a teprve ta se projdou do hloubky.
    """
    cinemas = fetch_cinemas()
    dates_by_cinema = {cid: fetch_dates(cid) for cid in cinemas}

    candidates = hint_cinema_ids() & set(cinemas)
    day_cache = {}
    for cid, days in dates_by_cinema.items():
        if not days:
            continue
        probe = days[0]
        day_cache[(cid, probe)] = fetch_day(cid, probe)
        if any(is_target_hall(e) for e in day_cache[(cid, probe)][1]):
            candidates.add(cid)

    found = {}
    for cid in sorted(candidates):
        for day in dates_by_cinema.get(cid, []):
            films, events = day_cache.get((cid, day)) or fetch_day(cid, day)
            for e in events:
                film = films.get(e["filmId"], {})
                if FILM_PATTERN not in film.get("name", "").lower():
                    continue
                if not is_target_hall(e):
                    continue
                found[e["id"]] = {
                    "id": e["id"],
                    "film": film.get("name", e["filmId"]),
                    "filmLink": film.get("link"),
                    "cinema": cinemas[cid]["displayName"],
                    "cinemaId": cid,
                    "datetime": e["eventDateTime"],
                    "auditorium": e.get("auditorium"),
                    "attrs": e.get("attributeIds", []),
                    # Žádné z polí, která API nabízí, není použitelné jako
                    # odkaz: bookingLink vrací na GET 404, obsoleteBookingUrl
                    # je i podle názvu mrtvý a bookingRouterLaunchLink vede na
                    # stránku se samoodesílacím POST formulářem, jehož cíl
                    # (tickets.rel.…) na přímý GET odpoví 403. Ten POST ale
                    # skončí na prosté adrese /order/{id}, která funguje i na
                    # GET a otevře rovnou výběr sedadel. Pozor, parametr lang
                    # tady dělá 404 — musí se vynechat.
                    # Pod tímhle kódem zná představení ticketing — platí jak pro
                    # odkaz na nákup, tak pro dotaz na obsazenost sedadel.
                    "presentation": str(e.get("presentationCode") or e["id"]),
                    "booking": f"https://tickets.cinemacity.cz/order/{e.get('presentationCode') or e['id']}",
                    "soldOut": bool(e.get("soldOut")),
                }
    return found


def tickets_api(path, post=False):
    """Dotaz na ticketing API. Vrací dekódované tělo, nebo None při chybě.

    Na rozdíl od api() tady výpadek nesmí shodit celý běh — místa jsou jen
    doplňková informace a rozpis se má nahlásit i tehdy, když ticketing zlobí.
    """
    url = f"{TICKETS}{path}"
    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "uuid": SESSION_UUID,
    }
    data = None
    if post:
        data = b"{}"
        headers["Content-Type"] = "application/json"
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            time.sleep(2 ** attempt)
    print(f"  ticketing API selhalo: {url} ({last})", file=sys.stderr)
    return None


_SEATPLAN_CACHE = {}


def seat_labels(venue_id, seatplan_id):
    """Popisky sedadel sálu: {(sekce, index sedadla, index řady): (řada, sedadlo, vozíčkář)}.

    Plán sálu je na všech představeních ve stejném sále totožný, takže se tahá
    jednou za běh — jinak by to byl jeden request navíc na každý termín.
    """
    key = (venue_id, seatplan_id)
    if key in _SEATPLAN_CACHE:
        return _SEATPLAN_CACHE[key]

    time.sleep(DELAY)
    body = tickets_api(f"/seats/seatplanV2?venueId={venue_id}&seatplanId={seatplan_id}", post=True)
    labels = {}
    for sec_id, section in (body or {}).get("S", {}).items():
        for group in section.get("G", {}).values():
            for row_idx, row in group.get("R", {}).items():
                for seat_idx, seat in row.get("S", {}).items():
                    labels[(sec_id, seat_idx, row_idx)] = (
                        row.get("n"),
                        seat.get("n"),
                        bool(seat.get("hc")),
                    )
    # Prázdný výsledek se schválně necachuje: kdyby plán sálu jednou selhal,
    # zablokovalo by to kontrolu míst u všech dalších termínů ve stejném sále.
    if labels:
        _SEATPLAN_CACHE[key] = labels
    return labels


def row_number(label, fallback):
    """Číslo řady pro porovnání s BACK_ROW_MIN; popisek nemusí být číslo."""
    try:
        return int(str(label).strip())
    except (TypeError, ValueError):
        try:
            return int(fallback)
        except (TypeError, ValueError):
            return 0


def seat_sort_key(seat):
    try:
        return (0, int(seat))
    except (TypeError, ValueError):
        return (1, str(seat))


def seat_report(presentation_id):
    """Rozbor volných míst pro jedno představení, nebo None když se nedá zjistit.

    Pozor na dvě pasti: příznak soldOut z rozpisového API je nespolehlivý
    (zůstává na 0 i u prakticky vyprodaných projekcí), a seats-statusV2 vrací
    místa VOLNÁ, ne obsazená — co v odpovědi není, je prodané.
    """
    body = tickets_api(f"/presentations/{presentation_id}?referralMiniSiteId=0")
    if not body or "presentation" not in body:
        # Typicky {"error": {"error": "TICKETING_ENDED"}} u projekce, na kterou
        # se už neprodává. Není to chyba, jen se nedá nic zjistit.
        return None
    pres = body["presentation"]

    time.sleep(DELAY)
    status = tickets_api(
        f"/seats/seats-statusV2?presentationId={presentation_id}"
        f"&venueTypeId={pres.get('venueTypeId')}"
        f"&isReserved={1 if pres.get('isReserved') else 0}"
    )
    if not status or "seats" not in status:
        return None

    labels = seat_labels(pres.get("venueId"), pres.get("seatplanId"))
    if not labels:
        return None

    free = []
    for key in status["seats"]:
        parts = key.split("_")
        if len(parts) != 3:
            continue
        label = labels.get(tuple(parts))
        if not label:
            continue
        row_label, seat_label, is_hc = label
        free.append(
            {
                "row": row_number(row_label, parts[2]),
                "rowLabel": row_label,
                "seat": seat_label,
                "hc": is_hc,
            }
        )

    # Vozíčkářská místa se nepočítají — jsou to vyhrazené pozice, ne sedadlo,
    # které by si člověk mohl jen tak koupit.
    back = sorted(
        (s for s in free if s["row"] >= BACK_ROW_MIN and not s["hc"]),
        key=lambda s: (s["row"], seat_sort_key(s["seat"])),
    )
    return {
        "free": len(free),
        "back": back,
        "backRows": sorted({s["row"] for s in back}),
    }


def check_seats(current, known):
    """Doplní představením seznam zadních řad s volnými místy.

    Vrací {event_id: rozbor} pro živé hlášení; do stavu se ukládá jen strohý
    seznam řad (klíč backRows), aby soubor nebobtnal a neměnil se při každém
    prodaném sedadle.

    Když se místa nepodaří zjistit, převezme se poslední známá hodnota. Kdyby
    se totiž výpadek zapsal jako "žádná volná místa", vypadalo by následující
    úspěšné čtení jako čerstvé uvolnění a přišlo by falešné hlášení.
    """
    live = {}
    moment = now().isoformat()
    for eid, event in sorted(current.items(), key=lambda kv: kv[1]["datetime"]):
        previous = known.get(eid, {}).get("backRows")
        if event["datetime"] < moment:
            event["backRows"] = previous if previous is not None else []
            continue
        info = seat_report(event.get("presentation") or event["id"])
        if info is None:
            event["backRows"] = previous if previous is not None else []
            continue
        live[eid] = info
        event["backRows"] = info["backRows"]
    return live


def newly_opened(current, known, live):
    """Představení, kde od minule přibyla zadní řada s volným místem."""
    opened = []
    for eid, event in current.items():
        before = known.get(eid, {}).get("backRows")
        if before is None:
            # Buď úplně nový termín (hlásí se zvlášť), nebo záznam ze stavu
            # pořízeného ještě před hlídáním míst — není s čím porovnávat.
            continue
        fresh = sorted(set(event.get("backRows", [])) - set(before))
        if fresh:
            opened.append((event, fresh, live.get(eid)))
    return sorted(opened, key=lambda item: item[0]["datetime"])


def load_state(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {"updated": None, "events": {}}


def signature(events):
    """To ze stavu, na čem stojí hlášení — jen kvůli tomu se soubor přepisuje."""
    return {eid: list(e.get("backRows", [])) for eid, e in events.items()}


def save_state(path, events):
    """Zapíše stav, ale jen když se změnilo něco, co se hlásí.

    Kdyby se soubor přepisoval při každém běhu, měnilo by se v něm razítko
    "updated" a workflow by si po sobě commitoval prázdnou změnu 48× denně.
    Rozhoduje proto seznam ID plus obsazenost zadních řad — a schválně jen
    seznam řad, ne počty volných sedadel: ty se u živého předprodeje mění
    každou chvíli a stav by se commitoval pořád dokola.
    Ostatní volatilní pole (soldOut) se tím pádem neaktualizují; drží se
    hodnota z chvíle, kdy se představení objevilo poprvé.
    """
    if signature(events) == signature(load_state(path).get("events", {})):
        return False
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload = {
        "updated": now().replace(microsecond=0).isoformat(),
        "events": dict(sorted(events.items(), key=lambda kv: kv[1]["datetime"])),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1, sort_keys=False)
        fh.write("\n")
    return True


def prune_past(events):
    """Zahodí ze stavu představení, která už proběhla — ať soubor neroste."""
    cutoff = (now() - timedelta(days=1)).isoformat()
    return {k: v for k, v in events.items() if v["datetime"] >= cutoff}


def fmt_dt(iso):
    dt = datetime.fromisoformat(iso)
    return f"{CZ_DAYS[dt.weekday()]} {dt.day}. {dt.month}. {dt.year} v {dt:%H:%M}"


def fmt_short(iso):
    dt = datetime.fromisoformat(iso)
    return f"{dt.day}. {dt.month}."


def fmt_back(back):
    """Volná zadní místa jako "řada 8: 3, 4 · řada 9: 12"."""
    by_row = {}
    for seat in back:
        by_row.setdefault(seat["rowLabel"], []).append(seat["seat"])
    return " · ".join(f"řada {row}: {', '.join(seats)}" for row, seats in by_row.items())


def render(new_events, gone_events, opened=(), live=None):
    """Markdown tělo hlášení."""
    live = live or {}
    lines = []
    if opened:
        lines.append(f"### Uvolnila se místa vzadu ({len(opened)})\n")
        for event, fresh, info in opened:
            rows = ", ".join(str(r) for r in fresh)
            lines.append(f"**{fmt_dt(event['datetime'])}** · {event['cinema']} · {event['auditorium']}\n")
            lines.append(f"- nově volno v řadě {rows}")
            if info and info["back"]:
                lines.append(f"- celkem vzadu: {fmt_back(info['back'])}")
            if event["booking"]:
                lines.append(f"- [koupit]({event['booking']})")
            lines.append("")
    if new_events:
        lines.append(f"### Nově vypsáno ({len(new_events)})\n")
        for cinema, group in group_by_cinema(new_events):
            lines.append(f"**{cinema}**\n")
            for e in group:
                flags = []
                if "70-mm" in e["attrs"]:
                    flags.append("70mm")
                if "subbed" in e["attrs"]:
                    flags.append("titulky")
                if "dubbed" in e["attrs"]:
                    flags.append("dabing")
                if e["soldOut"]:
                    flags.append("**vyprodáno**")
                suffix = f" — {', '.join(flags)}" if flags else ""
                link = f" — [koupit]({e['booking']})" if e["booking"] else ""
                lines.append(f"- {fmt_dt(e['datetime'])} · {e['auditorium']}{suffix}{link}")
                info = live.get(e["id"])
                if info is not None:
                    detail = fmt_back(info["back"]) if info["back"] else "vzadu nic volného"
                    lines.append(f"  - {detail}")
            lines.append("")
    if gone_events:
        lines.append(f"### Zmizelo z rozpisu ({len(gone_events)})\n")
        for cinema, group in group_by_cinema(gone_events):
            lines.append(f"**{cinema}**\n")
            for e in group:
                lines.append(f"- {fmt_dt(e['datetime'])} · {e['auditorium']}")
            lines.append("")
    candidates = list(new_events) + list(gone_events) + [event for event, _, _ in opened]
    film_link = next((e["filmLink"] for e in candidates if e.get("filmLink")), None)
    if film_link:
        lines.append(f"[Stránka filmu na Cinema City]({film_link})")
    lines.append("")
    lines.append(
        f"<sub>Zkontrolováno {now():%d. %m. %Y %H:%M} · "
        f"film ~ `{FILM_PATTERN}` · sál ~ `{AUDITORIUM_PATTERN}` · "
        f"vzadu = řada {BACK_ROW_MIN} a dál</sub>"
    )
    return "\n".join(lines)


def group_by_cinema(events):
    order = {}
    for e in sorted(events, key=lambda x: (x["cinema"], x["datetime"])):
        order.setdefault(e["cinema"], []).append(e)
    return order.items()


def span_of(events):
    days = sorted({e["datetime"][:10] for e in events})
    span = fmt_short(days[0])
    if len(days) > 1:
        span += f"–{fmt_short(days[-1])}"
    return span


def title_for(new_events, opened=()):
    if new_events:
        n = len(new_events)
        word = "nový termín" if n == 1 else ("nové termíny" if n < 5 else "nových termínů")
        return f"🎬 {new_events[0]['film']} v IMAXu: {n} {word} ({span_of(new_events)})"
    events = [event for event, _, _ in opened]
    n = len(events)
    word = "termínu" if n == 1 else ("termínů" if n < 5 else "termínů")
    return f"🎟️ {events[0]['film']} v IMAXu: volná místa vzadu u {n} {word} ({span_of(events)})"


def gh_output(**kwargs):
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for key, value in kwargs.items():
            fh.write(f"{key}={value}\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--state", default="state/seen.json", help="soubor se stavem")
    ap.add_argument("--seed", action="store_true", help="jen ulož stav, nic nehlas")
    ap.add_argument("--force-report", action="store_true", help="nahlas vše, i známé")
    ap.add_argument("--report", default="report.md", help="kam zapsat markdown hlášení")
    ap.add_argument("--title", default="title.txt", help="kam zapsat titulek issue")
    args = ap.parse_args()

    current = collect()
    state = load_state(args.state)
    known = state.get("events", {})

    print(f"Nalezeno {len(current)} hlídaných představení, ve stavu {len(known)}.")

    live = check_seats(current, known) if CHECK_SEATS else {}
    if live:
        with_back = sum(1 for info in live.values() if info["back"])
        print(f"Obsazenost ověřena u {len(live)}, volno vzadu u {with_back}.")

    if args.seed:
        save_state(args.state, prune_past(current))
        print(f"Stav zapsán do {args.state} (seed, nic se nehlásí).")
        gh_output(has_news="false")
        return

    if args.force_report:
        new_events = sorted(current.values(), key=lambda e: e["datetime"])
        gone = []
        opened = []
    else:
        new_events = sorted(
            (v for k, v in current.items() if k not in known),
            key=lambda e: e["datetime"],
        )
        future = now().isoformat()
        gone = sorted(
            (v for k, v in known.items() if k not in current and v["datetime"] > future),
            key=lambda e: e["datetime"],
        )
        opened = newly_opened(current, known, live)

    save_state(args.state, prune_past(current))

    if not new_events and not gone and not opened:
        print("Nic nového.")
        gh_output(has_news="false")
        return

    body = render(new_events, gone, opened, live)
    if new_events or opened:
        title = title_for(new_events, opened)
    else:
        title = "🎬 Odyssea v IMAXu: zrušené termíny"
    with open(args.report, "w", encoding="utf-8") as fh:
        fh.write(body + "\n")
    with open(args.title, "w", encoding="utf-8") as fh:
        fh.write(title + "\n")

    print(f"\n{title}\n")
    print(body)
    gh_output(has_news="true")


if __name__ == "__main__":
    sys.exit(main())
