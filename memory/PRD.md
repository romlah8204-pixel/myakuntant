# Liniar — Manufacturing OS PRD

## Original Problem
Aplikasi untuk usaha manufaktur fashion: pembelian bahan baku, produksi, persediaan barang jadi, siap dijual, HPP, harga jual, laporan keuangan (Neraca / Laba Rugi / Arus Kas), grafik penjualan, dashboard.

## Architecture
- Backend: FastAPI + Motor (MongoDB) + JWT via httpOnly cookie
- Frontend: React (CRA/CRACO) + custom CSS (Liniar theme)
- Auth: `admin@liniar.id` / `Liniar123!`

## Modules Implemented
- Auth (login/logout/me, brute-force lock)
- Dashboard KPI + chart
- Pembelian (PO bahan baku)
- Produksi (HPP per batch)
- Persediaan (barang jadi + bahan baku)
- Siap Dijual (filter kanal + ringkasan stok)
- Penjualan multi-kanal (Offline, Bazar, Shopee, Tokopedia, TikTok) dengan auto-deduct stok
- Laporan dinamis (Neraca / Laba Rugi / Arus Kas) dengan filter kanal + ringkasan per kanal

## Data Migration
- Legacy channel "Marketplace" otomatis di-migrasi ke "Shopee" pada startup.

## Completed (Feb 2026)
- Split kanal Marketplace → Shopee / Tokopedia / TikTok
- Laporan keuangan dinamis (tidak hardcoded lagi)
- Modul Siap Dijual dengan metrics + filter kanal
- Backend testing 13/13 pass

## Backlog (P1/P2)
- P1: Filter periode (bulan/kuartal) di laporan
- P1: Role Staff (server-side enforcement) + halaman pengaturan
- P2: HPP produksi granular (link ke bahan baku PO)
- P2: Grafik penjualan per kanal (bukan hanya bar bulanan)
- P2: Export laporan (PDF/CSV)
