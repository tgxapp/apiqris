
"""POST /webhook-paykita — terima notifikasi pembayaran dari PayKita.

Kontrak docs (https://pay.digikita.id/documentation):

    header  : x-paykita-timestamp, x-paykita-signature (``v1=<hex>``)
    signature: HMAC-SHA256(PAYKITA_WEBHOOK_SECRET, timestamp + "." + raw_body)
    payload : {"id": "evt_...", "event": "order.paid",
               "data": {"order_id": "pay_...", "reference": "DEPO-...",
                         "amount": 50787, "status": "paid", "paid_at": "..."}}

Aturan PayKita:

- Endpoint HARUS HTTPS tanpa redirect (redirect dianggap gagal).
- Harus balas 200-299 dalam 10 detik; proses berat lewat BackgroundTasks.
- PayKita retry hingga 9x saat gagal (3 dtk s/d 3 jam) -> wajib idempotent;
  update record Redis bersifat overwrite sehingga aman untuk event ulang.
- Event ``webhook.test`` dari tombol Kirim Test cukup dibalas 200.

URL webhook dikirim per order saat create (field ``webhook_url``) bila
``PAYKITA_WEBHOOK_URL`` diisi di .env, atau dipasang global di dashboard
PayKita (Pengaturan -> Webhook -> Webhook Default Project).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

from config.config import PAYKITA_WEBHOOK_SECRET, REDIS_URL
from data.redis import update_deposit

WIB = ZoneInfo("Asia/Jakarta")

router = APIRouter()


def _signature_valid(timestamp: str, received: str, raw_body: bytes) -> bool:
    """Validasi header x-paykita-signature (HMAC-SHA256 hex, prefix ``v1=``)."""
    if not PAYKITA_WEBHOOK_SECRET:
        return False
    given = str(received or "").strip()
    if given.lower().startswith("v1="):
        given = given[3:].strip()
    if not given or not timestamp:
        return False
    signed = str(timestamp).encode("utf-8") + b"." + (raw_body or b"")
    expected = hmac.new(
        PAYKITA_WEBHOOK_SECRET.encode("utf-8"),
        signed,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(given.lower(), expected.lower())


async def _handle(
    payload: dict,
    data: dict,
    reference: str,
    order_id: str,
) -> None:
    """Update record invoice di Redis dengan status dari webhook."""
    if not REDIS_URL:
        print("[WEBHOOK-PK] REDIS_URL kosong - callback tidak disimpan", flush=True)
        return
    fields = {
        "status": str(data.get("status") or "paid").strip().lower(),
        "provider": "paykita",
        "total_dibayar": data.get("amount"),
        "paykita_order_id": order_id,
        "paid_at": str(data.get("paid_at") or ""),
        # Waktu webhook diterima apiqris (WIB).
        "received_at": datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S"),
        # Payload asli PayKita untuk audit.
        "callback": payload,
    }
    try:
        await update_deposit(reference, order_id, fields)
    except Exception as exc:  # Redis down dsb — jangan gagalkan webhook.
        print(f"[WEBHOOK-PK] simpan Redis gagal ref_id={reference}: {exc}", flush=True)


@router.post("/webhook-paykita")
async def webhook_paykita(request: Request, tasks: BackgroundTasks) -> JSONResponse:
    body = await request.body()
    # Log semuanya (dipotong): bila format webhook asli PayKita tidak persis
    # seperti docs, ini satu-satunya cara melihat payload aslinya.
    print(
        f"[WEBHOOK-PK] headers={dict(request.headers)} body={body[:400]!r}",
        flush=True,
    )

    timestamp = (request.headers.get("x-paykita-timestamp") or "").strip()
    signature = (request.headers.get("x-paykita-signature") or "").strip()
    if not _signature_valid(timestamp, signature, body):
        print(
            f"[WEBHOOK-PK] signature tidak valid ts={timestamp!r} sig={signature!r}",
            flush=True,
        )
        return JSONResponse({"ok": False, "message": "signature tidak valid"}, status_code=401)

    payload: dict = {}
    try:
        parsed = json.loads((body or b"").decode("utf-8", "ignore"))
        if isinstance(parsed, dict):
            payload = parsed
    except ValueError:
        pass

    event = str(payload.get("event") or "").strip().lower()
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}

    if event == "webhook.test":
        print(f"[WEBHOOK-PK] test: {data.get('message') or payload}", flush=True)
        return JSONResponse({"ok": True})

    if not event.startswith("order."):
        print(f"[WEBHOOK-PK] event {event!r} diabaikan", flush=True)
        return JSONResponse({"ok": True})

    reference = str(data.get("reference") or "").strip()
    order_id = str(data.get("order_id") or "").strip()
    if not reference:
        print(f"[WEBHOOK-PK] payload tanpa reference: {payload}", flush=True)
        return JSONResponse({"ok": False, "message": "payload tanpa reference"}, status_code=400)

    print(
        f"[WEBHOOK-PK] ok ref_id={reference} order={order_id} "
        f"status={data.get('status')} amount={data.get('amount')} "
        f"paid_at={data.get('paid_at')}",
        flush=True,
    )
    tasks.add_task(_handle, payload, data, reference, order_id)
    # Format balasan bebas (PayKita hanya cek HTTP 2xx).
    return JSONResponse({"ok": True})
