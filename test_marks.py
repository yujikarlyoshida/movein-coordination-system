"""
Mark done: a unit you took care of yourself stays off "need you" -- for that
lease email only -- across checks and restarts, and Undo puts it back.

Run with:  python -m pytest test_marks.py -v
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("MOVEIN_HISTORY_PATH", os.path.join(tempfile.mkdtemp(prefix="movein-test-"), "history.sqlite3"))
os.environ.setdefault("MOVEIN_TEMPLATE_PATH", os.path.join(tempfile.mkdtemp(prefix="movein-test-"), "no-template.txt"))

import board  # noqa: E402
import history  # noqa: E402
import menu_model  # noqa: E402
import rules  # noqa: E402
from models import Result, Verdict  # noqa: E402


_saved_path = None


def setup_module():
    # A log of this module's own, put back afterwards so other modules keep theirs.
    global _saved_path
    _saved_path = os.environ.get("MOVEIN_HISTORY_PATH")
    os.environ["MOVEIN_HISTORY_PATH"] = os.path.join(tempfile.mkdtemp(prefix="movein-test-"), "history.sqlite3")


def teardown_module():
    if _saved_path is None:
        os.environ.pop("MOVEIN_HISTORY_PATH", None)
    else:
        os.environ["MOVEIN_HISTORY_PATH"] = _saved_path


def _results():
    return [Result(unit="0317", verdict=Verdict.NEEDS_OUTREACH, trigger_id="lease-a"),
            Result(unit="0735", verdict=Verdict.NEEDS_OUTREACH, trigger_id="lease-b"),
            Result(unit="0900", verdict=Verdict.SKIPPED_CORPORATE, trigger_id="lease-c")]


def test_a_marked_lease_shows_as_handled_by_you():
    out = rules.with_marks(_results(), {"lease-a", "lease-c"})
    assert [r.verdict for r in out] == [Verdict.HANDLED, Verdict.NEEDS_OUTREACH, Verdict.SKIPPED_CORPORATE]
    assert out[0].evidence[0].describe() == "you marked it done"


def test_a_new_lease_email_for_the_same_unit_shows_again():
    fresh = [Result(unit="0317", verdict=Verdict.NEEDS_OUTREACH, trigger_id="lease-a2")]
    assert rules.with_marks(fresh, {"lease-a"})[0].verdict is Verdict.NEEDS_OUTREACH


def test_marked_units_leave_the_count_and_the_notifications():
    out = rules.with_marks(_results(), {"lease-a"})
    snap = menu_model.Snapshot(results=out, state="watching")
    assert [r.unit for r in snap.needs_outreach] == ["0735"]


def test_the_board_offers_mark_done_and_undo():
    out = rules.with_marks(_results(), {"lease-a"})
    page = board.render(out, senders={"addresses": [], "names": []})
    assert 'data-action="unmark_done" data-value="0317"' in page
    assert 'data-action="mark_done" data-value="0735"' in page
    assert 'data-action="mark_done" data-value="0317"' not in page
    assert "You marked this done" in page
    # Corporate leases are never offered it.
    assert 'data-value="0900"' not in page.split("0900", 1)[1].split("</tr>", 1)[0]


def test_marks_survive_in_the_log():
    history.mark_done("lease-a", "0317")
    assert rules.with_marks(_results(), history.marked_done())[0].verdict is Verdict.HANDLED
    history.unmark_done("lease-a")
    assert rules.with_marks(_results(), history.marked_done())[0].verdict is Verdict.NEEDS_OUTREACH
