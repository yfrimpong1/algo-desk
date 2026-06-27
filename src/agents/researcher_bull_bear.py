"""Bull vs. Bear researchers — a short structured debate over the analyst signals.

Why debate at all? A single "weigh the evidence" prompt tends to anchor on the loudest
signal and skip the counter-case. Splitting into an adversarial bull and bear forces both
sides of the trade onto the table, so the portfolio manager (the judge, Phase 5) decides
with the strongest version of each argument in front of it.

The researchers reason over the SIGNALS the analysts produced — they have no data tools,
so they cannot wander; they must argue from the evidence on the table. Output is plain
text (the cases); the PM turns it into a decision.
"""

from __future__ import annotations

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    TextBlock,
    query,
)

from src.schemas import Signal


def format_signals(signals: list[Signal]) -> str:
    return "\n".join(
        f"- {s.analyst} analyst: {s.direction.value.upper()} "
        f"(confidence {s.confidence:.2f}) — {s.rationale}"
        for s in signals
    )


async def _run_text_agent(system_prompt: str, prompt: str, model: str = "claude-sonnet-4-6") -> str:
    """Run a tool-less agent and return its concatenated text."""
    options = ClaudeAgentOptions(system_prompt=system_prompt, model=model, allowed_tools=[])
    chunks: list[str] = []
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    chunks.append(block.text)
    return "".join(chunks).strip()


async def run_debate(symbol: str, signals: list[Signal], rounds: int = 1) -> dict:
    """Return {'bull': ..., 'bear': ...} — the strongest case for and against buying.

    rounds=1: bull makes its case, then bear rebuts seeing the bull case. rounds=2 adds a
    bull rebuttal of the bear and a final bear reply (more thorough, ~2x the cost).
    """
    evidence = format_signals(signals)

    bull = await _run_text_agent(
        system_prompt=(
            "You are the BULL researcher on a trading desk. Argue the strongest case to BUY/hold "
            f"{symbol}, grounded ONLY in the analyst evidence given. 3-5 sentences. Be persuasive "
            "but honest; acknowledge the biggest risk to your thesis."
        ),
        prompt=f"Analyst evidence for {symbol}:\n{evidence}\n\nMake the bull case.",
    )

    bear = await _run_text_agent(
        system_prompt=(
            "You are the BEAR researcher on a trading desk. Argue the strongest case to SELL/avoid "
            f"{symbol}, grounded ONLY in the analyst evidence given. Directly rebut the bull's "
            "points. 3-5 sentences."
        ),
        prompt=(
            f"Analyst evidence for {symbol}:\n{evidence}\n\n"
            f"The bull argued:\n{bull}\n\nMake the bear case and rebut the bull."
        ),
    )

    if rounds >= 2:
        bull2 = await _run_text_agent(
            system_prompt=f"You are the BULL researcher. Rebut the bear's case on {symbol} in 2-3 sentences.",
            prompt=f"Evidence:\n{evidence}\n\nBear said:\n{bear}\n\nRebut.",
        )
        bear2 = await _run_text_agent(
            system_prompt=f"You are the BEAR researcher. Final reply on {symbol} in 2-3 sentences.",
            prompt=f"Evidence:\n{evidence}\n\nBull's rebuttal:\n{bull2}\n\nFinal bear reply.",
        )
        bull = f"{bull}\n\n[Rebuttal] {bull2}"
        bear = f"{bear}\n\n[Final] {bear2}"

    return {"bull": bull, "bear": bear}
