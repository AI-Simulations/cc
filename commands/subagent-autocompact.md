---
description: Set a cc subagent's auto-compact threshold (1-100 percent of its context window, or default). Only when the user asks for it (directly or relayed from their other session)
argument-hint: <subagent> [1-100|default]
allowed-tools: Bash, PowerShell
---

The arguments are: $ARGUMENTS

Split them into a subagent name and, optionally, a whole number from 1 to 100, or `default`.
Quote values with single quotes on the command line. If anything else is given, stop and ask me.

If no subagent was named, run this to list the profiles:

    python "$HOME/.config/cc/cc-switch.py" agents models

If exactly one profile exists, use it. Otherwise ask with the AskUserQuestion tool, one option per
profile (up to 4; the rest are reachable through "Other"), then continue with the chosen one.

Then run this and show its output:

    python "$HOME/.config/cc/cc-switch.py" agents models '<subagent>'

If it reports that the subagent does not exist, show the profile list instead and stop —
never create a profile by accident. The header line shows the profile's current threshold.

If no percentage was given, ask with the AskUserQuestion tool: the question names the current
threshold; offer `default`, `50`, `70` and `85` minus the current one (any other value stays
reachable through "Other"). Then set the chosen value as below.
If a percentage was given, run this and report its output verbatim:

    python "$HOME/.config/cc/cc-switch.py" agents set '<subagent>' --autocompact '<percent>'

The value becomes the subagent's `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`, independent of the main
session's threshold; `default` clears it. If the command fails, show the error and stop; do not
retry and do not edit agents.json yourself.
