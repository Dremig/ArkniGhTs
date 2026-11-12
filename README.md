# ArkniGhTs

Claude Agent SDK harness for Arknights' **Stronghold Protocol / 卫戍协议**
(`AutoChess`) mode. The default runtime reads structured state from the live
game process and exposes semantic MCP tools. The model does not receive
screenshots, raw coordinates, a shell, or fabricated game state.

## Is it portable?

The host-side Python and ADB orchestration are designed to run on macOS, Linux,
and Windows. The complete stack is **not universal or plug-and-play** because
the device-side runtime is build-, ABI-, root-, and resolution-specific.

| Component | Current support |
|---|---|
| macOS host | End-to-end tested |
| Linux host | Expected to work; not yet end-to-end tested |
| Windows host | Expected to work through Python and `adb.exe`; not yet end-to-end tested |
| Android device ABI | `arm64-v8a` only with the included helper |
| Android access | `adb root` must produce a uid-0 shell |
| Game client | Bilibili package `com.hypergryph.arknights.bilibili`, pinned layout/build |
| Display/input | Verified 2280×1080 landscape calibration |

Other game versions may change IL2CPP layouts. Other display sizes require a
new calibration and gesture verification. An x86/x86_64 or 32-bit Android
target needs a separately built helper and a separately validated reader.

## Quick Start

For an agent performing setup, use the stricter
[Quick Start for an Agent](quickstart4agent.md) runbook. It includes compatibility
gates, phase-aware validation, and failure handling.

Prerequisites:

- Python 3.10–3.13 (3.13 tested and recommended; 3.14 is not supported by the current dependency stack)
- Android SDK platform-tools (`adb` / `adb.exe`)
- one online rooted Android `arm64-v8a` emulator/device
- the supported game client already installed, fully downloaded, and logged in
- Claude Code login **or** an Anthropic-compatible model endpoint

Clone the repository:

### macOS / Linux

```bash
git clone https://github.com/Dremig/ArkniGhTs.git
cd ArkniGhTs
```

### Windows PowerShell

```powershell
git clone https://github.com/Dremig/ArkniGhTs.git
Set-Location ArkniGhTs
```

Then follow [quickstart4agent.md](quickstart4agent.md) with Python 3.13. The
runbook creates `.venv`, validates ADB/device compatibility, installs the helper,
checks real process-memory state, and launches the agent. It does not install
the game, download resources, start a match, or store model credentials.

After setup, start the game and enter an AutoChess strategy or battle phase.

For an existing Claude Code login, the equivalent POSIX command is:

```bash
AK_BACKEND=mread AK_MODEL=sonnet .venv/bin/python -m ak_agent.main
```

PowerShell:

```powershell
$env:AK_BACKEND='mread'
$env:AK_MODEL='sonnet'
& .\.venv\Scripts\python.exe -m ak_agent.main
```

With DeepSeek through Claude Code Router, use Node.js 22+ and configure the
provider through CCR's management UI:

```bash
npm install -g @musistudio/claude-code-router
ccr ui
```

In the UI, add DeepSeek and a model, create a CCR client key, and start the
gateway. The default gateway address is `http://127.0.0.1:3456`. Then keep both
keys in your shell and run:

```bash
export DEEPSEEK_API_KEY='set-this-in-your-shell'
export ANTHROPIC_BASE_URL='http://127.0.0.1:3456'
export ANTHROPIC_AUTH_TOKEN='your-ccr-client-key'
export AK_MODEL='deepseek-chat'
.venv/bin/python -m ak_agent.main
```

Never commit provider credentials.

## Runtime behavior

The default `mread` backend:

1. discovers the single connected Android device and current game PID;
2. restores ADB root, port forwarding, and the device helper;
3. reads phase, players, shop, hand, board, equipment, valid grids, enemies,
   team strategy state, and terminal settlement state;
4. converts semantic actions into calibrated game input; and
5. reports success only when a fresh memory snapshot confirms the expected
   change.

The agent exposes only its semantic MCP tools. Claude Agent SDK is configured
with built-in tools disabled and strict MCP configuration enabled.

The Frida backend is retained for research but is never selected automatically.
This client terminates when Frida attaches, so it is unsuitable for normal runs.

## Configuration

| Variable | Purpose |
|---|---|
| `AK_BACKEND` | `mread` by default; `adb` and `frida` are explicit alternatives |
| `AK_ADB` | path to `adb` or `adb.exe` |
| `AK_ADB_SERIAL` | required when more than one device is online |
| `AK_CALIB` | alternate verified calibration JSON |
| `AK_STATE_READER` | alternate `state_reader.py` path |
| `AK_MREAD_PORT` | helper forwarding port; defaults to `27047` |
| `AK_MODEL` | Claude Code alias or router model ID |

## Verification

Read-only checks against a real active battle:

### macOS / Linux

```bash
AK_LIVE_TEST=1 .venv/bin/python -m unittest -v tests.test_live_runtime
```

### Windows PowerShell

```powershell
$env:AK_LIVE_TEST='1'
& .\.venv\Scripts\python.exe -m unittest -v tests.test_live_runtime
```

The tests require a live process and deliberately provide no fixture or fallback
state.

## Repository layout

| Path | Purpose |
|---|---|
| `ak_agent/main.py` | Claude Agent SDK loop with strict MCP-only tools |
| `ak_agent/tools.py` | structured reads and semantic game actions |
| `ak_agent/executors/mread_exec.py` | runtime lifecycle, dispatch, and post-action verification |
| `runtime/mread/` | arm64 helper and live state reader |
| `calib/` | resolution-specific board/UI calibration |
| `quickstart4agent.md` | cross-platform setup and validation runbook for an agent |
| `tests/test_live_runtime.py` | read-only live integration checks |
| `router/` | archived legacy router reference; current CCR uses its management UI |

## Known limits

Normal shop, board, equipment, withdrawal, sale, ready, team, and terminal paths
have live verification. Magic use and special choose-one prompts still need an
actual occurrence in a live run before their calibrations can be marked verified.

## License

GNU General Public License v3.0 only (`GPL-3.0-only`). See [LICENSE](LICENSE).
