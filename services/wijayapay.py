
from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import string
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp

from config.config import (
    WIJAYAPAY_API_KEY,
    WIJAYAPAY_API_URL,
    WIJAYAPAY_CALLBACK_URL,
    WIJAYAPAY_CODE_MERCHANT,
)

WIB = ZoneInfo("Asia/Jakarta")

_UA = {
    "User-Agent": "Mozilla/5.0 (compatible; APIQRIS/1.0)",
    "Accept": "application/json",
}

_session: aiohttp.ClientSession | None = None


def _base_url() -> str:
    # Wajib diisi di .env (WIJAYAPAY_API_URL); tidak ada default di kode.
    return (WIJAYAPAY_API_URL or "").strip().rstrip("/")


def _credentials_ready() -> bool:
    return bool(WIJAYAPAY_CODE_MERCHANT and WIJAYAPAY_API_KEY)


def _signature(ref_id: str) -> str:
    # Docs: gabungkan code_merchant + api_key + ref_id (tanpa spasi), md5 hex.
    raw = f"{WIJAYAPAY_CODE_MERCHANT}{WIJAYAPAY_API_KEY}{ref_id}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def verify_signature(ref_id: str, signature: str) -> bool:
    """Validasi header X-Signature callback (create & webhook memakai rumus sama)."""
    if not _credentials_ready():
        return False
    given = str(signature or "").strip().lower()
    if not given:
        return False
    return hmac.compare_digest(given, _signature(ref_id))


def _rand_upper(length: int) -> str:
    """String acak uppercase (A-Z, 0-9) sepanjang ``length``."""
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _new_ref_id() -> str:
    """ref_id unik buatan kita: DEPO-XXXXXXXX-XXXXXX (acak, uppercase)."""
    return f"DEPO-{_rand_upper(8)}-{_rand_upper(6)}"


def _new_unique_code() -> int:
    """Kode unik 1-99 ditambahkan ke nominal."""
    return secrets.randbelow(99) + 1


def _as_int(value: Any) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


def _error_message(data: dict[str, Any], http_status: int) -> str:
    for key in ("message", "error", "detail"):
        val = data.get(key)
        if isinstance(val, dict):
            val = val.get("message") or val.get("detail")
        if val:
            return str(val)
    return f"http_{http_status}"


def _format_expired_at(raw: Any) -> str:
    # Contoh nilai: "2025-12-15 13:34:47" (naive, zona WIB server WijayaPay).
    text = str(raw or "").strip()
    if not text:
        return ""
    try:
        dt = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return text
    return dt.replace(tzinfo=WIB).strftime("%Y-%m-%d %H:%M")


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
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not _credentials_ready():
        return {
            "status": False,
            "message": "WIJAYAPAY_CODE_MERCHANT / WIJAYAPAY_API_KEY not configured",
        }

    base = _base_url()
    if not base:
        return {"status": False, "message": "WIJAYAPAY_API_URL not configured"}

    url = f"{base}{path}"
    headers = {"Content-Type": "application/json"}
    if json_body is not None:
        headers["X-Signature"] = _signature(str(json_body.get("ref_id") or ""))
    session = await _ensure_session()
    try:
        async with session.request(
            method,
            url,
            headers=headers,
            json=json_body,
            params=params,
        ) as resp:
            try:
                data = await resp.json(content_type=None)
            except (ValueError, aiohttp.ContentTypeError):
                data = {}
            if not isinstance(data, dict):
                data = {}
            if resp.status >= 400:
                return {
                    "status": False,
                    "message": _error_message(data, resp.status),
                }
            return data
    except (asyncio.TimeoutError, aiohttp.ClientError) as e:
        return {"status": False, "message": str(e) or type(e).__name__}


async def create_qris(
    *,
    amount: int,
    user_id: int = 0,
    device: str = "",
) -> dict[str, Any]:
    """Buat invoice QRIS. Sukses: {status: True, orderId, qris_string, ...}."""
    nominal = _as_int(amount)
    if nominal <= 0:
        return {"status": False, "message": "invalid amount"}

    # Masa berlaku invoice ditentukan server WijayaPay (tidak ada param expiry).
    unique = _new_unique_code()
    ref_id = _new_ref_id()
    payload: dict[str, Any] = {
        "code_merchant": WIJAYAPAY_CODE_MERCHANT,
        "api_key": WIJAYAPAY_API_KEY,
        "code_payment": "QRIS",
        "payment_method": "QRIS",
        "ref_id": ref_id,
        "nominal": nominal + unique,
    }
    if device and user_id:
        payload["customer_name"] = f"{device}-{int(user_id)}"
    elif user_id:
        payload["customer_name"] = str(int(user_id))
    if WIJAYAPAY_CALLBACK_URL:
        payload["callback_url"] = WIJAYAPAY_CALLBACK_URL

    body = await _request("POST", "/api/transaction/create", json_body=payload)
    if body.get("status") is False or body.get("success") is False:
        return {
            "status": False,
            "message": str(body.get("message") or body.get("error") or "create failed"),
        }
    inv = body.get("data")
    if not isinstance(inv, dict) and (body.get("qr_string") or body.get("ref_id")):
        inv = body
    if not isinstance(inv, dict):
        return {"status": False, "message": "invalid invoice response"}

    order_id = str(inv.get("ref_id") or "").strip()
    qris_string = str(inv.get("qr_string") or "").strip()
    total = _as_int(inv.get("total_bayar"))
    if not order_id or not qris_string or total <= 0:
        return {"status": False, "message": "invalid invoice response"}

    return {
        "status": True,
        "orderId": order_id,
        "qris_string": qris_string,
        "total_bayar": total,
        "unique_code": max(0, total - nominal),
        "trx_reference": str(inv.get("trx_reference") or "").strip(),
        "qr_image": str(inv.get("qr_image") or "").strip(),
        "expired_at": _format_expired_at(inv.get("expired")),
        "data": inv,
    }
