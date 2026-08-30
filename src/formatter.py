"""Render judgment JSON into X-safe tweet text (≤280 chars)."""

from __future__ import annotations

from typing import Any

from .judge import AXES

LIMIT = 270  # margin below X's 280-character cap
HASHTAG = "\n#ハムジャッジ"


def _clip(s: str, n: int) -> str:
    s = s.strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _score_lines(participants: list[dict[str, Any]], detailed: bool) -> list[str]:
    lines = []
    for p in participants:
        handle = p["handle"].lstrip("@")
        lines.append(f"{handle} {p['total']}点")
        if detailed:
            sc = p.get("scores", {})
            parts = [f"{ja}{sc.get(k, 0)}" for k, ja in AXES]
            lines.append("　" + "/".join(parts))
    return lines


def format_verdict(result: dict[str, Any]) -> str:
    parts = sorted(result.get("participants", []), key=lambda p: -p.get("total", 0))[:4]
    topic = _clip(result.get("topic", ""), 30)
    winner = result.get("winner", "引き分け")
    winner_label = winner if winner in ("引き分け", "") else winner.lstrip("@")

    header = f"⚖️ レスバ判定\n論点: {topic}\n"
    footer = f"\n🏆 勝者: {winner_label}"

    for detailed in (True, False):
        body = "\n".join(_score_lines(parts, detailed))
        text = header + body + footer
        if len(text) <= LIMIT:
            break
    else:
        text = _clip(text, LIMIT)

    return _clip(text, LIMIT - len(HASHTAG)) + HASHTAG


def format_skip(reason_text: str) -> str:
    return _clip(f"⚖️ 判定できませんでした。\n{reason_text}", LIMIT - len(HASHTAG)) + HASHTAG
