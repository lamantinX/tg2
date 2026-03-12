from __future__ import annotations

import logging
import random
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.models import Character

logger = logging.getLogger("tg2.decision")

BOT_PERSONALITY = {
    "likes": [
        "movies",
        "technology",
        "memes",
        "games",
        "music",
    ],
    "dislikes": [
        "politics",
        "long arguments",
        "spam",
        "ads",
    ],
}

REACTION_TYPES: list[tuple[str, float]] = [
    ("short_comment", 0.35),
    ("question", 0.18),
    ("joke", 0.15),
    ("agree", 0.10),
    ("disagree", 0.10),
    ("topic_change", 0.08),
    ("meme_reaction", 0.04),
]

REACTION_TYPE_LABELS: dict[str, str] = {
    "short_comment": "short comment",
    "question": "question",
    "joke": "joke",
    "agree": "agree",
    "disagree": "disagree",
    "topic_change": "topic change",
    "meme_reaction": "meme reaction",
}

_QUESTION_KEYWORDS = (
    "?",
    "как",
    "почему",
    "зачем",
    "когда",
    "кто",
    "что",
    "где",
    "чем",
    "куда",
    "how",
    "why",
    "when",
    "who",
    "what",
    "where",
)

_DISPUTE_KEYWORDS = (
    "нет",
    "не согласен",
    "неправда",
    "докажи",
    "ерунда",
    "чушь",
    "бред",
    "ты не прав",
    "вреш",
    "неверно",
    "wrong",
    "disagree",
)

_LOOP_STOPWORDS = {
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
    "ещё",
    "кто",
    "что",
    "как",
    "куда",
    "где",
    "там",
    "тут",
    "рядом",
    "потом",
    "после",
    "будет",
    "будут",
    "идем",
    "идём",
    "пойду",
    "пойдем",
    "пойдем",
    "ктото",
    "кто-нибудь",
    "очень",
    "просто",
    "тоже",
    "тут",
    "here",
    "there",
    "this",
    "that",
}


def _pick_reaction_type() -> str:
    types = [reaction_type for reaction_type, _ in REACTION_TYPES]
    weights = [weight for _, weight in REACTION_TYPES]
    return random.choices(types, weights=weights, k=1)[0]


def _normalize_tokens(text: str) -> list[str]:
    lowered = text.lower().replace("ё", "е")
    return re.findall(r"[0-9a-zа-я]+", lowered)


def _token_set(text: str) -> set[str]:
    return {token for token in _normalize_tokens(text) if len(token) >= 3}


def _message_similarity(left: str, right: str) -> float:
    left_tokens = _token_set(left)
    right_tokens = _token_set(right)
    if not left_tokens or not right_tokens:
        return 0.0
    union = left_tokens | right_tokens
    if not union:
        return 0.0
    return len(left_tokens & right_tokens) / len(union)


def _is_question_message(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in _QUESTION_KEYWORDS)


def _detect_question(messages: list[dict]) -> bool:
    tail = messages[-5:] if len(messages) >= 5 else messages
    return any(_is_question_message(message["text"]) for message in tail)


def _detect_dispute(messages: list[dict]) -> bool:
    tail = messages[-5:] if len(messages) >= 5 else messages
    for message in tail:
        lowered = message["text"].lower()
        if any(keyword in lowered for keyword in _DISPUTE_KEYWORDS):
            return True
    return False


def _detect_interesting_topic(messages: list[dict], character: Character | None = None) -> bool:
    tail = messages[-5:] if len(messages) >= 5 else messages
    combined = " ".join(message["text"] for message in tail).lower()

    likes = BOT_PERSONALITY["likes"]
    if character and character.likes:
        likes = [item.strip().lower() for item in character.likes.split(",") if item.strip()]

    return any(like.lower() in combined for like in likes)


def _detect_bot_mentioned(messages: list[dict], bot_name: str | None) -> bool:
    if not bot_name:
        return False
    tail = messages[-3:] if len(messages) >= 3 else messages
    name_lower = bot_name.lower()
    return any(name_lower in message["text"].lower() for message in tail)


def _count_unique_recent_authors(messages: list[dict]) -> int:
    tail = messages[-10:] if len(messages) >= 10 else messages
    authors = {message["sender"] for message in tail if message["text"].strip()}
    return len(authors)


def _detect_conversation_loop(messages: list[dict]) -> tuple[bool, list[str]]:
    tail = [message for message in (messages[-6:] if len(messages) >= 6 else messages) if message.get("text", "").strip()]
    if len(tail) < 4:
        return False, []

    reasons: list[str] = []
    normalized = [" ".join(_normalize_tokens(message["text"])) for message in tail]
    if len(set(normalized)) <= max(2, len(normalized) // 2):
        reasons.append("same phrases repeat")

    duplicate_pairs = 0
    for index, current in enumerate(tail):
        for other in tail[index + 1:]:
            if _message_similarity(current["text"], other["text"]) >= 0.72:
                duplicate_pairs += 1
    if duplicate_pairs >= 3:
        reasons.append("many near duplicates")

    question_count = sum(1 for message in tail if _is_question_message(message["text"]))
    if question_count >= 4:
        reasons.append("question chain")

    token_counter: Counter[str] = Counter()
    for message in tail:
        filtered_tokens = {
            token for token in _token_set(message["text"])
            if token not in _LOOP_STOPWORDS
        }
        token_counter.update(filtered_tokens)
    dominant_tokens = [token for token, count in token_counter.most_common(3) if count >= max(4, len(tail) - 1)]
    if dominant_tokens:
        reasons.append("dominant topic: " + ", ".join(dominant_tokens[:2]))

    return bool(reasons), reasons


@dataclass
class DecisionContext:
    messages: list[dict] = field(default_factory=list)
    last_bot_post_at: Optional[datetime] = None
    last_message_at: Optional[datetime] = None
    bot_name: Optional[str] = None
    character: Optional[Character] = None


@dataclass
class DecisionResult:
    should_send: bool
    reaction_type: Optional[str]
    trigger_score: int
    random_factor: int
    decision_score: int
    reason: str


class DecisionEngine:
    DECISION_THRESHOLD = 2

    def decide(self, ctx: DecisionContext) -> DecisionResult:
        now = datetime.now(timezone.utc)
        trigger_score = 0
        reasons: list[str] = []

        loop_detected, loop_reasons = _detect_conversation_loop(ctx.messages)
        if loop_detected:
            trigger_score -= 6
            reasons.append("loop detected (-6)")
            reasons.extend(loop_reasons[:2])

        if _detect_bot_mentioned(ctx.messages, ctx.bot_name):
            trigger_score += 5
            reasons.append("bot mentioned (+5)")

        if _detect_question(ctx.messages):
            trigger_score += 4
            reasons.append("question in chat (+4)")

        if _detect_interesting_topic(ctx.messages, ctx.character):
            trigger_score += 2
            reasons.append("interesting topic (+2)")

        if _detect_dispute(ctx.messages):
            trigger_score += 3
            reasons.append("dispute (+3)")

        if ctx.last_message_at is not None:
            last_message_at = ctx.last_message_at
            if last_message_at.tzinfo is None:
                last_message_at = last_message_at.replace(tzinfo=timezone.utc)
            silence_minutes = (now - last_message_at).total_seconds() / 60
            if silence_minutes > 10:
                trigger_score += 3
                reasons.append(f"silence {silence_minutes:.0f}m (+3)")

        if ctx.last_bot_post_at is not None:
            last_bot_post_at = ctx.last_bot_post_at
            if last_bot_post_at.tzinfo is None:
                last_bot_post_at = last_bot_post_at.replace(tzinfo=timezone.utc)
            minutes_since_last = (now - last_bot_post_at).total_seconds() / 60
            if minutes_since_last < 5:
                trigger_score -= 3
                reasons.append(f"bot posted {minutes_since_last:.0f}m ago (-3)")
            elif minutes_since_last < 15:
                trigger_score -= 1
                reasons.append(f"bot posted {minutes_since_last:.0f}m ago (-1)")

        recent_count = _count_unique_recent_authors(ctx.messages)
        if recent_count > 8:
            trigger_score -= 1
            reasons.append(f"chat too active ({recent_count}) (-1)")

        random_factor = random.randint(0, 3)
        decision_score = trigger_score + random_factor
        should_send = decision_score >= self.DECISION_THRESHOLD

        reaction_type: Optional[str] = None
        if should_send:
            reaction_type = "topic_change" if loop_detected else _pick_reaction_type()

        reason_str = (
            f"trigger={trigger_score} rand={random_factor} total={decision_score} "
            f"threshold={self.DECISION_THRESHOLD} "
            f"({'SEND' if should_send else 'SKIP'})"
            + (" | " + "; ".join(reasons) if reasons else "")
        )
        logger.info("decision %s reaction=%s %s", "SEND" if should_send else "SKIP", reaction_type, reason_str)

        return DecisionResult(
            should_send=should_send,
            reaction_type=reaction_type,
            trigger_score=trigger_score,
            random_factor=random_factor,
            decision_score=decision_score,
            reason=reason_str,
        )


decision_engine = DecisionEngine()
