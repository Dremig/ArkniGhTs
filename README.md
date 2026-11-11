# ArkniGhTs

An LLM-driven agent that plays Arknights' **Stronghold Protocol / 卫戍协议** (SSS) mode
via tool calls — no human in the loop during a match.

```
LLM (DeepSeek / any OpenAI-compatible model)
  → claude-code-router        (Anthropic API ⇄ OpenAI API translation)
  → claude-agent-sdk          (agent harness / tool-call loop)
  → MCP tools                 (semantic game actions)
  → executor backend
      ├─ frida : in-process method calls on a rooted device/emulator
      └─ adb   : input tap/swipe + screenshots on any device
```

## Layout

| path | what |
|---|---|
| `ak_agent/main.py` | agent entry: ClaudeSDKClient + kickoff prompt |
| `ak_agent/tools.py` | 20 MCP tools (state read, board ops, shop ops, UI taps) |
| `ak_agent/model.py` | game-state dataclasses + enums (OpType, Direction, GameState...) |
| `ak_agent/grid.py` | board grid ↔ screen-pixel mapping (4-corner bilinear calibration) |
| `ak_agent/executors/` | `frida_exec.py` / `adb_exec.py` behind one `OpExecutor` interface |
| `ak_agent/prompts.py` | system prompt incl. latency discipline for timed phases |
| `frida/agent.ts` | frida-il2cpp-bridge script (bundled to `frida/dist/agent.js`) |
| `router/` | claude-code-router config (DeepSeek + OpenRouter providers) |
| `calib/` | board pixel calibration files per resolution |

## Tool surface (MCP)

- **Read**: `get_game_state` (phase, hp/coins, board+hand pieces, shop goods), `get_hand`, `screenshot`
- **Board ops**: `deploy_chess`, `move_chess`, `withdraw_chess`, `equip_item`, `replace_equip`, `cast_magic`, `sell_or_destroy`
- **Shop / meta**: `shop_buy`, `shop_refresh`, `shop_freeze`, `shop_upgrade`, `confirm_ready`, `pick_slot`, `choose_strategy`, `send_game_request`
- **UI fallback**: `tap_ui` (calibrated names), `tap_raw` (pixels)

## Setup

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cd frida && npm install && npm run build   # produces dist/agent.js
```

Run a router so the SDK can talk to DeepSeek:

```bash
npm i -g @musistudio/claude-code-router
export DEEPSEEK_API_KEY=sk-...
ccr start -c router/claude-code-router.config.json
```

Then:

```bash
AK_BACKEND=auto \                 # auto | frida | adb
AK_ADB_SERIAL=emulator-5554 \
AK_FRIDA=usb \                    # or host:port for remote frida-server
AK_FRIDA_TARGET=com.hypergryph.arknights.bilibili \
ANTHROPIC_BASE_URL=http://127.0.0.1:3456 \
ANTHROPIC_AUTH_TOKEN=ak-router \
AK_MODEL=deepseek-chat \
python -m ak_agent.main
```

## Notes

- **frida backend** reads structured state (board pieces, shop goods, player
  stats) straight from the game process and performs ops by calling the same
  methods the UI calls — no screen recognition needed.
- **adb backend** needs `calib/board_default.json` filled with real pixel
  coordinates for the target resolution; `tap_ui`/`tap_raw` always work.
- Decision phases are timed (~30s countdowns). Use a fast model for `default`
  routing; heavier reasoning models belong on the `think` route only.
