#!/bin/sh
# Housewarm demo. Double-click to run.
#
# Practice mode: a sample mailbox with fictional residents, and practice copies
# of Outlook, Xfinity and RiseIO. No real resident data reaches the screen and
# nothing real is sent or saved, so it is safe to show anyone.
#
# Your live Housewarm is closed for the demo (the two share one board) and
# started again when the demo ends: quit Housewarm from the menu bar, or close
# this window. Your sign-ins may ask for a verification again afterwards.

cd "$(dirname "$0")" || exit 1
SUPPORT="$HOME/Library/Application Support/MoveInTriage"
mkdir -p "$SUPPORT"

stop_housewarm() {
  pids=$(pgrep -f "Python.*menubar.py")
  [ -z "$pids" ] && return
  kill $pids 2>/dev/null
  i=0
  while pgrep -f "Python.*menubar.py" >/dev/null && [ $i -lt 20 ]; do sleep 0.25; i=$((i + 1)); done
  pkill -9 -f "Python.*menubar.py" 2>/dev/null
}

back_to_live() {
  trap - EXIT INT TERM HUP
  stop_housewarm
  echo
  echo "Demo over. Starting your live Housewarm again."
  nohup .venv/bin/python menubar.py --board >> "$SUPPORT/run.log" 2>&1 &
  exit 0
}

echo "Housewarm demo: practice mode, fictional data only."
echo "Closing your live Housewarm for the demo..."
stop_housewarm
trap back_to_live EXIT INT TERM HUP

echo "Starting practice mode. Quit Housewarm from the menu bar when you're done."
.venv/bin/python menubar.py --practice
