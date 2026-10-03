"""The dashboard, the trade pages and the weekly review, over HTTP, on a made-up journal."""

from __future__ import annotations

import json
import re
from datetime import datetime

import pytest

from tradejournal import db, settings, synth, tvimport
from tradejournal.synth import HISTORY_HEADER, Spec, history_rows, to_csv
from webtools import start, stop

MONEY = "98765.43"


@pytest.fixture
def client(tmp_path):
    server, c = start()
    conn = db.connect()
    data = synth.journal(40, seed=5)
    synth.insert_checklists(conn, data.checklists)
    path = tmp_path / "h.csv"
    path.write_bytes(to_csv(HISTORY_HEADER, history_rows(data.specs)))
    tvimport.import_file(conn, path, settings.parse(""))
    # one trade with a money figure nobody could mistake
    tid = conn.execute("SELECT id FROM trades ORDER BY entry_time LIMIT 1").fetchone()[
        0
    ]
    conn.execute(
        "UPDATE trades SET result_currency = ? WHERE id = ?", (float(MONEY), tid)
    )
    conn.commit()
    conn.close()
    c.trade_id = tid
    yield c
    stop(server)


def test_home_opens_the_dashboard(client):
    status, headers, _ = client.get("/")
    assert status == 303 and headers["Location"] == "/dashboard"


def test_the_dashboard_opens_on_rule_adherence_then_the_charts(client):
    status, _, page = client.get("/dashboard")
    assert status == 200
    assert page.index("Rules followed") < page.index("<svg")
    assert re.search(r"\d+ of 50", page)
    assert "Checklist before the entry" in page
    assert page.count("<svg") == 2
    assert "Too few to tell" in page


def test_the_four_trade_types_are_named(client):
    _, _, page = client.get("/dashboard")
    for words in (
        "Followed and won",
        "Followed and lost",
        "Broke and won",
        "Broke and lost",
        "good trade",
        "dangerous",
    ):
        assert words in page


def test_no_money_and_no_streaks_outside_a_trades_detail(client):
    for path in ("/dashboard", "/trades"):
        _, _, page = client.get(path)
        assert MONEY not in page and "98,765" not in page
        assert "streak" not in page.lower()
    _, _, detail = client.get(f"/trade/{client.trade_id}")
    assert "98,765.43" in detail


def test_the_trade_list_shows_grades_and_r(client):
    _, _, page = client.get("/trades")
    assert re.search(r'class="grade [ABC]"', page)
    assert re.search(r"[+-]\d+\.\d\dR", page)


def test_a_trade_page_shows_why_it_got_its_grade(client):
    _, _, page = client.get(f"/trade/{client.trade_id}")
    conn = db.connect()
    reasons = json.loads(
        conn.execute(
            "SELECT grade_reasons_json FROM trades WHERE id = ?", (client.trade_id,)
        ).fetchone()[0]
    )
    conn.close()
    assert reasons and all(r in page for r in reasons)


def test_a_note_is_saved_and_logged(client):
    status, headers, _ = client.post(
        f"/trade/{client.trade_id}/notes",
        {"lesson": "Waited for the close", "mistake": "", "note": ""},
    )
    assert status == 303
    conn = db.connect()
    assert (
        conn.execute(
            "SELECT lesson FROM trades WHERE id = ?", (client.trade_id,)
        ).fetchone()[0]
        == "Waited for the close"
    )
    assert conn.execute("SELECT field, new FROM edits").fetchall()[0][:] == (
        "lesson",
        "Waited for the close",
    )
    conn.close()


def test_fixing_the_initial_stop_recomputes_r_and_the_grade_follows(client, tmp_path):
    conn = db.connect()
    spec = Spec(
        symbol="TEST:FIXUSD",
        direction="long",
        qty=1,
        entry=100,
        stop=100,
        target=104,
        placed=datetime(2026, 10, 9, 15, 0),
        exit_kind="target",
    )
    path = tmp_path / "fix.csv"
    path.write_bytes(to_csv(HISTORY_HEADER, history_rows([spec], first_id=9000)))
    tvimport.import_file(conn, path, settings.parse(""))
    tid = conn.execute("SELECT id FROM trades WHERE asset = 'TEST:FIXUSD'").fetchone()[
        0
    ]
    assert (
        conn.execute("SELECT result_r FROM trades WHERE id = ?", (tid,)).fetchone()[0]
        is None
    )
    conn.close()
    status, _, _ = client.post(
        f"/trade/{tid}/fix",
        {"stop": "98", "setup": "sweep-BOS-FVG", "emotion_word": "calm"},
    )
    assert status == 303
    conn = db.connect()
    r, flags, overrides = conn.execute(
        "SELECT result_r, flags_json, overrides_json FROM trades WHERE id = ?", (tid,)
    ).fetchone()
    edits = conn.execute(
        "SELECT field, old, new FROM edits WHERE trade_id = ?", (tid,)
    ).fetchall()
    conn.close()
    assert r == pytest.approx(2.0)  # (104 - 100) / (100 - 98)
    assert flags is None
    assert json.loads(overrides) == {"stop": 98.0}
    assert ("stop", "100.0", "98.0") in [tuple(e) for e in edits]


def test_a_bad_stop_is_refused(client):
    status, _, _ = client.post(
        f"/trade/{client.trade_id}/fix",
        {"stop": "lots", "setup": "sweep-BOS-FVG", "emotion_word": ""},
    )
    assert status == 400


# --- the weekly review --------------------------------------------------------


def test_the_review_shows_the_weeks_c_trades_with_reasons_and_the_three_prompts(client):
    _, _, page = client.get("/review?week=2026-W41")
    assert "What in the setup or environment led to each C?" in page
    assert "One rule to keep doing" in page
    assert "One change, as an if-then" in page
    conn = db.connect()
    cs = conn.execute(
        "SELECT grade_reasons_json FROM trades WHERE grade = 'C' AND entry_time < '2026-10-12'"
    ).fetchall()
    conn.close()
    assert cs, "the made-up week should hold at least one C"
    assert json.loads(cs[0][0])[0] in page


def test_review_answers_are_saved_and_shown_again(client):
    answers = {
        "week": "2026-W41",
        "c_causes": "Traded the first 5 minutes",
        "keep": "Stop in the bracket",
        "change": "If it's before 14:00, then I close TradingView, at my desk, checked at 14:05",
    }
    status, headers, _ = client.post("/review", answers)
    assert status == 303 and headers["Location"] == "/review?week=2026-W41"
    _, _, page = client.get("/review?week=2026-W41")
    assert "Traded the first 5 minutes" in page and "close TradingView" in page


def test_a_review_without_the_page_token_is_refused(client):
    status, _, _ = client.post(
        "/review", {"week": "2026-W41", "keep": "x"}, token=False
    )
    assert status == 403


def test_a_week_that_is_not_a_week_is_refused(client):
    assert client.get("/review?week=2026-W99")[0] == 400


def test_the_dashboard_follows_new_data(client, tmp_path):
    _, _, before = client.get("/dashboard")
    n_before = int(re.search(r"(\d+) of 50", before).group(1))
    conn = db.connect()
    spec = Spec(
        symbol="TEST:NEWUSD",
        direction="long",
        qty=1,
        entry=100,
        stop=99,
        target=102,
        placed=datetime(2026, 10, 23, 15, 0),
        exit_kind="target",
    )
    path = tmp_path / "new.csv"
    path.write_bytes(to_csv(HISTORY_HEADER, history_rows([spec], first_id=7000)))
    tvimport.import_file(conn, path, settings.parse(""))
    conn.close()
    _, _, after = client.get("/dashboard")
    assert int(re.search(r"(\d+) of 50", after).group(1)) == n_before + 1


def test_an_empty_journal_says_so(tmp_path):
    server, c = start()
    try:
        _, _, page = c.get("/dashboard")
        assert "0 of 50" in page and "No closed trades yet" in page
    finally:
        stop(server)


def test_with_no_windows_the_session_table_says_so(client):
    _, _, page = client.get("/dashboard")
    assert "no window set" in page
    assert "(none)" not in page


def test_the_review_lists_what_decided_each_grade_not_the_side_notes(client):
    _, _, page = client.get("/review?week=2026-W41")
    assert "the size check used your checklist answer" not in page
    _, _, detail = client.get(f"/trade/{client.trade_id}")
    assert "the size check used your checklist answer" in detail


def test_past_the_checkpoint_it_says_reached(tmp_path):
    server, c = start()
    try:
        conn = db.connect()
        data = synth.journal(55, seed=2)
        synth.insert_checklists(conn, data.checklists)
        path = tmp_path / "h.csv"
        path.write_bytes(to_csv(HISTORY_HEADER, history_rows(data.specs)))
        tvimport.import_file(conn, path, settings.parse(""))
        conn.close()
        _, _, page = c.get("/dashboard")
        assert "55 trades · reached at 50" in page
        assert "The rulebook reads:" in page
    finally:
        stop(server)


def test_trades_before_grading_started_are_shown_and_named(tmp_path):
    from webtools import ROOT

    text = (ROOT / "examples" / "rulebook.toml").read_text()
    assert 'grading_starts = "first-checklist"' in text  # the example's default
    server, c = start(rulebook_text=text)
    try:
        conn = db.connect()
        spec = Spec(
            symbol="TEST:PREUSD",
            direction="long",
            qty=1,
            entry=100,
            stop=99,
            target=102,
            placed=datetime(2026, 10, 5, 10, 0),
            exit_kind="target",
        )
        path = tmp_path / "pre.csv"
        path.write_bytes(to_csv(HISTORY_HEADER, history_rows([spec], first_id=8000)))
        tvimport.import_file(conn, path, settings.parse(""))
        tid = conn.execute("SELECT id FROM trades").fetchone()[0]
        conn.close()
        _, _, page = c.get("/dashboard")
        assert "1 before grading started" in page
        assert "0 of 50" in page
        _, _, detail = c.get(f"/trade/{tid}")
        assert "before grading started" in detail
    finally:
        stop(server)
