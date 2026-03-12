from __future__ import annotations

from datetime import timedelta
import random

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import AppSetting, Character, ChatBinding, MessageLog, ReplyTask, TelegramAccount, utcnow


class AccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, phone: str, session_name: str, proxy_url: str | None, character_id: int | None = None) -> TelegramAccount:
        account = TelegramAccount(phone=phone, session_name=session_name, proxy_url=proxy_url, character_id=character_id)
        self.session.add(account)
        await self.session.commit()
        await self.session.refresh(account)
        return account

    async def get(self, account_id: int) -> TelegramAccount | None:
        query = select(TelegramAccount).where(TelegramAccount.id == account_id).options(selectinload(TelegramAccount.character))
        return await self.session.scalar(query)

    async def get_by_phone(self, phone: str) -> TelegramAccount | None:
        query = select(TelegramAccount).where(TelegramAccount.phone == phone).options(selectinload(TelegramAccount.character))
        return await self.session.scalar(query)

    async def list(self) -> list[TelegramAccount]:
        query = select(TelegramAccount).order_by(TelegramAccount.id.desc()).options(selectinload(TelegramAccount.character))
        return list(await self.session.scalars(query))

    async def update_login_code_hash(self, account: TelegramAccount, phone_code_hash: str) -> TelegramAccount:
        account.phone_code_hash = phone_code_hash
        account.auth_status = "code_requested"
        await self.session.commit()
        await self.session.refresh(account)
        return account

    async def mark_authorized(self, account: TelegramAccount) -> TelegramAccount:
        account.auth_status = "authorized"
        account.phone_code_hash = None
        account.is_active = True
        account.last_login_at = utcnow()
        await self.session.commit()
        await self.session.refresh(account)
        return account

    async def mark_status(self, account: TelegramAccount, auth_status: str, is_active: bool) -> TelegramAccount:
        account.auth_status = auth_status
        account.is_active = is_active
        if auth_status == "authorized":
            account.last_login_at = utcnow()
        await self.session.commit()
        await self.session.refresh(account)
        return account

    async def update_profile(self, account: TelegramAccount, account_name: str | None = None) -> TelegramAccount:
        account.account_name = account_name
        await self.session.commit()
        await self.session.refresh(account)
        return account

class BindingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _account_load_options():
        return (selectinload(ChatBinding.account).selectinload(TelegramAccount.character),)

    @staticmethod
    def _schedule_minutes(min_minutes: int, max_minutes: int) -> int:
        return random.randint(min_minutes, max_minutes)

    async def create(
        self,
        account_id: int,
        chat_ref: str,
        chat_title: str | None,
        interval_minutes: int,
        interval_min_minutes: int,
        interval_max_minutes: int,
        reply_interval_min_minutes: int | None,
        reply_interval_max_minutes: int | None,
        context_message_count: int,
        system_prompt: str | None,
    ) -> ChatBinding:
        now = utcnow()
        next_reply_run_at = None
        if reply_interval_min_minutes is not None and reply_interval_max_minutes is not None:
            next_reply_run_at = now + timedelta(minutes=self._schedule_minutes(reply_interval_min_minutes, reply_interval_max_minutes))

        binding = ChatBinding(
            account_id=account_id,
            chat_ref=chat_ref,
            chat_title=chat_title,
            interval_minutes=interval_minutes,
            interval_min_minutes=interval_min_minutes,
            interval_max_minutes=interval_max_minutes,
            reply_interval_min_minutes=reply_interval_min_minutes,
            reply_interval_max_minutes=reply_interval_max_minutes,
            context_message_count=context_message_count,
            system_prompt=system_prompt,
            next_run_at=now + timedelta(minutes=interval_max_minutes),
            next_reply_run_at=next_reply_run_at,
        )
        self.session.add(binding)
        await self.session.commit()
        return await self.get(binding.id)

    async def list(self) -> list[ChatBinding]:
        query = select(ChatBinding).order_by(ChatBinding.id.desc()).options(*self._account_load_options())
        return list(await self.session.scalars(query))

    async def list_enabled(self) -> list[ChatBinding]:
        query = select(ChatBinding).where(ChatBinding.is_enabled.is_(True)).options(*self._account_load_options())
        return list(await self.session.scalars(query))

    async def get(self, binding_id: int) -> ChatBinding | None:
        query = select(ChatBinding).where(ChatBinding.id == binding_id).options(*self._account_load_options())
        return await self.session.scalar(query)

    async def get_by_account_and_chat(self, account_id: int, chat_ref: str) -> ChatBinding | None:
        query = select(ChatBinding).where(
            ChatBinding.account_id == account_id,
            ChatBinding.chat_ref == chat_ref,
        ).options(*self._account_load_options())
        return await self.session.scalar(query)

    async def update_settings(
        self,
        binding: ChatBinding,
        interval_min_minutes: int | None = None,
        interval_max_minutes: int | None = None,
        reply_interval_min_minutes: int | None = None,
        reply_interval_max_minutes: int | None = None,
        context_message_count: int | None = None,
        system_prompt: str | None = None,
        reset_prompt: bool = False,
        reset_reply_interval: bool = False,
    ) -> ChatBinding:
        if interval_min_minutes is not None:
            binding.interval_min_minutes = interval_min_minutes
        if interval_max_minutes is not None:
            binding.interval_max_minutes = interval_max_minutes
        binding.interval_minutes = binding.interval_max_minutes

        reply_interval_changed = False
        if reset_reply_interval:
            binding.reply_interval_min_minutes = None
            binding.reply_interval_max_minutes = None
            binding.next_reply_run_at = None
            binding.last_reply_target_msg_id = None
        else:
            if reply_interval_min_minutes is not None:
                binding.reply_interval_min_minutes = reply_interval_min_minutes
                reply_interval_changed = True
            if reply_interval_max_minutes is not None:
                binding.reply_interval_max_minutes = reply_interval_max_minutes
                reply_interval_changed = True
            if reply_interval_changed:
                if binding.reply_interval_min_minutes is None or binding.reply_interval_max_minutes is None:
                    binding.next_reply_run_at = None
                else:
                    now = utcnow()
                    minutes = self._schedule_minutes(binding.reply_interval_min_minutes, binding.reply_interval_max_minutes)
                    binding.next_reply_run_at = now + timedelta(minutes=minutes)

        if context_message_count is not None:
            binding.context_message_count = context_message_count
        if reset_prompt:
            binding.system_prompt = None
        elif system_prompt is not None:
            binding.system_prompt = system_prompt
        await self.session.commit()
        return await self.get(binding.id)

    async def set_chat_title(self, binding: ChatBinding, chat_title: str | None) -> ChatBinding:
        binding.chat_title = chat_title
        await self.session.commit()
        return await self.get(binding.id)

    async def delete_by_id(self, binding_id: int) -> int:
        query = delete(ChatBinding).where(ChatBinding.id == binding_id)
        result = await self.session.execute(query)
        await self.session.commit()
        return int(result.rowcount or 0)

    async def delete_by_account_id(self, account_id: int) -> int:
        query = delete(ChatBinding).where(ChatBinding.account_id == account_id)
        result = await self.session.execute(query)
        await self.session.commit()
        return int(result.rowcount or 0)

    async def touch_posted(self, binding: ChatBinding) -> None:
        now = utcnow()
        binding.last_posted_at = now
        minutes = self._schedule_minutes(binding.interval_min_minutes, binding.interval_max_minutes)
        binding.interval_minutes = minutes
        binding.next_run_at = now + timedelta(minutes=minutes)
        await self.session.commit()

    async def touch_reply_posted(self, binding: ChatBinding, target_msg_id: int | None = None) -> None:
        now = utcnow()
        binding.last_reply_posted_at = now
        if target_msg_id is not None:
            binding.last_reply_target_msg_id = target_msg_id
        if binding.reply_interval_min_minutes is None or binding.reply_interval_max_minutes is None:
            binding.next_reply_run_at = None
        else:
            minutes = self._schedule_minutes(binding.reply_interval_min_minutes, binding.reply_interval_max_minutes)
            binding.next_reply_run_at = now + timedelta(minutes=minutes)
        await self.session.commit()

    async def schedule_next_reply_run(self, binding: ChatBinding) -> None:
        if binding.reply_interval_min_minutes is None or binding.reply_interval_max_minutes is None:
            binding.next_reply_run_at = None
        else:
            now = utcnow()
            minutes = self._schedule_minutes(binding.reply_interval_min_minutes, binding.reply_interval_max_minutes)
            binding.next_reply_run_at = now + timedelta(minutes=minutes)
        await self.session.commit()


class AppSettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, key: str) -> AppSetting | None:
        return await self.session.get(AppSetting, key)

    async def get_value(self, key: str) -> str | None:
        setting = await self.get(key)
        return setting.value if setting else None

    async def set_value(self, key: str, value: str | None) -> AppSetting:
        setting = await self.get(key)
        if setting is None:
            setting = AppSetting(key=key, value=value)
            self.session.add(setting)
        else:
            setting.value = value
        await self.session.commit()
        await self.session.refresh(setting)
        return setting

    async def delete(self, key: str) -> int:
        setting = await self.get(key)
        if setting is None:
            return 0
        await self.session.delete(setting)
        await self.session.commit()
        return 1

class MessageLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, account_id: int, chat_ref: str, content: str, msg_id: int | None = None, direction: str = "outbound") -> MessageLog:
        message = MessageLog(
            account_id=account_id,
            chat_ref=chat_ref,
            content=content,
            msg_id=msg_id,
            direction=direction,
        )
        self.session.add(message)
        await self.session.commit()
        await self.session.refresh(message)
        return message

    async def get_by_msg_id(self, account_id: int, chat_ref: str, msg_id: int) -> MessageLog | None:
        query = select(MessageLog).where(
            MessageLog.account_id == account_id,
            MessageLog.chat_ref == chat_ref,
            MessageLog.msg_id == msg_id,
        )
        return await self.session.scalar(query)


class ReplyTaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, account_id: int, chat_ref: str, trigger_msg_id: int, execute_at: str | object) -> ReplyTask:
        task = ReplyTask(
            account_id=account_id,
            chat_ref=chat_ref,
            trigger_msg_id=trigger_msg_id,
            execute_at=execute_at,
        )
        self.session.add(task)
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def get_by_trigger(self, account_id: int, chat_ref: str, trigger_msg_id: int) -> ReplyTask | None:
        query = select(ReplyTask).where(
            ReplyTask.account_id == account_id,
            ReplyTask.chat_ref == chat_ref,
            ReplyTask.trigger_msg_id == trigger_msg_id,
        )
        return await self.session.scalar(query)

    async def list_due_tasks(self, now: object) -> list[ReplyTask]:
        query = select(ReplyTask).where(
            ReplyTask.is_completed.is_(False),
            ReplyTask.execute_at <= now,
        ).order_by(ReplyTask.execute_at.asc())
        return list(await self.session.scalars(query))

    async def mark_completed(self, task: ReplyTask) -> ReplyTask:
        task.is_completed = True
        await self.session.commit()
        await self.session.refresh(task)
        return task


class CharacterRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, **kwargs) -> Character:
        character = Character(**kwargs)
        self.session.add(character)
        await self.session.commit()
        await self.session.refresh(character)
        return character

    async def get(self, character_id: int) -> Character | None:
        return await self.session.get(Character, character_id)

    async def list(self) -> list[Character]:
        query = select(Character).order_by(Character.id.asc())
        return list(await self.session.scalars(query))

    async def count(self) -> int:
        from sqlalchemy import func
        query = select(func.count(Character.id))
        return await self.session.scalar(query) or 0


