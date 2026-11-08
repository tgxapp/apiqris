"""POST /check-qris — cek status transaksi dari Redis (TANPA call WijayaPay).

Body JSON:
    {"api_key": "...", "ref_id": "DEPO-...", "trx_reference": "WPTRX..."}

``trx_reference`` opsional — bila kosong/salah, record dicari via pola
key ``apiqris:{ref_id}:*``.

Respons:
    ditemukan      : {"status": "check_ok", "payment_status": "paid",
                      "ref_id": "...", "trx_reference": "...", "data": {...}}
    tidak ditemukan: {"status": "check_failed", "message": "transaksi tidak ditemukan"}
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from config.config import REDIS_URL, check_api_key
from data.redis import get_deposit

router = APIRouter()


class CheckQrisRequest(BaseModel):
    api_key: str
    ref_id: str
    trx_reference: str = ""


class CheckQrisResponse(BaseModel):
    status: str
    ref_id: str = ""
    trx_reference: str = ""
    payment_status: str = ""
    data: dict[str, Any] | None = None
    message: str = ""


@router.post("/check-qris", response_model=CheckQrisResponse)
async def check_qris_endpoint(payload: CheckQrisRequest) -> CheckQrisResponse:
    ref_id = payload.ref_id.strip()
    trx = payload.trx_reference.strip()
    print(f"[CHECK] masuk ref_id={ref_id} trx={trx}", flush=True)

    if not check_api_key(payload.api_key):
        print("[CHECK] 401 api_key tidak valid", flush=True)
        return CheckQrisResponse(
            status="check_failed",
            ref_id=ref_id,
            message="api_key tidak valid.",
        )

    if not ref_id:
        return CheckQrisResponse(
            status="check_failed",
            message="ref_id wajib diisi.",
        )

    if not REDIS_URL:
        return CheckQrisResponse(
            status="check_failed",
            ref_id=ref_id,
            message="REDIS_URL not configured",
        )

    record = await get_deposit(ref_id, trx)
    if not record:
        print(f"[CHECK] tidak ditemukan ref_id={ref_id}", flush=True)
        return CheckQrisResponse(
            status="check_failed",
            ref_id=ref_id,
            trx_reference=trx,
            message="transaksi tidak ditemukan.",
        )

    payment_status = str(record.get("status") or "")
    print(
        f"[CHECK] ok ref_id={ref_id} trx={record.get('trx_reference')} status={payment_status}",
        flush=True,
    )
    return CheckQrisResponse(
        status="check_ok",
        ref_id=str(record.get("ref_id") or ref_id),
        trx_reference=str(record.get("trx_reference") or trx),
        payment_status=payment_status,
        data=record,
    )
