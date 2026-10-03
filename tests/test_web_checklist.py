"""The checklist page over HTTP: what it shows, what it saves, and what it refuses."""

from __future__ import annotations

import json

import pytest

from conftest import rows
from tradejournal import db, web
from webtools import NOW, start, stop

GO = {
    "direction": "long",
    "setup": "sweep-BOS-FVG",
    "stop_in_order": "yes",
    "planned_rr_ok": "yes",
    "size_ok": "yes",
    "emotion_word": "calm",
    "confidence": "4",
}


@pytest.fixture
def client():
    server, c = start()
    yield c
    stop(server)


def saved():
    conn = db.connect()
    out = conn.execute("SELECT * FROM checklists ORDER BY id").fetchall()
    conn.close()
    return out


def test_the_page_shows_the_auto_checks_and_his_setups(client):
    status, headers, page = client.get("/checklist")
    assert status == 200
    assert "Trades today: 0 of 3" in page
    assert "Losses today: 0 of 2" in page
    assert "No session window set" in page
    assert "sweep-BOS-FVG" in page and "trendline-FVG" in page
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert headers["Cache-Control"] == "no-store"


def test_a_go_checklist_is_saved_with_the_server_time(client):
    status, headers, _ = client.post("/checklist", GO)
    assert status == 303
    (c,) = saved()
    assert headers["Location"] == f"/checklist/{c['id']}"
    assert c["filled_at"] == NOW.isoformat(timespec="seconds")
    assert (c["verdict"], c["emotion_word"], c["confidence"]) == ("go", "calm", 4)
    _, _, page = client.get(headers["Location"])
    assert "Go." in page


def test_a_red_answer_says_no_trade_and_offers_both_choices(client):
    _, headers, _ = client.post("/checklist", {**GO, "size_ok": "no"})
    _, _, page = client.get(headers["Location"])
    assert "Rulebook says no trade" in page
    assert "Size = 1R at 0.5% of the account" in page
    assert "Not taking it" in page and "Taking it anyway" in page


def test_taking_it_anyway_from_the_page(client):
    _, headers, _ = client.post("/checklist", {**GO, "size_ok": "no"})
    status, _, _ = client.post(headers["Location"] + "/anyway", {})
    assert status == 303
    assert saved()[0]["verdict"] == "taken-anyway"


def test_not_taking_it_from_the_page(client):
    _, headers, _ = client.post("/checklist", {**GO, "stop_in_order": "no"})
    client.post(headers["Location"] + "/skip", {})
    assert saved()[0]["outcome"] == "skipped"


def test_a_missing_answer_shows_the_form_again_and_saves_nothing(client):
    fields = {k: v for k, v in GO.items() if k != "planned_rr_ok"}
    status, _, page = client.post("/checklist", fields)
    assert status == 400
    assert "Answer every question" in page
    assert saved() == []


def test_typed_text_is_shown_as_text_never_as_code(client):
    _, headers, _ = client.post(
        "/checklist", {**GO, "asset": "<script>alert(1)</script>"}
    )
    _, _, page = client.get(headers["Location"])
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


# --- what it refuses -----------------------------------------------------------


def test_a_post_without_the_page_token_is_refused(client):
    status, _, _ = client.post("/checklist", GO, token=False)
    assert status == 403
    assert saved() == []


def test_a_post_from_another_website_is_refused(client):
    status, _, _ = client.post("/checklist", GO, origin="http://evil.example")
    assert status == 403
    assert saved() == []


def test_a_request_for_another_host_name_is_refused(client):
    status, _, _ = client.get("/checklist", host="evil.example")
    assert status == 421


def test_a_huge_body_is_refused(client):
    status, _, _ = client.post("/checklist", {**GO, "asset": "x" * 100_000})
    assert status == 413
    assert saved() == []


def test_an_unknown_path_is_not_found(client):
    assert client.get("/etc/passwd")[0] == 404
    assert client.get("/static/../journal.sqlite")[0] == 404


def test_the_stylesheet_and_script_are_served(client):
    status, headers, _ = client.get("/static/app.css")
    assert status == 200 and headers["Content-Type"].startswith("text/css")
    status, headers, _ = client.get("/static/app.js")
    assert status == 200 and headers["Content-Type"].startswith("text/javascript")


@pytest.mark.parametrize(
    "addr, ok",
    [
        ("127.0.0.1", True),
        ("::1", True),
        ("100.101.102.103", True),
        ("0.0.0.0", False),
        ("::", False),
        ("192.168.1.20", False),
        ("10.0.0.5", False),
        ("8.8.8.8", False),
    ],
)
def test_it_binds_only_to_localhost_or_the_tailnet(addr, ok):
    assert web.bind_allowed(addr) is ok


def test_starting_on_every_address_is_refused():
    with pytest.raises(web.UnsafeBind):
        web.make_servers(["0.0.0.0"], 0, web.App(hostnames={"localhost"}))


@pytest.mark.parametrize(
    "peer, allowed, ok",
    [
        ("127.0.0.1", set(), True),
        ("100.88.1.2", set(), True),
        ("100.88.1.2", {"100.88.9.9"}, False),
        ("100.88.9.9", {"100.88.9.9"}, True),
        ("192.168.1.9", set(), False),
    ],
)
def test_only_this_mac_and_his_tailnet_devices_may_ask(peer, allowed, ok):
    assert web.peer_allowed(peer, allowed) is ok


def test_pages_keep_the_origin_header_on_their_own_posts(client):
    """Browsers send `Origin: null` on a form post when the page's referrer
    policy is no-referrer, which made the cross-site check refuse the user's own
    Save (seen in a real browser, 2 Oct). same-origin keeps the real origin."""
    _, headers, page = client.get("/checklist")
    assert headers["Referrer-Policy"] == "same-origin"
    assert '<meta name="referrer" content="same-origin">' in page


def test_a_post_with_a_null_origin_is_refused(client):
    status, _, _ = client.post("/checklist", GO, origin="null")
    assert status == 403
    assert saved() == []


def test_the_page_asks_for_no_favicon(client):
    _, _, page = client.get("/checklist")
    assert '<link rel="icon" href="data:,">' in page


def test_a_post_relayed_by_tailscale_serve_is_accepted(client):
    """Behind Tailscale Serve the journal may see Host 127.0.0.1 while the
    phone's browser names the Mac: any of the journal's own names, on its own
    port, is the same origin."""
    other_name = f"http://localhost:{client.port}"
    status, _, _ = client.post("/checklist", GO, origin=other_name)
    assert status == 303


def test_one_of_its_names_on_another_port_is_still_refused(client):
    status, _, _ = client.post(
        "/checklist", GO, origin=f"http://localhost:{client.port + 1}"
    )
    assert status == 403
    assert saved() == []
