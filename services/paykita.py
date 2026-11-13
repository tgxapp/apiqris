
"""Klien PayKita (https://pay.digikita.id) — order QRIS via REST API.

Dokumentasi (https://pay.digikita.id/documentation):
  Base URL  : https://pay.digikita.id/api
  Auth      : header ``x-api-key: pk_live_...`` (API key project)
  POST /api/orders           -> buat order (HTTP 201)
  GET  /api/orders/:id       -> cek status (berjalan walau langganan habis)
  POST /api/orders/:id/cancel -> batalkan order pending

Mode Hybrid PayKita: ``pay_amount = base_amount + fee + unique_code``.
``base_amount`` = nominal deposit user; ``pay_amount`` = total yang harus
dibayar user (yang dilempar ke QRIS).
"""

from __future__ import annotations

import asyncio
import secrets
import string
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp

from config.config import (
    PAYKITA_API_KEY,
    PAYKITA_API_URL,
    PAYKITA_TTL_SECONDS,
    PAYKITA_WEBHOOK_URL,
)

WIB = ZoneInfo("Asia/Jakarta")

_UA = {
    "User-Agent": "Mozilla/5.0 (compatible; APIQRIS-PayKita/1.0)",
    "Accept": "application/json",
}

_session: aiohttp.ClientSession | None = None


def _base_url() -> str:
    return (PAYKITA_API_URL or "https://pay.digikita.id/api").strip().rstrip("/")


def _credentials_ready() -> bool:
    return bool(PAYKITA_API_KEY)


def _as_int(value: Any) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


def _error_message(data: dict[str, Any], http_status: int) -> str:
    # Format error PayKita: {"ok": false, "error": {"code", "message"}}.
    err = data.get("error")
    if isinstance(err, dict):
        code = str(err.get("code") or "").strip()
        msg = str(err.get("message") or "").strip()
        if code and msg:
            return f"{code}: {msg}"
        return msg or code or f"http_{http_status}"
    for key in ("message", "error", "detail"):
        val = data.get(key)
        if isinstance(val, dict):
            val = val.get("message") or val.get("detail")
        if val:
            return str(val)
    return f"http_{http_status}"


def _format_expired_at(raw: Any) -> str:
    # PayKita: ISO 8601 UTC, contoh "2026-08-07T10:03:21.000Z"
    # -> WIB "2026-08-07 17:03" (format sama dengan layar QRIS bot).
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    return dt.astimezone(WIB).strftime("%Y-%m-%d %H:%M")


def _rand_upper(length: int) -> str:
    """String acak uppercase (A-Z, 0-9) sepanjang ``length``."""
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def new_ref_id() -> str:
    """ref_id unik buatan kita: DEPO-XXXXXXXX-XXXXXX (acak, uppercase).

    Format sama dengan WijayaPay agar klien (bot) tidak perlu membedakan
    provider; dikirim ke PayKita sebagai ``reference``.
    """
    return f"DEPO-{_rand_upper(8)}-{_rand_upper(6)}"


async def _ensure_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        timeout = aiohttp.ClientTimeout(total=30)
        _session = aiohttp.ClientSession(timeout=timeout, headers=_UA)
    return _session


async def close_session() -> None:
    global _session
    if _session and not _session.closed:
        await _session.close()
    _session = None


async def _request(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not _credentials_ready():
        return {"status": False, "message": "PAYKITA_API_KEY not configured"}

    url = f"{_base_url()}{path}"
    headers = {"x-api-key": PAYKITA_API_KEY, "Accept": "application/json"}
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    session = await _ensure_session()
    try:
        async with session.request(method, url, headers=headers, json=json_body) as resp:
            try:
                data = await resp.json(content_type=None)
            except (ValueError, aiohttp.ContentTypeError):
                data = {}
            if not isinstance(data, dict):
                data = {}
            if resp.status >= 400:
                return {"status": False, "message": _error_message(data, resp.status)}
            return data
    except (asyncio.TimeoutError, aiohttp.ClientError) as e:
        return {"status": False, "message": str(e) or type(e).__name__}


async def create_order(
    *,
    amount: int,
    reference: str,
    ttl_seconds: int = 0,
) -> dict[str, Any]:
    """Buat order QRIS PayKita.

    ``amount`` = nominal deposit (``base_amount``), ``reference`` = ref_id kita.
    Sukses: ``{status: True, order_id, pay_amount, qris, checkout_url,
    expires_at, order_status, data}``.
    """
    nominal = _as_int(amount)
    if nominal <= 0:
        return {"status": False, "message": "invalid amount"}
    if not str(reference or "").strip():
        return {"status": False, "message": "invalid reference"}

    ttl = ttl_seconds or PAYKITA_TTL_SECONDS
    body_params: dict[str, Any] = {
        "base_amount": nominal,
        "reference": reference,
        "ttl_seconds": min(86400, max(60, int(ttl))),
    }
    if PAYKITA_WEBHOOK_URL:
        body_params["webhook_url"] = PAYKITA_WEBHOOK_URL
    body = await _request(
        "POST",
        "/orders",
        json_body=body_params,
    )
    if body.get("ok") is False or body.get("status") is False:
        return {"status": False, "message": str(body.get("message") or "create gagal")}

    data = body.get("data") if isinstance(body.get("data"), dict) else body
    order_id = str(data.get("id") or "").strip()
    qris = str(data.get("qris") or "").strip()
    pay_amount = _as_int(data.get("pay_amount"))
    if not order_id or not qris or pay_amount <= 0:
        return {"status": False, "message": "invalid order response"}

    return {
        "status": True,
        "order_id": order_id,
        "pay_amount": pay_amount,
        "qris": qris,
        "checkout_url": str(data.get("checkout_url") or "").strip(),
        "expires_at": _format_expired_at(data.get("expires_at")),
        "order_status": str(data.get("status") or "pending").strip().lower(),
        "data": data,
    }


async def get_order(order_id: str) -> dict[str, Any]:
    """Cek status order langsung ke PayKita.

    Sukses: ``{status: True, order_id, order_status, pay_amount, paid_at,
    expires_at, data}``. ``order_status`` = ``pending`` / ``paid`` /
    ``expired`` / ``cancelled``. Endpoint ini tetap berjalan walau
    langganan API merchant sudah berakhir.
    """
    oid = str(order_id or "").strip()
    if not oid:
        return {"status": False, "message": "invalid order_id"}
    body = await _request("GET", f"/orders/{oid}")
    if body.get("ok") is False or body.get("status") is False:
        return {"status": False, "message": str(body.get("message") or "cek gagal")}
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    return {
        "status": True,
        "order_id": str(data.get("id") or oid).strip(),
        "order_status": str(data.get("status") or "").strip().lower(),
        "pay_amount": _as_int(data.get("pay_amount")),
        "paid_at": str(data.get("paid_at") or ""),
        "expires_at": _format_expired_at(data.get("expires_at")),
        "data": data,
    }


async def cancel_order(order_id: str) -> dict[str, Any]:
    """Batalkan order (hanya status ``pending``; paid/expired/cancelled ditolak)."""
    oid = str(order_id or "").strip()
    if not oid:
        return {"status": False, "message": "invalid order_id"}
    body = await _request("POST", f"/orders/{oid}/cancel")
    if body.get("ok") is False or body.get("status") is False:
        return {"status": False, "message": str(body.get("message") or "cancel gagal")}
    return {"status": True}
