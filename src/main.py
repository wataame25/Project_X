"""Entry point: poll X mentions -> judge thread -> reply."""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import sys
import traceback

from .twitter import TwitterClient
from .formatter import format_skip, format_verdict
from .judge import build_transcript, judge

STATE_PATH = pathlib.Path("state/seen.json")
STATE_KEEP = 500

MAX_AGE_MIN = int(os.getenv("MAX_AGE_MINUTES", "90"))
MAX_PER_RUN = int(os.getenv("MAX_PER_RUN", "3"))
MIN_POSTS = int(os.getenv("MIN_POSTS", "4"))
DRY_RUN = os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"processed": [], "last_mention_id": None}


def save_state(state: dict) -> None:
    state["processed"] = state["processed"][-STATE_KEEP:]
    state["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_fresh(created_at: str) -> bool:
    try:
        ts = dt.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    age = dt.datetime.now(dt.timezone.utc) - ts
    return age <= dt.timedelta(minutes=MAX_AGE_MIN)


def handle_mention(client: TwitterClient, mention: dict, gemini_key: str, model: str) -> None:
    tweet_id = mention["id"]
    summoner = mention["handle"]
    conv_id = mention["conversation_id"]
    print(f"  summoned by @{summoner}")

    if client.has_bot_replied(conv_id):
        print("  already replied, skipping")
        return

    chain = client.get_full_chain(tweet_id, conv_id)
    if not chain:
        print("  ! could not read thread")
        return

    debate = [p for p in chain if p["handle"] != client.handle]
    speakers = {p["handle"] for p in debate}

    if len(debate) < MIN_POSTS or len(speakers) < 2:
        text = format_skip("2名以上・4投稿以上のやりとりが必要です。")
    else:
        transcript = build_transcript(chain, client.handle)
        result = judge(transcript, gemini_key, model)
        print(f"  verdict: {json.dumps(result, ensure_ascii=False)[:200]}")
        if not result.get("valid"):
            text = format_skip("論争として成立していないと判断しました。")
        else:
            text = format_verdict(result)

    print(f"  --- reply ({len(text)} chars) ---\n{text}")
    if DRY_RUN:
        print("  [dry-run] not posting")
        return
    client.reply(text, tweet_id)
    print("  posted")


def main() -> int:
    handle = os.environ["X_HANDLE"]
    api_key = os.environ["X_API_KEY"]
    api_key_secret = os.environ["X_API_KEY_SECRET"]
    access_token = os.environ["X_ACCESS_TOKEN"]
    access_token_secret = os.environ["X_ACCESS_TOKEN_SECRET"]
    bearer_token = os.environ["X_BEARER_TOKEN"]
    gemini_key = os.environ["GEMINI_API_KEY"]
    model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

    client = TwitterClient(
        handle, api_key, api_key_secret, access_token, access_token_secret, bearer_token
    )
    client.login()
    print(f"logged in as @{client.handle} (id: {client.user_id})")

    state = load_state()
    processed = set(state.get("processed", []))
    since_id = state.get("last_mention_id")

    mentions = client.list_mentions(since_id=since_id, max_results=10)
    targets = [
        m for m in mentions
        if m["id"] not in processed
        and m["handle"] != client.handle
        and _is_fresh(m["created_at"])
    ]
    targets.sort(key=lambda m: m["created_at"])
    targets = targets[:MAX_PER_RUN]
    print(f"{len(mentions)} mentions found, {len(targets)} to process")

    for m in targets:
        print(f"- tweet_id={m['id']}")
        try:
            handle_mention(client, m, gemini_key, model)
        except Exception:
            traceback.print_exc()
        finally:
            processed.add(m["id"])

    if mentions:
        state["last_mention_id"] = mentions[0]["id"]

    state["processed"] = list(processed)
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
