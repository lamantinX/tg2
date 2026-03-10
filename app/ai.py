import logging
import httpx

from app.config import settings
from app.decision_engine import BOT_PERSONALITY, REACTION_TYPE_LABELS
from app.models import Character
from app.character_engine import get_character_prompt

logger = logging.getLogger("tg2.ai")


class AIService:
    def _compose_system_prompt(
        self,
        system_prompt: str | None,
        reaction_type: str | None = None,
        character: Character | None = None,
    ) -> str:
        # 1. Личность персонажа
        character_info = ""
        if character:
            character_info = get_character_prompt(character)
        else:
            likes = ", ".join(BOT_PERSONALITY["likes"])
            dislikes = ", ".join(BOT_PERSONALITY["dislikes"])
            character_info = f"ЛИЧНОСТЬ:\nТебе нравится: {likes}.\nТебе НЕ нравится: {dislikes}."

        # 2. Стиль ответа (если определен DecisionEngine)
        style_hint = ""
        if reaction_type:
            label = REACTION_TYPE_LABELS.get(reaction_type, reaction_type)
            style_hint = f"\n\nТЕКУЩИЙ СТИЛЬ ОТВЕТА (настройся на это): {label}."

        # 3. Дополнительный промпт от пользователя
        user_add_on = ""
        if system_prompt and system_prompt.strip():
            user_add_on = f"\n\nПРАВИЛА ОТ ПОЛЬЗОВАТЕЛЯ:\n{system_prompt.strip()}"

        return f"""
Ты — обычный человек, участник чата в Telegram. Ты НЕ помощник, НЕ ИИ и НЕ бот.
Твоя задача — общаться так, чтобы никто не догадался, что ты программа.

{character_info}

ОБЩИЕ ПРАВИЛА:
- Пиши коротко (1-5 слов, максимум 20).
- Используй разговорный стиль (можно с маленькой буквы, без лишних знаков).
- Будь дружелюбным, иногда шути, иногда задавай вопросы.
- Не используй букву 'ё'.
- Запрещено обсуждать политику, религию и наркотики. Если разговор уходит туда — просто промолчи.
- Если тебя подозревают в том, что ты бот — отшутись (например: "ахаха, а ты?").

Твоя цель: органично вписаться в диалог, чтобы никто не заподозрил автоматизацию.
{style_hint}{user_add_on}
""".strip()

    def _build_stub(self, chat_ref: str, context_messages: list[dict]) -> str:
        tail_msgs = context_messages[-4:]
        tail = " ".join(m["text"] for m in tail_msgs).strip()
        if tail:
            body = f"Контекст чата {chat_ref}: {tail[:280]}. Продолжаю обсуждение кратко и по делу."
        else:
            body = f"Контекст чата {chat_ref} пока пустой. Начинаю разговор коротким приветствием."
        return self._ensure_disclosure(body)

    def _ensure_disclosure(self, text: str) -> str:
        prefix = settings.ai_disclosure_prefix.strip()
        value = text.strip()
        if value.startswith(prefix):
            return value
        return f"{prefix} {value}"

    def _extract_output_text(self, payload: dict) -> str:
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()

        fragments: list[str] = []
        for item in payload.get("output", []):
            if not isinstance(item, dict):
                continue
            for content in item.get("content", []):
                if not isinstance(content, dict):
                    continue
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    fragments.append(text.strip())
        return "\n".join(fragments).strip()

    async def generate_reply(
        self,
        chat_ref: str,
        context_messages: list[dict],
        system_prompt: str | None = None,
        reaction_type: str | None = None,
        character: Character | None = None,
    ) -> str:
        if not settings.openai_api_key:
            logger.warning("generate_reply: no openai_api_key, using stub chat_ref=%s", chat_ref)
            return self._build_stub(chat_ref, context_messages)

        composed_system_prompt = self._compose_system_prompt(
            system_prompt,
            reaction_type=reaction_type,
            character=character
        )
        reaction_hint = ""
        if reaction_type:
            label = REACTION_TYPE_LABELS.get(reaction_type, reaction_type)
            reaction_hint = f"\nrequested_style: {label}"
        user_prompt = (
            f"chat_ref: {chat_ref}{reaction_hint}\n"
            "recent_messages:\n"
            + "\n".join(f"[{message['sender']}]: {message['text']}" for message in context_messages[-12:])
        )

        request_payload = {
            "model": settings.openai_model,
            "input": [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": composed_system_prompt}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": user_prompt}],
                },
            ],
        }
        headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        }

        logger.info(
            "generate_reply: request chat_ref=%s context_count=%s model=%s",
            chat_ref, len(context_messages), settings.openai_model,
        )
        try:
            async with httpx.AsyncClient(timeout=settings.openai_timeout_seconds) as client:
                response = await client.post(
                    f"{settings.openai_base_url.rstrip('/')}/responses",
                    headers=headers,
                    json=request_payload,
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.error("generate_reply: request failed chat_ref=%s error=%s", chat_ref, exc)
            return self._build_stub(chat_ref, context_messages)

        content = self._extract_output_text(data)
        if not content:
            logger.warning("generate_reply: empty response from AI, using stub chat_ref=%s", chat_ref)
            return self._build_stub(chat_ref, context_messages)
        logger.info("generate_reply: ok chat_ref=%s reply_len=%s", chat_ref, len(content))
        return self._ensure_disclosure(content)

    async def generate_group_details(self, description: str) -> dict:
        if not settings.openai_api_key:
            logger.warning("generate_group_details: no openai_api_key, using stub")
            return {
                "title": "Сгенерированная группа",
                "about": f"Описание для: {description}",
                "username": None,
                "messages": [f"Всем привет! Обсуждаем {description}"] + [f"Сообщение {i+1}" for i in range(9)]
            }

        instruction = """
Тебе нужно сгенерировать данные для новой Telegram группы.
Ты должен вернуть результат СТРОГО В ФОРМАТЕ JSON объектом, без какого-либо дополнительного текста или форматирования markdown.
Формат JSON:
{
  "title": "Название группы (максимум 64 символа)",
  "about": "Описание группы (максимум 255 символов)",
  "username": null,
  "messages": [
    "сгенерируй 10 разнообразных приветственных или тематических сообщений (как будто люди начинают общаться и знакомиться в новой группе. Сообщения должны быть от разных предполагаемых лиц)"
  ]
}
Поле username должно быть null.
""".strip()

        request_payload = {
            "model": settings.openai_model,
            "input": [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": instruction}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": f"Описание группы: {description}"}],
                },
            ],
        }
        headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        }

        logger.info("generate_group_details: request model=%s", settings.openai_model)
        try:
            async with httpx.AsyncClient(timeout=settings.openai_timeout_seconds) as client:
                response = await client.post(
                    f"{settings.openai_base_url.rstrip('/')}/responses",
                    headers=headers,
                    json=request_payload,
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.error("generate_group_details: request failed error=%s", exc)
            return {
                "title": "Новая группа",
                "about": description,
                "username": None,
                "messages": ["Всем привет!"] * 10
            }

        content = self._extract_output_text(data)

        # Пытаемся распарсить JSON
        import json
        import re
        
        # Убираем markdown вокруг сырого json если есть
        content_stripped = re.sub(r'```json\s*(.*?)\s*```', r'\1', content, flags=re.DOTALL)
        content_stripped = re.sub(r'```\s*(.*?)\s*```', r'\1', content_stripped, flags=re.DOTALL)
        
        try:
            result = json.loads(content_stripped.strip())
            messages = result.get("messages", [])
            if not isinstance(messages, list) or not messages:
                messages = [f"Сообщение {i+1}" for i in range(10)]
            title = result.get("title", "Новая группа")[:64]
            logger.info("generate_group_details: ok title=%r messages_count=%s", title, len(messages[:10]))
            return {
                "title": title,
                "about": result.get("about", description)[:255],
                "username": result.get("username", None),
                "messages": messages[:10]
            }
        except Exception as exc:
            logger.error("generate_group_details: JSON parse failed error=%s content=%r", exc, content_stripped[:200])
            return {
                "title": "Новая группа",
                "about": description[:255],
                "username": None,
                "messages": ["Приветик"] * 10
            }
