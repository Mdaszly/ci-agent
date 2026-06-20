from fastapi import HTTPException

from app.core.security import (
    estimate_budget_usage,
    estimate_source_count,
    validate_image_name,
    validate_public_url,
)


def test_validate_public_url_blocks_localhost() -> None:
    try:
        validate_public_url("http://localhost:8000/private")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "私网" in exc.detail
    else:
        raise AssertionError("localhost URL should be blocked")


def test_validate_public_url_requires_http_scheme() -> None:
    try:
        validate_public_url("file:///etc/passwd")
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "http/https" in exc.detail
    else:
        raise AssertionError("file scheme should be blocked")


def test_estimate_source_count() -> None:
    assert estimate_source_count(url_count=2, has_comments=True, image_count=1) == 4


def test_validate_image_name_blocks_path_traversal() -> None:
    try:
        validate_image_name("../secret.png")
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("path traversal image name should be blocked")


def test_estimate_budget_usage_scales_with_sources() -> None:
    tokens, cost = estimate_budget_usage(3)
    assert tokens == 3900
    assert cost > 0
