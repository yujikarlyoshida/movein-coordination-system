"""
Renders results as a self-contained HTML board.

Shows unit numbers, move-in dates, what was checked, and a status. No resident
names, email addresses or phone numbers -- not because they are filtered out
here, but because they never reach this module (see models.py).

Each unit links to an Outlook search for that unit number, so one click takes you
from "0735 needs outreach" to the actual thread, read in Outlook as yourself.
That is the whole privacy model in one gesture: the board says which unit, the
mail system says everything else.

Look: Apple-style. Near-white page, white cards with soft shadows, a frosted
top bar, large tight headlines, and one pink-to-blue gradient used sparingly --
the headline's key words, the hot count card and a short rule. Blue pill
buttons, tinted pill statuses. Sentence case, plain words, no symbols or emoji
anywhere in the text.
"""

from __future__ import annotations

import html
import re
import urllib.parse
from datetime import datetime

import confirm_sheet
import hero
import triggers
from models import Result, Verdict

MOTION_CSS = r"""
/* ---- Motion -------------------------------------------------------------
   Springy, but calm: things travel a short way and overshoot just a touch
   before settling, like iOS. Movement runs on a spring curve; fading
   runs on a plain ease, so nothing flickers past fully visible.

   Entrances play the first time real results appear in a window (the board
   redraws itself every few minutes, and replaying them each time would be
   noise). After that only what changed moves: a new or changed unit's row
   glows. Everything here is off under System Settings > Accessibility >
   Reduce motion. */
:root {
  --ease: cubic-bezier(.2, .7, .2, 1);
  --spring: cubic-bezier(.34, 1.3, .64, 1);
  --grad-loop: linear-gradient(100deg, #e5317a 0%, #c13fd1 19%, #8a6ce8 33%, #4fb6f5 50%,
                                       #8a6ce8 67%, #c13fd1 81%, #e5317a 100%);
}
/* A real spring (overshoot, then a smaller one back) where WebKit supports it. */
@supports (transition-timing-function: linear(0, 1)) {
  :root {
    --spring: linear(0, 0.009 1.5%, 0.037 3.1%, 0.142 6.4%, 0.462 13.7%, 0.725 20.8%, 0.893 28.3%,
                     0.99 35.8%, 1.039 42.4%, 1.058 48.6%, 1.059 55.3%, 1.045 62.6%, 1.012 77.6%,
                     0.998 92%, 1);
  }
}
@keyframes lift { from { transform: translateY(28px) scale(.985) } to { transform: none } }
@keyframes fade { from { opacity: 0 } to { opacity: 1 } }
@keyframes grow { from { transform: scaleX(0) } to { transform: scaleX(1) } }
@keyframes pop { from { transform: translateY(24px) scale(.95) } to { transform: none } }
@keyframes shimmer { from { background-position: 0% 50% } to { background-position: 200% 50% } }
@keyframes ring { from { background-position: 0 0, 0% 50% } to { background-position: 0 0, 200% 50% } }
@keyframes glow { 0% { background: var(--hi) } 100% { background: transparent } }
@keyframes nudge { 0%, 100% { box-shadow: 0 0 0 0 rgba(193, 63, 209, .3) } 50% { box-shadow: 0 0 0 8px rgba(193, 63, 209, 0) } }

/* The gradient words drift slowly, like the Siri wordmark. */
.mark, .card.hot .n { background-image: var(--grad-loop); background-size: 200% auto; animation: shimmer 9s linear infinite }
.card.hot { background: linear-gradient(var(--card), var(--card)) padding-box, var(--grad-loop) border-box;
  background-size: auto, 200% auto; animation: ring 9s linear infinite }

/* First view: the page springs together from the top down. */
.intro h1, .intro .lede, .intro .guide, .intro .cards .card, .intro .table, .intro .panel:not(.guide) {
  animation-name: lift, fade; animation-duration: 1.05s, .75s;
  animation-timing-function: var(--spring), var(--ease); animation-fill-mode: both }
.intro .lede { animation-delay: .1s }
.intro .rule { transform-origin: left; animation: grow 1.1s var(--spring) .2s both }
.intro .guide { animation-delay: .28s }
.intro .cards .card:nth-child(1) { animation-delay: .36s }
.intro .cards .card:nth-child(2) { animation-delay: .46s }
.intro .cards .card:nth-child(3) { animation-delay: .56s }
.intro .cards .card:nth-child(4) { animation-delay: .66s }
.intro .card.hot { animation-name: lift, fade, ring; animation-duration: 1.05s, .75s, 9s;
  animation-timing-function: var(--spring), var(--ease), linear; animation-iteration-count: 1, 1, infinite;
  animation-delay: .36s, .36s, 0s; animation-fill-mode: both }
.intro .table { animation-delay: .76s }
.intro .panel:not(.guide) { animation-delay: .86s }
.intro .top { animation: fade .8s ease both }

/* Hover and press: things that can be clicked spring in response. */
.card, .choice, .btn, .pill, .chip { transition: transform .5s var(--spring), box-shadow .4s var(--ease), background-color .25s ease, filter .15s ease }
tr { transition: background-color .25s ease }
.cards .card:hover { transform: translateY(-4px) scale(1.005); box-shadow: 0 4px 8px rgba(0, 0, 0, .05), 0 22px 44px rgba(0, 0, 0, .1) }
.choice:hover { transform: translateY(-3px) }
.btn:hover, .pill.p-prepare:hover, .p-missing:hover { transform: translateY(-2px) }
tbody tr:hover { background-color: rgba(0, 0, 0, .018) }
.btn:active, .choice:active, .pill:active { transform: scale(.94); transition-duration: .15s }

/* A unit that is new, or whose status changed, since the last look. */
tr.fresh td { animation: glow 3.4s ease-out both }

/* Pop-ups spring up into place. */
.veil.open { animation: fade .35s ease both }
.veil.open .pop { animation-name: pop, fade; animation-duration: .8s, .4s;
  animation-timing-function: var(--spring), var(--ease); animation-fill-mode: both }

/* The guided run's "Your turn" pulses until you act. */
.progress li.your_turn { animation: nudge 2.4s ease-in-out infinite }
.progress li { animation: fade .5s ease both }

/* Scrolling. Things spring in every time they come into view, from below
   when scrolling down and from above when scrolling up; things already on
   screen at load are left alone. The headline drifts back as it
   scrolls away, and the top bar gains its edge once the page has moved.
   Hidden states apply only while the script runs them ("scrolly" on the page),
   so without it nothing is ever hidden. */
.scrolly .reveal:not(.in) { opacity: 0; transform: translateY(40px) scale(.97) }
.scrolly .reveal.from-above:not(.in) { transform: translateY(-40px) scale(.97) }
.scrolly .reveal.in { transition: opacity .7s var(--ease), transform 1.1s var(--spring) }
.scrolly .reveal.done { opacity: 1; transform: none; transition: none }
.top { border-bottom-color: transparent; transition: border-color .3s ease, box-shadow .3s ease }
.scrolled .top { border-bottom-color: var(--line); box-shadow: 0 1px 14px rgba(0, 0, 0, .06) }
.scrolly h1, .scrolly .lede { transform: translateY(calc(var(--away, 0) * -24px)) scale(calc(1 - var(--away, 0) * .02));
  transform-origin: left; opacity: calc(1 - var(--away, 0) * .5) }

/* ---- Colour and depth ---------------------------------------------------
   A soft Siri-style glow behind the top of the page, frosted-glass surfaces
   over it, and one colour per status carried through the count cards and the
   table, so the board reads at a glance. */
:root { --c-needs: #e5317a; --c-check: #a23fd1; --c-handled: #2f7cf6; --c-skipped: #a1a1aa }
body { position: relative; isolation: isolate }
body::before {
  content: ""; position: fixed; z-index: -1; left: -10vw; right: -10vw; top: -30vh; height: 95vh; pointer-events: none;
  background:
    radial-gradient(38% 46% at 18% 40%, rgba(229, 49, 122, .20), transparent 70%),
    radial-gradient(34% 44% at 50% 30%, rgba(161, 77, 233, .18), transparent 70%),
    radial-gradient(36% 46% at 84% 42%, rgba(79, 182, 245, .20), transparent 70%);
  filter: blur(30px); animation: drift 26s ease-in-out infinite alternate;
}
@keyframes drift { from { transform: translate3d(-2%, 0, 0) scale(1) } to { transform: translate3d(2%, 3%, 0) scale(1.06) } }
.card, .table, .panel, .pop, .choice {
  background-color: rgba(255, 255, 255, .74);
  -webkit-backdrop-filter: saturate(170%) blur(22px); backdrop-filter: saturate(170%) blur(22px);
  border: 1px solid rgba(255, 255, 255, .7);
}
.card.hot { border: 2px solid transparent }
h1 { font-size: 58px }
/* The top bar spans the whole window, frosted over the glow. */
.top { margin-inline: calc(50% - 50vw); padding-inline: calc(50vw - 50% + 0px);
  background: rgba(251, 251, 253, .55) }
.wz h1 { font-size: 52px }
/* Status colours on the count cards: a slim bar along the top. */
.cards .card { position: relative; overflow: hidden }
.cards .card::before { content: ""; position: absolute; left: 0; right: 0; top: 0; height: 4px; background: var(--k, transparent); opacity: .85 }
.cards .k-needs { --k: var(--grad) } .cards .k-handled { --k: var(--c-handled) }
.cards .k-skipped { --k: var(--c-skipped) } .cards .k-check { --k: var(--c-check) }
.cards .card.hot::before { display: none }
/* ...and down the left edge of each row. */
tbody tr td:first-child { box-shadow: inset 4px 0 0 var(--row, transparent) }
tr[data-verdict="needs_outreach"] { --row: var(--c-needs) }
tr[data-verdict="unclear"] { --row: var(--c-check) }
tr[data-verdict="handled"] { --row: var(--c-handled) }
tr[data-verdict="skipped_corporate"] { --row: var(--c-skipped) }
tr[data-verdict="handled"] .unit a, tr[data-verdict="skipped_corporate"] .unit a { color: var(--ink-2) }
/* The main call to action carries the brand gradient. */
.p-prepare { background-image: var(--grad-loop); background-size: 200% auto; color: #fff;
  box-shadow: 0 4px 14px rgba(193, 63, 209, .28); animation: shimmer 9s linear infinite }
.btn.dark { box-shadow: 0 4px 14px rgba(0, 113, 227, .28) }
@media (max-width: 720px) { h1 { font-size: 36px } }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important }
  .scrolly .reveal:not(.in) { opacity: 1; transform: none }
  .scrolly h1, .scrolly .lede { transform: none; opacity: 1 }
}
"""

INTRO_HEAD = r"""<script>
(function () {
  var root = document.documentElement;
  try {
    // Plays on the first board with real results in this window. The brief
    // "Starting" board before the first check plays it too, without using it up.
    if (!sessionStorage.getItem('movein-intro')) {
      root.classList.add('intro');
      if (root.dataset.ready) sessionStorage.setItem('movein-intro', '1');
    }
  } catch (e) { root.classList.add('intro'); }
  // A safety net, not a timing: the entrance is over by now. If WebKit has
  // paused animations (a hidden window), never leave the page invisible.
  setTimeout(function () { root.classList.remove('intro'); }, 2800);
})();
</script>"""

MOTION_BODY = r"""<script>
(function () {
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (document.documentElement.classList.contains('intro') && !reduce) {
    document.querySelectorAll('.cards .n').forEach(function (el) {
      var end = parseInt(el.textContent, 10);
      if (!(end > 0)) return;
      var start = null;
      el.textContent = '0';
      setTimeout(function () { el.textContent = String(end); }, 2800);
      function step(t) {
        if (start === null) start = t;
        var p = Math.min(1, (t - start) / 1300);
        el.textContent = String(Math.round(end * (1 - Math.pow(1 - p, 3))));
        if (p < 1) requestAnimationFrame(step);
      }
      setTimeout(function () { requestAnimationFrame(step); }, 450);
    });
  }
  var now = {};
  document.querySelectorAll('tbody tr[data-verdict]').forEach(function (tr) {
    var a = tr.querySelector('.unit a');
    if (a) now[a.textContent.trim()] = { verdict: tr.dataset.verdict, row: tr };
  });
  var before = null;
  try { before = JSON.parse(sessionStorage.getItem('movein-rows') || 'null'); } catch (e) {}
  if (before) {
    Object.keys(now).forEach(function (unit) {
      if (before[unit] !== now[unit].verdict) now[unit].row.classList.add('fresh');
    });
  }
  var save = {};
  Object.keys(now).forEach(function (unit) { save[unit] = now[unit].verdict; });
  try { sessionStorage.setItem('movein-rows', JSON.stringify(save)); } catch (e) {}
})();
</script>"""

SCROLL_JS = r"""<script>
(function () {
  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  var root = document.documentElement;
  var targets = document.querySelectorAll(
    'tbody tr, .panel, .wz > .card, .wz > .panel, .howto-steps li, .g-status, .g-steps li, .choice');
  if (!targets.length) return;
  // Whatever is already on screen at load stays put, so a refresh never blinks.
  targets.forEach(function (el) {
    el.classList.add('reveal');
    if (el.getBoundingClientRect().top < window.innerHeight) el.classList.add('in', 'done');
  });
  root.classList.add('scrolly');
  // Every time something comes into view it springs in, from below when
  // scrolling down and from above when scrolling back up. Once it is fully
  // off screen it resets, ready to spring in again. Checked by position on
  // each scroll frame: a few dozen elements, and exact for table rows too.
  function settle(el) {
    // Settle for good (also the safety net if WebKit pauses transitions).
    el._settle = setTimeout(function () { el.classList.add('done'); el.style.transitionDelay = ''; }, 2000);
  }
  function check() {
    var h = window.innerHeight, arriving = 0;
    targets.forEach(function (el) {
      var b = el.getBoundingClientRect();
      var shown = el.classList.contains('in');
      if (!shown && b.top < h - 24 && b.bottom > 24) {
        el.style.transitionDelay = (Math.min(arriving++, 6) * 70) + 'ms';
        el.classList.add('in');
        settle(el);
      } else if (shown && (b.bottom < 0 || b.top > h)) {
        clearTimeout(el._settle);
        el.style.transitionDelay = '';
        el.classList.remove('in', 'done');
        el.classList.toggle('from-above', b.bottom < 0);
      }
    });
  }
  var ticking = false;
  function onScroll() {
    check();
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(function () {
      var y = window.scrollY || 0;
      root.classList.toggle('scrolled', y > 8);
      root.style.setProperty('--away', Math.min(1, y / 320).toFixed(3));
      ticking = false;
    });
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();
})();
</script>"""

CSS = """
:root {
  color-scheme: light;
  --paper: #fbfbfd; --card: #ffffff; --ink: #1d1d1f; --ink-2: #6e6e73;
  --line: rgba(0, 0, 0, .08); --fill: #f0f0f3; --accent: #0071e3;
  --grad: linear-gradient(100deg, #e5317a 0%, #c13fd1 38%, #8a6ce8 66%, #4fb6f5 100%);
  --hi: #f6ecfb; --soft: #eaf2ff; --shadow: 0 1px 2px rgba(0, 0, 0, .04), 0 8px 28px rgba(0, 0, 0, .06);
}
* { box-sizing: border-box }
body {
  margin: 0; padding: 0 40px 64px; background: var(--paper); color: var(--ink);
  font: 15px/1.47 -apple-system, "SF Pro Text", system-ui, "Helvetica Neue", sans-serif;
  letter-spacing: -.01em; -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 980px; margin: 0 auto }
.top {
  position: sticky; top: 0; z-index: 5; display: flex; justify-content: space-between; align-items: center; gap: 16px;
  margin: 0 -40px; padding: 10px 40px; background: rgba(251, 251, 253, .8);
  -webkit-backdrop-filter: saturate(180%) blur(20px); backdrop-filter: saturate(180%) blur(20px);
  border-bottom: 1px solid var(--line);
}
.brand { font-weight: 600; font-size: 14px }
.status { font-size: 12px; color: var(--ink-2) }
h1 {
  font: 600 52px/1.07 -apple-system, "SF Pro Display", system-ui, sans-serif; letter-spacing: -.035em;
  margin: 56px 0 12px;
}
.mark { background: var(--grad); -webkit-background-clip: text; background-clip: text; color: transparent; padding: 0 2px }
.lede { font-size: 19px; line-height: 1.42; color: var(--ink-2); margin: 0 0 36px; max-width: 640px }
.rule { height: 3px; background: var(--grad); border-radius: 3px; margin: 0 0 32px; width: 120px }
.cards { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; margin-bottom: 32px }
.card { background: var(--card); border: 2px solid transparent; border-radius: 22px; padding: 18px 20px; box-shadow: var(--shadow) }
.card.hot { border: 2px solid transparent;
  background: linear-gradient(var(--card), var(--card)) padding-box, var(--grad) border-box }
.card .label { font-size: 13px; color: var(--ink-2) }
.card .n { font: 600 40px/1.1 -apple-system, "SF Pro Display", system-ui, sans-serif; letter-spacing: -.03em; margin-top: 6px }
.card.hot .n { background: var(--grad); -webkit-background-clip: text; background-clip: text; color: transparent }
.table { background: var(--card); border-radius: 24px; overflow: hidden; box-shadow: var(--shadow) }
table { width: 100%; border-collapse: collapse }
th { text-align: left; font-size: 12px; font-weight: 600; color: var(--ink-2); padding: 16px 22px 10px; border-bottom: 1px solid var(--line) }
td { padding: 16px 22px; border-bottom: 1px solid var(--line); vertical-align: top; font-size: 14.5px; line-height: 24px }
tr:last-child td { border-bottom: 0 }
.unit a { color: var(--ink); font-weight: 600; font-size: 17px; line-height: 24px; text-decoration: none; font-variant-numeric: tabular-nums }
.unit a:hover { color: var(--accent) }
.check { color: var(--ink) }
.note { font-size: 13px; line-height: 1.45; color: var(--ink-2); margin-top: 4px }
.pill { display: inline-block; font-size: 12.5px; font-weight: 600; padding: 5px 13px; border-radius: 999px; white-space: nowrap }
.p-needs { background: var(--ink); color: #fff }
.p-unclear { background: var(--hi); color: #8a2bb0 }
.p-skipped { background: var(--fill); color: var(--ink-2) }
.p-handled { background: var(--soft); color: #0058b0 }
.empty { padding: 44px 22px; text-align: center; color: var(--ink-2) }
.foot { margin-top: 32px; font-size: 12px; color: var(--ink-2); line-height: 1.7 }
.right { display: flex; align-items: center; gap: 14px }
.btn { font: inherit; font-size: 14px; font-weight: 500; border-radius: 999px; padding: 8px 18px; cursor: pointer;
  border: 0; background: var(--fill); color: var(--ink) }
.btn.dark { background: var(--accent); color: #fff }
.btn:hover { filter: brightness(.95) } .btn:active { transform: scale(.98) }
.btn.small { font-size: 12.5px; padding: 4px 12px }
.notice { background: var(--soft); border-radius: 14px; padding: 12px 18px; margin: 24px 0 0; font-size: 14px }
.panel { margin-top: 32px; background: var(--card); border-radius: 24px; padding: 24px 26px; box-shadow: var(--shadow) }
.panel h2 { font: 600 22px/1.2 -apple-system, "SF Pro Display", system-ui, sans-serif; letter-spacing: -.02em; margin: 0 0 4px }
.panel .sub { font-size: 14px; color: var(--ink-2); margin: 0 0 18px }
.chips { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 18px }
.chip { display: inline-flex; align-items: center; gap: 10px; background: var(--fill); border-radius: 999px; padding: 5px 6px 5px 14px; font-size: 13.5px }
.chip .kind { color: var(--ink-2); font-size: 12px }
.chip button { font: inherit; font-size: 12px; border: 0; background: transparent; color: var(--accent); cursor: pointer; padding: 3px 8px; border-radius: 999px }
.chip button:hover { background: var(--card) }
.addrow { display: flex; gap: 10px }
.addrow input { flex: 1; font: inherit; font-size: 14px; border: 1px solid rgba(0, 0, 0, .14); border-radius: 12px; padding: 9px 14px; background: var(--card); color: var(--ink) }
.addrow input:focus { outline: 0; border-color: var(--accent); box-shadow: 0 0 0 4px rgba(0, 113, 227, .18) }
.seen { margin-top: 20px; padding-top: 16px; border-top: 1px solid var(--line) }
.seen .item { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 8px 0; font-size: 14px }
.seen .item > span:last-child { display: flex; align-items: center; gap: 8px }
.seen .hl { background: var(--hi); padding: 1px 6px; border-radius: 6px }
.no-app .app-only { display: none }
.sites { display: inline-flex; align-items: center; gap: 6px; margin-right: 6px }
.sites-label { font-size: 12px; color: var(--ink-2) }
.p-missing { background: transparent; color: var(--ink); border: 1px dashed rgba(0, 0, 0, .3); cursor: pointer }
.stack { display: flex; flex-direction: column; align-items: flex-start; gap: 6px }
.gap { font-size: 13px; line-height: 1.45; color: var(--ink); margin-top: 4px }
.gap .blocks { color: var(--ink-2) }
.veil { position: fixed; inset: 0; background: rgba(0, 0, 0, .28); -webkit-backdrop-filter: blur(8px); backdrop-filter: blur(8px);
  display: none; align-items: center; justify-content: center; padding: 24px; z-index: 10 }
.veil.open { display: flex }
.pop { background: var(--card); border-radius: 28px; padding: 28px 30px; width: 100%; max-width: 520px; max-height: 80vh; overflow: auto;
  box-shadow: 0 24px 80px rgba(0, 0, 0, .22) }
.pop h2 { font: 600 28px/1.15 -apple-system, "SF Pro Display", system-ui, sans-serif; letter-spacing: -.025em; margin: 0 0 6px }
.pop .sub { font-size: 14px; color: var(--ink-2); margin: 0 0 18px }
.pop .unitgap { border-top: 1px solid var(--line); padding: 12px 0 }
.pop .unitgap b { font-size: 17px }
.pop .actions { display: flex; justify-content: flex-end; margin-top: 16px }
.p-prepare { background: var(--accent); color: #fff; cursor: pointer }
.p-done { background: var(--soft); color: #0058b0 }
.guide { margin: 0 0 32px }
.g-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 4px }
.g-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 28px; margin-top: 6px }
.guide h3 { font-size: 14px; font-weight: 600; margin: 8px 0 8px }
.g-status { display: grid; grid-template-columns: 148px 1fr; gap: 14px; align-items: start; font-size: 13.5px; padding: 7px 0 }
.g-status .pill { text-align: center }
.g-status span:last-child { color: var(--ink-2); line-height: 1.5; padding-top: 3px }
.g-steps { margin: 0; padding-left: 20px; font-size: 13.5px }
.g-steps li { padding: 4px 0; line-height: 1.5 }
@media (max-width: 720px) {
  body { padding: 0 16px 40px } .top { margin: 0 -16px; padding: 10px 16px; flex-wrap: wrap }
  .top .status, .sites-label { display: none } .right { flex-wrap: wrap; gap: 8px }
  h1 { font-size: 34px; margin-top: 36px } .lede { font-size: 16px }
  .cards { grid-template-columns: repeat(2, minmax(0, 1fr)) }
  .g-cols { grid-template-columns: 1fr }
  th, td { padding: 12px 12px }
}
""" + confirm_sheet.CSS + MOTION_CSS

_STATUS = {
    Verdict.NEEDS_OUTREACH: ("Needs outreach", "p-needs"),
    Verdict.UNCLEAR: ("Check", "p-unclear"),
    Verdict.SKIPPED_CORPORATE: ("Skipped, corporate", "p-skipped"),
    Verdict.HANDLED: ("Handled", "p-handled"),
}


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def _outlook_search_url(unit: str) -> str:
    """Deep-link to an Outlook search for this unit."""
    return (
        "https://outlook.office.com/mail/search/id/"
        if not unit
        else "https://outlook.office.com/mail/?" + urllib.parse.urlencode({"q": unit})
    )


def _move_in(text: str, today: datetime | None = None) -> str:
    """'09/20/2026' -> 'Sep 20'; the year only when it is not this year."""
    if not text:
        return ""
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            when = datetime.strptime(text.strip(), fmt)
        except ValueError:
            continue
        label = f"{when:%b} {when.day}"
        if when.year != (today or datetime.now()).year:
            label += f", {when.year}"
        return label
    return text.strip()


def _checks(result: Result) -> tuple[str, list[str]]:
    """What was checked, in plain words, plus any notes worth a second line."""
    notes = list(result.problems)
    if result.verdict is Verdict.HANDLED:
        kinds = {e.kind for e in result.evidence}
        said = []
        if kinds & {"thread_reply", "announcement"}:
            said.append("Teammate replied")
        if "welcome_sent" in kinds:
            said.append("Welcome email already sent")
        return (", ".join(said) or "Handled by a teammate"), []
    if result.verdict is Verdict.SKIPPED_CORPORATE:
        first = notes.pop(0) if notes else ""
        signals = re.search(r"\(([^)]*)\)", first)
        what = signals.group(1) if signals else "corporate lease"
        if first.startswith("possibly"):
            return f"Possibly corporate: {what}", ["Check this one by hand"] + notes
        return what[:1].upper() + what[1:], notes
    if result.verdict is Verdict.UNCLEAR:
        if not notes:
            return "Couldn't read this lease", []
        first = notes.pop(0)
        return first[:1].upper() + first[1:], notes
    return "No teammate reply, no welcome sent", notes


_GAP_NOTE = re.compile(
    r": (no (phone number|email address) given|(email address|phone number|lease start date) couldn't be read"
    r"|name has more than two words.*)$"
)


def _gap_lines(result: Result) -> list[tuple[str, str]]:
    """[("1st resident: no phone", "Blocks Xfinity")] -- positions and field names only."""
    out = []
    for position, fields in result.missing:
        blocks = triggers.blocked_steps(fields)
        out.append((f"{triggers.ordinal(position)} resident: {triggers.gap_phrase(fields)}",
                    f"Blocks {' and '.join(blocks)}" if blocks else ""))
    return out


def missing_keys(result: Result) -> set[str]:
    """One key per missing field, for remembering what the user has seen."""
    return {f"{result.unit}:{p}:{f}" for p, fields in result.missing for f in fields}


def _row(result: Result, interactive: bool = False, done: bool = False) -> str:
    label, cls = _STATUS[result.verdict]
    if done:
        label, cls = "Done", "p-done"
    check, notes = _checks(result)
    gaps = _gap_lines(result)
    if gaps:
        # The structured lines below say the same thing more usefully.
        notes = [n for n in notes if not _GAP_NOTE.search(n)]
    link = (
        f'<a href="{_esc(_outlook_search_url(result.unit))}" target="_blank" '
        f'rel="noopener" title="Search Outlook for this unit">{_esc(result.unit)}</a>'
    )
    note_html = "".join(f'<div class="note">{_esc(n[:1].upper() + n[1:])}</div>' for n in notes)
    note_html += "".join(
        f'<div class="gap">{_esc(what)}. <span class="blocks">{_esc(blocks)}</span></div>'
        for what, blocks in gaps
    )
    prepare = (f'<span class="pill p-prepare app-only" data-prepare="{_esc(result.unit)}" '
               'title="Review and carry out this unit">Prepare</span>'
               if interactive and not done and result.verdict is Verdict.NEEDS_OUTREACH else "")
    missing_pill = ('<span class="pill p-missing" data-open-missing="1" '
                    'title="See what is missing">Missing info</span>') if gaps else ""
    return (
        f'<tr data-verdict="{_esc(result.verdict.value)}">'
        f'<td class="unit">{link}</td>'
        f"<td>{_esc(_move_in(result.move_in))}</td>"
        f'<td><div class="check">{_esc(check)}</div>{note_html}</td>'
        f'<td><div class="stack"><span class="pill {cls}">{_esc(label)}</span>{prepare}{missing_pill}</div></td>'
        "</tr>"
    )


def _hero_lines(needs: int, unclear: int) -> tuple[str, str, str]:
    """The headline in the hero's three-line shape."""
    if needs:
        return f'{needs} unit{"s" if needs != 1 else ""}', "need you", "right now"
    if unclear:
        return f'{unclear} lease{"s" if unclear != 1 else ""}', "to check", "when you can"
    return "Nothing", "needs you", "right now"


def _headline(needs: int, unclear: int) -> str:
    if needs and unclear:
        return (f'{needs} unit{"s" if needs != 1 else ""} <span class="mark">need you</span>, '
                f"{unclear} to check")
    if needs:
        return f'{needs} unit{"s" if needs != 1 else ""} <span class="mark">need{"s" if needs == 1 else ""} you</span>'
    if unclear:
        return f'{unclear} lease{"s" if unclear != 1 else ""} <span class="mark">to check</span>'
    return 'Nothing <span class="mark">needs you</span> right now'


_CONN = {
    "signed_in": ("Signed in", "p-handled"),
    "signed_out": ("Sign in needed", "p-unclear"),
    "loading": ("Checking", "p-skipped"),
    "not_open": ("Not open yet", "p-skipped"),
}


def _connections_panel(connections: list) -> str:
    """Each system's sign-in state, with a button to its tab in the app's browser."""
    rows = "".join(
        f'<div class="item"><span><b>{_esc(label)}</b></span><span>'
        f'<span class="pill {_CONN.get(state, _CONN["loading"])[1]}">{_CONN.get(state, _CONN["loading"])[0]}</span> '
        f'<button class="btn small app-only" data-action="open_site" data-value="{_esc(key)}">Open</button></span></div>'
        for key, label, state in connections
    )
    return (
        '<div class="panel"><h2>Connections</h2>'
        '<p class="sub">Sign in to each system in the app\'s own browser. '
        "The app never sees your passwords.</p>"
        f'<div class="seen" style="border:0;padding:0;margin:0">{rows}</div></div>'
    )


def _senders_panel(senders: dict, unknown: list) -> str:
    """The editable sender list, plus senders seen sending lease emails but missing."""
    chips = "".join(
        f'<span class="chip">{_esc(v)} <span class="kind">{kind}</span>'
        f'<button class="app-only" data-action="remove_sender" data-value="{_esc(v)}" '
        f'title="Remove from the list">Remove</button></span>'
        for kind, values in (("address", senders.get("addresses", [])), ("name", senders.get("names", [])))
        for v in values
    ) or '<span class="sub">No senders yet. Add the leasing team below.</span>'
    seen = ""
    if unknown:
        rows = "".join(
            f'<div class="item"><span><span class="hl">{_esc(who)}</span> sent {n} lease '
            f'email{"s" if n != 1 else ""} the app is ignoring</span>'
            f'<button class="btn small app-only" data-action="add_sender" data-value="{_esc(who)}">Add</button></div>'
            for who, n in unknown
        )
        seen = f'<div class="seen"><div class="sub" style="margin:0 0 6px">Not on your list yet</div>{rows}</div>'
    return (
        '<div class="panel"><h2>Lease email senders</h2>'
        '<p class="sub">Only emails from these senders are read as new leases. '
        "Use an email address, or a full name exactly as Outlook shows it.</p>"
        f'<div class="chips">{chips}</div>'
        '<form class="addrow app-only" id="add">'
        '<input id="sender" autocomplete="off" placeholder="leasing.manager@company.com or Jordan Lee">'
        '<button class="btn dark" type="submit">Add sender</button></form>'
        f"{seen}</div>"
    )


_SCRIPT = """
<script>
(function () {
  var bridge = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.triage;
  if (!bridge) { document.body.classList.add('no-app'); return; }
  function send(action, value) { bridge.postMessage({ action: action, value: value || '' }); }
  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-action]');
    if (!b) return;
    e.preventDefault();
    if (b.dataset.action === 'run') { b.textContent = 'Checking...'; b.disabled = true; }
    if (b.dataset.action === 'ack_missing') { document.getElementById('missing').classList.remove('open'); }
    send(b.dataset.action, b.dataset.value);
  });
  document.addEventListener('click', function (e) {
    if (e.target.closest('[data-open-missing]')) document.getElementById('missing').classList.add('open');
  });
  var form = document.getElementById('add');
  if (form) form.addEventListener('submit', function (e) {
    e.preventDefault();
    var v = document.getElementById('sender').value.trim();
    if (v) send('add_sender', v);
  });
})();
</script>"""


def _guide() -> str:
    """Getting started: what the statuses mean and how a move-in is done."""
    statuses = "".join(
        f'<div class="g-status"><span class="pill {css}">{_esc(label)}</span><span>{text}</span></div>'
        for label, css, text in (
            ("Needs outreach", "p-needs", "A new lease nobody outside leasing has replied to. It's yours to do."),
            ("Check", "p-unclear", "Someone asked a question or said it isn't done yet, or the lease "
                                   "couldn't be read. Open the thread and look."),
            ("Handled", "p-handled", "A teammate replied in the thread, said it was added to all "
                                     "platforms, or a welcome email went out. Nothing to do."),
            ("Skipped, corporate", "p-skipped", "A company lease, handled outside this workflow."),
        ))
    steps = "".join(f"<li>{s}</li>" for s in (
        "Pick a unit that <b>Needs outreach</b>. Reply <b>On it</b> in its lease thread so teammates "
        "see it as Check instead of starting it too.",
        "Press <b>Prepare</b>. Check each resident's details and fill in anything marked missing.",
        "Press <b>Start guided run</b>. The app fills RiseIO, Xfinity and the welcome email in the "
        "browser window.",
        "When a step says <b>Your turn</b>, look over what was filled in, press the final button "
        "(Create, Save or Send) yourself, then press <b>Done</b> on the sheet.",
        "Reply in the lease thread that it's done, for example <b>Added to all platforms</b>. "
        "On the next check the unit shows Handled for everyone.",
    ))
    return (
        '<div class="panel guide"><div class="g-head"><h2>Getting started</h2>'
        '<button class="btn small app-only" data-action="guide_hide">Got it, hide this</button></div>'
        '<p class="sub">The board checks your Move ins folder every few minutes. Select a unit number '
        "to open its thread in Outlook. Run check looks again right away.</p>"
        f'<div class="g-cols"><div><h3>What the statuses mean</h3>{statuses}</div>'
        f'<div><h3>Doing a move-in</h3><ol class="g-steps">{steps}</ol></div></div>'
        '<p class="sub" style="margin:14px 0 0">Signed out of a site? Use the Email, RiseIO and '
        "Xfinity buttons at the top. Settings changes your details, the property and the sender list. "
        "Help brings this guide back.</p></div>"
    )


def _missing_popup(results: list[Result], acknowledged: set[str]) -> str:
    """The pop-up listing units that cannot be fully prepared. Opens on its own
    when something is new since the user last pressed Got it."""
    gappy = [r for r in results if r.missing]
    if not gappy:
        return ""
    keys = set().union(*(missing_keys(r) for r in gappy))
    fresh = keys - acknowledged
    units = "".join(
        f'<div class="unitgap"><b>{_esc(r.unit)}</b>'
        + "".join(f'<div class="gap">{_esc(what)}. <span class="blocks">{_esc(blocks)}</span></div>'
                  for what, blocks in _gap_lines(r))
        + "</div>"
        for r in gappy
    )
    n = len(gappy)
    return (
        f'<div class="veil{" open" if fresh else ""}" id="missing">'
        '<div class="pop" role="dialog" aria-labelledby="missing-title">'
        '<h2 id="missing-title">Some details are missing</h2>'
        f'<p class="sub">{n} unit{"s" if n != 1 else ""} can\'t be fully prepared yet. '
        "Ask the leasing team for what's missing, then run a check again. "
        "Nothing is guessed or filled in for you.</p>"
        f"{units}"
        '<div class="actions"><button class="btn dark" data-action="ack_missing" '
        f'data-value="{_esc(",".join(sorted(keys)))}">Got it</button></div>'
        "</div></div>"
    )


def render(results: list[Result], operator: str = "", status: str = "",
           senders: dict | None = None, unknown: list | None = None, notice: str = "",
           acknowledged: set[str] | None = None, done_units: set[str] | None = None,
           connections: list | None = None, settings_button: bool = False,
           guide: bool | None = None) -> str:
    """
    The whole board as one HTML page.

    `operator` is accepted for callers that still pass it and is not shown.
    `senders` ({"addresses": [...], "names": [...]}) adds the editable sender
    panel, with `unknown` [(sender, threads)] as one-click suggestions; the
    in-app board passes them, the plain HTML file from the CLI does not.
    `notice` is a one-line message at the top, such as "Added the address".
    `done_units` are units carried out this session; they show as Done.
    `connections` [(key, label, state)] adds the sign-in panel for the app's
    browser (live mode only).
    `acknowledged` holds missing-field keys the user has already seen (see
    missing_keys); the missing-info pop-up opens by itself only for new ones.
    `status` is the watching line for the top right ("Watching Move ins, last
    check 4:31 PM"); without it the board says when it was generated.
    """
    by = lambda v: [r for r in results if r.verdict is v]  # noqa: E731
    interactive = senders is not None
    needs, unclear = by(Verdict.NEEDS_OUTREACH), by(Verdict.UNCLEAR)
    handled, skipped = by(Verdict.HANDLED), by(Verdict.SKIPPED_CORPORATE)

    generated = datetime.now().strftime("%I:%M %p").lstrip("0")
    status_text = status or f"Updated {generated}"

    cards = [
        ("Need outreach", len(needs), bool(needs), "k-needs"),
        ("Handled", len(handled), False, "k-handled"),
        ("Skipped, corporate", len(skipped), False, "k-skipped"),
        ("Check", len(unclear), bool(unclear), "k-check"),
    ]
    cards_html = "".join(
        f'<div class="card {kind}{" hot" if hot else ""}"><div class="label">{_esc(label)}</div>'
        f'<div class="n">{n}</div></div>'
        for label, n, hot, kind in cards
    )

    # Order: what needs you, then what to check, then skipped, then done.
    order = needs + unclear + skipped + handled
    if order:
        table = (
            '<div class="table"><table>'
            "<thead><tr><th style=\"width:14%\">Unit</th><th style=\"width:16%\">Move-in</th>"
            "<th>Checks</th><th style=\"width:20%\">Status</th></tr></thead><tbody>"
            + "".join(_row(r, interactive, r.unit in (done_units or set())) for r in order)
            + "</tbody></table></div>"
        )
    else:
        table = ('<div class="table"><div class="empty">No move-in leases in the window yet. '
                 "The board fills in after the first check.</div></div>")

    # Open browser: the app's own browser (Outlook, RiseIO, Xfinity tabs), from
    # the top of the board, whenever there are systems to sign in to.
    # The design's header: sites as glass nav pills, Help once the guide is
    # hidden, and Settings as the gooey pill button (Run check without it).
    nav = []
    if interactive and connections is not None:
        nav += [("Email", "open_site", "outlook"), ("RiseIO", "open_site", "riseio"), ("Xfinity", "open_site", "xfinity")]
    if interactive and guide is False:
        nav.append(("Help", "guide_show", ""))
    head_button = ("Settings", "open_settings", "") if settings_button else ("Run check", "run", "")
    header_html = hero.header(nav, head_button, interactive)
    guide_html = _guide() if interactive and guide else ""
    notice_html = f'<div class="notice">{_esc(notice)}</div>' if notice else ""
    panel = _senders_panel(senders, unknown or []) if interactive else ""
    if interactive and connections:
        panel = _connections_panel(connections) + panel
    popup = _missing_popup(results, acknowledged or set()) if interactive else ""
    # Empty on disk: the sheet's resident details are injected from memory when
    # it opens, so they never reach this file.
    sheet_host = ('<div class="veil" id="sheetveil"><div class="pop" role="dialog" '
                  'aria-label="Prepare unit"><div id="sheet-host"></div></div></div>') if interactive else ""

    lede = ("Checked against every reply in the thread before anything is suggested. "
            "Read-only: nothing is sent, saved or changed in Outlook.")
    l1, l2, l3 = _hero_lines(len(needs), len(unclear))
    heading_html = hero.heading(
        pill=status_text, line1=l1, line2=l2, line3=l3, paragraph=lede,
        ghost=("Open Outlook", "open_site", "outlook"), bright=("Run check", "run", ""),
        interactive=interactive)

    return f"""<!DOCTYPE html>
<html lang="en"{' data-ready="1"' if results else ""}>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Housewarm</title>
<style>{CSS}{hero.CSS}{hero.THEME_CSS}</style>
{INTRO_HEAD}
</head>
<body>
{hero.backdrop()}
{header_html}
<div class="wrap">
{notice_html}
<div class="page-hero hero-in">{heading_html}</div>
{guide_html}
<div class="cards">{cards_html}</div>
{table}
{panel}
{popup}
{sheet_host}
<div class="foot">
Units and statuses only. Residents' names, email addresses and phone numbers stay in Outlook.
Select a unit to search Outlook for its thread.
</div>
</div>
{_SCRIPT if interactive else ""}
{MOTION_BODY}
{SCROLL_JS}
{hero.script()}
{"<script>" + confirm_sheet.SCRIPT + "</script>" if interactive else ""}
</body>
</html>
"""
