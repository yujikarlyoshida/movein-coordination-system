#!/bin/sh
# Housewarm demo. Double-click to run.
#
# Practice mode: a sample mailbox with fictional residents, and practice copies
# of Outlook, Xfinity and RiseIO. No real resident data reaches the screen and
# nothing real is sent or saved, so it is safe to show anyone.
#
# Your live Housewarm is closed for the demo (the two share one board). When
# you choose Quit from the Housewarm menu bar item, your live Housewarm starts
# again by itself. This window can be closed at any time: the demo runs on its
# own (found after a real demo: tied to this window, closing it took both down).

cd "$(dirname "$0")" || exit 1
SUPPORT="$HOME/Library/Application Support/MoveInTriage"
mkdir -p "$SUPPORT"

echo "Housewarm demo: practice mode, fictional data only."
echo "Closing your live Housewarm for the demo..."
pids=$(pgrep -f "Python.*menubar.py")
if [ -n "$pids" ]; then
  kill $pids 2>/dev/null
  i=0
  while pgrep -f "Python.*menubar.py" >/dev/null && [ $i -lt 20 ]; do sleep 0.25; i=$((i + 1)); done
  pkill -9 -f "Python.*menubar.py" 2>/dev/null
fi

# Practice mode, then the live app once practice mode quits, in a session of
# its own so nothing here can take it down.
.venv/bin/python -c 'import subprocess, sys
log = open(sys.argv[1], "a")
subprocess.Popen(["/bin/sh", "-c", ".venv/bin/python menubar.py --practice; exec .venv/bin/python menubar.py --board"],
                 start_new_session=True, stdin=subprocess.DEVNULL, stdout=log, stderr=log)' "$SUPPORT/run.log"

echo
echo "Practice mode is starting."
echo "When you're done, choose Quit from the Housewarm menu bar item:"
echo "your live Housewarm starts again by itself. You can close this window."
