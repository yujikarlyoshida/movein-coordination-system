"""
Retired.

This module read the property master spreadsheet from SharePoint and turned it into rows
with per-step completion columns -- orientation, internet, COI, mail key, fob.
The spreadsheet layer was removed by direction; the tool is email-only.

Kept as a marker rather than left as dead code that still imports and half-works.
Nothing references it. If spreadsheet reading is ever wanted again, the history
has it, along with the two things that were genuinely hard-won:

  * Columns had to be matched by header text, not letter. Letters shift whenever
    someone inserts a column -- which happened -- and a letter-based mapping then
    reads the wrong field silently.

  * Completion cells hold staff initials, not ticks. The test is "is this cell
    non-empty", never "does it equal x".
"""

raise ImportError(
    "sheet.py is retired -- this tool is email-only. "
    "Remove the import rather than reinstating this module."
)
