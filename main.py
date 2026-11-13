"""apiqris — API pembuat invoice QRIS WijayaPay (project mandiri).

Jalankan dari folder apiqris (pola sama dengan apisms):
  python main.py

Project ini TIDAK membaca kode bot. Bot/klien lain memanggil API ini
via HTTP, lalu menyimpan order_id sebagai txid transaksinya.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from config.config import APIQRIS_HOST, APIQRIS_PORT
from data.redis import close_redis
from routers.check_me import router as check_me_router
from routers.check_qris import router as check_qris_router
from routers.create_me import router as create_me_router
from routers.create_qris import router as create_qris_router
from routers.webhook import router as webhook_router
from routers.webhook_paykita import router as webhook_paykita_router
from services.paykita import close_session as close_paykita_session
from services.wijayapay import close_session


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[START] apiqris siap menerima request", flush=True)
    yield
    await close_session()
    await close_paykita_session()
    await close_redis()
    print("[STOP] koneksi WijayaPay, PayKita & Redis ditutup", flush=True)


app = FastAPI(title="apiqris", lifespan=lifespan)
app.include_router(create_qris_router)
app.include_router(check_qris_router)
app.include_router(webhook_router)
app.include_router(create_me_router)
app.include_router(check_me_router)
app.include_router(webhook_paykita_router)


@app.get("/")
async def root() -> dict:
    return {"status": True, "service": "apiqris"}


if __name__ == "__main__":
    uvicorn.run(app, host=APIQRIS_HOST, port=APIQRIS_PORT)
