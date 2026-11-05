# vadgr-computer-use

Local MCP server for computer use. 33 tools across three tiers: **Tier 0** system tools (files, shell, HTTP, clipboard, time, listing and launching apps, and more), **Tier 1** structured control (drive your real Chrome through an MV3 extension with direct DOM ops plus window / tab / profile management, and, on Linux, read and drive native apps through the accessibility tree with AT-SPI, any window by name and not just the focused one), and **Tier 2** desktop control (screenshot plus mouse/keyboard, driven from the pixels). The agent picks the highest-precision tier that fits the task: act on a web page through the DOM or a native control through its accessibility node, run a system op directly, or fall back to screenshot-and-pixels for anything on the desktop.

Tested with **Claude Code**, **Codex CLI**, and **Gemini CLI** (same server, same tools, same prompt).

> **Platforms:** works on **Linux (X11 and Wayland incl. GNOME 46-50, KDE, wlroots)**, **Windows native**, **WSL2**, and **macOS**. On **Linux** run `vadgr-cua install-deps` once after install (clipboard backend + input permissions); on **macOS** grant Accessibility + Screen Recording on first run. See [First run on Linux](#first-run-on-linux), [First run on macOS](#first-run-on-macos), and [Platform support](#platform-support).

---

## Install

```bash
pip install vadgr-computer-use
```

The standalone package selects helpers for the actual native operating system
and architecture. Windows and WSL support x86_64 and ARM64 helpers; WSL also
checks the Windows host architecture before deployment. Native Linux and macOS
do not deploy Windows helpers. Installation needs no owner compiler or helper
download.

Managed installations use eight explicit profile artifacts from the GitHub
release. Linux and WSL profile selection comes from the verified artifact
catalog, not competing PyPI wheels. Managed profiles require an authenticated
parent launch and cannot fall back to standalone input validation.

Standalone clients can adopt an exactly authorized installed signed helper
offline. After adoption they retain verified signed bytes or report a repair
requirement; they cannot silently return to unsigned helpers.
Signed upgrades and rollbacks require an authenticated catalog naming the exact
previous and replacement helper bytes. A different version label or installation
identifier alone does not authorize a replacement. Signed state persists across
changes to the packaged input, and an unlisted predecessor remains untouched.
Invalid candidate or adoption records return `browser_broker_upgrade_unsafe`
with a repair instruction before the running broker is touched.

That ships a console script called `vadgr-cua`. On **Linux**, run the one-time
system-dependency step (the second of the two install commands):

```bash
vadgr-cua install-deps        # prints the plan; add --yes to run it
```

It provisions the clipboard backend (`wl-clipboard`), the accessibility stack
(`at-spi2-core` plus the ATK bridge, for the Tier 1 structured tools) and
`/dev/uinput` access via a
single graphical auth prompt (`pkexec`, falling back to `sudo`). pip can't install
those (they are OS packages), so this command bridges the gap. See
[First run on Linux](#first-run-on-linux).

Verify:

```bash
vadgr-cua doctor
# Linux also reports the resolved capture/input backends under "platform_backends"
```

On WSL2, the bridge daemon auto-launches the first time a tool is called. On other platforms it's a no-op; direct backends handle everything.

### Browser workspaces and paced typing

The browser tier uses one local per-user broker. Multiple Claude, Codex, vadgr,
or direct MCP clients can use the same extension at once. Each client normally
gets an owned browser window with any number of tabs. Registry listings still
show other clients' targets, but an action against one returns
`target_owned_by_another_client`. Use `windows(op="claim"|"release")` for a
whole window. Use `tabs(op="claim"|"release")` only for one tab in a shared
user window.

When native Windows and WSL share Windows Chrome, the broker is a verified
self-contained Windows process bound only to Windows loopback. WSL reaches it
through the packaged Windows stdio proxy, so NAT and mirrored WSL networking
use the same path. This requires neither Windows Python nor a firewall, DNS,
route, adapter, proxy, VPN, or WSL networking change.
The elected broker repairs its missing or damaged owner-only discovery record
without changing its epoch or disconnecting existing clients. New clients
report ready only after an authenticated endpoint handshake. Broker discovery,
bundle, endpoint and Windows interop failures keep separate error codes and
matching remedies.
An update from the released 0.7.6 or 0.7.7 Windows broker now hands ownership
to the new verified broker automatically. The handoff verifies the process,
owner, creation identity, installed path and complete frozen payload before it
stops anything. An unknown process remains untouched and returns a specific
safe-upgrade error. The update does not reload the extension or restart Chrome.
The native host closes both relay directions together and joins its input
reader before exit, including when the browser broker disconnects first.

Fast input remains the default. Use human-paced input only when a field needs
real intermediate key events:

```text
browser(op="type", selector="#search", text="...", human=true)
type_text(text="...", human=true)
```

Both tools use the versioned `us_adult_transcription_2026` timing profile by
default. It draws from the released within-word, after-space, and after-sentence
empirical gap tables. A fitted stationary four-bin rank chain adds nearby
cadence while preserving those marginal distributions. The runtime has no
latent motor, pause, or learned model. Ordinary spaces add no artificial pause and their
complete total gaps remain within 20 through 1,500 milliseconds. The generator
does not rescale a complete message to force an exact duration. An advanced
caller can instead provide both `wpm` from 10 through 200 and `iki_cv` from 0
through 1. These options tune the same sequence model.
Human-paced input has no implicit total deadline, so long text continues while
complete units make progress. A caller may provide a positive `timeout` in
milliseconds as an explicit total budget. Browser pacing transports long plans
in bounded progress-confirmed chunks and never replays a chunk whose dispatch
result is uncertain.
`browser(op="fill")` stays a bulk value operation. Paced input models event
cadence for input-driven interfaces. It does not claim stealth or biometric
human identity. Browser pacing sends page-visible synthetic DOM events to the
exact leased tab; it does not claim physical input or `Event.isTrusted`, and it
does not activate an inactive tab or foreground its window. Paced browser input
supports native text inputs and textareas; rich contenteditable editors remain
on the bulk path until their structure-preserving paced path is proven. A
trusted `press` against an inactive target fails by name instead of silently
activating the target or claiming an input Chromium discarded.
Trusted browser clicks temporarily emulate focus inside the exact target while
dispatching their pointer sequence. They do not activate its tab or foreground
its window.

The profile's residual timing data derives from the CC BY 4.0 KeyRecs dataset
by Tiago Dias, João Vitorino, Eva Maia, Orlando Sousa, and Isabel Praça
([dataset](https://doi.org/10.5281/zenodo.7886743),
[data article](https://doi.org/10.1016/j.dib.2023.109509)). The checked-in
artifact records the exact source hashes, filtering, weighting, and derivation
script. Participant-grouped inner folds select empirical shrinkage and four-bin
rank dependence from training participants only. A fixed KeyRecs pilot sets
simulation precision, and five participant-disjoint outer folds select the
smallest eligible model. Independent participant-clustered confirmation accepts
that model only when normalized gap CRPS is superior, sequence energy has a
favorable point estimate and a 95 percent upper bound below the operational
`0.10` standardized-energy loss margin, and the predeclared secondary
non-inferiority, rate, boundary, and bounded-support gates all clear.

---

## Wire it into your agent

Pick your client. The server command is `vadgr-cua --transport stdio` in every case. Each agent launches that stdio process itself, so it needs the full path to the binary unless `vadgr-cua` is already on the agent's `PATH`.

First, find the path:

```bash
which vadgr-cua
# global install: /home/you/.local/bin/vadgr-cua
# venv install:  /path/to/.venv/bin/vadgr-cua
```

Substitute that path in each config below.

### Claude Code

Project-level (`.mcp.json` at the repo root you want to automate from):

```json
{
  "mcpServers": {
    "vadgr-computer-use": {
      "type": "stdio",
      "command": "/path/to/vadgr-cua",
      "args": ["--transport", "stdio"]
    }
  }
}
```

User-level (add to `~/.claude.json` under `mcpServers` with the same shape).

Verify: `claude mcp list` should print `vadgr-computer-use: ... ✓ Connected`.

### Codex CLI

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.vadgr-computer-use]
command = "/path/to/vadgr-cua"
args = ["--transport", "stdio"]
```

Verify: `codex mcp list` should list `vadgr-computer-use` with status `enabled`.

### Gemini CLI

```bash
gemini mcp add --scope user --trust \
  vadgr-computer-use /path/to/vadgr-cua \
  -- --transport stdio
```

That writes `~/.gemini/settings.json`. Verify by running an interactive session: Gemini shows MCP tool calls inline.

---

## Try it

Once the wire-up is done, any of these commands launch the client, which starts `vadgr-cua --transport stdio` in the background via MCP, and drives your desktop. Same prompt, same tools: pick the client you already use.

**Sanity check (focus + Ctrl+A):**

```
Take a screenshot, tell me in one sentence what application is in focus,
then press Ctrl+A and take another screenshot to confirm the action.
```

### Claude Code

Interactive (most common):

```bash
claude --dangerously-skip-permissions
# then paste the prompt at the > cursor
```

Headless one-shot:

```bash
claude --dangerously-skip-permissions -p \
  "Take a screenshot, tell me what app is in focus, then press Ctrl+A and screenshot again."
```

### Codex CLI

Headless one-shot (the usual way to drive Codex):

```bash
codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check \
  "Take a screenshot, tell me what app is in focus, then press Ctrl+A and screenshot again."
```

Expected output (abbreviated):

```
mcp: vadgr-computer-use/screenshot (completed)
mcp: vadgr-computer-use/key_press (completed)
mcp: vadgr-computer-use/screenshot (completed)
The focused app is <...>; Ctrl+A selected its content.
```

### Gemini CLI

Works end-to-end, but pixel grounding on full-screen shots is weaker than Claude/Codex: first-attempt clicks on small targets can miss by 20-60 px (the model usually recovers via `screenshot_region` crops). **Pass the model explicitly**, since the default may silently fall back to an older Gemini on some accounts:

```bash
gemini -m gemini-3.1-pro-preview -p \
  "Use only vadgr-computer-use tools. Take a screenshot, tell me what app is in focus, then press Ctrl+A and screenshot again." \
  -y --allowed-mcp-server-names vadgr-computer-use
```

---

## Fuller example: play a song on YouTube Music (Codex)

A Chrome window is already open with a "YouTube Music" tab. One call:

```bash
codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check \
  "Use only vadgr-computer-use MCP tools. In the already-open Chrome,
   switch to the YouTube Music tab, search 'Space Oddity David Bowie',
   and play the first result."
```

Real transcript (trimmed):

```
mcp: vadgr-computer-use/screenshot (completed)
mcp: vadgr-computer-use/click (completed)        # YouTube Music tab
mcp: vadgr-computer-use/click (completed)        # search box
mcp: vadgr-computer-use/type_text (completed)
mcp: vadgr-computer-use/key_press (completed)    # enter
mcp: vadgr-computer-use/click (completed)        # first result
mcp: vadgr-computer-use/click (completed)        # dismiss ad overlay
mcp: vadgr-computer-use/screenshot (completed)   # verify now-playing bar
Yes, "Space Oddity" by David Bowie is now playing.
```

---

## How it works

The LLM owns the "where to click" decision; the server owns "how to click it precisely". No other abstraction in between.

## Platform support

The Linux backend is selected per session by a capability resolver (run
`vadgr-cua doctor` to see what it picks and why):

| Platform | Screenshots | Mouse / keyboard | Install notes |
|----------|-------------|------------------|----------------|
| Linux / X11 | `mss` | XTEST (`python-xlib`) | nothing extra; pure-Python, no `xdotool` |
| Linux / Wayland (GNOME 46-48) | `gnome-screenshot` | Mutter RemoteDesktop via `jeepney` | nothing extra |
| Linux / Wayland (GNOME 49-50) | XDG Screenshot portal | Mutter RemoteDesktop via `jeepney` | one consent prompt on first capture (persisted) |
| Linux / Wayland (KDE, wlroots) | `grim` (wlroots) / portal | pure-Python uinput | `vadgr-cua install-deps` for `/dev/uinput` access |
| Windows native | Win32 GDI | SendInput | nothing extra |
| WSL2 to Windows host | TCP bridge daemon (`mss` on Windows) | TCP bridge daemon (Win32 `SendInput`) | bridge daemon auto-launches |
| macOS | `mss` | Quartz `CGEvent` (via `pyobjc`) | nothing extra; deps pulled by pip. Grant Accessibility + Screen Recording on first run |

`pip install vadgr-computer-use` pulls `jeepney`, `python-xlib` and `dbus-fast` automatically on Linux (pure-Python, no compilation). The pixel-input fallback uses a pure-Python `/dev/uinput` writer, so **no C compiler is needed**; the optional `evdev`-backed path is available via `pip install vadgr-computer-use[linux-uinput]`. The clipboard backend (`wl-clipboard`), the accessibility stack (`at-spi2-core` plus the ATK bridge) and `/dev/uinput` access are OS-level and installed by `vadgr-cua install-deps`. The Tier 1 structured tools and Wayland foreground-window detection speak AT-SPI over `dbus-fast` (a plain wheel, no PyGObject), so they work on a stock desktop with no extra install.

On macOS, `pip install vadgr-computer-use` pulls `pyobjc-framework-Quartz` and `pyobjc-framework-ApplicationServices` (wheel install, no compilation). No Homebrew packages required.

## First run on Linux

After `pip install`, run the one-time system-dependency step:

```bash
vadgr-cua install-deps --yes   # one pkexec/sudo prompt; omit --yes to preview the plan
```

It installs the clipboard backend (`wl-clipboard`), the accessibility stack
(`at-spi2-core` plus the ATK bridge, when the bus is not already reachable) and sets
up `/dev/uinput` access (udev rule + `input` group) under a single graphical auth
prompt. pip cannot install these because they are OS packages, not Python wheels.

On **GNOME 49/50 Wayland**, the first `screenshot()` shows a one-time GNOME consent
dialog (the XDG Screenshot portal); click **Share** and the grant is remembered, so
later screenshots are silent. For unattended/remote runs, trigger one screenshot
while you are at the machine first so the prompt is out of the way. On GNOME 46-48,
`gnome-screenshot` is used and there is no prompt. Input (mouse/keyboard) on GNOME
uses Mutter RemoteDesktop and needs no prompt.

Check what the resolver selected:

```bash
vadgr-cua doctor
# "platform_backends": { "capture": {"selected": "portal"}, "input": {"selected": "mutter-remotedesktop"}, ... }
```

## First run on macOS

You can pre-grant permissions before connecting an agent:

```bash
vadgr-cua setup
```

That fires the Accessibility and Screen Recording prompts and prints the current grant state as JSON. Toggle the entries on in System Settings when prompted. If you skip this, the same prompts fire on the first MCP tool call from your agent.

The first time the MCP server captures the screen or injects an input event, macOS opens System Settings to two panes and asks you to grant the running Python interpreter:

- **Privacy & Security -> Screen Recording** (required for `screenshot()` / `screenshot_region()`).
- **Privacy & Security -> Accessibility** (required for clicks, typing, scroll, drag).

Toggle both for the python binary that runs `vadgr-cua` (e.g. `/path/to/.venv/bin/python` or `/opt/homebrew/bin/python3.12`). The grant is per-interpreter and persists; you will not be asked again. Verify status:

```bash
vadgr-cua doctor
# {... "macos_accessibility_granted": true, "macos_screen_recording_granted": true,
#      "python_executable": "/opt/homebrew/bin/python3.12" }
```

Apple enforces these prompts at the OS level for every screen-capture / input-injection API; they cannot be skipped.

If you later revoke either permission in System Settings, the next MCP tool call detects it via `CGPreflightScreenCaptureAccess()` / `AXIsProcessTrusted()`, opens System Settings to the right pane, and returns a structured error to the agent. Toggle the entry back on and the next call works. No silent black screenshots, no hunting through System Settings.

If the WSL2 daemon can't start (e.g. no Windows Python available), the server falls back to a slower PowerShell path. See [Daemon management](#daemon-management-wsl2) below.

## MCP tools (26)

Three tiers; `vadgr-cua doctor` reports the live `tool_count`.

### Tier 0: system (10)
- `fs(op, ...)`: read / write / list / stat / mkdir / remove on the filesystem. A leading `~` is expanded, so `~/notes.txt` lands in the home directory rather than in a directory named `~`, and the result reports the resolved path.
- `shell(op, ...)`: run a command, capture stdout / stderr / exit code. `command` is an argv list, or a string split into argv the way the running platform writes a command line, so `"uname -a"` runs and a Windows path keeps its separators. No shell is involved, so shell syntax (`&&`, `|`, `;`, redirection) is refused by name and needs `shell_mode=True`.
- `http(op, ...)`: make an HTTP request.
- `clipboard(op, ...)`: read / write the OS clipboard.
- `env(op, ...)` / `time(op, ...)` / `tempfile(op, ...)` / `data(op, ...)`: environment variables, time, temp files, and structured-data helpers.
- `apps()`: list installed launchable apps (id, name, icon) from the XDG desktop entries.
- `app_open(target, timeout_ms)`: launch an installed app by id or name and confirm a window appeared on the a11y bus (Linux; via `gtk-launch` / `gio`); `ok` carries the window, a dispatch that maps nothing fails `no_window`.

### Tier 1: browser (5)
- `browser(op, ...)`: drive your real Chrome through the MV3 extension with direct DOM ops (`navigate`, `click`, `fill`, `query`, `read_text`, `wait_for`, `hover`, `dialog`, `upload`, `element_state`, `snapshot`, `use_target`, `back`/`forward`, and more). The DOM is the ground truth, so a mutating op is confirmed by a structured read-back rather than a screenshot. Every result also carries a `target: {window_id, tab_id, url}` so you always see which tab you acted on. Requires the companion extension - install it from the Chrome Web Store, or load the release asset `vadgr-cua-extension-<ver>.zip` unpacked; the native-host manifest allowlists both install flavors.
- `tabs(op, ...)`: enumerate and manage tabs. `list` returns the full window/tab map with per-client ownership and current-target labels. `open` / `switch` / `close` manage tabs; `claim(tab_id)` claims an unowned or orphaned tab without activating it, and `release(tab_id)` releases a tab lease without closing the tab. Switching requires the window lease; closing requires ownership. `force=True` never overrides another client. Release and closed-target results need not carry a target.
- `windows(op, ...)`: enumerate and manage windows: `list` (the thin variant), `open` (a new owned window, unfocused by default), `claim(window_id)` (claim an unowned or orphaned window and its tabs), `release(window_id)` (release ownership without closing), `focus` (the explicit raise), and `close`. A foreign child-tab lease blocks a window claim. Focus and close require the window lease; `force=True` never overrides another client.
- `profiles(op, ...)`: enumerate and select the connected browser profile when the extension is installed in more than one Chrome profile (personal, work, several Google accounts). `list` shows each profile with recognition context (window / tab counts and a few open tab titles, e.g. "the one with work Gmail and Figma"); `use(profile_id)` pins which profile the browser / tabs / windows ops act within. A single connected profile is used automatically; with more than one connected and none selected, the next op raises a terminal `profile_ambiguous` listing the choices (never a silent guess). You can also pin a default with `CUA_BROWSER_PROFILE` (a profile_id prefix or a tab-title substring).
- `browser_eval(expression)`: evaluate an expression in the page, for verification and debugging.

### Tier 1: structured desktop (5, Linux)
Read and drive native apps through the accessibility tree (AT-SPI over `dbus-fast`), so the agent acts on a control by role and name instead of guessing a pixel. Small text and tens of milliseconds instead of a full screenshot. Reads use a one-call bulk read (`Cache.GetItems`) per app where the toolkit exports it, degrading to a node walk where it does not. Enablement is handled per toolkit: `app_open` launches a Chromium or Electron entry with its accessibility flag (Chromium only reads that gate at startup). The tier never sets the bus screen-reader flag, because on GNOME that autostarts a screen reader that then speaks; a thin already-running Chromium or Electron window reads tree-only, and the remedy is to relaunch it through `app_open` (which adds the flag) or to drive it through the browser tier. Reported by `get_platform_info`'s `structured` block, which also carries `coordinate_trust`: `real` on X11, and `per_window` on Wayland, where a Wayland-native window's bounds are window-relative but an XWayland client's are true screen pixels, so each found element carries its own `coordinate_trust` and a `real` one can ground a Tier 2 click.
- `ui_tree(depth=6, app="")`: an accessible tree, filtered and depth-capped. `app=""` is the focused window; `app="Name"` is that application's window even if unfocused; `app="*"` is every open window.
- `ui_find(role, name, app="")`: elements matching a role and/or name, each with an opaque `ref`, `bounds`, decoded `states` and the title of its owning `window` (what tells equal matches in different windows apart). On Wayland each element also carries `coordinate_trust`. An application that leaves a state or bounds read unanswered (Flutter apps do) costs one half-second wait per call, not five seconds per element: its elements are still returned, with `unknown` naming the `bounds` and `states` that could not be read. `app` scopes the search the same way as `ui_tree`. An empty match is a successful read, not an error.
- `ui_act(ref, action, text="")`: act on a `ref`: `click`, `focus`, `set_text`, `toggle`, `expand`. It re-reads the element and returns its new state; a stale `ref` fails `element_gone` and never falls back to clicking an old coordinate.
- `ui_wait(role, name, timeout_ms=5000, app="")`: block until a matching element appears or the timeout elapses.
- `ui_windows()`: list open top-level windows across all apps (app name, title, active flag, ref), so you can discover what is open before targeting a window by name.

### Tier 2: desktop (13)
Capture (2)
- `screenshot()`: full screen, downscaled to `CU_MAX_WIDTH` (auto-picks 1024 / 1280 / 1366).
- `screenshot_region(x, y, w, h)`: cropped region.

Input (8)
- `click(x, y)` / `double_click(x, y)` / `right_click(x, y)`
- `move_mouse(x, y)` / `drag(start_x, start_y, end_x, end_y, duration=0.5)`
- `scroll(x, y, amount)`: positive = up, negative = down
- `type_text(text)` / `key_press(keys)`: keys like `ctrl+s`, `alt+tab`, `enter`

Platform info (3)
- `get_platform()` / `get_platform_info()` / `get_screen_size()`
- `get_platform()` reports the detected OS even before a capture/input backend
  is available; the richer capability and screen probes still require one.

## Daemon management (WSL2)

Most users never touch this. For when you do:

```bash
vadgr-cua doctor           # JSON: platform, Windows Python, daemon state, port, hash
vadgr-cua install-daemon   # Eager deploy + launch
vadgr-cua stop-daemon      # Kill the running daemon
vadgr-cua restart-daemon   # Stop then start
```

The daemon file is deployed to `%USERPROFILE%\vadgr\daemon.py` and listens on TCP `127.0.0.1:19542`. After `pip install -U vadgr-computer-use`, the next MCP session detects the version-hash drift via a `ping` handshake and redeploys the daemon automatically.

## Library usage

```python
from computer_use import ComputerUseEngine

engine = ComputerUseEngine()
shot = engine.screenshot()
engine.click(500, 300)
engine.type_text("hello")
```

The library is just the input/capture primitives, no LLM or agent loop inside. To drive it with a model, point an MCP client (Claude Code, Codex, Gemini, or your own) at the `vadgr-cua` server as shown above.

## Environment

| Variable | Purpose |
|----------|---------|
| `CU_MAX_WIDTH` | Override screenshot downscale target (default: auto 1024/1280/1366) |
| `CUE_BRIDGE_PORT` | Override WSL2 bridge daemon TCP port (default: 19542) |
| `VADGR_DEBUG` | Set to `1` to dump screenshots to `<package>/.debug/` |

## Tests

```bash
pip install -e ".[dev]"
pytest computer_use/tests -q
```

## License

Apache 2.0. See `LICENSE`.

## Part of Vadgr

- [vadgr](https://github.com/MONTBRAIN/vadgr): the daemon that runs on your machine, and the CLI (brain)
- **[vadgr-computer-use](https://github.com/MONTBRAIN/vadgr-computer-use)**: computer-use MCP with system, browser, and desktop tiers (hands and eyes)
- [vadgr-agent-os](https://github.com/MONTBRAIN/vadgr-agent-os): containerized agent runtime
