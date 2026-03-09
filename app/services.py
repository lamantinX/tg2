import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import AIService
from app.decision_engine import DecisionContext, decision_engine
from app.repositories import AccountRepository, BindingRepository, MessageLogRepository, ReplyTaskRepository
from app.telegram_client import TelegramAccountClient


audit_logger = logging.getLogger("tg2.audit")


def configure_audit_logger() -> None:
    log_path = Path("data/logs/account_audit.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if not audit_logger.handlers:
        handler = logging.FileHandler(log_path, encoding="utf-8")
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        handler.setFormatter(formatter)
        audit_logger.addHandler(handler)
        audit_logger.setLevel(logging.INFO)


class AccountService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = AccountRepository(session)
        self.binding_repo = BindingRepository(session)
        configure_audit_logger()

    async def create_account(self, phone: str, proxy_url: str | None) -> object:
        existing = await self.repo.get_by_phone(phone)
        if existing:
            return existing
        session_name = phone.replace("+", "").replace(" ", "")
        return await self.repo.create(phone=phone, session_name=session_name, proxy_url=proxy_url)

    async def list_accounts(self) -> list[object]:
        return await self.repo.list()

    async def request_login_code(self, account_id: int) -> str:
        account = await self.repo.get(account_id)
        if account is None:
            raise ValueError(f"Account {account_id} not found")

        tg = TelegramAccountClient(session_name=account.session_name, proxy_url=account.proxy_url)
        try:
            if await tg.is_authorized():
                await self.repo.mark_authorized(account)
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

        tg = TelegramAccountClient(session_name=account.session_name, proxy_url=account.proxy_url)
        try:
            status = await tg.complete_login(
                phone=account.phone,
                code=code,
                phone_code_hash=account.phone_code_hash or "",
                password=password,
            )
            if status == "authorized":
                await self.repo.mark_authorized(account)
            return status
        finally:
            await tg.disconnect()

    async def complete_password_login(self, account_id: int, password: str) -> str:
        account = await self.repo.get(account_id)
        if account is None:
            raise ValueError(f"Account {account_id} not found")
        if account.auth_status not in {"code_requested", "authorized"} and not account.phone_code_hash:
            raise ValueError("Login code was not requested for this account")

        tg = TelegramAccountClient(session_name=account.session_name, proxy_url=account.proxy_url)
        try:
            status = await tg.complete_password_login(password=password)
            if status == "authorized":
                await self.repo.mark_authorized(account)
            return status
        finally:
            await tg.disconnect()

    async def audit_accounts(self) -> dict[str, object]:
        accounts = await self.repo.list()
        report: dict[str, object] = {
            "audited": 0,
            "active": 0,
            "inactive": 0,
            "cleaned_bindings": 0,
            "details": [],
        }
        for account in accounts:
            report["audited"] += 1
            tg = TelegramAccountClient(session_name=account.session_name, proxy_url=account.proxy_url)
            try:
                status = await tg.check_health()
            finally:
                await tg.disconnect()

            auth_status = str(status["auth_status"])
            is_active = bool(status["is_active"])
            reason = str(status["reason"])
            await self.repo.mark_status(account, auth_status=auth_status, is_active=is_active)

            cleaned = 0
            if not is_active or auth_status != "authorized":
                cleaned = await self.binding_repo.delete_by_account_id(account.id)
                report["inactive"] += 1
                report["cleaned_bindings"] += cleaned
            else:
                report["active"] += 1

            detail = {
                "account_id": account.id,
                "phone": account.phone,
                "auth_status": auth_status,
                "is_active": is_active,
                "cleaned_bindings": cleaned,
                "reason": reason,
            }
            report["details"].append(detail)

        audit_logger.info("account_audit %s", json.dumps(report, ensure_ascii=False))
        return report


class BindingService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = BindingRepository(session)
        self.account_repo = AccountRepository(session)

    def _normalize_dt(self, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    def _validate_settings(
        self,
        interval_min_minutes: int | None,
        interval_max_minutes: int | None,
        context_message_count: int | None,
    ) -> None:
        if interval_min_minutes is not None and interval_min_minutes < 1:
            raise ValueError("interval_min_minutes must be >= 1")
        if interval_max_minutes is not None and interval_max_minutes < 1:
            raise ValueError("interval_max_minutes must be >= 1")
        if interval_min_minutes is not None and interval_max_minutes is not None and interval_min_minutes > interval_max_minutes:
            raise ValueError("interval_min_minutes cannot be greater than interval_max_minutes")
        if context_message_count is not None and not 1 <= context_message_count <= 200:
            raise ValueError("context_message_count must be between 1 and 200")

    async def create_binding(
        self,
        account_id: int,
        chat_ref: str,
        interval_minutes: int,
        context_message_count: int = 12,
        system_prompt: str | None = None,
    ) -> object:
        account = await self.account_repo.get(account_id)
        if account is None:
            raise ValueError(f"Account {account_id} not found")
        if not account.is_active or account.auth_status != "authorized":
            raise ValueError(f"Account {account_id} is not active and authorized")
        self._validate_settings(interval_minutes, interval_minutes, context_message_count)

        # Проверяем участие аккаунта в чате и вступаем, если он не состоит в нём
        tg = TelegramAccountClient(session_name=account.session_name, proxy_url=account.proxy_url)
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
        except Exception as exc:
            raise ValueError(f"Не удалось вступить в чат {chat_ref!r}: {exc}") from exc
        finally:
            await tg.disconnect()

        return await self.repo.create(
            account_id=account_id,
            chat_ref=chat_ref,
            interval_minutes=interval_minutes,
            interval_min_minutes=interval_minutes,
            interval_max_minutes=interval_minutes,
            context_message_count=context_message_count,
            system_prompt=system_prompt.strip() if system_prompt else None,
        )

    async def list_bindings(self) -> list[object]:
        return await self.repo.list()

    async def get_binding(self, binding_id: int) -> object:
        binding = await self.repo.get(binding_id)
        if binding is None:
            raise ValueError(f"Binding {binding_id} not found")
        return binding

    async def update_binding_settings(
        self,
        binding_id: int,
        interval_min_minutes: int | None = None,
        interval_max_minutes: int | None = None,
        context_message_count: int | None = None,
        system_prompt: str | None = None,
        reset_prompt: bool = False,
    ) -> object:
        binding = await self.get_binding(binding_id)
        new_min = interval_min_minutes if interval_min_minutes is not None else binding.interval_min_minutes
        new_max = interval_max_minutes if interval_max_minutes is not None else binding.interval_max_minutes
        new_context = context_message_count if context_message_count is not None else binding.context_message_count
        self._validate_settings(new_min, new_max, new_context)
        cleaned_prompt = system_prompt.strip() if system_prompt else system_prompt
        return await self.repo.update_settings(
            binding,
            interval_min_minutes=interval_min_minutes,
            interval_max_minutes=interval_max_minutes,
            context_message_count=context_message_count,
            system_prompt=cleaned_prompt,
            reset_prompt=reset_prompt,
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
            last_posted_at = self._normalize_dt(binding.last_posted_at)
            next_run_at = self._normalize_dt(binding.next_run_at)
            if next_run_at is None and binding.is_enabled:
                if last_posted_at is None:
                    next_run_at = now
                else:
                    next_run_at = last_posted_at + timedelta(minutes=binding.interval_max_minutes)

            if not binding.is_enabled:
                state = "disabled"
            elif account is None or not account.is_active or account.auth_status != "authorized":
                state = "blocked"
            elif next_run_at is None or next_run_at <= now:
                state = "due"
            else:
                state = "waiting"

            items.append(
                {
                    "binding_id": binding.id,
                    "account_id": binding.account_id,
                    "phone": getattr(account, "phone", None),
                    "chat_ref": binding.chat_ref,
                    "interval_minutes": binding.interval_minutes,
                    "interval_min_minutes": binding.interval_min_minutes,
                    "interval_max_minutes": binding.interval_max_minutes,
                    "context_message_count": binding.context_message_count,
                    "system_prompt": binding.system_prompt,
                    "is_enabled": binding.is_enabled,
                    "account_active": bool(account and account.is_active),
                    "account_auth_status": getattr(account, "auth_status", "missing"),
                    "last_posted_at": last_posted_at.isoformat() if last_posted_at else None,
                    "next_run_at": next_run_at.isoformat() if next_run_at else None,
                    "state": state,
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
            next_run_at = self._normalize_dt(binding.next_run_at)
            last_posted_at = self._normalize_dt(binding.last_posted_at)
            if next_run_at is None:
                if last_posted_at is None:
                    due.append(binding)
                    continue
                next_run_at = last_posted_at + timedelta(minutes=binding.interval_max_minutes)
            if next_run_at <= now:
                due.append(binding)
        return due

    async def touch_posted(self, binding: object) -> None:
        await self.repo.touch_posted(binding)


class ChatAutomationService:
    def __init__(self, session: AsyncSession) -> None:
        self.account_repo = AccountRepository(session)
        self.binding_repo = BindingRepository(session)
        self.message_log_repo = MessageLogRepository(session)
        self.reply_task_repo = ReplyTaskRepository(session)
        self.ai = AIService()

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

        tg = TelegramAccountClient(session_name=account.session_name, proxy_url=account.proxy_url)
        try:
            # Проверяем участие и вступаем, если нужно (например, при ручном вызове generate_once)
            try:
                if not await tg.check_chat_membership(chat_ref):
                    chat_ref = await tg.join_chat(chat_ref)
            except Exception as e:
                logging.getLogger("tg2.services").warning("Auto-join failed for %s: %s", chat_ref, e)

            context = await tg.fetch_recent_messages(chat_ref, limit=context_message_count)

            # ── Алгоритм принятия решения ─────────────────────────────────
            ctx = DecisionContext(
                messages=context,
                last_bot_post_at=last_bot_post_at,
                last_message_at=None,   # нет метаданных в текстовом списке
                bot_name=getattr(account, "username", None),
            )
            result = decision_engine.decide(ctx)
            if not result.should_send:
                logging.getLogger("tg2.scheduler").info(
                    "decision=SKIP account_id=%s chat_ref=%s reason=%s",
                    account_id, chat_ref, result.reason,
                )
                return ""  # бот молчит
            # ─────────────────────────────────────────────────────────────

            content = await self.ai.generate_reply(
                chat_ref=chat_ref,
                context_messages=context,
                system_prompt=system_prompt,
                reaction_type=result.reaction_type,
            )
            msg_id = await tg.send_message(chat_ref, content)
            await self.message_log_repo.add(account_id=account_id, chat_ref=chat_ref, content=content, msg_id=msg_id)
            return content
        finally:
            await tg.disconnect()

    async def generate_and_send_binding(self, binding: object) -> str:
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

    async def create_group(self, account_id: int, description: str) -> str:
        account = await self.account_repo.get(account_id)
        if account is None:
            raise ValueError(f"Account {account_id} not found")
        if account.auth_status != "authorized" or not account.is_active:
            raise ValueError(f"Account {account_id} is not active and authorized")

        group_details = await self.ai.generate_group_details(description)
        title = group_details.get("title", "Новая группа")
        about = group_details.get("about", description)[:255]
        username = group_details.get("username", None)
        messages = group_details.get("messages", [])

        tg = TelegramAccountClient(session_name=account.session_name, proxy_url=account.proxy_url)
        try:
            chat_ref = await tg.create_group(title=title, about=about, username=username, pinned_post=None)
            
            import asyncio
            for msg in messages[:10]:
                if msg and isinstance(msg, str):
                    msg_id = await tg.send_message(chat_ref, msg)
                    await self.message_log_repo.add(account_id=account_id, chat_ref=chat_ref, content=msg, msg_id=msg_id)
                    await asyncio.sleep(2)  # Небольшая задержка между сообщениями
                    
            return chat_ref
        finally:
            await tg.disconnect()

    async def poll_for_replies(self, binding: object) -> None:
        account = await self.account_repo.get(binding.account_id)
        if account is None or account.auth_status != "authorized" or not account.is_active:
            return

        tg = TelegramAccountClient(session_name=account.session_name, proxy_url=account.proxy_url)
        try:
            recent_msgs = await tg.fetch_recent_detailed(binding.chat_ref, limit=15)
            for msg in recent_msgs:
                reply_to = msg.get("reply_to_msg_id")
                
                # Check if it replied to our message
                if reply_to:
                    our_msg = await self.message_log_repo.get_by_msg_id(binding.account_id, binding.chat_ref, reply_to)
                    if our_msg:
                        # Ensure we haven't already tasks for this user message
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

                tg = TelegramAccountClient(session_name=account.session_name, proxy_url=account.proxy_url)
                try:
                    context = await tg.fetch_recent_messages(task.chat_ref, limit=binding.context_message_count)
                    ctx = DecisionContext(
                        messages=context,
                        last_bot_post_at=getattr(binding, "last_posted_at", None),
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
                        reaction_type=decision.reaction_type,
                    )
                    msg_id = await tg.send_message(task.chat_ref, content, reply_to=task.trigger_msg_id)
                    await self.message_log_repo.add(account_id=task.account_id, chat_ref=task.chat_ref, content=content, msg_id=msg_id)
                    await self.reply_task_repo.mark_completed(task)
                    logging.getLogger("tg2.scheduler").info(
                        "sent_reply account_id=%s chat_ref=%s trigger_msg_id=%s msg_id=%s",
                        task.account_id, task.chat_ref, task.trigger_msg_id, msg_id
                    )
                except Exception as e:
                    logging.getLogger("tg2.scheduler").error(f"process_due_reply_tasks failed for task {task.id}: {e}")
                finally:
                    await tg.disconnect()

        await asyncio.gather(*(process_one(task) for task in tasks))
