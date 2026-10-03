"""The journal's HTML. Every value goes through `e` (escaped); no inline
scripts or styles, so the page's content-security policy can forbid them."""

from __future__ import annotations

import html
import json
from datetime import datetime, timedelta

from . import checklist, svg
from .checklist import AutoChecks
from .rulebook import AUTO_ITEMS, TAPPED_ITEMS, Rulebook

NAV = (
    ("/dashboard", "Dashboard"),
    ("/checklist", "Checklist"),
    ("/trades", "Trades"),
    ("/review", "Review"),
    ("/import", "Import"),
)


def e(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def layout(title: str, body: str, active: str = "") -> str:
    links = "".join(
        f'<a href="{href}"{" aria-current=page" if href == active else ""}>{e(name)}</a>'
        for href, name in NAV
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Journal">
<meta name="color-scheme" content="light dark">
<meta name="referrer" content="same-origin">
<link rel="icon" href="data:,">
<title>{e(title)} · Journal</title>
<link rel="stylesheet" href="/static/app.css">
<script src="/static/app.js" defer></script>
</head>
<body>
<header><span class="brand">Journal</span><nav>{links}</nav></header>
<main>
{body}
</main>
</body>
</html>
"""


def _chip(state: bool | None, words: str) -> str:
    cls, mark = {True: ("ok", "✓"), False: ("red", "✗"), None: ("off", "–")}[state]
    return (
        f'<li class="chip {cls}"><span aria-hidden="true">{mark}</span> {e(words)}</li>'
    )


def _auto_states(rb: Rulebook, auto: AutoChecks) -> dict:
    return {
        "under_max": auto.under_max,
        "under_stop_after": auto.under_stop_after,
        "in_session": auto.in_session,
    }


def _radio(
    name: str, value: str, words: str, values: dict, required: bool = False
) -> str:
    checked = " checked" if values.get(name) == value else ""
    req = " required" if required else ""
    return f'<label><input type="radio" name="{e(name)}" value="{e(value)}"{checked}{req}><span>{e(words)}</span></label>'


def checklist_form(
    rb: Rulebook,
    auto: AutoChecks,
    token: str,
    recent: list[str],
    error: str | None = None,
    values: dict | None = None,
) -> str:
    values = values or {}
    states = _auto_states(rb, auto)
    chips = "".join(_chip(states[i], checklist.label(i, rb, auto)) for i in AUTO_ITEMS)
    auto_red = any(states[i] is False for i in AUTO_ITEMS if i in rb.killer_items)
    if auto.last_import:
        last = datetime.fromisoformat(auto.last_import).strftime("%a %H:%M")
        note = f"Losses count imported trades; last import {e(last)}."
    else:
        note = "Losses count imported trades; nothing imported yet."
    practice = ""
    if rb.placeholder:
        practice = f'<p class="note">Rulebook {e(rb.version)} has placeholder numbers until you set them.</p>'
    err = f'<p class="error" role="alert">{e(error)}</p>' if error else ""

    def question(item: str) -> str:
        words = checklist.label(item, rb) + "?"
        return (
            f'<fieldset class="seg q"><legend>{e(words)}</legend>'
            + _radio(item, "yes", "Yes", values, True)
            + _radio(item, "no", "No", values)
            + "</fieldset>"
        )

    setups = "".join(
        _radio("setup", s, s, values, i == 0) for i, s in enumerate(rb.setups)
    )
    emotions = "".join(_radio("emotion_word", w, w, values) for w in rb.emotion_words)
    other_checked = " checked" if values.get("emotion_word") == "other" else ""
    confidence = "".join(
        _radio("confidence", str(n), str(n), values) for n in range(1, 6)
    )
    options = "".join(f'<option value="{e(a)}">' for a in recent)
    killers = " ".join(rb.killer_items)
    body = f"""
<h1>Before you click</h1>
{practice}
<ul class="chips" aria-label="Checks from the journal">{chips}</ul>
<p class="note">{note}</p>
{err}
<form method="post" action="/checklist" class="checklist" data-checklist data-killers="{e(killers)}" data-auto-red="{1 if auto_red else 0}">
<input type="hidden" name="csrf" value="{e(token)}">
<fieldset class="seg"><legend>Direction</legend>{_radio("direction", "long", "Long", values, True)}{_radio("direction", "short", "Short", values)}</fieldset>
<fieldset class="seg wrap"><legend>Setup</legend>{setups}</fieldset>
{"".join(question(i) for i in TAPPED_ITEMS)}
<fieldset class="seg wrap"><legend>One word for how you feel</legend>{emotions}<label><input type="radio" name="emotion_word" value="other"{other_checked}><span>other</span></label>
<input class="other" type="text" name="emotion_other" maxlength="30" autocomplete="off" placeholder="your word" value="{e(values.get("emotion_other"))}"></fieldset>
<fieldset class="seg five"><legend>Confidence</legend>{confidence}</fieldset>
<label class="field">Asset (optional)<input type="text" name="asset" list="assets" maxlength="30" autocomplete="off" autocapitalize="characters" value="{e(values.get("asset"))}"></label>
<datalist id="assets">{options}</datalist>
<p id="preview" class="preview" aria-live="polite"></p>
<button type="submit">Save checklist</button>
</form>
"""
    return layout("Checklist", body, "/checklist")


def checklist_view(
    rb: Rulebook, row, auto: AutoChecks, answers: dict, token: str
) -> str:
    states = {
        **_auto_states(rb, auto),
        **{k: bool(answers.get(k)) for k in TAPPED_ITEMS},
    }
    items = "".join(
        _chip(
            states[i] if (i in rb.killer_items or i in TAPPED_ITEMS) else None,
            checklist.label(i, rb, auto),
        )
        for i in (*TAPPED_ITEMS, *AUTO_ITEMS)
    )
    filled = datetime.fromisoformat(row["filled_at"])
    verdict = row["verdict"]
    if verdict == "go":
        banner = '<p class="verdict go">Go.</p><p>Take it as planned: the stop and target are in the order.</p>'
    elif verdict == "taken-anyway":
        banner = '<p class="verdict no">Taken anyway.</p><p>The rulebook said no trade, so this trade will grade C.</p>'
    else:
        banner = '<p class="verdict no">Rulebook says no trade.</p><p>If a box is red, don\'t click.</p>'
    choices = ""
    if (
        verdict == "no-trade"
        and row["outcome"] is None
        and row["matched_trade_id"] is None
    ):
        token_field = f'<input type="hidden" name="csrf" value="{e(token)}">'
        choices = f"""
<div class="choices">
<form method="post" action="/checklist/{row["id"]}/skip">{token_field}<button type="submit">Not taking it</button></form>
<form method="post" action="/checklist/{row["id"]}/anyway">{token_field}<button type="submit" class="quiet">Taking it anyway (grades C)</button></form>
</div>"""
    elif row["outcome"] == "skipped":
        choices = '<p class="note">Not taken: logged as a skipped setup. A no-trade decision is a process result too.</p>'
    details = [f"{e(row['direction'])}", f"{e(row['setup'])}"]
    if row["asset"]:
        details.append(e(row["asset"]))
    if row["emotion_word"]:
        details.append(f"feeling {e(row['emotion_word'])}")
    if row["confidence"]:
        details.append(f"confidence {e(row['confidence'])}/5")
    body = f"""
<h1>Checklist</h1>
{banner}
<ul class="chips">{items}</ul>
<p class="note">{" · ".join(details)}</p>
<p class="note">Saved {e(filled.strftime("%a %d %b %H:%M:%S"))}. The time is the proof it came before the entry.</p>
{choices}
<p><a class="button" href="/checklist">New checklist</a></p>
"""
    return layout("Checklist", body, "/checklist")


# --- shared bits -----------------------------------------------------------------


def r_text(r) -> str:
    return "–" if r is None else f"{r:+.2f}R"


def pct(v) -> str:
    return "–" if v is None else f"{v:.0f}%"


def money(v) -> str:
    return "–" if v is None else f"{v:,.2f}"


def when(iso: str | None, fmt: str = "%a %d %b %H:%M") -> str:
    return datetime.fromisoformat(iso).strftime(fmt) if iso else "–"


def grade_badge(t) -> str:
    if t["source"] == "legacy-csv":
        return '<span class="grade old" title="From the old tracker: not graded">old</span>'
    if t["grade"] is None:
        return (
            '<span class="grade none" title="Before the rulebook: not graded">–</span>'
        )
    return f'<span class="grade {e(t["grade"])}">{e(t["grade"])}</span>'


def short(symbol: str | None) -> str:
    return (symbol or "").split(":")[-1]


def reasons(t, notes: bool = True) -> list[str]:
    """What decided the grade; side notes ("Note: ...") only when asked for."""
    out = json.loads(t["grade_reasons_json"] or "[]")
    return out if notes else [r for r in out if not r.startswith("Note: ")]


def range_text(rng, fmt) -> str:
    return "too few to tell" if rng is None else f"{fmt(rng[0])} to {fmt(rng[1])}"


# --- dashboard ------------------------------------------------------------------


def _rule_text(rb: Rulebook) -> str:
    base = (
        f"At {rb.checkpoint_trades}: rules followed under {rb.rule_following_threshold_pct:g}% means fix the"
        " process first; otherwise "
    )
    if rb.checkpoint_reading == "interval":
        return base + (
            "read the average R by its rough range: all of it below 0 means change the setup; a range that spans 0"
            " means keep going to 100 trades or test the setup mechanically."
        )
    return base + "an average R below 0 means change the setup."


def _four(four: dict) -> str:
    cells = (
        ("followed_won", "Followed and won", ""),
        ("followed_lost", "Followed and lost", "a good trade"),
        ("broke_won", "Broke and won", "dangerous"),
        ("broke_lost", "Broke and lost", ""),
    )
    out = "".join(
        f'<div class="four {k}"><p class="value">{four[k]}</p><p>{e(words)}</p>'
        + (f'<p class="note">{e(tag)}</p>' if tag else "")
        + "</div>"
        for k, words, tag in cells
    )
    return f'<div class="fourgrid">{out}</div>'


def _bucket_table(title: str, rows: list[dict]) -> str:
    if not rows:
        return ""
    body = "".join(
        f'<tr class="{"few" if b["too_few"] else ""}"><th scope="row">{e(b["key"])}</th><td>{b["n"]}</td>'
        + (
            '<td colspan="2">Too few to tell</td>'
            if b["too_few"]
            else f'<td>{r_text(b["expectancy"])}<br><span class="note">{e(range_text(b["expectancy_range"], r_text))}</span></td>'
            f'<td>{pct(100 * b["win_rate"])}<br><span class="note">{e(range_text(b["wilson"], lambda x: pct(100 * x)))}</span></td>'
        )
        + "</tr>"
        for b in rows
    )
    return (
        f'<h3>{e(title)}</h3><table class="buckets"><thead><tr><th></th><th>Trades</th><th>Average R</th>'
        f"<th>Win rate</th></tr></thead><tbody>{body}</tbody></table>"
    )


def dashboard(d: dict, rb: Rulebook) -> str:
    adh, comp, cp, today, s = (
        d["adherence"],
        d["completion"],
        d["checkpoint"],
        d["today"],
        d["summary"],
    )
    mix = " · ".join(f"{g} {d['grades'].get(g, 0)}" for g in ("A", "B", "C"))
    graded = adh["graded"]
    adh_value = (
        f"{adh['followed']} of {graded} trades · {pct(adh['pct'])}"
        if graded
        else "No graded trades yet"
    )
    session = (
        "no window set"
        if today.in_session is None
        else f"open ({today.window})"
        if today.in_session
        else "closed"
    )
    progress = (
        f"{cp['n']} trades · reached at {cp['target']}"
        if cp["reached"]
        else f"{cp['n']} of {cp['target']}"
    )
    reading = ""
    if cp["reached"]:
        reading = f'<p class="reading">The rulebook reads: <strong>{e(cp["reading"])}</strong></p>'
    flagged = "".join(
        f'<li><a href="/trade/{t["id"]}">{e(t["trade_ref"])}</a>: {e("; ".join(json.loads(t["flags_json"])))}</li>'
        for t in d["flagged"]
    )
    flagged_html = (
        f'<h2>To look at</h2><ul class="flags">{flagged}</ul>' if flagged else ""
    )
    ungraded = []
    if d["old_tracker"]:
        ungraded.append(f"{d['old_tracker']} from the old tracker")
    if d["before_rulebook"]:
        ungraded.append(f"{d['before_rulebook']} before the rulebook")
    if d["before_grading"]:
        ungraded.append(f"{d['before_grading']} before grading started")
    ungraded_html = (
        f'<p class="note">Not graded: {e(", ".join(ungraded))}.</p>' if ungraded else ""
    )
    payoff = "–" if s["payoff"] is None else f"{s['payoff']:.2f}"
    pf = (
        "no losses yet"
        if s["profit_factor"] is None and s["n"]
        else ("–" if s["profit_factor"] is None else f"{s['profit_factor']:.2f}")
    )
    win = (
        "–"
        if s["win_rate"] is None
        else f"{pct(100 * s['win_rate'])} ({range_text(s['wilson'], lambda x: pct(100 * x))})"
    )
    exp_range = range_text(s["expectancy_range"], r_text)
    body = f"""
<h1>Journal</h1>
<section class="cards">
<div class="card big"><p class="label">Rules followed</p><p class="value">{e(adh_value)}</p><p class="note">Grades: {e(mix)}</p></div>
<div class="card"><p class="label">Checkpoint</p><p class="value">{progress}</p><p class="note">{e(_rule_text(rb))}</p>{reading}</div>
<div class="card"><p class="label">Today</p><p class="value small">Trades {today.trades_today} of {today.max_trades} · losses {today.losses_today} of {today.stop_after}</p><p class="note">Session: {e(session)}</p></div>
<div class="card"><p class="label">Checklist before the entry</p><p class="value">{comp["with"]} of {comp["graded"]} · {pct(comp["pct"])}</p><p class="note">Skipped setups: {d["skipped"]}. A no-trade decision is a process result too.</p></div>
</section>
<h2>Equity curve in R</h2>
{svg.equity(d["curve"])}
<p class="note">Deepest drawdown: {d["max_drawdown"]:.2f}R.</p>
<h2>R distribution</h2>
{svg.histogram(d["histogram"])}
<h2>The numbers</h2>
<table class="numbers"><tbody>
<tr><th scope="row">Trades with an R</th><td>{s["n"]}{f' <span class="note">({s["no_r"]} without: initial stop unknown)</span>' if s["no_r"] else ""}</td></tr>
<tr><th scope="row">Win rate</th><td>{e(win)}</td></tr>
<tr><th scope="row">Average R</th><td>{r_text(s["expectancy"])} <span class="note">rough range: {e(exp_range)}</span></td></tr>
<tr><th scope="row">Profit factor</th><td>{e(pf)}</td></tr>
<tr><th scope="row">Average win / loss</th><td>{r_text(s["avg_win"])} / {r_text(s["avg_loss"])} · payoff {e(payoff)}</td></tr>
</tbody></table>
<p class="note">At 50 trades these ranges stay wide: 25 wins in 50 is anywhere from 37% to 63%. The process is what 50 trades can judge.</p>
<h2>Four kinds of trade</h2>
{_four(d["four"])}
<h2>Where it went well and badly</h2>
{"".join(_bucket_table(title, rows) for title, rows in d["buckets"])}
{flagged_html}
{ungraded_html}
"""
    return layout("Dashboard", body, "/dashboard")


# --- trades ----------------------------------------------------------------------


def trades_list(trades: list) -> str:
    items = []
    for t in trades:
        first = reasons(t)[0] if t["grade"] == "C" and reasons(t) else ""
        why = f'<span class="why-c">{e(first)}</span>' if first else ""
        items.append(
            f'<li><a href="/trade/{t["id"]}">'
            f'<span class="top"><span>{e(when(t["entry_time"]))}</span>'
            f'<span>{grade_badge(t)} <span class="num">{e(r_text(t["result_r"]))}</span></span></span>'
            f'<span class="what">{e(short(t["asset"]))} · {e(t["direction"])} · {e(t["setup"] or "no setup")}</span>'
            f"{why}</a></li>"
        )
    listing = (
        f'<ul class="tradelist">{"".join(items)}</ul>'
        if items
        else '<p class="note">No trades yet. Drop a TradingView export on the import page, or run journal import.</p>'
    )
    return layout("Trades", f"<h1>Trades</h1>{listing}", "/trades")


def _facts(t, overrides: dict) -> str:
    stop_note = " (set by hand)" if "stop" in overrides else ""
    stop = overrides.get("stop", t["stop"])
    moved = {None: "not visible in the export", 0: "no", 1: "yes"}[
        t["stop_moved_further"]
    ]
    rows = [
        ("Entry order placed", when(t["placed_time"], "%a %d %b %H:%M:%S")),
        (
            "Entry",
            f"{when(t['entry_time'], '%a %d %b %H:%M:%S')} at {t['entry'] if t['entry'] is not None else '–'}",
        ),
        ("Initial stop", f"{'–' if stop is None else stop}{stop_note}"),
        ("Target", "–" if t["target"] is None else t["target"]),
        (
            "Exit",
            f"{when(t['exit_time'], '%a %d %b %H:%M:%S')} at {t['exit'] if t['exit'] is not None else '–'} ({t['exit_reason'] or '–'})",
        ),
        ("Size", "–" if t["size"] is None else f"{t['size']:g}"),
        ("Result", f"{r_text(t['result_r'])} · {money(t['result_currency'])}"),
        ("1R", money(t["risk_currency"])),
        ("Fees", money(t["fees"])),
        ("Planned R:R", "–" if t["planned_rr"] is None else f"{t['planned_rr']:.2f}"),
        (
            "Risk",
            "unknown: import Account history for the balance"
            if t["risk_pct"] is None
            else f"{t['risk_pct']:.2f}% of {money(t['balance_at_entry'])}",
        ),
        ("Stop placed", t["stop_placed"] or "–"),
        ("Stop moved further away", moved),
        ("Session window", t["session_window"] or "no window set"),
        ("Rulebook", t["rulebook_version"] or "none in force"),
    ]
    return (
        '<table class="numbers"><tbody>'
        + "".join(
            f'<tr><th scope="row">{e(k)}</th><td>{e(v)}</td></tr>' for k, v in rows
        )
        + "</tbody></table>"
    )


def trade_detail(
    t,
    checklist_row,
    shots: list,
    edits: list,
    rb: Rulebook,
    token: str,
    error: str | None = None,
    downloads: list[str] | None = None,
) -> str:
    overrides = json.loads(t["overrides_json"] or "{}")
    why = "".join(
        f'<li class="{"note" if r.startswith("Note: ") else ""}">{e(r)}</li>'
        for r in reasons(t)
    )
    flags = "".join(f"<li>{e(f)}</li>" for f in json.loads(t["flags_json"] or "[]"))
    if checklist_row is not None:
        answers = json.loads(checklist_row["answers_json"] or "{}")
        ticks = ", ".join(
            f"{checklist.label(k, rb)}: {'yes' if answers.get(k) else 'no'}"
            for k in answers
        )
        check = (
            f"<p>Filled {e(when(checklist_row['filled_at'], '%a %d %b %H:%M:%S'))} · verdict"
            f' <strong>{e(checklist_row["verdict"])}</strong></p><p class="note">{e(ticks)}</p>'
        )
    elif t["source"] == "legacy-csv":
        check = (
            '<p class="note">From the old tracker, before the checklist existed.</p>'
        )
    else:
        check = "<p>No checklist before the entry.</p>"
    gallery = "".join(
        f'<a href="/shot/{s["id"]}"><img src="/shot/{s["id"]}" alt="{e(s["phase"])} screenshot" class="thumb"></a>'
        for s in shots
    )
    history = "".join(
        f"<li>{e(when(x['edited_at']))}: {e(x['field'])} {e(x['old'] or '–')} → {e(x['new'] or '–')}</li>"
        for x in edits
    )
    tok = f'<input type="hidden" name="csrf" value="{e(token)}">'
    err = f'<p class="error" role="alert">{e(error)}</p>' if error else ""
    legacy_table = ""
    if t["legacy_json"]:
        cells = "".join(
            f'<tr><th scope="row">{e(k)}</th><td>{e(v)}</td></tr>'
            for k, v in json.loads(t["legacy_json"]).items()
        )
        legacy_table = f'<h2>As written in the old tracker</h2><table class="numbers"><tbody>{cells}</tbody></table>'
    setups = "".join(
        f'<option value="{e(s)}"{" selected" if t["setup"] == s else ""}>{e(s)}</option>'
        for s in rb.setups
    )
    fix = ""
    if t["source"] != "legacy-csv":
        stop_value = overrides.get("stop", "")
        fix = f"""
<h2>Fix the data</h2>
<p class="note">The grade can't be changed by hand. If the data is wrong, fix it here and the grade follows.</p>
<form method="post" action="/trade/{t["id"]}/fix" class="stack">{tok}
<label class="field">Setup<select name="setup"><option value="">–</option>{setups}</select></label>
<label class="field">Emotion word<input type="text" name="emotion_word" maxlength="30" value="{e(t["emotion_word"])}"></label>
<label class="field">Initial stop, only if the export's stop isn't the one you started with<input type="text" inputmode="decimal" name="stop" value="{e(stop_value)}"></label>
<button type="submit">Save the fix</button></form>"""
    body = f"""
<h1>{e(t["trade_ref"])} {grade_badge(t)}</h1>
{_ungraded_note(t)}
<p class="note">{e(short(t["asset"]))} · {e(t["direction"])} · {e(t["setup"] or "no setup")}{" · feeling " + e(t["emotion_word"]) if t["emotion_word"] else ""}</p>
{err}
{f'<h2>Why this grade</h2><ul class="why">{why}</ul>' if why else ""}
{f'<h2>To look at</h2><ul class="flags">{flags}</ul>' if flags else ""}
<h2>Checklist</h2>
{check}
<h2>The trade</h2>
{_facts(t, overrides)}
<h2>Screenshots</h2>
{gallery or '<p class="note">None yet.</p>'}
{_shot_forms(t, token, downloads or [])}
<h2>Notes</h2>
<form method="post" action="/trade/{t["id"]}/notes" class="stack">{tok}
<label class="field">Lesson<textarea name="lesson" rows="2" maxlength="2000">{e(t["lesson"])}</textarea></label>
<label class="field">Mistake<textarea name="mistake" rows="2" maxlength="2000">{e(t["mistake"])}</textarea></label>
<label class="field">Note<textarea name="note" rows="2" maxlength="2000">{e(t["note"])}</textarea></label>
<button type="submit">Save notes</button></form>
{fix}
{legacy_table}
{f'<h2>Changes</h2><ul class="note">{history}</ul>' if history else ""}
"""
    return layout(t["trade_ref"], body, "/trades")


# --- the weekly review --------------------------------------------------------------


PROMPTS = (
    (
        "c_causes",
        "What in the setup or environment led to each C?",
        "The cue, the time, the place: what made the break easy?",
    ),
    ("keep", "One rule to keep doing", "The rule that held up this week."),
    (
        "change",
        "One change, as an if-then with a time and a place",
        "If [cue], then I [action], at [time], at [place].",
    ),
)


def review(w: dict, token: str) -> str:
    monday = w["monday"]
    sunday = monday + timedelta(days=6)
    adh, comp, four = w["adherence"], w["completion"], w["four"]

    def group(g: str, title: str) -> str:
        items = w["by_grade"][g]
        if not items:
            return ""
        lis = "".join(
            f'<li><a href="/trade/{t["id"]}">{e(when(t["entry_time"]))} {e(short(t["asset"]))}</a> {e(r_text(t["result_r"]))}'
            + (
                "<ul>"
                + "".join(f"<li>{e(r)}</li>" for r in reasons(t, notes=False))
                + "</ul>"
                if g != "A"
                else ""
            )
            + "</li>"
            for t in items
        )
        return f'<h3>{e(title)} ({len(items)})</h3><ul class="week">{lis}</ul>'

    answers = w["answers"]
    fields = "".join(
        f'<label class="field">{e(q)}<textarea name="{k}" rows="3" maxlength="4000" placeholder="{e(hint)}">{e(answers.get(k))}</textarea></label>'
        for k, q, hint in PROMPTS
    )
    body = f"""
<h1>Week {e(w["week"].split("-W")[1])}</h1>
<p class="note">{e(monday.strftime("%a %d %b"))} to {e(sunday.strftime("%a %d %b %Y"))} ·
<a href="/review?week={e(w["previous"])}">previous week</a> · <a href="/review?week={e(w["next"])}">next week</a></p>
<section class="cards">
<div class="card"><p class="label">Rules followed</p><p class="value">{adh["followed"]} of {adh["graded"]} · {pct(adh["pct"])}</p></div>
<div class="card"><p class="label">Checklist before the entry</p><p class="value">{comp["with"]} of {comp["graded"]}</p><p class="note">Skipped setups: {w["skipped"]}</p></div>
</section>
<h2>Four kinds of trade</h2>
{_four(four)}
<h2>The week's trades by grade</h2>
{group("C", "C: a rule broken") or ""}{group("B", "B: a housekeeping slip")}{group("A", "A")}
{"" if w["trades"] else '<p class="note">No trades this week.</p>'}
<h2>The review</h2>
<form method="post" action="/review" class="stack"><input type="hidden" name="csrf" value="{e(token)}">
<input type="hidden" name="week" value="{e(w["week"])}">
{fields}
<button type="submit">Save the review</button></form>
<p><button type="button" class="quiet" data-print>Print or save as PDF</button></p>
"""
    return layout(f"Week {w['week']}", body, "/review")


def _phase_select() -> str:
    return (
        '<select name="phase"><option value="entry">at the entry</option>'
        '<option value="exit">at the exit</option><option value="other">other</option></select>'
    )


def _shot_forms(t, token: str, downloads: list[str]) -> str:
    if t["source"] == "legacy-csv":
        return ""
    tok = f'<input type="hidden" name="csrf" value="{e(token)}">'
    found = "".join(
        f'<li><form method="post" action="/trade/{t["id"]}/attach" class="inline">{tok}'
        f'<input type="hidden" name="name" value="{e(name)}"><input type="hidden" name="phase" value="entry">'
        f'<button type="submit" class="quiet">Attach {e(name)}</button></form></li>'
        for name in downloads
    )
    found_html = (
        f'<p class="note">In Downloads, saved around this trade:</p><ul class="plain">{found}</ul>'
        if found
        else ""
    )
    return f"""
<form method="post" action="/trade/{t["id"]}/shot" enctype="multipart/form-data" class="stack" data-drop>
{tok}
<label class="drop">Drop a screenshot here, or tap to pick one (PNG or JPEG)
<input type="file" name="shot" accept="image/png,image/jpeg" required></label>
<label class="field">Taken {_phase_select()}</label>
<button type="submit">Add the screenshot</button>
</form>
{found_html}"""


# --- import ---------------------------------------------------------------------------


def import_page(
    token: str, results: list | None = None, error: str | None = None
) -> str:
    out = []
    for name, r, err in results or []:
        if err:
            out.append(f'<li class="error">{e(name)}: refused. {e(err)}</li>')
        elif r.already:
            out.append(f"<li>{e(name)}: already imported (nothing changed).</li>")
        elif r.kind == "account-history":
            out.append(
                f"<li>{e(name)}: Account history, {r.new_balances} new balance rows.</li>"
            )
        else:
            trades = (
                "1 new trade" if r.new_trades == 1 else f"{r.new_trades} new trades"
            )
            still = (
                f", {r.open_positions} position(s) still open"
                if r.open_positions
                else ""
            )
            old = (
                f", {r.matched_old} matched to the old tracker" if r.matched_old else ""
            )
            out.append(
                f"<li>{e(name)}: {trades}, {r.updated_trades} updated{old}{still}.</li>"
            )
    done = (
        f'<ul class="results">{"".join(out)}</ul><p><a href="/trades">See the trades</a></p>'
        if out
        else ""
    )
    err = f'<p class="error" role="alert">{e(error)}</p>' if error else ""
    body = f"""
<h1>Import from TradingView</h1>
<p class="note">In the Paper Trading panel, use <strong>Export data…</strong> and drop the
<strong>History</strong> file here (and <strong>Account history</strong>, for the balance behind the risk check).
Export in English, after each session: History keeps the last 100 orders. The same file twice changes nothing.</p>
{err}
{done}
<form method="post" action="/import" enctype="multipart/form-data" class="stack" data-drop>
<input type="hidden" name="csrf" value="{e(token)}">
<label class="drop">Drop the CSV files here, or tap to pick them
<input type="file" name="files" accept=".csv,text/csv" multiple required></label>
<button type="submit">Import</button>
</form>
"""
    return layout("Import", body, "/import")


def _ungraded_note(t) -> str:
    if t["source"] == "legacy-csv" or t["grade"] is not None:
        return ""
    if not t["rulebook_version"]:
        return '<p class="note">Not graded: entered before the rulebook started. Shown, never counted.</p>'
    return '<p class="note">Not graded: entered before grading started. Shown, never counted.</p>'
