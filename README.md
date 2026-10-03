# Claude Code provider switcher

Windows-only launcher and local protocol adapter for Claude Code. Requires
Python 3.10 or newer, PowerShell, and `claude` on PATH. The Python scripts use
only the standard library. The optional OpenAI OAuth route also requires Node.js
and a separately configured `openai-oauth` installation.

The launcher reuses AI-suite's OAuth startup and background process options.
The OAuth/NVIDIA adapter sends completions through
`AIService.chat_completion_response`, preserving tools, images, usage and SSE.
Anthropic-format routes still connect Claude directly to their provider.
The installer bundles a generated AI-suite copy; workspace development prefers
the sibling `ai-suite` checkout (`AI_SUITE_DIR` can override it).
After editing AI-suite, run `python -B ai-suite/sync.py` from the workspace root
before reinstalling cc.

Helper processes use Windows `CREATE_NO_WINDOW` without `DETACHED_PROCESS`.
The interactive Claude session stays in your terminal. OAuth browser sign-in
can still appear when authentication is needed. The OAuth proxy is reused
between requests; it does not need a new terminal for each completion.
After an upgrade the launcher stops an older running Messages adapter and starts
the new one; Claude sessions still on the old adapter lose their connection. Another
service on the adapter port is never stopped: the launcher fails instead.

## Install

From this repository's checkout, run:

```powershell
python -B install-resume.py
```

This copies the runtime and checks to `~/.config/cc`, backs up existing files,
and registers the launcher in the default Windows PowerShell and PowerShell 7
profiles under `~/Documents`. Run the installer from the checkout, not from
the installation directory. Redirected Documents folders or custom host profiles
require adding the function below to the profile shown by `$PROFILE` manually:

```powershell
function cc {
    & "$env:USERPROFILE\.config\cc\cc-switch.ps1" @args
}
```

If your profile contains `Set-Alias -Name cc -Value claude`, it shadows this
provider-switching function. The installer now appends a guard that removes this
legacy alias when the profile loads, preserving the existing profile and a backup.
For the current session, use `Remove-Item Alias:cc -ErrorAction SilentlyContinue`,
then `. $PROFILE`.
Existing `cc` functions are preserved by the installer; check that they point
to the launcher above.

Credentials, generated `apply.ps1`, saved selections, and backups are local
only and excluded from Git. Configure provider credentials separately.

## Usage

Open a new PowerShell terminal, then run:

```powershell
cc                                      # Choose a provider and live model
cc commandcode claude-sonnet-4-6          # Claude Code using Command Code credits
cc commandcode-cli deepseek/deepseek-v4-flash  # Claude Code on the Command Code subscription, via cmdc
cc openrouter openai/gpt-5.3-codex        # Experimental Codex model through OpenRouter
cc official                             # Return to the normal Claude login
cc openai-oauth gpt-6-astra               # Open Claude Code with the selected OpenAI model
cc nvidia <model-id>                     # Open Claude Code with the selected NVIDIA model
cc gpt4free default                     # Free, best-effort models via local gpt4free
cc -NoRun commandcode claude-sonnet-4-6   # Configure this terminal without launching
cc --resume <session-id>
cc --resume                             # Claude session picker
cc --continue                           # Latest conversation in this directory
cc openai-oauth gpt-6-astra --resume <session-id>
cc status
```

The installer registers `cc` in both Windows PowerShell and PowerShell 7.
Open a new terminal after installation, or run `. $PROFILE` in an existing one.
A shell started with `-NoProfile` does not load aliases; use
`& "$env:USERPROFILE\.config\cc\cc-switch.ps1" --resume <session-id>` there.

Resume and continue use Claude Code's native conversation storage. Without an
explicit provider/model, launch flags reuse the last selection saved by `cc`
(across terminals); they do not recover the provider from the conversation.
Specify the provider and model to override it. With no saved selection, the
provider menu opens. `-r` and `-c` also work. Native flags follow the provider and
optional model and are forwarded literally, including session names with spaces.
`-NoRun` configures only, so it cannot be combined with launch flags.

The switch is local to this terminal. After `-NoRun`, launch `claude` in the same
terminal. A new terminal does not inherit the selection. The ordinary `claude`
command retains its normal behavior until a provider is selected in that shell.

Command Code reads `CMD_API_KEY` or the existing login in
`~/.commandcode/auth.json`. Its Messages endpoint serves Claude models only; other
models go through the local adapter's Chat Completions route. The menu hides models
that serve neither endpoint (for example Responses-only), and a typed model outside
that list gets a warning.

`commandcode-cli` serves the Command Code subscription (the Go plan works only
through its own CLI). The adapter route `http://127.0.0.1:10532/commandcode-cli`
runs `cmdc -p --output-format json --max-turns 1` once per Messages request, in a
neutral directory, with the whole conversation on stdin. Claude's tools travel as a
text protocol: the prompt lists their schemas and the model replies with
`<tool_call>{"name": ..., "input": {...}}</tool_call>` blocks, which the adapter
returns as ordinary `tool_use` blocks. The adapter reads `cmdc`'s NDJSON event stream
and forwards thinking and text as they arrive, so Claude Code shows live thinking, elapsed
time and token counts; text from the first `<tool_call>` on is held back and becomes tool
calls. When a model calls Command Code's own tools instead (`shell_command`, `read_file`,
`write_file`, `edit_file`, `grep`, `glob`, `web_fetch`, `web_search`), `cmdc` stops at its
one-turn limit before running them (exit 8); the adapter hands those calls to Claude Code
as `Bash`/`PowerShell`, `Read`, `Write`, `Edit`, `Grep`, `Glob`, `WebFetch` and
`WebSearch`, and names any other tool in the reply instead of failing. Every custom route
sets `API_TIMEOUT_MS=1800000`, the adapter's own 30-minute limit for one CLI run. Images are dropped. Each request
also carries cmdc's own system prompt (about 20k input tokens) against the plan's
usage limits. The CLI's error text, such as a weekly limit notice, reaches Claude.
Any Anthropic-format client, not only Claude Code, can use the route: set its base
URL to the route and any nonempty API key.

`gpt4free` starts the installed `g4f` package's OpenAI-compatible server on
`127.0.0.1:1337` when it is not already running (`pip install -U "g4f[api]"`) and
routes it through the same adapter. `G4F_API_KEY` is sent only when set. Free
upstreams are best-effort: models can time out or return nothing, and tool calling
depends on whichever upstream g4f picks.
OpenRouter reads `OPENROUTER_API_KEY` or its own entry in OpenCode's credential
file. Keys from another provider are never used as a fallback. The model picker
fetches current models, with a small fallback list when offline.

OpenRouter's Codex models use OpenRouter credits, not a ChatGPT subscription.
Non-Claude models are experimental in Claude Code; tool use and advanced features
are not guaranteed. For models with known context metadata, `cc` sets
`CLAUDE_CODE_MAX_CONTEXT_TOKENS` so unfamiliar model IDs can use their declared
window with auto-compaction enabled. OAuth limits come from the proxy when
provided, otherwise the matching model in `CODEX_HOME/models_cache.json`
(default `~/.codex`), preferring `max_context_window` over `context_window`.
These account-route limits can be lower than public API specifications. No model
family limits are guessed. The menu and `cc status` display the selected limit;
switching to an unknown limit or official Claude clears the previous override.
Claude's recognized native model IDs retain Claude's own context handling.
Every provider launches Claude Code. `cc` never launches Codex. `-NoRun` sets the
environment without launching Claude; the next plain `claude` command uses it.

For `openai-oauth`, the existing OAuth proxy runs on `127.0.0.1:10531`. The
stdlib-only `cc-anthropic-proxy.py` adapter starts automatically on
`127.0.0.1:10532`, translating Claude's Messages requests to OpenAI Chat
Completions. NVIDIA uses the same adapter and its own credential. The adapter
keeps no credentials on disk and logs neither prompts nor keys. The selected
model also applies to Claude's background models and subagents.

The adapter streams text, translates function tools and tool results, supports
user images, and reports usage. Tool argument fragments are buffered until
complete. Deferred MCP tool search is disabled for these two providers so full
tool schemas are sent; switching away clears that setting. Upstream reasoning
(`reasoning_content` or `reasoning` deltas) streams as Claude's thinking block, so
Claude Code shows the thinking and counts it in the live token total; the final
usage chunk gives Claude the real input and output token counts. Thinking blocks
carry an empty signature and are not sent back upstream. Cache controls do not
carry over. Server-side Anthropic
tools and document blocks are unsupported. Token counts fall back to Claude's
estimate. These protocol limits do not prevent ordinary coding tool use.
For every custom route, `cc` sets `CLAUDE_CODE_AUTO_MODE_SERVER=0` to use
Claude's existing client-side auto-mode classifier. These providers cannot return
Anthropic's server-side `safeguard_results`; the setting avoids the unsupported
gateway billing notice without disabling the classifier or making its requests
free. Switching to official Claude clears it.
See https://code.claude.com/docs/en/auto-mode-classifier-billing.

The external OAuth bridge must preserve optional tool arguments when converting
Chat Completions to Responses. Incompatible bridge versions may require optional
arguments such as Agent `isolation`. Bridge installation and patches are outside
this repository; no installed npm bundle or local patch is included here.

MiniMax, OpenCode and other Anthropic-format providers keep their direct routes.

Inside Claude Code, `/model` lists the chosen model (Opus and Haiku slots, so
background work stays on it) plus up to three more models from the same provider:
your recent `cc` picks for that provider first, then its fallback list. Command Code
lists only Claude ids after a Claude pick, and only other ids otherwise, because the
two use different routes. `/model <id>` still accepts any id. The context limit stays
the one of the model chosen at launch.

Every custom route sends its key as `ANTHROPIC_AUTH_TOKEN` (OpenCode also gets it as
an `x-api-key` custom header), never `ANTHROPIC_API_KEY`, so Claude Code does not
warn "Both claude.ai and ANTHROPIC_API_KEY set". `ENABLE_CLAUDEAI_MCP_SERVERS=false`
hides the "claude.ai connectors are disabled" notice; connectors cannot load over a
gateway anyway. `cc official` clears both.

After the model menu, `cc` asks the session's starting effort (Claude Code's own
`low`/`medium`/`high`/`xhigh`/`max`, or `default`) and launches `claude --effort <level>`;
the pick is remembered. A typed `cc <provider> <model>`, a `--effort` launch flag or
`-NoRun` skips the menu. `/effort` changes it mid-session as in stock Claude Code: every
custom route sets `CLAUDE_CODE_ALWAYS_ENABLE_EFFORT=1`, because Claude Code hides effort
for model ids it does not know. Claude sends it as `output_config.effort`; Anthropic-format
gateways receive it unchanged, and the adapter forwards it as `reasoning_effort` (`max`
becomes `xhigh`, OpenAI's top level) or as `cmdc --effort` on the `commandcode-cli` route.
A model without reasoning levels may reject it; pick `default` there.

## Verification

```powershell
python -B test_cc_switch.py
python -B test_cc_resume.py
python -B test_cc_proxy.py
python -B critic_mock_check.py
```

Run these commands from the checkout. The first three checks run offline with
fake credentials and fake Claude and Codex commands. The optional fourth check
requires Claude Code at `~/.local/bin/claude.exe` and uses local mock providers.
It tests credential isolation, model filtering, environment switching, shell
quoting, cleanup, invalid input, returning to official Claude, OAuth/NVIDIA Claude
launch arguments and `-NoRun`. Adapter checks cover streamed text, tools, usage,
truncated responses, authentication and HTTP errors. The independent critic check
runs the actual Claude executable through the adapter against local mock OpenAI
providers, including a real Read tool and its follow-up result, for both routes.
It also asserts that Claude reports the declared 872,000-token context window.
No paid generation is performed. These checks do not verify live OAuth generation,
account model access, model quality, or interactive terminal appearance.

Sources:
- https://commandcode.ai/docs/provider
- https://openrouter.ai/docs/cookbook/coding-agents/claude-code-integration
- https://code.claude.com/docs/en/llm-gateway
- https://code.claude.com/docs/en/llm-gateway-protocol
- https://platform.claude.com/docs/en/build-with-claude/streaming
