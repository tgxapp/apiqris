"""Koneksi Redis — simpan invoice & callback webhook (pola sama dengan apisms).

Satu transaksi = satu key: ``apiqris:{ref_id}:{trx_reference}``.

- Saat create invoice : record awal disimpan dengan ``status: "pending"``.
- Saat callback webhook: record yang sama di-update dengan status pembayaran.

Klien (bot) membaca key tersebut untuk mengetahui status terkini.
TTL diatur ``DEPOSIT_TTL`` dan di-reset setiap kali key ditulis ulang.
"""

from __future__ import annotations

import json
from typing import Any

from redis.asyncio import Redis

from config.config import DEPOSIT_TTL, REDIS_URL

_redis: Redis | None = None


def get_redis() -> Redis:
    global _redis
    if _redis is None:
        _redis = Redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        try:
            await _redis.aclose()
        except AttributeError:  # redis-py < 5 memakai close()
            await _redis.close()
    _redis = None


def deposit_key(ref_id: str, trx_reference: str) -> str:
    """Key deposit: ``apiqris:{ref_id}:{trx_reference}``."""
    return f"apiqris:{str(ref_id or '').strip()}:{str(trx_reference or '').strip()}"


async def save_deposit(ref_id: str, trx_reference: str, data: dict[str, Any]) -> None:
    """Simpan record awal invoice (dipanggil saat create sukses)."""
    key = deposit_key(ref_id, trx_reference)
    await get_redis().set(key, json.dumps(data), ex=DEPOSIT_TTL)
    print(f"[REDIS] set {key} ttl={DEPOSIT_TTL}", flush=True)


async def find_deposit(ref_id: str) -> tuple[str, dict] | None:
    """Cari record invoice untuk ref_id (pola key ``apiqris:{ref_id}:*``)."""
    async for key in get_redis().scan_iter(match=f"apiqris:{ref_id}:*", count=100):
        raw = await get_redis().get(key)
        if raw:
            try:
                return key, json.loads(raw)
            except ValueError:
                continue
    return None


async def get_deposit(ref_id: str, trx_reference: str = "") -> dict | None:
    """Baca record transaksi (``None`` bila tidak ada / sudah expired).

    Bila trx_reference diisi tapi key-nya tidak ketemu, dicari via pola
    ``apiqris:{ref_id}:*`` (trx_reference bisa salah ketik / berubah).
    """
    if trx_reference:
        raw = await get_redis().get(deposit_key(ref_id, trx_reference))
        if raw:
            try:
                return json.loads(raw)
            except ValueError:
                pass
    found = await find_deposit(ref_id)
    return found[1] if found else None


async def update_deposit(ref_id: str, trx_reference: str, fields: dict[str, Any]) -> None:
    """Update record dengan fields dari callback webhook.

    Bila record create sudah ada, fields di-merge ke record itu (field create
    seperti device/nominal/qris_string tetap terjaga). Bila belum ada (mis.
    Redis sempat flush), dibuat record baru dari fields callback saja.
    """
    found = await find_deposit(ref_id)
    if found:
        key, record = found
        record.update(fields)
        if not str(record.get("trx_reference") or "").strip() and trx_reference:
            record["trx_reference"] = trx_reference
    else:
        key = deposit_key(ref_id, trx_reference or "unknown")
        record = {"ref_id": ref_id, "trx_reference": trx_reference, **fields}
    await get_redis().set(key, json.dumps(record), ex=DEPOSIT_TTL)
    print(f"[REDIS] set {key} ttl={DEPOSIT_TTL}", flush=True)
