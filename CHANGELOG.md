# Changelog

All notable changes to this project are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows [SemVer](https://semver.org/).

## [0.7.9] - Unreleased

### Added

- Package explicit Windows, WSL, Linux and macOS artifacts for x86_64 and ARM64.
- Keep one standalone installation with native Windows helpers for both architectures.
- Require authenticated final helper identities for managed installations and
  verify exact signed helper adoption offline for standalone clients.
- Publish retained profile artifacts without rebuilding their validated bytes.
- Add a GNOME Shell extension for GNOME 50, installed per user by
  `vadgr-cua install-deps`. While a native UI tool, a pixel tool or `app_open`
  runs, an orange glow lights every monitor edge, a pill at the top names the
  action, and a click-type action first shows a ring at its target, or a box
  around a control whose screen bounds are trusted. Esc stops vadgr: for 30
  seconds every desktop tool returns `user_stopped` instead of acting, and the
  key does not reach the application. The server instructions tell the model to
  stop and wait for the user when it sees `user_stopped`.
- Take GNOME Wayland screenshots through that extension, without the flash the
  screenshot portal always shows and without the overlay in the image. Capture
  falls back to the portal when the extension is not running, and
  `get_platform_info` names the capture path.
- Let `ui_act` find its own target by `name`, `role` and `app` when no `ref` is
  given, so a native interaction takes one call instead of a `ui_find` and a
  `ui_act`. It acts only when exactly one element matches, and a whole-name
  match wins over partial ones. No match returns `not_found` with the nearest
  candidates, several return `ambiguous` with up to five candidate refs, and a
  by-name act returns the ref it used. A `ref` still wins when both are given.
  The server instructions now lead native apps with `ui_act` by name.

### Changed

- Return dict tool results as compact JSON. MCP clients hand the text to the
  model unchanged, and the indent=2 layout was about 40% of the tokens of a
  structured read such as `ui_tree`, `ui_windows` or `browser`. A tool whose
  declared output schema is an object, such as `type_text`, keeps its dict,
  because the MCP SDK validates that result against the schema.
- Lead the server instructions with the structured tiers and keep screenshots as
  the fallback. The previous instructions required a screenshot before and after
  every click, which contradicted the tier order the tools are built around.

### Fixed

- Show "button" as the `ui_find` role example. Current toolkits report that name,
  so a search that followed the old "push button" example matched nothing and
  the agent fell back to a whole-window `ui_tree`.
- Preserve Insert in Linux keyboard chords and reject unsupported Wayland keys
  before sending any part of the chord.
- Read Linux accessibility roles when a toolkit omits the optional role-name
  method, keeping its windows and controls available through the structured tier.
- Report an unsupported action, rather than a vanished element, when a live
  Linux control does not expose the native Action interface.
- Bound the Linux structured tier's state and bounds reads at half a second.
  Flutter apps such as the Ubuntu App Center leave them unanswered for some
  nodes, and at the five-second bus timeout one `ui_tree` of such an app took
  25 seconds and dropped those nodes. After one unanswered read an app is not
  asked again within the same tool call, and its elements are still returned,
  with `unknown` naming the `bounds` and `states` that could not be read.
- Cancel an accessibility-bus call that outlives its timeout instead of leaving
  it pending, and report every such timeout as one error class, including on
  Python 3.10, where the loop raises its own timeout classes.
- Serialize GNOME Wayland portal screenshots across processes. Overlapping
  requests from concurrent computer-use servers waited out their whole timeout
  and failed; each request now takes a per-user turn.
- Select Windows helpers by the native system architecture, including the
  Windows host used by WSL, and refuse mismatched execution profiles.
- Preserve exact member identity through signed deployment, shared Windows/WSL
  helper use, restart and authorized predecessor handoff.
- Refuse unsigned fallback after signed helper adoption.
- Verify signed predecessor manifests during exact cataloged upgrades and
  rollbacks. Preserve signed adoption state across changes to packaged inputs.
- Return a structured unsafe-upgrade refusal for invalid candidate or adoption
  records without exposing private paths or hiding unexpected runtime faults.

## [0.7.8] - 2026-09-12

### Added

- Add an authenticated broker drain operation for later Windows broker updates.
- Record the Windows process creation identity in private broker discovery and
  authenticated client status.

### Fixed

- Replace a live released 0.7.6 or 0.7.7 Windows browser broker automatically
  when it retains the shared startup lock during an update.
- Require the current owner, one stable process handle, the Windows creation
  identity, the exact content-addressed path, a frozen release manifest and
  every installed payload hash before stopping a predecessor.
- Resolve the single process holding an unreadable released broker lock through
  Windows Restart Manager and recheck that lock ownership before termination.
- Preserve unknown processes and state with an exact safe-upgrade error. Broker
  recovery does not toggle the extension, restart Chrome, change the network or
  replay an uncertain browser operation.
- Carry explicit broker, browser discovery and application-data paths across
  WSL executable interop so isolated clients reach the intended Windows broker.

## [0.7.7] - 2026-09-12

### Fixed

- Restore a missing or damaged browser-broker discovery record while the
  elected broker remains live. Existing clients keep the same broker epoch.
- Require an authenticated endpoint before startup reports readiness. A held
  broker lock no longer stands in for a usable connection.
- Preserve exact broker discovery, bundle and endpoint failures through the WSL
  proxy. Windows interop advice now appears only when interop itself is absent.
- Add deterministic and live lifecycle coverage for clean, concurrent, missing,
  corrupt, stale and mismatched broker state.

## [0.7.6] - 2026-09-01

### Added

- Add one detached per-user browser broker for concurrent local MCP clients.
  Each client has its own profile selection and exact current target.
- Run the shared Windows/WSL broker as one verified self-contained Windows
  process. WSL uses the packaged stdio proxy, so NAT and mirrored networking
  converge without Windows Python or network-setting changes.
- Add `vadgr-cua --version` so clean-install and support checks can identify the
  installed distribution without importing source files.
- Add window leases as the normal multi-tab browser workspace. Add explicit tab
  claims for a single tab in a shared user window. Registry listings show mine,
  other, unowned, and orphaned targets without hiding foreign targets.
- Add opt-in human-paced browser and pixel typing. Both surfaces use one
  validated schedule, a versioned 68 WPM default profile, or an explicit WPM
  and within-session IKI coefficient of variation.

### Changed

- Route browser operations by exact profile, window, and tab identifiers.
  Concurrent requests no longer share one mutable target or one response lock.
- Make browser actionability descend through nested open shadow roots and use
  composed-tree containment while retaining real-overlay rejection.
- Keep `browser fill`, fast browser `type`, and fast pixel `type_text` as the
  backward-compatible bulk or existing backend paths.
- Remove the implicit total deadline from human-paced browser and pixel typing.
  An optional caller timeout remains one explicit monotonic budget. Browser
  typing now streams bounded, progress-confirmed chunks with a per-chunk
  inactivity backstop and never replays an uncertain dispatch.
- Replace independent key delays and whole-message normalization with the
  released empirical gap tables plus one stationary four-bin rank chain. The
  chain adds local cadence while preserving each timing distribution. Ordinary
  spaces add no separate pause.
- Use Unicode grapheme clusters as timed units. Custom human timing accepts an
  IKI coefficient of variation from 0 through 1 and keeps the same rank chain.

### Fixed

- Verify the bundled Windows broker against its exact source inventory after a
  squash merge, without requiring the feature-branch commit to remain reachable.
- Retire a definitively dead or replaced browser-broker transport before the
  next dispatch. The first stale client now reaches the replacement epoch's
  explicit `target_lost` fence without replaying an uncertain operation.
- Stop and join the native host's standard-input reader when either Chrome or
  the browser broker disconnects, avoiding an interpreter shutdown abort.
- Deliver trusted browser clicks to an exact inactive tab by emulating page
  focus only for the CDP pointer sequence. The tab stays inactive and its window
  stays unfocused.
- Describe the existing tab and window claim/release operations in the MCP
  tool catalog, including lease requirements and release-without-close behavior.
- Reject a stale browser target immediately after broker replacement, before
  extension recovery can mask the required explicit-reclaim error.
- Protect browser credentials and their dedicated directories before the first
  write, replace discovery files atomically, and refuse unsafe paths or failed
  permission setup. Windows publication verifies an owner-and-SYSTEM-only ACL.
- Require native-host authentication before sending a hello or registering a
  browser session. Reject missing, malformed and incorrect credentials.
- Allow an explicit child-tab claim after its window lease expires without
  granting the parent window or clearing the remaining siblings' orphan fences.
- Preserve explicit releases when refreshing opener-derived tabs and popup
  windows, including descendants received out of order.
- Report profile selection and ambiguity for the requesting browser client,
  without inheriting another client's selection or changing setup diagnoses.
- Update vulnerable extension development dependencies while preserving the
  shipped extension bytes and separate scientific tests from shared gate tests.
- Use Windows PowerShell's built-in modules for broker installation and launch,
  without inheriting incompatible modules from a PowerShell 7 parent.
- Publish the Windows broker bundle without nesting a second install inside
  the first when native Windows or WSL clients start concurrently.
- Reconnect before the next browser operation when a heartbeat has already
  detected a closed broker connection, without replaying dispatched operations.
- Find Windows browser profiles under redirected local application data when
  reporting a disabled development extension.
- Preserve Windows broker deployment paths containing spaces or shell syntax
  when native Windows and WSL launch the broker through PowerShell.
- Keep browser mutation response targets local to the requesting client,
  including when a window or tab closes while another client remains active.
- Preserve named browser-evaluation errors in diagnostic redaction without
  retaining private page details.
- Keep browser test setup responsive when its management tab is hidden.
- Preserve supplementary Unicode characters and joined emoji in macOS pixel
  typing by passing their complete UTF-16 length to the native event API.
- Keep macOS synthetic keyboard state in a private event source and balance
  modifier presses and releases, including interrupted typing and key chords.
- Report missing or disabled browser setup for an already selected profile,
  with the matching remedy, instead of waiting for recovery that cannot succeed.
- Restore a connected browser client's liveness when its authenticated
  heartbeat resumes, so a temporary heartbeat gap does not orphan its leases.
- Detect missing Windows native-host setup from the real HKCU registration
  instead of a separate broker manifest copy.
- Keep an explicitly selected browser profile and its owned target pinned while
  its MV3 worker reconnects, instead of silently retargeting a remaining browser
  profile and rejecting the original operation as foreign ownership.
- Return the named Windows interop remedy when WSL cannot launch the shared
  browser broker, instead of collapsing the setup failure to a generic tool error.
- Preserve a released current target as a stale fenced selection. The next page
  dispatch now fails before extension execution instead of silently creating a
  replacement window, so lease revisions remain observable and race-safe.
- Keep covered-element rejection mandatory in real extension documents when a
  hidden or throttled target transiently reports a zero root layout box or no
  compositor hit, while preserving ordinary inactive-target operations.
- Deliver each human-paced browser unit through the exact leased tab's content
  channel, with ordered keydown, before-input, input and keyup events plus
  per-unit read-back. Inactive tabs and unfocused windows no longer discard
  paced input, and the path never activates or foregrounds its target.
- Keep hidden background tabs from waiting forever for an animation frame while
  verifying a state-bearing DOM click.
- Reject trusted keyboard commands by name when their exact tab or window is
  inactive, instead of reporting a discarded CDP key event as success or
  activating the target. Discarded, frozen and restricted targets now fail by
  name too.
- Keep native Windows auto-registration on the packaged, content-addressed
  native-host executable. Starting a Windows MCP client no longer replaces the
  working Chrome registration with a Python batch launcher.
- Honor `profile_id` carried by `browser(use_target)` before the shared broker
  performs its multi-profile ambiguity check.
- Confirm a native port only after the broker hello arrives, and probe an
  apparently open port from the MV3 heartbeat so a failed host can reconnect.
- Distinguish missing native-host setup, a missing extension, a disabled
  extension, an enabled but sleeping worker, and a bounded recovery timeout.
- Stop returning typed input in the pixel tool result. The result now reports
  counts and timing metadata only.
- Keep WSL input-bearing PowerShell scripts on the persistent process pipe.
  They no longer fall back to a temporary script file.

## [0.7.5] - 2026-08-29

### Fixed

- Let the MCP `get_platform` probe report the detected operating system even
  when that host's capture or input backend is unavailable or not configured.

## [0.7.4] - 2026-08-20

### Fixed
- **The supervisor imports on Windows, and `vadgr-cua doctor` runs there.**
  `supervisor.py` imported `fcntl` at module scope, which does not exist on
  Windows, so the module that supervises the Windows bridge could not be
  imported on Windows and `doctor` died on a traceback. `fcntl` is optional
  now, and the no-op lock the file already documented for Windows can finally
  run.
- **The test suite can be collected on Windows and macOS.** An autouse fixture
  patched a Linux-only module, so every test errored in setup off Linux and the
  suite could not run at all. `test_supervisor.py` was also skipped whole on
  Windows, which removed the only tests that would have caught the import above.
  On Windows the suite goes from uncollectable to 752 passed, 82 skipped and 11
  failed; the 11 are Linux assumptions in tests, now visible and untouched.
- **The clean install is proven on every platform this runtime claims.** It ran
  only on Linux, across three interpreters, so a first-time install on Windows
  or macOS was never checked at all. Linux still makes the machine with a
  container; Windows and macOS approximate it with a fresh virtual environment
  entered from a neutral directory, and each step says which of the two it does
  so a green run is read for what it is.
- **The clean install checks that the wheel it tested is the wheel this run
  built.** Nothing compared versions, so an older wheel left in `dist/` would
  install and pass every other check. The expected version is read from
  `pyproject.toml` at run time and the smoke test refuses to run without it,
  rather than passing while comparing nothing.
- **A missing console script is a failure where a wheel install is expected.**
  The smoke test skipped that check when `vadgr-cua` was not on `PATH`, which is
  right for an isolated site directory and wrong after a real install, where its
  absence is the `0.6.6` failure exactly.

## [0.7.3] - 2026-08-17

### Fixed
- `fs` expands a leading `~` in its path. `fs write` with `~/notes.txt` used to
  create a directory literally named `~` beside the process working directory
  and report success, and `fs read` of the same string found the file there, so
  the round trip looked correct while nothing existed where the owner would
  look. Found on macOS by a live model that wrote its result to `~/report.txt`
  and confirmed it, leaving the file inside the daemon's checkout.
- `shell.run` expands a leading `~` in `cwd`. A subprocess given a literal `~`
  failed with a "no such file or directory" naming the tilde, which reads as a
  broken tool rather than an unexpanded path.
- The `at_spi_unavailable` remedy fits the platform it is reported on. The
  structured native tier speaks AT-SPI, which only Linux has, but every
  platform was told to install `at-spi2-core` with `vadgr-cua install-deps`.
  On macOS and Windows that names a package the platform cannot have and
  reads as a broken install rather than a tier that is not offered there.
  Those platforms are now pointed at the browser tier and the pointer tools.

## [0.7.2] - 2026-08-17

### Fixed
- `shell.run` splits a string command into argv instead of treating the whole
  string as one program name. `command: "uname -a"` now runs. It used to fail
  with `No such file or directory: 'uname -a'`, which reads as a missing binary
  and blames the machine for the argument shape. No shell is involved, so
  nothing is expanded and no operator is interpreted.
- `shell.run` refuses a string carrying shell syntax (`&&`, `||`, `|`, `;`,
  redirection, `$()`, backticks) and names the operator it found, instead of
  passing it through as a literal argument. The message points at
  `shell_mode=True`, which is what actually runs it.
- The split follows the platform. POSIX reads a backslash as an escape and
  Windows reads it as a path separator, so tokenising a Windows path in POSIX
  mode turns `C:\Users\me\tool.exe` into `C:Usersmetool.exe`, which names
  nothing. Windows tokenises in non-POSIX mode and the quotes that mode leaves
  around a token are stripped, so a quoted program path still resolves. When a
  split yields several tokens and the whole string names a file that exists,
  the whole string wins, which keeps a bare unquoted path with a space in it
  working exactly as it did before.

### Changed
- The `shell` tool description states that a string command is split into argv
  and that shell syntax needs `shell_mode=True`, so a caller does not have to
  discover the contract by failing.

### Repository
Nothing here changes the published package; it repairs the gate that guards it.

- The credential gate can pass on Windows. `scripts/check_no_secrets.py` passed
  the target path as a trailing argument to `powershell.exe -Command`, which
  does not populate `$args`, so `Get-Acl` received nothing and the check failed
  closed on every file. A protected file and an unprotected one produced the
  same refusal, so the gate could not tell them apart. The target now arrives as
  the `VADGR_ACL_TARGET` environment variable, which also keeps a path with
  spaces or quotes out of the PowerShell parser.
- The gate now runs on Linux, macOS and Windows in CI, and it has tests of its
  own for the first time. It had only ever run on one platform, so its Windows
  control had never been executed anywhere.

## [0.7.1] - 2026-08-17

### Fixed
- Cancelling an active `shell.run` MCP request now terminates its owned process
  group on Unix and process tree on Windows.

### Changed
- `shell` is asynchronous, so a cancelled request reaches the child rather than
  leaving it running.
- Command output decodes with replacement rather than strictly. The synchronous
  path used the locale codepage, so output that is not valid UTF-8 kept working;
  a strict decode would have turned ordinary Windows output into a failed call.

## [0.7.0] - 2026-08-12

Adds a structured Tier 1 on Linux: read the accessibility tree, find an element
by role and name, act on it by reference, and wait for one to appear. Small text
and tens of milliseconds instead of a full screenshot, and correct under a
window that moved. Nothing in Tier 2 changes: the pixel path stays exactly as it
is and remains the fallback.

The surface goes from 26 tools to **33**: the four structured-tier tools plus the
`0.7.x` expansion (`ui_windows`, `apps`, `app_open`). The 26 existing tools keep
their names and their derived schemas byte-for-byte; the new tools are added,
never altered.

### Added
- **Read any window, and read it fast.** `ui_find`, `ui_tree` and `ui_wait` gain
  an optional `app` target. `app=""` is the focused window (unchanged behaviour
  and token cost); `app="Name"` targets that application's window even when it is
  not focused; `app="*"` searches every open window. Reads use a one-call bulk
  read (`org.a11y.atspi.Cache.GetItems`) per app, fanned out concurrently, and
  match in-process, so a warm single-window read is about eleven D-Bus round
  trips against about seven hundred for the node walk. An app whose toolkit does
  not export the cache degrades to the walk; an incomplete branch falls to a live
  read that also warms it, so no element is missed.
- **`ui_windows`** lists every open top-level window (app name, title, active
  flag, and a `ref`), so the model can discover what is open before targeting a
  window by name.
- **`apps` and `app_open`** (Tier 0). `apps` lists installed launchable apps from
  the XDG desktop entries (id, name, icon), dropping `Hidden` and `NoDisplay`
  entries and keying by desktop-file-id with the home directory winning.
  `app_open` launches one by id or name through the desktop's own launchers
  (`gio launch`, `gtk-launch`, then a manual `Exec` field-code expansion) and
  confirms the launch on the a11y bus before reporting `ok`: a launcher's exit
  code only proves dispatch, so the tool polls for a window belonging to the
  app, returns it in the result (with `already_open` when it predates the
  launch, since a single-instance app presents its existing window), escalates
  to the next launcher when a dispatched one maps nothing, and fails
  `no_window` rather than claiming success. A window matches by the strongest
  identity available: the launched process (or a descendant) owning it, a name
  token from the desktop entry, the comm of a process that appeared after
  dispatch, or, last, a window that did not exist before dispatch and whose
  title carries an entry token. The last rule is what confirms a
  single-instance snap: LibreOffice opens the new Writer window inside the
  already-running `soffice` process, so no pid, token or new comm ever matches
  it, and only the windows-since-dispatch diff plus the title
  (`Untitled 2 - LibreOffice Writer`) identifies the launch. `timeout_ms`
  (default 5s) is one total budget split across the launcher ladder, so
  escalation never multiplies the caller's wait. A `DBusActivatable` entry tries `gtk-launch` before `gio launch`,
  which exits 0 after queueing the D-Bus `Activate` call it never waits for;
  the daemon drops the queued call once the sender is gone, so the service
  starts, idles, and exits without a window. Where the a11y bus cannot answer,
  the result says `confirmed: false` instead of guessing. The launcher
  prerequisite is registered in `vadgr-cua install-deps` beside the
  accessibility stack.
- **Four structured-tier tools.** `ui_tree` reads the focused window's tree
  (filtered, depth-capped). `ui_find` returns elements matching a role and/or
  name, each with an opaque session-scoped `ref`, `bounds`, decoded `states`,
  and the title of its owning `window`, which is what tells equal matches
  apart (nine Writer documents expose nine paragraphs identical in role, name
  and window-relative bounds); an empty match is a successful read, not an
  error. A find whose walk lost a subtree to a node that stopped answering
  mid-mutation (an app replacing its tree while it navigates) retries once and
  returns the settled read. `ui_act` performs
  `click`, `focus`, `set_text`, `toggle` or `expand` on a `ref`, then re-reads
  the element and returns its new state; a stale `ref` fails `element_gone` and
  never falls back to clicking an old coordinate, and an unsupported verb fails
  `unsupported_action` listing what the element does support. `ui_wait` blocks
  until a matching element appears or a timeout elapses. On a machine with no
  accessibility bus every one returns `at_spi_unavailable` with a one-line
  remedy, and the tools stay listed so a consumer's catalogue does not vary per
  machine.
- **The tier survives the toolkits as they are**, each behaviour observed on
  real applications and encoded with a test:
  - Text is read by the element's real range. LibreOffice answers
    `GetText(0, end)` with an empty string whenever `end` exceeds the character
    count (GTK clamps instead), so a fixed-cap read reported every Writer
    paragraph as empty and made a landed `set_text` look like a no-op; an empty
    read is re-checked against `CharacterCount` before it is believed.
  - The `click` verb resolves the toolkit's own action name: GTK's `click`,
    Qt's `Press`, Flutter's `Tap`. An element whose bulk `GetActions` reply
    carries blank names (the Ubuntu App Center) is re-asked per index.
  - `set_text` trusts only the read-back, because the bus adaptor reports
    success unconditionally; a write that did not land (Flutter accepts
    `SetTextContents` and writes nothing) is retried as delete-plus-insert,
    which lands.
  - An application that never answers a call cannot stall the tier: an
    unanswered `GetExtents` or `Cache.GetItems` trips a per-app breaker (the
    element reads as boundless, the app reads live), a gathered bulk read is
    bounded per call so one silent app cannot sink the batch, and a node the
    app will not answer for is `element_gone`, which walks already skip.
- **A Chromium or Electron app is launched with its tree enabled.** Chromium
  reads its accessibility gate once at startup and never again, so `app_open`
  detects a Chromium-family desktop entry (the Exec program's basename, the
  `StartupWMClass`, or the entry id, matched exactly against the known family)
  and dispatches its expanded `Exec` line first with
  `--force-renderer-accessibility` appended and the enabling environment set
  (`ACCESSIBILITY_ENABLED=1`, and Qt's gate rides beside it). `gio` and
  `gtk-launch` run the `Exec` line as written and cannot carry the flag, so
  they stay on that ladder as fallbacks. Driven live: a cua-launched VS Code
  exposes its full tree (menu bar, explorer, about 111 KB at depth 5) where an
  unflagged launch exposes two nodes per window. Every non-Chromium entry
  keeps the unchanged ladder and gets neither the flag nor the environment.
- **The tier never raises `org.a11y.Status.ScreenReaderEnabled`.** On GNOME that
  bus property is bridged to the `org.gnome.desktop.a11y.applications`
  `screen-reader-enabled` gsetting, which autostarts a screen reader within about
  130 ms (verified live on GNOME 50.1), and the screen reader then speaks. So a
  read never sets it, even for a thin toolkit that would build its tree only
  under an assistive tool. A thin already-running Chromium or Electron window
  reads tree-only; the remedy is to relaunch it through `app_open`, which adds
  `--force-renderer-accessibility`, or to drive it through the browser tier.
  Modern Flutter, the original reason for a forced enable, now exposes its tree
  with no such flag.
- **Per-window coordinate trust on Wayland, and a grounded pixel click where
  it is `real`.** Wayland is two answers, not one: a Wayland-native window's
  bounds are window-relative, while an XWayland client's bounds are true
  screen pixels the X server vouches for. Each found element now carries
  `coordinate_trust` (`real` or `none`) on Wayland, earned by an exact match
  between the frame's claimed geometry and a mapped X toplevel; a
  Wayland-native popup that guesses a nonzero origin never passes, and a
  zero-origin frame is refused without an X round trip. Driven live: the VS
  Code File menu item's bounds grounded a Tier 2 `click` that opened the menu,
  confirmed structurally. Elements the check refuses keep the coordinate-free
  paths: native actions cover 94 percent of the interactive elements measured
  across five real apps, and `focus` plus a key covers most of the rest.
- **A `structured` block on `get_platform_info`.** Reports the backend, whether
  the bus is reachable and enabled, the toolkits seen, and `coordinate_trust`:
  `real` on X11, and `per_window` on Wayland, where each found element carries
  its own verdict as above.
- **The AT-SPI stack in `vadgr-cua install-deps`.** When the accessibility bus
  is not reachable, the plan now installs `at-spi2-core` plus the ATK bridge
  (package names resolved per distro), alongside the existing clipboard and
  `/dev/uinput` steps.

### Changed
- **The structured tier and Wayland foreground-window detection speak AT-SPI
  over `dbus-fast`**, a plain pure-Python wheel now pulled in automatically on
  Linux. The previous foreground-window path imported PyGObject under an extra
  that did not exist, so it was dead on every install this package produces;
  migrating it onto `dbus-fast` makes it work.
- **`apps` and `app_open` resolve one provider per OS behind a seam**, the same
  shape the structured tier uses. The Linux behaviour is unchanged: it still
  lists the XDG desktop entries and launches through `gio`, `gtk-launch` and the
  `Exec` expansion, with the a11y-bus window confirmation and every launch fix
  intact. On Windows and macOS the two tools now return a named
  `apps_unsupported` result with a reason, rather than an empty list or a crash,
  until those platforms get their own provider. The Linux provider is split into
  desktop-entry reading, window confirmation, and launch orchestration.

## [0.6.6] - 2026-08-09

Fixes #39: a fresh `pip install vadgr-computer-use` could not start. `mcp` 2.0.0
removed `mcp.server.fastmcp`, this package declared `mcp>=1.0` with no upper
bound, so a new install resolved 2.0.0 and `vadgr-cua` died at import with
`ModuleNotFoundError: No module named 'mcp.server.fastmcp'`. The `test` workflow
has been red on the same error since 2026-08-04.

**The tool surface is unchanged.** The 26 tools keep their names, their derived
input and output schemas and their result shapes; both majors were snapshotted
over a live stdio `tools/list` and the diff is empty. **Every one of the 26 was
then called** over the wire from a fresh, non-editable install and each returned
its declared shape, so the claim covers what the tools do and not only how they
are declared. Nothing else in this release changes behaviour.

### Fixed
- **The MCP server builds on `mcp.server.mcpserver`.** `FastMCP` becomes
  `MCPServer` and `mcp.server.fastmcp.exceptions.ToolError` becomes
  `mcp.server.mcpserver.exceptions.ToolError`, at the same position in the
  exception hierarchy, so the browser tier's page reason, remediation and guided
  pixel fallback still reach the model verbatim. `Image` comes from the same new
  module and serializes the same content block.
- **The `--port` flag reaches the SSE transport again.** 2.x's settings object
  has no `host` or `port`; the transport takes its options on `run()`. The start
  path now branches on the transport and passes each call only what its own
  overload accepts, so `--transport sse --port N` binds to N as it did before.
  Nothing on the SSE path changes otherwise: the host, `sse_path`,
  `message_path` and the loopback rebinding protection all keep their previous
  defaults.

### Changed
- **The `mcp` dependency is now `mcp>=2.0`.** The floor moves because
  `mcp.server.mcpserver` exists in no 1.x release, so without it a resolver
  reaching 1.x would rebuild the same broken install from the other side. There
  is deliberately no upper bound: this package tracks the current `mcp`.
- The three tests that read the tool surface now read it from the public
  `list_tools()` instead of a private tool-manager dictionary, and the one that
  could `pytest.skip` itself when that dictionary moved no longer can. Two new
  tests hold the surface as a total: the exact 26 names as set equality, and a
  source scan proving no module reaches for the removed package.
- Version bumped to 0.6.6 across the package, the extension manifest, the
  extension package and lockfile, `CUA_VERSION` and the `background.ts` version
  fallback.

### Notes
- No new tools, no changed tools, no behaviour change on any tool, and no new
  transport: 2.x's `streamable-http` is not offered, `--transport` still takes
  `stdio` and `sse`.
- The Python floor is unchanged. `mcp` 2.0.0 requires >=3.10, the same floor
  this package declares, so the 3.10 / 3.11 / 3.12 matrix is untouched.
- One difference worth knowing about even though nothing here depends on it:
  1.x read the SSE bind from `FASTMCP_HOST` and `FASTMCP_PORT`, and 2.x does
  not. This package never read them, never documented them and has its own
  `--port`.

## [0.6.5] - 2026-07-24

Fixes #36: a Chrome Web Store install of the extension could never connect to
the native host, and once the first connect was refused the MV3 service worker
had no path left to retry.

### Fixed
- **Native-host manifest now allowlists the Web Store extension ID.** The
  store build has a different ID than the unpacked dev build (the store strips
  the pinned `key`), but `extension_setup.py` wrote
  `allowed_origins: ["chrome-extension://<dev id>/"]` only - so Chrome refused
  `connectNative` for every Web Store install before the host ever spawned,
  with nothing logged. The manifest now always carries BOTH origins
  (`EXTENSION_ID` dev + `WEBSTORE_EXTENSION_ID` store), on every platform the
  registration covers (Linux/macOS/Windows/WSL, chrome/chromium/edge).
- **Manifest rewrites merge `allowed_origins` instead of clobbering them.**
  `ensure_registered()` runs on every MCP-server start and rewrote the manifest
  unconditionally, silently reverting any manual allowlist fix. Extra origins
  found in an existing manifest now survive the rewrite (union, deduplicated),
  while both known IDs are always guaranteed present.
- **The service worker can now (re)establish the native port after MV3 idle
  termination.** `connect()` was reachable only from `onStartup`, `onInstalled`
  and the in-memory reconnect controller - none of which fire when an
  idle-killed worker is woken by any other event, so a dropped port stayed dead
  until a full browser restart. Now: `connect()` is idempotent (no-op while a
  port is open, and a synchronous `connectNative` throw backs off instead of
  killing the worker start), the module top level calls
  `ensureOffscreen()` + `connect()` on EVERY worker start, a `chrome.alarms`
  keep-alive (1-minute period, new `"alarms"` permission) revives the port
  after idle death, and the offscreen document's ~20s heartbeat message is now
  answered with a reconnect check as a faster-cadence path.
- The offscreen-document comments/justification no longer claim it "holds the
  native-messaging port alive" - it cannot (the port belongs to the service
  worker); its heartbeat wakes the worker so the reconnect paths run.
- Stale version strings: `CUA_VERSION` in `computer_use/browser/server.py` and
  the `background.ts` fallback both read `"0.6.1"`; both now track the release
  and `test_release_consistency.py` enforces they never drift again.
- The extension advertised `"status"` in `supported_ops` but registered no
  handler (a latent `unknown op`). The router now serves a lightweight
  SW-resolved `status` (`{connected, ext_version, browser}`); the op remains
  answered cua-side (`bridge.status()`) in normal operation.

### Added
- `browser(op="status")`-visible setup text and `extension/README.md` now
  document the Web Store install path and both extension IDs.
- Release-consistency guards: `CUA_VERSION` == package version, the
  `background.ts` version fallback == package version, and the extension
  manifest must keep the `"alarms"` permission.

## [0.6.4] - 2026-07-24

CI/CD: keyless Chrome Web Store auto-publish. No extension behavior change.

### Added
- The release pipeline publishes the extension to the Chrome Web Store on a
  version tag, **keyless** via GitHub OIDC to Google Workload Identity Federation
  impersonating the publisher-linked service account (CWS API, `chromewebstore`
  scope) - no long-lived credential in GitHub. It is gated by a required-reviewer
  `chrome-web-store` environment, and a first-party `extension/scripts/cws_publish.py`
  uploads + publishes so no third-party action ever touches the credential.
- The build job now packages a **key-stripped store zip** (the store rejects a
  manifest with a `key` field) alongside the unpacked zip, verifies the store zip
  has no `key`, records both zips' sha256, and the publish job ships those exact
  bytes.

### Changed
- Every GitHub Action in the release workflow is pinned to a full commit SHA
  (tj-actions CVE class).
- The build job refuses to publish a tag whose commit is not an ancestor of
  `master`, so an off-branch tag cannot ship unreviewed code.
- New `test_release_consistency.py` guards manifest version == package version and
  that the source manifest keeps its pinned `key`.

## [0.6.3] - 2026-07-21

Trusted-click fix for pointer-driven widgets.

### Fixed
- `browser(op="click")` now drives widgets that only respond to genuine pointer
  events. It dispatched `HTMLElement.click()`, which fires exactly ONE synthetic
  `click` event with `isTrusted:false` and **no** `pointerdown`/`pointerup` or
  `mousedown`/`mouseup` at all. Component libraries that open on `pointerdown` and
  act on `pointerup` (menus, selects, popovers, custom radio/scale groups, and the
  dismissable-layer pattern generally) ignored it completely, while the op still
  returned `{clicked: true}` - a silent no-op reported as success. Clicking such a
  control now falls back to real input:
  - `click` self-verifies by diffing a widget-state signature (`data-state`,
    `aria-expanded`, `aria-checked`, `aria-pressed`, `aria-selected`,
    `aria-current`) across the click, so a control that did not react returns
    `ok:false`;
  - `click` joins `ESCALATING`, so that `ok:false` escalates DOM to CDP;
  - `CdpExecutor` gains a `click` case that dispatches a trusted
    `Input.dispatchMouseEvent` stream (`mouseMoved` -> `mousePressed` ->
    `mouseReleased`) through Chrome's real input pipeline.

  Elements with no state signature (plain buttons, links) report no `ok` and never
  escalate, and native checkboxes/radios keep their `checked` read-back and are
  deliberately excluded from `ok`-gating so an escalation cannot toggle them back.

### Added
- `click(..., trusted=True)` skips the DOM fast path and goes straight to the
  trusted CDP event stream. This is the escape hatch for a pointer-driven control
  that exposes no readable state, where the `ok:false` trigger can never fire.
- The CDP `click` hit-tests its centre point (shadow-root aware) and fails loudly
  when another element covers the target, rather than clicking the wrong thing.
  `force=True` bypasses the check.
- Selector resolution in the content script falls back to a breadth-first walk of
  **open** shadow roots when the light DOM misses, so selector ops reach
  shadow-DOM components that previously only `snapshot`/`eval` could see. Pages
  without shadow DOM are unaffected. Closed roots remain out of reach.

### Notes
- Coordinates are viewport CSS pixels taken straight from `getBoundingClientRect()`,
  which is what `Input.dispatchMouseEvent` expects - no DPR correction and no
  window-chrome offset, so the pixel coordinate-mismatch class does not apply, and
  the click works with the window unfocused or occluded.
- Additive and wire-compatible: `click` was already in `SUPPORTED_OPS` on both
  sides, `trusted` and `ok` are new optional fields. No `PROTOCOL_VERSION` bump
  (stays 1). Extension and package versions bump to 0.6.3 in lockstep.
- Out of scope: iframes, closed shadow roots, and `select` escalation.

## [0.6.2] - 2026-07-18

Browser-eval fix for strict-CSP pages.

### Fixed
- `browser_eval` no longer silently returns `null` on pages with a strict nonce-based
  CSP (`script-src 'nonce-...'`, no `'unsafe-eval'`), and no longer swallows page
  exceptions (#28). It ran page-world `eval()` via `chrome.scripting.executeScript`,
  which the page CSP blocks and whose thrown errors surface as `undefined`, so every
  call returned `{value: null}` regardless of the expression, DOM writes never landed,
  and even a `ReferenceError` came back as `null`. `eval` now routes through the
  `chrome.debugger` `Runtime.evaluate` path (like `press` / `snapshot`), which is exempt
  from page CSP and reports exceptions, so a page error is thrown with its real message
  instead of a silent null. The MAIN-world injection stays as the fallback when the
  debugger is unavailable. Additive and wire-compatible; no `PROTOCOL_VERSION` bump.

### Notes
- Extension and package versions bump to 0.6.2 in lockstep; `PROTOCOL_VERSION` stays 1.

## [0.6.1] - 2026-07-17

Browser round 3.1: multi-profile targeting. When the extension is installed in
more than one Chrome profile (personal, work, several Google accounts), cua now
knows which profile each connection is and the agent can choose one, instead of
binding to whichever connected first. No protocol version bump: the handshake
gains additive profile fields and the capability list grows one op.

### Added
- `profiles(op, ...)` op-group (a new Tier 1 tool): `list` returns every
  connected browser profile with recognition context (`profile_id`, `browser`,
  `is_current`, `window_count`, `tab_count`, `sample_tab_titles`) so you can tell
  them apart by what is open in each ("the one with work Gmail and Figma");
  `use(profile_id)` pins which profile the browser / tabs / windows / DOM ops act
  within.
- `use_target` gains an optional `profile_id` that selects the connected profile
  and (optionally) a target within it in one call.
- The extension mints a stable per-profile id (a UUID in `chrome.storage.local`,
  which is isolated per profile) once and reports it plus the recognition context
  in the `hello` handshake. No new permission (storage was already granted).
- `CUA_BROWSER_PROFILE` env var pins a default profile when more than one is
  connected: it matches a `profile_id` prefix or a `sample_tab_title` substring.
- `browser(op="status")` grows a `profiles` array listing every connected
  profile, so the pre-flight shows the choices.

### Changed
- The browser transport keeps every accepted connection concurrently, keyed by
  `(browser, profile_id)`, with a `current` pointer selecting which one ops route
  to. This replaces the single-listener bond (one connection owned the pipe).
  Selection follows a ladder: an explicit `profiles(use)` / `use_target` choice,
  then the `CUA_BROWSER_PROFILE` pin, then the sole connection if there is exactly
  one.

### Fixed
- Never silently wrong on multiple profiles: with more than one connected and none
  selected, the next op raises a terminal `profile_ambiguous` error that lists the
  choices, rather than guessing (the same doctrine as 0.6.0 `target_lost`). If an
  explicitly-selected profile disconnects, the next op is loud too, instead of
  silently falling through to another profile.
- Concurrent cua instances can coexist (#26). The browser server now honors the
  `VADGR_CUA_BROWSER_DISCOVERY` env when writing its discovery file, symmetric with
  the native host that already reads it; previously the server always wrote the one
  per-user path, so a second cua process clobbered the first and the extension bonded
  only to whichever wrote last. Set the same value for a cua process and the Chrome
  that hosts its extension to isolate that pair; on WSL, `VADGR_CUA_BROWSER_DISCOVERY_WINDOWS`
  overrides the Windows-side copy. This unblocks running one cua per profile / per agent.
- `profiles(list)` reports live window/tab counts, not a stale snapshot. The recognition
  context (`window_count` / `tab_count` / `sample_tab_titles`) was captured once in the
  `hello` handshake, so it went stale as windows were opened or closed (a closed profile
  still reported its old windows/tabs). `profiles(list)` now re-queries each connected
  extension for a live context and refreshes the cache; an unreachable session keeps its
  last-known value so the list never fails. Found and fixed in the 0.6.1 WSL e2e round.

### Notes
- Back-compat: a connection whose `hello` carries no `profile_id` (an older
  extension) is registered under a synthetic `default` profile, so single-profile
  setups are unchanged and need no new step.
- Extension version and package version bump to 0.6.1 in lockstep; the
  `PROTOCOL_VERSION` stays 1 (the hello profile fields and the `profiles` op are
  additive).
- The multi-connection registry and the profile handshake are pure Python + a
  pure extension handshake with no path boundary, so Linux / Windows / macOS / WSL
  behave identically; WSL is the parity boundary that proves the multiplexed
  connections round-trip over the bridge. Validated on the automated gate
  (`pytest` / `vitest` / `npm run build` / `npm run typecheck`); the live
  two-profile run is the hardware verification round. This entry is re-checked
  against the final diff at release.

## [0.6.0] - 2026-07-09

Browser round 3: window/tab management (the multi-context session model). The
0.5.0 single pinned target becomes an enumerable, switchable registry, and the
three ways the agent used to get lost in the browser are closed. No protocol
version bump: the new ops grow the capability list only.

### Added
- `tabs(op, ...)` op-group: `list` returns the full `window -> tabs ->
  {tab_id, url, title, active, owned, is_current}` map across the agent's own
  window and yours (tagged by provenance); `open` / `switch` / `close` manage
  tabs. The agent sees every tab but acts only on the pinned target. `switch`
  moves the target and activates the tab within its window without raising that
  window over your foreground; closing one of your tabs requires `force=True`.
- `windows(op, ...)` op-group: `list` (the thin, per-window variant), `open` (a
  new owned window, unfocused by default), `focus` (the explicit, agent-intended
  raise), and `close` (owned only unless `force=True`).
- Every browser op result now carries a `target: {window_id, tab_id, url}`
  field, so the agent always sees which tab it just acted on and a surprise
  `chrome://newtab` is visible immediately (additive; older clients tolerate it).
- `use_target` now also reports the resolved `url` and `provenance`
  (owned / attached / user) and switches the registry's current target.

### Changed
- The target model generalizes from a single pin to a registry of known contexts
  plus a `current` pointer and an `established` flag, all persisted in
  `chrome.storage.session`. Every op still resolves and acts by id (focus-proof,
  unchanged).
- The `target_lost` remediation now points at `tabs(op='list')` then
  `use_target` to re-pin, or `use_target(mode='owned')` to open a fresh window.

### Fixed
- Loud loss: a mid-task target loss (the pinned tab/window closed after a target
  was established) now raises `target_lost` instead of silently re-opening a
  blank `chrome://newtab` window under the agent. A cold start (no target ever
  set this session) still auto-opens the owned window. The split rides on the
  persisted `established` flag, so it survives service-worker idle-termination.
- Content-script self-heal now covers mutating ops (`click` / `fill` / `type`):
  an "unreachable" reply means the message never arrived and the op never ran, so
  re-injecting the content script and delivering once is a safe first delivery,
  not a retry. This closes the "reads self-heal, writes fail on a fresh
  navigation" split. A channel torn down mid-message is still reported as a
  navigation, never redelivered.

### Notes
- Extension version and package version bump to 0.6.0 in lockstep; the
  `PROTOCOL_VERSION` stays 1 (the new ops are additive via `supported_ops`).
- The window/tab/registry/storage surfaces are pure `chrome.windows` /
  `chrome.tabs` extension APIs with no path boundary, so Linux / Windows / macOS /
  WSL behave identically (unlike `upload`). Validated on the Linux + xvfb gating
  harness; per-OS live runs on Windows native / macOS / WSL are done in the
  hardware verification round. This entry is re-checked against the final diff at
  release.

## [0.5.0] - 2026-07-08

### Added
- Session-target model for the browser tier: the extension pins an explicit
  `{browser, window_id, tab_id}` target resolved once and used by every op by id,
  instead of resolving each op against the last-focused window. By default the
  agent opens its own dedicated window in your real Chrome profile (real
  cookies/logins), kept separate so it never fights your foreground tab. The
  pinned target survives service-worker idle-termination (`chrome.storage.session`)
  and follows tabs the agent itself spawns (OAuth popups, `target=_blank`).
- `use_target(mode="owned"|"attach", window_id=None, tab_id=None)` browser op - 
  explicitly pin the session target. Attach mode snapshots the tab you are
  currently looking at once, then pins it by id.
- New browser ops on the `chrome.debugger` path: `hover` (with an optional
  `reveals` check), `dialog` (one-shot arm for JS `alert`/`confirm`/`prompt`/
  `beforeunload`), `upload` (file inputs via `DOM.setFileInputFiles`),
  `element_state` (visible / receives_events / enabled / focused / editable /
  checked / value / bbox), `focus`/`blur`, `clear`, `get_value`, and `snapshot`
  (paginated accessibility tree that pierces shadow DOM and frames; supersedes
  `accessibility_tree`, which stays for back-compat).
- `upload` translates each file path to the browser process's OS before the op
  crosses the wire, so a WSL path (`/home/...` or `/mnt/c/...`) reaches Windows
  Chrome as a path it can actually read. Native Chrome paths pass through
  unchanged.

### Changed
- `query` now caps the node count and per-node text and paginates (`limit` +
  `cursor` -> `next_cursor`), so a large page degrades to pages instead of a
  single oversized result.
- The `browser` tool docstring documents that `screenshot` is a pixel tool, not a
  browser op; `browser(op="screenshot")` now returns that guidance instead of an
  opaque error.
- The extension requires the `storage` permission (for `chrome.storage.session`).

### Fixed
- The browser tier no longer acts on whatever window the user last focused. Every
  op targets the pinned window/tab by id, so a focus change (or a popup stealing
  focus) can no longer move the target mid-task. When the pinned tab/window is
  closed the op fails with a terminal `target_lost` error and remediation; the
  tier never silently retargets the user's active tab.
- WSL: the MCP server no longer hangs on `initialize`. The startup subprocess
  probes (the `reg.exe` native-host registration and the `cmd.exe`/`powershell.exe`
  interop probes) inherited the JSON-RPC stdio pipe on fd 0; they now run with a
  null stdin, so `initialize` returns immediately instead of blocking. (#18)
- WSL: the native messaging host now auto-registers against the Windows registry
  on first run. Platform detection resolves WSL2 through `detect_platform()`
  instead of `sys.platform` (which reports `linux` under WSL), so registration
  targets Windows Chrome and the extension bonds with no manual step. (#19)
- The owned automation window opens at a real, hit-testable size (`state:"normal"`,
  1200x900, still unfocused) instead of minimized. On WSL driving Windows Chrome a
  `focused:false` window could open minimized (about 0px viewport), which made the
  actionability hit-test fail and forced `force` on every mutation. A null
  `elementFromPoint` result (a throttled or occluded window that is not composited)
  is also no longer treated as covered, so a CDP-driven owned window is never
  falsely gated.
- Native replies are correlated by request id instead of arrival order, and
  navigation/read ops are time-bounded. A stray frame (a reconnect `hello`, or a
  late reply from a timed-out op) can no longer shift every following reply by one,
  and a page that never reports load-complete can no longer hang the pipe.
- The browser session tears down on an op timeout so the extension can reconnect.
  A socket read-timeout previously left the buffered reader unrecoverable and
  wedged the session; cua now closes the connection on timeout, the native-host
  relay hits EOF, and the extension reconnects with a fresh session.

### Notes
- No `PROTOCOL_VERSION` bump - every new op is additive and gated on the
  extension's `supported_ops`; an older extension returns a precise
  `op_unsupported` for a 0.5.0 op.

## [0.4.1] - 2026-06-29

### Fixed
- Restore the desktop screenshot tier on GNOME 49/50 (Ubuntu 25.10/26.04): add an
  XDG Desktop Portal screenshot backend as the portable Wayland capture path. It
  is tried after the no-dialog CLI tools, so GNOME 46 / Ubuntu 24.04 keeps using
  `gnome-screenshot` unchanged (no new consent dialog) while GNOME 49+ - where the
  CLI tools no longer work - transparently falls through to the portal.

### Added
- Provider/resolver backend abstraction (`SessionContext` + `CaptureProvider` /
  `InputProvider` + `BackendResolver`) so adding a desktop is one provider, not a
  factory edit. `vadgr-cua doctor` now reports the resolved capture/input backend
  and candidate applicability under `platform_backends`.
- Pure-python uinput input fallback (no `evdev`, no C compiler) and an X11 XTEST
  executor via `python-xlib` (no `xdotool`).
- `vadgr-cua install-deps`: distro-aware provisioning (apt/dnf/pacman/zypper) for
  `wl-clipboard` and the `/dev/uinput` udev rule. Prints the plan; `--yes` runs the
  whole plan under a single privilege prompt - `pkexec` (graphical polkit auth, no
  terminal sudo) when a display is present, falling back to `sudo`. This is the
  second of the two install commands: `pip install` then `vadgr-cua install-deps`.

### Changed
- `evdev` moved to the optional `[linux-uinput]` extra; `python-xlib` added as a
  Linux dependency. A plain `pip install` no longer needs a C compiler.

## [0.4.0] - 2026-06-26

### Added
- Browser tier (Tier 1): an MV3 browser extension bridged to cua over native
  messaging. Acts DOM-first (content scripts + `chrome.tabs`/`chrome.cookies`)
  and escalates to a `chrome.debugger` CDP path only when a DOM op cannot
  complete. Drives the user's own logged-in browser by selector.
- `browser` MCP tool (Tier ONE, MEDIUM) op-routed via `OperationGroup`:
  `navigate`/`back`/`forward`/`reload`, `wait_for`, `query`, `read_text`,
  `get_attribute`, `click`, `type`/`fill`, `select`, `scroll`, `cookies`,
  `press`, `accessibility_tree`, and the `status` pre-flight op.
- `browser_eval` MCP tool (Tier ONE, HIGH): arbitrary JS in the page, kept
  separate so the common ops keep a lower risk ceiling.
- Actionability gate (Playwright-style): before a mutating op (`type`/`fill`/
  `click`/`select`) the target is checked for visible / receives-events
  (hit-test) / enabled; a non-actionable target raises `op_failed` instead of
  silently acting on a hidden or wrong node. A `force` param bypasses the gate.
- Self-verifying ops: `type`/`fill` return `{value, ok}`, `select` returns
  `{selected, value, ok}`, and a checkbox `click` returns `{checked}`, so the
  agent confirms an action landed from structured read-back rather than a
  screenshot; the tool descriptions instruct it to. contenteditable editors are
  filled via `execCommand('insertText')`, the only path that drives rich-editor
  state.
- Executor seam + escalation: a DOM executor and a `chrome.debugger` CDP
  executor behind one interface; on a DOM `ok:false` the escalation policy
  retries over CDP (trusted `Input` events, `Accessibility.getFullAXTree`).
- Availability & failure model: a typed `BrowserError` taxonomy (`not_set_up`,
  `not_connected`, `op_unsupported`, `proto_mismatch`, `waking`, `op_failed`)
  mapped to a `ToolError` carrying remediation + a guided pixel fallback;
  `status` reports `{connected, browsers, setup, reason}`.
- `computer_use.browser`: the wire protocol (`PROTOCOL_VERSION=1`, the `hello`
  handshake, `supported_ops`), `BrowserBridge` (`NativeMessagingBridge` +
  `FakeBridge`, session registry, per-OS manifest probe), and the native-host
  stdio framing.
- `computer_use.setup.extension_setup`: installs the `com.vadgr.cua.json`
  native-host manifest to the per-OS paths with `allowed_origins` pinned to the
  extension's stable dev ID. On WSL it also auto-places the Windows-side relay
  executable under `%LOCALAPPDATA%\vadgr-cua\`, so no manual copy step is needed.
- `extension/`: the MV3 extension (manifest with a pinned `key` for a stable
  unpacked ID, service worker, op router, content DOM ops, the ported native
  value-setter fill, the actionability checks, contenteditable fill, the
  executor/escalation seam and `chrome.debugger` CDP executor, Offscreen
  keep-alive), with vitest + happy-dom unit tests.

### Changed
- Tool catalog grows from 21 to 23 (adds the two Tier ONE browser tools).
  `tier_breakdown` now reports `{"0": 8, "0.5": 0, "1": 2, "2": 13}`.

### Notes
- cua and `extension/` are independent builds that share no imports - only the
  versioned wire protocol (`protocol.py` / `protocol.ts`).
- Validated end-to-end on real logged-in sites via the agent-driven runbook in
  `E2E/0.4.0/` (see its per-OS results table); the framing, routing, error
  model, DOM ops, fill, and actionability checks are also unit-tested headlessly.

## [0.3.1] - 2026-06-12

### Fixed
- `clipboard` copy op no longer hangs forever on Wayland. `wl-copy` forks a
  background daemon that keeps serving the clipboard and inherits the parent's
  captured stdout/stderr pipes, so the previous `subprocess.run(..., capture_output=True)`
  blocked on the pipe read indefinitely. The `wl-copy` backend now launches
  detached (stdout/stderr to `DEVNULL`, text written to stdin, bounded wait), so
  the call returns immediately while the daemon keeps the clipboard available for
  later pastes. (#11)
- All clipboard copy backends now run with a defensive timeout, so a stuck copy
  surfaces as an error instead of an indefinite hang.

## [0.3.0] - 2026-05-29

### Added
- 8 first-party Tier 0 MCP tools: `fs` (read/write/list/stat/delete), `shell` (run/which), `http` (get/post), `env` (get/set), `time` (now/sleep), `tempfile` (temp_path), `data` (parse/serialize JSON/CSV/YAML), `clipboard` (copy/paste). Each tool dispatches sub-operations via an `op` argument; signatures and risk levels documented in each tool's docstring.
- `computer_use.tools.system` package with one module per tool. Implementations live next to their tests; the MCP wire wrappers in `computer_use.mcp_server` apply `@mcp.tool()` + `@tool(...)` exactly like the existing pixel tools.
- Optional `data-yaml` extra: `pip install vadgr-computer-use[data-yaml]` enables YAML parsing/serialization. Without it, `data` raises a clear RuntimeError on `parse_yaml` / `serialize_yaml` and still serves the JSON/CSV ops.

### Changed
- Tool catalog grows from 13 to 21. `vadgr-cua doctor` JSON now reports `tool_count: 21` and `tier_breakdown: {"0": 8, "0.5": 0, "1": 0, "2": 13}`.
- `shell.run` defaults to `shell_mode=False` and treats a string `command` as a single argv element when the caller didn't ask for shell parsing. Set `shell_mode=True` explicitly to interpret a shell string.
- `time.sleep` is capped at 60 seconds and `shell.run` timeout is capped at 600 seconds so a stuck call cannot stall the MCP session.

### Notes
- `env.set` is process-scoped: the value is applied to `os.environ` of the running MCP server and is NOT persisted to the user's shell init.
- `clipboard` tries `clip.exe` + `powershell.exe Get-Clipboard` (Windows / WSL2), then `pbcopy` / `pbpaste` (macOS), then `wl-copy` / `wl-paste` (Wayland), then `xclip` (X11). When no backend is on PATH the tool raises a RuntimeError listing the supported backends instead of silently no-op'ing.
- The 13 existing pixel-layer tools (Tier 2) are unchanged; this release only adds new tools.

## [0.2.0] - 2026-05-21

### Added
- `computer_use.core` framework: `@tool(name, tier, risk)` decorator, `ToolRegistry` (import-time auto-registration, introspection by name / tier / risk, count, tier breakdown), `Tier` enum (ZERO, HALF, ONE, TWO) and `Risk` enum (READ_ONLY, LOW, MEDIUM, HIGH).
- `computer_use.core.middleware` chain: lightweight `MiddlewareChain` + `TelemetryMiddleware` for structured event emission around tool calls. No policy decisions; no filtering.
- `vadgr-cua doctor` JSON output gains `registry_loaded`, `tool_count`, `tier_breakdown` so users can verify the new registry loads correctly.

### Changed
- All 13 existing pixel-layer MCP tools (`screenshot`, `screenshot_region`, `click`, `double_click`, `right_click`, `move_mouse`, `drag`, `scroll`, `type_text`, `key_press`, `get_screen_size`, `get_platform`, `get_platform_info`) are now registered through `@tool` in addition to `@mcp.tool()`. Tier 2 for all; read-only risk for query tools, medium risk for input-mutating tools.

### Notes
- Pure refactor - no functional change to any MCP tool. The wire surface is identical to 0.1.5.
- Scope is strict: `vadgr-computer-use` drives the local machine, exposes `tier` + `risk` metadata, and emits telemetry. Authorization, denylist, log redaction, approval prompts, and auth-mode policy are not cua concerns and live in the host's agent loop.

## [0.1.5] - 2026-04-26

### Changed
- Default screenshot encoding switched from PNG to JPEG quality 70. On a real desktop this drops a single full-screen capture from ~564 KB to ~100 KB (5-6x). Aspect ratio and coordinate space are preserved across formats so existing click flows work unchanged.
- `screenshot()` and `screenshot_region()` accept a new `format` argument: `"jpeg"` (default), `"png"` (lossless, opt-in), or `"thumbnail"` (~640 px wide JPEG q40, ~15 KB for sanity-check shots in long flows).
- Tool description gains a sixth rule reminding agents that screenshots are point-in-time and older ones should not be referenced.

### Fixed
- Hard dimension ceiling at 1600 px applied inside `_downscale` so a single screenshot can no longer trip the Anthropic 2000 px many-image limit (anthropics/claude-code#37461, #46656). User-set `CU_MAX_WIDTH` below the ceiling still wins; values above it are clamped.
- `get_screen_size()` applies the same ceiling pre-screenshot so its answer matches what the first screenshot will actually return.

### Notes
- Combined with the smaller per-image bytes, the 32 MB request-size cap now fits ~320 default JPEG screenshots (vs ~58 with PNG) and ~2,200 thumbnails. The 2000 px dimension error is structurally eliminated for any vadgr-cua screenshot.

## [0.1.4] - 2026-04-26

### Added
- macOS backend is now functional out of the box. `pip install vadgr-computer-use` pulls `pyobjc-framework-Quartz` and `pyobjc-framework-ApplicationServices` on macOS (wheel install, no compilation, no Homebrew packages).
- `vadgr-cua doctor` now reports `macos_accessibility_granted`, `macos_screen_recording_granted`, and `python_executable` on macOS, so users can verify permission state without launching the agent.
- Backend construction proactively triggers the Accessibility and Screen Recording prompts on first run; macOS opens System Settings to the right pane instead of the user having to find it.
- Every macOS capture and input call now preflights its permission. If denied, System Settings is deep-linked to the matching Privacy pane via the `x-apple.systempreferences:` URL scheme and the call returns a structured error to the agent. This eliminates the silent wallpaper-only screenshot when Screen Recording is revoked, and the silent input no-op when Accessibility is revoked.
- `vadgr-cua setup` subcommand: fires the Accessibility and Screen Recording prompts on demand and prints permission state as JSON. Useful as a one-time post-install step so the user can grant permissions before wiring up an agent.

### Changed
- macOS screen capture switched from the `screencapture` subprocess to `mss` (already a runtime dep). `mss` returns logical-point images that share CGEvent's coordinate space, eliminating the Retina coordinate mismatch that was causing 2x click offsets.
- macOS input switched from optional `cliclick` plus AppleScript fallbacks to `Quartz.CGEvent*` APIs (mouse, keyboard, scroll, drag). `cliclick` is no longer used.
- macOS `type_text` uses `CGEventKeyboardSetUnicodeString`, so any Unicode character types correctly without per-layout keymap maintenance.
- macOS executor now plugs into `core.smooth_move` (WindMouse + Fitts) like the Linux and Windows executors.

### Fixed
- macOS `key_press` correctly handles chords with multiple non-modifier keys (e.g. `ctrl+shift+t`) instead of silently dropping the second non-modifier.
- macOS `scroll` now uses `CGEventCreateScrollWheelEvent` with line units instead of an AppleScript form that did not actually scroll.
- macOS `drag` no longer collapses to a single click in the absence of `cliclick`; it emits the full down/dragged/up event sequence.
- macOS `get_screen_size` and `get_scale_factor` no longer shell out to a system `python3` that lacks `AppKit`; they read directly from Quartz.

## [0.1.3] - 2026-04-25

### Fixed
- `import computer_use.mcp_server` no longer fails on native Windows. The bridge supervisor (which imports `fcntl`) is now loaded lazily inside the daemon CLI subcommands, so the stdio MCP server starts cleanly on Windows where `fcntl` does not exist.

### Changed
- Test suite is green on native Windows: WSL2/Linux/daemon-only test files (`test_supervisor.py`, `test_wsl2.py`) skip cleanly on `win32`, and the two D-Bus tests in `test_linux.py` use `pytest.importorskip("jeepney")`.

## [0.1.2] - 2026-04-24

### Fixed
- `pip install vadgr-computer-use` now works on Linux Wayland (including GNOME) with no manual setup. `jeepney` and `evdev` are added to runtime dependencies under `sys_platform == 'linux'`; both are pure-Python or shipped as wheels, so there is no `libdbus-1-dev` or compilation step.
- Mutter RemoteDesktop client switched from `dbus-python` to `jeepney` (no C build at install time).
- Engine error on backend unavailability now names the missing component and the exact remediation, instead of a generic "required system tools" message.
- Unit tests no longer move the real cursor or keyboard. A new `conftest.py` blocks platform side-effects during `pytest`.

### Added
- `availability_report()` on `PlatformBackend` returning `(available, missing, remediation)`. `is_available()` is kept as a thin wrapper for back-compat.
- Optional `linux-atspi` extra (`pip install vadgr-computer-use[linux-atspi]`) for AT-SPI2 foreground-window detection on Wayland. Pulls `PyGObject` only if requested.
- README rows for Linux / Wayland (GNOME) and Linux / Wayland (Sway, Hyprland, wlroots) with concrete install notes per row.

### Changed
- Linux backend probes screen capture at construct time, so a missing `grim` fails with a clear message instead of on the first `screenshot()` call.

## [0.1.1] - 2026-04-22

### Removed
- Autonomous mode and the entire `computer_use/providers/` package (Anthropic, OpenAI, base, registry). The package is now MCP-only; external clients (Claude Code, Codex CLI, Gemini CLI) drive the server over stdio.
- `ComputerUseEngine.run_task()` and the associated in-process agent loop.

### Changed
- README rewritten: new "Try it" section with runnable one-shot commands per client (Claude interactive + headless, Codex exec, Gemini with explicit `-m gemini-3.1-pro-preview`); real YouTube Music Codex transcript replaces the trivial `get_platform_info` demo.
- Platform support table reordered (Linux, Windows, WSL2, macOS) and macOS flagged as WIP up front.
- Collapsed "How it works" to its tagline; removed duplicate WSL2 auto-launch notes.

### Fixed
- Environment table: `VADGR_DATA` was documented but never existed; removed. `VADGR_DEBUG` now correctly documents the actual debug-screenshot path (`<package>/.debug/`).

### Added
- `.env` and `.env.*` to `.gitignore`.

## [0.1.0] - 2026-04-21

- Initial public release.
- 13 MCP tools: `screenshot`, `screenshot_region`, `click`, `double_click`, `right_click`, `move_mouse`, `drag`, `scroll`, `type_text`, `key_press`, `get_platform`, `get_platform_info`, `get_screen_size`.
- WSL2 bridge daemon with auto-launch, self-heal, and management CLI (`vadgr-cua doctor|install-daemon|stop-daemon|restart-daemon`).
- Backends: `mss` + `xdotool` (Linux/X11), Win32 GDI + SendInput (Windows), TCP bridge to Windows host (WSL2). macOS backend is present but not functional.
