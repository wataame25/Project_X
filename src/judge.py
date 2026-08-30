"""Argument-structure judging via Gemini."""

from __future__ import annotations

import json
import time
from typing import Any

import requests

TIMEOUT = 120
RETRY_DELAYS = [10, 20, 40]  # seconds between retries on 503/429
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

AXES = [
    ("evidence", "根拠"),
    ("focus", "論点"),
    ("responsiveness", "応答"),
    ("consistency", "一貫"),
    ("fallacy", "誤謬"),
]

SYSTEM_PROMPT = """あなたは議論の「論証構造」だけを評価する審判です。

## 採点軸（各0〜20点、合計100点）
- evidence(根拠): 主張に具体的な根拠・出典・事例を添えているか
- focus(論点): 当初の論点を維持しているか。論点のすり替えやゴールポストの移動がないか
- responsiveness(応答): 相手の問いや反論に正面から答えているか。無視・はぐらかしは減点
- consistency(一貫): 自分の発言間に矛盾がないか
- fallacy(誤謬回避): 藁人形論法・人身攻撃・論点先取などの誤謬や、罵倒・レッテル貼りがないか

## 厳守事項
1. どちらの「立場」が正しいかで採点しない。政治的・思想的な内容の是非は一切評価しない。
2. 事実の真偽判定はしない。「根拠を示したか」という形式面のみを見る。
3. 発言数の多さは加点理由にならない。
4. 参加者は最低2名。ハンドルは入力に出てきた文字列をそのまま使う。
5. winner には最高得点者のハンドルを入れる。同点なら "引き分け" とする。
6. 議論として成立していない場合（雑談、単なる同意、1人しか実質発言していない等）は valid を false にする。
7. reason は120文字以内の日本語。topic は30文字以内。comment は40文字以内。
"""

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "valid": {"type": "BOOLEAN"},
        "topic": {"type": "STRING"},
        "participants": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "handle": {"type": "STRING"},
                    "scores": {
                        "type": "OBJECT",
                        "properties": {k: {"type": "INTEGER"} for k, _ in AXES},
                        "required": [k for k, _ in AXES],
                    },
                    "total": {"type": "INTEGER"},
                    "comment": {"type": "STRING"},
                },
                "required": ["handle", "scores", "total", "comment"],
            },
        },
        "winner": {"type": "STRING"},
        "reason": {"type": "STRING"},
    },
    "required": ["valid", "topic", "participants", "winner", "reason"],
}


def build_transcript(chain: list[dict[str, Any]], bot_handle: str) -> str:
    lines = []
    for i, p in enumerate(chain, 1):
        if p["handle"] == bot_handle:
            continue
        text = p["text"].strip().replace("\n", " ")
        if len(text) > 500:
            text = text[:500] + "…"
        speaker = p.get("name") or p["handle"]
        lines.append(f"[{i}] {speaker}: {text}")
    return "\n".join(lines)


def judge(transcript: str, api_key: str, model: str = "gemini-3.6-flash") -> dict[str, Any]:
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [
            {
                "role": "user",
                "parts": [{"text": f"以下のスレッドを採点してください。\n\n{transcript}"}],
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
        },
    }

    last_error: Exception | None = None
    for attempt, delay in enumerate([0] + RETRY_DELAYS):
        if delay:
            print(f"  gemini unavailable, retrying in {delay}s (attempt {attempt + 1})")
            time.sleep(delay)
        r = requests.post(
            ENDPOINT.format(model=model),
            params={"key": api_key},
            json=body,
            timeout=TIMEOUT,
        )
        if r.status_code in (429, 503):
            last_error = RuntimeError(f"gemini [{r.status_code}]: {r.text[:300]}")
            continue
        if r.status_code != 200:
            raise RuntimeError(f"gemini [{r.status_code}]: {r.text[:300]}")
        data = r.json()
        try:
            raw = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as e:
            raise RuntimeError(f"unexpected gemini response: {json.dumps(data)[:300]}") from e
        return json.loads(raw)

    raise last_error  # type: ignore[misc]
