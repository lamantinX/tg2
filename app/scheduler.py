import logging
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.db import SessionLocal
from app.services import BindingService, ChatAutomationService

scheduler = AsyncIOScheduler()
logger = logging.getLogger("tg2.scheduler")


def configure_scheduler_logging() -> None:
    log_path = Path("data/logs/scheduler.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if not logger.handlers:
        handler = logging.FileHandler(log_path, encoding="utf-8")
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)


processing_bindings = set()


async def tick_chat_automation() -> None:
    configure_scheduler_logging()
    async with SessionLocal() as session:
        binding_service = BindingService(session)
        chat_service = ChatAutomationService(session)
        due = await binding_service.due_bindings()
        logger.info("tick started due=%s", len(due))
        for binding in due:
            if binding.id in processing_bindings:
                logger.warning("skipping binding_id=%s as it is already being processed", binding.id)
                continue
            
            processing_bindings.add(binding.id)
            try:
                logger.info(
                    "processing binding_id=%s account_id=%s chat_ref=%s interval_range=%s-%s context_count=%s",
                    binding.id,
                    binding.account_id,
                    binding.chat_ref,
                    binding.interval_min_minutes,
                    binding.interval_max_minutes,
                    binding.context_message_count,
                )
                import asyncio
                sent_content = await asyncio.wait_for(
                    chat_service.generate_and_send_binding(binding),
                    timeout=120.0,
                )
                if sent_content:
                    # Бот написал — обновляем время следующего запуска
                    await binding_service.touch_posted(binding)
                    logger.info(
                        "sent_ok binding_id=%s account_id=%s chat_ref=%s next_run_at=%s",
                        binding.id,
                        binding.account_id,
                        binding.chat_ref,
                        binding.next_run_at,
                    )
                else:
                    # Бот решил молчать — не трогаем next_run_at
                    logger.info(
                        "decision_skip binding_id=%s account_id=%s chat_ref=%s",
                        binding.id,
                        binding.account_id,
                        binding.chat_ref,
                    )
            except Exception as exc:
                logger.exception(
                    "sent_failed binding_id=%s account_id=%s chat_ref=%s error=%s",
                    binding.id,
                    binding.account_id,
                    binding.chat_ref,
                    exc,
                )
                await binding_service.touch_posted(binding)
            finally:
                processing_bindings.discard(binding.id)

        all_bindings = await binding_service.repo.list_enabled()
        import asyncio

        async def poll_safe(b):
            try:
                await asyncio.wait_for(chat_service.poll_for_replies(b), timeout=45.0)
            except Exception as exc:
                logger.error("error polling replies for binding %s: %s", b.id, exc)

        # Опрашиваем ответы параллельно, но не более 3 одновременно, чтобы не спамить Telegram
        semaphore = asyncio.Semaphore(3)
        async def sem_poll(b):
            async with semaphore:
                await poll_safe(b)

        if all_bindings:
            await asyncio.gather(*(sem_poll(b) for b in all_bindings))

        try:
            await asyncio.wait_for(chat_service.process_due_reply_tasks(), timeout=120.0)
        except Exception as exc:
            logger.error("error processing reply tasks: %s", exc)


def start_scheduler() -> None:
    configure_scheduler_logging()
    if scheduler.running:
        logger.info("scheduler already running")
        return
    if scheduler.get_job("chat-automation") is None:
        scheduler.add_job(tick_chat_automation, "interval", seconds=60, id="chat-automation", max_instances=2)
    scheduler.start()
    logger.info("scheduler started")


def restart_scheduler() -> None:
    configure_scheduler_logging()
    if scheduler.running:
        try:
            scheduler.shutdown(wait=False)
            logger.info("scheduler stopped for restart")
        except Exception as exc:
            logger.warning("scheduler shutdown error: %s", exc)
    for job in scheduler.get_jobs():
        try:
            scheduler.remove_job(job.id)
        except Exception:
            pass
    if scheduler.get_job("chat-automation") is None:
        scheduler.add_job(tick_chat_automation, "interval", seconds=60, id="chat-automation", max_instances=2)
    scheduler.start()
    logger.info("scheduler restarted manually")

