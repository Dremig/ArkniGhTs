# Quick Start for an Agent

This runbook is written for a coding agent preparing ArkniGhTs on a user's
machine. Follow it literally. Do not replace failed checks with fabricated
state, screenshots, or guessed coordinates.

## 1. Compatibility gate

Proceed only when all conditions are true:

- Host Python is 3.10–3.13. Python 3.13 is the tested recommendation.
- Android SDK platform-tools are installed (`adb` on macOS/Linux, `adb.exe` on
  Windows).
- Exactly one Android device is online, or the user supplied its serial.
- The Android ABI is `arm64-v8a`.
- `adb shell` can become uid 0 through `adb root`.
- The Bilibili client package is `com.hypergryph.arknights.bilibili` and matches
  the reader's pinned game layout (the verified client was 2.7.91).
- The calibrated display is 2280×1080 landscape. `wm size` may report the same
  physical dimensions as 1080×2280 before rotation.

Stop and explain the mismatch if any gate fails. Linux and Windows host code is
intended to be portable, but only macOS has been tested end to end. Do not claim
that a different Android ABI, game update, root method, or resolution is
supported merely because installation succeeds.

## 2. Locate ADB and the device

POSIX:

```bash
ADB="${AK_ADB:-adb}"
"$ADB" devices -l
```

PowerShell:

```powershell
$Adb = if ($env:AK_ADB) { $env:AK_ADB } else { 'adb.exe' }
& $Adb devices -l
```

Select the intended serial from `adb devices`, then set it for the rest of the
runbook even when only one device is online:

```bash
export AK_ADB_SERIAL=SERIAL
```

```powershell
$env:AK_ADB_SERIAL='SERIAL'
```

Validate the device:

```bash
"$ADB" -s "$AK_ADB_SERIAL" shell getprop ro.product.cpu.abi
"$ADB" -s "$AK_ADB_SERIAL" shell wm size
"$ADB" -s "$AK_ADB_SERIAL" shell pm path com.hypergryph.arknights.bilibili
"$ADB" -s "$AK_ADB_SERIAL" root
"$ADB" -s "$AK_ADB_SERIAL" wait-for-device
"$ADB" -s "$AK_ADB_SERIAL" shell id -u
```

Expected results are `arm64-v8a`, dimensions matching 2280×1080, a package
path, and uid `0`. `adb root` support is required; an interactive `su` prompt
alone is not enough for the default runtime.

## 3. Create the Python environment

macOS/Linux:

```bash
python3.13 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

Windows PowerShell:

```powershell
py -3.13 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The normal runtime does not require Frida. Install
`requirements-frida.txt` only when explicitly working on the experimental
backend. Never select Frida automatically: this client terminates when Frida
attaches.

## 4. Install the device helper

The included helper is a static Android arm64 executable:

```bash
"$ADB" -s "$AK_ADB_SERIAL" push runtime/mread/mread /data/local/tmp/mread
"$ADB" -s "$AK_ADB_SERIAL" shell chmod 755 /data/local/tmp/mread
"$ADB" -s "$AK_ADB_SERIAL" forward tcp:27047 tcp:27047
```

PowerShell uses the same ADB arguments:

```powershell
& $Adb -s $env:AK_ADB_SERIAL push runtime/mread/mread /data/local/tmp/mread
& $Adb -s $env:AK_ADB_SERIAL shell chmod 755 /data/local/tmp/mread
& $Adb -s $env:AK_ADB_SERIAL forward tcp:27047 tcp:27047
```

The executor will restart the helper and rebind it to the current game PID when
the game or computer restarts.

## 5. Configure a model

For an existing Claude Code login, use `AK_MODEL=sonnet` and leave
`ANTHROPIC_BASE_URL` unset.

For DeepSeek, install the current Claude Code Router CLI with Node.js 22+:

```bash
npm install -g @musistudio/claude-code-router
ccr ui
```

In CCR's management UI:

1. Add the DeepSeek provider and model.
2. Create a CCR client key.
3. Start the model gateway (default `http://127.0.0.1:3456`).

Keep the DeepSeek key and CCR client key in the user's shell or secret manager.
Never write either value into this repository.

## 6. Start the real runtime

The user must start the game, log in, and enter an AutoChess strategy, battle,
or settlement page. Then run a read-only preflight.

POSIX:

```bash
export AK_BACKEND=mread
export AK_ADB=/absolute/path/to/adb
export AK_ADB_SERIAL=SERIAL
.venv/bin/python runtime/mread/state_reader.py --pretty
```

PowerShell:

```powershell
$env:AK_BACKEND='mread'
$env:AK_ADB='C:\path\to\adb.exe'
$env:AK_ADB_SERIAL='SERIAL'
& .\.venv\Scripts\python.exe runtime\mread\state_reader.py --pretty
```

Do not continue unless the JSON reports:

```json
{"available": true, "live": true, "source": "process_memory"}
```

The remaining fields depend on the current phase. A settlement snapshot is
valid even though its `unavailable` list says that the battle object graph was
disposed.

## 7. Verify and launch

Run the read-only integration checks:

```bash
AK_LIVE_TEST=1 .venv/bin/python -m unittest -v tests.test_live_runtime
```

PowerShell:

```powershell
$env:AK_LIVE_TEST='1'
& .\.venv\Scripts\python.exe -m unittest -v tests.test_live_runtime
```

Launch with Claude Code:

```bash
AK_MODEL=sonnet .venv/bin/python -m ak_agent.main
```

Launch through CCR/DeepSeek:

```bash
export ANTHROPIC_BASE_URL='http://127.0.0.1:3456'
export ANTHROPIC_AUTH_TOKEN='CCR_CLIENT_KEY'
export AK_MODEL='deepseek-chat'
.venv/bin/python -m ak_agent.main
```

The default agent tool list is semantic MCP only. It disables built-in shell and
file tools and does not expose screenshot, raw tap, or arbitrary request tools.

## Troubleshooting

| Symptom | Meaning / action |
|---|---|
| Python/dependency installation fails | Recreate `.venv` with Python 3.13; do not reuse a 3.14 environment. |
| `adb root` fails | The default live backend is unsupported on this device. |
| ABI is not `arm64-v8a` | The included helper and 64-bit layout cannot be used. |
| Calibration size mismatch | Supply a separately verified `AK_CALIB`; do not scale coordinates blindly. |
| `state reader not found` | Confirm `runtime/mread/state_reader.py` exists or set `AK_STATE_READER`. |
| `expected one ... pid` | Start the game, or set the intended device serial. |
| TypeInfo/class validation fails after an update | The game build/layout changed; stop rather than guessing offsets. |
| Snapshot is temporarily unavailable during phase change | Re-read; the reader discards mixed old/new object graphs. |
| Frida attach terminates the client | Expected in this environment; use `AK_BACKEND=mread`. |

## Current unverified branches

Normal shop, board, equipment, withdrawal, sale, ready, team, and terminal paths
have live verification. Magic use and special choose-one prompts still require a
real occurrence before their calibrations can be marked verified. An agent must
return an error for an unverified action rather than claim success.
