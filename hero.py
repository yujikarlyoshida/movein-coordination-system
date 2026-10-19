"""
The shader hero: a dark, moving mesh-gradient stage with a pulsing badge.

A faithful port of a React "ShaderShowcase" hero (Paper Shaders + framer-motion)
to the app's plain HTML pages, which have no React. Every parameter is kept:

  * two MeshGradient layers, the second at 60% opacity, with the original
    colours and speeds (backgroundColor and wireframe were never props of
    Paper Shaders and had no effect, so they are omitted)
  * the PulsingBorder badge with all fourteen of its settings, and the
    rotating ring of text around it (20 s per turn, drawn 1.6x)
  * the SVG filters (glass, gooey, logo glow, text glow) and gradients
  * the entrance timings (pill 0.6 s at 0.2 s, headline 0.8 s at 0.4 s,
    paragraph at 0.8 s, buttons at 1.0 s), the 8 s headline gradient loop,
    hover and press springs (scale 1.05 / 0.95; the logo at stiffness 400,
    damping 10), and the logo's sparkles
  * every Tailwind size, weight, colour and spacing, written out as CSS

Only the words change, to Housewarm's own; and the template's emoji is left
out (no emoji anywhere in the app). The shader code is the vendored bundle in
vendor/paper-shaders/, copied next to the page (install()) and loaded from
there by name, so the page fetches nothing from the network.

Pure: returns strings. Under Reduce motion the shaders hold still and nothing
animates.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

_VENDOR = Path(__file__).resolve().parent / "vendor" / "paper-shaders" / "shaders.min.js"

# The design's parameters, exactly as given.
MESH_LAYERS = [
    {"colors": ["#000000", "#06b6d4", "#0891b2", "#164e63", "#f97316"], "speed": 0.3, "opacity": 1},
    {"colors": ["#000000", "#ffffff", "#06b6d4", "#f97316"], "speed": 0.2, "opacity": 0.6},
]
PULSING_BORDER = {
    "colors": ["#06b6d4", "#0891b2", "#f97316", "#00FF88", "#FFD700", "#FF6B35", "#ffffff"],
    "colorBack": "#00000000",
    "speed": 1.5,
    "roundness": 1,
    "thickness": 0.1,
    "softness": 0.2,
    "intensity": 5,
    "spotsPerColor": 5,
    "spotSize": 0.1,
    "pulse": 0.1,
    "smoke": 0.5,
    "smokeSize": 4,
    "scale": 0.65,
    "rotation": 0,
    "frame": 9161408.251009725,
}
# Sized to the badge's circle (r=38, about 239 units): the text closes on
# itself with a separator instead of overrunning its own start.
RING_TEXT = "Housewarm · Every move-in, done · "

# An "H" in the same 100x100 box and stroke weight as the design's mark.
LOGO_PATH = "M15 85V15h12v29h46V15h12v70H73V56H27v29H15z"


def _esc(text: object) -> str:
    return html.escape(str(text or ""), quote=True)


SCRIPT_NAME = "paper-shaders.js"


def install(folder) -> None:
    """Put the shader bundle next to a page that uses the hero. Pages load it
    by this relative name; WebKit may read only the page's own folder."""
    import os
    import shutil

    target = Path(folder) / SCRIPT_NAME
    try:
        if not target.exists() or target.stat().st_size != _VENDOR.stat().st_size:
            shutil.copyfile(_VENDOR, target)
            os.chmod(target, 0o644)
    except OSError:
        pass          # No bundle: the stage stays black, nothing breaks.


CSS = r"""
/* ---- The shader hero ---------------------------------------------------- */
.hero { position: relative; overflow: hidden; background: #000000; color: #ffffff;
  font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; isolation: isolate }
.hero.full { min-height: 100vh; width: 100vw; margin-left: calc(50% - 50vw) }
.hero.band { min-height: 560px; border-radius: 28px; margin: 28px 0 32px }
.hero .stage { position: absolute; inset: 0; width: 100%; height: 100% }
.hero .stage.soft { opacity: .6 }
.hero .defs { position: absolute; inset: 0; width: 0; height: 0 }
/* Paper Shaders' own canvas rules, unlayered and un-nested so every WebKit
   applies them (its built-in stylesheet uses both). */
.stage, .ring-pb { isolation: isolate }
.ring-pb { position: relative }
.stage > canvas, .ring-pb > canvas { contain: strict; display: block; position: absolute; inset: 0; z-index: -1;
  width: 100%; height: 100%; border-radius: inherit }
.hero-head { position: relative; z-index: 20; display: flex; align-items: center; justify-content: space-between; padding: 24px }
.hero-logo { position: relative; display: flex; align-items: center; cursor: pointer; background: none; border: 0; padding: 0; color: #ffffff }
.hero-logo svg { width: 40px; height: 40px; filter: url(#logo-glow); transition: all .3s; fill: currentColor }
.hero-logo:hover svg { filter: url(#logo-glow) drop-shadow(0 10px 8px rgb(0 0 0 / .04)) drop-shadow(0 4px 3px rgb(0 0 0 / .1)) }
.hero-logo .wm { margin-left: 10px; font-size: 15px; font-weight: 600; letter-spacing: -.01em }
.hero-logo .sparks { position: absolute; inset: 0; opacity: 0; transition: opacity .3s; pointer-events: none }
.hero-logo:hover .sparks { opacity: 1 }
.hero-logo .spark { position: absolute; width: 4px; height: 4px; border-radius: 999px; background: rgb(255 255 255 / .6);
  animation: spark 2s ease-in-out infinite both }
@keyframes spark {
  0% { transform: translate(0, -10px) scale(0); opacity: 0 }
  50% { transform: translate(var(--sx, 0px), -20px) scale(1); opacity: 1 }
  100% { transform: translate(0, -10px) scale(0); opacity: 0 }
}
.hero-nav { display: flex; align-items: center; gap: 8px }
.hero-nav button, .hero-nav a { font: inherit; font-size: 12px; font-weight: 300; color: rgb(255 255 255 / .8); background: transparent;
  border: 0; padding: 8px 12px; border-radius: 999px; cursor: pointer; text-decoration: none; transition: all .2s }
.hero-nav button:hover, .hero-nav a:hover { color: #ffffff; background: rgb(255 255 255 / .1) }
.gooey { position: relative; display: flex; align-items: center; filter: url(#gooey-filter) }
.gooey button { font: inherit; border: 0; cursor: pointer; border-radius: 999px; background: #ffffff; color: #000000;
  font-size: 12px; font-weight: 400; height: 32px; display: flex; align-items: center; justify-content: center; transition: all .3s }
.gooey button:hover { background: rgb(255 255 255 / .9) }
.gooey .arrow { position: absolute; right: 0; padding: 8px 10px; z-index: 0; transform: translateX(-2.5rem) }
.gooey:hover .arrow { transform: translateX(-4.75rem) }
.gooey .arrow svg { width: 12px; height: 12px }
.gooey .main { padding: 8px 24px; z-index: 10; position: relative }
.hero-main { position: absolute; bottom: 32px; left: 32px; z-index: 20; max-width: 42rem; text-align: left }
.hero-pill { position: relative; display: inline-flex; align-items: center; padding: 8px 16px; border-radius: 999px;
  background: rgb(255 255 255 / .05); -webkit-backdrop-filter: blur(4px); backdrop-filter: blur(4px); margin-bottom: 24px;
  border: 1px solid rgb(255 255 255 / .1); filter: url(#glass-effect) }
.hero-pill .line { position: absolute; top: 0; left: 4px; right: 4px; height: 1px; border-radius: 999px;
  background: linear-gradient(to right, transparent, rgb(34 211 238 / .3), transparent) }
.hero-pill span { position: relative; z-index: 10; color: rgb(255 255 255 / .9); font-size: 14px; font-weight: 500; letter-spacing: .025em }
.hero-h1 { margin: 0 0 24px; font-size: 60px; font-weight: 700; color: #ffffff; line-height: 1; letter-spacing: -.025em }
.hero-h1 .l1 { display: block; font-weight: 300; color: rgb(255 255 255 / .9); font-size: 36px; margin-bottom: 8px; letter-spacing: .05em;
  background: linear-gradient(135deg, #ffffff 0%, #06b6d4 30%, #f97316 70%, #ffffff 100%);
  -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; filter: url(#text-glow);
  animation: hero-pan 8s linear infinite }
@keyframes hero-pan { 0% { background-position: 0% 50% } 50% { background-position: 100% 50% } 100% { background-position: 0% 50% } }
.hero-h1 .l2 { display: block; font-weight: 900; color: #ffffff; filter: drop-shadow(0 25px 25px rgb(0 0 0 / .15)) }
.hero-h1 .l3 { display: block; font-weight: 300; color: rgb(255 255 255 / .8); font-style: italic }
.hero-p { margin: 0 0 32px; font-size: 18px; font-weight: 300; color: rgb(255 255 255 / .7); line-height: 1.625; max-width: 36rem }
.hero-actions { display: flex; align-items: center; gap: 24px; flex-wrap: wrap }
.hero-btn { font: inherit; cursor: pointer; padding: 16px 40px; border-radius: 999px; font-size: 14px; transition: all .3s }
.hero-btn.ghost { background: transparent; border: 2px solid rgb(255 255 255 / .3); color: #ffffff; font-weight: 500;
  -webkit-backdrop-filter: blur(4px); backdrop-filter: blur(4px) }
.hero-btn.ghost:hover { background: rgb(255 255 255 / .1); border-color: rgb(34 211 238 / .5); color: #cffafe }
.hero-btn.bright { border: 0; color: #ffffff; font-weight: 600; background-image: linear-gradient(to right, #06b6d4, #f97316);
  box-shadow: 0 10px 15px -3px rgb(0 0 0 / .1), 0 4px 6px -4px rgb(0 0 0 / .1) }
.hero-btn.bright:hover { background-image: linear-gradient(to right, #22d3ee, #fb923c);
  box-shadow: 0 20px 25px -5px rgb(0 0 0 / .1), 0 8px 10px -6px rgb(0 0 0 / .1) }
.hero-badge { position: absolute; bottom: 32px; right: 32px; z-index: 30 }
.hero-badge .wrap { position: relative; width: 80px; height: 80px; display: flex; align-items: center; justify-content: center }
.hero-badge .ring-pb { width: 60px; height: 60px; border-radius: 50% }
.hero-badge svg.ring { position: absolute; inset: 0; width: 100%; height: 100%; transform: scale(1.6);
  animation: hero-spin 20s linear infinite }
@keyframes hero-spin { from { transform: scale(1.6) rotate(0deg) } to { transform: scale(1.6) rotate(360deg) } }
.hero-badge svg.ring text { font-size: 14px; font-weight: 500; fill: rgb(255 255 255 / .8) }
/* Entrances, as in the design. A timer drops "hero-in" once they are over, so
   a paused animation can never leave text invisible. */
@keyframes hero-rise { from { opacity: 0; transform: translateY(var(--dy, 20px)) } to { opacity: 1; transform: none } }
.hero-in .hero-pill { animation: hero-rise .6s ease-out .2s both }
.hero-in .hero-h1 { --dy: 30px; animation: hero-rise .8s ease-out .4s both }
.hero-in .hero-p { animation: hero-rise .6s ease-out .8s both }
.hero-in .hero-actions { animation: hero-rise .6s ease-out 1s both }
@media (min-width: 768px) {
  .hero-h1 { font-size: 72px } .hero-h1 .l1 { font-size: 48px }
}
@media (min-width: 1024px) {
  .hero-h1 { font-size: 96px } .hero-h1 .l1 { font-size: 60px }
}
@media (max-width: 720px) {
  .hero-nav { display: none }
  .hero-main { left: 16px; right: 16px; bottom: 120px }
  .hero-badge { right: 16px; bottom: 16px }
}
/* In the board's band the content flows under the header rather than sitting
   at the bottom of a full window, at sizes that fit a band. */
.hero.band { min-height: 0 }
.hero.band .hero-main { position: relative; bottom: auto; left: auto; padding: 4px 32px 40px; max-width: 46rem }
.hero.band .hero-h1 { font-size: 64px } .hero.band .hero-h1 .l1 { font-size: 40px }
.hero.band .hero-pill { margin-bottom: 20px }
@media (max-width: 720px) {
  .hero.band .hero-main { padding: 4px 16px 120px }
  .hero.band .hero-h1 { font-size: 44px } .hero.band .hero-h1 .l1 { font-size: 30px }
}
@media (prefers-reduced-motion: reduce) {
  .hero *, .hero *::before { animation: none !important; transition: none !important }
}
"""

# The SVG filters and gradients, as in the design.
DEFS = """<svg class="defs" aria-hidden="true"><defs>
<filter id="glass-effect" x="-50%" y="-50%" width="200%" height="200%">
<feTurbulence baseFrequency="0.005" numOctaves="1" result="noise"></feTurbulence>
<feDisplacementMap in="SourceGraphic" in2="noise" scale="0.3"></feDisplacementMap>
<feColorMatrix type="matrix" values="1 0 0 0 0.02 0 1 0 0 0.02 0 0 1 0 0.05 0 0 0 0.9 0" result="tint"></feColorMatrix>
</filter>
<filter id="gooey-filter" x="-50%" y="-50%" width="200%" height="200%">
<feGaussianBlur in="SourceGraphic" stdDeviation="4" result="blur"></feGaussianBlur>
<feColorMatrix in="blur" mode="matrix" values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 19 -9" result="gooey"></feColorMatrix>
<feComposite in="SourceGraphic" in2="gooey" operator="atop"></feComposite>
</filter>
<filter id="logo-glow" x="-50%" y="-50%" width="200%" height="200%">
<feGaussianBlur stdDeviation="3" result="coloredBlur"></feGaussianBlur>
<feMerge><feMergeNode in="coloredBlur"></feMergeNode><feMergeNode in="SourceGraphic"></feMergeNode></feMerge>
</filter>
<linearGradient id="logo-gradient" x1="0%" y1="0%" x2="100%" y2="100%">
<stop offset="0%" stop-color="#06b6d4"></stop><stop offset="50%" stop-color="#ffffff"></stop><stop offset="100%" stop-color="#0891b2"></stop>
</linearGradient>
<linearGradient id="hero-gradient" x1="0%" y1="0%" x2="100%" y2="100%">
<stop offset="0%" stop-color="#ffffff"></stop><stop offset="30%" stop-color="#06b6d4"></stop>
<stop offset="70%" stop-color="#f97316"></stop><stop offset="100%" stop-color="#ffffff"></stop>
</linearGradient>
<filter id="text-glow" x="-50%" y="-50%" width="200%" height="200%">
<feGaussianBlur stdDeviation="2" result="coloredBlur"></feGaussianBlur>
<feMerge><feMergeNode in="coloredBlur"></feMergeNode><feMergeNode in="SourceGraphic"></feMergeNode></feMerge>
</filter>
</defs></svg>"""


def _sparks() -> str:
    """Six sparkles around the logo, placed and drifted at random as in the
    design (20-80% across and down, up to 10 px sideways), 0.2 s apart."""
    import random

    rnd = random.Random()
    return "".join(
        f'<span class="spark" style="left: {20 + rnd.random() * 60:.1f}%; top: {20 + rnd.random() * 60:.1f}%; '
        f'--sx: {rnd.random() * 20 - 10:.1f}px; animation-delay: {i * 0.2:.1f}s"></span>'
        for i in range(6)
    )


def _logo() -> str:
    return (f'<span class="hero-logo" role="img" aria-label="Housewarm">'
            f'<svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">'
            f'<path class="logo-path" d="{LOGO_PATH}"></path></svg>'
            f'<span class="wm">Housewarm</span><span class="sparks">{_sparks()}</span></span>')


def _btn(cls: str, b: tuple[str, str, str]) -> str:
    label, action, value = b
    v = f' data-value="{_esc(value)}"' if value else ""
    return f'<button class="{cls}" data-action="{_esc(action)}"{v}>{_esc(label)}</button>'


def header(nav: list[tuple[str, str, str]] | None = None,
           head_button: tuple[str, str, str] | None = None,
           interactive: bool = True, extra: str = "") -> str:
    """The design's header: glowing logo, glass nav pills, gooey pill button.
    Buttons are (label, action, value) for the page's bridge (data-action);
    a plain (non-interactive) page gets the logo only."""
    nav_html = "".join(_btn("on" if len(b) > 3 and b[3] else "", b[:3]) for b in (nav or [])) if interactive else ""
    head = ""
    if head_button and interactive:
        label, action, value = head_button[:3]
        v = f' data-value="{_esc(value)}"' if value else ""
        head = (f'<div class="gooey">'
                f'<button class="arrow" data-action="{_esc(action)}"{v} aria-label="{_esc(label)}">'
                '<svg fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">'
                '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 17L17 7M17 7H7M17 7V17"></path></svg></button>'
                f'<button class="main" data-action="{_esc(action)}"{v}>{_esc(label)}</button></div>')
    return (f'<header class="hero-head">{_logo()}<nav class="hero-nav" aria-label="Housewarm">{nav_html}{extra}</nav>'
            f'{head or "<span></span>"}</header>')


def heading(*, pill: str, line1: str, line2: str, line3: str, paragraph: str = "",
            ghost: tuple[str, str, str] | None = None, bright: tuple[str, str, str] | None = None,
            interactive: bool = True) -> str:
    """The design's text block: glass pill, three-line headline, paragraph,
    ghost and bright pill buttons."""
    buttons = "".join(_btn(c, b) for c, b in (("hero-btn ghost", ghost), ("hero-btn bright", bright)) if b)
    actions = f'<div class="hero-actions">{buttons}</div>' if interactive and buttons else ""
    para = f'<p class="hero-p">{_esc(paragraph)}</p>' if paragraph else ""
    return (f'<div class="hero-pill"><div class="line"></div><span>{_esc(pill)}</span></div>'
            f'<h1 class="hero-h1"><span class="l1">{_esc(line1)}</span><span class="l2">{_esc(line2)}</span>'
            f'<span class="l3">{_esc(line3)}</span></h1>{para}{actions}')


def badge() -> str:
    """The pulsing border with its rotating ring of text."""
    return f"""<div class="hero-badge"><div class="wrap">
<div class="ring-pb" data-pulsing="1"></div>
<svg class="ring" viewBox="0 0 100 100" aria-hidden="true"><defs>
<path id="hero-circle" d="M 50, 50 m -38, 0 a 38,38 0 1,1 76,0 a 38,38 0 1,1 -76,0"></path></defs>
<text><textPath href="#hero-circle" startOffset="0%" textLength="236" lengthAdjust="spacing">{_esc(RING_TEXT)}</textPath></text></svg>
</div></div>"""


def backdrop() -> str:
    """The whole-window stage for an app page: the SVG filters, both mesh
    layers fixed behind everything, and the pulsing badge in the corner."""
    return (f'<div class="backdrop" aria-hidden="true">{DEFS}<div class="stage" data-mesh="0"></div>'
            f'<div class="stage soft" data-mesh="1"></div></div>{badge()}')


def render(*, pill: str, line1: str, line2: str, line3: str, paragraph: str,
           ghost: tuple[str, str, str], bright: tuple[str, str, str],
           nav: list[tuple[str, str, str]] | None = None,
           head_button: tuple[str, str, str] | None = None, full: bool = True,
           interactive: bool = True) -> str:
    """The full-window hero, as in the design (the welcome page's opening)."""
    return f"""<section class="hero {'full' if full else 'band'} hero-in" aria-label="Housewarm">
{DEFS}
<div class="stage" data-mesh="0"></div>
<div class="stage soft" data-mesh="1"></div>
{header(nav, head_button, interactive)}
<div class="hero-main">
{heading(pill=pill, line1=line1, line2=line2, line3=line3, paragraph=paragraph, ghost=ghost, bright=bright, interactive=interactive)}
</div>
{badge()}
</section>"""


def script() -> str:
    """Loads the bundle beside the page, mounts the shaders and runs the springs."""
    config = json.dumps({"mesh": MESH_LAYERS, "pulsing": PULSING_BORDER})
    return ('<script src="' + SCRIPT_NAME + '"></script>\n<script>\n(function () {\n'
            "var cfg = " + config + ";\n" + r"""
var hero = document;
if (!document.querySelector('[data-mesh]')) return;
var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
// The entrance is over by 2.2 s; never leave text hidden after that.
setTimeout(function () {
  document.querySelectorAll('.hero-in').forEach(function (el) { el.classList.remove('hero-in'); });
}, 2200);
var S = window.HousewarmShaders;
if (S) {
  try {
    hero.querySelectorAll('[data-mesh]').forEach(function (el) {
      var layer = cfg.mesh[+el.dataset.mesh];
      S.meshGradient(el, { colors: layer.colors, speed: reduce ? 0 : layer.speed });
    });
    var pb = hero.querySelector('[data-pulsing]');
    if (pb) {
      var p = Object.assign({}, cfg.pulsing);
      if (reduce) p.speed = 0;
      S.pulsingBorder(pb, p);
    }
  } catch (e) { /* No WebGL: the stage stays black. */ }
}
if (reduce || !Element.prototype.animate) return;
// framer-motion's springs, simulated: whileHover scale 1.05 / whileTap 0.95
// use its default transform spring (stiffness 500, damping 25); the logo
// uses stiffness 400, damping 10, as in the design.
function spring(stiffness, damping) {
  var x = 0, v = 0, dt = 1 / 120, pts = [], t = 0;
  while (t < 3) {
    var a = -stiffness * (x - 1) - damping * v;
    v += a * dt; x += v * dt; t += dt; pts.push(x);
    if (t > 0.1 && Math.abs(x - 1) < 0.001 && Math.abs(v) < 0.01) break;
  }
  var step = Math.max(1, Math.floor(pts.length / 60)), out = ['0'];
  for (var i = step; i < pts.length; i += step) out.push(pts[i].toFixed(4));
  out.push('1');
  return { easing: 'linear(' + out.join(', ') + ')', duration: Math.round(t * 1000) };
}
var soft = spring(500, 25), bouncy = spring(400, 10);
function springTo(el, s, scale) {
  var from = getComputedStyle(el).scale;
  from = from && from !== 'none' ? from : '1';
  el.animate([{ scale: from }, { scale: String(scale) }], { duration: s.duration, easing: s.easing, fill: 'forwards' });
}
hero.querySelectorAll('.hero-btn').forEach(function (b) {
  b.addEventListener('mouseenter', function () { springTo(b, soft, 1.05); });
  b.addEventListener('mouseleave', function () { springTo(b, soft, 1); });
  b.addEventListener('mousedown', function () { springTo(b, soft, 0.95); });
  b.addEventListener('mouseup', function () { springTo(b, soft, 1.05); });
});
var logo = hero.querySelector('.hero-logo');
if (logo) {
  var svg = logo.querySelector('svg'), path = logo.querySelector('.logo-path');
  logo.addEventListener('mouseenter', function () {
    springTo(logo, bouncy, 1.05);
    svg.animate([{ rotate: '0deg' }, { rotate: '-2deg' }, { rotate: '2deg' }, { rotate: '0deg' }], { duration: 600, easing: 'ease-in-out' });
    path.setAttribute('fill', 'url(#logo-gradient)');
    var len = path.getTotalLength ? path.getTotalLength() : 400;
    path.style.strokeDasharray = len; path.setAttribute('stroke', 'url(#logo-gradient)'); path.setAttribute('stroke-width', '1');
    path.animate([{ strokeDashoffset: 0 }, { strokeDashoffset: len }, { strokeDashoffset: 0 }], { duration: 1200, easing: 'ease-in-out' });
  });
  logo.addEventListener('mouseleave', function () {
    springTo(logo, bouncy, 1);
    path.removeAttribute('fill');
  });
}
})();
</script>""")


# ---------------------------------------------------------------------------
# The app's theme: every page in the design's language
# ---------------------------------------------------------------------------
#
# Applied after the board's stylesheet, it replaces the light look outright:
# black ground under the moving mesh, white type, dark glass surfaces with the
# design's white/10 hairlines, the cyan-to-orange gradient for anything primary,
# and the design's glass pills for anything secondary.
THEME_CSS = r"""
:root {
  color-scheme: dark;
  --paper: #000000; --card: rgb(0 0 0 / .38); --ink: #ffffff; --ink-2: rgb(255 255 255 / .7);
  --line: rgb(255 255 255 / .1); --fill: rgb(255 255 255 / .08); --accent: #06b6d4;
  --grad: linear-gradient(to right, #06b6d4, #f97316);
  --grad-loop: linear-gradient(100deg, #ffffff 0%, #06b6d4 25%, #f97316 50%, #06b6d4 75%, #ffffff 100%);
  --hi: rgb(249 115 22 / .16); --soft: rgb(6 182 212 / .16);
  --c-needs: #f97316; --c-check: #fdba74; --c-handled: #06b6d4; --c-skipped: rgb(255 255 255 / .3);
  --shadow: 0 1px 0 rgb(255 255 255 / .06) inset, 0 20px 40px rgb(0 0 0 / .35);
}
html, body { background: #000000 }
body { color: #ffffff; font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; padding: 0 32px 120px }
body::before { display: none }
a { color: #67e8f9 } a:hover { color: #cffafe }

/* The stage: both mesh layers fixed behind every page. */
.backdrop { position: fixed; inset: 0; z-index: -1; background: #000000; pointer-events: none }
.backdrop .stage { position: absolute; inset: 0; width: 100%; height: 100% }
.backdrop .stage.soft { opacity: .6 }
.backdrop .defs { position: absolute; width: 0; height: 0 }
body > .hero-badge { position: fixed; bottom: 32px; right: 32px; z-index: 30 }
.hero-badge .wrap { max-width: none; margin: 0 }

/* Header: the design's, pinned; it gains a glass floor once the page moves. */
body > .hero-head { position: sticky; top: 0; z-index: 40; margin: 0 -32px; padding: 24px 32px;
  transition: background-color .3s ease, -webkit-backdrop-filter .3s ease }
.scrolled body > .hero-head { background: rgb(0 0 0 / .45); -webkit-backdrop-filter: blur(16px); backdrop-filter: blur(16px) }
.hero-nav button.on { color: #ffffff; background: rgb(255 255 255 / .12) }
.hero-nav .sep { width: 1px; height: 16px; background: rgb(255 255 255 / .15); margin: 0 4px }
.wrap { max-width: 1040px }
.page-hero { margin: 40px 0 40px; max-width: 44rem }
.page-hero .hero-h1 { font-size: 72px } .page-hero .hero-h1 .l1 { font-size: 48px }
.wz .page-hero .hero-h1 { font-size: 60px } .wz .page-hero .hero-h1 .l1 { font-size: 38px }

/* Surfaces: dark glass with the design's hairline. */
.card, .table, .panel, .pop, .choice, .howto {
  background: rgb(0 0 0 / .38) !important; color: #ffffff;
  -webkit-backdrop-filter: blur(18px) saturate(140%); backdrop-filter: blur(18px) saturate(140%);
  border: 1px solid rgb(255 255 255 / .1) !important; box-shadow: var(--shadow) }
.card .label, .panel .sub, .pop .sub, th, .note, .status, .foot, .chip .kind, .sites-label, .g-status span:last-child,
.f label, .steps .why, .sheet .fine, .progress .detail, .field label, .field .help, .why, .choice span, .promises li span {
  color: rgb(255 255 255 / .62) !important }
.cards .card::before { height: 3px }
.cards .k-needs { --k: var(--grad) }
.card.hot { border: 1px solid rgb(249 115 22 / .65) !important; animation: none;
  box-shadow: 0 0 0 1px rgb(6 182 212 / .25), 0 0 28px rgb(249 115 22 / .22), var(--shadow) }
.intro .card.hot { animation-name: lift, fade; animation-duration: 1.05s, .75s; animation-iteration-count: 1, 1 }
.card .n { color: #ffffff }
.card.hot .n { background-image: linear-gradient(135deg, #ffffff 0%, #06b6d4 35%, #f97316 100%); animation: none }
td { border-bottom: 1px solid rgb(255 255 255 / .08) }
th { border-bottom: 1px solid rgb(255 255 255 / .1) }
tbody tr:hover { background-color: rgb(255 255 255 / .04) }
.unit a { color: #ffffff } .unit a:hover { color: #67e8f9 }
tr[data-verdict="handled"] .unit a, tr[data-verdict="skipped_corporate"] .unit a { color: rgb(255 255 255 / .55) }
.check { color: #ffffff } .gap { color: rgb(255 255 255 / .85) } .gap .blocks { color: rgb(255 255 255 / .55) }

/* Statuses. */
.pill { font-weight: 500; letter-spacing: .01em }
.p-needs { background-image: var(--grad); color: #ffffff }
.p-unclear { background: rgb(249 115 22 / .16); color: #fdba74; border: 1px solid rgb(249 115 22 / .35) }
.p-handled, .p-done { background: rgb(6 182 212 / .16); color: #67e8f9; border: 1px solid rgb(6 182 212 / .3) }
.p-skipped { background: rgb(255 255 255 / .07); color: rgb(255 255 255 / .6); border: 1px solid rgb(255 255 255 / .1) }
.p-prepare { background-image: linear-gradient(to right, #06b6d4, #f97316); background-size: auto; animation: none;
  box-shadow: 0 10px 15px -3px rgb(0 0 0 / .3) }
.p-missing { color: #ffffff; border: 1px dashed rgb(255 255 255 / .4) }

/* Buttons: secondary = the design's glass nav pill; primary = its gradient. */
.btn { background: rgb(255 255 255 / .08); color: rgb(255 255 255 / .85); border: 1px solid rgb(255 255 255 / .12); font-weight: 400 }
.btn:hover { background: rgb(255 255 255 / .14); color: #ffffff; filter: none }
.btn.dark { background-image: linear-gradient(to right, #06b6d4, #f97316); color: #ffffff; border: 0; font-weight: 600;
  box-shadow: 0 10px 15px -3px rgb(0 0 0 / .3) }
.btn.dark:hover { background-image: linear-gradient(to right, #22d3ee, #fb923c) }
.btn.later, .nav .later { background: transparent; border: 0; color: #67e8f9 }
.chip { background: rgb(255 255 255 / .07); border: 1px solid rgb(255 255 255 / .1); color: #ffffff }
.chip button { color: #67e8f9 } .chip button:hover { background: rgb(255 255 255 / .08) }

/* Fields. */
.addrow input, .f input, .field input {
  background: rgb(0 0 0 / .35) !important; color: #ffffff !important; border: 1px solid rgb(255 255 255 / .18) !important }
.addrow input::placeholder, .f input::placeholder, .field input::placeholder { color: rgb(255 255 255 / .35) }
.addrow input:focus, .f input:focus, .field input:focus { border-color: #06b6d4 !important; box-shadow: 0 0 0 4px rgb(6 182 212 / .22) !important }
.f.need input { border-color: #f97316 !important; background: rgb(249 115 22 / .12) !important }
.f .hint { color: #fdba74 !important }
.sheet input[type=checkbox], .check input { accent-color: #06b6d4 }

/* Notes and tints. */
.notice { background: rgb(6 182 212 / .14); color: #cffafe; border: 1px solid rgb(6 182 212 / .3) }
.problems { background: rgb(249 115 22 / .14) !important; color: #fed7aa !important; border: 1px solid rgb(249 115 22 / .3) }
.tip { background: rgb(6 182 212 / .12) !important; color: #cffafe }
.verified { background: rgb(6 182 212 / .14) !important; color: #cffafe }
.confirmname, .progress li.your_turn { background: rgb(249 115 22 / .14) !important; color: #ffffff }
.seen .hl { background: rgb(249 115 22 / .2) }
.found { color: #67e8f9 !important }
.veil { background: rgb(0 0 0 / .55); z-index: 60 }
.steps-dots span { background: rgb(255 255 255 / .12) }
.steps-dots span.on { background: linear-gradient(to right, #06b6d4, #f97316) }
.mark { background-image: linear-gradient(135deg, #ffffff 0%, #06b6d4 30%, #f97316 70%, #ffffff 100%) }
.rule { background: linear-gradient(to right, #06b6d4, #f97316) }
.promises li { border-top: 1px solid rgb(255 255 255 / .1) }
h1, h2, h3, .panel h2, .pop h2, .sheet h2 { color: #ffffff }
.wz h1 { font-size: 52px }

@media (max-width: 720px) {
  body { padding: 0 16px 120px }
  body > .hero-head { margin: 0 -16px; padding: 16px }
  .page-hero .hero-h1, .wz .page-hero .hero-h1 { font-size: 44px }
  .page-hero .hero-h1 .l1, .wz .page-hero .hero-h1 .l1 { font-size: 30px }
  body > .hero-badge { right: 16px; bottom: 16px }
}
"""
