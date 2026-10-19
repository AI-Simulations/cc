---
description: Set a cc subagent's reasoning effort (low, medium, high, xhigh, max, or default)
argument-hint: <subagent> [low|medium|high|xhigh|max|default]
allowed-tools: Bash, PowerShell
---

The arguments are: $ARGUMENTS

Split them into a subagent name and, optionally, one of `low`, `medium`, `high`, `xhigh`, `max`
or `default`. Quote values with single quotes on the command line. If anything else is given,
stop and ask me.

If no subagent was named, run this to list the profiles:

    python "$HOME/.config/cc/cc-switch.py" agents models

If exactly one profile exists, use it. Otherwise ask with the AskUserQuestion tool, one option per
profile (up to 4; the rest are reachable through "Other"), then continue with the chosen one.

Then run this and show its output:

    python "$HOME/.config/cc/cc-switch.py" agents models '<subagent>'

If it reports that the subagent does not exist, show the profile list instead and stop —
never create a profile by accident. The header line shows the profile's current effort.

If no effort level was given, ask with the AskUserQuestion tool: the question names the current
effort; offer four of `low medium high xhigh max default` other than the current one, preferring
its neighbours (the rest stay reachable through "Other"). Then set the chosen level as below.
If a level was given, run this and report its output verbatim:

    python "$HOME/.config/cc/cc-switch.py" agents set '<subagent>' --effort '<level>'

The level is passed to the subagent's `claude -p` as `--effort`; `default` removes the option.
If the command fails, show the error and stop; do not retry and do not edit agents.json yourself.
