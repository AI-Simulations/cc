---
description: Switch this session's provider and model without restarting (adapter routes)
argument-hint: [<provider> <model> | off]
allowed-tools: Bash, PowerShell
---

The arguments are: $ARGUMENTS

They must be empty, `off`, or an adapter provider name and a model id (letters, digits and `. _ / : - @`).
Adapter providers are listed by `cc route routes`. If the arguments contain anything else, stop and ask me.

Run this, with the arguments single-quoted, and report its output verbatim:

    python "$HOME/.config/cc/cc-switch.py" route <arguments>

With no arguments it shows the current route. The switch applies to this session only. If the command fails,
show the error and stop; do not retry and do not edit routes.json yourself.
