# berita-indonesia

Kirim 3 headline berita kritis Indonesia ke Telegram tiap jam (06:00-23:00 WIB) via GitHub Actions. **100% gratis.**

## Cara setup (~5 menit)

### 1. Buat repo private di GitHub
- Buka https://github.com/new
- Nama: `berita-indonesia` (atau bebas)
- Pilih **Private**
- Klik **Create repository**

### 2. Upload file
Dari folder ini, push ke repo baru:

```bash
cd D:/marco/berita-indonesia
git init
git add .
git commit -m "initial setup"
git branch -M main
git remote add origin https://github.com/<USERNAME>/<NAMA-REPO>.git
git push -u origin main
```

Ganti `<USERNAME>` dan `<NAMA-REPO>` sesuai akun & repo Anda.

### 3. Tambahkan secrets
Di repo GitHub Anda:
1. **Settings** → **Secrets and variables** → **Actions** → **New repository secret**
2. Tambah 2 secret:
   - Name: `TELEGRAM_BOT_TOKEN`  Value: token bot Telegram dari BotFather
   - Name: `TELEGRAM_CHAT_ID`  Value: chat ID Telegram Anda

### 4. Aktifkan Actions & test
1. Tab **Actions** → klik **I understand my workflows, go ahead and enable them**
2. Pilih workflow **berita-indonesia** → **Run workflow** → **Run workflow** (test manual)
3. Cek Telegram — pesan harus masuk dalam 1-2 menit

Selesai. Setelah ini, workflow akan jalan otomatis tiap jam saat 06:00-23:00 WIB tanpa biaya apa pun.

## Customize

### Ubah jadwal
Edit `.github/workflows/schedule.yml`, ubah `cron`. Format UTC. Contoh:
- `0 */2 * * *` — tiap 2 jam
- `0 0 * * *` — sekali sehari jam 07:00 WIB

### Ubah sumber atau keyword filter
Edit `fetch_news.py`:
- `FEEDS` — daftar RSS sumber berita
- `CRITICAL_KEYWORDS` — keyword untuk filter "kritis"
- Angka 3 di `pick_top(articles, n=3)` untuk ubah jumlah headline

## Cara matikan
- Tab **Actions** → pilih **berita-indonesia** → **...** → **Disable workflow**
- Atau hapus repo

## Setelah ini jalan, matikan routine Claude
Routine Claude di https://claude.ai/code/routines/trig_01VxKJi9Tzt3xiyzowAGfawk
→ Disable supaya tidak boros kuota API.
