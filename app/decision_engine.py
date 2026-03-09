"""
Decision Engine — алгоритм принятия решений для бота.

Определяет:
  1. Нужно ли боту писать в чат (trigger_score + random_factor >= threshold).
  2. Какой тип реакции выбрать (короткий комментарий, вопрос, шутка и т.д.).

Используется из ChatAutomationService перед вызовом generate_reply.
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("tg2.decision")

# ---------------------------------------------------------------------------
# Профиль личности бота
# ---------------------------------------------------------------------------

BOT_PERSONALITY = {
    "likes": [
        "фильмы",
        "технологии",
        "мемы",
        "игры",
        "музыка",
    ],
    "dislikes": [
        "политика",
        "длинные обсуждения",
        "спам",
        "реклама",
    ],
}

# ---------------------------------------------------------------------------
# Типы реакций и их вероятности
# ---------------------------------------------------------------------------

REACTION_TYPES: list[tuple[str, float]] = [
    ("short_comment", 0.35),   # короткий комментарий
    ("question",      0.20),   # вопрос
    ("joke",          0.15),   # шутка
    ("agree",         0.10),   # согласие
    ("disagree",      0.10),   # несогласие
    ("topic_change",  0.05),   # смена темы
    ("meme_reaction", 0.05),   # мемная реакция
]

REACTION_TYPE_LABELS: dict[str, str] = {
    "short_comment": "короткий комментарий",
    "question":      "вопрос",
    "joke":          "шутка",
    "agree":         "согласие",
    "disagree":      "несогласие",
    "topic_change":  "смена темы",
    "meme_reaction": "мемная реакция",
}


def _pick_reaction_type() -> str:
    """Выбирает тип реакции по заданным вероятностям."""
    types = [t for t, _ in REACTION_TYPES]
    weights = [w for _, w in REACTION_TYPES]
    return random.choices(types, weights=weights, k=1)[0]


# ---------------------------------------------------------------------------
# Вспомогательные детекторы контекста
# ---------------------------------------------------------------------------

_QUESTION_KEYWORDS = ("?", "как", "почему", "зачем", "когда", "кто", "что", "где", "чем", "куда")
_DISPUTE_KEYWORDS  = ("нет", "не согласен", "неправда", "докажи", "ерунда", "чушь", "бред",
                      "ты не прав", "врёшь", "неверно")


def _detect_question(messages: list[str]) -> bool:
    """Есть ли вопрос в последних 5 сообщениях."""
    tail = messages[-5:] if len(messages) >= 5 else messages
    for m in tail:
        ml = m.lower()
        if any(kw in ml for kw in _QUESTION_KEYWORDS):
            return True
    return False


def _detect_dispute(messages: list[str]) -> bool:
    """Есть ли признаки спора в последних 5 сообщениях."""
    tail = messages[-5:] if len(messages) >= 5 else messages
    for m in tail:
        ml = m.lower()
        if any(kw in ml for kw in _DISPUTE_KEYWORDS):
            return True
    return False


def _detect_interesting_topic(messages: list[str]) -> bool:
    """Обсуждается ли «интересная» для бота тема (из LIKES)."""
    tail = messages[-5:] if len(messages) >= 5 else messages
    combined = " ".join(tail).lower()
    return any(like.lower() in combined for like in BOT_PERSONALITY["likes"])


def _detect_bot_mentioned(messages: list[str], bot_name: str | None) -> bool:
    """Упомянули ли имя бота в последних 3 сообщениях."""
    if not bot_name:
        return False
    tail = messages[-3:] if len(messages) >= 3 else messages
    name_lower = bot_name.lower()
    return any(name_lower in m.lower() for m in tail)


def _count_unique_recent_authors(messages: list[str]) -> int:
    """
    Упрощённая эвристика: считаем сообщения за «активное обсуждение».
    Так как у нас только тексты (без метаданных об авторах), используем
    количество непустых сообщений в хвосте — если > 5, считаем «много».
    """
    return len([m for m in messages[-10:] if m.strip()])


# ---------------------------------------------------------------------------
# Основной класс
# ---------------------------------------------------------------------------

@dataclass
class DecisionContext:
    """Контекст для принятия решения."""
    messages: list[str] = field(default_factory=list)
    """Последние сообщения из чата (тексты)."""

    last_bot_post_at: Optional[datetime] = None
    """Когда бот последний раз писал в этот чат (UTC)."""

    last_message_at: Optional[datetime] = None
    """Когда было последнее сообщение в чате (UTC)."""

    bot_name: Optional[str] = None
    """Имя (username) бота для детекции упоминаний."""


@dataclass
class DecisionResult:
    """Результат работы движка."""
    should_send: bool
    reaction_type: Optional[str]    # None если should_send == False
    trigger_score: int
    random_factor: int
    decision_score: int
    reason: str                     # человекочитаемое объяснение


class DecisionEngine:
    """Движок принятия решений: писать боту или молчать."""

    DECISION_THRESHOLD = 2

    def decide(self, ctx: DecisionContext) -> DecisionResult:
        now = datetime.now(timezone.utc)

        trigger_score = 0
        reasons: list[str] = []

        # --- Шаг 1: триггеры ---

        # Упоминание имени бота
        if _detect_bot_mentioned(ctx.messages, ctx.bot_name):
            trigger_score += 5
            reasons.append("упомянули имя бота (+5)")

        # Вопрос в чате
        if _detect_question(ctx.messages):
            trigger_score += 4
            reasons.append("задали вопрос (+4)")

        # Интересная тема
        if _detect_interesting_topic(ctx.messages):
            trigger_score += 2
            reasons.append("интересная тема (+2)")

        # Спор
        if _detect_dispute(ctx.messages):
            trigger_score += 3
            reasons.append("спор (+3)")

        # Чат затих > 10 минут
        if ctx.last_message_at is not None:
            lma = ctx.last_message_at
            if lma.tzinfo is None:
                lma = lma.replace(tzinfo=timezone.utc)
            silence_minutes = (now - lma).total_seconds() / 60
            if silence_minutes > 10:
                trigger_score += 3
                reasons.append(f"тишина {silence_minutes:.0f} мин (+3)")

        # Штрафы за недавние посты бота
        if ctx.last_bot_post_at is not None:
            lbpa = ctx.last_bot_post_at
            if lbpa.tzinfo is None:
                lbpa = lbpa.replace(tzinfo=timezone.utc)
            minutes_since_last = (now - lbpa).total_seconds() / 60
            if minutes_since_last < 5:
                trigger_score -= 3
                reasons.append(f"бот писал {minutes_since_last:.0f} мин назад (-3)")
            elif minutes_since_last < 15:
                trigger_score -= 1
                reasons.append(f"бот писал {minutes_since_last:.0f} мин назад (-1)")

        # Много активных участников (эвристика: > 8 коротких сообщений в хвосте из 10)
        recent_count = _count_unique_recent_authors(ctx.messages)
        if recent_count > 8:
            trigger_score -= 1
            reasons.append(f"очень активный чат ({recent_count} реч. единиц) (-1)")

        # --- Шаг 2: случайность ---
        random_factor = random.randint(0, 3)

        decision_score = trigger_score + random_factor

        # --- Шаг 3: решение ---
        should_send = decision_score >= self.DECISION_THRESHOLD

        reaction_type = _pick_reaction_type() if should_send else None

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


# Глобальный синглтон — можно импортировать напрямую
decision_engine = DecisionEngine()
