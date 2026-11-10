# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Housewarm: move-in coordination for a multifamily property. It reads the shared "Move ins" mail folder, decides which units still need outreach (an audit found most were already handled by a teammate, so verification comes first), and for those it runs a guided run that fills Outlook, Xfinity Communities and RiseIO in the app's own browser, stopping at each final button. README.md has the full safety model and the reasoning behind it.

## Commands

```
.venv/bin/python menubar.py --board              # the app: menu bar, board window, in-app browser
.venv/bin/python menubar.py --practice           # sample mailbox + practice/ systems, fictional data (demos)
open "Housewarm Demo.command"                    # practice mode; restores the live app when practice quits
MOVEIN_TRACE=1 .venv/bin/python menubar.py --board   # log every board action and check outcome, no values
.venv/bin/python menubar.py --board --probe DIR  # dev: DIR/<site>/cmd.js is evaluated in that tab, result in out.json
                                                 #   sites: outlook, riseio, xfinity, reader, board, practice
.venv/bin/python menubar.py --welcome            # walk the first-run setup again

.venv/bin/python app.py --demo --console         # verdicts only, in the terminal (older CLI pipeline)
.venv/bin/python app.py --history | --db | --pause | --resume

.venv/bin/python -m pytest                       # ~300 tests, ~4 min (WebKit mock pages are the slow part)
.venv/bin/python -m pytest test_rules.py -q      # decision logic only, under a second
.venv/bin/python -m pytest test_guided.py -k welcome   # one area, by substring of test name
```

No linter or build step. Logs from a detached run go to `~/Library/Application Support/MoveInTriage/run.log`.

## Architecture

**Decision pipeline (pure).** `mailsource.owa_messages` turns reader records into message dicts (`id`, `conv_id`, `subject`, `body`, `sender`, `sender_name`, `received`) → `triggers.find_triggers` → `rules.evaluate` → `Result` per unit with a `Verdict` (NEEDS_OUTREACH, UNCLEAR "Check", SKIPPED_CORPORATE, HANDLED). `triggers.py` and `rules.py` do no I/O; keep it that way. After evaluation, `menubar._with_marks` applies two overlays: units the user marked done (`history.marked_done`, keyed by the lease email's id, not the unit) and leases whose move-in date has passed (`rules.with_past_move_ins`, skipped in demo/practice). Both become HANDLED with their own `Evidence` kind so the board says why.

**The app (`menubar.py`, PyObjC).** `TriageDelegate` owns three background threads: the live loop (reads mail every 15 min, every 60 s while there is a backlog), `_watch_connections` (sign-in state every 20 s, reload-proves RiseIO/Xfinity sessions every 5 min, notifies on sign-out), and per-action workers (guided runs, opening a lease email). Anything that runs page scripts must be on a worker thread: `outlook_web.run_js` hands the call to the main thread and waits, and doing that from the main thread inside a WebKit message handler freezes the window.

- **`browser.py`**: one window, three tabs (Outlook, RiseIO, Xfinity) sharing the default WebKit data store; the user signs in there (MFA, SSO). The mail reader (`outlook_web.OutlookWebReader`) uses the same Outlook tab, so it must stay hands-off: `menubar._outlook_hands_off` covers user input in that tab (15 s), guided runs, and an email the app opened for the user while that tab stays focused. Sign-in state comes from the address plus a visible password box (`browser.classify`).
- **`board.py` + `window.py`**: the board is an HTML file in App Support loaded in a WKWebView. Pages mark a live part (`<div id="live">…<!--/live-->`); `window.refresh_if_open` swaps only that part, keeping scroll, focus and typed values, and fully reloads only when the shell changes. Things that update often (the check progress bar) live outside the live part and are moved with `window.board_js`, because a swap re-runs the board's entrance animations. Page → app messages go through `window.webkit.messageHandlers.triage`; an action must be in `window.BOARD_ACTIONS` (and `_VALUE_LIMITS` if its value is long) or it is refused. Buttons use document-level delegated listeners so they survive swaps.
- **`confirm_sheet.py`**: the per-unit sheet (resident details are injected from memory, never written to disk). `validate` re-checks everything in Python. It also carries `auto_final`, the systems whose final button the app may press for this unit.
- **`guided.py`**: guided runs on the live systems. Each step fills what it can verify and calls `_your_turn` for the rest. Opt-in auto press (Send, Save resident, Create New Resident; never Confirm Move-In) requires an exact read-back, one button found by name, a cancellable countdown (`_go_ahead`), a single press, and the system's own confirmation; outcomes go to `actions.log` (units only). Xfinity is Prism web components (shadow DOM, buttons drawn late, screens drawn in stages); helpers in the `_DEEP` script (`G.all`, `G.native`, `G.press`) pierce shadow roots.
- **`autofill.py` + `practice/`**: the same flow against practice pages, used by practice mode, at a watchable pace (`DEMO_PACE`); tests run at full speed.
- **`settings.py`, `senders.py`, `welcome.py`, `welcome_template.py`**: per-user setup in App Support (0600): settings, the lease sender list (with "shared account" entries), the captured welcome template. `apply()` functions push them into `config` attributes, which is all the rest of the app reads.

## Decision rules that are easy to break

- A trigger needs **both** a sender on the lease sender list **and** a move-in phrase or the field block (`config.TRIGGER_PHRASES` / `TRIGGER_FIELD_MARKERS`). For a shared account (`config.TEAM_ACCOUNTS`) only its own text, not the quoted lease, can start a trigger.
- Handled evidence: a reply in the lease's thread (same `conv_id`) from anyone not a lease sender, or from a shared account; the newest such reply decides, and one that asks a question or says not done (`NOT_DONE_PHRASES`) gives UNCLEAR. Also completion notes that mention the unit, and an already-sent welcome. Read status is never evidence.
- Evidence must be dated at or after the trigger it clears, less `EVIDENCE_CLOCK_SKEW_MINUTES` (5); unit numbers are reused. Unreadable dates never count as evidence; a trigger with an unreadable date is UNCLEAR. The newest trigger per unit wins.
- `rules.is_announcement` layers: substring `HANDLED_PHRASES`, regex `ANNOUNCEMENT_PATTERNS` (past-tense "added", so "please add" does not count), `is_terse_completion`. Never add "complete" to the phrase list.
- Units are normalised to four digits (`#735` → `0735`) before comparing.
- Corporate leases (`triggers.corporate_signals`) are checked first and never staged; weak signals still skip but ask for a check.
- A false HANDLED is the dangerous direction: when unsure, UNCLEAR.

## Constraints

- **Public repo, synthetic data only.** Real senders and the property name live in gitignored `config_local.py` and in App Support files, never in code, tests, comments or commits. Fixtures use `.invalid` addresses and 555-01xx phones.
- **No resident data at rest or on display.** Board, menu bar, notifications, traces and logs carry unit numbers and statuses only; problems name residents by position. `test_no_resident_data_reaches_the_results` and `test_no_resident_data_reaches_the_log_file` enforce it. Probes of live pages return structure only (labels, counts, flags), never values.
- **Writes only by explicit per-unit instruction.** The mail reader only selects the folder and opens threads (opening marks read; accepted for this folder). No `--yes` flag anywhere. Click by element or name, never coordinates. Never store credentials or tokens, and never read them from a page.
- **Testing against live sites:** probe the live page read-only (`--probe`), reproduce the finding as behaviour in a mock under `testpages/`, write a test that fails without the fix. Tests pin config and use scratch paths (`MOVEIN_HISTORY_PATH`, `MOVEIN_SENDERS_PATH`, `settings.use_throwaway()`, `guided.ACTION_LOG`) before importing app modules; keep that pattern so no test touches real state.
- `graph_client.py` exposes GET only (`Mail.Read`, `User.Read`).

## macOS / PyObjC gotchas

`rumps` silently fails on Python 3.14, so `menubar.py` uses `NSStatusBar` directly with an explicit numeric length and an image. Argument-taking helpers on `NSObject` subclasses need `@objc.python_method`. An Objective-C class can only be registered once per process: define delegate/handler classes once and reuse them (`window._make_delegate`, `_message_handler`). WebKit throttles hidden tabs, so Xfinity's components may not draw until its tab is on screen.

## Untracked clutter

`*.log`, `board.html`, `index.html`, `Publish Site.command` and the `Diagnose*.command` / `Check.command` scripts are local leftovers, not part of the product. The user's `.gitignore` edits are uncommitted on purpose.
