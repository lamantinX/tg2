import json
import logging
import re
from collections import Counter

import httpx

from app.character_engine import get_character_prompt
from app.config import settings
from app.decision_engine import BOT_PERSONALITY, REACTION_TYPE_LABELS
from app.models import Character

logger = logging.getLogger("tg2.ai")

_STOPWORDS = {
    "и",
    "а",
    "но",
    "или",
    "да",
    "нет",
    "не",
    "ну",
    "вот",
    "это",
    "эта",
    "этот",
    "еще",
    "кто",
    "что",
    "как",
    "куда",
    "где",
    "когда",
    "там",
    "тут",
    "рядом",
    "потом",
    "после",
    "будет",
    "будут",
    "идем",
    "пойду",
    "пойдем",
    "очень",
    "просто",
    "тоже",
    "with",
    "that",
    "this",
    "what",
    "who",
    "when",
    "where",
}

DEFAULT_MAIN_SYSTEM_PROMPT = (
    "You are a normal participant in a Telegram group chat. "
    "You are not an assistant and you do not explain yourself.\n\n"
    "Rules:\n"
    "- Write one short chat message, usually under 18 words.\n"
    "- Match the language used in the chat.\n"
    "- Sound casual and human.\n"
    "- Do not echo, paraphrase, or rhyme the last messages.\n"
    "- If the chat is looping, either change the topic naturally or add one concrete new detail.\n"
    "- Never repeat a recent question with different wording.\n"
    "- If you agree or disagree, add a fresh point.\n"
    "- Avoid politics, religion, drugs, or bot talk.\n"
    "- Never offer help or propose meeting offline.\n"
    "- Return only the exact message to send."
)


class AIService:
    def _normalize_context_messages(self, context_messages: list[dict | str]) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        for message in context_messages:
            if isinstance(message, str):
                text = message.strip()
                if text:
                    normalized.append({"sender": "unknown", "text": text})
                continue
            if not isinstance(message, dict):
                continue
            text = str(message.get("text", "")).strip()
            if not text:
                continue
            sender = str(message.get("sender", "unknown")).strip() or "unknown"
            normalized.append({"sender": sender, "text": text})
        return normalized

    def _normalize_tokens(self, text: str) -> list[str]:
        lowered = text.lower().replace("ё", "е")
        return re.findall(r"[0-9a-zа-я]+", lowered)

    def _token_set(self, text: str) -> set[str]:
        return {token for token in self._normalize_tokens(text) if len(token) >= 3}

    def _text_similarity(self, left: str, right: str) -> float:
        left_tokens = self._token_set(left)
        right_tokens = self._token_set(right)
        if not left_tokens or not right_tokens:
            return 0.0
        union = left_tokens | right_tokens
        if not union:
            return 0.0
        return len(left_tokens & right_tokens) / len(union)

    def _repeated_keywords(self, context_messages: list[dict[str, str]], window: int = 6) -> list[str]:
        tail = context_messages[-window:]
        if len(tail) < 4:
            return []
        counter: Counter[str] = Counter()
        for message in tail:
            counter.update(
                token for token in self._token_set(message["text"])
                if token not in _STOPWORDS
            )
        threshold = max(4, len(tail) - 1)
        return [token for token, count in counter.most_common(4) if count >= threshold]

    def should_skip_generated_reply(
        self,
        candidate: str,
        context_messages: list[dict | str],
        recent_self_messages: list[str] | None = None,
    ) -> tuple[bool, str]:
        text = candidate.strip()
        if not text:
            return True, "empty"

        normalized_context = self._normalize_context_messages(context_messages)
        for message in normalized_context[-6:]:
            similarity = self._text_similarity(text, message["text"])
            if similarity >= 0.78:
                return True, "too_similar_to_context"

        for own_message in recent_self_messages or []:
            similarity = self._text_similarity(text, own_message)
            if similarity >= 0.72:
                return True, "too_similar_to_own_recent"

        repeated_keywords = self._repeated_keywords(normalized_context)
        if repeated_keywords:
            candidate_tokens = self._token_set(text)
            overlap = [token for token in repeated_keywords if token in candidate_tokens]
            if len(overlap) >= min(2, len(repeated_keywords)):
                return True, "repeats_loop_keywords"

        question_loop = sum(1 for message in normalized_context[-6:] if "?" in message["text"])
        if question_loop >= 4 and "?" in text:
            for message in normalized_context[-4:]:
                if self._text_similarity(text, message["text"]) >= 0.55:
                    return True, "repeats_question_chain"

        return False, ""

    def _compose_system_prompt(
        self,
        system_prompt: str | None,
        main_system_prompt: str | None = None,
        reaction_type: str | None = None,
        character: Character | None = None,
        reply_target: dict[str, str] | None = None,
    ) -> str:
        if character:
            character_info = get_character_prompt(character)
        else:
            likes = ", ".join(BOT_PERSONALITY["likes"])
            dislikes = ", ".join(BOT_PERSONALITY["dislikes"])
            character_info = f"PERSONA:\nLikes: {likes}.\nDislikes: {dislikes}."

        parts = [(main_system_prompt or DEFAULT_MAIN_SYSTEM_PROMPT).strip(), character_info]

        if reply_target and str(reply_target.get("text", "")).strip():
            parts.append(
                "Reply mode:\n"
                "- You are replying directly to one recent chat message.\n"
                "- Stay focused on that message and answer it naturally."
            )

        if reaction_type:
            label = REACTION_TYPE_LABELS.get(reaction_type, reaction_type)
            parts.append(f"Preferred reaction type: {label}.")

        if system_prompt and system_prompt.strip():
            parts.append(f"Chat-specific instructions:\n{system_prompt.strip()}")

        return "\n\n".join(part for part in parts if part).strip()

    def _build_stub(self, chat_ref: str, context_messages: list[dict[str, str]]) -> str:
        tail_messages = context_messages[-4:]
        tail = " ".join(message["text"] for message in tail_messages).strip()
        if tail:
            body = f"Chat context for {chat_ref}: {tail[:280]}. Reply briefly and add a new angle."
        else:
            body = f"Chat context for {chat_ref} is empty. Start with a brief casual greeting."
        return self._ensure_disclosure(body)

    def _ensure_disclosure(self, text: str) -> str:
        prefix = settings.ai_disclosure_prefix.strip()
        value = text.strip()
        if not prefix:
            return value
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

    def _resolve_model(self, model: str | None) -> str:
        return (model or settings.openai_model).strip() or settings.openai_model

    async def generate_reply(
        self,
        chat_ref: str,
        context_messages: list[dict | str],
        system_prompt: str | None = None,
        main_system_prompt: str | None = None,
        reaction_type: str | None = None,
        character: Character | None = None,
        recent_self_messages: list[str] | None = None,
        reply_target: dict[str, str] | None = None,
        model: str | None = None,
    ) -> str:
        normalized_context = self._normalize_context_messages(context_messages)
        if not settings.openai_api_key:
            logger.warning("generate_reply: no openai_api_key, using stub chat_ref=%s", chat_ref)
            return self._build_stub(chat_ref, normalized_context)

        composed_system_prompt = self._compose_system_prompt(
            system_prompt,
            main_system_prompt=main_system_prompt,
            reaction_type=reaction_type,
            character=character,
            reply_target=reply_target,
        )

        reaction_hint = ""
        if reaction_type:
            label = REACTION_TYPE_LABELS.get(reaction_type, reaction_type)
            reaction_hint = f"\nrequested_style: {label}"

        repeated_keywords = self._repeated_keywords(normalized_context)
        loop_hint = ""
        if repeated_keywords:
            loop_hint = "\nrepeated_keywords_to_avoid: " + ", ".join(repeated_keywords)

        own_messages_hint = ""
        recent_self = [text.strip() for text in (recent_self_messages or []) if isinstance(text, str) and text.strip()]
        if recent_self:
            own_messages_hint = "\nyour_recent_messages:\n" + "\n".join(f"- {text}" for text in recent_self[-4:])

        reply_target_hint = "\nreply_goal:\nSend one natural message that adds a new angle instead of repeating the loop."
        if reply_target and str(reply_target.get("text", "")).strip():
            target_sender = str(reply_target.get("sender", "unknown")).strip() or "unknown"
            target_text = str(reply_target.get("text", "")).strip()
            reply_target_hint = (
                "\nreply_target:\n"
                f"[{target_sender}]: {target_text}\n"
                "reply_goal:\nReply directly to that target message while staying natural in the chat."
            )

        user_prompt = (
            f"chat_ref: {chat_ref}{reaction_hint}{loop_hint}\n"
            "recent_messages:\n"
            + "\n".join(f"[{message['sender']}]: {message['text']}" for message in normalized_context[-12:])
            + own_messages_hint
            + reply_target_hint
        )

        resolved_model = self._resolve_model(model)
        request_payload = {
            "model": resolved_model,
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
            chat_ref,
            len(normalized_context),
            resolved_model,
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
            return self._build_stub(chat_ref, normalized_context)

        content = self._extract_output_text(data)
        if not content:
            logger.warning("generate_reply: empty response from AI chat_ref=%s", chat_ref)
            return ""
        logger.info("generate_reply: ok chat_ref=%s reply_len=%s", chat_ref, len(content))
        return self._ensure_disclosure(content)

    async def generate_group_details(self, description: str, model: str | None = None) -> dict:
        if not settings.openai_api_key:
            logger.warning("generate_group_details: no openai_api_key, using stub")
            return {
                "title": "Generated Group",
                "about": f"Description for: {description}",
                "username": None,
                "messages": [f"Hi everyone, let's discuss {description}"] + [f"Message {index + 1}" for index in range(9)],
            }

        instruction = (
            "Generate data for a new Telegram group. Return strict JSON only with keys "
            "title, about, username, messages. Username must be null. Messages must contain 10 varied "
            "openers that sound like different humans entering a new group."
        )

        resolved_model = self._resolve_model(model)
        request_payload = {
            "model": resolved_model,
            "input": [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": instruction}],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": f"Group description: {description}"}],
                },
            ],
        }
        headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        }

        logger.info("generate_group_details: request model=%s", resolved_model)
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
                "title": "New Group",
                "about": description,
                "username": None,
                "messages": ["Hi everyone"] * 10,
            }

        content = self._extract_output_text(data)
        content_stripped = re.sub(r"```json\s*(.*?)\s*```", r"\1", content, flags=re.DOTALL)
        content_stripped = re.sub(r"```\s*(.*?)\s*```", r"\1", content_stripped, flags=re.DOTALL)

        try:
            result = json.loads(content_stripped.strip())
            messages = result.get("messages", [])
            if not isinstance(messages, list) or not messages:
                messages = [f"Message {index + 1}" for index in range(10)]
            title = str(result.get("title", "New Group"))[:64]
            logger.info("generate_group_details: ok title=%r messages_count=%s", title, len(messages[:10]))
            return {
                "title": title,
                "about": str(result.get("about", description))[:255],
                "username": result.get("username", None),
                "messages": messages[:10],
            }
        except Exception as exc:
            logger.error("generate_group_details: JSON parse failed error=%s content=%r", exc, content_stripped[:200])
            return {
                "title": "New Group",
                "about": description[:255],
                "username": None,
                "messages": ["Hello"] * 10,
            }
