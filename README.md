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
profiles under `~/Documents`. It also installs the status line and global Claude
delegation hooks, preserving unrelated settings, hooks, and permission rules.
Run the installer from the checkout, not from
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

Official launches (`cc official`, plain `claude`) pass through the local adapter, which hands
them to Anthropic untouched until `/model` or a fallback reroutes the launch. They also set
`_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL=1`, so Claude Code keeps its first-party behaviour
behind the adapter, such as the cheap Haiku-written background-agent summaries. On a reroute
to a third-party Anthropic-format provider the adapter drops what only Anthropic accepts (the
`diagnostics` field and first-party betas). `CC_OFFICIAL_DIRECT=1` connects directly instead.
In a direct session, `/model` switches Claude models in place; another provider copies a
`cc … --resume <session-id>` command that continues this conversation there after you exit.

The switch is local to this terminal. After `-NoRun`, launch `claude` in the same
terminal. A new terminal does not inherit the selection. The ordinary `claude`
command retains its normal behavior until a provider is selected in that shell.

Command Code reads `CMD_API_KEY` or the existing login in
`~/.commandcode/auth.json`. Its Messages endpoint serves Claude models only; other
models go through the local adapter's Chat Completions route. The menu hides models
that serve neither endpoint (for example Responses-only), and a typed model outside
that list gets a warning. Both Command Code routes also hide models the account's plan
cannot use: the menu reads the active plan from the billing subscription and each model's
`minimumPlan` (or its category when that is absent) from the client catalog, exactly as
`cmdc` ranks them (Go, GOAT, Pro, Provider, Max, Ultra). Purchased or free credits unlock
every model; when the plan or catalog cannot be read, every advertised model stays listed.
The `/model` picker also gets up to three extra entries from that live, plan-filtered list,
and on adapter routes Claude Code discovers the gateway's own `/v1/models` (served by the
adapter). A remembered pick the plan no longer allows stays out. Claude Code keeps only
Claude-named ids from that discovery, so a non-Claude model is chosen by typing its id in
`/model` or in `/model`'s Subagent tab.

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
usage limits. A turn that answers with only punctuation (a lone `.`) is retried once
before Claude Code sees it: the degenerate reply is transient, so the retry keeps the
request moving instead of ending it. The CLI's error text, such as a weekly limit
notice, also reaches Claude.
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
Command Code publishes each model's `reasoningEfforts`; a model that publishes none (MiMo
V2.6 Pro, for example) is run without an effort so `cmdc` does not reject it. Other providers
do not publish this, so their models keep whatever level you pick.

## Fallback route

Off by default. Sessions on an adapter route (gemini, gpt4free, lmstudio, nvidia, ollama-cloud,
openai-oauth) can name a second route that the adapter retries once when the first answers 429,
402 or 5xx: pick it in the launch menus (after auto-compact) or change it mid-session with
`/fallback <provider> <model>`, `/fallback off`, or `/fallback` to show it. The setting belongs to
that launch only (`~/.config/cc/fallbacks.json`). The fallback model answers under the session's
model name, so the transcript does not show the switch.

A key out of quota can also rotate: put spare keys, comma-separated, in `<KEY>_API_KEYS`
(e.g. `GOOGLE_API_KEYS`); a key that got 429/402 rests until its retry-after.

### In-session model route

`/model` (no arguments) opens the picker as its own dark window (`cc gui`, Tkinter, no
dependencies): Model, Subagent, Fallbacks, Auto, Tools and Presets tabs, each with a search field.
A session with no cc launch (plain `claude`) keeps the in-terminal grid instead: only that grid can
run Claude Code's own `/model` and `/effort`, which its Model tab switches, and the window would
refuse its Apply.
Model lists carry cc's facts in separate Context, Price, AA and Score columns and a Sort menu:
Intelligence (the default), Score, Context, Price, Family, or the provider's own Listed order,
with rows missing a fact last — the launch model menu's rules. The list stripes alternate rows and
highlights the row and tab under the pointer without changing the keyboard selection. Each tab remembers its target, provider, one pick per
provider, sort, search and effort per session, and each provider's model list is kept for the rest
of the window, so reopening or switching back lands where you left it without listing again.
Nothing is applied until Enter: the remembered view never changes a route. The Subagent and Fallbacks tabs carry the
Target picker because each profile has its own route: it chooses the subagent profile (or `session`
for this conversation's own fallback) the tab's provider, model, effort and compact columns edit.
Apply moves the green Current mark in place, without reloading the list or losing the selection.
The window applies through the same cc commands as the
terminal (`cc route`, `cc fallback`, `cc agents set`, `cc helper`), so the session route, profiles
and status line stay one source of truth. On a cc adapter session the Model tab switches provider
and model live (`cc route <provider> <model> [effort]` from the shell); a session on Claude Code's
own model switches Claude models through `/model` and `/effort`, and another provider copies its
`cc` relaunch command. `/fallback` opens the window on the Fallbacks tab. `/model <name>` with
arguments is Claude Code's own. The adapter applies it per launch id, so other sessions and workers
keep their own route. Only adapter routes are listed (`python cc-switch.py route routes`); a
session that connects directly (official with `CC_OFFICIAL_DIRECT=1`, Command Code, MiniMax, OpenCode, OpenRouter)
must be relaunched to change provider; `/model` copies that relaunch with `--resume`.

The Presets tab is one Enter away from each provider's default: the curated auto-helper default,
else the model last picked there, else the first of its list (`python cc-switch.py presets`).
Each GUI preset includes Model, Subagent, Fallbacks, Auto and Tools settings. Double-click a row
or press Edit to change all five provider/model/effort settings in one editor. Each model dropdown
shows an animated spinner while its list loads; Save writes
`~/.config/cc/presets.json` without changing active routes. Use applies the complete preset to this
session and every configured subagent profile. Fallback defaults to off; Auto uses the provider's
curated helper default, and Tools uses its helper default or the main model at low effort when
supported. Restore default removes the override. Existing main-model presets remain compatible;
`cc presets set <provider> <model> [effort]` edits only the main setting, `cc presets reset <provider>`
restores the full default, and `cc presets bundles` lists all five settings as JSON. `cc gui [tab]` opens the window directly, one per session, raising the open one instead of
stacking another.

Brand marks are local SVGs in `~/.config/cc/logos` (Simple Icons, CC0; each mark remains its
owner's trademark): a provider or model family without one shows a colored dot. Drop another
24×24 SVG there under the provider's key to use it. If no window can open (no Tk, a missing file),
the subagent-picker mod falls back to its in-terminal grid: Tab switches tabs, ←/→ columns, ↑/↓
rows, Space picks, Enter sets, Esc closes, `s` toggles the family order and `f` moves the ring to
the find field. The launch model menu's default sort is the aggregate score, and rows missing a
displayed fact (`ctx ?`, `$?`, `AA ?`) are always listed last. If a provider has no key, `cc` asks
for one once and stores it in `~/.config/cc/keys.json` (gitignored, user-only permissions).

When OpenCode rejects the Go/Zen key stored in opencode's `auth.json` (for example
`api_key_expired`), `cc` creates a new personal API key in the OpenCode console and stores
it there for both routes. It uses a signed-in Chrome profile at
`~/.config/cc/browser_profile/opencode` (`CC_OPENCODE_PROFILE`), headless. If that session
has expired and `cc` runs in a terminal, a plain Chrome window opens for sign-in; close it to
continue. `python opencode_key.py` checks and renews by hand (`--force` always renews).
Keys from `OPENCODE_GO_API_KEY` or `keys.json` are never replaced.

OpenCode Go answers Anthropic `/messages` only for some models (MiniMax, Qwen, Claude;
per models.dev). `cc opencode-go` sends the rest (MiMo, GLM, Kimi, DeepSeek, ...) through
the local adapter's `/chat/completions`, like Command Code's non-Claude models.

## Usage-limit resets

The Messages adapter distinguishes exhausted provider usage from temporary request throttling.
When a provider returns a real reset time (or the status line has a fresh, exhausted account
window), it returns Claude Code's native usage-limit headers. Interactive sessions can then use
`/rate-limit-options` and Claude's existing automatic-continue preference; no extra restart loop
or invented reset schedule is installed. Missing reset metadata is reported explicitly.
Payment/authentication errors and transient 429s do not arm a quota-reset timer.

The local OAuth bridge needs `python patch-oauth-quota.py` after reinstalling its patched npm
build; this preserves upstream quota status/reset metadata before streaming starts. The script
keeps the other local OAuth patches. Restart the OAuth bridge and adapter after deployment,
only between requests: restarting a shared listener interrupts active calls. Adapter launchers
serialize replacement so simultaneous sessions do not race to restart the same process.

Codex controls its server-side cache lifetime. No exact one-hour OAuth retention option is
documented; the status line's five-minute countdown is an estimate, not a TTL sent to OpenAI.
Do not change that display to claim a guaranteed hour. Session cache keys remain stable.

## Subagents on other providers

Claude Code subagents share their session's environment, so they cannot reach a different
provider or use a different auto-compact threshold. `cc` instead runs each profile in
`~/.config/cc/agents.json` as its own headless `claude -p`:

```json
{
  "cheap": {"provider": "openai-oauth", "model": "gpt-6-luna", "effort": "low",
            "autocompact": 60, "args": ["--permission-mode", "auto"],
            "description": "Bulk mechanical edits."},
  "deep":  {"provider": "official", "model": "claude-opus-5-5", "effort": "max", "autocompact": 85}
}
```

`provider` defaults to `official`; `model`, `effort` (`low` to `max`), `autocompact`
(percentage for `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`) and `args` (extra Claude arguments) are
optional. The child process drops the parent's route variables and auto-compact override, so
an unset `autocompact` means Claude's default, not the main session's value.

```powershell
cc agents                    # edit profiles in a menu, then write ~/.claude/agents/cc-<name>.md
cc agents set cheap --provider nvidia --model deepseek-ai/deepseek-v4-pro --effort low --autocompact 60
cc agents set cheap --autocompact default   # "default" removes model, effort or autocompact
cc agents remove cheap
cc agent cheap "task text"   # run one task directly; without text the task is read from stdin
cc agent cheap --model other-id --autocompact 40 "task"   # options before the task apply to this run only
```

Every menu launch (provider and model not typed) asks the main session's provider, model,
effort and auto-compact, then the subagent's provider, model, effort and auto-compact, and
rewrites the relays. Edits made at the startup prompt or with the `/subagent-*` commands are
stored per launch in `~/.config/cc/session-agents.json`, so another session keeps its own
profiles; `cc agents` from a plain shell edits the shared defaults in `agents.json`. The first row of the subagent provider menu, `skip`, keeps the profiles
unchanged. With no profile the answers create `sub`; with several, a menu first asks which one.
`cc agents` opens a list to add, edit or remove profiles. Esc returns to the previous
selection prompt, keeping the answers already chosen; at the first prompt it cancels.
Changing the provider clears the previous provider's model. This also applies to the
subagent prompts and the profile editor. Ctrl+C at any prompt, model loading
included, quits with "No change."; saves replace whole files, so nothing is left half-written.
Changing a profile's provider without a model drops the old model id. `--description` sets the
text the main session reads when choosing the subagent.

`/model`'s Subagent tab sets a profile's provider, model, effort and auto-compact threshold
(Compact column, `cc agents set`), so the result matches the terminal. The Main model tab's
Compact column has the subagent-picker mod compact this session once its context reaches that
share of the window (`default` leaves Claude Code's own threshold).

The main session's auto-compact threshold is set the same way: the startup prompt asks for it
after the effort and remembers the pick, or `cc <provider> <model> --autocompact 70` sets it for
that launch (`default` clears it). `cc` owns `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` and clears it on
launches that set none, so a value set elsewhere in your profile does not carry over.

Each generated `cc-<name>` subagent is a Haiku relay with Bash only. It sends the task on
stdin to `cc agent <name>` and returns the output verbatim. On a custom main route, the Haiku
slot is the main model. The relay still uses that session's provider; the actual task runs
on the configured worker's provider/model/effort. Removing a profile also removes its relay.
One relay run is limited by the Bash tool's 10-minute timeout. If the worker outlives that
call, the installed `SubagentStop` hook blocks the relay once with the `agent-wait` command,
so a relay model that would otherwise stop at `CC_RELAY_PENDING` keeps waiting. `agent-wait`
is bounded by the same 10-minute tool timeout and can be repeated.

The global hooks tell new and existing sessions to use the configured `cc-*` types and block
native fork, general-purpose, and custom delegation instead of silently falling back to
the main model. They preserve normal permission evaluation. User settings and hooks reload
in existing sessions; startup environment changes require a new launch. An already-running
native request keeps its original model until it finishes. Disabled hooks or clients that
do not load user settings cannot enforce this policy.

The hook adds a context envelope to each task. The relay must pass it unchanged. The worker
resumes an isolated copy of the parent conversation, reloads current project instructions
and project auto-memory, and uses the configured route. Original transcripts are never
rewritten. To continue an old native worker, use a configured `cc-*` type with its old
`resume` ID: cc restores its parent context up to the original fork cutoff plus its own
subsequent work. Repeated configured-worker resumes use the latest worker session and
working directory. Missing or invalid context fails explicitly without starting fresh.
Interrupted tool calls are context, not replayed commands; workers must check effects before
retrying them. A direct `cc agent <name> "task"` without an envelope still starts fresh.

Old Explore/Plan workers retain a read-only tool set, no MCP tools, and plan permissions.
Unknown custom-agent migrations are blocked because their role, tool policy, hooks, and
private agent memory cannot safely be inferred from a transcript. Supply their definition
and relevant memory in a self-contained task, preserving their restrictions. Legacy cc
relays created before context records existed can recover only their available relay
transcript, not a previously unrecorded external worker conversation. Context-window and
provider tool support still apply; these checks do not guarantee model quality.

The main status line always lists configured worker provider/model/effort, including
official main sessions and profiles without quota APIs. It explicitly marks `same provider`
when applicable, for example `↳ sub: gpt-6.1-sol xhigh · openai-oauth`. Quota bars are omitted
when they duplicate the main account. Different account quota bars remain visible; model,
provider, and plan labels are shortened. Effort draws one circle per step up that model's own
reasoning ladder — the provider publishes it (Command Code's `reasoningEfforts`, Codex's
`supported_reasoning_levels`) and `~/.config/cc/efforts.json` caches it for the status line — and
colours it on the same cool-to-hot ramp, so a model that tops out at `high` shows three filled
circles in the hottest colour while `high` on a full ladder stays at three fifths. A model with
no adjustable effort renders `default`. The worker row reuses the main model's mapping at lower
brightness, for example `↳ sub: 6.1 sol ●●●● · OAuth`. A failed quota fetch names its cause
(`key expired`, `auth failed`) instead of dropping the bars; adapter routes show the rate-limit
windows the adapter last saw. A prompt-cache countdown follows Anthropic's own expiry. OpenAI,
Command Code, OpenCode and OpenRouter publish no expiry, so their countdown assumes the provider's
usual five-minute warm window from the last transcript write and disappears once it is cold,
instead of claiming the cache is off. Agent rows show only the model doing the work (the recorded or
configured worker route, never the native relay model), the worker's token use and the task;
running rows are drawn bright. Existing native rows
keep their actual native model; changing a profile does not relabel those agents. Worker
session IDs and route snapshots are local files under `~/.config/cc/contexts`, excluded from
Git; they contain no prompts or credentials.

## Verification

```powershell
python -B test_cc_switch.py
python -B test_cc_resume.py
python -B test_cc_proxy.py
python -B test_cc_relay.py
python -B test_statusline.py
python -B test_cc_session.py
python -B test_cc_gui.py
python -B critic_mock_check.py
```

Run these commands from the checkout. The first six checks run offline with fake credentials and
fake Claude and Codex commands; the seventh, `test_cc_gui.py`, needs Tk and uses hidden widgets
with stubbed cc commands, so it changes no real settings. The optional last check
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
