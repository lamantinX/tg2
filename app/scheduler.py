import logging
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.db import SessionLocal
from app.proxy_manager import proxy_manager
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
    import asyncio

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
                sent_content = await asyncio.wait_for(
                    chat_service.generate_and_send_binding(binding),
                    timeout=120.0,
                )
                if sent_content:
                    await binding_service.touch_posted(binding)
                    logger.info(
                        "sent_ok binding_id=%s account_id=%s chat_ref=%s next_run_at=%s",
                        binding.id,
                        binding.account_id,
                        binding.chat_ref,
                        binding.next_run_at,
                    )
                else:
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

        due_replies = await binding_service.due_reply_bindings()
        logger.info("reply tick started due=%s", len(due_replies))
        for binding in due_replies:
            if binding.id in processing_bindings:
                logger.warning("skipping recent reply binding_id=%s as it is already being processed", binding.id)
                continue

            processing_bindings.add(binding.id)
            try:
                logger.info(
                    "processing_recent_reply binding_id=%s account_id=%s chat_ref=%s reply_interval_range=%s-%s",
                    binding.id,
                    binding.account_id,
                    binding.chat_ref,
                    binding.reply_interval_min_minutes,
                    binding.reply_interval_max_minutes,
                )
                reply_result = await asyncio.wait_for(
                    chat_service.generate_and_send_recent_reply(binding),
                    timeout=120.0,
                )
                if reply_result:
                    _, target_msg_id = reply_result
                    await binding_service.touch_reply_posted(binding, target_msg_id=target_msg_id)
                    await binding_service.touch_posted(binding)
                    logger.info(
                        "sent_recent_reply binding_id=%s account_id=%s chat_ref=%s target_msg_id=%s next_reply_run_at=%s",
                        binding.id,
                        binding.account_id,
                        binding.chat_ref,
                        target_msg_id,
                        binding.next_reply_run_at,
                    )
                else:
                    await binding_service.schedule_next_reply_run(binding)
                    logger.info(
                        "recent_reply_skip binding_id=%s account_id=%s chat_ref=%s",
                        binding.id,
                        binding.account_id,
                        binding.chat_ref,
                    )
            except Exception as exc:
                logger.exception(
                    "recent_reply_failed binding_id=%s account_id=%s chat_ref=%s error=%s",
                    binding.id,
                    binding.account_id,
                    binding.chat_ref,
                    exc,
                )
                await binding_service.schedule_next_reply_run(binding)
            finally:
                processing_bindings.discard(binding.id)

        all_bindings = await binding_service.repo.list_enabled()

        async def poll_safe(binding):
            try:
                await asyncio.wait_for(chat_service.poll_for_replies(binding), timeout=45.0)
            except Exception as exc:
                logger.error("error polling replies for binding %s: %s", binding.id, exc)

        semaphore = asyncio.Semaphore(3)

        async def sem_poll(binding):
            async with semaphore:
                await poll_safe(binding)

        if all_bindings:
            await asyncio.gather(*(sem_poll(binding) for binding in all_bindings))

        try:
            await asyncio.wait_for(chat_service.process_due_reply_tasks(), timeout=120.0)
        except Exception as exc:
            logger.error("error processing reply tasks: %s", exc)


async def tick_proxy_health() -> None:
    """?????????????????????????? ???????????????? ???????????????? ???????????? ?? ???????????????????????????? ???????? ??????????"""
    async with SessionLocal() as session:
        logger.info("checking proxy health...")
        try:
            await proxy_manager.health_check_all(session)
        except Exception:
            logger.exception("failed to health check proxies")


def start_scheduler() -> None:
    configure_scheduler_logging()
    if scheduler.running:
        logger.info("scheduler already running")
        return
    if scheduler.get_job("chat-automation") is None:
        scheduler.add_job(tick_chat_automation, "interval", seconds=60, id="chat-automation", max_instances=2)
    if scheduler.get_job("proxy-health") is None:
        scheduler.add_job(tick_proxy_health, "interval", minutes=5, id="proxy-health")
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
    if scheduler.get_job("proxy-health") is None:
        scheduler.add_job(tick_proxy_health, "interval", minutes=5, id="proxy-health")
    scheduler.start()
    logger.info("scheduler restarted manually")
