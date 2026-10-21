# Housewarm

Move-in coordination for a multifamily property. It watches the team's shared
move-in mailbox and answers one question, continuously:

> **Which move-ins still need me, and which has a colleague already handled?**

For the ones that need you, it then does the typing: it fills the welcome email
in Outlook, adds the resident in Xfinity Communities, and creates them in RiseIO,
stopping at every final button for you to press, or pressing Save and Create
itself where you ticked it for that unit.

```
$ python app.py --demo --console

Housewarm — 5 triggered unit(s)

NEEDS OUTREACH (2)
  #0735  New Resident Move In - #735
  #1408  Adding a roommate - #1408
      note: the 1st resident listed: no phone number given
      note: the 2nd resident listed: no apartment number given; assuming 1408 from the rest of the message
      note: the 2nd resident listed: no phone number given

SKIPPED, CORPORATE (1)
  #1520  corporate lease (POC label, company in the Name field, company in the subject, phone given as N/A, shared or company contact address); skipped, out of scope

ALREADY HANDLED (2)
  #0901  welcome email already sent — Welcome Home # 0901
  #1204  colleague reported it done — 1204 - added to all platforms
```

---

## The interesting part: I built the wrong thing first

The obvious tool here drafts the welcome emails automatically. A lease is
signed, a trigger email lands in a shared inbox, software writes the outreach.

Before building it, I audited six consecutive move-ins by hand — checking the
mailbox, the internet-provider portal, and the tracking spreadsheet for each.

**In five of the six, a colleague had already done the work.** Once within three
minutes of the trigger arriving.

An auto-drafting tool would have produced a duplicate resident contact five times
out of six. Not a small bug — the tool would have been actively worse than
nothing, and the failure would have been invisible to the person running it.

So the design inverted. Verification became the primary function; preparing
outreach became a conditional step that only runs when nothing else has
happened. That inversion is the whole project, and it still holds now that the
app does the outreach too: nothing is offered for a unit until the thread has
been checked.

---

## How it decides

```
     mail
      │
      ▼
  ┌─────────────────────────────────────────┐
  │ TRIGGER?  sender on the leasing roster  │  ← both required
  │           AND a move-in event           │
  └─────────────────────────────────────────┘
      │
      ▼
  ┌─────────────────────────────────────────┐
  │ PARSE     residents · unit · dates      │
  │           unit padded to four digits    │
  └─────────────────────────────────────────┘
      │
      ▼
  ┌─────────────────────────────────────────┐
  │ EVIDENCE? a teammate's reply in thread  │
  │           OR a completion note          │
  │           OR welcome email already sent │
  └─────────────────────────────────────────┘
      │
      ├── found ──────────────────> HANDLED         (say nothing)
      ├── a question, or "not yet" > CHECK           (surface it)
      ├── corporate lease ────────> SKIPPED         (never staged)
      ├── unreadable ─────────────> CHECK
      └── none ───────────────────> NEEDS OUTREACH
```

**Trigger detection requires two independent conditions.** Sender alone sweeps in
every unrelated message from the leasing desk. Event wording alone matches a
colleague replying in-thread and quoting the original — which would re-fire on
work already underway, the exact duplicate this exists to prevent.

**Evidence matches on body text as well as thread position.** A reply from
someone else in the thread counts as handled, but a reply that asks a question
or says the work isn't done yet ("on it", "will do", "tomorrow", "waiting on")
shows as Check instead: a false *handled* is the dangerous direction. And a
colleague once sent a standalone message titled *"0612 - added to all
platforms"* that replied to nothing, so body matching catches that shape too.

**Unit numbers normalise to four digits on read.** One observed trigger email
wrote `#735` and `0812` in the same paragraph. Without a canonical form, `735`
and `0735` read as different apartments and every duplicate check silently stops
working — the worst kind of bug, because the tool keeps confidently reporting.

**Corporate leases are skipped, never staged.** When the signals are weak, it
still skips but asks for a check. When unsure, skip and flag.

---

## From telling you to doing it with you

The board lists every unit with its verdict. A unit that needs outreach has a
**Prepare** button, which opens one confirmation sheet: the residents as parsed,
any gap highlighted as a field to fill (a missing phone says which system it
blocks), the steps to run, and one button.

A **guided run** then works through the systems in the app's own browser:

| System | The app does | The final button |
|---|---|---|
| Outlook | Opens a new message; fills To, Cc, Subject and the body from your own captured template, names filled in; checks each field reads back | **Send** — always yours |
| Xfinity Communities | Finds your property and its residents page; fills contact details, searches the unit and picks the one exact match, types the move-in date | **Save resident** — yours, or the app's if ticked |
| RiseIO | Fills the Add Resident form, a placeholder birthday, the activation box as you chose | **Create New Resident** — yours, or the app's if ticked; **Confirm Move-In** stays yours |

When it is your turn, the browser's toolbar says exactly what to check and press,
with Done and Skip step beside it. Anything the app cannot verify — a unit that
matches two search results, a screen it has not seen before — is handed to you
with a sentence saying what to do, never guessed.

**Pressing Save and Create itself is opt-in, per unit, per system,** ticked on the
sheet. The app presses only when:

- every value reads back exactly from the page (the Xfinity review, the RiseIO form),
- exactly one button by that name is showing, found by its name, never by screen position,
- a five-second countdown passes without you pressing Skip step.

It presses once, never twice, then waits for the system's answer. An error banner
stops the step with the system's own words; no answer stops it too, with a note
to check before trying again. Every press is written to `actions.log` with the
time, unit, system and outcome — no names, emails or phones.

---

## Constraints that shaped it

**It writes only where you said so.** The mail reader never sends, replies or
moves anything: its only clicks are selecting the folder and opening threads.
Outlook gets a draft and nothing more; Send is always a person's. Xfinity and
RiseIO are saved only by your own press, or by the app's single press after the
checks above for a unit you ticked. There is no `--yes` flag anywhere: a plan
approvable from the same command that produced it is one typo from being
approved by accident.

**It keeps its hands off while you work.** The reader shares the Outlook tab
with you, so it stops before every click if you have typed, clicked or scrolled
there in the last fifteen seconds, and picks up when you stop.

**Resident data is never stored.** Names, emails and phone numbers are parsed
out of the trigger, shown on the confirmation sheet (held in memory, never
written to a file), typed into the systems you confirmed, and gone when the run
ends. What persists is a local history log of unit numbers, statuses and
timestamps, the units you marked done, and the press log.
`test_no_resident_data_reaches_the_log_file` runs the whole pipeline on the
sample mailbox, then reads the log file as bytes and greps for every name,
address, phone, sender and subject in it, so the claim is checked against the
disk rather than asserted in a comment.

Nothing renderable — board, menu bar, notification, logs — carries more than a
unit number and a status, which `test_no_resident_data_reaches_the_results`
enforces separately.

**This is the second time the design arrived here.** The first version held no
resident data at all, after management declined an earlier approach on
data-privacy grounds. A later version stored residents in an encrypted local
database (Django, Fernet-encrypted columns, a Keychain-held key) to support
tracking. On review, nothing read that data back except the draft being composed
in the same run, so the encrypted store was removed: no personal data at rest is
a stronger guarantee than encrypted personal data at rest.

The same rule applies to this repository. Every name, address, phone number and
unit number in the source and tests is invented — residents are Alpha through
Hotel, addresses are on the reserved `.invalid` TLD, phones use the `555-01xx`
fiction block. What is modelled from experience is the *shape* of the messages
and pages: the drifting field labels, the inconsistent unit formatting, the
blank field mid-block. That shape is what the parser has to survive, and it
identifies nobody.

**The decision logic is pure.** `rules.py` and `triggers.py` are functions over
already-fetched messages — no network, no credentials, no I/O. So the part most
worth trusting is verifiable in about a second on any machine.

**It never handles a password.** Each system is signed in by you, in the app's
own browser, MFA and single sign-on included. The app stores no credentials or
tokens and never reads them from a page.

---

## Testing against sites you cannot test against

Outlook, Xfinity and RiseIO have no test environments the app can use, and two
of them publish no API. So every assumption about them comes from read-only,
structure-only probes of the live pages — element names, labels, counts, never a
value — and each finding becomes two things: a behaviour in a mock page under
`testpages/`, and a test that fails without the fix.

Some of what the live pages taught it:

- Outlook leaves an address typed with a trailing semicolon as loose text and
  flags it. Each recipient is now typed alone and its exact suggestion clicked.
- Someone in your company directory appears in To by name only; their address is
  nowhere on the page. So the check confirms the exact suggestion was picked and
  added exactly one recipient, rather than looking for the address.
- Xfinity draws its next wizard screen in stages, and draws buttons only while
  its tab is on screen. A screen is classified only once it has settled, and a
  button is pressed only once it is drawn.
- After a restart Xfinity opens on the organisation's property list, with the
  properties as cards that have no links. The app finds yours and remembers the
  residents page.
- A company sign-in page in front of Outlook read as "still loading", forever.
  Any page that is not Outlook now reads as a sign-in, and the window comes
  forward for you.

The mocks run in real WebKit, the same engine as the app. 280 tests in all.

---

## Running it

```bash
python menubar.py --board          # the app: menu bar, board, browser
python menubar.py --practice       # sample mailbox and practice systems; safe to show anyone
open "Housewarm Demo.command"      # the same, closing and restoring the live app around it

python app.py --demo --console     # verdicts only, in the terminal
python app.py --history            # units, statuses, when they changed

python -m pytest                   # 280 tests
MOVEIN_TRACE=1 python menubar.py   # log every button press and check outcome (no values)
```

The first launch opens a welcome page that sets it up for whoever runs it: your
name and email, the property, the mail folder, the Cc address, and your own
welcome email captured as the template. A setup exports as a profile that a
teammate imports in one step.

**Nothing private lives in the repository, and never has.** Settings, the
sender list, the template, the history log and the press log all live in
`~/Library/Application Support/MoveInTriage/`, outside the project directory,
created owner-only (`0600`). Practice and demo runs keep theirs in memory or a
scratch folder.

**It runs continuously.** It checks every fifteen minutes until paused. Pausing
writes a file rather than setting a flag, so it survives restarts — a pause that
silently expired overnight would be worse than none.

---

## Layout

| File | Does what |
|---|---|
| `triggers.py` · `rules.py` | What counts as a trigger; evidence and verdicts. Pure, no I/O |
| `outlook_web.py` · `owa_extract.js` | Reads the move-in folder through Outlook on the web, hands off while you use it |
| `browser.py` | The app's browser: Outlook, RiseIO and Xfinity tabs, sign-in states, the your-turn bar |
| `guided.py` | Guided runs on the live systems, and the opt-in final press |
| `autofill.py` · `practice/` | The same flow on practice copies of the three systems |
| `confirm_sheet.py` | The per-unit confirmation sheet and its checks, run again in Python |
| `board.py` · `hero.py` · `window.py` | The board, its design, the native window and its live updates |
| `welcome.py` · `settings.py` · `welcome_template.py` | First-run setup, settings, profiles, the captured template |
| `menubar.py` · `menu_model.py` | The macOS menu bar app that ties it together |
| `history.py` | Local log of units, statuses and timestamps. No PII |
| `staging.py` · `mailsource.py` · `graph_client.py` · `app.py` | The command-line pipeline; a read-only Graph client |
| `sample_data.py` · `testpages/` | The fictional mailbox, and mock pages shaped from the live sites |

---

## Field notes

Things that cost real time and are not findable by reading the code.

**`rumps` silently fails on Python 3.14.** The usual macOS menu bar wrapper. Its
`NSApplication` delegate never fires, so no status item is ever created — no
error, no traceback, nothing in the menu bar. Replaced with direct `NSStatusBar`
calls.

**`NSVariableStatusItemLength` yields a zero-width item on macOS 26.** AppKit
returns an `NSSceneStatusItem` that reports `isVisible=True` with the correct
title while occupying no pixels. Set an explicit numeric length.

**pyobjc exposes every method on an `NSObject` subclass as a selector,** and
derives the argument count from underscores in the name. A plain helper taking
arguments fails at class-creation time with `BadPrototypeError`. Mark them
`@objc.python_method`.

**Running page scripts from a WebKit message handler on the main thread
freezes the window.** Anything that waits on a page now runs on a worker
thread and hands results back to the main thread.

**Reloading the board on every check made it flicker and lose what you were
typing.** Pages mark a live part, and only that part is swapped in place,
keeping focus, typed values and scroll position.

**Outlook moved to `outlook.cloud.microsoft`.** An allow-list of Outlook hosts
without it read real Outlook as a sign-in page. A page showing Outlook's folder
list is now Outlook at any address.

---

## Status

Live on all three systems. Mail reading, verdicts and the board run against the
real mailbox daily. Guided runs fill the real Outlook, Xfinity and RiseIO; the
recipient handling was confirmed on a live welcome draft.

Not yet proven live: the app's own Save resident and Create New Resident presses
(built and tested on the mocks, off unless ticked), and RiseIO's Move into Unit
dialog, which appears only after a real resident is created and stays a
your-turn step until it has been mapped.

## Licence

MIT — see `LICENSE`.
