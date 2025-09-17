# OrderKuota / Payment Bot (QRIS statis)

## Struktur
- bot.py               : bot utama
- mutasi_client.py     : client API mutasi
- config.json.example  : contoh konfigurasi kredensial + produk
- ui.json.example      : contoh teks UI
- orders_state.json    : dibuat otomatis saat runtime
- requirements.txt

## Setup
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp config.json.example config.json
cp ui.json.example ui.json
# Edit kedua file sesuai kebutuhan

export CONFIG_PATH=config.json
export UI_PATH=ui.json
python3 bot.py
```

## Catatan
- QRIS statis + kode unik. Kecocokan berdasarkan nominal.
- Worker polling API mutasi setiap `poll_interval_sec`.
- Setelah PAID, bot kirim invite link produk.
