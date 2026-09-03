# Move-In Coordination System

A read-only triage tool for resident move-ins at a multifamily property. It
watches a shared team mailbox and answers one question, continuously:

> **Which move-ins still need me, and which has a colleague already handled?**

It reads email. That is all it reads. It never sends, replies, or modifies
anything.

```
$ python app.py --demo --console

Move-in triage — 4 triggered unit(s)

NEEDS OUTREACH (2)
  #0735  New Resident Move In - #735
  #1408  Adding a roommate - #1408
      note: Resident Echo: no apartment number given; assuming 1408 from the rest

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
outreach became a conditional afterthought that only runs when nothing else has
happened. That inversion is the whole project.

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
  │ EVIDENCE? colleague's completion note   │
  │           OR welcome email already sent │
  └─────────────────────────────────────────┘
      │
      ├── found ──────> HANDLED     (say nothing)
      ├── unreadable ─> UNCLEAR     (surface it)
      └── none ───────> NEEDS OUTREACH
```

**Trigger detection requires two independent conditions.** Sender alone sweeps in
every unrelated message from the leasing desk. Event wording alone matches a
colleague replying in-thread and quoting the original — which would re-fire on
work already underway, the exact duplicate this exists to prevent.

**Evidence matches on body text, not thread position.** The obvious
implementation asks "did anyone reply to the trigger?" That misses a real case:
a colleague sent a standalone message titled *"0612 - added to all platforms"*
that replied to nothing. Body matching catches both shapes.

**Unit numbers normalise to four digits on read.** One observed trigger email
wrote `#735` and `0812` in the same paragraph. Without a canonical form, `735`
and `0735` read as different apartments and every duplicate check silently stops
working — the worst kind of bug, because the tool keeps confidently reporting.

---

## Constraints that shaped it

**Read-only, by construction.** The Graph client requests `Mail.Read` and
`User.Read` and implements no HTTP verb but `GET`. There is no `_post`, no
`_patch`, no `_delete`. Adding a write would be a deliberate act, not an
accident.

**Resident data is stored, encrypted, and confined to two places.** Names,
emails and phone numbers live in a local SQLite database with those columns
encrypted individually (Fernet — AES-128-CBC with an HMAC). The key is held in
the macOS Keychain, never beside the data. `test_pii_is_not_readable_in_the_raw_file`
opens the database as bytes and greps for the names it just wrote, so the claim
is checked against the disk rather than asserted in a comment.

Column-level rather than whole-file encryption, deliberately. Encrypting the
file protects it until the app opens it, then everything is plaintext in memory
and in any log line that prints a row. Encrypting columns means a name is
ciphertext everywhere except the one attribute access that needs it. The cost is
real and worth naming: **you cannot query an encrypted column.** Fernet uses a
random IV, so `WHERE first_name = ?` can never match. Everything the app filters
on — unit, status, dates — is deliberately left plaintext, and none of it
identifies a person alone.

So PII reaches exactly two destinations: the encrypted database, and the draft
addressed to those residents. Nothing renderable — board, menu bar, notification,
log — carries more than a unit number and a subject line, which
`test_no_resident_data_reaches_the_results` enforces separately.

**This was not the original design.** The first version held no resident data at
all, after management declined an earlier approach on data-privacy grounds. That
constraint produced a better architecture and the no-PII rule is still enforced
across every display surface. Storing records was a later, deliberate reversal to
support tracking, and it is worth being straightforward about what changed:
personal data now sits at rest on one machine. The mitigations — encryption at
rest, a Keychain-held key, `0600` file permissions, a database path outside the
repository, and a gitignore that catches it anyway — reduce that exposure but do
not erase it.

The same rule applies to this repository. Every name, address, phone number and
unit number in the source and in the examples below is invented — residents are
Alpha through Hotel, addresses are on the reserved `.invalid` TLD, phones use the
`555-01xx` fiction block. What is modelled from experience is the *shape* of the
messages: the drifting field labels, the inconsistent unit formatting, the blank
field mid-block. That shape is what the parser has to survive, and it identifies
nobody.

**The decision logic is pure.** `rules.py` and `triggers.py` are functions over
already-fetched messages — no network, no credentials, no I/O. So the part most
worth trusting is verifiable in about a second on any machine, with no tenant and
no mailbox.

---

## Running it

```bash
python app.py --demo --console     # verdicts only
python app.py --stage --demo       # prepare everything, show the confirmation
python app.py --history            # what has been recorded, no PII decrypted
python app.py --pause              # stop the background watcher (persists)

python app.py --db                 # where the local encrypted database is
python test_rules.py               # 33 tests — decision logic
python test_store.py               # 13 tests — encryption, staging, idempotence
```

**The database is not in this repository and never has been.** It lives at
`~/Library/Application Support/MoveInTriage/residents.sqlite3`, outside the
project directory entirely, so no command run inside the repo can sweep it into
a commit — `git log --all --diff-filter=A` shows no database file in any commit
in the history. `.gitignore` blocks `*.sqlite3` and `*.key` as a second line of
defence, and the file is created `0600`.

`--db` is the link to it: path, size, permissions, and where the key is. It
prints no resident data, because knowing where the database is and knowing
what's in it are different permissions, and only the first belongs in a command
you might run with someone looking over your shoulder.

The key is in the macOS Keychain, not beside the data — an encrypted file whose
key sits next to it is a locked door with the key in the lock. It is also not
recoverable: lose the Keychain item and the contents are gone, by design.

**Staging prepares; it does not commit.** `--stage` composes the draft in full,
resolves recipients, builds the portal payloads, and stops at one confirmation
screen showing all of it. Nothing is sent, saved or submitted before you approve,
and there is deliberately no `--yes` flag: a plan approvable from the same command
that produced it is one typo from being approved by accident. Drafts are never
sent even after approval.

**It runs continuously.** A LaunchAgent starts the watcher at login and it polls
on an interval until paused. Pausing writes a file rather than setting a flag, so
it survives restarts — a pause that silently expired overnight would be worse
than none, because you would believe the tool was quiet while it had resumed.

Polling rather than Graph webhooks, because a push subscription needs a public
HTTPS endpoint that a laptop behind a router does not have. Given that colleagues
have closed move-ins in as little as 23 minutes, arriving a few minutes late costs
nothing — the job is to notice what is still open, not to race anyone.

Demo mode needs no credentials and no network. For live use, set
`MOVEIN_CLIENT_ID` and `MOVEIN_TENANT_ID` from an Azure app registration; the
sign-in is device-code, so the app never handles a password.

There is also a macOS menu bar app — `Install Move-In Triage.command` builds a
self-contained bundle and registers a LaunchAgent. The menu bar shows `● 3` when
three units need outreach, `⚠ 1` when a trigger could not be read, `✓` when
nothing is outstanding.

---

## Layout

| File | Does what |
|---|---|
| `triggers.py` | What counts as a trigger; parsing residents out of one |
| `rules.py` | Evidence detection and verdicts. Pure, no I/O |
| `staging.py` | Prepares every action in full; the single confirmation gate |
| `watcher.py` | The background poll loop, notifications, persistent pause |
| `mailsource.py` | One interface, three backends: Graph, connector, demo |
| `store.py` | Database API. Django configured as a standalone ORM |
| `residentdb/` | Models, migrations, and the encrypted field |
| `keystore.py` | Encryption key in the macOS Keychain, never beside the data |
| `config.py` | Senders, trigger phrases, completion phrases — all site-specific values |
| `models.py` | Display-path data structures, and why PII cannot enter them |
| `graph_client.py` | Read-only Graph client. Exposes `GET` and nothing else |
| `app.py` · `board.py` | CLI and HTML board |
| `menubar.py` · `window.py` | macOS menu bar app and native window |
| `sample_data.py` | Fictional sample mailbox for demo mode and tests |
| `test_rules.py` · `test_store.py` | 46 offline tests |
| `sheet.py` | Retired spreadsheet reader, kept as a marker |

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

**A title-only status item does not render at all.** It needs an image too. And
`imageWithSystemSymbolName_` returns `None` for symbols the OS lacks, so one
hard-coded name can leave you invisible; the code tries several and takes the
first that resolves.

**`LSUIElement` plus LaunchServices suppresses the status item entirely.**
Double-clicked, the app runs and never draws. Run directly from a shell, it
draws. Hence the LaunchAgent.

**pyobjc exposes every method on an `NSObject` subclass as a selector,** and
derives the argument count from underscores in the name. A plain helper taking
arguments fails at class-creation time with `BadPrototypeError`. Mark them
`@objc.python_method`.

---

## Status

The verification half runs against live mail today. Preparing outreach stays
manual and supervised: the internet-provider portal and the resident-experience
platform publish no API, and the mail write scope needs tenant-admin consent. So
the tool reports what needs doing; a human does it.

That split is deliberate. Given the audit, being told *"nothing needs you"* is
worth more than having five wrong drafts written for you.

## Licence

MIT — see `LICENSE`.
