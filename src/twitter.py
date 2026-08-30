"""X (Twitter) API v2 client via tweepy."""

from __future__ import annotations

from typing import Any

import tweepy

MAX_CONVERSATION = 60
MAX_QUOTE_DEPTH = 10


class TwitterError(RuntimeError):
    pass


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
            user_fields=["username"],
        )
        if not resp.data:
            return []
        users = {
            str(u.id): u.username
            for u in (resp.includes or {}).get("users", [])
        }
        result = []
        for t in resp.data:
            result.append({
                "id": str(t.id),
                "text": t.text or "",
                "author_id": str(t.author_id),
                "handle": users.get(str(t.author_id), "unknown"),
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
            user_fields=["username"],
        )
        tweets = list(resp.data or [])
        users = {
            str(u.id): u.username
            for u in (resp.includes or {}).get("users", [])
        }

        # Root tweet may not appear in search results — fetch separately
        root_resp = self._client.get_tweet(
            conv_id,
            tweet_fields=["author_id", "created_at", "text"],
            expansions=["author_id"],
            user_fields=["username"],
        )
        if root_resp.data:
            root_users = {
                str(u.id): u.username
                for u in (root_resp.includes or {}).get("users", [])
            }
            users.update(root_users)
            existing_ids = {str(t.id) for t in tweets}
            if str(root_resp.data.id) not in existing_ids:
                tweets.append(root_resp.data)

        chain = []
        for t in tweets:
            chain.append({
                "id": str(t.id),
                "text": (t.text or "").strip(),
                "handle": users.get(str(t.author_id), "unknown"),
                "created_at": t.created_at.isoformat() if t.created_at else "",
            })

        chain.sort(key=lambda x: x["created_at"])
        return chain[-MAX_CONVERSATION:]

    def _fetch_tweet(self, tweet_id: str) -> dict[str, Any] | None:
        """Fetch a single tweet including referenced_tweets for quote traversal."""
        try:
            resp = self._client.get_tweet(
                tweet_id,
                tweet_fields=["author_id", "created_at", "text", "referenced_tweets"],
                expansions=["author_id"],
                user_fields=["username"],
            )
        except Exception:
            return None
        if not resp.data:
            return None
        t = resp.data
        users = {str(u.id): u.username for u in (resp.includes or {}).get("users", [])}
        return {
            "id": str(t.id),
            "text": (t.text or "").strip(),
            "handle": users.get(str(t.author_id), "unknown"),
            "created_at": t.created_at.isoformat() if t.created_at else "",
            "_refs": [
                {"type": r.type, "id": str(r.id)}
                for r in (getattr(t, "referenced_tweets", None) or [])
            ],
        }

    def _traverse_quote_chain(self, start_tweet_id: str) -> list[dict[str, Any]]:
        """Walk up the quote chain from start_tweet_id, up to MAX_QUOTE_DEPTH levels."""
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

            quoted_id = next(
                (r["id"] for r in refs if r["type"] == "quoted"), None
            )
            if not quoted_id:
                break
            tweet_id = quoted_id

        return chain

    def get_full_chain(self, mention_id: str, conv_id: str) -> list[dict[str, Any]]:
        """Reply thread + quote chain merged, deduplicated, sorted chronologically."""
        seen: set[str] = set()
        combined: list[dict[str, Any]] = []

        def _add(tweets: list[dict[str, Any]]) -> None:
            for t in tweets:
                if t["id"] not in seen:
                    seen.add(t["id"])
                    combined.append(t)

        _add(self.get_conversation_chain(conv_id))
        _add(self._traverse_quote_chain(mention_id))
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
