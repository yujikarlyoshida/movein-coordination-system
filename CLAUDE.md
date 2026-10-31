# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Housewarm: move-in coordination. It watches a shared Outlook mailbox and reports which units still need outreach, because an audit found most move-ins were already handled by a colleague. Verification comes first. For a unit that needs outreach, a guided run (`guided.py`) fills Outlook, Xfinity Communities and RiseIO in the app's own browser (`browser.py`), stopping at each final button; pressing Send / Save resident / Create New Resident itself is opt-in per unit, after an exact read-back (repeated after a countdown). The mail reader never sends; never Confirm Move-In; never press by coordinates; never log a press as done without the system confirming it. See README.md for the full safety model.

## Commands

```
pip install -r requirements.txt                    # add -r requirements-mac.txt for the menu bar app

python app.py --demo --console                     # whole pipeline on the synthetic mailbox, no credentials
python app.py --stage --demo                       # prepare actions, stop at the confirmation screen
python app.py --watch                              # poll continuously (LaunchAgent does this in production)
python app.py --history | --db | --pause | --resume

python -m pytest test_rules.py test_history.py test_outlook_web.py -v   # preferred runner
python -m pytest test_rules.py -k unit_padding     # single test (substring match on name)
python test_rules.py                               # also runs standalone, no pytest needed (same for the other two)
python outlook_web.py                              # open the in-app Outlook sign-in window
```

There is no linter or build step. `Install Move-In Triage.command` and `build_dmg.sh` package the macOS app.

## Architecture

Data flows one way: `mailsource` → `triggers` → `rules` → `staging` → single confirmation. `history` logs unit and status.

- **`triggers.py` and `rules.py` are pure**: functions over already-fetched message dicts (`id, subject, body, sender, received`). No network, I/O or credentials. Keep them that way, since that is what makes the decision logic testable offline.
- **`mailsource.py`** is the only place mail enters. Backends: `graph` (target), `connector` (works only while an assistant session is open), `demo`, and Outlook on the web (in progress). `get_source()` falls back to demo when Graph credentials are missing. A new data source goes here. `owa_messages()` converts Outlook-web extractions into message dicts: dates read as local time, ids from conversation id plus a hash of sender, time and body start, never pane position.
- **`outlook_web.py` + `owa_extract.js`**: the app's own WKWebView window; the user signs in themselves (MFA included). It identifies itself honestly (WebKit's user agent plus `MoveInTriage/1.0`); never disguise it to get past an access rule. `owa_extract.js` reads the open thread from the rendered page only. Never read Outlook's tokens from the page or call Microsoft APIs with them. Click by element, never by coordinates; the only interactions are selecting the folder and opening threads. The reading pane shows sender display names for directory users, so `TRIGGER_SENDER_NAMES` (real values only in `config_local.py`) backs up `TRIGGER_SENDERS`; an address, when present, always decides.
- **`graph_client.py`** exposes `GET` only, on purpose. Do not add `_post`/`_patch`/`_delete`; scopes are `Mail.Read` + `User.Read`.
- **`history.py`**: standard-library `sqlite3` log of trigger message id, unit, status and timestamps. **No resident data, subjects or senders**, which is why it needs no encryption or key. `record` is idempotent on message id; `status_changed_at` moves only on a real status change. `MOVEIN_HISTORY_PATH` overrides the path (tests); `use_throwaway_log()` puts demo runs in memory. Residents go from `triggers` straight into the staged draft and are never stored. Do not add a column that can hold PII; `test_no_resident_data_reaches_the_log_file` greps the raw file.
- **`staging.py`** composes the welcome draft and the portal and resident-app payloads and renders one confirmation. There is deliberately no `--yes` flag, and drafts are never sent.
- **`watcher.py`** polls; pause is a persisted file, not an in-memory flag, so it survives restarts.

### Decision rules that are easy to break

- A trigger needs **both** a sender on `TRIGGER_SENDERS` **and** a move-in event phrase (or the field-block fallback). Either alone gives false positives or re-fires on quoted replies.
- "Already handled" evidence matches **body text, not thread position**, and the trigger is excluded from its own evidence by id.
- Evidence must be **received at or after the trigger it clears**, less `EVIDENCE_CLOCK_SKEW_MINUTES` (5) for clock disagreement between mailboxes. Unit numbers are reused, so an older announcement must never clear a newer lease. Evidence with an unreadable date never counts; a trigger with an unreadable date is `UNCLEAR`. The newest trigger per unit wins, so an updated or corrected resend is judged only on mail after the resend; older matching mail adds a note but does not count. Do not widen the skew past a few minutes: the fastest real hand-off was 3 minutes.
- `rules.is_announcement` has three layers: substring `HANDLED_PHRASES`, regex `ANNOUNCEMENT_PATTERNS` (anchored on past-tense "added", so "please add…" does not count), and `is_terse_completion` (first non-empty line must be exactly a `TERSE_COMPLETIONS` word). Never add "complete" to the phrase list.
- Units are normalised to four digits (`#735` → `0735`) before any comparison.
- A false `HANDLED` is the dangerous direction; when unsure, prefer `UNCLEAR`.
- **Corporate leases are out of scope**: `triggers.corporate_signals` reads strong signals (a `POC:`/`POC-` label, a company word in `Name:` or the subject, from `CORPORATE_NAME_WORDS`) and weak ones (`Phone: N/A`, a shared mailbox address from `CORPORATE_SHARED_MAILBOXES`). Any signal gives `SKIPPED_CORPORATE`, checked before every other verdict; weak-only signals say "possibly corporate, check by hand". Skipped units are shown, never staged, never counted as needing outreach. Notes name signals, never the company or contact. The property's own name is stripped from subjects first. Test with synthetic fixtures only.
- Read status is never evidence. Handled means a reply in the thread (team SOP: reply-all when done), not whether a message was opened.

## Constraints

- **Public repo, synthetic data only.** Real senders and the property name live in the gitignored `config_local.py`, which `config.py` imports last so it overrides the example.com values. Do not put real names, units, phone numbers or addresses in code, tests, comments or commits.
- **Opening a thread in Outlook on the web marks it read.** The owner accepted this for the Move ins folder (decision 2026-10-16): read status may change there, nothing else may. No sending, replying, moving, flagging or deleting.
- **No resident names in any display surface** (board, menu bar, notifications, logs, problem strings). Problems refer to residents by position ("the 2nd resident listed"). A test enforces this.
- The history log lives at `~/Library/Application Support/MoveInTriage/history.sqlite3` (0600), outside the repo. Tests and demo runs must never touch it or the real pause file. A `residents.sqlite3` and `db.key` there are leftovers from the removed encrypted store; nothing reads them.
- Both test files pin `config.TRIGGER_SENDERS` and `PROPERTY_NAME` and a scratch `MOVEIN_HISTORY_PATH` before importing modules, so `config_local.py` cannot change results and no test writes real state. Keep that pattern in new tests. Demo runs pin the fixture roster only for the run (`sample_data.fixture_config()`).
- The resident app's add form has no unit field and a birthday range of 1923–2023, so `PLACEHOLDER_BIRTHDAY` is 1 Jan 1923.
- RiseIO and Xfinity have no documented APIs. Do not build write paths into them. Their verification scripts are deliberately kept out of the repo.

## macOS menu bar gotchas

`rumps` silently fails on Python 3.14, so `menubar.py` calls `NSStatusBar` directly. Use an explicit numeric status item length (variable length is zero-width on macOS 26), give the item an image, and mark argument-taking helpers on `NSObject` subclasses `@objc.python_method`. The installer copies `*.py` only; there are no packages to copy. It import-checks the pipeline at install time.

## Untracked clutter

`*.log`, `board.html` and the `Diagnose*.command` / `Check.command` scripts are local debugging leftovers, not part of the product.
