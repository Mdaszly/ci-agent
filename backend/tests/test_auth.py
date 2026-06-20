"""认证授权模块测试。

覆盖：
- 密码哈希与验证
- JWT 签发与解码（有效/过期/无效）
- API Key 生成、哈希、前缀提取
- 认证依赖 get_auth_subject（JWT/API Key/无凭证/禁用模式）
"""
from __future__ import annotations

import os
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 确保测试环境有 JWT secret
os.environ.setdefault("AUTH_JWT_SECRET", "test-secret-key-for-unit-tests-32chars")

from app.core.auth import (
    AuthSubject,
    TokenData,
    _create_token,
    create_access_token,
    create_refresh_token,
    create_token_pair,
    decode_token,
    generate_api_key,
    get_api_key_prefix,
    get_auth_subject,
    hash_api_key,
    hash_password,
    verify_password,
)
from app.core.config import auth_settings
from app.db.models import UserDB


# ========== 密码哈希测试 ==========


class TestPasswordHashing:
    def test_hash_password_returns_bcrypt_hash(self) -> None:
        hashed = hash_password("mypassword123")
        assert hashed != "mypassword123"
        assert hashed.startswith("$2")  # bcrypt 前缀

    def test_verify_password_correct(self) -> None:
        hashed = hash_password("correct-password")
        assert verify_password("correct-password", hashed) is True

    def test_verify_password_wrong(self) -> None:
        hashed = hash_password("correct-password")
        assert verify_password("wrong-password", hashed) is False

    def test_hash_password_different_salts(self) -> None:
        h1 = hash_password("same-password")
        h2 = hash_password("same-password")
        assert h1 != h2  # 不同盐值产生不同哈希


# ========== JWT 测试 ==========


class TestJWT:
    def _make_test_user(self) -> UserDB:
        return UserDB(
            id="user-test-001",
            tenant_id="tenant-001",
            username="testuser",
            password_hash=hash_password("pass123"),
            role="user",
            is_active=1,
        )

    def test_create_access_token_returns_string(self) -> None:
        auth_settings.jwt_secret = "test-secret"
        user = self._make_test_user()
        token = create_access_token(user)
        assert isinstance(token, str)
        assert len(token) > 0

    def test_create_refresh_token_returns_string(self) -> None:
        auth_settings.jwt_secret = "test-secret"
        user = self._make_test_user()
        token = create_refresh_token(user)
        assert isinstance(token, str)

    def test_create_token_pair(self) -> None:
        auth_settings.jwt_secret = "test-secret"
        user = self._make_test_user()
        pair = create_token_pair(user)
        assert pair.access_token
        assert pair.refresh_token
        assert pair.token_type == "bearer"
        assert pair.expires_in > 0

    def test_decode_token_valid(self) -> None:
        auth_settings.jwt_secret = "test-secret"
        user = self._make_test_user()
        token = create_access_token(user)
        token_data = decode_token(token)
        assert token_data.sub == user.id
        assert token_data.tenant_id == user.tenant_id
        assert token_data.role == user.role
        assert token_data.type == "access"

    def test_decode_token_expired(self) -> None:
        auth_settings.jwt_secret = "test-secret"
        user = self._make_test_user()
        # 签发一个已过期的 token
        data = TokenData(
            sub=user.id,
            tenant_id=user.tenant_id,
            role=user.role,
            type="access",
        )
        token = _create_token(data, timedelta(seconds=-1))  # 过期 1 秒
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            decode_token(token)
        assert exc_info.value.status_code == 401

    def test_decode_token_invalid_signature(self) -> None:
        auth_settings.jwt_secret = "secret-A"
        user = self._make_test_user()
        token = create_access_token(user)
        # 用不同 secret 验证
        auth_settings.jwt_secret = "secret-B"
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            decode_token(token)
        assert exc_info.value.status_code == 401

    def test_decode_token_malformed(self) -> None:
        auth_settings.jwt_secret = "test-secret"
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            decode_token("not.a.valid.jwt.token")
        assert exc_info.value.status_code == 401


# ========== API Key 测试 ==========


class TestAPIKey:
    def test_generate_api_key_returns_tuple(self) -> None:
        plaintext, key_hash = generate_api_key()
        assert isinstance(plaintext, str)
        assert isinstance(key_hash, str)
        assert plaintext.startswith(auth_settings.api_key_prefix)

    def test_generate_api_key_unique(self) -> None:
        k1, _ = generate_api_key()
        k2, _ = generate_api_key()
        assert k1 != k2

    def test_hash_api_key_is_sha256(self) -> None:
        key_hash = hash_api_key("ci_testkey123")
        # SHA256 产生 64 位十六进制字符串
        assert len(key_hash) == 64
        assert all(c in "0123456789abcdef" for c in key_hash)

    def test_hash_api_key_deterministic(self) -> None:
        h1 = hash_api_key("same-key")
        h2 = hash_api_key("same-key")
        assert h1 == h2

    def test_hash_api_key_not_plaintext(self) -> None:
        plaintext = "ci_secret-key-12345"
        key_hash = hash_api_key(plaintext)
        assert plaintext not in key_hash
        assert key_hash != plaintext

    def test_get_api_key_prefix(self) -> None:
        prefix = get_api_key_prefix("ci_abcdef1234567890")
        assert prefix == "ci_abcde..."
        assert len(prefix) == 11  # 8 字符 + "..."


# ========== 认证依赖测试 ==========


class TestAuthSubject:
    def test_auth_subject_jwt_mode(self) -> None:
        subject = AuthSubject(
            user_id="user-001",
            tenant_id="tenant-001",
            role="user",
            auth_method="jwt",
        )
        assert subject.user_id == "user-001"
        assert subject.api_key_id is None
        assert subject.auth_method == "jwt"

    def test_auth_subject_api_key_mode(self) -> None:
        subject = AuthSubject(
            api_key_id="key-001",
            tenant_id="tenant-001",
            role="api",
            auth_method="api_key",
        )
        assert subject.api_key_id == "key-001"
        assert subject.user_id is None
        assert subject.auth_method == "api_key"


class TestGetAuthSubject:
    @pytest.mark.asyncio
    async def test_no_credentials_raises_401(self) -> None:
        from fastapi import HTTPException

        session_mock = AsyncMock()
        with pytest.raises(HTTPException) as exc_info:
            await get_auth_subject(
                authorization=None,
                x_api_key=None,
                session=session_mock,
            )
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_disabled_mode_returns_anonymous(self) -> None:
        original_disabled = auth_settings.disabled
        try:
            auth_settings.disabled = True
            session_mock = AsyncMock()
            subject = await get_auth_subject(
                authorization=None,
                x_api_key=None,
                session=session_mock,
            )
            assert subject.user_id == "anonymous"
            assert subject.role == "admin"
            assert subject.auth_method == "disabled"
        finally:
            auth_settings.disabled = original_disabled

    @pytest.mark.asyncio
    async def test_valid_jwt_returns_subject(self) -> None:
        auth_settings.jwt_secret = "test-secret"
        auth_settings.disabled = False
        # 构造一个有效 JWT
        user = UserDB(
            id="user-jwt-test",
            tenant_id="tenant-jwt",
            username="jwtuser",
            password_hash="hash",
            role="admin",
            is_active=1,
        )
        token = create_access_token(user)
        session_mock = AsyncMock()

        subject = await get_auth_subject(
            authorization=f"Bearer {token}",
            x_api_key=None,
            session=session_mock,
        )
        assert subject.user_id == "user-jwt-test"
        assert subject.tenant_id == "tenant-jwt"
        assert subject.role == "admin"
        assert subject.auth_method == "jwt"

    @pytest.mark.asyncio
    async def test_refresh_token_rejected_for_access(self) -> None:
        auth_settings.jwt_secret = "test-secret"
        auth_settings.disabled = False
        user = UserDB(
            id="user-refresh-test",
            tenant_id="tenant-refresh",
            username="refreshuser",
            password_hash="hash",
            role="user",
            is_active=1,
        )
        # 用 refresh token 尝试访问
        token = create_refresh_token(user)
        session_mock = AsyncMock()

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await get_auth_subject(
                authorization=f"Bearer {token}",
                x_api_key=None,
                session=session_mock,
            )
        assert exc_info.value.status_code == 401
        assert "access token" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_invalid_api_key_raises_401(self) -> None:
        auth_settings.disabled = False
        session_mock = AsyncMock()
        # mock validate_api_key 返回 None
        with patch("app.core.auth.validate_api_key", new_callable=AsyncMock, return_value=None):
            from fastapi import HTTPException

            with pytest.raises(HTTPException) as exc_info:
                await get_auth_subject(
                    authorization=None,
                    x_api_key="ci_invalid_key",
                    session=session_mock,
                )
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_api_key_returns_subject(self) -> None:
        auth_settings.disabled = False
        session_mock = AsyncMock()
        mock_subject = AuthSubject(
            api_key_id="key-001",
            tenant_id="tenant-api",
            role="api",
            auth_method="api_key",
        )
        with patch("app.core.auth.validate_api_key", new_callable=AsyncMock, return_value=mock_subject):
            subject = await get_auth_subject(
                authorization=None,
                x_api_key="ci_valid_key",
                session=session_mock,
            )
            assert subject.api_key_id == "key-001"
            assert subject.auth_method == "api_key"
