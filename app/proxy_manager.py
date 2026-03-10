import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import aiohttp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import TelegramAccount

logger = logging.getLogger("tg2.proxy_manager")


class ProxySession:
    """Представляет одну прокси-сессию с session_id от Decodo"""
    
    def __init__(self, session_id: str, proxy_url: str, created_at: datetime):
        self.session_id = session_id
        self.proxy_url = proxy_url
        self.created_at = created_at
        self.last_check_at: Optional[datetime] = None
        self.is_alive = True
        self.assigned_accounts: list[int] = []
        self.fail_count = 0
    
    def can_assign_account(self) -> bool:
        """Проверяет, можно ли назначить еще один аккаунт на этот прокси"""
        return self.is_alive and len(self.assigned_accounts) < settings.accounts_per_proxy
    
    def assign_account(self, account_id: int) -> None:
        """Назначает аккаунт на этот прокси"""
        if account_id not in self.assigned_accounts:
            self.assigned_accounts.append(account_id)
    
    def unassign_account(self, account_id: int) -> None:
        """Убирает аккаунт с этого прокси"""
        if account_id in self.assigned_accounts:
            self.assigned_accounts.remove(account_id)
    
    def mark_dead(self) -> None:
        """Помечает прокси как мертвый"""
        self.is_alive = False
        logger.warning("Proxy session %s marked as dead", self.session_id)


class DecodoproxyManager:
    """Менеджер прокси для работы с Decodo.com API"""
    
    def __init__(self):
        self.sessions: Dict[str, ProxySession] = {}
        self.account_to_session: Dict[int, str] = {}
        self._lock = asyncio.Lock()
        self._health_check_interval = 300  # 5 минут
        self._last_health_check: Optional[datetime] = None
    
    async def _create_session(self) -> Optional[ProxySession]:
        """Создает новую прокси-сессию через Decodo API"""
        if not settings.decodo_api_key:
            logger.error("DECODO_API_KEY not configured")
            return None
        
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bearer {settings.decodo_api_key}",
                    "Content-Type": "application/json"
                }
                
                # Создаем новую сессию
                async with session.post(
                    f"{settings.decodo_api_url}/sessions",
                    headers=headers,
                    json={"type": "residential"},  # или "datacenter" в зависимости от нужд
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.error("Failed to create Decodo session: %s - %s", resp.status, error_text)
                        return None
                    
                    data = await resp.json()
                    session_id = data.get("session_id")
                    proxy_host = data.get("proxy_host")
                    proxy_port = data.get("proxy_port")
                    proxy_user = data.get("proxy_user")
                    proxy_pass = data.get("proxy_pass")
                    
                    if not all([session_id, proxy_host, proxy_port]):
                        logger.error("Invalid response from Decodo API: %s", data)
                        return None
                    
                    # Формируем proxy URL
                    if proxy_user and proxy_pass:
                        proxy_url = f"socks5://{proxy_user}:{proxy_pass}@{proxy_host}:{proxy_port}"
                    else:
                        proxy_url = f"socks5://{proxy_host}:{proxy_port}"
                    
                    proxy_session = ProxySession(
                        session_id=session_id,
                        proxy_url=proxy_url,
                        created_at=datetime.now(timezone.utc)
                    )
                    
                    logger.info("Created new Decodo proxy session: %s", session_id)
                    return proxy_session
                    
        except asyncio.TimeoutError:
            logger.error("Timeout creating Decodo session")
            return None
        except Exception as e:
            logger.error("Error creating Decodo session: %s", e, exc_info=True)
            return None
    
    async def _check_session_health(self, proxy_session: ProxySession) -> bool:
        """Проверяет здоровье прокси-сессии"""
        if not settings.decodo_api_key:
            return False
        
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bearer {settings.decodo_api_key}",
                }
                
                async with session.get(
                    f"{settings.decodo_api_url}/sessions/{proxy_session.session_id}",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status == 404:
                        logger.warning("Proxy session %s not found", proxy_session.session_id)
                        return False
                    
                    if resp.status != 200:
                        logger.warning("Proxy session %s health check failed: %s", 
                                     proxy_session.session_id, resp.status)
                        proxy_session.fail_count += 1
                        return proxy_session.fail_count < 3
                    
                    data = await resp.json()
                    is_active = data.get("status") == "active"
                    
                    if is_active:
                        proxy_session.fail_count = 0
                    else:
                        proxy_session.fail_count += 1
                    
                    proxy_session.last_check_at = datetime.now(timezone.utc)
                    return is_active and proxy_session.fail_count < 3
                    
        except Exception as e:
            logger.error("Error checking session health %s: %s", proxy_session.session_id, e)
            proxy_session.fail_count += 1
            return proxy_session.fail_count < 3
    
    async def _delete_session(self, session_id: str) -> None:
        """Удаляет прокси-сессию через Decodo API"""
        if not settings.decodo_api_key:
            return
        
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bearer {settings.decodo_api_key}",
                }
                
                async with session.delete(
                    f"{settings.decodo_api_url}/sessions/{session_id}",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status in (200, 204, 404):
                        logger.info("Deleted Decodo session: %s", session_id)
                    else:
                        logger.warning("Failed to delete session %s: %s", session_id, resp.status)
                        
        except Exception as e:
            logger.error("Error deleting session %s: %s", session_id, e)
    
    async def get_proxy_for_account(self, account_id: int, db: AsyncSession) -> Optional[str]:
        """Получает прокси URL для аккаунта, создавая новый если нужно"""
        if not settings.decodo_api_key:
            return None
            
        async with self._lock:
            # Проверяем, есть ли уже назначенный прокси
            if account_id in self.account_to_session:
                session_id = self.account_to_session[account_id]
                proxy_session = self.sessions.get(session_id)
                
                if proxy_session and proxy_session.is_alive:
                    return proxy_session.proxy_url
                
                # Прокси мертв, нужно переназначить
                logger.info("Proxy for account %s is dead, reassigning", account_id)
                if proxy_session:
                    proxy_session.unassign_account(account_id)
                del self.account_to_session[account_id]
            
            # Ищем существующий прокси с свободными слотами
            for proxy_session in self.sessions.values():
                if proxy_session.can_assign_account():
                    proxy_session.assign_account(account_id)
                    self.account_to_session[account_id] = proxy_session.session_id
                    
                    # Обновляем в БД
                    await self._update_account_proxy(db, account_id, proxy_session.proxy_url, proxy_session.session_id)
                    
                    logger.info("Assigned account %s to existing proxy session %s", 
                              account_id, proxy_session.session_id)
                    return proxy_session.proxy_url
            
            # Нужно создать новый прокси
            new_session = await self._create_session()
            if not new_session:
                logger.error("Failed to create new proxy session for account %s", account_id)
                return None
            
            self.sessions[new_session.session_id] = new_session
            new_session.assign_account(account_id)
            self.account_to_session[account_id] = new_session.session_id
            
            # Обновляем в БД
            await self._update_account_proxy(db, account_id, new_session.proxy_url, new_session.session_id)
            
            logger.info("Created new proxy session %s for account %s", 
                       new_session.session_id, account_id)
            return new_session.proxy_url
    
    async def _update_account_proxy(self, db: AsyncSession, account_id: int, proxy_url: str, session_id: str) -> None:
        """Обновляет proxy_url и proxy_session_id в базе данных для аккаунта"""
        try:
            account = await db.get(TelegramAccount, account_id)
            if account:
                account.proxy_url = proxy_url
                account.proxy_session_id = session_id
                await db.commit()
        except Exception as e:
            logger.error("Error updating proxy for account %s: %s", account_id, e)
            await db.rollback()
    
    async def health_check_all(self, db: AsyncSession) -> None:
        """Проверяет здоровье всех прокси-сессий параллельно"""
        now = datetime.now(timezone.utc)
        
        # Проверяем, нужна ли проверка
        if self._last_health_check:
            elapsed = (now - self._last_health_check).total_seconds()
            if elapsed < self._health_check_interval:
                return
        
        self._last_health_check = now
        
        async with self._lock:
            active_sessions = [s for s in self.sessions.values() if s.is_alive]
            if not active_sessions:
                return

            logger.info("Starting health check for %s proxy sessions", len(active_sessions))
            
            # Проверяем параллельно
            tasks = [self._check_session_health(s) for s in active_sessions]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            dead_sessions_ids = []
            
            for proxy_session, is_healthy in zip(active_sessions, results):
                if isinstance(is_healthy, Exception) or not is_healthy:
                    if isinstance(is_healthy, Exception):
                        logger.error("Error checking health for session %s: %s", 
                                   proxy_session.session_id, is_healthy)
                        proxy_session.fail_count += 1
                        is_healthy = proxy_session.fail_count < 3
                    
                    if not is_healthy:
                        proxy_session.mark_dead()
                        dead_sessions_ids.append(proxy_session.session_id)
                        
                        # Переназначаем все аккаунты с этого прокси
                        accounts_to_reassign = proxy_session.assigned_accounts[:]
                        for account_id in accounts_to_reassign:
                            logger.info("Reassigning account %s from dead proxy %s", 
                                      account_id, proxy_session.session_id)
                            if account_id in self.account_to_session:
                                del self.account_to_session[account_id]
                            
                            # Не вызываем get_proxy_for_account внутри цикла с блокировкой, 
                            # так как мы уже под _lock. Но get_proxy_for_account тоже берет _lock.
                            # Нам нужно либо отпустить лок, либо иметь метод без лока.
                            # Для простоты: пометили как дед, удалили из сессий, 
                            # а в следующем вызове любого сервиса для этого аккаунта get_proxy_for_account сам создаст новый.
            
            # Удаляем мертвые сессии из основного словаря
            for session_id in dead_sessions_ids:
                if session_id in self.sessions:
                    await self._delete_session(session_id)
                    del self.sessions[session_id]
            
            if dead_sessions_ids:
                logger.info("Removed %s dead proxy sessions", len(dead_sessions_ids))
    
    async def initialize_from_db(self, db: AsyncSession) -> None:
        """Инициализирует менеджер из существующих аккаунтов в БД"""
        if not settings.decodo_api_key:
            return
            
        async with self._lock:
            query = select(TelegramAccount).where(
                TelegramAccount.is_active.is_(True),
                TelegramAccount.proxy_url.isnot(None),
                TelegramAccount.proxy_session_id.isnot(None)
            )
            result = await db.execute(query)
            accounts = result.scalars().all()
            
            logger.info("Initializing proxy manager with %s accounts from DB", len(accounts))
            
            for account in accounts:
                session_id = account.proxy_session_id
                proxy_url = account.proxy_url
                
                if session_id not in self.sessions:
                    # Создаем запись о сессии на основе данных из БД
                    # Мы не знаем точное время создания, поэтому ставим текущее
                    self.sessions[session_id] = ProxySession(
                        session_id=session_id,
                        proxy_url=proxy_url,
                        created_at=datetime.now(timezone.utc)
                    )
                
                proxy_session = self.sessions[session_id]
                proxy_session.assign_account(account.id)
                self.account_to_session[account.id] = session_id

            logger.info("Loaded %s active proxy sessions from DB", len(self.sessions))
    
    async def get_stats(self) -> dict:
        """Возвращает статистику по прокси"""
        async with self._lock:
            total_sessions = len(self.sessions)
            alive_sessions = sum(1 for s in self.sessions.values() if s.is_alive)
            total_accounts = len(self.account_to_session)
            
            sessions_info = []
            for session_id, proxy_session in self.sessions.items():
                sessions_info.append({
                    "session_id": session_id,
                    "is_alive": proxy_session.is_alive,
                    "accounts_count": len(proxy_session.assigned_accounts),
                    "accounts": proxy_session.assigned_accounts,
                    "created_at": proxy_session.created_at.isoformat(),
                    "last_check_at": proxy_session.last_check_at.isoformat() if proxy_session.last_check_at else None,
                    "fail_count": proxy_session.fail_count
                })
            
            return {
                "total_sessions": total_sessions,
                "alive_sessions": alive_sessions,
                "total_accounts_assigned": total_accounts,
                "accounts_per_proxy": settings.accounts_per_proxy,
                "sessions": sessions_info
            }
    
    async def cleanup(self) -> None:
        """Очищает все прокси-сессии"""
        async with self._lock:
            for session_id in list(self.sessions.keys()):
                await self._delete_session(session_id)
            
            self.sessions.clear()
            self.account_to_session.clear()
            logger.info("Proxy manager cleaned up")


# Глобальный экземпляр менеджера
proxy_manager = DecodoproxyManager()
