
"""POST /check-me — cek status order; sadar provider.

Body JSON:
    {"api_key": "...", "ref_id": "DEPO-...", "trx_reference": "pay_..."}

``trx_reference`` opsional — bila kosong, record dicari di Redis via pola
``apiqris:{ref_id}:*`` untuk mendapatkan ID order PayKita. Record milik
PayKita (provider "paykita") yang masih ``pending`` di-poll langsung ke
PayKita ``GET /api/orders/:id`` lalu Redis di-update; record WijayaPay
(tanpa field provider) cukup dibaca dari Redis seperti /check-qris
sehingga deposit lama tetap terbaca walau bot ganti APIQRIS_MODE.

``payment_status`` = ``pending`` / ``paid`` / ``expired`` / ``cancelled``
(kontrak PayKita; klien/bot memetakan ``paid`` -> ``success``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from pydantic import BaseModel

from config.config import REDIS_URL, check_api_key
from data.redis import get_deposit, update_deposit
from services.paykita import get_order

WIB = ZoneInfo("Asia/Jakarta")

router = APIRouter()


class CheckMeRequest(BaseModel):
    api_key: str
    ref_id: str
    trx_reference: str = ""


class CheckMeResponse(BaseModel):
    status: str
    ref_id: str = ""
    trx_reference: str = ""
    payment_status: str = ""
    data: dict[str, Any] | None = None
    message: str = ""


def _fail(
    *,
    ref_id: str = "",
    trx: str = "",
    message: str,
) -> CheckMeResponse:
    return CheckMeResponse(
        status="check_failed",
        ref_id=ref_id,
        trx_reference=trx,
        message=message,
    )


async def _sync_record(
    ref_id: str,
    trx: str,
    live: dict[str, Any],
) -> dict[str, Any] | None:
    """Update record Redis dengan hasil polling PayKita; balik record baru."""
    if not REDIS_URL:
        return None
    fields = {
        "status": str(live.get("order_status") or "").strip().lower(),
        "pay_amount": live.get("pay_amount"),
        "paid_at": str(live.get("paid_at") or ""),
        "expires_at": str(live.get("expires_at") or ""),
        "checked_at": datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        await update_deposit(ref_id, trx, fields)
    except Exception as exc:  # Redis down — jangan gagalkan respons.
        print(f"[CHECK-ME] simpan Redis gagal ref_id={ref_id}: {exc}", flush=True)
    return await get_deposit(ref_id, trx)


@router.post("/check-me", response_model=CheckMeResponse)
async def check_me_endpoint(payload: CheckMeRequest) -> CheckMeResponse:
    ref_id = payload.ref_id.strip()
    trx = payload.trx_reference.strip()
    print(f"[CHECK-ME] masuk ref_id={ref_id} trx={trx}", flush=True)

    if not check_api_key(payload.api_key):
        print("[CHECK-ME] 401 api_key tidak valid", flush=True)
        return _fail(ref_id=ref_id, message="api_key tidak valid.")

    if not ref_id:
        return _fail(message="ref_id wajib diisi.")

    # Cari record Redis dulu (untuk dapat ID order PayKita bila trx kosong).
    record = await get_deposit(ref_id, trx) if REDIS_URL else None
    paykita_id = trx
    if not paykita_id and record:
        paykita_id = str(record.get("trx_reference") or "").strip()

    # Record milik provider lain (WijayaPay, atau record lama tanpa field
    # provider): cukup balik status dari Redis — tidak menabrak PayKita.
    provider = str((record or {}).get("provider") or "").strip().lower()
    if record and provider != "paykita":
        payment_status = str(record.get("status") or "").strip().lower() or "pending"
        print(
            f"[CHECK-ME] record non-paykita ref_id={ref_id} provider={provider or '?'} "
            f"status={payment_status} (dari Redis, tidak polling PayKita)",
            flush=True,
        )
        return CheckMeResponse(
            status="check_ok",
            ref_id=ref_id,
            trx_reference=paykita_id,
            payment_status=payment_status,
            data=record,
        )

    # Record PayKita sudah final (paid/expired/cancelled): tidak perlu poll
    # ulang — langsung balik status dari Redis.
    cur_status = str((record or {}).get("status") or "").strip().lower()
    if record and cur_status not in ("", "pending"):
        print(
            f"[CHECK-ME] record paykita final ref_id={ref_id} status={cur_status} (skip poll)",
            flush=True,
        )
        return CheckMeResponse(
            status="check_ok",
            ref_id=ref_id,
            trx_reference=paykita_id,
            payment_status=cur_status,
            data=record,
        )

    if not paykita_id:
        print(f"[CHECK-ME] tidak ada order_id PayKita untuk ref_id={ref_id}", flush=True)
        return _fail(ref_id=ref_id, trx=trx, message="transaksi tidak ditemukan.")

    live = await get_order(paykita_id)
    if not live or live.get("status") is False:
        msg = str((live or {}).get("message") or "cek gagal")
        print(f"[CHECK-ME] poll gagal ref_id={ref_id} order={paykita_id}: {msg}", flush=True)
        return _fail(ref_id=ref_id, trx=paykita_id, message=msg)

    new_record = await _sync_record(ref_id, paykita_id, live)
    payment_status = str(live.get("order_status") or "").strip().lower() or "pending"
    print(
        f"[CHECK-ME] ok ref_id={ref_id} order={paykita_id} status={payment_status}",
        flush=True,
    )
    return CheckMeResponse(
        status="check_ok",
        ref_id=ref_id,
        trx_reference=paykita_id,
        payment_status=payment_status,
        data=new_record or (record or {}),
    )
