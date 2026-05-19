# berita-indonesia

Channel Telegram untuk berita Indonesia **+ berita Blitar lengkap** (kota,
kabupaten, semua kecamatan). Polling tiap 15 menit via GitHub Actions, dedupe
lintas run, dan **ringkasan + kategorisasi otomatis oleh Gemini AI**.
**100% gratis.**

## Sumber

- **Nasional**: Antara (Politik/Ekonomi/Hukum/Peristiwa), Detik, BBC Indonesia,
  Tempo, CNN Indonesia, Kompas. Difilter ke berita "kritis" via keyword.
- **Blitar**: Google News RSS untuk 27 query (Blitar, Kota Blitar, Kabupaten
  Blitar, dan semua kecamatan Wlingi, Sutojayan, Kanigoro, Srengat, Garum, dst).
  Google News mengindeks ratusan portal sehingga coverage luas.

Setiap artikel baru:
- Dilewatkan ke **Gemini 2.5 Flash** untuk dapat **kategori** (Kriminal,
  Kesehatan, Politik, dll) dan **ringkasan 2-3 kalimat**.
- Dikirim ke Telegram dengan foto (kalau ada), badge `📍 BLITAR` atau `🇮🇩 NASIONAL`,
  emoji kategori, ringkasan AI, dan link sumber.

State (artikel yang sudah dikirim) di-commit ke `state/seen.txt` supaya
tidak duplikat antar run.

## Setup (~5 menit)

### 1. Push repo
```bash
cd D:/marco/berita-indonesia
git add .
git commit -m "update: integrasi Blitar + Gemini AI"
git push
```

### 2. Tambah Secrets
Settings → Secrets and variables → Actions → New repository secret:

| Secret | Isi |
|---|---|
| `TELEGRAM_BOT_TOKEN` | token dari BotFather |
| `TELEGRAM_CHAT_ID`   | chat ID Telegram tujuan |
| `GEMINI_API_KEY`     | dari https://aistudio.google.com/apikey |

### 3. (Opsional) Tambah Variables
Settings → Secrets and variables → Actions → **Variables** tab:

| Variable | Default | Catatan |
|---|---|---|
| `GEMINI_MODEL` | `gemini-2.5-flash` | bisa diganti `gemini-2.5-pro` (lebih cerdas, lebih lambat) |
| `MAX_PER_RUN`  | `15` | batas artikel per run, hindari flood |

### 4. Aktifkan & test
1. Tab **Actions** → enable workflows
2. Pilih **berita-indonesia** → **Run workflow** untuk test manual
3. Cek Telegram dalam 1-2 menit

Setelah ini workflow jalan otomatis tiap 15 menit.

## Catatan kuota

Repo private dapat 2000 menit/bulan free Actions. Cron 15 menit ≈ 2880 run/bulan;
tiap run ~1 menit → bisa habis ~2880 menit. **Opsi**:
- Ubah repo jadi public (Actions unlimited).
- Atau ubah cron jadi `*/30 * * * *` (kuota cukup).

## Customize

### Ubah jadwal
Edit `.github/workflows/schedule.yml`, ganti `cron`. Format UTC.

### Tambah/ubah kecamatan Blitar
Edit `BLITAR_QUERIES` dan `BLITAR_NEEDLES` di `fetch_news.py`.

### Tambah sumber nasional
Edit `NATIONAL_FEEDS` di `fetch_news.py`.

### Matikan AI sementara
Hapus secret `GEMINI_API_KEY` — script tetap jalan tanpa ringkasan AI.

## Matikan total
Actions → workflow → ... → Disable workflow. Atau hapus repo.
