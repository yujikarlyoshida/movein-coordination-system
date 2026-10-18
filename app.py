"""
Entry point.

    python app.py                 run once, write board.html
    python app.py --console       print a text report instead
    python app.py --stage         prepare everything and show the confirmation
    python app.py --watch         run continuously on the configured interval
    python app.py --demo          run against the sample mailbox, no sign-in

    python app.py --pause         stop the background watcher (persists)
    python app.py --resume        start it again
    python app.py --history       what the tool has recorded, from the database
    python app.py --db            where the local encrypted database is
    python app.py --db --reveal   ...and open it in Finder

--demo exercises the whole pipeline against sample_data.MESSAGES, so the logic
can be shown to somebody without giving them a tenant login.

--stage is the one that does real work. It prepares every action in full and
stops at a single confirmation. Nothing is sent, saved or submitted before you
approve, and drafts are never sent even after you do.
"""

from __future__ import annotations

import argparse
import sys
import time

import board
import config
import rules
from models import Result, Verdict


def collect(use_demo: bool = False) -> tuple[list[Result], str]:
    """Fetch mail and evaluate it. Returns results plus the operator's name."""
    if use_demo:
        import sample_data

        with sample_data.fixture_config():
            return rules.evaluate(sample_data.MESSAGES), "demo mode (no sign-in)"

    import mailsource

    source = mailsource.get_source()
    available, reason = source.is_available()
    if not available:
        raise RuntimeError(f"mail source '{source.name}' unavailable — {reason}")

    messages = source.list_recent_messages(days=config.LOOKBACK_DAYS)
    with _fixture_config_if_demo(source.name):
        return rules.evaluate(messages), f"{source.name} source"


def _fixture_config_if_demo(source_name: str):
    """get_source() falls back to the sample mailbox; judge it by its own roster."""
    import contextlib

    import sample_data

    if source_name == "demo":
        return sample_data.fixture_config()
    return contextlib.nullcontext()


def print_console(results: list[Result]) -> None:
    """Text report, for a terminal or a log."""
    needs = [r for r in results if r.verdict is Verdict.NEEDS_OUTREACH]
    unclear = [r for r in results if r.verdict is Verdict.UNCLEAR]
    handled = [r for r in results if r.verdict is Verdict.HANDLED]
    skipped = [r for r in results if r.verdict is Verdict.SKIPPED_CORPORATE]

    print(f"\nHousewarm — {len(results)} triggered unit(s)\n")

    if needs:
        print(f"NEEDS OUTREACH ({len(needs)})")
        for r in needs:
            print(f"  #{r.unit}  {r.trigger_subject}")
            for p in r.problems:
                print(f"      note: {p}")
        print()
    else:
        print("NEEDS OUTREACH — none\n")

    if unclear:
        print(f"COULD NOT READ ({len(unclear)})")
        for r in unclear:
            print(f"  #{r.unit}  {r.trigger_subject}")
            for p in r.problems:
                print(f"      note: {p}")
        print()

    if skipped:
        print(f"SKIPPED, CORPORATE ({len(skipped)})")
        for r in skipped:
            print(f"  #{r.unit}  {r.problems[0] if r.problems else ''}")
        print()

    if handled:
        print(f"ALREADY HANDLED ({len(handled)})")
        for r in handled:
            why = r.evidence[0].describe() if r.evidence else ""
            print(f"  #{r.unit}  {why}")
        print()


def run_once(args: argparse.Namespace) -> int:
    try:
        results, operator = collect(use_demo=args.demo)
    except Exception as exc:                            # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.console:
        print_console(results)
    else:
        html = board.render(results, operator=operator)
        with open(config.OUTPUT_HTML, "w", encoding="utf-8") as fh:
            fh.write(html)

        needs = sum(1 for r in results if r.verdict is Verdict.NEEDS_OUTREACH)
        print(
            f"wrote {config.OUTPUT_HTML} — {needs} needing outreach, "
            f"{len(results)} triggered unit(s) total"
        )

    return 0


def run_stage(args: argparse.Namespace) -> int:
    """
    Prepare everything, show it, and stop.

    This command deliberately ends at the confirmation. Approving is a separate,
    explicit act -- there is no --yes flag, because a staged plan that can be
    approved from the same command line that produced it is one typo away from
    being approved by accident.
    """
    import staging

    if args.demo:
        import history
        import sample_data

        history.use_throwaway_log()
        messages = sample_data.MESSAGES
        source_name = "demo"
    else:
        import mailsource

        source = mailsource.get_source()
        available, reason = source.is_available()
        if not available:
            print(f"error: mail source '{source.name}' unavailable — {reason}", file=sys.stderr)
            return 1
        messages = source.list_recent_messages(days=config.LOOKBACK_DAYS)
        source_name = source.name

    with _fixture_config_if_demo(source_name):
        plans = staging.collect(messages)
        confirmation = staging.render_confirmation(plans)
    print()
    print(confirmation)
    return 0


def run_history(args: argparse.Namespace) -> int:
    """What the tool has recorded: units, statuses and when they changed."""
    import history

    rows = history.history(limit=50)

    if not rows:
        print("nothing recorded yet.")
        return 0

    counts = history.stats()
    print(f"\n{len(rows)} recorded move-in(s):", ", ".join(
        f"{k}={v}" for k, v in sorted(counts.items())
    ), "\n")

    for row in rows:
        when = row.trigger_received[:10] or "    ??    "
        print(f"  {when}  #{row.unit:<6} {row.status:<16} since {row.status_changed_at[:16]}")
    print()
    return 0


def run_db(args: argparse.Namespace) -> int:
    """
    Where the history log is, and what state it is in.

    The repository contains no log and never has -- this command is the link to
    it. The log holds unit numbers, statuses and timestamps only, so there is no
    key to report on.
    """
    import subprocess
    import sys as _sys
    from pathlib import Path

    import history

    path = Path(history.path())

    print()
    print("  Local history log")
    print(f"  {path}")
    print()

    if path.exists():
        size = path.stat().st_size
        mode = oct(path.stat().st_mode & 0o777)
        print(f"  size         {size:,} bytes")
        print(f"  permissions  {mode}  (0o600 = only you can read it)")
        counts = history.stats()
        if counts:
            total = sum(counts.values())
            print(f"  contents     {total} move-in(s): "
                  + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    else:
        print("  not created yet — it appears on the first recorded move-in")
    print("  holds        unit numbers, statuses and timestamps; no names, emails or phones")

    if history.LEGACY_DB_PATH.exists():
        print()
        print("  An encrypted resident database from an earlier version is still at")
        print(f"  {history.LEGACY_DB_PATH}")
        print("  Nothing reads it any more. Remove it, its key file and its Keychain item")
        print("  by hand once you no longer need it.")
    print()

    if args.reveal and _sys.platform == "darwin" and path.exists():
        subprocess.run(["open", "-R", str(path)], capture_output=True)
        print("  revealed in Finder.")
        print()

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Housewarm")
    parser.add_argument("--watch", action="store_true", help="run continuously")
    parser.add_argument("--console", action="store_true", help="print text instead of HTML")
    parser.add_argument("--stage", action="store_true", help="prepare actions and show the confirmation")
    parser.add_argument("--demo", action="store_true", help="use the sample mailbox")
    parser.add_argument("--history", action="store_true", help="show what has been recorded")
    parser.add_argument("--pause", action="store_true", help="pause the background watcher")
    parser.add_argument("--resume", action="store_true", help="resume the background watcher")
    parser.add_argument("--db", action="store_true", help="where the local history log is")
    parser.add_argument("--reveal", action="store_true", help="with --db, open it in Finder")
    args = parser.parse_args()

    if args.demo:
        import history

        history.use_throwaway_log()

    import watcher

    if args.db:
        return run_db(args)

    if args.pause:
        watcher.pause()
        print("paused. the background watcher will not poll until resumed.")
        return 0

    if args.resume:
        watcher.resume()
        print("resumed.")
        return 0

    if args.history:
        return run_history(args)

    if args.stage:
        return run_stage(args)

    if not args.watch:
        return run_once(args)

    interval = config.REFRESH_INTERVAL_SECONDS
    print(f"watching — refreshing every {interval // 60} min. ctrl-c to stop.")
    while True:
        if watcher.is_paused():
            print("(paused)")
        else:
            run_once(args)
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nstopped.")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
