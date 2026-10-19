---
description: Set a cc subagent's model, or list every model its provider offers
argument-hint: <subagent> [model|default]
allowed-tools: Bash, PowerShell
---

The arguments are: $ARGUMENTS

Split them into a subagent name and, optionally, a model id. Names use lowercase letters,
digits and hyphens; model ids use letters, digits and `. _ / : - @`. Quote both with single
quotes on the command line. If either contains anything else, stop and ask me.

If no subagent was named, run this to list the profiles:

    python "$HOME/.config/cc/cc-switch.py" agents models

If exactly one profile exists, use it. Otherwise ask with the AskUserQuestion tool, one option per
profile (up to 4; the rest are reachable through "Other"), then continue with the chosen one.

Then run this and show its output:

    python "$HOME/.config/cc/cc-switch.py" agents models '<subagent>'

If it reports that the subagent does not exist, show the profile list instead and stop —
never create a profile by accident.

If no model id was given, ask with the AskUserQuestion tool: the question names the current
model; offer four models from that list (newest generation first, never the current one; any other
id stays reachable through "Other"). Then set the chosen model as below.
If a model id was given, run this and report its output verbatim:

    python "$HOME/.config/cc/cc-switch.py" agents set '<subagent>' --model '<model>'

`default` clears the model, so the subagent uses its provider's Claude default. A profile's
provider is not changed by this command. If the command fails, show the error and stop; do not
retry and do not edit agents.json yourself.
