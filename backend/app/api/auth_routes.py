"""认证授权路由。

提供：
- POST /api/auth/login：用户名密码登录，返回 JWT 令牌对
- POST /api/auth/refresh：刷新 access token
- GET /api/auth/me：获取当前用户信息
- POST /api/api-keys：创建 API Key（明文仅返回一次）
- GET /api/api-keys：列出当前租户的 API Key（仅前缀）
- DELETE /api/api-keys/{key_id}：撤销 API Key
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    AuthSubject,
    create_access_token,
    create_refresh_token,
    create_token_pair,
    decode_token,
    generate_api_key,
    get_api_key_prefix,
    get_auth_subject,
    get_current_user,
    hash_api_key,
    hash_password,
    verify_password,
)
from app.core.config import auth_settings, rate_limit_settings
from app.core.limiter import limiter
from app.db.models import APIKeyDB, UserDB
from app.db.session import get_db_session
from app.models.schemas import new_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["auth"])


# ========== 请求/响应模型 ==========


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str


class CreateAPIKeyRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, description="API Key 名称")
    scopes: list[str] = Field(default_factory=list, description="权限范围")
    expires_in_days: Optional[int] = Field(None, ge=1, le=365, description="过期天数")


class APIKeyResponse(BaseModel):
    id: str
    name: str
    key_prefix: str
    scopes: list[str]
    expires_at: Optional[datetime]
    is_revoked: bool
    created_at: datetime
    last_used_at: Optional[datetime]


class APIKeyCreatedResponse(APIKeyResponse):
    """创建 API Key 时返回明文（仅一次）"""
    plaintext_key: str = Field(..., description="明文 API Key，仅此一次返回，请妥善保存")


class UserInfoResponse(BaseModel):
    id: str
    username: str
    tenant_id: str
    role: str
    is_active: bool
    auth_method: str


# ========== 认证端点 ==========


@router.post("/auth/login")
@limiter.limit(rate_limit_settings.auth)
async def login(request: Request, req: LoginRequest, session: AsyncSession = Depends(get_db_session)):
    """用户名密码登录，返回 JWT 令牌对"""
    result = await session.execute(
        select(UserDB).where(UserDB.username == req.username)
    )
    user = result.scalar_one_or_none()

    if user is None or not verify_password(req.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="用户已禁用",
        )

    token_pair = create_token_pair(user)
    logger.info("User %s logged in successfully", user.username)
    return token_pair


@router.post("/auth/refresh")
@limiter.limit(rate_limit_settings.auth)
async def refresh_token(request: Request, req: RefreshRequest):
    """使用 refresh token 刷新 access token"""
    token_data = decode_token(req.refresh_token)

    if token_data.type != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要 refresh token，而非 access token",
        )

    # 构造新的 token 对（不查库，基于 token 中的信息）
    from app.core.auth import TokenData, _create_token
    from datetime import timedelta

    access_data = TokenData(
        sub=token_data.sub,
        tenant_id=token_data.tenant_id,
        role=token_data.role,
        scopes=token_data.scopes,
        type="access",
    )
    refresh_data = TokenData(
        sub=token_data.sub,
        tenant_id=token_data.tenant_id,
        role=token_data.role,
        scopes=token_data.scopes,
        type="refresh",
    )

    return {
        "access_token": _create_token(
            access_data, timedelta(minutes=auth_settings.access_token_expire_minutes)
        ),
        "refresh_token": _create_token(
            refresh_data, timedelta(days=auth_settings.refresh_token_expire_days)
        ),
        "token_type": "bearer",
        "expires_in": auth_settings.access_token_expire_minutes * 60,
    }


@router.get("/auth/me", response_model=UserInfoResponse)
async def get_me(user: UserDB = Depends(get_current_user)):
    """获取当前登录用户信息"""
    return UserInfoResponse(
        id=user.id,
        username=user.username,
        tenant_id=user.tenant_id,
        role=user.role,
        is_active=bool(user.is_active),
        auth_method="jwt",
    )


# ========== API Key 管理端点 ==========


@router.post("/api-keys", response_model=APIKeyCreatedResponse)
async def create_api_key(
    req: CreateAPIKeyRequest,
    subject: AuthSubject = Depends(get_auth_subject),
    session: AsyncSession = Depends(get_db_session),
):
    """创建 API Key（明文仅返回一次）"""
    plaintext, key_hash = generate_api_key()
    key_prefix = get_api_key_prefix(plaintext)

    expires_at = None
    if req.expires_in_days is not None:
        from datetime import timedelta

        expires_at = datetime.now(timezone.utc) + timedelta(days=req.expires_in_days)

    api_key = APIKeyDB(
        id=new_id("apikey"),
        tenant_id=subject.tenant_id,
        key_hash=key_hash,
        key_prefix=key_prefix,
        name=req.name,
        scopes=req.scopes,
        expires_at=expires_at,
        is_revoked=0,
    )
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)

    logger.info(
        "API Key created: name=%s, tenant=%s, prefix=%s",
        req.name,
        subject.tenant_id,
        key_prefix,
    )

    return APIKeyCreatedResponse(
        id=api_key.id,
        name=api_key.name,
        key_prefix=api_key.key_prefix,
        scopes=api_key.scopes or [],
        expires_at=api_key.expires_at,
        is_revoked=bool(api_key.is_revoked),
        created_at=api_key.created_at,
        last_used_at=api_key.last_used_at,
        plaintext_key=plaintext,
    )


@router.get("/api-keys", response_model=list[APIKeyResponse])
async def list_api_keys(
    subject: AuthSubject = Depends(get_auth_subject),
    session: AsyncSession = Depends(get_db_session),
):
    """列出当前租户的 API Key（仅前缀，不含明文）"""
    result = await session.execute(
        select(APIKeyDB)
        .where(APIKeyDB.tenant_id == subject.tenant_id)
        .order_by(APIKeyDB.created_at.desc())
    )
    keys = result.scalars().all()
    return [
        APIKeyResponse(
            id=k.id,
            name=k.name,
            key_prefix=k.key_prefix,
            scopes=k.scopes or [],
            expires_at=k.expires_at,
            is_revoked=bool(k.is_revoked),
            created_at=k.created_at,
            last_used_at=k.last_used_at,
        )
        for k in keys
    ]


@router.delete("/api-keys/{key_id}")
async def revoke_api_key(
    key_id: str,
    subject: AuthSubject = Depends(get_auth_subject),
    session: AsyncSession = Depends(get_db_session),
):
    """撤销 API Key"""
    result = await session.execute(
        select(APIKeyDB).where(
            APIKeyDB.id == key_id,
            APIKeyDB.tenant_id == subject.tenant_id,
        )
    )
    api_key = result.scalar_one_or_none()

    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API Key 不存在",
        )

    api_key.is_revoked = 1
    await session.commit()

    logger.info("API Key revoked: id=%s, name=%s", key_id, api_key.name)
    return {"status": "revoked", "id": key_id}


# ========== 用户注册（管理员专用，P0 简化版） ==========


class RegisterUserRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=6, max_length=128)
    role: str = Field("user", pattern="^(user|admin)$")
    tenant_id: Optional[str] = None


@router.post("/auth/register", response_model=UserInfoResponse)
@limiter.limit(rate_limit_settings.auth)
async def register_user(
    request: Request,
    req: RegisterUserRequest,
    subject: AuthSubject = Depends(get_auth_subject),
    session: AsyncSession = Depends(get_db_session),
):
    """注册新用户（需要管理员权限）"""
    if subject.role not in ("admin", "api"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限才能注册新用户",
        )

    # 检查用户名是否已存在
    existing = await session.execute(
        select(UserDB).where(UserDB.username == req.username)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="用户名已存在",
        )

    user = UserDB(
        id=new_id("user"),
        tenant_id=req.tenant_id or subject.tenant_id,
        username=req.username,
        password_hash=hash_password(req.password),
        role=req.role,
        is_active=1,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    logger.info("User registered: username=%s, role=%s", user.username, user.role)
    return UserInfoResponse(
        id=user.id,
        username=user.username,
        tenant_id=user.tenant_id,
        role=user.role,
        is_active=bool(user.is_active),
        auth_method="jwt",
    )
