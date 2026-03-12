import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Optional
from urllib.parse import quote
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import TelegramAccount

logger = logging.getLogger("tg2.proxy_manager")


class ProxySession:
    """Represents one sticky proxy session bound to a Decodo username suffix."""

    def __init__(self, session_id: str, proxy_url: str, created_at: datetime):
        self.session_id = session_id
        self.proxy_url = proxy_url
        self.created_at = created_at
        self.last_check_at: Optional[datetime] = None
        self.is_alive = True
        self.assigned_accounts: list[int] = []
        self.fail_count = 0

    def can_assign_account(self) -> bool:
        return self.is_alive and len(self.assigned_accounts) < settings.accounts_per_proxy

    def assign_account(self, account_id: int) -> None:
        if account_id not in self.assigned_accounts:
            self.assigned_accounts.append(account_id)

    def unassign_account(self, account_id: int) -> None:
        if account_id in self.assigned_accounts:
            self.assigned_accounts.remove(account_id)

    def mark_dead(self) -> None:
        self.is_alive = False
        logger.warning("Proxy session %s marked as dead", self.session_id)


class DecodoproxyManager:
    """Manages sticky Decodo proxy assignments for Telegram accounts."""

    def __init__(self):
        self.sessions: Dict[str, ProxySession] = {}
        self.account_to_session: Dict[int, str] = {}
        self._lock = asyncio.Lock()
        self._health_check_interval = 300
        self._last_health_check: Optional[datetime] = None

    def _is_configured(self) -> bool:
        return settings.decodo_enabled

    def _build_proxy_username(self, session_id: str) -> str:
        username = settings.decodo_proxy_username.strip()
        if not username:
            raise ValueError("DECODO_PROXY_USERNAME is not configured")

        if settings.decodo_proxy_country and "-country-" not in username:
            username = f"{username}-country-{settings.decodo_proxy_country.strip()}"

        if settings.decodo_proxy_session_duration > 0 and "-sessionduration-" not in username:
            username = f"{username}-sessionduration-{settings.decodo_proxy_session_duration}"

        if "-session-" not in username:
            username = f"{username}-session-{session_id}"

        return username

    def _build_proxy_url(self, session_id: str) -> str:
        username = quote(self._build_proxy_username(session_id), safe="-._~")
        password = quote(settings.decodo_proxy_password, safe="-._~")
        scheme = settings.decodo_proxy_scheme.lower()
        host = settings.decodo_proxy_host.strip()
        port = settings.decodo_proxy_port

        if not host:
            raise ValueError("DECODO_PROXY_HOST is not configured")
        if port <= 0:
            raise ValueError("DECODO_PROXY_PORT must be positive")

        return f"{scheme}://{username}:{password}@{host}:{port}"

    async def _create_session(self) -> Optional[ProxySession]:
        if not self._is_configured():
            logger.info("Decodo proxying disabled: set DECODO_PROXY_USERNAME and DECODO_PROXY_PASSWORD")
            return None

        try:
            session_id = uuid4().hex[:12]
            proxy_url = self._build_proxy_url(session_id)
            proxy_session = ProxySession(
                session_id=session_id,
                proxy_url=proxy_url,
                created_at=datetime.now(timezone.utc),
            )
            logger.info("Created new Decodo sticky proxy session: %s", session_id)
            return proxy_session
        except Exception as exc:
            logger.error("Error creating Decodo proxy session: %s", exc, exc_info=True)
            return None

    async def _check_session_health(self, proxy_session: ProxySession) -> bool:
        proxy_session.last_check_at = datetime.now(timezone.utc)
        return proxy_session.is_alive

    async def _delete_session(self, session_id: str) -> None:
        logger.debug("Dropping local Decodo sticky session mapping: %s", session_id)

    async def get_proxy_for_account(self, account_id: int, db: AsyncSession) -> Optional[str]:
        if not self._is_configured():
            return None

        async with self._lock:
            if account_id in self.account_to_session:
                session_id = self.account_to_session[account_id]
                proxy_session = self.sessions.get(session_id)
                if proxy_session and proxy_session.is_alive:
                    return proxy_session.proxy_url

                logger.info("Proxy for account %s is dead, reassigning", account_id)
                if proxy_session:
                    proxy_session.unassign_account(account_id)
                del self.account_to_session[account_id]

            for proxy_session in self.sessions.values():
                if proxy_session.can_assign_account():
                    proxy_session.assign_account(account_id)
                    self.account_to_session[account_id] = proxy_session.session_id
                    await self._update_account_proxy(db, account_id, proxy_session.proxy_url, proxy_session.session_id)
                    logger.info("Assigned account %s to existing proxy session %s", account_id, proxy_session.session_id)
                    return proxy_session.proxy_url

            new_session = await self._create_session()
            if not new_session:
                logger.error("Failed to create new proxy session for account %s", account_id)
                return None

            self.sessions[new_session.session_id] = new_session
            new_session.assign_account(account_id)
            self.account_to_session[account_id] = new_session.session_id
            await self._update_account_proxy(db, account_id, new_session.proxy_url, new_session.session_id)

            logger.info("Created new proxy session %s for account %s", new_session.session_id, account_id)
            return new_session.proxy_url

    async def _update_account_proxy(self, db: AsyncSession, account_id: int, proxy_url: str, session_id: str) -> None:
        try:
            account = await db.get(TelegramAccount, account_id)
            if account:
                account.proxy_url = proxy_url
                account.proxy_session_id = session_id
                await db.commit()
        except Exception as exc:
            logger.error("Error updating proxy for account %s: %s", account_id, exc)
            await db.rollback()

    async def health_check_all(self, db: AsyncSession) -> None:
        now = datetime.now(timezone.utc)
        if self._last_health_check:
            elapsed = (now - self._last_health_check).total_seconds()
            if elapsed < self._health_check_interval:
                return

        self._last_health_check = now

        async with self._lock:
            active_sessions = [session for session in self.sessions.values() if session.is_alive]
            if not active_sessions:
                return

            logger.info("Refreshing metadata for %s Decodo sticky sessions", len(active_sessions))
            tasks = [self._check_session_health(session) for session in active_sessions]
            await asyncio.gather(*tasks, return_exceptions=True)

    async def initialize_from_db(self, db: AsyncSession) -> None:
        if not self._is_configured():
            return

        async with self._lock:
            query = select(TelegramAccount).where(
                TelegramAccount.is_active.is_(True),
                TelegramAccount.proxy_url.isnot(None),
                TelegramAccount.proxy_session_id.isnot(None),
            )
            result = await db.execute(query)
            accounts = result.scalars().all()

            logger.info("Initializing proxy manager with %s accounts from DB", len(accounts))

            for account in accounts:
                session_id = account.proxy_session_id
                proxy_url = account.proxy_url
                if not session_id or not proxy_url:
                    continue

                if session_id not in self.sessions:
                    self.sessions[session_id] = ProxySession(
                        session_id=session_id,
                        proxy_url=proxy_url,
                        created_at=datetime.now(timezone.utc),
                    )

                proxy_session = self.sessions[session_id]
                proxy_session.assign_account(account.id)
                self.account_to_session[account.id] = session_id

            logger.info("Loaded %s active proxy sessions from DB", len(self.sessions))

    async def get_stats(self) -> dict:
        async with self._lock:
            sessions_info = []
            for session_id, proxy_session in self.sessions.items():
                sessions_info.append(
                    {
                        "session_id": session_id,
                        "is_alive": proxy_session.is_alive,
                        "accounts_count": len(proxy_session.assigned_accounts),
                        "accounts": proxy_session.assigned_accounts,
                        "created_at": proxy_session.created_at.isoformat(),
                        "last_check_at": proxy_session.last_check_at.isoformat() if proxy_session.last_check_at else None,
                        "fail_count": proxy_session.fail_count,
                    }
                )

            return {
                "decodo_enabled": self._is_configured(),
                "decodo_proxy_host": settings.decodo_proxy_host,
                "decodo_proxy_port": settings.decodo_proxy_port,
                "total_sessions": len(self.sessions),
                "alive_sessions": sum(1 for session in self.sessions.values() if session.is_alive),
                "total_accounts_assigned": len(self.account_to_session),
                "accounts_per_proxy": settings.accounts_per_proxy,
                "sessions": sessions_info,
            }

    async def cleanup(self) -> None:
        async with self._lock:
            for session_id in list(self.sessions.keys()):
                await self._delete_session(session_id)

            self.sessions.clear()
            self.account_to_session.clear()
            logger.info("Proxy manager cleaned up")


proxy_manager = DecodoproxyManager()
