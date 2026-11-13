
"""POST /create-me — buat invoice QRIS via PayKita (pay.digikita.id).

Body & respons sederajat /create-qris:
    {"api_key": "...", "device": "bot1", "user_id": 123456, "amount": 50000}

``order_id`` (DEPO-...) dibuat lokal dan dikirim ke PayKita sebagai
``reference``; ``trx_reference`` = ID order PayKita (``pay_...``). Record
disimpan ke Redis dengan key yang sama (``apiqris:{ref_id}:{trx_reference}``)
sehingga /check-qris tetap menemukannya; status terkini dibaca via /check-me
(polling langsung ke PayKita, bukan hanya Redis).

Catatan: PayKita menambahkan fee + kode unik sendiri (Mode Hybrid), jadi
``total`` = ``pay_amount`` dari respons PayKita (bukan nominal + kode unik
buatan kita seperti /create-qris).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from config.config import MAX_AMOUNT, MIN_AMOUNT, REDIS_URL, check_api_key
from data.redis import save_deposit
from services.paykita import create_order, new_ref_id

WIB = ZoneInfo("Asia/Jakarta")

router = APIRouter()


class CreateMeRequest(BaseModel):
    api_key: str
    device: str
    user_id: int
    amount: int


class CreateMeResponse(BaseModel):
    status: str
    device: str = ""
    user_id: int = 0
    nominal: int = 0
    order_id: str = ""
    trx_reference: str = ""
    unique_code: int = 0
    total: int = 0
    qris_string: str = ""
    qr_image: str = ""
    checkout_url: str = ""
    expired_at: str = ""
    message: str = ""


def _fail(
    *,
    message: str,
    device: str = "",
    user_id: int = 0,
    nominal: int = 0,
) -> CreateMeResponse:
    return CreateMeResponse(
        status="create_failed",
        device=device,
        user_id=user_id,
        nominal=nominal,
        message=message,
    )


async def _save_invoice(record: dict) -> None:
    """Simpan record invoice ke Redis (dipanggil via BackgroundTasks)."""
    if not REDIS_URL:
        print("[CREATE-ME] REDIS_URL kosong - invoice tidak disimpan", flush=True)
        return
    try:
        await save_deposit(record["ref_id"], record["trx_reference"], record)
    except Exception as exc:  # Redis down dsb — jangan gagalkan create.
        print(
            f"[CREATE-ME] simpan Redis gagal order={record.get('ref_id')}: {exc}",
            flush=True,
        )


@router.post("/create-me", response_model=CreateMeResponse)
async def create_me_endpoint(
    payload: CreateMeRequest,
    tasks: BackgroundTasks,
) -> CreateMeResponse:
    device = payload.device.strip()
    print(
        f"[CREATE-ME] masuk device={device} user={payload.user_id} amount={payload.amount}",
        flush=True,
    )

    if not check_api_key(payload.api_key):
        print("[CREATE-ME] 401 api_key tidak valid", flush=True)
        return _fail(message="api_key tidak valid.", device=device)

    if not device:
        return _fail(
            message="device wajib diisi.",
            user_id=payload.user_id,
            nominal=payload.amount,
        )

    if payload.user_id <= 0:
        return _fail(
            message="user_id tidak valid.",
            device=device,
            user_id=payload.user_id,
            nominal=payload.amount,
        )

    if not (MIN_AMOUNT <= payload.amount <= MAX_AMOUNT):
        return _fail(
            message=f"nominal harus Rp {MIN_AMOUNT:,} - Rp {MAX_AMOUNT:,}".replace(",", "."),
            device=device,
            user_id=payload.user_id,
            nominal=payload.amount,
        )

    ref_id = new_ref_id()
    order = await create_order(amount=payload.amount, reference=ref_id)
    if not order or order.get("status") is False:
        msg = str((order or {}).get("message") or "create gagal")
        print(f"[CREATE-ME] gagal device={device} user={payload.user_id}: {msg}", flush=True)
        return _fail(
            message=msg,
            device=device,
            user_id=payload.user_id,
            nominal=payload.amount,
        )

    pay_amount = int(order.get("pay_amount") or 0)
    resp = CreateMeResponse(
        status="create_ok",
        device=device,
        user_id=payload.user_id,
        nominal=payload.amount,
        order_id=ref_id,
        trx_reference=str(order.get("order_id") or ""),
        unique_code=max(0, pay_amount - payload.amount),
        total=pay_amount,
        qris_string=str(order.get("qris") or ""),
        qr_image="",
        checkout_url=str(order.get("checkout_url") or ""),
        expired_at=str(order.get("expires_at") or ""),
    )
    # Simpan record awal invoice ke Redis (satu key per transaksi:
    # apiqris:{ref_id}:{trx_reference}); status berubah saat /check-me
    # melakukan polling ke PayKita.
    tasks.add_task(
        _save_invoice,
        {
            "ref_id": resp.order_id,
            "trx_reference": resp.trx_reference,
            "status": "pending",
            "provider": "paykita",
            "device": device,
            "user_id": payload.user_id,
            "nominal": payload.amount,
            "unique_code": resp.unique_code,
            "total_bayar": resp.total,
            "qris_string": resp.qris_string,
            "qr_image": resp.qr_image,
            "checkout_url": resp.checkout_url,
            "expired_at": resp.expired_at,
            # Waktu invoice dibuat (WIB).
            "created_at": datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S"),
        },
    )
    print(
        f"[CREATE-ME] ok device={device} user={payload.user_id} "
        f"order={resp.order_id} trx={resp.trx_reference} total={resp.total}",
        flush=True,
    )
    return resp
