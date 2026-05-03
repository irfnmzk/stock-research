"""Agent — Anthropic SDK conversation loop with tool dispatch.

Two entry points:
- generate_eod_brief(cfg) — single Claude call, no tools, returns brief text
- run_conversation(cfg, user_message, session_id) — multi-turn with tools
"""

import json
import logging
import os
from pathlib import Path

import anthropic
from dotenv import load_dotenv
load_dotenv()

from context import build_context
from db import get_db
from memory import save_turn, get_turns, start_session, save_session_summary
from system_prompt import build_eod_prompt, build_chat_prompt
from tools import TOOL_DEFINITIONS, handle_tool

SCRIPT_DIR = Path(__file__).resolve().parent
log = logging.getLogger(__name__)


def _extract_text(content):
    """Extract text from response content blocks, skipping ThinkingBlocks."""
    return "".join(block.text for block in content if hasattr(block, "text"))


def _get_client():
    base_url = os.environ.get("ANTHROPIC_BASE_URL")
    if base_url:
        return anthropic.Anthropic(base_url=base_url)
    return anthropic.Anthropic()


def generate_eod_brief(cfg):
    """Generate EOD brief — single Claude call, all data in context."""
    client = _get_client()
    agent_cfg = cfg.get("agent", {})
    model = agent_cfg.get("model", "claude-sonnet-4-20250514")
    max_tokens = agent_cfg.get("max_tokens", 4096)

    context = build_context(cfg)
    system = build_eod_prompt(context)

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": "Generate today's end-of-day brief."}],
    )

    return _extract_text(response.content)


def run_conversation(cfg, user_message, session_id=None):
    """Run a multi-turn conversation with tool dispatch.

    Returns (response_text, chart_paths, session_id).
    chart_paths is a list of PNG paths generated during the conversation.
    """
    client = _get_client()
    agent_cfg = cfg.get("agent", {})
    model = agent_cfg.get("model", "claude-sonnet-4-20250514")
    max_tokens = agent_cfg.get("max_tokens", 4096)
    max_turns = agent_cfg.get("max_turns", 15)

    db = get_db(cfg)

    if session_id is None:
        session_id = start_session(db)

    # Load conversation history
    history = get_turns(db, session_id)
    messages = [{"role": t["role"], "content": t["content"]} for t in history]

    # Add new user message
    messages.append({"role": "user", "content": user_message})
    save_turn(db, session_id, "user", user_message)

    context = build_context(cfg)
    system = build_chat_prompt(context)

    chart_paths = []
    turns = 0

    while turns < max_turns:
        turns += 1
        log.info("turn %d/%d", turns, max_turns)

        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            tools=TOOL_DEFINITIONS,
        )

        if response.stop_reason == "tool_use":
            # Process tool calls — filter out thinking blocks before appending to history
            assistant_content = [b for b in response.content if b.type != "thinking"]
            messages.append({"role": "assistant", "content": assistant_content})

            tool_results = []
            for block in assistant_content:
                if block.type == "tool_use":
                    log.info("tool_call: %s(%s)", block.name, json.dumps(block.input, ensure_ascii=False)[:200])
                    result = handle_tool(cfg, block.name, block.input)

                    # Check for chart image
                    if isinstance(result, str) and result.startswith("__CHART__:"):
                        path = result.split(":", 1)[1]
                        chart_paths.append(path)
                        result = f"Chart generated: {Path(path).name}"

                    log.info("tool_result: %s → %s", block.name, str(result)[:300])

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            messages.append({"role": "user", "content": tool_results})

        else:
            text = _extract_text(response.content)

            save_turn(db, session_id, "assistant", text)
            db.close()
            return text, chart_paths, session_id

    # Max turns reached
    text = "I've reached the maximum number of steps for this conversation. Please start a new message."
    save_turn(db, session_id, "assistant", text)
    db.close()
    return text, chart_paths, session_id


def close_session(cfg, session_id):
    """Generate a session summary and save it."""
    client = _get_client()
    db = get_db(cfg)

    turns = get_turns(db, session_id)
    if not turns:
        db.close()
        return

    # Build a compact transcript
    transcript = "\n".join(f"{t['role']}: {t['content'][:200]}" for t in turns)

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": f"Summarize this trading research conversation in 1-2 sentences. Focus on what stocks were discussed and any decisions made.\n\n{transcript}",
        }],
    )

    summary = _extract_text(response.content)
    save_session_summary(db, session_id, summary)
    db.close()


if __name__ == "__main__":
    import argparse
    import yaml

    parser = argparse.ArgumentParser()
    parser.add_argument("--test-brief", action="store_true", help="Generate EOD brief")
    parser.add_argument("--test-chat", type=str, help="Test chat with a message")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    cfg = yaml.safe_load(open(SCRIPT_DIR / args.config))

    if args.test_brief:
        print(generate_eod_brief(cfg))
    elif args.test_chat:
        text, charts, sid = run_conversation(cfg, args.test_chat)
        print(text)
        if charts:
            print(f"\nCharts: {charts}")
        print(f"\nSession ID: {sid}")
