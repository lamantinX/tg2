from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import AIService, DEFAULT_MAIN_SYSTEM_PROMPT
from app.config import settings
from app.decision_engine import DecisionContext, decision_engine
from app.repositories import AccountRepository, AppSettingsRepository, BindingRepository, CharacterRepository, MessageLogRepository, ReplyTaskRepository
from app.telegram_client import TelegramAccountClient, AuthKeyDuplicatedError
from app.character_engine import DEFAULT_CHARACTERS


audit_logger = logging.getLogger("tg2.audit")


def configure_audit_logger() -> None:
    log_path = settings.resolved_data_dir / "logs" / "account_audit.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if not audit_logger.handlers:
        handler = logging.FileHandler(log_path, encoding="utf-8")
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        handler.setFormatter(formatter)
        audit_logger.addHandler(handler)
        audit_logger.setLevel(logging.INFO)
from app.proxy_manager import proxy_manager

class AccountService:
    _TRANSIENT_HEALTH_ERROR_MARKERS = (
        "database is locked",
        "connection to telegram failed",
        "timeout",
        "timed out",
    )

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = AccountRepository(session)
        self.binding_repo = BindingRepository(session)
        configure_audit_logger()

    async def create_account(self, phone: str, proxy_url: str | None) -> object:
        existing = await self.repo.get_by_phone(phone)
        if existing:
            return existing
        session_name = phone.replace("+", "").replace(" ", "")
        existing_by_session = await self.repo.get_by_session_name(session_name)
        if existing_by_session:
            return existing_by_session

        # Assign a random character if available
        char_repo = CharacterRepository(self.session)
        chars = await char_repo.list()
        character_id = None
        if chars:
            import random
            character_id = random.choice(chars).id

        return await self.repo.create(phone=phone, session_name=session_name, proxy_url=proxy_url, character_id=character_id)

    async def list_accounts(self) -> list[object]:
        return await self.repo.list()

    async def get_account(self, account_id: int) -> object | None:
        return await self.repo.get(account_id)

    async def list_accounts_for_menu(self) -> list[object]:
        return await self.repo.list()

    async def ensure_account_name(self, account: object) -> object:
        if getattr(account, "account_name", None):
            return account
        if getattr(account, "auth_status", None) != "authorized" or not bool(getattr(account, "is_active", False)):
            return account

        proxy_url = await proxy_manager.get_proxy_for_account(account.id, self.session)
        tg = TelegramAccountClient(session_name=account.session_name, proxy_url=proxy_url or account.proxy_url)
        try:
            return await self._sync_account_name(account, tg)
        finally:
            await tg.disconnect()

    async def _sync_account_name(self, account: object, tg: TelegramAccountClient) -> object:
        try:
            account_name = await tg.get_account_name()
        except Exception as exc:
            logging.getLogger("tg2.services").warning(
                "Failed to resolve account name for account_id=%s: %s",
                account.id,
                exc,
            )
            return account
        if not account_name or account_name == getattr(account, "account_name", None):
            return account
        return await self.repo.update_profile(account, account_name=account_name)

    async def check_account(self, account_id: int) -> dict[str, object]:
        account = await self.repo.get(account_id)
        if account is None:
            raise ValueError(f"Account {account_id} not found")

        proxy_url = await proxy_manager.get_proxy_for_account(account.id, self.session)
        tg = TelegramAccountClient(session_name=account.session_name, proxy_url=proxy_url or account.proxy_url)
        try:
            status = await tg.check_health()
            auth_status = str(status["auth_status"])
            is_active = bool(status["is_active"])
            reason = str(status["reason"])

            keep_last_known_authorized_state = self._should_keep_last_known_authorized_state(account, auth_status, reason)
            if keep_last_known_authorized_state:
                auth_status = "authorized"
                is_active = True
                reason = f"transient health check error; kept last known authorized state: {reason}"

            if auth_status == "authorized" and is_active:
                account = await self._sync_account_name(account, tg)

            account = await self.repo.mark_status(
                account,
                auth_status=auth_status,
                is_active=is_active,
                touch_last_login=not keep_last_known_authorized_state,
            )
        finally:
            await tg.disconnect()

        paused = 0
        resumed = 0
        if not is_active or auth_status != "authorized":
            paused = await self.binding_repo.auto_pause_for_account(account.id, reason)
        else:
            resumed = await self.binding_repo.resume_auto_paused_for_account(account.id)

        return {
            "account": account,
            "auth_status": auth_status,
            "is_active": is_active,
            "reason": reason,
            "paused_bindings": paused,
            "resumed_bindings": resumed,
        }

    async def delete_account(self, account_id: int) -> dict[str, object]:
        account = await self.repo.get(account_id)
        if account is None:
            raise ValueError(f"Account {account_id} not found")

        deleted_bindings = await self.binding_repo.delete_by_account_id(account.id)
        deleted = bool(await self.repo.delete(account))

        session_file = settings.resolved_data_dir / "sessions" / f"{account.session_name}.session"
        if session_file.exists():
            session_file.unlink()

        return {
            "account_id": account.id,
            "deleted": deleted,
            "deleted_bindings": deleted_bindings,
        }

    async def request_login_code(self, account_id: int) -> str:
        account = await self.repo.get(account_id)
        if account is None:
            raise ValueError(f"Account {account_id} not found")

        proxy_url = await proxy_manager.get_proxy_for_account(account.id, self.session)
        tg = TelegramAccountClient(session_name=account.session_name, proxy_url=proxy_url or account.proxy_url)
        try:
            if await tg.is_authorized():
                await self._sync_account_name(account, tg)
                await self.repo.mark_authorized(account)
                await self.binding_repo.resume_auto_paused_for_account(account.id)
                return "already_authorized"
            phone_code_hash = await tg.request_login_code(account.phone)
            await self.repo.update_login_code_hash(account, phone_code_hash)
            return "code_requested"
        finally:
            await tg.disconnect()

    async def complete_login(self, account_id: int, code: str, password: str | None = None) -> str:
        account = await self.repo.get(account_id)
        if account is None:
            raise ValueError(f"Account {account_id} not found")
        if not account.phone_code_hash and account.auth_status != "authorized":
            raise ValueError("Login code was not requested for this account")

        proxy_url = await proxy_manager.get_proxy_for_account(account.id, self.session)
        tg = TelegramAccountClient(session_name=account.session_name, proxy_url=proxy_url or account.proxy_url)
        try:
            status = await tg.complete_login(
                phone=account.phone,
                code=code,
                phone_code_hash=account.phone_code_hash or "",
                password=password,
            )
            if status == "authorized":
                await self._sync_account_name(account, tg)
                await self.repo.mark_authorized(account)
                await self.binding_repo.resume_auto_paused_for_account(account.id)
            return status
        finally:
            await tg.disconnect()

    async def complete_password_login(self, account_id: int, password: str) -> str:
        account = await self.repo.get(account_id)
        if account is None:
            raise ValueError(f"Account {account_id} not found")
        if account.auth_status not in {"code_requested", "authorized"} and not account.phone_code_hash:
            raise ValueError("Login code was not requested for this account")

        proxy_url = await proxy_manager.get_proxy_for_account(account.id, self.session)
        tg = TelegramAccountClient(session_name=account.session_name, proxy_url=proxy_url or account.proxy_url)
        try:
            status = await tg.complete_password_login(password=password)
            if status == "authorized":
                await self._sync_account_name(account, tg)
                await self.repo.mark_authorized(account)
                await self.binding_repo.resume_auto_paused_for_account(account.id)
            return status
        finally:
            await tg.disconnect()

    @classmethod
    def _should_keep_last_known_authorized_state(cls, account: object, auth_status: str, reason: str) -> bool:
        if auth_status != "error":
            return False
        normalized_reason = reason.lower()
        if not any(marker in normalized_reason for marker in cls._TRANSIENT_HEALTH_ERROR_MARKERS):
            return False

        previous_auth_status = str(getattr(account, "auth_status", ""))
        if previous_auth_status == "authorized":
            return True
        return previous_auth_status == "error" and getattr(account, "last_login_at", None) is not None

    async def audit_accounts(self) -> dict[str, object]:
        accounts = await self.repo.list()
        report: dict[str, object] = {
            "audited": 0,
            "active": 0,
            "inactive": 0,
            "paused_bindings": 0,
            "resumed_bindings": 0,
            "details": [],
        }
        for account in accounts:
            report["audited"] += 1
            # Пропускаем выключенные/деактивированные без перепроверки
            proxy_url = await proxy_manager.get_proxy_for_account(account.id, self.session)

            tg = TelegramAccountClient(session_name=account.session_name, proxy_url=proxy_url or account.proxy_url)
            try:
                status = await tg.check_health()
            finally:
                await tg.disconnect()

            auth_status = str(status["auth_status"])
            is_active = bool(status["is_active"])
            reason = str(status["reason"])

            keep_last_known_authorized_state = self._should_keep_last_known_authorized_state(account, auth_status, reason)
            if keep_last_known_authorized_state:
                auth_status = "authorized"
                is_active = True
                reason = f"transient health check error; kept last known authorized state: {reason}"

            await self.repo.mark_status(
                account,
                auth_status=auth_status,
                is_active=is_active,
                touch_last_login=not keep_last_known_authorized_state,
            )

            paused = 0
            resumed = 0
            if not is_active or auth_status != "authorized":
                paused = await self.binding_repo.auto_pause_for_account(account.id, reason)
                report["inactive"] += 1
                report["paused_bindings"] += paused
            else:
                resumed = await self.binding_repo.resume_auto_paused_for_account(account.id)
                report["active"] += 1
                report["resumed_bindings"] += resumed

            detail = {
                "account_id": account.id,
                "phone": account.phone,
                "auth_status": auth_status,
                "is_active": is_active,
                "paused_bindings": paused,
                "resumed_bindings": resumed,
                "reason": reason,
            }
            report["details"].append(detail)

        audit_logger.info("account_audit %s", json.dumps(report, ensure_ascii=False))
        return report


class BindingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = BindingRepository(session)
        self.account_repo = AccountRepository(session)

    def _normalize_dt(self, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    def _validate_interval_range(
        self,
        label: str,
        min_minutes: int | None,
        max_minutes: int | None,
        *,
        allow_disabled: bool = False,
    ) -> None:
        if min_minutes is None and max_minutes is None and allow_disabled:
            return
        if min_minutes is None or max_minutes is None:
            raise ValueError(f"{label} interval requires both min and max values")
        if min_minutes < 1:
            raise ValueError(f"{label}_min_minutes must be >= 1")
        if max_minutes < 1:
            raise ValueError(f"{label}_max_minutes must be >= 1")
        if min_minutes > max_minutes:
            raise ValueError(f"{label}_min_minutes cannot be greater than {label}_max_minutes")

    def _validate_settings(
        self,
        interval_min_minutes: int | None,
        interval_max_minutes: int | None,
        context_message_count: int | None,
        reply_interval_min_minutes: int | None = None,
        reply_interval_max_minutes: int | None = None,
    ) -> None:
        self._validate_interval_range("interval", interval_min_minutes, interval_max_minutes)
        self._validate_interval_range(
            "reply_interval",
            reply_interval_min_minutes,
            reply_interval_max_minutes,
            allow_disabled=True,
        )
        if context_message_count is not None and not 1 <= context_message_count <= 200:
            raise ValueError("context_message_count must be between 1 and 200")

    def _resolve_reply_interval(
        self,
        binding: object,
        reply_interval_min_minutes: int | None,
        reply_interval_max_minutes: int | None,
        reset_reply_interval: bool,
    ) -> tuple[int | None, int | None]:
        if reset_reply_interval:
            return None, None
        current_min = getattr(binding, "reply_interval_min_minutes", None)
        current_max = getattr(binding, "reply_interval_max_minutes", None)
        if reply_interval_min_minutes is None and reply_interval_max_minutes is None:
            return current_min, current_max

        resolved_min = reply_interval_min_minutes
        if resolved_min is None:
            resolved_min = current_min if current_min is not None else reply_interval_max_minutes

        resolved_max = reply_interval_max_minutes
        if resolved_max is None:
            resolved_max = current_max if current_max is not None else reply_interval_min_minutes

        return resolved_min, resolved_max

    def _resolve_next_run(
        self,
        last_posted_at: datetime | None,
        next_run_at: datetime | None,
        max_minutes: int | None,
        now: datetime,
    ) -> datetime | None:
        if max_minutes is None:
            return next_run_at
        if next_run_at is not None:
            return next_run_at
        if last_posted_at is None:
            return now
        return last_posted_at + timedelta(minutes=max_minutes)

    def _resolve_state(
        self,
        enabled: bool,
        auto_paused: bool,
        account_ok: bool,
        next_run_at: datetime | None,
        now: datetime,
    ) -> str:
        if not enabled:
            return "disabled"
        if auto_paused:
            return "auto_paused"
        if not account_ok:
            return "blocked"
        if next_run_at is None or next_run_at <= now:
            return "due"
        return "waiting"

    async def _fetch_account_name(self, account: object) -> str | None:
        proxy_url = await proxy_manager.get_proxy_for_account(account.id, self.session)
        tg = TelegramAccountClient(session_name=account.session_name, proxy_url=proxy_url or account.proxy_url)
        try:
            return await tg.get_account_name()
        except Exception as exc:
            logging.getLogger("tg2.services").warning(
                "Failed to resolve account name for account_id=%s: %s",
                account.id,
                exc,
            )
            return None
        finally:
            await tg.disconnect()

    async def _fetch_chat_title(self, account: object, chat_ref: str) -> str | None:
        proxy_url = await proxy_manager.get_proxy_for_account(account.id, self.session)
        tg = TelegramAccountClient(session_name=account.session_name, proxy_url=proxy_url or account.proxy_url)
        try:
            return await tg.get_chat_title(chat_ref)
        except Exception as exc:
            logging.getLogger("tg2.services").warning(
                "Failed to resolve chat title for account_id=%s chat_ref=%s: %s",
                account.id,
                chat_ref,
                exc,
            )
            return None
        finally:
            await tg.disconnect()

    async def _hydrate_binding_metadata(self, binding: object) -> object:
        account = getattr(binding, "account", None) or await self.account_repo.get(binding.account_id)
        if account is None:
            return binding

        if not getattr(account, "account_name", None) and account.is_active and account.auth_status == "authorized":
            account_name = await self._fetch_account_name(account)
            if account_name:
                account = await self.account_repo.update_profile(account, account_name=account_name)
                binding.account = account

        if getattr(binding, "chat_title", None) or not account.is_active or account.auth_status != "authorized":
            return binding

        chat_title = await self._fetch_chat_title(account, binding.chat_ref)
        if not chat_title:
            return binding

        refreshed = await self.repo.set_chat_title(binding, chat_title)
        return refreshed or binding

    async def create_binding(
        self,
        account_id: int,
        chat_ref: str,
        interval_minutes: int,
        reply_interval_min_minutes: int | None = None,
        reply_interval_max_minutes: int | None = None,
        context_message_count: int = 12,
        system_prompt: str | None = None,
    ) -> object:
        account = await self.account_repo.get(account_id)
        if account is None:
            raise ValueError(f"Account {account_id} not found")
        if not account.is_active or account.auth_status != "authorized":
            raise ValueError(f"Account {account_id} is not active and authorized")
        self._validate_settings(
            interval_minutes,
            interval_minutes,
            context_message_count,
            reply_interval_min_minutes,
            reply_interval_max_minutes,
        )

        proxy_url = await proxy_manager.get_proxy_for_account(account.id, self.session)

        tg = TelegramAccountClient(session_name=account.session_name, proxy_url=proxy_url or account.proxy_url)
        chat_title = None
        try:
            is_member = await tg.check_chat_membership(chat_ref)
            if not is_member:
                logging.getLogger("tg2.services").info(
                    "account %s is not a member of %s, joining...", account_id, chat_ref
                )
                chat_ref = await tg.join_chat(chat_ref)
                logging.getLogger("tg2.services").info(
                    "account %s successfully joined %s", account_id, chat_ref
                )
            try:
                chat_title = await tg.get_chat_title(chat_ref)
            except Exception as exc:
                logging.getLogger("tg2.services").warning(
                    "Failed to resolve chat title during binding creation account_id=%s chat_ref=%s: %s",
                    account_id,
                    chat_ref,
                    exc,
                )
        except AuthKeyDuplicatedError:
            await tg._invalidate_session()
            await self.account_repo.mark_status(account, auth_status="revoked", is_active=False)
            await self.repo.auto_pause_for_account(account.id, "сессия недействительна")
            raise ValueError(
                f"Сессия аккаунта использовалась с двух IP одновременно и стала недействительной. "
                f"Требуется повторная авторизация (Аккаунты → Проверить)."
            )
        except Exception as exc:
            raise ValueError(f"Не удалось вступить в чат {chat_ref!r}: {exc}") from exc
        finally:
            await tg.disconnect()

        return await self.repo.create(
            account_id=account_id,
            chat_ref=chat_ref,
            chat_title=chat_title,
            interval_minutes=interval_minutes,
            interval_min_minutes=interval_minutes,
            interval_max_minutes=interval_minutes,
            reply_interval_min_minutes=reply_interval_min_minutes,
            reply_interval_max_minutes=reply_interval_max_minutes,
            context_message_count=context_message_count,
            system_prompt=system_prompt.strip() if system_prompt else None,
        )

    async def list_bindings(self) -> list[object]:
        bindings = await self.repo.list()
        hydrated: list[object] = []
        for binding in bindings:
            hydrated.append(await self._hydrate_binding_metadata(binding))
        return hydrated

    async def get_binding(self, binding_id: int) -> object:
        binding = await self.repo.get(binding_id)
        if binding is None:
            raise ValueError(f"Binding {binding_id} not found")
        return await self._hydrate_binding_metadata(binding)

    async def update_binding_settings(
        self,
        binding_id: int,
        interval_min_minutes: int | None = None,
        interval_max_minutes: int | None = None,
        reply_interval_min_minutes: int | None = None,
        reply_interval_max_minutes: int | None = None,
        context_message_count: int | None = None,
        system_prompt: str | None = None,
        reset_prompt: bool = False,
        reset_reply_interval: bool = False,
    ) -> object:
        binding = await self.get_binding(binding_id)
        new_min = interval_min_minutes if interval_min_minutes is not None else binding.interval_min_minutes
        new_max = interval_max_minutes if interval_max_minutes is not None else binding.interval_max_minutes
        new_context = context_message_count if context_message_count is not None else binding.context_message_count
        new_reply_min, new_reply_max = self._resolve_reply_interval(
            binding,
            reply_interval_min_minutes,
            reply_interval_max_minutes,
            reset_reply_interval,
        )
        self._validate_settings(new_min, new_max, new_context, new_reply_min, new_reply_max)
        cleaned_prompt = system_prompt.strip() if system_prompt else system_prompt
        return await self.repo.update_settings(
            binding,
            interval_min_minutes=interval_min_minutes,
            interval_max_minutes=interval_max_minutes,
            reply_interval_min_minutes=new_reply_min if (reply_interval_min_minutes is not None or reply_interval_max_minutes is not None) else None,
            reply_interval_max_minutes=new_reply_max if (reply_interval_min_minutes is not None or reply_interval_max_minutes is not None) else None,
            context_message_count=context_message_count,
            system_prompt=cleaned_prompt,
            reset_prompt=reset_prompt,
            reset_reply_interval=reset_reply_interval,
        )

    async def delete_binding(self, binding_id: int) -> None:
        binding = await self.repo.get(binding_id)
        if binding is None:
            raise ValueError(f"Binding {binding_id} not found")
        deleted = await self.repo.delete_by_id(binding_id)
        if deleted == 0:
            raise ValueError(f"Binding {binding_id} not found")

    async def list_binding_statuses(self) -> list[dict[str, object]]:
        bindings = await self.repo.list()
        now = datetime.now(timezone.utc)
        items: list[dict[str, object]] = []
        for binding in bindings:
            account = await self.account_repo.get(binding.account_id)
            account_ok = bool(account and account.is_active and account.auth_status == "authorized")

            last_posted_at = self._normalize_dt(binding.last_posted_at)
            next_run_at = self._resolve_next_run(last_posted_at, self._normalize_dt(binding.next_run_at), binding.interval_max_minutes, now)

            last_reply_posted_at = self._normalize_dt(getattr(binding, "last_reply_posted_at", None))
            reply_next_run_at = self._resolve_next_run(
                last_reply_posted_at,
                self._normalize_dt(getattr(binding, "next_reply_run_at", None)),
                getattr(binding, "reply_interval_max_minutes", None),
                now,
            )
            reply_enabled = getattr(binding, "reply_interval_min_minutes", None) is not None and getattr(binding, "reply_interval_max_minutes", None) is not None

            items.append(
                {
                    "binding_id": binding.id,
                    "account_id": binding.account_id,
                    "phone": getattr(account, "phone", None),
                    "chat_ref": binding.chat_ref,
                    "interval_minutes": binding.interval_minutes,
                    "interval_min_minutes": binding.interval_min_minutes,
                    "interval_max_minutes": binding.interval_max_minutes,
                    "reply_interval_min_minutes": getattr(binding, "reply_interval_min_minutes", None),
                    "reply_interval_max_minutes": getattr(binding, "reply_interval_max_minutes", None),
                    "context_message_count": binding.context_message_count,
                    "system_prompt": binding.system_prompt,
                    "is_enabled": binding.is_enabled,
                    "auto_paused": getattr(binding, "auto_paused", False),
                    "auto_pause_reason": getattr(binding, "auto_pause_reason", None),
                    "auto_paused_at": self._normalize_dt(getattr(binding, "auto_paused_at", None)).isoformat() if getattr(binding, "auto_paused_at", None) else None,
                    "account_active": bool(account and account.is_active),
                    "account_auth_status": getattr(account, "auth_status", "missing"),
                    "last_posted_at": last_posted_at.isoformat() if last_posted_at else None,
                    "next_run_at": next_run_at.isoformat() if next_run_at else None,
                    "last_reply_posted_at": last_reply_posted_at.isoformat() if last_reply_posted_at else None,
                    "next_reply_run_at": reply_next_run_at.isoformat() if reply_next_run_at else None,
                    "reply_enabled": reply_enabled,
                    "reply_state": self._resolve_state(reply_enabled, getattr(binding, "auto_paused", False), account_ok, reply_next_run_at, now),
                    "state": self._resolve_state(binding.is_enabled, getattr(binding, "auto_paused", False), account_ok, next_run_at, now),
                }
            )
        return items

    async def due_bindings(self) -> list[object]:
        bindings = await self.repo.list_enabled()
        now = datetime.now(timezone.utc)
        due = []
        for binding in bindings:
            account = await self.account_repo.get(binding.account_id)
            if account is None or not account.is_active or account.auth_status != "authorized":
                continue
            next_run_at = self._resolve_next_run(
                self._normalize_dt(binding.last_posted_at),
                self._normalize_dt(binding.next_run_at),
                binding.interval_max_minutes,
                now,
            )
            if next_run_at is None or next_run_at <= now:
                due.append(binding)
        return due

    async def due_reply_bindings(self) -> list[object]:
        bindings = await self.repo.list_enabled()
        now = datetime.now(timezone.utc)
        due = []
        for binding in bindings:
            account = await self.account_repo.get(binding.account_id)
            if account is None or not account.is_active or account.auth_status != "authorized":
                continue
            if getattr(binding, "reply_interval_min_minutes", None) is None or getattr(binding, "reply_interval_max_minutes", None) is None:
                continue
            next_reply_run_at = self._resolve_next_run(
                self._normalize_dt(getattr(binding, "last_reply_posted_at", None)),
                self._normalize_dt(getattr(binding, "next_reply_run_at", None)),
                getattr(binding, "reply_interval_max_minutes", None),
                now,
            )
            if next_reply_run_at is None or next_reply_run_at <= now:
                due.append(binding)
        return due

    async def touch_posted(self, binding: object) -> None:
        await self.repo.touch_posted(binding)

    async def touch_reply_posted(self, binding: object, target_msg_id: int | None = None) -> None:
        await self.repo.touch_reply_posted(binding, target_msg_id=target_msg_id)

    async def schedule_next_reply_run(self, binding: object) -> None:
        await self.repo.schedule_next_reply_run(binding)


class AppSettingsService:
    MAIN_SYSTEM_PROMPT_KEY = "main_system_prompt"
    OPENAI_MODEL_KEY = "openai_model"

    def __init__(self, session: AsyncSession) -> None:
        self.repo = AppSettingsRepository(session)

    async def get_main_system_prompt(self) -> str | None:
        return await self.repo.get_value(self.MAIN_SYSTEM_PROMPT_KEY)

    async def get_effective_main_system_prompt(self) -> str:
        prompt = await self.get_main_system_prompt()
        return prompt or DEFAULT_MAIN_SYSTEM_PROMPT

    async def set_main_system_prompt(self, prompt: str) -> str:
        cleaned_prompt = prompt.strip()
        if not cleaned_prompt:
            raise ValueError("main_system_prompt cannot be empty")
        await self.repo.set_value(self.MAIN_SYSTEM_PROMPT_KEY, cleaned_prompt)
        return cleaned_prompt

    async def reset_main_system_prompt(self) -> None:
        await self.repo.delete(self.MAIN_SYSTEM_PROMPT_KEY)

    async def get_openai_model(self) -> str | None:
        return await self.repo.get_value(self.OPENAI_MODEL_KEY)

    async def get_effective_openai_model(self) -> str:
        model = await self.get_openai_model()
        return model or settings.openai_model

    async def set_openai_model(self, model: str) -> str:
        cleaned_model = model.strip()
        if not cleaned_model:
            raise ValueError("openai_model cannot be empty")
        await self.repo.set_value(self.OPENAI_MODEL_KEY, cleaned_model)
        return cleaned_model

    async def reset_openai_model(self) -> None:
        await self.repo.delete(self.OPENAI_MODEL_KEY)


class ChatAutomationService:
    RECENT_REPLY_WINDOW = 10
    _DB_LOCK_RETRY_ATTEMPTS = 3
    _DB_LOCK_RETRY_DELAY_SECONDS = 0.5

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.account_repo = AccountRepository(session)
        self.binding_repo = BindingRepository(session)
        self.message_log_repo = MessageLogRepository(session)
        self.reply_task_repo = ReplyTaskRepository(session)
        self.app_settings = AppSettingsService(session)
        self.ai = AIService()

    @staticmethod
    def _build_context_from_detailed(messages: list[dict], limit: int) -> list[dict[str, object]]:
        context = [
            {
                "sender": message.get("sender", "unknown"),
                "text": message.get("message", ""),
                "date": message.get("date"),
            }
            for message in messages
            if str(message.get("message", "")).strip()
        ]
        return context[-limit:]

    @classmethod
    def _pick_recent_reply_target(cls, messages: list[dict], last_target_msg_id: int | None) -> dict | None:
        candidates = [
            message
            for message in messages[-cls.RECENT_REPLY_WINDOW:]
            if str(message.get("message", "")).strip()
        ]
        if not candidates:
            return None
        if len(candidates) > 1 and last_target_msg_id is not None:
            filtered = [message for message in candidates if message.get("id") != last_target_msg_id]
            if filtered:
                candidates = filtered
        import random
        return random.choice(candidates)

    @staticmethod
    def _is_transient_db_lock_error(exc: Exception) -> bool:
        return "database is locked" in str(exc).lower()

    async def _run_with_db_lock_retry(self, operation) -> None:
        last_exc: Exception | None = None
        for attempt in range(1, self._DB_LOCK_RETRY_ATTEMPTS + 1):
            try:
                await operation()
                return
            except Exception as exc:
                if not self._is_transient_db_lock_error(exc) or attempt >= self._DB_LOCK_RETRY_ATTEMPTS:
                    raise
                last_exc = exc
                await asyncio.sleep(self._DB_LOCK_RETRY_DELAY_SECONDS)
        if last_exc is not None:
            raise last_exc

    async def force_generate_and_send(
        self,
        account_id: int,
        chat_ref: str,
        context_message_count: int = 12,
        system_prompt: str | None = None,
    ) -> str:
        account = await self.account_repo.get(account_id)
        if account is None:
            raise ValueError(f"Account {account_id} not found")
        if account.auth_status != "authorized" or not account.is_active:
            raise ValueError(f"Account {account_id} is not active and authorized")

        proxy_url = await proxy_manager.get_proxy_for_account(account.id, self.session)
        main_system_prompt = await self.app_settings.get_main_system_prompt()
        model = await self.app_settings.get_effective_openai_model()

        tg = TelegramAccountClient(session_name=account.session_name, proxy_url=proxy_url or account.proxy_url)
        try:
            try:
                if not await tg.check_chat_membership(chat_ref):
                    chat_ref = await tg.join_chat(chat_ref)
            except Exception as e:
                logging.getLogger("tg2.services").warning("Auto-join failed for %s: %s", chat_ref, e)

            context = await tg.fetch_recent_messages(chat_ref, limit=context_message_count)
            content = await self.ai.generate_reply(
                chat_ref=chat_ref,
                context_messages=context,
                system_prompt=system_prompt,
                main_system_prompt=main_system_prompt,
                model=model,
                reaction_type="message",
                character=getattr(account, "character", None),
            )
            if not content:
                return ""

            msg_id = await tg.send_message(chat_ref, content)
            await self._run_with_db_lock_retry(
                lambda: self.message_log_repo.add(
                    account_id=account_id,
                    chat_ref=chat_ref,
                    content=content,
                    msg_id=msg_id,
                )
            )
            return content
        finally:
            await tg.disconnect()

    async def generate_and_send(
        self,
        account_id: int,
        chat_ref: str,
        context_message_count: int = 12,
        system_prompt: str | None = None,
        last_bot_post_at: datetime | None = None,
    ) -> str:
        account = await self.account_repo.get(account_id)
        if account is None:
            raise ValueError(f"Account {account_id} not found")
        if account.auth_status != "authorized" or not account.is_active:
            raise ValueError(f"Account {account_id} is not active and authorized")

        proxy_url = await proxy_manager.get_proxy_for_account(account.id, self.session)
        main_system_prompt = await self.app_settings.get_main_system_prompt()
        model = await self.app_settings.get_effective_openai_model()

        tg = TelegramAccountClient(session_name=account.session_name, proxy_url=proxy_url or account.proxy_url)
        try:
            try:
                if not await tg.check_chat_membership(chat_ref):
                    chat_ref = await tg.join_chat(chat_ref)
            except Exception as e:
                logging.getLogger("tg2.services").warning("Auto-join failed for %s: %s", chat_ref, e)

            context = await tg.fetch_recent_messages(chat_ref, limit=context_message_count)

            ctx = DecisionContext(
                messages=context,
                last_bot_post_at=last_bot_post_at,
                last_message_at=context[-1]["date"] if context else None,
                bot_name=getattr(account, "username", None),
                character=getattr(account, "character", None),
            )
            result = decision_engine.decide(ctx)
            if not result.should_send:
                logging.getLogger("tg2.scheduler").info(
                    "decision=SKIP account_id=%s chat_ref=%s reason=%s",
                    account_id, chat_ref, result.reason,
                )
                return ""

            content = await self.ai.generate_reply(
                chat_ref=chat_ref,
                context_messages=context,
                system_prompt=system_prompt,
                main_system_prompt=main_system_prompt,
                model=model,
                reaction_type=result.reaction_type,
                character=getattr(account, "character", None),
            )
            if not content:
                return ""
            msg_id = await tg.send_message(chat_ref, content)
            await self.message_log_repo.add(account_id=account_id, chat_ref=chat_ref, content=content, msg_id=msg_id)
            return content
        finally:
            await tg.disconnect()

    async def generate_and_send_binding(self, binding: object, force: bool = False) -> str:
        if force:
            return await self.force_generate_and_send_binding(binding)
        last_bot_post_at = getattr(binding, "last_posted_at", None)
        if last_bot_post_at is not None and last_bot_post_at.tzinfo is None:
            last_bot_post_at = last_bot_post_at.replace(tzinfo=timezone.utc)
        return await self.generate_and_send(
            account_id=binding.account_id,
            chat_ref=binding.chat_ref,
            context_message_count=binding.context_message_count,
            system_prompt=binding.system_prompt,
            last_bot_post_at=last_bot_post_at,
        )

    async def force_generate_and_send_binding(self, binding: object) -> str:
        content = await self.force_generate_and_send(
            account_id=binding.account_id,
            chat_ref=binding.chat_ref,
            context_message_count=binding.context_message_count,
            system_prompt=binding.system_prompt,
        )
        if content:
            await self._run_with_db_lock_retry(lambda: self.binding_repo.touch_posted(binding))
        return content

    async def generate_and_send_recent_reply(self, binding: object) -> tuple[str, int] | None:
        account = await self.account_repo.get(binding.account_id)
        if account is None or account.auth_status != "authorized" or not account.is_active:
            return None

        proxy_url = await proxy_manager.get_proxy_for_account(account.id, self.session)
        main_system_prompt = await self.app_settings.get_main_system_prompt()
        model = await self.app_settings.get_effective_openai_model()
        tg = TelegramAccountClient(session_name=account.session_name, proxy_url=proxy_url or account.proxy_url)
        try:
            try:
                if not await tg.check_chat_membership(binding.chat_ref):
                    await tg.join_chat(binding.chat_ref)
            except Exception as e:
                logging.getLogger("tg2.services").warning("Auto-join failed for %s: %s", binding.chat_ref, e)

            recent_limit = max(binding.context_message_count, self.RECENT_REPLY_WINDOW)
            recent_messages = await tg.fetch_recent_detailed(binding.chat_ref, limit=recent_limit)
            target = self._pick_recent_reply_target(recent_messages, getattr(binding, "last_reply_target_msg_id", None))
            if target is None:
                return None

            context = self._build_context_from_detailed(recent_messages, binding.context_message_count)
            content = await self.ai.generate_reply(
                chat_ref=binding.chat_ref,
                context_messages=context,
                system_prompt=binding.system_prompt,
                main_system_prompt=main_system_prompt,
                model=model,
                reaction_type="reply",
                character=getattr(account, "character", None),
                reply_target={
                    "sender": target.get("sender", "unknown"),
                    "text": target.get("message", ""),
                },
            )
            if not content:
                return None

            msg_id = await tg.send_message(binding.chat_ref, content, reply_to=target["id"])
            await self.message_log_repo.add(
                account_id=binding.account_id,
                chat_ref=binding.chat_ref,
                content=content,
                msg_id=msg_id,
            )
            return content, int(target["id"])
        finally:
            await tg.disconnect()

    async def create_group(self, account_id: int, description: str) -> str:
        account = await self.account_repo.get(account_id)
        if account is None:
            raise ValueError(f"Account {account_id} not found")
        if account.auth_status != "authorized" or not account.is_active:
            raise ValueError(f"Account {account_id} is not active and authorized")

        model = await self.app_settings.get_effective_openai_model()
        group_details = await self.ai.generate_group_details(description, model=model)
        title = group_details.get("title", "Новая группа")
        about = group_details.get("about", description)[:255]
        username = group_details.get("username", None)
        messages = group_details.get("messages", [])

        proxy_url = await proxy_manager.get_proxy_for_account(account.id, self.session)

        tg = TelegramAccountClient(session_name=account.session_name, proxy_url=proxy_url or account.proxy_url)
        try:
            chat_ref = await tg.create_group(title=title, about=about, username=username, pinned_post=None)

            import asyncio
            for msg in messages[:10]:
                if msg and isinstance(msg, str):
                    msg_id = await tg.send_message(chat_ref, msg)
                    await self.message_log_repo.add(account_id=account_id, chat_ref=chat_ref, content=msg, msg_id=msg_id)
                    await asyncio.sleep(2)

            return chat_ref
        finally:
            await tg.disconnect()

    async def poll_for_replies(self, binding: object) -> None:
        account = await self.account_repo.get(binding.account_id)
        if account is None or account.auth_status != "authorized" or not account.is_active:
            return

        proxy_url = await proxy_manager.get_proxy_for_account(binding.account_id, self.session)

        tg = TelegramAccountClient(session_name=account.session_name, proxy_url=proxy_url or account.proxy_url)
        try:
            recent_msgs = await tg.fetch_recent_detailed(binding.chat_ref, limit=15)
            for msg in recent_msgs:
                reply_to = msg.get("reply_to_msg_id")

                if reply_to:
                    our_msg = await self.message_log_repo.get_by_msg_id(binding.account_id, binding.chat_ref, reply_to)
                    if our_msg:
                        existing_task = await self.reply_task_repo.get_by_trigger(binding.account_id, binding.chat_ref, msg["id"])
                        if not existing_task:
                            import random
                            delay_minutes = random.randint(2, 8)
                            execute_at = datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)
                            await self.reply_task_repo.create(
                                account_id=binding.account_id,
                                chat_ref=binding.chat_ref,
                                trigger_msg_id=msg["id"],
                                execute_at=execute_at
                            )
                            logging.getLogger("tg2.scheduler").info(
                                "scheduled reply account_id=%s chat_ref=%s trigger_msg_id=%s delay=%sm",
                                binding.account_id, binding.chat_ref, msg["id"], delay_minutes
                            )
        except Exception as e:
            logging.getLogger("tg2.scheduler").error(f"poll_for_replies failed for account {binding.account_id}: {e}")
        finally:
            await tg.disconnect()

    async def process_due_reply_tasks(self) -> None:
        tasks = await self.reply_task_repo.list_due_tasks(datetime.now(timezone.utc))
        if not tasks:
            return

        import asyncio
        semaphore = asyncio.Semaphore(2)

        async def process_one(task):
            async with semaphore:
                binding = await self.binding_repo.get_by_account_and_chat(task.account_id, task.chat_ref)
                if not binding:
                    await self.reply_task_repo.mark_completed(task)
                    return

                account = await self.account_repo.get(task.account_id)
                if not account or not account.is_active or account.auth_status != "authorized":
                    return

                proxy_url = await proxy_manager.get_proxy_for_account(account.id, self.session)
                main_system_prompt = await self.app_settings.get_main_system_prompt()
                model = await self.app_settings.get_effective_openai_model()

                tg = TelegramAccountClient(session_name=account.session_name, proxy_url=proxy_url or account.proxy_url)
                try:
                    detailed = await tg.fetch_recent_detailed(task.chat_ref, limit=max(binding.context_message_count, 15))
                    context = self._build_context_from_detailed(detailed, binding.context_message_count)
                    target = next((item for item in detailed if item.get("id") == task.trigger_msg_id), None)
                    ctx = DecisionContext(
                        messages=context,
                        last_bot_post_at=getattr(binding, "last_posted_at", None),
                        last_message_at=context[-1]["date"] if context else None,
                        bot_name=getattr(account, "username", None),
                    )
                    decision = decision_engine.decide(ctx)
                    if not decision.should_send:
                        logging.getLogger("tg2.scheduler").info(
                            "reply_decision=SKIP account_id=%s chat_ref=%s reason=%s",
                            task.account_id, task.chat_ref, decision.reason,
                        )
                        await self.reply_task_repo.mark_completed(task)
                        return

                    content = await self.ai.generate_reply(
                        chat_ref=task.chat_ref,
                        context_messages=context,
                        system_prompt=binding.system_prompt,
                        main_system_prompt=main_system_prompt,
                        model=model,
                        reaction_type=decision.reaction_type,
                        character=getattr(account, "character", None),
                        reply_target=(
                            {
                                "sender": target.get("sender", "unknown"),
                                "text": target.get("message", ""),
                            }
                            if target
                            else None
                        ),
                    )
                    if not content:
                        await self.reply_task_repo.mark_completed(task)
                        return
                    msg_id = await tg.send_message(task.chat_ref, content, reply_to=task.trigger_msg_id)
                    await self.message_log_repo.add(account_id=task.account_id, chat_ref=task.chat_ref, content=content, msg_id=msg_id)
                    await self.reply_task_repo.mark_completed(task)
                    await self.binding_repo.touch_posted(binding)
                    logging.getLogger("tg2.scheduler").info(
                        "sent_reply account_id=%s chat_ref=%s trigger_msg_id=%s msg_id=%s",
                        task.account_id, task.chat_ref, task.trigger_msg_id, msg_id
                    )
                except Exception as e:
                    logging.getLogger("tg2.scheduler").error(f"process_due_reply_tasks failed for task {task.id}: {e}")
                finally:
                    await tg.disconnect()

        await asyncio.gather(*(process_one(task) for task in tasks))


class CharacterService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = CharacterRepository(session)

    async def list_characters(self) -> list[object]:
        return await self.repo.list()

    async def ensure_default_characters(self) -> None:
        count = await self.repo.count()
        if count == 0:
            logging.getLogger("tg2.services").info("Initializing default characters...")
            for char_data in DEFAULT_CHARACTERS:
                await self.repo.create(**char_data)

    async def assign_character(self, account_id: int, character_id: int) -> None:
        account_repo = AccountRepository(self.session)
        account = await account_repo.get(account_id)
        if account:
            account.character_id = character_id
            await self.session.commit()




