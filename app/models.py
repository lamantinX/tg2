from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TelegramAccount(Base):
    __tablename__ = "telegram_accounts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    session_name: Mapped[str] = mapped_column(String(128), unique=True)
    proxy_url: Mapped[str | None] = mapped_column(String(256), nullable=True)
    proxy_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    auth_status: Mapped[str] = mapped_column(String(32), default="new")
    phone_code_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    character_id: Mapped[int | None] = mapped_column(ForeignKey("characters.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    bindings: Mapped[list["ChatBinding"]] = relationship(back_populates="account")
    character: Mapped["Character | None"] = relationship(back_populates="accounts")


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128))
    gender: Mapped[str | None] = mapped_column(String(16), nullable=True)  # male/female
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    occupation: Mapped[str | None] = mapped_column(String(128), nullable=True)
    personality: Mapped[str | None] = mapped_column(Text, nullable=True)
    likes: Mapped[str | None] = mapped_column(Text, nullable=True)  # Comma-separated or JSON
    dislikes: Mapped[str | None] = mapped_column(Text, nullable=True)
    speech_style: Mapped[str | None] = mapped_column(Text, nullable=True)
    background: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(String(128), default="Паттайя")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    accounts: Mapped[list["TelegramAccount"]] = relationship(back_populates="character")


class ChatBinding(Base):
    __tablename__ = "chat_bindings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("telegram_accounts.id"), index=True)
    chat_ref: Mapped[str] = mapped_column(String(128), index=True)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=10)
    interval_min_minutes: Mapped[int] = mapped_column(Integer, default=10)
    interval_max_minutes: Mapped[int] = mapped_column(Integer, default=10)
    context_message_count: Mapped[int] = mapped_column(Integer, default=12)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    account: Mapped["TelegramAccount"] = relationship(back_populates="bindings")


class MessageLog(Base):
    __tablename__ = "message_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("telegram_accounts.id"), index=True)
    chat_ref: Mapped[str] = mapped_column(String(128), index=True)
    direction: Mapped[str] = mapped_column(String(16), default="outbound")
    content: Mapped[str] = mapped_column(Text)
    msg_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReplyTask(Base):
    __tablename__ = "reply_tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("telegram_accounts.id"), index=True)
    chat_ref: Mapped[str] = mapped_column(String(128), index=True)
    trigger_msg_id: Mapped[int] = mapped_column(Integer)
    execute_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
