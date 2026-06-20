from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
from contextlib import closing

import uvicorn

DEFAULT_HOST = os.getenv("BACKEND_HOST", "0.0.0.0")
DEFAULT_PORT = int(os.getenv("BACKEND_PORT", "8000"))

logger = logging.getLogger("ci_agent.backend.startup")


def _is_port_occupied(host: str, port: int) -> tuple[bool, str | None]:
    candidates = [host]
    if host in {"0.0.0.0", "::", ""}:
        candidates = ["127.0.0.1", "localhost"]

    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            with closing(socket.create_connection((candidate, port), timeout=0.5)):
                return True, candidate
        except OSError:
            continue
    return False, None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start the ci-agent backend with a port guard.")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind host for the backend server.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port for the backend server.")
    parser.add_argument("--reload", action="store_true", help="Enable uvicorn reload mode.")
    parser.add_argument("--check-only", action="store_true", help="Only validate that the target port is free.")
    return parser


def _fail_if_port_busy(host: str, port: int) -> bool:
    occupied, bound_host = _is_port_occupied(host, port)
    if occupied:
        logger.error(
            "启动中止：端口 %s 已被其他后端进程占用（探测到 %s:%s）。请先停止已启动的后端，再重新启动。",
            port,
            bound_host,
            port,
        )
        return True

    logger.info("Port check passed: %s:%s is available.", host, port)
    return False


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _build_parser().parse_args(argv)

    if _fail_if_port_busy(args.host, args.port):
        return 1
    if args.check_only:
        return 0

    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload)
    return 0


if __name__ == "__main__":
    sys.exit(main())