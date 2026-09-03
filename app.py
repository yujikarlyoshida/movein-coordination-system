"""
Entry point.

    python app.py                 run once, write board.html
    python app.py --console       print a text report instead
    python app.py --watch         re-run on the configured interval
    python app.py --demo          run against the sample mailbox, no sign-in

--demo exercises the whole pipeline against sample_data.MESSAGES, so the logic can
be shown to somebody without giving them a tenant login, and the board can be
previewed before any Azure setup exists.
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

    # Imported lazily so --demo works with no credentials and no network.
    import graph_client

    client = graph_client.from_environment()
    client.authenticate()

    operator = client.signed_in_as()
    messages = client.list_recent_messages()

    return rules.evaluate(messages), operator


def print_console(results: list[Result]) -> None:
    """Text report, for a terminal or a cron log."""
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
    except Exception as exc:
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Move-in triage")
    parser.add_argument("--watch", action="store_true", help="refresh on an interval")
    parser.add_argument("--console", action="store_true", help="print text instead of HTML")
    parser.add_argument("--demo", action="store_true", help="use the sample mailbox")
    args = parser.parse_args()

    if not args.watch:
        return run_once(args)

    interval = config.REFRESH_INTERVAL_SECONDS
    print(f"watching — refreshing every {interval // 60} min. ctrl-c to stop.")
    while True:
        run_once(args)
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nstopped.")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
