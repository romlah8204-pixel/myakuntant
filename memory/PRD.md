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
- Filter Periode Laporan (bulanan/kuartalan) + perbandingan periode sebelumnya
- Grafik Per Kanal stacked bar 6 bulan di dashboard
- Ekspor PDF Laporan via window.print dengan print CSS optimized
- Role Staf (staff@liniar.id/Staff123!) — akses terbatas
- Beban Operasional Bertingkat per bulan
- **HPP Granular**: Produksi bisa tarik biaya bahan otomatis dari PO aktual (multi-line), decrement remaining_qty, breakdown detail per bahan
- **Ubah Password Sendiri**: Halaman Profil dengan form ganti password (bcrypt verify + minimum 8 karakter)
- **Audit Log Aktivitas**: Setiap login, buat PO, batch produksi, penjualan, beban ops (create/delete), dan ganti password tercatat ke collection `activity_logs` dengan (siapa/kapan/apa/details). Halaman "Audit Log" (admin only) dengan filter action/entity/user + pagination
- **Backup Manual ke Emergent Object Storage**: Tombol "Backup sekarang" (admin only) menyimpan snapshot 5 collection bisnis (purchases, production, sales_transactions, inventory, operating_expenses) ke cloud storage sebagai JSON. Halaman "Backup" dengan riwayat + tombol unduh per entri. Users & activity_logs sengaja dikecualikan.
- Backend testing 58/58 PASS (iter5+iter6+iter7+iter8 kumulatif)

## Backlog (P2)
- Atomic decrement material_lines dengan concurrency guard
- Token versioning untuk invalidate session lama saat change-password
- Rate-limit on /api/auth/change-password
- Pagination pada /api/purchases dan /api/opex
- Aggregation pipeline untuk sales-by-channel (perf)
- Soft-delete OpEx untuk audit trail
- Notifikasi stok kritis (email/WhatsApp integration)
- Backup otomatis ke cloud storage
