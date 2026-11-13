# apiqris — API QRIS WijayaPay (project mandiri)

Membuat invoice QRIS (WijayaPay) lewat HTTP API. **Project ini berdiri
sendiri** — tidak membaca kode maupun `.env` bot. Bot/klien lain (mis.
tgxapp) memanggil API ini via HTTP, lalu menyimpan `order_id` yang
dikembalikan sebagai txid transaksinya.

Struktur pola `apisms`:

```
apiqris/
├── main.py                  # FastAPI + uvicorn + lifespan tutup session
├── config/
│   └── config.py            # konfigurasi (baca .env milik apiqris sendiri)
├── services/
│   └── wijayapay.py         # klien WijayaPay (create, signature MD5, verifikasi callback)
├── data/
│   └── redis.py             # koneksi Redis + simpan/ambil record deposit callback
├── routers/
│   ├── create_qris.py       # POST /create-qris
│   ├── check_qris.py        # POST /check-qris (baca status dari Redis)
│   └── webhook.py           # POST /webhook (callback status pembayaran)
├── requirements.txt         # fastapi, uvicorn, aiohttp, pydantic, python-dotenv, redis
├── install.sh               # deploy VPS (systemd + opsional nginx/HTTPS)
├── README.md
└── .env                     # konfigurasi (gitignored)
```

## Menjalankan

Dari folder `apiqris`:

```bash
pip install -r requirements.txt   # atau venv sendiri
python main.py                    # http://0.0.0.0:2020
```

Deploy ke VPS tanpa domain: `sudo ./install.sh` (API di `http://IP_VPS:2020`,
port 2020 dibuka otomatis di firewall). Dengan domain:
`sudo ./install.sh qris.domainkamu.com` (nginx + HTTPS). Install script
menyalin folder ke `/var/www/apiqris` lalu membuat service systemd.

## Konfigurasi (`apiqris/.env`)

| Variabel | Keterangan |
|---|---|
| `APIQRIS_API_KEY` | API key klien untuk body `api_key`. Kosong = semua request ditolak |
| `WIJAYAPAY_API_URL` | Base URL gateway WijayaPay — wajib diisi di .env (mis. `https://gateway.wijayapay.com`) |
| `WIJAYAPAY_CODE_MERCHANT` | Kode merchant dari dashboard WijayaPay |
| `WIJAYAPAY_API_KEY` | API key dari dashboard WijayaPay |
| `WIJAYAPAY_CALLBACK_URL` | URL callback yang dikirim saat create invoice — boleh IP VPS tanpa domain: `http://IP_VPS:2020/webhook`. Kosong = tidak dikirim |
| `WIJAYAPAY_CALLBACK_IP` | Whitelist IP server callback WijayaPay (docs: `172.235.245.77`), bisa beberapa IP dipisah koma. Kosong = tidak dicek |
| `REDIS_URL` | Redis untuk menyimpan callback webhook (mis. `redis://127.0.0.1:6379/0`). Kosong = callback tidak disimpan |
| `APIQRIS_HOST` / `APIQRIS_PORT` | Bind address server API (default `0.0.0.0:2020`) |

## Endpoint

### `POST /create-qris`

Body JSON:

```json
{"api_key": "APIQRIS_API_KEY", "device": "bot1", "user_id": 123456, "amount": 50000}
```

Contoh curl:

```bash
curl -X POST http://127.0.0.1:2020/create-qris \
  -H "Content-Type: application/json" \
  -d '{"api_key":"APIQRIS_API_KEY","device":"bot1","user_id":123456,"amount":50000}'
```

Respons sukses:

```json
{
  "status": "create_ok",
  "device": "bot1",
  "user_id": 123456,
  "nominal": 50000,
  "order_id": "DEPO-K3M9N2PQ-7X4Y2Z",
  "trx_reference": "WPTRX825527",
  "unique_code": 317,
  "total": 50317,
  "qris_string": "00020101021226...",
  "qr_image": "https://wijayapay.com/qris/xxx.png",
  "expired_at": "2026-11-08 14:05",
  "message": ""
}
```

Keterangan:

- `order_id` = `ref_id` WijayaPay (dibuat lokal, format
  `DEPO-XXXXXXXX-XXXXXX` acak huruf/angka uppercase). Klien (bot) menyimpan
  nilai ini sebagai txid dan memakainya saat mencocokkan pembayaran.
- `nominal + unique_code = total` — kode unik 1-99 dibuat lokal agar nilai
  yang dibayar unik per invoice. Fee gateway dipotong dari penerima
  merchant (`total_bayar` == nilai yang dikirim).
- `device` tidak divalidasi terhadap daftar store (API mandiri) — hanya
  di-echo balik, masuk log, dan `customer_name` di WijayaPay
  (`<device>-<user_id>`) agar mudah direkonsiliasi.
- Bila `WIJAYAPAY_CALLBACK_URL` diisi di `.env`, nilai ini dikirim sebagai
  `callback_url` saat create sehingga WijayaPay mengirim callback status
  pembayaran ke `POST /webhook`.
- Saat sukses, invoice otomatis disimpan ke Redis (key
  `apiqris:{ref_id}:{trx_reference}`, `status: "pending"`) — detail di
  bagian `POST /webhook`.
- `qris_string` = payload EMV QRIS (bisa dirender jadi gambar QR sendiri);
  `qr_image` = URL gambar QR versi WijayaPay bila tersedia.
- `expired_at` dalam WIB (`YYYY-MM-DD HH:MM`); masa berlaku invoice
  ditentukan server WijayaPay.
- Respons gagal: `status: "create_failed"` + `message` (api_key salah,
  device kosong, user_id/nominal tidak valid, atau error dari WijayaPay).

### `POST /check-qris`

Cek status transaksi **hanya dari Redis** — tidak memanggil WijayaPay. Record
milik PayKita (dibuat `/create-me`, field `provider: "paykita"`) yang masih
`pending` otomatis di-poll langsung ke PayKita lalu di-update ke Redis,
sehingga deposit PayKita tetap terbaca walau bot memakai mode `qris`.

Body JSON:

```json
{"api_key": "APIQRIS_API_KEY", "ref_id": "DEPO-K3M9N2PQ-7X4Y2Z", "trx_reference": "WPTRX825527"}
```

`trx_reference` opsional — bila kosong/salah, record dicari via pola key
`apiqris:{ref_id}:*`.

Respons ditemukan:

```json
{
  "status": "check_ok",
  "ref_id": "DEPO-K3M9N2PQ-7X4Y2Z",
  "trx_reference": "WPTRX825527",
  "payment_status": "paid",
  "data": { "..." : "isi penuh record (lihat bagian /webhook)" }
}
```

Respons tidak ditemukan / gagal: `status: "check_failed"` + `message`
(api_key salah, ref_id kosong, transaksi belum ada di Redis, atau
`REDIS_URL` belum diisi). `payment_status` berisi `pending` (baru
dibuat) atau `paid`/status lain dari callback WijayaPay.

### `POST /webhook`

Menerima callback status pembayaran dari WijayaPay (dikirim gateway ke
`callback_url` yang dikirim saat create). Endpoint ini diisi di dashboard
WijayaPay sebagai callback URL. Tanpa domain, isi dengan IP VPS:

```
WIJAYAPAY_CALLBACK_URL=http://123.45.67.89:2020/webhook
```

Pastikan port 2020 terbuka di firewall VPS (install.sh tanpa domain sudah
membukanya).

Contoh payload dari WijayaPay (docs):

```json
{
  "status": "paid",
  "data": {
    "ref_id": "DEPO-K3M9N2PQ-7X4Y2Z",
    "total_dibayar": 50317,
    "total_fee": 2500,
    "amount_received": 47817,
    "trx_reference": "WPTRX825527",
    "payment_methode": "QRIS",
    "updated_at": "2026-11-08 14:31:05"
  }
}
```

Dengan header `X-Signature: md5(code_merchant + api_key + ref_id)`.

Verifikasi berlapis:

1. **Whitelist IP** (opsional, bila `WIJAYAPAY_CALLBACK_IP` diisi) — IP
   pengirim dicek dari `X-Real-IP` → `X-Forwarded-For` → client host;
   di luar whitelist → `403`.
2. **Signature** — header `X-Signature` wajib cocok dengan rumus docs
   (`hmac.compare_digest`); salah/kosong → `401`.
3. Payload tanpa `ref_id` → `400`.

Jawaban sukses: `{"status": true}` (format yang diharapkan gateway).

```bash
curl -X POST http://127.0.0.1:2020/webhook \
  -H "Content-Type: application/json" \
  -H "X-Signature: <md5(code_merchant+api_key+ref_id)>" \
  -d '{"status":"paid","data":{"ref_id":"DEPO-K3M9N2PQ-7X4Y2Z","total_dibayar":50317}}'
```

Data transaksi disimpan otomatis di Redis, **satu key per transaksi**:
`apiqris:{ref_id}:{trx_reference}`, TTL 7 hari (di-reset tiap update).

- **Saat create** — record awal disimpan via BackgroundTasks (tidak
  memperlambat respons) dengan `status: "pending"`: ref_id,
  trx_reference, device, user_id, nominal, unique_code, total_bayar,
  qris_string, qr_image, expired_at, created_at (WIB).
- **Saat callback webhook** — record yang sama di-update dengan status
  pembayaran: status, total_dibayar, total_fee, amount_received,
  payment_methode, callback_updated_at, received_at (WIB), plus payload
  asli WijayaPay di `callback` untuk audit.

Contoh isi key setelah dibayar:

```json
{
  "ref_id": "DEPO-K3M9N2PQ-7X4Y2Z",
  "trx_reference": "WPTRX825527",
  "status": "paid",
  "device": "bot1",
  "user_id": 123456,
  "nominal": 50000,
  "unique_code": 317,
  "total_bayar": 50317,
  "total_dibayar": 50317,
  "total_fee": 2500,
  "amount_received": 47817,
  "payment_methode": "QRIS",
  "qris_string": "00020101021226...",
  "qr_image": "https://...",
  "expired_at": "2026-11-08 14:05",
  "created_at": "2026-11-08 13:35:00",
  "callback_updated_at": "2026-11-08 14:31:05",
  "received_at": "2026-11-08 14:31:06"
}
```

Klien (bot) tinggal membaca key tersebut
(`GET apiqris:{ref_id}:{trx_reference}`) — `status: "paid"` berarti
deposit lunas. Callback ulang untuk transaksi sama hanya meng-update key.
Callback yang datang tanpa record create (mis. Redis sempat flush)
dibuatkan record baru dari data callback. Bila `REDIS_URL` kosong di
`.env`, apa pun tidak disimpan (hanya log).

## PayKita (`/create-me` & `/check-me`)

Endpoint QRIS alternatif via **PayKita** (`https://pay.digikita.id`, API
`/api`, auth header `x-api-key: pk_live_...`). Interface body/respons
sederajat `/create-qris` — klien cukup ganti path. Kredensial di `.env`:
`PAYKITA_API_KEY` (API key project, wajib), `PAYKITA_API_URL` (default
`https://pay.digikita.id/api`), `PAYKITA_TTL_SECONDS` (umur order,
60–86400, default 900 = 15 menit).

### `POST /create-me`

Body JSON (sama dengan `/create-qris`):

```json
{"api_key": "APIQRIS_API_KEY", "device": "bot1", "user_id": 123456, "amount": 50000}
```

Respons sukses:

```json
{
  "status": "create_ok",
  "device": "bot1",
  "user_id": 123456,
  "nominal": 50000,
  "order_id": "DEPO-K3M9N2PQ-7X4Y2Z",
  "trx_reference": "pay_msiabc123def456",
  "unique_code": 787,
  "total": 50787,
  "qris_string": "00020101021226...",
  "qr_image": "",
  "checkout_url": "https://pay.digikita.id/pay/pay_msiabc123def456",
  "expired_at": "2026-11-14 21:35",
  "message": ""
}
```

Keterangan:

- `order_id` = `DEPO-XXXXXXXX-XXXXXX` buatan kita, dikirim ke PayKita
  sebagai `reference` (pembanding di webhook/payload mereka).
- `trx_reference` = ID order PayKita (`pay_...`) — dipakai `/check-me`
  untuk polling status.
- **PayKita Mode Hybrid**: fee + kode unik ditambahkan PayKita sendiri,
  jadi `total` = `pay_amount` respons PayKita (`nominal + unique_code`).
  Nilai `unique_code` di respons hanya `total - nominal`.
- `qris_string` = payload EMV QRIS (dirender bot jadi gambar QR);
  `qr_image` selalu kosong (PayKita tidak menyediakan URL gambar).
- `checkout_url` = halaman pembayaran PayKita (opsional ditampilkan).
- `expired_at` dalam WIB (`YYYY-MM-DD HH:MM`) dari `expires_at` PayKita.
- Record awal disimpan ke Redis dengan key sama
  (`apiqris:{ref_id}:{trx_reference}`, `status: "pending"`,
  `provider: "paykita"`) sehingga `/check-qris` juga menemukannya.
- Respons gagal: `status: "create_failed"` + `message` (api_key salah,
  nominal di luar batas, atau error PayKita seperti `qris_not_configured`,
  `subscription_required`).

### `POST /check-me`

Cek status order **langsung ke PayKita** (`GET /api/orders/:id`) — bukan
hanya Redis. Record Redis ikut di-update tiap polling.

Body JSON:

```json
{"api_key": "APIQRIS_API_KEY", "ref_id": "DEPO-K3M9N2PQ-7X4Y2Z", "trx_reference": "pay_msiabc123def456"}
```

`trx_reference` opsional — bila kosong, ID order PayKita diambil dari
record Redis (`apiqris:{ref_id}:*`).

Respons:

```json
{
  "status": "check_ok",
  "ref_id": "DEPO-K3M9N2PQ-7X4Y2Z",
  "trx_reference": "pay_msiabc123def456",
  "payment_status": "paid",
  "data": { "...": "isi penuh record (create + hasil polling)" }
}
```

- `payment_status` = `pending` / `paid` / `expired` / `cancelled`
  (kontrak PayKita; bot memetakan `paid` -> `success`).
- Endpoint cek status PayKita tetap berjalan walau langganan API
  merchant berakhir.
- **Sadar provider**: record milik WijayaPay (dibuat `/create-qris`,
  tanpa field `provider`) dibaca dari Redis saja — tidak ditabrak ke
  PayKita, sehingga deposit lama tetap terbaca saat bot mengganti
  `APIQRIS_MODE`. Record PayKita yang sudah final (paid/expired) tidak
  di-poll ulang.

### `POST /webhook-paykita`

Terima notifikasi pembayaran langsung dari PayKita (alternatif polling
`/check-me`). **Wajib HTTPS** — PayKita menolak URL `http://...` maupun
URL yang me-redirect. Server apiqris harus dideploy dengan domain +
HTTPS (`sudo ./install.sh qris.domainkamu.com` mengaktifkan nginx +
Let's Encrypt otomatis).

Kontrak webhook (docs PayKita):

- Header: `x-paykita-timestamp`, `x-paykita-signature` (`v1=<hex
  HMAC-SHA256>`)
- Signature: `HMAC-SHA256(PAYKITA_WEBHOOK_SECRET,
  timestamp + "." + raw_request_body)` — pakai raw body persis
  (jangan stringify ulang JSON).
- Payload `order.paid`:

```json
{
  "id": "evt_msiabc_abcd1234",
  "event": "order.paid",
  "data": {
    "order_id": "pay_msiabc123def456",
    "reference": "DEPO-K3M9N2PQ-7X4Y2Z",
    "mode": "live",
    "provider": "shopee",
    "amount": 10087,
    "status": "paid",
    "paid_at": "2026-08-07T10:03:21.000Z"
  }
}
```

- Balas `2xx` dalam 10 detik; proses berat lewat BackgroundTasks. Retry
  PayKita 9x (3 dtk s/d 3 jam) bila gagal — endpoint wajib idempotent
  (update Redis bersifat overwrite, aman untuk event ulang).
- Event `webhook.test` (tombol Kirim Test di dashboard) cukup dibalas
  `200 {"ok": true}`.

Cara mengaktifkan:

1. Deploy apiqris dengan domain HTTPS (install.sh dengan argumen domain).
2. Isi `.env`:
   ```
   PAYKITA_WEBHOOK_URL=https://qris.domainkamu.com/webhook-paykita
   PAYKITA_WEBHOOK_SECRET=<rahasia dari Pengaturan → Webhook>
   ```
   Saat `PAYKITA_WEBHOOK_URL` terisi, `/create-me` mengirimnya sebagai
   `webhook_url` per order. Alternatif: set global di dashboard PayKita
   (Pengaturan → Webhook → Webhook Default Project) dan biarkan
   `PAYKITA_WEBHOOK_URL` kosong.
3. Di dashboard PayKita (Pengaturan → Webhook) ambil **Webhook Secret**
   → isi `PAYKITA_WEBHOOK_SECRET`. Tanpa secret, semua webhook
   ditolak (401) untuk mencegah request palsu.
4. Klik **Kirim Test** sampai endpoint membalas `2xx`.

Setelah webhook aktif, record Redis (`apiqris:{ref_id}:{trx_reference}`)
di-update `status: "paid"` oleh webhook; `/check-qris` maupun `/check-me`
bisa membacanya. Bila webhook belum dipasang (server HTTP saja),
status tetap didapat via polling `/check-me`.

## Catatan

- Alur pembayaran: klien memanggil `/create-qris` → menyimpan `order_id`
  (sebagai txid) + nominal/total di sisi klien → begitu dibayar, WijayaPay
  mengirim callback ke `/webhook` dan record transaksi di Redis
  (`apiqris:{order_id}:{trx_reference}`) ter-update `status: "paid"` →
  klien membaca key itu untuk konfirmasi.
- `ref_id` bersifat idempotent di WijayaPay; invoice tidak bisa dibatalkan
  lewat API (kadaluarsa otomatis di sisi server).
- Endpoint baru berikutnya tinggal ditambahkan sebagai file di `routers/`
  lalu didaftarkan di `main.py`, pola sama dengan `apisms`.
