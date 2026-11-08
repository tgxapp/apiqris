
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel

from config.config import MAX_AMOUNT, MIN_AMOUNT, REDIS_URL, check_api_key
from data.redis import save_deposit
from services.wijayapay import create_qris

WIB = ZoneInfo("Asia/Jakarta")

router = APIRouter()


class CreateQrisRequest(BaseModel):
    api_key: str
    device: str
    user_id: int
    amount: int


class CreateQrisResponse(BaseModel):
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
    expired_at: str = ""
    message: str = ""


def _fail(
    *,
    message: str,
    device: str = "",
    user_id: int = 0,
    nominal: int = 0,
) -> CreateQrisResponse:
    return CreateQrisResponse(
        status="create_failed",
        device=device,
        user_id=user_id,
        nominal=nominal,
        message=message,
    )


async def _save_invoice(record: dict) -> None:
    """Simpan record invoice ke Redis (dipanggil via BackgroundTasks)."""
    if not REDIS_URL:
        print("[CREATE] REDIS_URL kosong - invoice tidak disimpan", flush=True)
        return
    try:
        await save_deposit(record["ref_id"], record["trx_reference"], record)
    except Exception as exc:  # Redis down dsb — jangan gagalkan create.
        print(
            f"[CREATE] simpan Redis gagal order={record.get('ref_id')}: {exc}",
            flush=True,
        )


@router.post("/create-qris", response_model=CreateQrisResponse)
async def create_qris_endpoint(
    payload: CreateQrisRequest,
    tasks: BackgroundTasks,
) -> CreateQrisResponse:
    device = payload.device.strip()
    print(
        f"[CREATE] masuk device={device} user={payload.user_id} amount={payload.amount}",
        flush=True,
    )

    if not check_api_key(payload.api_key):
        print("[CREATE] 401 api_key tidak valid", flush=True)
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

    body = await create_qris(
        amount=payload.amount,
        user_id=payload.user_id,
        device=device,
    )
    if not body or body.get("status") is False:
        msg = str((body or {}).get("message") or "create gagal")
        print(f"[CREATE] gagal device={device} user={payload.user_id}: {msg}", flush=True)
        return _fail(
            message=msg,
            device=device,
            user_id=payload.user_id,
            nominal=payload.amount,
        )

    resp = CreateQrisResponse(
        status="create_ok",
        device=device,
        user_id=payload.user_id,
        nominal=payload.amount,
        order_id=str(body.get("orderId") or ""),
        trx_reference=str(body.get("trx_reference") or ""),
        unique_code=int(body.get("unique_code") or 0),
        total=int(body.get("total_bayar") or 0),
        qris_string=str(body.get("qris_string") or ""),
        qr_image=str(body.get("qr_image") or ""),
        expired_at=str(body.get("expired_at") or ""),
    )
    # Simpan record awal invoice ke Redis (satu key per transaksi:
    # apiqris:{ref_id}:{trx_reference}); status berubah saat webhook masuk.
    tasks.add_task(
        _save_invoice,
        {
            "ref_id": resp.order_id,
            "trx_reference": resp.trx_reference,
            "status": "pending",
            "device": device,
            "user_id": payload.user_id,
            "nominal": payload.amount,
            "unique_code": resp.unique_code,
            "total_bayar": resp.total,
            "qris_string": resp.qris_string,
            "qr_image": resp.qr_image,
            "expired_at": resp.expired_at,
            # Waktu invoice dibuat (WIB).
            "created_at": datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S"),
        },
    )
    print(
        f"[CREATE] ok device={device} user={payload.user_id} order={resp.order_id} total={resp.total}",
        flush=True,
    )
    return resp
