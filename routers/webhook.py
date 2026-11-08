
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

from config.config import REDIS_URL, WIJAYAPAY_CALLBACK_IP
from data.redis import update_deposit
from services.wijayapay import verify_signature

router = APIRouter()

WIB = ZoneInfo("Asia/Jakarta")


async def _handle(payload: dict, data: dict, ref_id: str) -> None:
    """Update record invoice di Redis dengan status dari callback."""
    if not REDIS_URL:
        print("[WEBHOOK] REDIS_URL kosong - callback tidak disimpan", flush=True)
        return
    fields = {
        "status": str(payload.get("status") or "").strip().lower(),
        "total_dibayar": data.get("total_dibayar"),
        "total_fee": data.get("total_fee"),
        "amount_received": data.get("amount_received"),
        "payment_methode": data.get("payment_methode"),
        "callback_updated_at": str(data.get("updated_at") or ""),
        # Waktu callback diterima apiqris (WIB).
        "received_at": datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S"),
        # Payload asli WijayaPay untuk audit.
        "callback": payload,
    }
    try:
        await update_deposit(
            ref_id,
            str(data.get("trx_reference") or "").strip(),
            fields,
        )
    except Exception as exc:  # Redis down dsb — jangan gagalkan webhook.
        print(f"[WEBHOOK] simpan Redis gagal ref_id={ref_id}: {exc}", flush=True)


def _client_ip(request: Request) -> str:
    """IP pengirim: X-Real-IP (nginx) -> X-Forwarded-For -> client.host."""
    real = (request.headers.get("x-real-ip") or "").strip()
    if real:
        return real
    forwarded = (request.headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def _ip_allowed(ip: str) -> bool:
    # Whitelist bisa berisi beberapa IP dipisah koma. Kosong = tidak dicek.
    whitelist = [part.strip() for part in WIJAYAPAY_CALLBACK_IP.split(",") if part.strip()]
    return not whitelist or ip in whitelist


@router.post("/webhook")
async def webhook(request: Request, tasks: BackgroundTasks) -> JSONResponse:
    body = await request.body()
    ip = _client_ip(request)
    # Log semuanya (dipotong): bila format callback asli WijayaPay tidak persis
    # seperti docs, ini satu-satunya cara melihat payload aslinya.
    print(
        f"[WEBHOOK] ip={ip} headers={dict(request.headers)} body={body[:400]!r}",
        flush=True,
    )

    if not _ip_allowed(ip):
        print(f"[WEBHOOK] ip {ip} ditolak (tidak ada di whitelist)", flush=True)
        return JSONResponse(
            {"status": False, "message": "ip tidak diizinkan"},
            status_code=403,
        )

    payload: dict = {}
    try:
        parsed = json.loads((body or b"").decode("utf-8", "ignore"))
        if isinstance(parsed, dict):
            payload = parsed
    except ValueError:
        pass

    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    ref_id = str(data.get("ref_id") or payload.get("ref_id") or "").strip()
    if not ref_id:
        print(f"[WEBHOOK] payload tanpa ref_id: {payload}", flush=True)
        return JSONResponse(
            {"status": False, "message": "payload tanpa ref_id"},
            status_code=400,
        )

    signature = (request.headers.get("x-signature") or "").strip()
    if not verify_signature(ref_id, signature):
        print(
            f"[WEBHOOK] signature tidak valid ref_id={ref_id} x-signature={signature!r}",
            flush=True,
        )
        return JSONResponse(
            {"status": False, "message": "signature tidak valid"},
            status_code=401,
        )

    print(
        f"[WEBHOOK] ok ref_id={ref_id} status={payload.get('status')} "
        f"total_dibayar={data.get('total_dibayar')} total_fee={data.get('total_fee')} "
        f"amount_received={data.get('amount_received')} "
        f"trx_reference={data.get('trx_reference')} "
        f"payment_methode={data.get('payment_methode')} "
        f"updated_at={data.get('updated_at')}",
        flush=True,
    )
    tasks.add_task(_handle, payload, data, ref_id)
    # Format balasan yang diharapkan gateway WijayaPay.
    return JSONResponse({"status": True})
