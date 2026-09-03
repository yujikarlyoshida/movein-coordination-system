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

        return rules.evaluate(sample_data.MESSAGES), "demo mode (no sign-in)"

    import mailsource

    source = mailsource.get_source()
    available, reason = source.is_available()
    if not available:
        raise RuntimeError(f"mail source '{source.name}' unavailable — {reason}")

    messages = source.list_recent_messages(days=config.LOOKBACK_DAYS)
    return rules.evaluate(messages), f"{source.name} source"


def print_console(results: list[Result]) -> None:
    """Text report, for a terminal or a log."""
    needs = [r for r in results if r.verdict is Verdict.NEEDS_OUTREACH]
    unclear = [r for r in results if r.verdict is Verdict.UNCLEAR]
    handled = [r for r in results if r.verdict is Verdict.HANDLED]

    print(f"\nMove-in triage — {len(results)} triggered unit(s)\n")

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
        import sample_data

        messages = sample_data.MESSAGES
    else:
        import mailsource

        source = mailsource.get_source()
        available, reason = source.is_available()
        if not available:
            print(f"error: mail source '{source.name}' unavailable — {reason}", file=sys.stderr)
            return 1
        messages = source.list_recent_messages(days=config.LOOKBACK_DAYS)

    plans = staging.collect(messages)
    print()
    print(staging.render_confirmation(plans))
    return 0


def run_history(args: argparse.Namespace) -> int:
    """
    What the tool has recorded. Reads only plaintext columns -- no resident
    name or address is decrypted to print this.
    """
    import store

    store.migrate()
    rows = store.history(limit=50)

    if not rows:
        print("nothing recorded yet.")
        return 0

    counts = store.stats()
    print(f"\n{len(rows)} recorded move-in(s):", ", ".join(
        f"{k}={v}" for k, v in sorted(counts.items())
    ), "\n")

    for row in rows:
        when = row.trigger_received.strftime("%Y-%m-%d") if row.trigger_received else "  ??  "
        print(f"  {when}  #{row.unit:<6} {row.status:<16} {row.trigger_subject[:52]}")
        if row.evidence_detail:
            print(f"              └─ {row.evidence_detail}")
    print()
    return 0


def run_db(args: argparse.Namespace) -> int:
    """
    Where the database is, and what state it is in.

    The repository contains no database and never has -- this command is the
    link to it. It prints the path, reports whether the key is in the Keychain
    or has fallen back to a file, and offers to reveal it in Finder.

    Deliberately prints no resident data. Knowing where the database is and
    knowing what is in it are different permissions, and only the first one
    belongs in a command you might run while someone is looking over your
    shoulder.
    """
    import subprocess
    import sys as _sys

    import keystore
    import store

    store.migrate()
    path = store.DB_PATH

    print()
    print("  Local encrypted database")
    print(f"  {path}")
    print()

    if path.exists():
        size = path.stat().st_size
        mode = oct(path.stat().st_mode & 0o777)
        print(f"  size         {size:,} bytes")
        print(f"  permissions  {mode}  (0o600 = only you can read it)")
    else:
        print("  not created yet — it appears on the first recorded move-in")

    # Where the key is matters more than where the database is. An encrypted
    # file whose key sits beside it is a locked door with the key in the lock.
    if keystore.FALLBACK_KEY_FILE.exists():
        print(f"  key          {keystore.FALLBACK_KEY_FILE}")
        print("               WARNING: on the same disk as the database.")
        print("               The Keychain was unavailable when the key was made.")
    else:
        print(f"  key          macOS Keychain — service '{keystore.KEYCHAIN_SERVICE}'")
        print("               not on disk, not in this repository, not recoverable if lost")

    counts = store.stats()
    if counts:
        total = sum(counts.values())
        print(f"  contents     {total} move-in(s): "
              + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print()
    print("  Nothing here is in git. `git log --all` shows no database in any commit.")
    print()

    if args.reveal and _sys.platform == "darwin" and path.exists():
        subprocess.run(["open", "-R", str(path)], capture_output=True)
        print("  revealed in Finder.")
        print()

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Move-in triage")
    parser.add_argument("--watch", action="store_true", help="run continuously")
    parser.add_argument("--console", action="store_true", help="print text instead of HTML")
    parser.add_argument("--stage", action="store_true", help="prepare actions and show the confirmation")
    parser.add_argument("--demo", action="store_true", help="use the sample mailbox")
    parser.add_argument("--history", action="store_true", help="show what has been recorded")
    parser.add_argument("--pause", action="store_true", help="pause the background watcher")
    parser.add_argument("--resume", action="store_true", help="resume the background watcher")
    parser.add_argument("--db", action="store_true", help="where the local encrypted database is")
    parser.add_argument("--reveal", action="store_true", help="with --db, open it in Finder")
    args = parser.parse_args()

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
