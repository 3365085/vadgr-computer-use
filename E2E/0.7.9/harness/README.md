# 0.7.9 evidence helpers

`harness.py` prepares an isolated root, reports the written cell inventory,
checks prerequisites, hashes evidence and plans recoverable cleanup.
Its `mcp-config` command writes a local Claude configuration and prints the
equivalent Codex argument array. It isolates the product child's home and state,
and does not copy the subscription driver's credentials or start either driver.
Neither helper drives a browser, chooses tool calls or supplies a live verdict.
Run the installed `vadgr-cua` entry point through the selected subscription CLI.

These committed helpers travel with the runbook so commands work on every host.
Start with `python E2E/0.7.9/harness/harness.py --help`.
No helper can produce a synthetic signed fixture or mark an unobserved cell pass.

`windows_fixture.py` reuses the 0.7.8 released-wheel fixture, including its
marker, hash checks and safe process cleanup, and adds the exact released 0.7.8
wheel as a predecessor. Its child roots still use the `vadgr-cua-078-` prefix
inside the enclosing marked 0.7.9 root. It prepares and observes fixtures;
it does not drive a product operation. `process_fixture.html` exposes an input
counter for an independent no-replay DOM observation.

`windows_boundary.py` records retained artifact hashes, native-host registration
hashes and read-only process/port cleanup observations for explicitly supplied
test PIDs and port. It never stops a process, modifies registration or assigns
a live-cell verdict.

`windows_session.py` captures native Windows sessions through the installed
public entry point. It prepares isolated configuration, launches only the
reviewed official Chrome-for-Testing executable with a fresh profile, and gives
a goal to the selected live subscription driver. Its Claude browser driver
disables built-in tools and exposes only `browser`, `browser_eval`, and `tabs`
from the isolated CUA MCP; every desktop, pixel, native-accessibility, shell and
filesystem CUA tool is explicitly denied. Prompt text alone is not a tier
boundary. Child environment allowlists keep ambient provider credentials out of
the product and browser; the driver's login paths remain separate. Its
read-only observer records process identities and the isolated fixture's DOM
counter. Cleanup checks PID plus exact process start identity. Registration
backup contents are private local state and must never be committed.

`agent-start`, `agent-observe`, and `agent-stop` support a real live-driver
cancellation boundary: record the active driver and descendants, terminate only
the exact recorded driver, then independently observe child exit. A stop request
is not an exit verdict. The fixture server is separately owned and must be
stopped by its recorded PID/start identity during final cleanup.

`seal_session.py` retains only observed CUA tool calls and matching results from
the subscription stream. It uses the established stream redactor for typed
values, arbitrary text, secrets, expressions and media, and additionally removes
private roots and owner identities. Counts do not assign a cell verdict. Review
the original local observations before sealing, run the required secret scan,
and preserve every failed or ineligible attempt in a separate boundary.

The Windows fixture's `pid-reuse` fault changes only a copied isolated endpoint's
creation identity, preserving its exact original bytes in a private local
backup. `pid-restore` restores those bytes. Neither action touches an owner
endpoint or terminates a process.

`gnome_cells.py` drives the R04 to R09 cells on a real GNOME Wayland desktop.
Run it with the installed entry point's own interpreter so the installed build
is the code under test. It calls the server's MCP tool layer in process, or the
vadgr GNOME Shell extension over the session bus, and prints JSON lines; it
assigns no verdict. `box`, `fallback` and `flash --portal-control` take one
portal screenshot each, which flashes the screen, and `fallback` disables and
re-enables the extension. `key_fixture.py` is a disposable GTK 4 window, run
with the system Python, that records the keys it receives for R06.
