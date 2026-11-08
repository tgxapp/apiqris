
from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent  # folder apiqris/
load_dotenv(BASE_DIR / ".env")

# --- WijayaPay (https://docs.wijayapay.com) ---
# Base URL gateway dibaca langsung dari .env (tanpa default di kode).
WIJAYAPAY_API_URL = (os.getenv("WIJAYAPAY_API_URL") or "").strip().rstrip("/")
WIJAYAPAY_CODE_MERCHANT = (os.getenv("WIJAYAPAY_CODE_MERCHANT") or "").strip()
WIJAYAPAY_API_KEY = (os.getenv("WIJAYAPAY_API_KEY") or "").strip()

# --- callback/webhook ---
# URL yang dikirim ke WijayaPay saat create; gateway akan POST status
# pembayaran ke URL ini. Kosong = callback_url tidak dikirim.
WIJAYAPAY_CALLBACK_URL = (os.getenv("WIJAYAPAY_CALLBACK_URL") or "").strip()
# Whitelist IP server callback WijayaPay (docs: 172.235.245.77).
# Kosong = tidak dicek.
WIJAYAPAY_CALLBACK_IP = (os.getenv("WIJAYAPAY_CALLBACK_IP") or "").strip()

# --- Redis (penyimpanan callback webhook) ---
# Contoh: redis://127.0.0.1:6379/0 . Kosong = callback tidak disimpan
# (hanya diverifikasi + dicatat di log).
REDIS_URL = (os.getenv("REDIS_URL") or "").strip()
# Masa hidup key deposit di Redis (detik) = 7 hari.
DEPOSIT_TTL = 7 * 24 * 60 * 60

# --- auth klien API (dikirim di body JSON sebagai "api_key") ---
# Kosong di .env = semua request ditolak.
APIQRIS_API_KEY = (os.getenv("APIQRIS_API_KEY") or "").strip()

# --- server API ---
APIQRIS_HOST = (os.getenv("APIQRIS_HOST") or "0.0.0.0").strip()
APIQRIS_PORT = int(os.getenv("APIQRIS_PORT") or "8100")

# --- batas nominal QRIS per transaksi (Rupiah) ---
MIN_AMOUNT = 10_000
MAX_AMOUNT = 1_000_000


def check_api_key(value: str) -> bool:
    given = hashlib.sha256(str(value or "").strip().encode()).digest()
    expected = hashlib.sha256(APIQRIS_API_KEY.encode()).digest()
    return bool(APIQRIS_API_KEY) and hmac.compare_digest(given, expected)
