from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    settings.database_url,
    future=True,
    connect_args={"timeout": 30},
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


from sqlalchemy import event


@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


async def get_session() -> AsyncSession:
    async with SessionLocal() as session:
        yield session


async def ensure_schema() -> None:
    if not settings.database_url.startswith("sqlite"):
        return

    async with engine.begin() as connection:
        table_names = {row[0] for row in (await connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).all()}
        if "chat_bindings" not in table_names:
            return

        column_rows = await connection.execute(text("PRAGMA table_info(chat_bindings)"))
        columns = {row[1] for row in column_rows.all()}

        migrations = [
            ("chat_title", "ALTER TABLE chat_bindings ADD COLUMN chat_title VARCHAR(256) NULL"),
            ("interval_min_minutes", "ALTER TABLE chat_bindings ADD COLUMN interval_min_minutes INTEGER NOT NULL DEFAULT 10"),
            ("interval_max_minutes", "ALTER TABLE chat_bindings ADD COLUMN interval_max_minutes INTEGER NOT NULL DEFAULT 10"),
            ("reply_interval_min_minutes", "ALTER TABLE chat_bindings ADD COLUMN reply_interval_min_minutes INTEGER NULL"),
            ("reply_interval_max_minutes", "ALTER TABLE chat_bindings ADD COLUMN reply_interval_max_minutes INTEGER NULL"),
            ("context_message_count", "ALTER TABLE chat_bindings ADD COLUMN context_message_count INTEGER NOT NULL DEFAULT 12"),
            ("system_prompt", "ALTER TABLE chat_bindings ADD COLUMN system_prompt TEXT NULL"),
            ("next_run_at", "ALTER TABLE chat_bindings ADD COLUMN next_run_at DATETIME NULL"),
            ("last_reply_posted_at", "ALTER TABLE chat_bindings ADD COLUMN last_reply_posted_at DATETIME NULL"),
            ("next_reply_run_at", "ALTER TABLE chat_bindings ADD COLUMN next_reply_run_at DATETIME NULL"),
            ("last_reply_target_msg_id", "ALTER TABLE chat_bindings ADD COLUMN last_reply_target_msg_id INTEGER NULL"),
        ]
        for column_name, sql in migrations:
            if column_name not in columns:
                await connection.execute(text(sql))

        if "message_logs" in table_names:
            ml_rows = await connection.execute(text("PRAGMA table_info(message_logs)"))
            ml_cols = {row[1] for row in ml_rows.all()}
            if "msg_id" not in ml_cols:
                await connection.execute(text("ALTER TABLE message_logs ADD COLUMN msg_id INTEGER NULL"))

        if "telegram_accounts" in table_names:
            ta_rows = await connection.execute(text("PRAGMA table_info(telegram_accounts)"))
            ta_cols = {row[1] for row in ta_rows.all()}
            if "proxy_session_id" not in ta_cols:
                await connection.execute(text("ALTER TABLE telegram_accounts ADD COLUMN proxy_session_id VARCHAR(128) NULL"))
            if "character_id" not in ta_cols:
                await connection.execute(text("ALTER TABLE telegram_accounts ADD COLUMN character_id INTEGER NULL"))
            if "account_name" not in ta_cols:
                await connection.execute(text("ALTER TABLE telegram_accounts ADD COLUMN account_name VARCHAR(256) NULL"))

        await connection.execute(
            text(
                "UPDATE chat_bindings "
                "SET interval_min_minutes = CASE "
                "WHEN interval_min_minutes IS NULL OR interval_min_minutes = 10 THEN COALESCE(interval_minutes, 10) "
                "ELSE interval_min_minutes END, "
                "interval_max_minutes = CASE "
                "WHEN interval_max_minutes IS NULL OR interval_max_minutes = 10 THEN COALESCE(interval_minutes, 10) "
                "ELSE interval_max_minutes END, "
                "context_message_count = COALESCE(context_message_count, 12)"
            )
        )

        await connection.run_sync(Base.metadata.create_all)
