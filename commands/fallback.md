---
description: Show or set this session's fallback route, used when its provider refuses a request (429, 402, 5xx)
argument-hint: [<provider> <model> | off]
allowed-tools: Bash, PowerShell
---

The arguments are: $ARGUMENTS

They must be empty, `off`, or a provider name and a model id (letters, digits and `. _ / : - @`).
If they contain anything else, stop and ask me.

Run this, with the arguments single-quoted, and report its output verbatim:

    python "$HOME/.config/cc/cc-switch.py" fallback <arguments>

With no arguments it shows the current fallback. If the command fails, show the error and stop;
do not retry and do not edit fallbacks.json yourself.
