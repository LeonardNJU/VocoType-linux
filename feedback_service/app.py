"""FastAPI adapter for the VoCoType feedback receiver."""

from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .core import FeedbackConfig, FeedbackError, FeedbackStore, MAX_BUNDLE_BYTES
from .multipart import parse_multipart

MAX_REQUEST_BYTES = 6 * 1024 * 1024 + 256 * 1024
app = FastAPI(title="VoCoType Feedback", version="1")
_store: FeedbackStore | None = None


def store() -> FeedbackStore:
    global _store
    if _store is None:
        _store = FeedbackStore(FeedbackConfig.from_env())
    return _store


def _client_ip(request: Request) -> str:
    direct = request.client.host if request.client else "unknown"
    trusted = {item.strip() for item in os.environ.get("VOCOTYPE_FEEDBACK_TRUSTED_PROXIES", "127.0.0.1,::1").split(",") if item.strip()}
    if direct in trusted:
        forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        if forwarded:
            try:
                return str(ipaddress.ip_address(forwarded))
            except ValueError:
                pass
        real_ip = request.headers.get("x-real-ip", "").strip()
        if real_ip:
            try:
                return str(ipaddress.ip_address(real_ip))
            except ValueError:
                pass
    return direct


@app.exception_handler(FeedbackError)
async def feedback_error_handler(_request: Request, exc: FeedbackError) -> JSONResponse:
    headers = {"Retry-After": "3600"} if exc.status_code == 429 else None
    return JSONResponse(
        status_code=exc.status_code,
        content={"ok": False, "error": exc.code, "message": str(exc)},
        headers=headers,
    )


@app.get("/")
async def root() -> dict[str, Any]:
    return {"service": "VoCoType Feedback", "version": 1, "ok": True}


@app.get("/healthz")
async def healthz() -> dict[str, bool]:
    store()
    return {"ok": True}


@app.post("/v1/feedback", status_code=202)
async def receive_feedback(request: Request) -> JSONResponse:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_REQUEST_BYTES:
                raise FeedbackError("请求体过大", status_code=413, code="payload_too_large")
        except ValueError as exc:
            raise FeedbackError("Content-Length 无效") from exc
    body = await request.body()
    if len(body) > MAX_REQUEST_BYTES:
        raise FeedbackError("请求体过大", status_code=413, code="payload_too_large")
    content_type = request.headers.get("content-type", "")
    bundle_name: str | None = None
    bundle_data: bytes | None = None
    if "multipart/form-data" in content_type.casefold():
        payload, bundle_name, bundle_data = parse_multipart(content_type, body)
    elif "application/json" in content_type.casefold():
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FeedbackError("请求体不是有效的 UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise FeedbackError("请求体必须是 JSON 对象")
        encoded = payload.pop("bundle_base64", None)
        bundle_name = payload.pop("bundle_name", None)
        if encoded is not None:
            if not isinstance(encoded, str) or len(encoded) > (MAX_BUNDLE_BYTES * 4 // 3 + 16):
                raise FeedbackError("支持包编码无效", status_code=413, code="payload_too_large")
            try:
                bundle_data = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise FeedbackError("支持包 base64 无效", code="invalid_bundle") from exc
    else:
        raise FeedbackError("只接受 multipart/form-data 或 application/json", status_code=415, code="unsupported_media_type")
    result = store().accept(
        payload,
        source_ip=_client_ip(request),
        bundle_name=bundle_name,
        bundle_data=bundle_data,
    )
    return JSONResponse(status_code=202, content=result.as_response())
