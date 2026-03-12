import asyncio
import logging
from pathlib import Path
from typing import Dict

from telethon import TelegramClient
from telethon.errors import AuthKeyUnregisteredError, SessionPasswordNeededError, SessionRevokedError
from telethon.errors import (
    ChannelPrivateError, UserAlreadyParticipantError,
    InviteHashExpiredError, InviteHashInvalidError
)
from telethon.tl.functions.channels import CreateChannelRequest, JoinChannelRequest, UpdateUsernameRequest
from telethon.tl.functions.messages import ImportChatInviteRequest, CheckChatInviteRequest

from app.config import settings
from app.proxy import parse_proxy_url

logger = logging.getLogger("tg2.telegram")

try:
    from telethon.errors.rpcerrorlist import UserDeactivatedBanError, UserDeactivatedError
except ImportError:  # pragma: no cover
    UserDeactivatedBanError = UserDeactivatedError = Exception


# Global registry for TelegramClient instances to prevent "database is locked" errors
# mapping: session_path -> TelegramClient
_client_registry: Dict[str, TelegramClient] = {}
_client_ref_counts: Dict[str, int] = {}
_registry_lock = asyncio.Lock()


class TelegramAccountClient:
    def __init__(self, session_name: str, proxy_url: str | None = None) -> None:
        self.session_name = session_name
        self.proxy_url = proxy_url
        self.session_dir = Path("data") / "sessions"
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.session_path = str(self.session_dir / session_name)
        self._client = None
        self._is_disconnected = False

    async def connect(self) -> TelegramClient:
        async with _registry_lock:
            if self.session_path not in _client_registry:
                logger.debug("Creating new TelegramClient for session %s", self.session_name)
                client = TelegramClient(
                    self.session_path,
                    settings.telegram_api_id,
                    settings.telegram_api_hash,
                    proxy=parse_proxy_url(self.proxy_url),
                    request_retries=3,
                    connection_retries=3,
                    retry_delay=2,
                    timeout=15,
                )
                _client_registry[self.session_path] = client
                _client_ref_counts[self.session_path] = 0
            
            self._client = _client_registry[self.session_path]
            _client_ref_counts[self.session_path] += 1
            
            if not self._client.is_connected():
                logger.info("Connecting session %s", self.session_name)
                await self._client.connect()
                
            return self._client

    async def disconnect(self) -> None:
        if self._is_disconnected or self._client is None:
            return
            
        async with _registry_lock:
            if self.session_path in _client_ref_counts:
                _client_ref_counts[self.session_path] -= 1
                
                if _client_ref_counts[self.session_path] <= 0:
                    logger.info("Actually disconnecting session %s (ref_count=0)", self.session_name)
                    client_to_close = _client_registry.pop(self.session_path, None)
                    _client_ref_counts.pop(self.session_path, None)
                    
                    if client_to_close:
                        try:
                            await asyncio.wait_for(client_to_close.disconnect(), timeout=5.0)
                        except Exception as e:
                            logger.warning("Error during disconnect for %s: %s", self.session_name, e)
        
        self._is_disconnected = True

    async def is_authorized(self) -> bool:
        client = await self.connect()
        result = await client.is_user_authorized()
        logger.debug("is_authorized session=%s result=%s", self.session_name, result)
        return result

    async def check_health(self) -> dict[str, str | bool]:
        try:
            client = await self.connect()
            if not await client.is_user_authorized():
                return {"is_active": False, "auth_status": "unauthorized", "reason": "session is not authorized"}
            me = await client.get_me()
            if me is None:
                return {"is_active": False, "auth_status": "unauthorized", "reason": "session returned no user"}
            return {"is_active": True, "auth_status": "authorized", "reason": "ok"}
        except (AuthKeyUnregisteredError, SessionRevokedError):
            return {"is_active": False, "auth_status": "revoked", "reason": "session revoked"}
        except (UserDeactivatedBanError, UserDeactivatedError):
            return {"is_active": False, "auth_status": "banned", "reason": "account deactivated or banned"}
        except Exception as exc:
            return {"is_active": False, "auth_status": "error", "reason": str(exc)}

    async def request_login_code(self, phone: str) -> str:
        client = await self.connect()
        sent = await client.send_code_request(phone)
        return sent.phone_code_hash

    async def complete_login(self, phone: str, code: str, phone_code_hash: str, password: str | None = None) -> str:
        client = await self.connect()
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
            return "authorized"
        except SessionPasswordNeededError:
            if not password:
                return "password_required"
            await client.sign_in(password=password)
            return "authorized"

    async def complete_password_login(self, password: str) -> str:
        client = await self.connect()
        await client.sign_in(password=password)
        return "authorized"

    async def _resolve_sender_name(self, message) -> str:
        sender_name = "Р В Р в‚¬Р РЋРІР‚РЋР В Р’В°Р РЋР С“Р РЋРІР‚С™Р В Р вЂ¦Р В РЎвЂР В РЎвЂќ"
        try:
            sender = await message.get_sender()
            if sender:
                if hasattr(sender, "first_name") and sender.first_name:
                    sender_name = sender.first_name
                    if hasattr(sender, "last_name") and sender.last_name:
                        sender_name += f" {sender.last_name}"
                elif hasattr(sender, "title") and sender.title:
                    sender_name = sender.title
                elif hasattr(sender, "username") and sender.username:
                    sender_name = sender.username
        except Exception:
            pass
        return sender_name

    async def fetch_recent_messages(self, chat_ref: str, limit: int = 12) -> list[dict]:
        logger.debug("fetch_recent_messages session=%s chat_ref=%s limit=%s", self.session_name, chat_ref, limit)
        client = await self.connect()
        entity = int(chat_ref) if chat_ref.lstrip('-').isdigit() else chat_ref
        messages = []
        # ?????????????????? ?? ?????????????????????? ??????????????, ?????????? ?????????????? ???????????? ??????-???? ?????????? ??????????????????
        fetched_total = 0
        async for message in client.iter_messages(entity, limit=limit * 3):
            if message.message and not message.out:
                sender_name = await self._resolve_sender_name(message)
                messages.append({"sender": sender_name, "text": message.message, "date": message.date})
                fetched_total += 1
                if fetched_total >= limit:
                    break
        logger.debug("fetch_recent_messages session=%s chat_ref=%s fetched=%s (own excluded)", self.session_name, chat_ref, len(messages))
        return list(reversed(messages))


    async def fetch_recent_detailed(self, chat_ref: str, limit: int = 15) -> list[dict]:
        client = await self.connect()
        messages = []
        entity = int(chat_ref) if chat_ref.lstrip('-').isdigit() else chat_ref
        async for message in client.iter_messages(entity, limit=limit):
            # ???????????????????? ?????????????????????? ?????????????????? ????????
            if message.message and not message.out:
                sender_name = await self._resolve_sender_name(message)
                messages.append({
                    "id": message.id,
                    "sender": sender_name,
                    "message": message.message,
                    "reply_to_msg_id": message.reply_to.reply_to_msg_id if message.reply_to else None,
                    "date": message.date,
                    "mentioned": getattr(message, "mentioned", False)
                })
        return list(reversed(messages))

    async def send_message(self, chat_ref: str, text: str, reply_to: int | None = None) -> int:
        logger.info("send_message session=%s chat_ref=%s reply_to=%s text_len=%s", self.session_name, chat_ref, reply_to, len(text))
        client = await self.connect()
        entity = int(chat_ref) if chat_ref.lstrip('-').isdigit() else chat_ref
        msg = await client.send_message(entity, text, reply_to=reply_to)
        logger.info("send_message ok session=%s chat_ref=%s msg_id=%s", self.session_name, chat_ref, msg.id)
        return msg.id

    @staticmethod
    def _resolve_entity_name(entity_obj) -> str | None:
        if getattr(entity_obj, "title", None):
            return str(entity_obj.title)
        first_name = getattr(entity_obj, "first_name", None)
        last_name = getattr(entity_obj, "last_name", None)
        full_name = " ".join(part for part in [first_name, last_name] if part)
        if full_name:
            return full_name
        if getattr(entity_obj, "username", None):
            return str(entity_obj.username)
        if getattr(entity_obj, "phone", None):
            return str(entity_obj.phone)
        return None

    async def get_chat_title(self, chat_ref: str) -> str | None:
        client = await self.connect()
        entity = int(chat_ref) if chat_ref.lstrip('-').isdigit() else chat_ref
        entity_obj = await client.get_entity(entity)
        return self._resolve_entity_name(entity_obj)

    async def get_account_name(self) -> str | None:
        client = await self.connect()
        me = await client.get_me()
        if me is None:
            return None
        return self._resolve_entity_name(me)

    async def check_chat_membership(self, chat_ref: str) -> bool:
        """Р В РІР‚в„ўР В РЎвЂўР В Р’В·Р В Р вЂ Р РЋР вЂљР В Р’В°Р РЋРІР‚В°Р В Р’В°Р В Р’ВµР РЋРІР‚С™ True, Р В Р’ВµР РЋР С“Р В Р’В»Р В РЎвЂ Р В Р’В°Р В РЎвЂќР В РЎвЂќР В Р’В°Р РЋРЎвЂњР В Р вЂ¦Р РЋРІР‚С™ Р РЋРЎвЂњР В Р’В¶Р В Р’Вµ Р РЋР С“Р В РЎвЂўР РЋР С“Р РЋРІР‚С™Р В РЎвЂўР В РЎвЂР РЋРІР‚С™ Р В Р вЂ  Р РЋРІР‚РЋР В Р’В°Р РЋРІР‚С™Р В Р’Вµ."""
        # Р В РІР‚СњР В Р’В»Р РЋР РЏ Р РЋР С“Р РЋР С“Р РЋРІР‚в„–Р В Р’В»Р В РЎвЂўР В РЎвЂќ-Р В РЎвЂ”Р РЋР вЂљР В РЎвЂР В РЎвЂ“Р В Р’В»Р В Р’В°Р РЋРІвЂљВ¬Р В Р’ВµР В Р вЂ¦Р В РЎвЂР В РІвЂћвЂ“ Р В РЎвЂ”Р РЋР вЂљР В РЎвЂўР В РЎвЂ”Р РЋРЎвЂњР РЋР С“Р В РЎвЂќР В Р’В°Р В Р’ВµР В РЎВ Р В РЎвЂ”Р РЋР вЂљР В РЎвЂўР В Р вЂ Р В Р’ВµР РЋР вЂљР В РЎвЂќР РЋРЎвЂњ Р В РЎвЂ Р РЋР С“Р РЋР вЂљР В Р’В°Р В Р’В·Р РЋРЎвЂњ Р В РЎвЂР В РўвЂР В Р’ВµР В РЎВ Р В Р вЂ  join (Р В РЎвЂўР В Р вЂ¦ Р РЋР С“Р В Р’В°Р В РЎВ Р РЋР вЂљР В Р’В°Р В Р’В·Р В Р’В±Р В Р’ВµР РЋР вЂљР В Р’ВµР РЋРІР‚С™Р РЋР С“Р РЋР РЏ)
        if "t.me/" in chat_ref or chat_ref.strip().startswith("+"):
            return False

        client = await self.connect()
        entity = int(chat_ref) if chat_ref.lstrip('-').isdigit() else chat_ref
        try:
            # get_permissions Р В Р’В±Р РЋР вЂљР В РЎвЂўР РЋР С“Р В Р’В°Р В Р’ВµР РЋРІР‚С™ Р В РЎвЂР РЋР С“Р В РЎвЂќР В Р’В»Р РЋР вЂ№Р РЋРІР‚РЋР В Р’ВµР В Р вЂ¦Р В РЎвЂР В Р’Вµ, Р В Р’ВµР РЋР С“Р В Р’В»Р В РЎвЂ Р В РЎВР РЋРІР‚в„– Р В Р вЂ¦Р В Р’Вµ Р В Р вЂ  Р РЋРІР‚РЋР В Р’В°Р РЋРІР‚С™Р В Р’Вµ
            await client.get_permissions(entity, 'me')
            return True
        except Exception:
            return False

    async def join_chat(self, chat_ref: str) -> str:
        """Р В РІР‚в„ўР РЋР С“Р РЋРІР‚С™Р РЋРЎвЂњР В РЎвЂ”Р В Р’В°Р В Р’ВµР РЋРІР‚С™ Р В Р вЂ  Р РЋРІР‚РЋР В Р’В°Р РЋРІР‚С™ Р В РЎвЂ Р В Р вЂ Р В РЎвЂўР В Р’В·Р В Р вЂ Р РЋР вЂљР В Р’В°Р РЋРІР‚В°Р В Р’В°Р В Р’ВµР РЋРІР‚С™ Р В Р вЂ¦Р В РЎвЂўР РЋР вЂљР В РЎВР В Р’В°Р В Р’В»Р В РЎвЂР В Р’В·Р В РЎвЂўР В Р вЂ Р В Р’В°Р В Р вЂ¦Р В Р вЂ¦Р РЋРІР‚в„–Р В РІвЂћвЂ“ chat_ref (username Р В РЎвЂР В Р’В»Р В РЎвЂ ID)."""
        logger.info("join_chat session=%s chat_ref=%s", self.session_name, chat_ref)
        client = await self.connect()
        raw = chat_ref.strip()
        
        # 1. Р В Р’ВР В Р вЂ¦Р В Р вЂ Р В Р’В°Р В РІвЂћвЂ“Р РЋРІР‚С™-Р РЋР С“Р РЋР С“Р РЋРІР‚в„–Р В Р’В»Р В РЎвЂќР В РЎвЂ t.me/+HASH Р В РЎвЂР В Р’В»Р В РЎвЂ +HASH
        invite_hash = None
        if "t.me/+" in raw or "t.me/joinchat/" in raw:
            invite_hash = raw.split("/")[-1].replace("+", "")
        elif raw.startswith("+") and not raw.lstrip("+").replace("-", "").isdigit():
            invite_hash = raw.lstrip("+")
            
        if invite_hash:
            try:
                updates = await client(ImportChatInviteRequest(invite_hash))
                if hasattr(updates, 'chats') and updates.chats:
                    chat = updates.chats[0]
                    res = getattr(chat, "username", None) or f"-100{chat.id}"
                    logger.info("join_chat via invite ok session=%s result=%s", self.session_name, res)
                    return res
                # Р В РІР‚СћР РЋР С“Р В Р’В»Р В РЎвЂ Р В Р вЂ Р В Р’ВµР РЋР вЂљР В Р вЂ¦Р РЋРЎвЂњР В Р’В»Р В РЎвЂў Р В Р вЂ¦Р В Р’Вµ Updates (Р РЋР вЂљР В Р’ВµР В РўвЂР В РЎвЂќР В РЎвЂў), Р В РЎвЂ”Р В РЎвЂўР В РЎвЂ”Р РЋР вЂљР В РЎвЂўР В Р’В±Р РЋРЎвЂњР В Р’ВµР В РЎВ CheckChatInviteRequest
                invite = await client(CheckChatInviteRequest(invite_hash))
                chat = invite.chat
                res = getattr(chat, "username", None) or f"-100{chat.id}"
                return res
            except UserAlreadyParticipantError:
                invite = await client(CheckChatInviteRequest(invite_hash))
                chat = invite.chat
                res = getattr(chat, "username", None) or f"-100{chat.id}"
                logger.info("join_chat already participant session=%s result=%s", self.session_name, res)
                return res
            except Exception as e:
                logger.error("Failed to join via invite hash %s: %s", invite_hash, e)
                raise

        # 2. Р В РЎвЂєР В Р’В±Р РЋРІР‚в„–Р РЋРІР‚РЋР В Р вЂ¦Р РЋРІР‚в„–Р В РІвЂћвЂ“ ID Р В РЎвЂР В Р’В»Р В РЎвЂ Р РЋР вЂ№Р В Р’В·Р В Р’ВµР РЋР вЂљР В Р вЂ¦Р В Р’ВµР В РІвЂћвЂ“Р В РЎВ
        entity = int(raw) if raw.lstrip('-').isdigit() else raw
        try:
            entity_obj = await client.get_entity(entity)
            try:
                await client(JoinChannelRequest(entity_obj))
                logger.info("join_chat via JoinChannelRequest ok session=%s chat_ref=%s", self.session_name, chat_ref)
            except UserAlreadyParticipantError:
                pass
            
            res = getattr(entity_obj, "username", None) or f"-100{entity_obj.id}"
            return res
        except Exception as e:
            logger.error("Failed to join chat %s: %s", entity, e)
            raise

    async def create_group(
        self,
        title: str,
        about: str,
        username: str | None = None,
        pinned_post: str | None = None,
    ) -> str:
        import asyncio
        logger.info("create_group session=%s title=%r username=%s", self.session_name, title, username)
        client = await self.connect()
        result = await client(CreateChannelRequest(title=title, about=about, megagroup=True))
        chat = result.chats[0]
        # Р В РІР‚СњР В Р’В°Р РЋРІР‚ВР В РЎВ Telegram 10 Р РЋР С“Р В Р’ВµР В РЎвЂќР РЋРЎвЂњР В Р вЂ¦Р В РўвЂ Р В Р вЂ¦Р В Р’В° Р РЋР вЂљР В Р’ВµР В РЎвЂ“Р В РЎвЂР РЋР С“Р РЋРІР‚С™Р РЋР вЂљР В Р’В°Р РЋРІР‚В Р В РЎвЂР РЋР вЂ№ Р В РЎвЂ“Р РЋР вЂљР РЋРЎвЂњР В РЎвЂ”Р В РЎвЂ”Р РЋРІР‚в„– Р В РЎвЂ”Р В Р’ВµР РЋР вЂљР В Р’ВµР В РўвЂ Р РЋР вЂљР В Р’ВµР В РўвЂР В Р’В°Р В РЎвЂќР РЋРІР‚С™Р В РЎвЂР РЋР вЂљР В РЎвЂўР В Р вЂ Р В Р’В°Р В Р вЂ¦Р В РЎвЂР В Р’ВµР В РЎВ
        await asyncio.sleep(10)
        if username:
            await client(UpdateUsernameRequest(channel=chat, username=username))
        if pinned_post:
            await client.send_message(chat, pinned_post)

        chat_ref = getattr(chat, "username", None) or f"-100{chat.id}"
        logger.info("create_group ok session=%s chat_ref=%s", self.session_name, chat_ref)
        # Р В РІР‚СћР РЋР С“Р В Р’В»Р В РЎвЂ Р РЋР вЂ№Р В Р’В·Р В Р’ВµР РЋР вЂљР В Р вЂ¦Р В Р’ВµР В РІвЂћвЂ“Р В РЎВР В Р’В° Р В Р вЂ¦Р В Р’ВµР РЋРІР‚С™, Р В Р вЂ Р В РЎвЂўР В Р’В·Р В Р вЂ Р РЋР вЂљР В Р’В°Р РЋРІР‚В°Р В Р’В°Р В Р’ВµР В РЎВ ID Р РЋР С“ Р В РЎвЂ”Р РЋР вЂљР В Р’ВµР РЋРІР‚С›Р В РЎвЂР В РЎвЂќР РЋР С“Р В РЎвЂўР В РЎВ -100 Р В РўвЂР В Р’В»Р РЋР РЏ Р В РЎвЂќР В РЎвЂўР РЋР вЂљР РЋР вЂљР В Р’ВµР В РЎвЂќР РЋРІР‚С™Р В Р вЂ¦Р В РЎвЂўР В РІвЂћвЂ“ Р РЋР вЂљР В Р’В°Р В Р’В±Р В РЎвЂўР РЋРІР‚С™Р РЋРІР‚в„– Telethon
        return chat_ref

