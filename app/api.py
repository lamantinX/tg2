from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.schemas import (
    AccountCreate,
    AccountRead,
    BindingCreate,
    BindingRead,
    BindingUpdate,
    CharacterRead,
    CharacterAssignRequest,
    GenerateMessageRequest,
    GroupCreateRequest,
    LoginCodeRequest,
    LoginCompleteRequest,
    LoginPasswordRequest,
)
from app.services import AccountService, BindingService, CharacterService, ChatAutomationService

from app.proxy_manager import proxy_manager

router = APIRouter()


@router.get("/health")
async def healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/proxy/stats")
async def get_proxy_stats() -> dict:
    return await proxy_manager.get_stats()


@router.post("/proxy/check")
async def check_proxies(session: AsyncSession = Depends(get_session)) -> dict:
    """Принудительно запускает проверку здоровья всех прокси"""
    await proxy_manager.health_check_all(session)
    return {"status": "started"}


@router.post("/proxy/rotate/{account_id}")
async def rotate_account_proxy(account_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """Принудительно меняет прокси для конкретного аккаунта"""
    # Удаляем текущую привязку
    async with proxy_manager._lock:
        if account_id in proxy_manager.account_to_session:
            session_id = proxy_manager.account_to_session.pop(account_id)
            if session_id in proxy_manager.sessions:
                proxy_manager.sessions[session_id].unassign_account(account_id)
    
    # Получаем новый прокси
    proxy_url = await proxy_manager.get_proxy_for_account(account_id, session)
    return {"proxy_url": proxy_url}


@router.get("/accounts", response_model=list[AccountRead])
async def list_accounts(session: AsyncSession = Depends(get_session)) -> list[object]:
    return await AccountService(session).list_accounts()


@router.post("/accounts", response_model=AccountRead)
async def create_account(payload: AccountCreate, session: AsyncSession = Depends(get_session)) -> object:
    return await AccountService(session).create_account(phone=payload.phone, proxy_url=payload.proxy_url)


@router.get("/characters", response_model=list[CharacterRead])
async def list_characters(session: AsyncSession = Depends(get_session)) -> list[object]:
    return await CharacterService(session).list_characters()


@router.post("/accounts/character/assign")
async def assign_character(payload: CharacterAssignRequest, session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    await CharacterService(session).assign_character(account_id=payload.account_id, character_id=payload.character_id)
    return {"status": "assigned"}


@router.post("/accounts/audit")
async def audit_accounts(session: AsyncSession = Depends(get_session)) -> dict[str, object]:
    return await AccountService(session).audit_accounts()


@router.post("/accounts/login/request")
async def request_login_code(payload: LoginCodeRequest, session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    try:
        status = await AccountService(session).request_login_code(account_id=payload.account_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": status}


@router.post("/accounts/login/complete")
async def complete_login(payload: LoginCompleteRequest, session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    try:
        status = await AccountService(session).complete_login(
            account_id=payload.account_id,
            code=payload.code,
            password=payload.password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": status}


@router.post("/accounts/login/password")
async def complete_password_login(payload: LoginPasswordRequest, session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    try:
        status = await AccountService(session).complete_password_login(
            account_id=payload.account_id,
            password=payload.password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": status}


@router.get("/bindings", response_model=list[BindingRead])
async def list_bindings(session: AsyncSession = Depends(get_session)) -> list[object]:
    return await BindingService(session).list_bindings()


@router.post("/bindings", response_model=BindingRead)
async def create_binding(payload: BindingCreate, session: AsyncSession = Depends(get_session)) -> object:
    try:
        return await BindingService(session).create_binding(
            account_id=payload.account_id,
            chat_ref=payload.chat_ref,
            interval_minutes=payload.interval_minutes,
            context_message_count=payload.context_message_count,
            system_prompt=payload.system_prompt,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/bindings/{binding_id}", response_model=BindingRead)
async def get_binding(binding_id: int, session: AsyncSession = Depends(get_session)) -> object:
    try:
        return await BindingService(session).get_binding(binding_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/bindings/{binding_id}", response_model=BindingRead)
async def update_binding(binding_id: int, payload: BindingUpdate, session: AsyncSession = Depends(get_session)) -> object:
    try:
        return await BindingService(session).update_binding_settings(
            binding_id=binding_id,
            interval_min_minutes=payload.interval_min_minutes,
            interval_max_minutes=payload.interval_max_minutes,
            context_message_count=payload.context_message_count,
            system_prompt=payload.system_prompt,
            reset_prompt=payload.reset_prompt,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/bindings/{binding_id}")
async def delete_binding(binding_id: int, session: AsyncSession = Depends(get_session)) -> dict[str, int]:
    try:
        await BindingService(session).delete_binding(binding_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": binding_id}


@router.post("/generate")
async def generate_message(payload: GenerateMessageRequest, session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    try:
        content = await ChatAutomationService(session).generate_and_send(
            account_id=payload.account_id,
            chat_ref=payload.chat_ref,
            context_message_count=payload.context_message_count,
            system_prompt=payload.system_prompt,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"content": content}


@router.post("/groups")
async def create_group(payload: GroupCreateRequest, session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    try:
        chat_ref = await ChatAutomationService(session).create_group(
            account_id=payload.account_id,
            description=payload.description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"chat_ref": chat_ref}
