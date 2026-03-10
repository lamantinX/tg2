from pydantic import BaseModel, Field


class AccountCreate(BaseModel):
    phone: str
    proxy_url: str | None = None


class CharacterRead(BaseModel):
    id: int
    name: str
    gender: str | None
    age: int | None
    occupation: str | None
    personality: str | None
    likes: str | None
    dislikes: str | None
    speech_style: str | None
    background: str | None
    location: str | None

    class Config:
        from_attributes = True


class AccountRead(BaseModel):
    id: int
    phone: str
    session_name: str
    proxy_url: str | None
    proxy_session_id: str | None
    auth_status: str
    is_active: bool
    character_id: int | None
    character: CharacterRead | None

    class Config:
        from_attributes = True


class BindingCreate(BaseModel):
    account_id: int
    chat_ref: str
    interval_minutes: int = Field(default=10, ge=1, le=1440)
    context_message_count: int = Field(default=12, ge=1, le=200)
    system_prompt: str | None = None


class BindingUpdate(BaseModel):
    interval_min_minutes: int | None = Field(default=None, ge=1, le=1440)
    interval_max_minutes: int | None = Field(default=None, ge=1, le=1440)
    context_message_count: int | None = Field(default=None, ge=1, le=200)
    system_prompt: str | None = None
    reset_prompt: bool = False


class BindingRead(BaseModel):
    id: int
    account_id: int
    chat_ref: str
    interval_minutes: int
    interval_min_minutes: int
    interval_max_minutes: int
    context_message_count: int
    system_prompt: str | None
    is_enabled: bool

    class Config:
        from_attributes = True


class GroupCreateRequest(BaseModel):
    account_id: int
    description: str


class GenerateMessageRequest(BaseModel):
    account_id: int
    chat_ref: str
    context_message_count: int = Field(default=12, ge=1, le=200)
    system_prompt: str | None = None


class LoginCodeRequest(BaseModel):
    account_id: int


class LoginCompleteRequest(BaseModel):
    account_id: int
    code: str
    password: str | None = None


class LoginPasswordRequest(BaseModel):
    account_id: int
    password: str


class CharacterAssignRequest(BaseModel):
    account_id: int
    character_id: int
