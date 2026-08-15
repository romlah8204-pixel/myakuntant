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
- **Audit Log Aktivitas**: Setiap login, buat PO, batch produksi, penjualan, beban ops (create/delete), dan ganti password tercatat ke collection `activity_logs` dengan (siapa/kapan/apa/details). Halaman "Audit Log" (admin only) dengan filter action/entity/user + pagination + tombol **Ekspor CSV** yang menghormati filter aktif
- **Backup Manual ke Emergent Object Storage**: Tombol "Backup sekarang" (admin only) menyimpan snapshot 5 collection bisnis (purchases, production, sales_transactions, inventory, operating_expenses) ke cloud storage sebagai JSON. Halaman "Backup" dengan riwayat + tombol unduh per entri. Users & activity_logs sengaja dikecualikan.
- **Foto Produk per SKU**: Halaman Persediaan menampilkan thumbnail per SKU + tombol "Ganti foto" (admin only) untuk upload PNG/JPG/WEBP (maks 3 MB) ke Emergent Object Storage. Halaman Siap Dijual juga menampilkan thumbnail. Endpoint `POST/GET/DELETE /api/inventory/{sku}/photo` dengan proxy download auth-protected. Setiap upload/delete tercatat di audit log entity=inventory.
- **Katalog Publik**: URL `/katalog` bisa diakses TANPA login — grid responsif berisi foto barang jadi + nama + variant + harga + status stok. Ada tombol "Salin link" untuk share ke calon pembeli. Backend endpoint publik: `GET /api/public/catalog` (list) dan `GET /api/public/catalog/{sku}/photo` (foto). Tidak leak field internal (value/stock/status), bahan baku disaring, hanya barang jadi dengan available>0.
- **Ringkasan Kas Per Kanal**: Panel baru di dashboard menampilkan 5 kartu (Offline/Bazar/Shopee/Tokopedia/TikTok) dengan revenue bulan berjalan, delta % vs bulan sebelumnya (naik/turun), dan sparkline mini 6 bulan berwarna sesuai kanal. **Kartu bisa diklik (admin only)** → modal berisi rincian setiap transaksi kanal itu di bulan berjalan (tanggal, invoice, deskripsi, nominal). Menggunakan endpoint `/api/sales-by-channel` + `/api/reports/detail` yang sudah ada.
- **Buku Besar & Drill-down**: Halaman "Buku Besar" (admin only) menampilkan timeline semua transaksi (PO/produksi/penjualan/beban) dengan running balance, filter tanggal + jenis + kanal penjualan (Shopee/Tokopedia/TikTok/Bazar/Offline), dan Ekspor CSV. Filter kanal hanya memfilter baris sales. Halaman Laporan: klik card Pendapatan/HPP/Beban Ops/Kas Masuk/Kas Keluar → modal berisi transaksi penyusun angka. Halaman Persediaan: tombol "Riwayat →" tiap SKU → modal berisi in (produksi) & out (penjualan) chronologis.
- Backend testing 38/38 PASS iter12 (setelah fix bug None-date sort di cash_out drill-down)

## Backlog (P2)
- Atomic decrement material_lines dengan concurrency guard
- Token versioning untuk invalidate session lama saat change-password
- Rate-limit on /api/auth/change-password
- Pagination pada /api/purchases dan /api/opex
- Aggregation pipeline untuk sales-by-channel (perf)
- Soft-delete OpEx untuk audit trail
- Notifikasi stok kritis (email/WhatsApp integration)
- Backup otomatis ke cloud storage
