import asyncio

from app.bot import run_bot
from app.db import Base, engine, ensure_schema
from app.scheduler import start_scheduler
import app.models  # noqa: F401


async def main() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await ensure_schema()
    start_scheduler()
    await run_bot()


if __name__ == "__main__":
    asyncio.run(main())
