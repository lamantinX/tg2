import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import router
from app.config import settings
from app.db import Base, engine, ensure_schema
from app.scheduler import start_scheduler
import app.models  # noqa: F401


def configure_logging() -> None:
    log_dir = settings.resolved_data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(fmt)

    file_handler = logging.FileHandler(log_dir / "app.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    root = logging.getLogger("tg2")
    if not root.handlers:
        root.setLevel(logging.DEBUG)
        root.addHandler(console_handler)
        root.addHandler(file_handler)


from app.proxy_manager import proxy_manager
from app.db import Base, engine, ensure_schema, SessionLocal
from app.services import CharacterService


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await ensure_schema()

    async with SessionLocal() as db:
        await proxy_manager.initialize_from_db(db)
        char_service = CharacterService(db)
        await char_service.ensure_default_characters()

    start_scheduler()
    yield


app = FastAPI(title="tg2 MVP", lifespan=lifespan)
app.include_router(router, prefix="/api")