"""
py2app build script -- turns the menu bar app into a double-clickable Move-In Triage.app.

    pip install py2app
    python setup_app.py py2app          # release build -> dist/Move-In Triage.app
    python setup_app.py py2app -A       # alias build, for development

The alias build is worth knowing about: it symlinks back to the source instead of
copying it, so edits to the .py files take effect on the next launch without
rebuilding. Faster to iterate with, but the bundle only works on this machine.
Use a full build for anything you hand to somebody else.

Named setup_app.py rather than setup.py so it cannot be mistaken for a packaging
manifest for the library itself.
"""

from setuptools import setup

APP = ["menubar.py"]

# Modules py2app cannot discover by static analysis, because they are imported
# lazily inside functions (deliberately -- see window.py) or pulled in
# dynamically by their own package machinery.
INCLUDES = [
    "app",
    "board",
    "config",
    "graph_client",
    "models",
    "rules",
    "sample_data",
    "sheet",
    "window",
]

PACKAGES = [
    "rumps",
    "msal",
    "requests",
    "certifi",       # requests' CA bundle -- missing it breaks TLS in the bundle
    "charset_normalizer",
    "idna",
    "urllib3",
]

PLIST = {
    "CFBundleName": "Move-In Triage",
    "CFBundleDisplayName": "Move-In Triage",
    "CFBundleIdentifier": "com.yujiyoshida.moveintriage",
    "CFBundleVersion": "1.0.0",
    "CFBundleShortVersionString": "1.0.0",

    # Menu bar utility: no Dock icon, no app switcher entry. The window still
    # opens normally -- you reach it from the menu bar rather than Cmd-Tab.
    # Set this to False if you would rather it behave like an ordinary app.
    "LSUIElement": True,

    # A bundle identifier is what makes rumps.notification() work. Running from
    # source there is no bundle ID, so notifications are silently unavailable --
    # that is expected, not a bug, and it starts working once bundled.
    "NSHumanReadableCopyright": "",

    # No microphone, camera, contacts or location use. Declared as nothing rather
    # than left unstated.
    "NSAppleEventsUsageDescription": "",
}

OPTIONS = {
    "argv_emulation": False,   # True interferes with menu bar apps
    "plist": PLIST,
    "includes": INCLUDES,
    "packages": PACKAGES,
    # Ship the demo fixture so --demo works from the bundle.
    "resources": [],
}

setup(
    app=APP,
    name="Move-In Triage",
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
