"""Entry point — Claude Agent SDK driving the SSS tools.

Model brain is swappable through ANTHROPIC_BASE_URL → claude-code-router
(see agent/router/) which forwards to DeepSeek's OpenAI-compatible API.
Run `ccr code`-style router first, then:

    AK_BACKEND=auto ANTHROPIC_BASE_URL=http://127.0.0.1:3456 \
    ANTHROPIC_AUTH_TOKEN=ak-router AK_MODEL=deepseek-chat \
    python -m ak_agent.main
"""
from __future__ import annotations

import asyncio

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions,
                              ClaudeSDKClient, ResultMessage, TextBlock,
                              ToolUseBlock)

from .config import load
from .executors import build
from .prompts import SYSTEM_PROMPT
from .tools import TOOL_NAMES, build_server

KICKOFF = (
    "We are entering a 卫戍协议 (AutoChess) run. First call get_game_state to see "
    "where we are; if not in battle take a screenshot and navigate/confirm until "
    "the run starts. Then play to win: manage shop economy in PREPARE, deploy and "
    "hold lanes in BATTLE, kill the boss in BOSS_BATTLE. Keep going until SETTLE "
    "or END, then summarize the run."
)


async def amain() -> None:
    cfg = load()
    executor = build(cfg)
    print(f"[agent] executor backend: {executor.name}")

    env = {}
    if cfg.anthropic_base_url:
        env["ANTHROPIC_BASE_URL"] = cfg.anthropic_base_url
    if cfg.anthropic_auth_token:
        env["ANTHROPIC_AUTH_TOKEN"] = cfg.anthropic_auth_token

    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={"arknights": build_server(executor)},
        allowed_tools=TOOL_NAMES + ["Read", "Write", "Bash"],
        model=cfg.model,
        env=env,
        max_turns=400,
        permission_mode="bypassPermissions",
    )

    async with ClaudeSDKClient(options=options) as client:
        await client.query(KICKOFF)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for blk in msg.content:
                    if isinstance(blk, TextBlock):
                        print(f"\n=== {blk.text}")
                    elif isinstance(blk, ToolUseBlock):
                        print(f"  → {blk.name} {blk.input}")
            elif isinstance(msg, ResultMessage):
                print(f"\n[done] turns={msg.num_turns} cost=${msg.total_cost_usd}")


if __name__ == "__main__":
    asyncio.run(amain())
