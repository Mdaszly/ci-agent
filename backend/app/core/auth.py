"""认证授权核心模块。

支持 JWT 和 API Key 双模式认证：
- JWT：前端用户登录后获取 access_token + refresh_token
- API Key：服务间/脚本调用，通过 X-API-Key 头传递

认证流程：
1. get_auth_subject 依赖统一解析两种凭证
2. 优先 JWT，其次 API Key，均无则 401
3. 返回 AuthSubject(user_id/api_key_id, tenant_id, role, scopes)
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import auth_settings
from app.db.models import APIKeyDB, UserDB
from app.db.session import get_db_session

logger = logging.getLogger(__name__)

# 密码哈希上下文
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class AuthSubject(BaseModel):
    """认证主体：统一表示 JWT 用户和 API Key 调用方"""

    user_id: Optional[str] = None  # JWT 模式有值
    api_key_id: Optional[str] = None  # API Key 模式有值
    tenant_id: str
    role: str = "user"  # user / admin
    scopes: list[str] = []
    auth_method: str = "jwt"  # jwt / api_key


class TokenPair(BaseModel):
    """JWT 令牌对"""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # access_token 过期秒数


class TokenData(BaseModel):
    """JWT payload 结构"""

    sub: str  # user_id
    tenant_id: str
    role: str
    scopes: list[str] = []
    type: str = "access"  # access / refresh
    exp: Optional[datetime] = None


# ========== 密码哈希 ==========


def hash_password(password: str) -> str:
    """哈希明文密码"""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证明文密码与哈希是否匹配"""
    return pwd_context.verify(plain_password, hashed_password)


# ========== JWT 签发与验证 ==========


def _create_token(data: TokenData, expires_delta: timedelta) -> str:
    """签发 JWT"""
    if not auth_settings.is_configured:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AUTH_JWT_SECRET 未配置，无法签发令牌",
        )
    to_encode = data.model_dump(exclude_none=True)
    expire = datetime.now(timezone.utc) + expires_delta
    to_encode["exp"] = expire
    return jwt.encode(to_encode, auth_settings.jwt_secret, algorithm=auth_settings.jwt_algorithm)


def create_access_token(user: UserDB) -> str:
    """签发 access token"""
    data = TokenData(
        sub=user.id,
        tenant_id=user.tenant_id,
        role=user.role,
        scopes=[],  # P0 阶段无细粒度 scope
        type="access",
    )
    return _create_token(data, timedelta(minutes=auth_settings.access_token_expire_minutes))


def create_refresh_token(user: UserDB) -> str:
    """签发 refresh token"""
    data = TokenData(
        sub=user.id,
        tenant_id=user.tenant_id,
        role=user.role,
        scopes=[],
        type="refresh",
    )
    return _create_token(data, timedelta(days=auth_settings.refresh_token_expire_days))


def decode_token(token: str) -> TokenData:
    """解码并验证 JWT"""
    try:
        payload = jwt.decode(
            token,
            auth_settings.jwt_secret,
            algorithms=[auth_settings.jwt_algorithm],
        )
        return TokenData(**payload)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"令牌无效或已过期: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def create_token_pair(user: UserDB) -> TokenPair:
    """签发 access + refresh 令牌对"""
    return TokenPair(
        access_token=create_access_token(user),
        refresh_token=create_refresh_token(user),
        expires_in=auth_settings.access_token_expire_minutes * 60,
    )


# ========== API Key 生成与校验 ==========


def generate_api_key() -> tuple[str, str]:
    """生成 API Key，返回 (明文 key, key_hash)。

    明文 key 仅在创建时返回一次，后续只存储 hash。
    格式：{prefix}{random_hex}
    """
    random_part = secrets.token_hex(auth_settings.api_key_length // 2)
    plaintext = f"{auth_settings.api_key_prefix}{random_part}"
    key_hash = hash_api_key(plaintext)
    return plaintext, key_hash


def hash_api_key(api_key: str) -> str:
    """计算 API Key 的 SHA256 哈希"""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def get_api_key_prefix(plaintext: str) -> str:
    """提取 API Key 的前 8 位用于展示"""
    return plaintext[:8] + "..."


async def validate_api_key(
    api_key: str, session: AsyncSession
) -> Optional[AuthSubject]:
    """验证 API Key，返回 AuthSubject 或 None"""
    key_hash = hash_api_key(api_key)
    result = await session.execute(
        select(APIKeyDB).where(
            APIKeyDB.key_hash == key_hash,
            APIKeyDB.is_revoked == 0,
        )
    )
    db_key = result.scalar_one_or_none()
    if db_key is None:
        return None

    # 检查过期
    if db_key.expires_at is not None:
        if datetime.now(timezone.utc) > db_key.expires_at:
            return None

    # 更新最后使用时间
    db_key.last_used_at = datetime.now(timezone.utc)
    await session.commit()

    return AuthSubject(
        api_key_id=db_key.id,
        tenant_id=db_key.tenant_id,
        role="api",  # API Key 调用方角色
        scopes=db_key.scopes or [],
        auth_method="api_key",
    )


# ========== FastAPI 依赖 ==========


async def get_auth_subject(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    session: AsyncSession = Depends(get_db_session),
) -> AuthSubject:
    """统一认证依赖：优先 JWT，其次 API Key。

    用法：
        @router.get("/protected")
        async def protected(subject: AuthSubject = Depends(get_auth_subject)):
            ...
    """
    # 认证关闭模式（仅限调试）
    if auth_settings.disabled:
        return AuthSubject(
            user_id="anonymous",
            tenant_id=auth_settings.default_tenant_id,
            role="admin",
            auth_method="disabled",
        )

    # 优先 JWT
    if authorization and authorization.startswith("Bearer "):
        token = authorization.removeprefix("Bearer ").strip()
        token_data = decode_token(token)
        if token_data.type != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="需要 access token，而非 refresh token",
            )
        return AuthSubject(
            user_id=token_data.sub,
            tenant_id=token_data.tenant_id,
            role=token_data.role,
            scopes=token_data.scopes,
            auth_method="jwt",
        )

    # 其次 API Key
    if x_api_key:
        subject = await validate_api_key(x_api_key, session)
        if subject is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API Key 无效、已撤销或已过期",
            )
        return subject

    # 均无凭证
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="未提供认证凭证，需要 Authorization: Bearer <jwt> 或 X-API-Key: <key>",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_optional_auth_subject(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    session: AsyncSession = Depends(get_db_session),
) -> Optional[AuthSubject]:
    """可选认证：有凭证则解析，无凭证返回 None（不报错）"""
    if auth_settings.disabled:
        return AuthSubject(
            user_id="anonymous",
            tenant_id=auth_settings.default_tenant_id,
            role="admin",
            auth_method="disabled",
        )

    if authorization and authorization.startswith("Bearer "):
        try:
            token = authorization.removeprefix("Bearer ").strip()
            token_data = decode_token(token)
            if token_data.type == "access":
                return AuthSubject(
                    user_id=token_data.sub,
                    tenant_id=token_data.tenant_id,
                    role=token_data.role,
                    scopes=token_data.scopes,
                    auth_method="jwt",
                )
        except HTTPException:
            pass

    if x_api_key:
        try:
            subject = await validate_api_key(x_api_key, session)
            if subject:
                return subject
        except Exception:
            pass

    return None


async def require_admin(subject: AuthSubject = Depends(get_auth_subject)) -> AuthSubject:
    """要求管理员权限"""
    if subject.role not in ("admin", "api"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return subject


async def get_current_user(
    subject: AuthSubject = Depends(get_auth_subject),
    session: AsyncSession = Depends(get_db_session),
) -> UserDB:
    """获取当前登录用户（仅 JWT 模式有效）"""
    if subject.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="此接口需要 JWT 认证，不支持 API Key",
        )
    result = await session.execute(select(UserDB).where(UserDB.id == subject.user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已禁用",
        )
    return user
