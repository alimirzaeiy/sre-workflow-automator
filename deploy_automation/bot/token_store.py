import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class InMemoryTokenStore:
    """
    Temporary, strictly in-memory storage for personal GitLab access tokens.
    Tokens are NEVER persisted to disk, database, or written to logs.
    Tokens are purged immediately upon conversation completion or expiration.
    """
    _store: dict[int, dict] = {}

    @classmethod
    def set_token(cls, user_telegram_id: int, token: str, ttl_seconds: int = 1800):
        cls._store[user_telegram_id] = {
            "token": token.strip(),
            "expires_at": time.time() + ttl_seconds
        }
        logger.info(f"Temporary token cached in memory for telegram user {user_telegram_id} (TTL: {ttl_seconds}s)")

    @classmethod
    def get_token(cls, user_telegram_id: int) -> Optional[str]:
        entry = cls._store.get(user_telegram_id)
        if not entry:
            return None
        if time.time() > entry.get("expires_at", 0):
            cls.remove_token(user_telegram_id)
            return None
        return entry.get("token")

    @classmethod
    def remove_token(cls, user_telegram_id: int):
        if user_telegram_id in cls._store:
            del cls._store[user_telegram_id]
            logger.info(f"Temporary token purged from memory for telegram user {user_telegram_id}")
