"""X (Twitter) API v2 client via tweepy."""

from __future__ import annotations

from typing import Any

import tweepy

MAX_CONVERSATION = 60
MAX_QUOTE_DEPTH = 10


class TwitterError(RuntimeError):
    pass


def _parse_users(includes: Any) -> tuple[dict[str, str], dict[str, str]]:
    """Return (handle_map, name_map) keyed by user id string."""
    users = (includes or {}).get("users", [])
    return (
        {str(u.id): u.username for u in users},
        {str(u.id): (u.name or u.username) for u in users},
    )


class TwitterClient:
    def __init__(
        self,
        handle: str,
        api_key: str,
        api_key_secret: str,
        access_token: str,
        access_token_secret: str,
        bearer_token: str,
    ) -> None:
        self.handle = handle.lstrip("@")
        self.user_id: str | None = None
        self._client = tweepy.Client(
            bearer_token=bearer_token,
            consumer_key=api_key,
            consumer_secret=api_key_secret,
            access_token=access_token,
            access_token_secret=access_token_secret,
            wait_on_rate_limit=False,
        )

    def login(self) -> None:
        resp = self._client.get_user(username=self.handle)
        if not resp.data:
            raise TwitterError(f"user not found: @{self.handle}")
        self.user_id = str(resp.data.id)

    def list_mentions(
        self, since_id: str | None = None, max_results: int = 10
    ) -> list[dict[str, Any]]:
        resp = self._client.get_users_mentions(
            self.user_id,
            since_id=since_id,
            max_results=max_results,
            tweet_fields=["conversation_id", "created_at", "author_id"],
            expansions=["author_id"],
            user_fields=["username", "name"],
        )
        if not resp.data:
            return []
        handles, names = _parse_users(resp.includes)
        result = []
        for t in resp.data:
            aid = str(t.author_id)
            result.append({
                "id": str(t.id),
                "text": t.text or "",
                "author_id": aid,
                "handle": handles.get(aid, "unknown"),
                "name": names.get(aid, handles.get(aid, "unknown")),
                "conversation_id": str(t.conversation_id),
                "created_at": t.created_at.isoformat() if t.created_at else "",
            })
        return result

    def get_conversation_chain(self, conv_id: str) -> list[dict[str, Any]]:
        """Fetch all tweets in a conversation, sorted chronologically."""
        resp = self._client.search_recent_tweets(
            query=f"conversation_id:{conv_id} -is:retweet",
            max_results=100,
            tweet_fields=["author_id", "created_at", "text"],
            expansions=["author_id"],
            user_fields=["username", "name"],
        )
        tweets = list(resp.data or [])
        handles, names = _parse_users(resp.includes)

        # Root tweet may not appear in search results — fetch separately
        root_resp = self._client.get_tweet(
            conv_id,
            tweet_fields=["author_id", "created_at", "text"],
            expansions=["author_id"],
            user_fields=["username", "name"],
        )
        if root_resp.data:
            rh, rn = _parse_users(root_resp.includes)
            handles.update(rh)
            names.update(rn)
            existing_ids = {str(t.id) for t in tweets}
            if str(root_resp.data.id) not in existing_ids:
                tweets.append(root_resp.data)

        chain = []
        for t in tweets:
            aid = str(t.author_id)
            chain.append({
                "id": str(t.id),
                "text": (t.text or "").strip(),
                "handle": handles.get(aid, "unknown"),
                "name": names.get(aid, handles.get(aid, "unknown")),
                "created_at": t.created_at.isoformat() if t.created_at else "",
            })

        chain.sort(key=lambda x: x["created_at"])
        return chain[-MAX_CONVERSATION:]

    def _fetch_tweet(self, tweet_id: str) -> dict[str, Any] | None:
        """Fetch a single tweet including referenced_tweets for quote traversal."""
        try:
            resp = self._client.get_tweet(
                tweet_id,
                tweet_fields=["author_id", "created_at", "text", "referenced_tweets", "conversation_id"],
                expansions=["author_id"],
                user_fields=["username", "name"],
            )
        except Exception:
            return None
        if not resp.data:
            return None
        t = resp.data
        handles, names = _parse_users(resp.includes)
        aid = str(t.author_id)
        conv_id = str(t.conversation_id) if getattr(t, "conversation_id", None) else tweet_id
        return {
            "id": str(t.id),
            "text": (t.text or "").strip(),
            "handle": handles.get(aid, "unknown"),
            "name": names.get(aid, handles.get(aid, "unknown")),
            "created_at": t.created_at.isoformat() if t.created_at else "",
            "conversation_id": conv_id,
            "_refs": [
                {"type": r.type, "id": str(r.id)}
                for r in (getattr(t, "referenced_tweets", None) or [])
            ],
        }

    def _traverse_debate_chain(self, start_tweet_id: str) -> list[dict[str, Any]]:
        """Walk backward through a debate chain (quotes and replies) up to MAX_QUOTE_DEPTH.

        Follows 'quoted' links first; falls back to 'replied_to' so mixed
        quote-and-reply debates are fully traversed.
        """
        chain: list[dict[str, Any]] = []
        seen: set[str] = set()
        tweet_id = start_tweet_id

        for _ in range(MAX_QUOTE_DEPTH):
            if tweet_id in seen:
                break
            tweet = self._fetch_tweet(tweet_id)
            if not tweet:
                break
            seen.add(tweet_id)
            refs = tweet.pop("_refs", [])
            chain.append(tweet)

            # Prefer quote link (debate via quote tweet); fall back to reply link
            next_id = next((r["id"] for r in refs if r["type"] == "quoted"), None)
            if not next_id:
                next_id = next((r["id"] for r in refs if r["type"] == "replied_to"), None)
            if not next_id:
                break
            tweet_id = next_id

        return chain

    def get_full_chain(self, mention_id: str, conv_id: str) -> list[dict[str, Any]]:
        """Fetch the debate chain the mention points to, supporting third-party judgments.

        Strategy:
        1. Find the "anchor" tweet the mention replies to or quotes.
        2. Traverse the quote chain upward from the anchor (covers quote-based debates).
        3. Also fetch the anchor's reply-thread conversation (covers reply-based debates).
        4. Fall back to the mention's own conversation / quote chain.
        """
        seen: set[str] = set()
        combined: list[dict[str, Any]] = []

        def _add(tweets: list[dict[str, Any]]) -> None:
            for t in tweets:
                if t["id"] not in seen:
                    seen.add(t["id"])
                    combined.append(t)

        # Fetch mention tweet to find what it points to
        mention_data = self._fetch_tweet(mention_id)
        refs = mention_data.pop("_refs", []) if mention_data else []

        # Anchor = the tweet the mention replies to OR quotes
        anchor_id = next(
            (r["id"] for r in refs if r["type"] in ("replied_to", "quoted")),
            None,
        )

        if anchor_id:
            # Get anchor's conversation_id for reply-thread fetch
            anchor_data = self._fetch_tweet(anchor_id)
            if anchor_data:
                anchor_data.pop("_refs", [])
                _add(self.get_conversation_chain(anchor_data.get("conversation_id", anchor_id)))

            # Traverse quote chain upward from anchor to root
            _add(self._traverse_debate_chain(anchor_id))

        # Fallback: mention's own conversation and quote chain
        _add(self.get_conversation_chain(conv_id))
        _add(self._traverse_debate_chain(mention_id))

        combined.sort(key=lambda x: x["created_at"])
        return combined[-MAX_CONVERSATION:]

    def has_bot_replied(self, conv_id: str) -> bool:
        """Return True if the bot already replied in this conversation."""
        try:
            resp = self._client.search_recent_tweets(
                query=f"conversation_id:{conv_id} from:{self.handle}",
                max_results=10,
            )
            return bool(resp.data)
        except Exception:
            return False

    def reply(self, text: str, in_reply_to_tweet_id: str) -> dict[str, Any]:
        resp = self._client.create_tweet(
            text=text,
            in_reply_to_tweet_id=in_reply_to_tweet_id,
        )
        return {"id": str(resp.data["id"])}
