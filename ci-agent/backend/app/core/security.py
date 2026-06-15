from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse

from fastapi import HTTPException, UploadFile, status


ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp"}
ALLOWED_DOC_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "text/markdown",
}
ALLOWED_FILE_TYPES = ALLOWED_IMAGE_TYPES | ALLOWED_DOC_TYPES
MAX_FILE_BYTES = 10 * 1024 * 1024
SAFE_FILE_NAME = re.compile(r"^[\w.\- ]{1,180}$")


def _is_blocked_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return any(
        [
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        ]
    )


def validate_public_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="只允许 http/https URL",
        )
    if not parsed.hostname:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="URL 缺少 hostname",
        )

    # 检查危险端口
    if parsed.port in {20, 21, 23, 25, 69, 110, 143, 445, 3389, 5900}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="禁止访问危险端口",
        )

    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="URL hostname 无法解析",
        ) from exc

    blocked_ips = set()
    for info in infos:
        address = info[4][0]
        if _is_blocked_ip(address):
            blocked_ips.add(address)

    if blocked_ips:
        # 即使部分 IP 可用，只要有一个私网 IP 就拒绝
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="禁止访问私网、本机或保留地址",
        )
    return url


async def validate_image_upload(file: UploadFile) -> None:
    if file.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="图片类型仅支持 png、jpeg、webp、gif、bmp",
        )

    size = 0
    while chunk := await file.read(1024 * 1024):
        size += len(chunk)
        if size > MAX_FILE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="文件不能超过 10MB",
            )
    await file.seek(0)


async def validate_file_upload(file: UploadFile) -> dict:
    if file.content_type not in ALLOWED_FILE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的文件类型。支持的类型：图片(png,jpeg,webp,gif,bmp)、文档(pdf,doc,docx,txt,md)",
        )

    size = 0
    content = b""
    while chunk := await file.read(1024 * 1024):
        size += len(chunk)
        content += chunk
        if size > MAX_FILE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="文件不能超过 10MB",
            )
    await file.seek(0)

    is_image = file.content_type in ALLOWED_IMAGE_TYPES
    return {
        "filename": file.filename or "uploaded_file",
        "content_type": file.content_type,
        "size": size,
        "is_image": is_image,
        "content": content if not is_image else None,
    }


def estimate_source_count(url_count: int, has_comments: bool, image_count: int) -> int:
    return url_count + (1 if has_comments else 0) + image_count


def validate_image_name(name: str) -> str:
    if "/" in name or "\\" in name or ".." in name or not SAFE_IMAGE_NAME.match(name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="图片文件名不合法",
        )
    return name


def estimate_budget_usage(source_count: int) -> tuple[int, float]:
    estimated_tokens = 1200 + source_count * 900
    estimated_cost = round(estimated_tokens / 1000 * 0.002, 4)
    return estimated_tokens, estimated_cost
