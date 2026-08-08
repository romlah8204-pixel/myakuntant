# PRD — Liniar Fashion Manufacturing OS

## Problem statement
Aplikasi usaha manufaktur fashion untuk pembelian bahan baku, produksi, persediaan barang jadi, barang siap dijual, HPP, harga jual, laporan neraca/laba rugi/arus kas, grafik penjualan, dan dashboard stok.

## Architecture decisions
- React JavaScript + React Router + CSS tokens untuk workspace responsif.
- FastAPI + MongoDB melalui MONGO_URL/DB_NAME yang sudah tersedia.
- JWT httpOnly cookie, bcrypt, role admin/staf, dan endpoint terproteksi.
- HPP batch = bahan baku + tenaga kerja + overhead; format mata uang id-ID/Rp.

## Personas
- Admin pemilik/manajer: memantau performa, produksi, stok, dan laporan.
- Staf pembelian/produksi/gudang: mencatat transaksi operasional harian.

## Core requirements (static)
Dashboard KPI dan grafik; pembelian bahan; produksi batch dan HPP; persediaan; laporan bulanan Neraca, Laba Rugi, Arus Kas; login admin/staf.

## Implemented (2026-08-08)
- Login demo admin, sesi httpOnly, logout, protected routes, lockout percobaan login.
- Dashboard dengan KPI, grafik pendapatan, alert stok, antrean batch.
- Form PO bahan baku dan form batch produksi dengan preview HPP setelah tersimpan.
- Daftar persediaan dari API dan laporan interaktif tiga tab.
- Modul penjualan multi-channel dengan invoice, validasi stok, pengurangan stok atomik, dan histori transaksi.
- Responsive layout Atelier Ledger dengan test IDs dan status/error dasar.

## Prioritized backlog
- P0: tambah modul barang siap dijual yang mengurangi stok.
- P0: alur penjualan Offline, Bazar, dan Marketplace dengan COGS serta laba kotor.
- P1: form master produk/SKU, supplier, harga jual, dan konfigurasi margin.
- P1: jurnal transaksi otomatis serta rekonsiliasi laporan dengan periode yang dapat dipilih.
- P2: ekspor PDF/Excel, audit trail, dan manajemen user staf.

## Next tasks
1. Hubungkan transaksi penjualan dengan COGS dan arus kas.
2. Tambahkan filter periode, pencarian tabel, dan validasi stok.
3. Tambahkan role staf server-side dan halaman pengaturan.
