"""
The welcome page: first-launch setup, and Settings afterwards.

Shown in the board's window, in the board's look, one step at a time:

    start     what the app does; set up a property, or join with a profile
    outlook   sign in to Outlook in the app's browser
    you       your name and email, read from Outlook when it can be
    property  property name, signature, team Cc, folder, lease senders
    systems   sign in to RiseIO and Xfinity (or later)
    template  capture the welcome template from a new Outlook message
    done      set up; offer to share the setup as a profile

Settings reopens the same pages from "you" onward, with a Back to board
button instead of the step counter.

Pure: takes the settings and what the app knows (sign-in states, the email
read from Outlook, whether a template is captured) and returns HTML. The
buttons post named actions through the board's bridge (window.BOARD_ACTIONS);
menubar.py carries them out. Nothing here touches files, Outlook or the
network. No symbols or emoji in any text.
"""

from __future__ import annotations

import html
import re

import board

STEPS = ("start", "outlook", "you", "property", "systems", "template", "done")
EDIT_STEPS = ("you", "property", "systems", "template")
_TITLES = {
    "outlook": "Outlook", "you": "About you", "property": "Your property",
    "systems": "RiseIO and Xfinity", "template": "Welcome email", "done": "Done",
}

# The one place Outlook on the web shows the signed-in account that a page can
# read: the folder list's main mailbox entry, whose tooltip is the address.
# Found by a read-only probe; returns the address only, or "".
ACCOUNT_JS = r"""(() => {
  const root = document.querySelector('[id^="primaryMailboxRoot_"]');
  const line = root ? (root.getAttribute('title') || '').split('\n')[0].trim() : '';
  return JSON.stringify({ email: /^[^\s@<>]+@[^\s@<>]+\.[A-Za-z]{2,}$/.test(line) ? line.toLowerCase() : '' });
})()"""

# The body of the message being written in Outlook, for capturing the template.
CAPTURE_JS = r"""(() => {
  const body = [...document.querySelectorAll('[contenteditable="true"][aria-label="Message body"]')]
    .find(e => e.offsetWidth || e.offsetHeight);
  if (!body) return JSON.stringify({ ok: false });
  return JSON.stringify({ ok: true, html: body.innerHTML });
})()"""

# Whether a folder with this name is in Outlook's folder list. Filled with
# json.dumps(folder name).
FOLDER_JS = r"""(() => {
  const want = %s.trim().toLowerCase();
  const found = [...document.querySelectorAll('[role="treeitem"]')]
    .some(e => (e.getAttribute('title') || e.innerText || '').split('\n')[0].trim().toLowerCase() === want);
  return JSON.stringify({ found });
})()"""


def name_from_email(email: str) -> str:
    """A suggested name from a work address: jordan.lee@... -> Jordan Lee.
    Only a suggestion for the person to correct."""
    local = (email or "").split("@")[0]
    parts = [p for p in re.split(r"[._-]+", local) if p.isalpha()]
    return " ".join(p.capitalize() for p in parts[:3]) if len(parts) >= 2 else ""


def _esc(text: object) -> str:
    return html.escape(str(text or ""), quote=True)


CSS = """
.wz { max-width: 640px; margin: 0 auto; padding-top: 8px }
.wz h1 { margin-top: 48px }
.steps-dots { display: flex; gap: 6px; margin: 28px 0 0 }
.steps-dots span { height: 4px; flex: 1; border-radius: 4px; background: var(--fill) }
.steps-dots span.on { background: var(--grad) }
.wz .card { margin-bottom: 18px; padding: 22px 24px }
.wz .panel { margin: 0 0 18px; padding: 22px 24px }
.promises { list-style: none; padding: 0; margin: 0 0 30px }
.promises li { padding: 14px 0; border-top: 1px solid var(--line); font-size: 15px }
.promises li b { display: block; font-weight: 600 }
.promises li span { color: var(--ink-2); font-size: 14px }
.choices { display: grid; gap: 12px }
.choice { text-align: left; width: 100%; font: inherit; border: 0; cursor: pointer; background: var(--card);
  border-radius: 20px; padding: 18px 20px; box-shadow: var(--shadow) }
.choice:hover { box-shadow: 0 0 0 2px var(--accent), var(--shadow) }
.choice b { display: block; font-size: 17px; font-weight: 600 }
.choice span { display: block; margin-top: 4px; color: var(--ink-2); font-size: 14px; line-height: 1.45 }
.field { margin-bottom: 16px }
.field label { display: block; font-size: 13px; color: var(--ink-2); margin-bottom: 5px }
.field input { width: 100%; font: inherit; font-size: 15px; border: 1px solid rgba(0, 0, 0, .14); border-radius: 12px;
  padding: 10px 14px; background: var(--card); color: var(--ink) }
.field input:focus { outline: 0; border-color: var(--accent); box-shadow: 0 0 0 4px rgba(0, 113, 227, .18) }
.field .help { font-size: 12.5px; color: var(--ink-2); margin-top: 5px }
.field .found { color: #0058b0 }
.nav { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-top: 28px }
.nav > span { display: flex; gap: 8px }
.nav .later { background: transparent; color: var(--accent) }
.status-row { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 12px 0; border-top: 1px solid var(--line) }
.status-row:first-child { border-top: 0; padding-top: 0 }
.status-row:last-child { padding-bottom: 0 }
.status-row > span { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; justify-content: flex-end }
.status-row b { font-weight: 600 }
.problems { background: var(--hi); color: #8a2bb0; border-radius: 14px; padding: 10px 16px; font-size: 14px; margin-bottom: 16px }
.demo-link { margin-top: 22px; font-size: 13.5px; line-height: 1.5 }
.demo-link button { font: inherit; border: 0; background: transparent; color: var(--accent); cursor: pointer; padding: 0 }
.check { display: flex; gap: 10px; align-items: center; font-size: 14px; margin: 4px 0 16px }
.howto { background: var(--card) }
.howto-title { font-weight: 600; font-size: 15px; margin-bottom: 8px }
.howto-steps { margin: 0; padding-left: 20px }
.howto-steps li { padding: 5px 0; font-size: 14.5px; line-height: 1.5 }
.tip { background: var(--soft); border-radius: 12px; padding: 9px 14px; font-size: 13.5px; margin: 12px 0 0 }
.why { margin-top: 12px; font-size: 13.5px; color: var(--ink-2) }
.why summary { cursor: pointer; color: var(--accent); list-style: none }
.why summary::-webkit-details-marker { display: none }
.why p { margin: 6px 0 0 }
.pick { font-size: 14px; color: var(--ink-2); margin: 0 0 10px }
.demo-link .note { color: var(--ink-2) }
"""

_SCRIPT = """
<script>
(function () {
  var bridge = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.triage;
  if (!bridge) { document.body.classList.add('no-app'); return; }
  function send(action, value) { bridge.postMessage({ action: action, value: value || '' }); }
  function form(id) {
    var out = {};
    document.querySelectorAll('#' + id + ' [name]').forEach(function (e) {
      out[e.name] = e.type === 'checkbox' ? (e.checked ? '1' : '') : e.value;
    });
    return JSON.stringify(out);
  }
  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-action]');
    if (!b) return;
    e.preventDefault();
    var value = b.dataset.form ? form(b.dataset.form) : b.dataset.value;
    if (b.dataset.busy) { b.textContent = b.dataset.busy; b.disabled = true; }
    send(b.dataset.action, value);
  });
  document.addEventListener('submit', function (e) {
    e.preventDefault();
    if (e.target.id === 'add') {
      var v = document.getElementById('sender').value.trim();
      // Keep what is typed above: adding a sender redraws the page.
      if (document.getElementById('property')) send('setup_keep', form('property'));
      if (v) send('add_sender', v);
    }
  });
})();
</script>"""


def _dots(step: str) -> str:
    shown = STEPS[1:]
    at = shown.index(step) if step in shown else -1
    return '<div class="steps-dots">' + "".join(
        f'<span class="{"on" if i <= at else ""}"></span>' for i in range(len(shown))) + "</div>"


def _btn(label, action, value="", kind="dark", form="", busy=""):
    attrs = f' data-action="{action}"'
    if value:
        attrs += f' data-value="{_esc(value)}"'
    if form:
        attrs += f' data-form="{form}"'
    if busy:
        attrs += f' data-busy="{_esc(busy)}"'
    return f'<button class="btn {kind}"{attrs}>{_esc(label)}</button>'


def _nav(back: str | None, next_button: str, edit: bool) -> str:
    if edit:
        left = _btn("Back to board", "setup_close", kind="")
    else:
        left = _btn("Back", "setup_go", back, kind="") if back else "<span></span>"
    return f'<div class="nav">{left}<span>{next_button}</span></div>'


def _field(name, label, value, help_text="", kind="text", placeholder=""):
    ph = f' placeholder="{_esc(placeholder)}"' if placeholder else ""
    helper = f'<div class="help">{help_text}</div>' if help_text else ""
    return (f'<div class="field"><label for="f-{name}">{_esc(label)}</label>'
            f'<input id="f-{name}" name="{name}" type="{kind}" value="{_esc(value)}" autocomplete="off"{ph}>'
            f"{helper}</div>")


def _howto(title: str, steps: list[str], tips: list[str] | None = None, why: str = "") -> str:
    """A "how to" card: numbered steps, then tips, then a fold-out reason."""
    items = "".join(f"<li>{s}</li>" for s in steps)
    tip = "".join(f'<p class="tip">{t}</p>' for t in tips or [])
    fold = (f'<details class="why"><summary>Why this is needed</summary><p>{why}</p></details>'
            if why else "")
    return (f'<div class="card howto"><div class="howto-title">{_esc(title)}</div>'
            f'<ol class="howto-steps">{items}</ol>{tip}{fold}</div>')


def _start(ctx) -> str:
    return f"""
<h1>Welcome to <span class="mark">Move-in triage</span></h1>
<p class="lede">It watches your Move ins folder, tells you which new residents still need
setting up, and fills RiseIO, Xfinity and the welcome email for you to check.</p>
<ul class="promises">
<li><b>It only reads your mail.</b><span>Nothing in Outlook is moved, deleted or marked by the app.</span></li>
<li><b>You press every final button.</b><span>Send, Save, Create and Confirm are always yours.</span></li>
<li><b>Nothing leaves this Mac.</b><span>You sign in as yourself. The app never sees your passwords.</span></li>
</ul>
<p class="pick">Pick how to start:</p>
<div class="choices">
<button class="choice" data-action="setup_go" data-value="outlook"><b>Set up my property</b>
<span>For the first person at a property. You enter the property's details once, then can share
them with teammates as a profile file. About five minutes.</span></button>
<button class="choice" data-action="setup_import"><b>Join with a teammate's profile</b>
<span>A teammate already set it up and sent you a profile file. Choose that file and the property
details fill in for you; you only sign in and confirm your name. About one minute.</span></button>
</div>
<div class="demo-link"><button data-action="setup_demo">Try it with demo data first</button>
<span class="note"> Opens a separate practice board with made-up units. Nothing is signed in to and
nothing real is changed. Quit it from its menu bar item when done.</span></div>"""


def _outlook(ctx) -> str:
    state = ctx.get("outlook_state", "")
    signed = state == "signed_in"
    status = ("Signed in" if signed else "Sign in needed" if state == "signed_out" else "Checking")
    pill = "p-handled" if signed else "p-unclear"
    return f"""
{_dots("outlook")}
<h1>Sign in to <span class="mark">Outlook</span></h1>
<p class="lede">The app reads your mail through its own browser window, signed in as you.</p>
{_howto("What to do", [
    "Press <b>Open Outlook</b>. A window called Move-in triage browser opens on the Email tab.",
    "Sign in with your usual work account, including any code sent to your phone.",
    "Wait until your inbox shows, then come back to this window.",
    "Press <b>Check again</b> until it says Signed in, then press <b>Continue</b>.",
], ["Already signed in? It can still say Checking for a few seconds while Outlook loads."],
   "Lease emails arrive in Outlook. The app reads them in its own window instead of asking "
   "for your password or for IT to set anything up, so it only ever sees what you can see.")}
<div class="card"><div class="status-row"><b>Outlook</b>
<span><span class="pill {pill}">{status}</span> {_btn("Open Outlook", "open_site", "outlook", kind="small")}
{_btn("Check again", "setup_go", "outlook", kind="small")}</span></div></div>
{_nav("start", _btn("Continue", "setup_go", "you", busy="Reading your account..."), False)}"""


def _you(ctx) -> str:
    you = ctx["settings"]["you"]
    detected = ctx.get("detected_email", "")
    email = you["email"] or detected
    name = you["name"] or name_from_email(email)
    if detected and detected == email:
        email_help = "Read from Outlook. If another account signs in later, the board warns you."
    elif detected:
        email_help = "Outlook is signed in as a different address. Check which one is right."
    else:
        email_help = "Couldn't read it from Outlook, so type the address you sign in with."
    edit = ctx.get("edit", False)
    return f"""
{"" if edit else _dots("you")}
<h1>About <span class="mark">you</span></h1>
<p class="lede">Your details stay on this Mac and are never part of a shared profile.</p>
{_howto("What to do", [
    "Check your name. It was suggested from your email address, so fix it if it's off.",
    "Check your work email. It should be the account you just signed in to Outlook with.",
    f"Press <b>{'Save' if edit else 'Continue'}</b>.",
], why="Your email lets the app notice if Outlook is ever signed in as someone else, so it "
       "never reads the wrong mailbox without telling you.")}
<div class="card" id="you">
{_field("name", "Your name", name, "Check this. It was suggested from your email address." if name and not you["name"] else "")}
{_field("email", "Your work email", email, email_help, kind="email")}
</div>
{_nav("outlook", _btn("Save" if edit else "Continue", "setup_save_you", form="you"), edit)}"""


def _property(ctx) -> str:
    prop = ctx["settings"]["property"]
    found = ctx.get("folder_found")
    folder_help = ('<span class="found">Found in Outlook.</span>' if found
                   else "Not found in Outlook yet. Check the spelling matches the folder exactly."
                   if found is False else "The Outlook folder lease emails are filed in.")
    roster = ctx.get("senders") or {"addresses": [], "names": []}
    edit = ctx.get("edit", False)
    senders_panel = board._senders_panel(roster, [])
    return f"""
{"" if edit else _dots("property")}
<h1>Your <span class="mark">property</span></h1>
<p class="lede">Shared by everyone at the property. Set it up once, then share it as a profile.</p>
{_howto("What to do", [
    "<b>Property name</b>: as it should appear in the welcome email subject, for example "
    "Example Commons | Welcome Home #0735.",
    "<b>Signature</b>: the team name the welcome email signs off with.",
    "<b>Team Cc</b>: the shared mailbox copied on every welcome email. Leave it empty if there isn't one.",
    "<b>Outlook folder</b>: the folder your lease emails are filed in, spelled exactly as in "
    "Outlook's folder list. It says Found in Outlook once the name matches.",
    "<b>Lease email senders</b>: everyone who sends the lease emails. Add each person's email "
    "address, or their name exactly as Outlook shows it (Outlook often shows names only).",
    f"Press <b>{'Save' if edit else 'Continue'}</b> at the bottom.",
], ["Not sure who sends lease emails? Leave it for now: the board lists anyone it sees sending "
    "lease emails who isn't on the list, with an Add button."],
   "Only emails from the senders on this list are treated as new leases, so a reply or a "
   "forward from anyone else never starts a move-in by mistake.")}
<div class="card" id="property">
{_field("name", "Property name", prop["name"], "Used in the welcome email subject.", placeholder="Example Commons")}
{_field("signature", "Signature", prop["signature"], "The team name the welcome email signs off with.", placeholder="The Experience Team")}
{_field("welcome_cc", "Team Cc", prop["welcome_cc"], "Copied on every welcome email. Leave empty for none.", kind="email", placeholder="team@company.com")}
{_field("folder", "Outlook folder", prop["folder"], folder_help)}
</div>
{senders_panel}
{_nav("you", _btn("Save" if edit else "Continue", "setup_save_property", form="property"), edit)}"""


def _systems(ctx) -> str:
    rows = []
    for key, label, state in ctx.get("connections") or []:
        if key == "outlook":
            continue
        signed = state == "signed_in"
        status = "Signed in" if signed else "Sign in needed" if state == "signed_out" else "Checking"
        rows.append(f'<div class="status-row"><b>{_esc(label)}</b><span><span class="pill '
                    f'{"p-handled" if signed else "p-unclear"}">{status}</span> '
                    f'{_btn("Open", "open_site", key, kind="small")}</span></div>')
    edit = ctx.get("edit", False)
    nxt = "" if edit else (_btn("Later", "setup_go", "template", kind="later") + " "
                           + _btn("Continue", "setup_go", "template"))
    return f"""
{"" if edit else _dots("systems")}
<h1>Sign in to <span class="mark">RiseIO and Xfinity</span></h1>
<p class="lede">Needed only when the app fills them in for you. You can do this later from the
board's Open buttons.</p>
{_howto("What to do", [
    "Press <b>Open</b> next to RiseIO. The browser window switches to the RiseIO tab.",
    "Sign in with your own RiseIO account.",
    "Do the same for Xfinity.",
    "Come back and press <b>Check again</b>, then <b>Continue</b>.",
], ["The app fills in forms on these sites but never presses Save, Create or Confirm. "
    "When it is your turn, the board says so and you press the button yourself."],
   "New residents are added to RiseIO and Xfinity. Signing in here once lets the app fill "
   "those forms in the same window you already trust.")}
<div class="card">{"".join(rows) or '<div class="status-row">Checking...</div>'}
<div class="status-row"><span></span>{_btn("Check again", "setup_go", "systems", kind="small")}</div></div>
{_nav("property", nxt, edit)}"""


def _template(ctx) -> str:
    have = ctx.get("template_captured", False)
    edit = ctx.get("edit", False)
    status = ('<span class="pill p-handled">Captured</span>' if have
              else '<span class="pill p-skipped">Not captured</span>')
    nxt = "" if edit else (_btn("Later" if not have else "Continue", "setup_go", "done",
                                kind="later" if not have else "dark"))
    return f"""
{"" if edit else _dots("template")}
<h1>Your <span class="mark">welcome email</span></h1>
<p class="lede">The app uses your own Outlook template, exactly as it looks, and fills in the
residents' names. Capture it once.</p>
{_howto("What to do", [
    "Press <b>Open Outlook</b> below and start a <b>New mail</b>.",
    "Insert your Welcome template. In the new message it's under <b>My templates</b>, found in "
    "the toolbar's More options menu or under Insert, depending on your Outlook layout.",
    "Leave <b>(Resident Name)</b> exactly as it is. The app replaces it with the residents' first names.",
    "Come back here and press <b>Capture</b>.",
    "Discard that message in Outlook. Nothing was sent.",
], ["No template yet? Press Later. The app uses a plain built-in welcome text until you capture one."],
   "Copying a formatted email by hand breaks its layout. Capturing it from Outlook keeps it "
   "exactly as you designed it, images and all.")}
<div class="card"><div class="status-row"><b>Template</b><span>{status}
{_btn("Open Outlook", "open_site", "outlook", kind="small")}
{_btn("Capture", "setup_capture", kind="small dark", busy="Capturing...")}</span></div></div>
{_nav("systems", nxt, edit)}"""


def _done(ctx) -> str:
    have = ctx.get("template_captured", False)
    include = (f'<label class="check"><input type="checkbox" name="template" {"checked" if have else "disabled"}> '
               f'Include the welcome template{"" if have else " (not captured yet)"}</label>')
    return f"""
{_dots("done")}
<h1>You're <span class="mark">set up</span></h1>
<p class="lede">The board opens next, with a short Getting started guide at the top.</p>
{_howto("How a move-in works from here", [
    "The board checks your Move ins folder every few minutes and lists each new lease.",
    "A unit marked <b>Needs outreach</b> is yours to do. Reply <b>On it</b> in the lease thread so "
    "teammates know.",
    "Press <b>Prepare</b> on it, check the residents' details, then <b>Start guided run</b>.",
    "The app fills RiseIO, Xfinity and the welcome email. When a step says <b>Your turn</b>, "
    "check it and press the final button yourself.",
    "Reply in the lease thread that it's done. The unit then shows <b>Handled</b> for everyone.",
])}
<div class="card" id="share">
<b>Share this setup with teammates</b>
<p class="note" style="margin: 4px 0 12px">Saves a small profile file: property, lease senders and
optionally the template. Never your name, email or sign-ins. Send it to a teammate; on their
welcome page they choose <b>Join with a teammate's profile</b> and pick the file.</p>
{include}
{_btn("Save profile...", "setup_export", form="share", kind="")}
</div>
<div class="nav"><span></span>{_btn("Open the board", "setup_finish")}</div>"""


_PAGES = {"start": _start, "outlook": _outlook, "you": _you, "property": _property,
          "systems": _systems, "template": _template, "done": _done}


def render(step: str, ctx: dict) -> str:
    """
    One step of the welcome page. `ctx` carries:
      settings           settings.load()
      senders            senders.load()
      edit               True when opened from the board's Settings button
      notice, problems   a line to show, and a list of field problems
      outlook_state      "signed_in" / "signed_out" / ""
      detected_email     the address read from Outlook, or ""
      folder_found       True / False / None (not checked)
      connections        [(key, label, state)] as on the board
      template_captured  bool
    """
    if step not in _PAGES:
        step = "start"
    edit = ctx.get("edit", False)
    tabs = ""
    if edit:
        tabs = '<span class="sites">' + "".join(
            f'<button class="btn small{" dark" if s == step else ""}" data-action="setup_go" '
            f'data-value="{s}">{_esc(_TITLES[s])}</button>' for s in EDIT_STEPS) + "</span>"
        tabs += (_btn("Export profile...", "setup_export", "1", kind="small")
                 + _btn("Import profile...", "setup_import", kind="small"))
    notice = f'<div class="notice">{_esc(ctx["notice"])}</div>' if ctx.get("notice") else ""
    problems = ""
    if ctx.get("problems"):
        problems = '<div class="problems">' + "<br>".join(
            _esc(p[:1].upper() + p[1:] + ".") for p in ctx["problems"]) + "</div>"
    title = "Settings" if edit else "Welcome"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Move-in triage</title>
<style>{board.CSS}{CSS}</style>
</head>
<body>
<div class="wrap">
<div class="top"><div class="brand">Move-in triage <span class="status">{title}</span></div>
<div class="right">{tabs}</div></div>
<div class="wz">
{notice}
{problems}
{_PAGES[step](ctx)}
</div>
</div>
{_SCRIPT}
</body>
</html>
"""
