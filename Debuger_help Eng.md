## Debugger — Reference Guide
The Tracer can output detailed diagnostics for selected tags directly to the browser console (F12).
## How to Run

   1. Create a text file named debug_tags.txt (the name is case-insensitive, but must be spelled exactly this way).
   2. Enter your tags — one per line (see available flags below).
   3. Open the browser console: Press F12 → go to the Console tab.
   4. Drag and drop debug_tags.txt into the Tracer window (or upload it using the file upload button).
   5. Load an L5X/SLC project — the trace log will start outputting to the console.

An empty file or the absence of this file means the debug mode is turned off.
Lines starting with # are ignored (comments).
To modify the tag list, simply edit debug_tags.txt and upload it again.
## Flags (Suffixes using /)
Flags are appended to the tag name using a forward slash (/). The order of /brief and /online does not matter.

| Entry | Offline Mode | Online Mode |
|---|---|---|
| Tag | verbose (full dump) | — |
| Tag/online | verbose | verbose |
| Tag/brief | errors + state changes only | — |
| Tag/brief/online | brief | verbose |
| Tag/change | only when the value changes | — |
| Tag/change/online | change | verbose |

## Description of Logging Levels

* verbose (default, no flag) — Full dump: loadProject (Pass1/2/3), buildGraph, buildLadderScene, cellState, drawCell, resolveLV. Highly detailed, outputs multiple lines every single frame.
* brief — Quieter: logs only critical information.
* cellState — Logs only when the state changes (on/off/timing/...)
   * resolveLV — Logs UNRESOLVED tags only
   * Pass3 — Logs UNRESOLVED tags; Pass1 — logs unexpected values only (null/empty/NaN)
   * drawCell — Muted
* change — Even quieter than brief: triggers only when a tag value changes.
* online — Always verbose for online components. Enables online checkpoints: plcAddr, getWatchTags, connectWS, mergeValues, _syncWatchList. Can be combined with any offline logging level.

## Example debug_tags.txt

# Verbose — full dump (legacy behavior)
Pause_NoDough

# Brief + online — state changes + full online trace
Motor_Run/brief/online

# Only value changes (the quietest mode)
Conveyor_Timer/change

# Online only (offline tracing remains verbose)
SeqHoming/online

## Console Output Example (Motor_Run/brief/online)

[cellState/brief] "CM_PRO1.Motor_Run"  → on  | v=1 t=XIC
[cellState/brief] "CM_PRO1.Motor_Run"  on → off | v=0 t=XIC
[connectWS/ONLINE] ← values: 45 total, 1 debug-tagged: [Program:CM_PRO1.Motor_Run]
[mergeValues/ONLINE] ⚡ CHANGED wsKey="Program:CM_PRO1.Motor_Run" → new: {value:0}

Note: This replaces the overwhelming flood of 20+ lines per frame generated in verbose mode.
## Tag Matching Tips

* You can specify either the full name (Program:CM_PRO1.Motor_Run) or the short name (Motor_Run).
* Matching works for scoped names (Prog.Tag), sub-elements (Tag.ACC matches the parent Tag), and prefixes (Timer catches Timer.ACC and Timer/DN).
* SLC addresses with a bit delimiter / are fully supported: a / inside an address will not conflict with the flags. The parser strips only known flags (/brief, /change, /online) from the very end of the line, leaving the rest as the tag name.
* B3:0/0 — Bit 0 of word B3:0
   * B3:0/0/brief — The exact same bit running in brief mode
   * B3:0/brief — The entire B3:0 word running in brief mode

------------------------------
