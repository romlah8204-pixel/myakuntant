from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, Depends, Query, UploadFile, File
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional
import uuid
from datetime import datetime, timezone, timedelta
import bcrypt
import jwt
import json
import asyncio
from storage import init_storage, put_object, get_object, APP_NAME


ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Create the main app without a prefix
app = FastAPI()

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")


# Define Models
class StatusCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")  # Ignore MongoDB's _id field
    
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    client_name: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class StatusCheckCreate(BaseModel):
    client_name: str

class LoginInput(BaseModel):
    email: str
    password: str

class PurchaseInput(BaseModel):
    supplier: str
    material: str
    quantity: float
    unit: str
    unit_cost: float

class ProductionInput(BaseModel):
    sku: str
    product: str
    output_qty: int
    material_cost: float
    labor_cost: float
    overhead_cost: float
    material_lines: List[dict] = []  # [{"purchase_id": str, "qty_used": float}]

class SaleInput(BaseModel):
    channel: str
    sku: str
    quantity: int
    unit_price: float
    customer: str = "Pelanggan umum"
    order_ref: str = ""

class OpExInput(BaseModel):
    period: str
    category: str
    amount: float
    note: str = ""

class PasswordChangeInput(BaseModel):
    current_password: str
    new_password: str

class CashMovementInput(BaseModel):
    date: str  # YYYY-MM-DD
    account: str  # kas | bank
    direction: str  # in | out
    category: str
    amount: float
    note: str = ""

class AssetInput(BaseModel):
    name: str
    category: str
    purchase_date: str  # YYYY-MM-DD
    purchase_cost: float
    useful_life_months: int
    salvage_value: float = 0

def public_user(user):
    return {"id": user["id"], "email": user["email"], "name": user["name"], "role": user["role"]}

def money_str(n):
    try:
        return f"Rp {int(round(float(n))):,}".replace(",", ".")
    except Exception:
        return "Rp 0"

def token_for(user):
    return jwt.encode({"sub": user["id"], "exp": datetime.now(timezone.utc) + timedelta(hours=8)}, os.environ["JWT_SECRET"], algorithm="HS256")

async def current_user(request: Request):
    token = request.cookies.get("access_token") or request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        raise HTTPException(401, "Silakan login terlebih dahulu")
    try:
        payload = jwt.decode(token, os.environ["JWT_SECRET"], algorithms=["HS256"])
        user = await db.users.find_one({"id": payload["sub"]}, {"_id": 0})
        if not user: raise HTTPException(401, "Sesi tidak valid")
        return user
    except (jwt.InvalidTokenError, KeyError):
        raise HTTPException(401, "Sesi telah berakhir")

async def admin_only(user=Depends(current_user)):
    if user.get("role") != "admin":
        raise HTTPException(403, "Akses hanya untuk administrator")
    return user

async def log_activity(user, action: str, entity: str, entity_id: str, summary: str, details: dict = None):
    """Write an audit trail entry. user may be None for anonymous events (e.g., failed login)."""
    doc = {
        "id": str(uuid.uuid4()),
        "user_id": (user or {}).get("id"),
        "user_email": (user or {}).get("email", "anonymous"),
        "user_role": (user or {}).get("role", "anonymous"),
        "action": action,
        "entity": entity,
        "entity_id": entity_id,
        "summary": summary,
        "details": details or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.activity_logs.insert_one(doc)

async def seed_data():
    await db.users.create_index("email", unique=True)
    # One-time migration: split legacy "Marketplace" channel into Shopee (default)
    await db.sales_transactions.update_many({"channel": "Marketplace"}, {"$set": {"channel": "Shopee"}})
    admin_email = os.environ["ADMIN_EMAIL"].lower()
    existing = await db.users.find_one({"email": admin_email})
    if not existing:
        await db.users.insert_one({"id": str(uuid.uuid4()), "email": admin_email, "password_hash": bcrypt.hashpw(os.environ["ADMIN_PASSWORD"].encode(), bcrypt.gensalt()).decode(), "name": "Pemilik Liniar", "role": "admin"})
    elif not bcrypt.checkpw(os.environ["ADMIN_PASSWORD"].encode(), existing["password_hash"].encode()):
        await db.users.update_one({"email": admin_email}, {"$set": {"password_hash": bcrypt.hashpw(os.environ["ADMIN_PASSWORD"].encode(), bcrypt.gensalt()).decode()}})
    # Seed demo staff account (only if not exists — never touches password if already exists)
    staff_email = "staff@liniar.id"
    if not await db.users.find_one({"email": staff_email}):
        await db.users.insert_one({"id": str(uuid.uuid4()), "email": staff_email, "password_hash": bcrypt.hashpw(b"Staff123!", bcrypt.gensalt()).decode(), "name": "Staf Produksi", "role": "staff"})
    if await db.inventory.count_documents({}) == 0:
        await db.inventory.insert_many([
            {"id":"inv-1","sku":"LIN-OVR-001","name":"Overshirt Linen Terra","variant":"M / Terra","type":"Barang Jadi","stock":42,"available":36,"unit":"pcs","value":7560000,"status":"Sehat"},
            {"id":"inv-2","sku":"FAB-COT-042","name":"Kain Cotton Combed 24s","variant":"Hitam","type":"Bahan Baku","stock":128,"available":128,"unit":"meter","value":8320000,"status":"Sehat"},
            {"id":"inv-3","sku":"LIN-PNT-008","name":"Pants Relaxed Twill","variant":"L / Navy","type":"Barang Jadi","stock":8,"available":5,"unit":"pcs","value":1840000,"status":"Menipis"},
        ])
    if await db.sales.count_documents({}) == 0:
        await db.sales.insert_many([{"month":"Jan","value":18500000},{"month":"Feb","value":22800000},{"month":"Mar","value":21400000},{"month":"Apr","value":28700000},{"month":"Mei","value":26400000},{"month":"Jun","value":34200000}])

@app.on_event("startup")
async def startup():
    await seed_data()

@api_router.post("/auth/login")
async def login(input: LoginInput, response: Response):
    identifier = input.email.lower()
    attempt = await db.login_attempts.find_one({"identifier": identifier}, {"_id": 0})
    if attempt and attempt.get("locked_until", "") > datetime.now(timezone.utc).isoformat():
        raise HTTPException(429, "Terlalu banyak percobaan. Coba lagi dalam 15 menit")
    user = await db.users.find_one({"email": identifier}, {"_id": 0})
    if not user or not bcrypt.checkpw(input.password.encode(), user["password_hash"].encode()):
        failed = (attempt or {}).get("count", 0) + 1
        await db.login_attempts.update_one({"identifier": identifier}, {"$set": {"count": failed, "locked_until": (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat() if failed >= 5 else ""}}, upsert=True)
        raise HTTPException(401, "Email atau password tidak sesuai")
    await db.login_attempts.delete_one({"identifier": identifier})
    response.set_cookie("access_token", token_for(user), httponly=True, secure=True, samesite="none", max_age=28800, path="/")
    await log_activity(user, "login", "auth", user["id"], f"Login berhasil: {user['email']}")
    return public_user(user)

@api_router.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    return {"message":"Berhasil keluar"}

@api_router.get("/auth/me")
async def me(user=Depends(current_user)): return public_user(user)

@api_router.get("/dashboard")
async def dashboard(user=Depends(current_user)):
    inventory = await db.inventory.find({}, {"_id":0}).to_list(100)
    sales = await db.sales.find({}, {"_id":0}).to_list(100)
    return {"metrics":{"revenue":34200000,"revenue_change":"+18,4%","inventory_value":17720000,"production_units":284,"gross_margin":"32,8%"},"sales":sales,"inventory":inventory,"queue":[{"batch":"BTH-2406-018","product":"Overshirt Linen Terra","qty":60,"status":"Berjalan"},{"batch":"BTH-2406-017","product":"Pants Relaxed Twill","qty":40,"status":"QC"},{"batch":"BTH-2406-016","product":"Boxy Tee Cotton","qty":100,"status":"Selesai"}]}

@api_router.get("/inventory")
async def inventory(user=Depends(current_user)):
    return await db.inventory.find({}, {"_id":0}).to_list(100)

@api_router.get("/ready-to-sell")
async def ready_to_sell(user=Depends(current_user)):
    items = await db.inventory.find({"type": "Barang Jadi", "available": {"$gt": 0}}, {"_id": 0}).to_list(100)
    return [{**item, "ready_qty": item.get("available", 0), "sell_status": "Siap dijual" if item.get("available", 0) > 5 else "Stok terbatas"} for item in items]

# ---------- Ledger, drill-down, and SKU history helpers ----------

def _iso_between(iso_str, start, end):
    if not iso_str:
        return False
    if start and iso_str < start:
        return False
    if end and iso_str >= end:
        return False
    return True

@api_router.get("/inventory/{sku}/history")
async def inventory_history(sku: str, user=Depends(current_user)):
    item = await db.inventory.find_one({"sku": sku}, {"_id": 0})
    if not item:
        raise HTTPException(404, "SKU tidak ditemukan")
    events = []
    prods = await db.production.find({"sku": sku}, {"_id": 0}).sort("created_at", 1).to_list(1000)
    for p in prods:
        events.append({"date": p.get("created_at", ""), "type": "produksi", "ref": p.get("batch"), "in": p.get("output_qty", 0), "out": 0, "note": f"Batch produksi · HPP {p.get('hpp', 0)}/unit"})
    sales = await db.sales_transactions.find({"sku": sku}, {"_id": 0}).sort("created_at", 1).to_list(1000)
    for s in sales:
        events.append({"date": s.get("created_at", ""), "type": "penjualan", "ref": s.get("invoice"), "in": 0, "out": s.get("quantity", 0), "note": f"{s.get('channel')} · {s.get('customer')} · {money_str(s.get('revenue', 0))}"})
    events.sort(key=lambda e: e["date"])
    balance = 0
    for e in events:
        balance += e["in"] - e["out"]
        e["balance"] = balance
    return {"sku": sku, "name": item.get("name"), "variant": item.get("variant"), "unit": item.get("unit"), "current_available": item.get("available", 0), "events": events}

@api_router.get("/ledger")
async def ledger(start: str = Query(""), end: str = Query(""), kind: str = Query(""), channel: str = Query("Semua"), user=Depends(admin_only)):
    """Combined chronological ledger of all business transactions.
    start/end: ISO date strings (YYYY-MM-DD). kind: '' | 'purchase' | 'production' | 'sale' | 'opex'.
    channel: 'Semua' | 'Offline' | 'Bazar' | 'Shopee' | 'Tokopedia' | 'TikTok' — only affects sales rows.
    """
    valid_channels = {"Semua", "Offline", "Bazar", "Shopee", "Tokopedia", "TikTok"}
    if channel not in valid_channels:
        raise HTTPException(422, "Kanal tidak valid")
    start_iso = f"{start}T00:00:00+00:00" if start else ""
    end_iso = f"{end}T23:59:59+00:00" if end else ""
    entries = []
    if kind in ("", "purchase"):
        for p in await db.purchases.find({}, {"_id": 0}).to_list(5000):
            if _iso_between(p.get("created_at", ""), start_iso, end_iso):
                entries.append({"date": p.get("created_at", ""), "type": "purchase", "ref": p.get("po"), "description": f"PO bahan · {p.get('supplier')} · {p.get('material')} {p.get('quantity')} {p.get('unit')}", "in": 0, "out": p.get("total", 0)})
    if kind in ("", "production"):
        for pr in await db.production.find({}, {"_id": 0}).to_list(5000):
            if _iso_between(pr.get("created_at", ""), start_iso, end_iso):
                entries.append({"date": pr.get("created_at", ""), "type": "production", "ref": pr.get("batch"), "description": f"Produksi · {pr.get('product')} {pr.get('output_qty')} unit · HPP {money_str(pr.get('hpp', 0))}/unit", "in": 0, "out": pr.get("total_cost", 0)})
    if kind in ("", "sale"):
        sale_query = {} if channel == "Semua" else {"channel": channel}
        for s in await db.sales_transactions.find(sale_query, {"_id": 0}).to_list(5000):
            if _iso_between(s.get("created_at", ""), start_iso, end_iso):
                entries.append({"date": s.get("created_at", ""), "type": "sale", "ref": s.get("invoice"), "description": f"Penjualan {s.get('channel')} · {s.get('sku')} {s.get('quantity')} pcs · {s.get('customer')}", "in": s.get("revenue", 0), "out": 0})
    if kind in ("", "opex"):
        for o in await db.operating_expenses.find({}, {"_id": 0}).to_list(5000):
            iso = o.get("created_at") or f"{o.get('period', '2020-01')}-01T00:00:00+00:00"
            if _iso_between(iso, start_iso, end_iso):
                entries.append({"date": iso, "type": "opex", "ref": o.get("period"), "description": f"Beban {o.get('category')} · {o.get('period')}{' · ' + o.get('note') if o.get('note') else ''}", "in": 0, "out": o.get("amount", 0)})
    entries.sort(key=lambda x: x["date"])
    balance = 0
    for e in entries:
        balance += e["in"] - e["out"]
        e["balance"] = balance
    total_in = sum(e["in"] for e in entries)
    total_out = sum(e["out"] for e in entries)
    return {"entries": entries, "count": len(entries), "total_in": total_in, "total_out": total_out, "net": total_in - total_out, "start": start, "end": end, "kind": kind, "channel": channel}

@api_router.get("/ledger/export.csv")
async def ledger_csv(start: str = Query(""), end: str = Query(""), kind: str = Query(""), channel: str = Query("Semua"), user=Depends(admin_only)):
    import csv, io
    data = await ledger(start=start, end=end, kind=kind, channel=channel, user=user)  # reuse
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    w.writerow(["date", "type", "ref", "description", "kas_masuk", "kas_keluar", "saldo"])
    for e in data["entries"]:
        w.writerow([e["date"], e["type"], e["ref"], e["description"], e["in"], e["out"], e["balance"]])
    w.writerow([])
    w.writerow(["", "", "", "TOTAL", data["total_in"], data["total_out"], data["net"]])
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return Response(content=buf.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="liniar-bukubesar-{ts}.csv"'})

@api_router.get("/reports/detail")
async def reports_detail(kind: str = Query(...), channel: str = Query("Semua"), granularity: str = Query("all"), period: str = Query(""), user=Depends(admin_only)):
    """Return underlying rows that make up a report card.
    kind: 'revenue' | 'cogs' | 'purchases' | 'production' | 'opex' | 'cash_out'
    """
    valid_kinds = {"revenue", "cogs", "purchases", "production", "opex", "cash_out"}
    if kind not in valid_kinds:
        raise HTTPException(422, "kind tidak valid")
    start, end, label, _, _, _ = _period_range(granularity, period) if granularity != "all" else (None, None, "Semua Periode", None, None, None)
    def in_range(iso):
        if not start:
            return True
        return start <= (iso or "") < end
    channel_q = {} if channel == "Semua" else {"channel": channel}
    rows = []
    if kind in ("revenue", "cogs"):
        sales = await db.sales_transactions.find(channel_q, {"_id": 0}).sort("created_at", -1).to_list(2000)
        for s in sales:
            if not in_range(s.get("created_at", "")):
                continue
            rows.append({"date": s.get("created_at") or "", "ref": s.get("invoice"), "description": f"{s.get('channel')} · {s.get('sku')} {s.get('quantity')} pcs · {s.get('customer')}", "amount": s.get("revenue", 0) if kind == "revenue" else s.get("cogs", 0)})
    elif kind == "purchases":
        for p in await db.purchases.find({}, {"_id": 0}).sort("created_at", -1).to_list(2000):
            if in_range(p.get("created_at", "")):
                rows.append({"date": p.get("created_at") or "", "ref": p.get("po"), "description": f"{p.get('supplier')} · {p.get('material')} {p.get('quantity')} {p.get('unit')}", "amount": p.get("total", 0)})
    elif kind == "production":
        for pr in await db.production.find({}, {"_id": 0}).sort("created_at", -1).to_list(2000):
            if in_range(pr.get("created_at", "")):
                rows.append({"date": pr.get("created_at") or "", "ref": pr.get("batch"), "description": f"{pr.get('product')} · {pr.get('output_qty')} unit", "amount": pr.get("total_cost", 0)})
    elif kind == "opex":
        opex_rows = await db.operating_expenses.find({}, {"_id": 0}).to_list(2000)
        for o in opex_rows:
            if channel != "Semua":
                continue
            if start and not _period_in_range(o.get("period", ""), start, end):
                continue
            rows.append({"date": o.get("created_at") or f"{o.get('period', '')}-01T00:00:00+00:00", "ref": o.get("period"), "description": f"{o.get('category')} · {o.get('period')}", "amount": o.get("amount", 0)})
    elif kind == "cash_out":
        for p in await db.purchases.find({}, {"_id": 0}).to_list(2000):
            if in_range(p.get("created_at", "")):
                rows.append({"date": p.get("created_at") or "", "ref": p.get("po"), "description": f"PO · {p.get('supplier')} · {p.get('material')}", "amount": p.get("total", 0)})
        for pr in await db.production.find({}, {"_id": 0}).to_list(2000):
            if in_range(pr.get("created_at", "")):
                rows.append({"date": pr.get("created_at") or "", "ref": pr.get("batch"), "description": f"Produksi · {pr.get('product')}", "amount": pr.get("total_cost", 0)})
        if channel == "Semua":
            for o in await db.operating_expenses.find({}, {"_id": 0}).to_list(2000):
                if start and not _period_in_range(o.get("period", ""), start, end):
                    continue
                rows.append({"date": o.get("created_at") or f"{o.get('period', '')}-01T00:00:00+00:00", "ref": o.get("period"), "description": f"Beban {o.get('category')}", "amount": o.get("amount", 0)})
        rows.sort(key=lambda r: r.get("date") or "", reverse=True)
    total = sum(r.get("amount", 0) for r in rows)
    return {"kind": kind, "period": label, "channel": channel, "rows": rows, "count": len(rows), "total": total}


BALANCE_KINDS = {
    "kas": "Kas",
    "bank": "Bank",
    "persediaan_bahan": "Persediaan Bahan Baku",
    "persediaan_barang_jadi": "Persediaan Barang Jadi",
    "aset_tetap": "Aset Tetap",
    "utang_pinjaman": "Utang Pinjaman (Jangka Panjang)",
    "modal_disetor": "Modal Disetor",
    "laba_ditahan": "Laba Ditahan",
}

@api_router.get("/balance-detail")
async def balance_detail(kind: str = Query(...), user=Depends(admin_only)):
    """Return the movements that build a Balance Sheet line so users can drill down.
    Each row: {date, ref, description, direction: 'in'|'out', amount}.
    """
    if kind not in BALANCE_KINDS:
        raise HTTPException(422, f"kind tidak valid. Pilih: {', '.join(sorted(BALANCE_KINDS.keys()))}")
    rows = []
    label = BALANCE_KINDS[kind]

    if kind == "kas":
        # Kas = operasional (semua penjualan cash-in − PO/produksi/opex cash-out) + cash_movements akun kas
        for s in await db.sales_transactions.find({}, {"_id": 0}).to_list(5000):
            rows.append({"date": s.get("created_at") or "", "ref": s.get("invoice"), "description": f"Penjualan {s.get('channel')} · {s.get('sku')} {s.get('quantity')} pcs · {s.get('customer')}", "direction": "in", "amount": s.get("revenue", 0)})
        for p in await db.purchases.find({}, {"_id": 0}).to_list(5000):
            rows.append({"date": p.get("created_at") or "", "ref": p.get("po"), "description": f"PO bahan · {p.get('supplier')} · {p.get('material')}", "direction": "out", "amount": p.get("total", 0)})
        for pr in await db.production.find({}, {"_id": 0}).to_list(5000):
            rows.append({"date": pr.get("created_at") or "", "ref": pr.get("batch"), "description": f"Produksi · {pr.get('product')} · {pr.get('output_qty')} unit", "direction": "out", "amount": pr.get("total_cost", 0)})
        for o in await db.operating_expenses.find({}, {"_id": 0}).to_list(2000):
            iso = o.get("created_at") or f"{o.get('period', '2020-01')}-01T00:00:00+00:00"
            rows.append({"date": iso, "ref": o.get("period"), "description": f"Beban {o.get('category')} · {o.get('period')}", "direction": "out", "amount": o.get("amount", 0)})
        for m in await db.cash_movements.find({"account": "kas"}, {"_id": 0}).to_list(2000):
            iso = m.get("created_at") or f"{m.get('date','2020-01-01')}T00:00:00+00:00"
            rows.append({"date": iso, "ref": m.get("id", "")[:8].upper(), "description": f"Kas · {m.get('category')}{' · ' + m.get('note') if m.get('note') else ''}", "direction": m.get("direction", "in"), "amount": m.get("amount", 0)})

    elif kind == "bank":
        for m in await db.cash_movements.find({"account": "bank"}, {"_id": 0}).to_list(2000):
            iso = m.get("created_at") or f"{m.get('date','2020-01-01')}T00:00:00+00:00"
            rows.append({"date": iso, "ref": m.get("id", "")[:8].upper(), "description": f"Bank · {m.get('category')}{' · ' + m.get('note') if m.get('note') else ''}", "direction": m.get("direction", "in"), "amount": m.get("amount", 0)})

    elif kind == "utang_pinjaman":
        for m in await db.cash_movements.find({"category": {"$in": ["pinjaman_diterima", "bayar_cicilan_pinjaman"]}}, {"_id": 0}).to_list(2000):
            iso = m.get("created_at") or f"{m.get('date','2020-01-01')}T00:00:00+00:00"
            # pinjaman_diterima menambah utang (in), bayar_cicilan mengurangi utang (out)
            direction = "in" if m.get("category") == "pinjaman_diterima" else "out"
            rows.append({"date": iso, "ref": m.get("id", "")[:8].upper(), "description": f"{m.get('category')} · {m.get('account')}{' · ' + m.get('note') if m.get('note') else ''}", "direction": direction, "amount": m.get("amount", 0)})

    elif kind == "modal_disetor":
        for m in await db.cash_movements.find({"category": {"$in": ["modal_masuk", "tarik_pribadi"]}}, {"_id": 0}).to_list(2000):
            iso = m.get("created_at") or f"{m.get('date','2020-01-01')}T00:00:00+00:00"
            direction = "in" if m.get("category") == "modal_masuk" else "out"
            rows.append({"date": iso, "ref": m.get("id", "")[:8].upper(), "description": f"{m.get('category')} · {m.get('account')}{' · ' + m.get('note') if m.get('note') else ''}", "direction": direction, "amount": m.get("amount", 0)})

    elif kind == "laba_ditahan":
        # Laba ditahan = akumulasi laba bersih historis (revenue - cogs - opex - depresiasi seumur hidup)
        for s in await db.sales_transactions.find({}, {"_id": 0}).to_list(5000):
            rows.append({"date": s.get("created_at") or "", "ref": s.get("invoice"), "description": f"Laba kotor · {s.get('channel')} · {s.get('sku')}", "direction": "in", "amount": s.get("gross_profit", 0)})
        for o in await db.operating_expenses.find({}, {"_id": 0}).to_list(2000):
            iso = o.get("created_at") or f"{o.get('period', '2020-01')}-01T00:00:00+00:00"
            rows.append({"date": iso, "ref": o.get("period"), "description": f"Beban ops · {o.get('category')} · {o.get('period')}", "direction": "out", "amount": o.get("amount", 0)})
        all_assets_ld = await db.fixed_assets.find({}, {"_id": 0}).to_list(500)
        for a in all_assets_ld:
            d = _asset_derived(a)
            if d["accumulated_dep"] > 0:
                iso = a.get("created_at") or f"{a.get('purchase_date','2020-01-01')}T00:00:00+00:00"
                rows.append({"date": iso, "ref": (a.get("id", "")[:8]).upper(), "description": f"Akumulasi penyusutan · {a.get('name')} ({d['elapsed_months']} bln)", "direction": "out", "amount": d["accumulated_dep"]})

    elif kind == "persediaan_bahan":
        for p in await db.purchases.find({}, {"_id": 0}).to_list(5000):
            rows.append({"date": p.get("created_at") or "", "ref": p.get("po"), "description": f"Terima bahan · {p.get('supplier')} · {p.get('material')} {p.get('quantity')} {p.get('unit')}", "direction": "in", "amount": p.get("total", 0)})
        for pr in await db.production.find({}, {"_id": 0}).to_list(5000):
            mb = pr.get("material_breakdown") or []
            for line in mb:
                rows.append({"date": pr.get("created_at") or "", "ref": pr.get("batch"), "description": f"Pakai bahan · {line.get('material')} {line.get('qty_used')} {line.get('unit')} untuk {pr.get('product')}", "direction": "out", "amount": line.get("line_cost", 0)})

    elif kind == "persediaan_barang_jadi":
        for pr in await db.production.find({}, {"_id": 0}).to_list(5000):
            rows.append({"date": pr.get("created_at") or "", "ref": pr.get("batch"), "description": f"Selesai produksi · {pr.get('product')} · {pr.get('output_qty')} unit", "direction": "in", "amount": pr.get("total_cost", 0)})
        for s in await db.sales_transactions.find({}, {"_id": 0}).to_list(5000):
            rows.append({"date": s.get("created_at") or "", "ref": s.get("invoice"), "description": f"Keluar (HPP) · {s.get('sku')} {s.get('quantity')} pcs · {s.get('channel')}", "direction": "out", "amount": s.get("cogs", 0)})

    elif kind == "aset_tetap":
        assets = await db.fixed_assets.find({}, {"_id": 0}).to_list(500)
        for a in assets:
            iso = a.get("created_at") or f"{a.get('purchase_date','2020-01-01')}T00:00:00+00:00"
            rows.append({"date": iso, "ref": (a.get("id", "")[:8]).upper(), "description": f"Perolehan · {a.get('name')} ({a.get('category')}) · masa manfaat {a.get('useful_life_months')} bln", "direction": "in", "amount": a.get("purchase_cost", 0)})
            d = _asset_derived(a)
            if d["accumulated_dep"] > 0:
                rows.append({"date": iso, "ref": (a.get("id", "")[:8]).upper(), "description": f"Akumulasi penyusutan · {a.get('name')} · {d['elapsed_months']}/{a.get('useful_life_months')} bln", "direction": "out", "amount": d["accumulated_dep"]})

    rows.sort(key=lambda r: r.get("date") or "", reverse=True)
    total_in = sum(r["amount"] for r in rows if r["direction"] == "in")
    total_out = sum(r["amount"] for r in rows if r["direction"] == "out")
    saldo = total_in - total_out
    return {"kind": kind, "label": label, "rows": rows, "count": len(rows), "total_in": total_in, "total_out": total_out, "saldo": saldo}


ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
EXT_FROM_MIME = {"image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png", "image/webp": "webp"}

@api_router.post("/inventory/{sku}/photo")
async def upload_product_photo(sku: str, file: UploadFile = File(...), user=Depends(admin_only)):
    item = await db.inventory.find_one({"sku": sku}, {"_id": 0})
    if not item:
        raise HTTPException(404, "SKU tidak ditemukan")
    ct = (file.content_type or "").lower()
    if ct not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(422, "Format tidak didukung. Pakai JPG, PNG, atau WEBP")
    data = await file.read()
    if len(data) > 3 * 1024 * 1024:
        raise HTTPException(413, "Ukuran gambar maksimal 3 MB")
    ext = EXT_FROM_MIME[ct]
    path = f"{APP_NAME}/product-photos/{sku}/{uuid.uuid4()}.{ext}"
    try:
        result = await asyncio.to_thread(put_object, path, data, ct)
    except Exception as e:
        raise HTTPException(502, f"Gagal upload foto: {e}")
    stored_path = result.get("path", path)
    await db.inventory.update_one({"sku": sku}, {"$set": {"photo_path": stored_path, "photo_content_type": ct, "photo_size": result.get("size", len(data)), "photo_updated_at": datetime.now(timezone.utc).isoformat()}})
    await log_activity(user, "update", "inventory", sku, f"Foto {sku} diunggah · {round(len(data)/1024, 1)} KB", {"content_type": ct, "size": len(data)})
    return {"sku": sku, "photo_path": stored_path, "size": len(data), "content_type": ct}

@api_router.get("/inventory/{sku}/photo")
async def get_product_photo(sku: str, user=Depends(current_user)):
    item = await db.inventory.find_one({"sku": sku}, {"_id": 0})
    if not item or not item.get("photo_path"):
        raise HTTPException(404, "Foto belum tersedia")
    try:
        data, content_type = await asyncio.to_thread(get_object, item["photo_path"])
    except Exception as e:
        raise HTTPException(502, f"Gagal ambil foto: {e}")
    return Response(content=data, media_type=item.get("photo_content_type") or content_type, headers={"Cache-Control": "private, max-age=300"})

@api_router.delete("/inventory/{sku}/photo")
async def delete_product_photo(sku: str, user=Depends(admin_only)):
    item = await db.inventory.find_one({"sku": sku}, {"_id": 0})
    if not item or not item.get("photo_path"):
        raise HTTPException(404, "Foto tidak ditemukan")
    await db.inventory.update_one({"sku": sku}, {"$unset": {"photo_path": "", "photo_content_type": "", "photo_size": "", "photo_updated_at": ""}})
    await log_activity(user, "delete", "inventory", sku, f"Foto {sku} dilepas")
    return {"sku": sku, "deleted": True}



@api_router.get("/sales")
async def sales(user=Depends(current_user)):
    return await db.sales_transactions.find({}, {"_id":0}).sort("created_at", -1).to_list(100)

@api_router.post("/sales")
async def create_sale(input: SaleInput, user=Depends(current_user)):
    if input.channel not in {"Offline", "Bazar", "Shopee", "Tokopedia", "TikTok"}:
        raise HTTPException(422, "Kanal penjualan tidak valid")
    if input.quantity < 1 or input.unit_price < 0:
        raise HTTPException(422, "Jumlah dan harga harus valid")
    item = await db.inventory.find_one({"sku": input.sku}, {"_id": 0})
    if not item:
        raise HTTPException(404, "SKU tidak ditemukan di persediaan")
    if item.get("available", 0) < input.quantity:
        raise HTTPException(409, f"Stok tersedia hanya {item.get('available', 0)} {item.get('unit', 'pcs')}")
    unit_cost = item.get("value", 0) / max(item.get("stock", 1), 1)
    revenue = input.quantity * input.unit_price
    cogs = input.quantity * unit_cost
    doc = {"id": str(uuid.uuid4()), "invoice": f"INV-{datetime.now().strftime('%y%m%d')}-{str(uuid.uuid4())[:4].upper()}", **input.model_dump(), "revenue": revenue, "cogs": round(cogs, 2), "gross_profit": round(revenue - cogs, 2), "created_at": datetime.now(timezone.utc).isoformat(), "status": "Lunas"}
    updated = await db.inventory.update_one({"sku": input.sku, "available": {"$gte": input.quantity}}, {"$inc": {"stock": -input.quantity, "available": -input.quantity}})
    if updated.modified_count != 1:
        raise HTTPException(409, "Stok berubah. Muat ulang persediaan lalu coba lagi")
    await db.sales_transactions.insert_one(doc)
    await log_activity(user, "create", "sale", doc["id"], f"Penjualan {doc['invoice']} · {input.channel} · {input.quantity} {item.get('unit','pcs')} {input.sku} · {money_str(revenue)}", {"channel": input.channel, "sku": input.sku, "quantity": input.quantity, "revenue": revenue})
    doc.pop("_id", None)
    return doc

@api_router.post("/auth/change-password")
async def change_password(input: PasswordChangeInput, user=Depends(current_user)):
    if len(input.new_password) < 8:
        raise HTTPException(422, "Password baru minimal 8 karakter")
    if not bcrypt.checkpw(input.current_password.encode(), user["password_hash"].encode()):
        raise HTTPException(401, "Password lama tidak sesuai")
    new_hash = bcrypt.hashpw(input.new_password.encode(), bcrypt.gensalt()).decode()
    await db.users.update_one({"id": user["id"]}, {"$set": {"password_hash": new_hash}})
    await log_activity(user, "change_password", "user", user["id"], f"Password diperbarui untuk {user['email']}")
    return {"message": "Password berhasil diperbarui"}

@api_router.get("/purchases")
async def list_purchases(user=Depends(current_user)):
    return await db.purchases.find({}, {"_id": 0}).sort("created_at", -1).to_list(500)

@api_router.post("/purchases")
async def create_purchase(input: PurchaseInput, user=Depends(current_user)):
    total = input.quantity * input.unit_cost
    doc = {"id":str(uuid.uuid4()), "po":f"PO-{datetime.now().strftime('%y%m')}-{str(uuid.uuid4())[:4].upper()}", "supplier":input.supplier, "material":input.material, "quantity":input.quantity, "remaining_qty": input.quantity, "unit":input.unit, "unit_cost": input.unit_cost, "total":total, "status":"Diterima", "created_at": datetime.now(timezone.utc).isoformat()}
    await db.purchases.insert_one(doc)
    await log_activity(user, "create", "purchase", doc["id"], f"PO {doc['po']} · {input.supplier} · {input.quantity} {input.unit} {input.material} · {money_str(total)}", {"supplier": input.supplier, "material": input.material, "total": total})
    doc.pop("_id", None)
    return doc

@api_router.post("/production")
async def create_production(input: ProductionInput, user=Depends(current_user)):
    if input.output_qty <= 0:
        raise HTTPException(422, "Output qty harus lebih dari 0")
    material_cost = input.material_cost
    material_breakdown = []
    if input.material_lines:
        # Validate & compute actual material_cost from linked PO lines
        computed = 0
        for line in input.material_lines:
            pid = line.get("purchase_id")
            qty_used = float(line.get("qty_used", 0))
            if not pid or qty_used <= 0:
                raise HTTPException(422, "Setiap baris bahan harus memiliki purchase_id dan qty_used > 0")
            po = await db.purchases.find_one({"id": pid}, {"_id": 0})
            if not po:
                raise HTTPException(404, f"PO {pid} tidak ditemukan")
            remaining = po.get("remaining_qty", po.get("quantity", 0))
            if remaining < qty_used:
                raise HTTPException(409, f"PO {po.get('po')} hanya sisa {remaining} {po.get('unit')}")
            unit_cost = po.get("unit_cost", 0)
            line_cost = qty_used * unit_cost
            computed += line_cost
            material_breakdown.append({"purchase_id": pid, "po": po.get("po"), "material": po.get("material"), "qty_used": qty_used, "unit": po.get("unit"), "unit_cost": unit_cost, "line_cost": round(line_cost, 2)})
        material_cost = round(computed, 2)
        # Deduct remaining_qty from each PO
        for line in input.material_lines:
            await db.purchases.update_one({"id": line["purchase_id"]}, {"$inc": {"remaining_qty": -float(line["qty_used"])}})
    total = material_cost + input.labor_cost + input.overhead_cost
    doc = {"id": str(uuid.uuid4()), "batch": f"BTH-{datetime.now().strftime('%y%m%d')}-{str(uuid.uuid4())[:3].upper()}", "sku": input.sku, "product": input.product, "output_qty": input.output_qty, "material_cost": material_cost, "labor_cost": input.labor_cost, "overhead_cost": input.overhead_cost, "material_breakdown": material_breakdown, "total_cost": total, "hpp": round(total / input.output_qty, 2), "status": "Draft", "created_at": datetime.now(timezone.utc).isoformat()}
    await db.production.insert_one(doc)
    await log_activity(user, "create", "production", doc["id"], f"Batch {doc['batch']} · {input.product} · {input.output_qty} unit · HPP {money_str(doc['hpp'])}/unit", {"sku": input.sku, "output_qty": input.output_qty, "hpp": doc["hpp"], "linked_lines": len(material_breakdown)})
    doc.pop("_id", None)
    return doc

def _period_range(granularity: str, period: str):
    """Return (start_iso, end_iso, label, prev_start_iso, prev_end_iso, prev_label) for a granularity+period.
    granularity: 'monthly' | 'quarterly' | 'all'. period: 'YYYY-MM' or 'YYYY-Qn' (n=1..4)."""
    id_month = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
    if granularity == "all":
        return None, None, "Semua Periode", None, None, "—"
    if granularity == "monthly":
        y, m = int(period[:4]), int(period[5:7])
        start = datetime(y, m, 1, tzinfo=timezone.utc)
        end = datetime(y + (m // 12), (m % 12) + 1, 1, tzinfo=timezone.utc)
        pm, py = (m - 1, y) if m > 1 else (12, y - 1)
        p_start = datetime(py, pm, 1, tzinfo=timezone.utc)
        p_end = datetime(py + (pm // 12), (pm % 12) + 1, 1, tzinfo=timezone.utc)
        return start.isoformat(), end.isoformat(), f"{id_month[m-1]} {y}", p_start.isoformat(), p_end.isoformat(), f"{id_month[pm-1]} {py}"
    if granularity == "quarterly":
        y, q = int(period[:4]), int(period[6])
        sm = (q - 1) * 3 + 1
        em = sm + 3
        start = datetime(y, sm, 1, tzinfo=timezone.utc)
        end = datetime(y + (em - 1) // 12, ((em - 1) % 12) + 1, 1, tzinfo=timezone.utc)
        pq, py = (q - 1, y) if q > 1 else (4, y - 1)
        psm = (pq - 1) * 3 + 1
        pem = psm + 3
        p_start = datetime(py, psm, 1, tzinfo=timezone.utc)
        p_end = datetime(py + (pem - 1) // 12, ((pem - 1) % 12) + 1, 1, tzinfo=timezone.utc)
        return start.isoformat(), end.isoformat(), f"Q{q} {y}", p_start.isoformat(), p_end.isoformat(), f"Q{pq} {py}"
    raise HTTPException(422, "Granularity tidak valid")

def _aggregate(sales_rows, purchase_rows, production_rows, operating_expense: float):
    revenue = sum(item.get("revenue", 0) for item in sales_rows)
    cogs = sum(item.get("cogs", 0) for item in sales_rows)
    cash_out = sum(item.get("total", 0) for item in purchase_rows) + sum(item.get("total_cost", 0) for item in production_rows) + operating_expense
    gross_profit = revenue - cogs
    net_profit = gross_profit - operating_expense
    cash_net = revenue - cash_out
    return {"revenue": revenue, "cogs": cogs, "gross_profit": gross_profit, "operating_expense": operating_expense, "net_profit": net_profit, "cash_in": revenue, "cash_out": cash_out, "cash_net": cash_net, "transaction_count": len(sales_rows)}

def _pct(current, previous):
    if previous == 0:
        return None if current == 0 else 100.0 if current > 0 else -100.0
    return round((current - previous) / abs(previous) * 100, 1)

def _period_in_range(period_str, start_iso, end_iso):
    """Check if a YYYY-MM period falls within [start_iso, end_iso)."""
    if not start_iso:
        return True
    y, m = int(period_str[:4]), int(period_str[5:7])
    d_iso = datetime(y, m, 15, tzinfo=timezone.utc).isoformat()
    return start_iso <= d_iso < end_iso

async def _opex_total(start_iso, end_iso, channel_all: bool):
    if not channel_all:
        return 0
    rows = await db.operating_expenses.find({}, {"_id": 0}).to_list(2000)
    return sum(r.get("amount", 0) for r in rows if _period_in_range(r.get("period", ""), start_iso, end_iso))

@api_router.get("/opex")
async def list_opex(user=Depends(current_user)):
    return await db.operating_expenses.find({}, {"_id": 0}).sort("period", -1).to_list(500)

@api_router.post("/opex")
async def create_opex(input: OpExInput, user=Depends(admin_only)):
    import re
    if not re.match(r"^\d{4}-\d{2}$", input.period):
        raise HTTPException(422, "Periode harus format YYYY-MM")
    if input.amount < 0:
        raise HTTPException(422, "Nominal tidak boleh negatif")
    doc = {"id": str(uuid.uuid4()), **input.model_dump(), "created_at": datetime.now(timezone.utc).isoformat()}
    await db.operating_expenses.insert_one(doc)
    await log_activity(user, "create", "opex", doc["id"], f"Beban {input.category} · {input.period} · {money_str(input.amount)}", {"period": input.period, "category": input.category, "amount": input.amount})
    doc.pop("_id", None)
    return doc

@api_router.delete("/opex/{opex_id}")
async def delete_opex(opex_id: str, user=Depends(admin_only)):
    existing = await db.operating_expenses.find_one({"id": opex_id}, {"_id": 0})
    r = await db.operating_expenses.delete_one({"id": opex_id})
    if r.deleted_count != 1:
        raise HTTPException(404, "Beban operasional tidak ditemukan")
    if existing:
        await log_activity(user, "delete", "opex", opex_id, f"Beban {existing.get('category')} · {existing.get('period')} · {money_str(existing.get('amount', 0))} dihapus", {"period": existing.get("period"), "category": existing.get("category"), "amount": existing.get("amount")})
    return {"deleted": opex_id}

# ---------- Cash movements & Fixed assets (accounting) ----------

CASH_MOVEMENT_CATEGORIES = {"modal_masuk", "tarik_pribadi", "pinjaman_diterima", "bayar_cicilan_pinjaman", "transfer_kas_bank", "lain_lain"}

@api_router.get("/cash-movements")
async def list_cash_movements(user=Depends(admin_only)):
    return await db.cash_movements.find({}, {"_id": 0}).sort("date", -1).to_list(500)

@api_router.post("/cash-movements")
async def create_cash_movement(input: CashMovementInput, user=Depends(admin_only)):
    import re
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", input.date):
        raise HTTPException(422, "Tanggal harus format YYYY-MM-DD")
    if input.account not in {"kas", "bank"}:
        raise HTTPException(422, "Akun harus kas atau bank")
    if input.direction not in {"in", "out"}:
        raise HTTPException(422, "Arah harus in atau out")
    if input.category not in CASH_MOVEMENT_CATEGORIES:
        raise HTTPException(422, f"Kategori tidak valid. Pilih: {', '.join(sorted(CASH_MOVEMENT_CATEGORIES))}")
    if input.amount <= 0:
        raise HTTPException(422, "Nominal harus positif")
    doc = {"id": str(uuid.uuid4()), **input.model_dump(), "created_at": datetime.now(timezone.utc).isoformat()}
    await db.cash_movements.insert_one(doc)
    await log_activity(user, "create", "cash_movement", doc["id"], f"Kas/Bank {input.direction.upper()} · {input.account} · {input.category} · {money_str(input.amount)}", {"account": input.account, "direction": input.direction, "amount": input.amount})
    doc.pop("_id", None)
    return doc

@api_router.delete("/cash-movements/{cm_id}")
async def delete_cash_movement(cm_id: str, user=Depends(admin_only)):
    existing = await db.cash_movements.find_one({"id": cm_id}, {"_id": 0})
    r = await db.cash_movements.delete_one({"id": cm_id})
    if r.deleted_count != 1:
        raise HTTPException(404, "Transaksi tidak ditemukan")
    if existing:
        await log_activity(user, "delete", "cash_movement", cm_id, f"Kas/Bank {existing.get('direction','').upper()} · {existing.get('category')} · {money_str(existing.get('amount', 0))} dihapus")
    return {"deleted": cm_id}

def _asset_derived(asset, at_iso=None):
    from datetime import date as _date
    at = _date.fromisoformat(at_iso[:10]) if at_iso else datetime.now(timezone.utc).date()
    try:
        purchase = _date.fromisoformat(asset["purchase_date"])
    except Exception:
        return {"monthly_dep": 0, "elapsed_months": 0, "accumulated_dep": 0, "book_value": asset.get("purchase_cost", 0)}
    life = max(1, int(asset.get("useful_life_months", 1)))
    salvage = float(asset.get("salvage_value", 0))
    cost = float(asset.get("purchase_cost", 0))
    monthly = (cost - salvage) / life
    if at < purchase:
        elapsed = 0
    else:
        elapsed = min(life, (at.year - purchase.year) * 12 + (at.month - purchase.month))
    accumulated = round(elapsed * monthly, 2)
    book = round(cost - accumulated, 2)
    return {"monthly_dep": round(monthly, 2), "elapsed_months": elapsed, "accumulated_dep": accumulated, "book_value": book}


def _dep_in_range(assets, start_iso, end_iso):
    """Compute total straight-line depreciation for all assets within [start, end).
    If start_iso is empty (all-time report), return sum of accumulated_dep-to-date."""
    from datetime import date as _date
    if not start_iso:
        return sum(_asset_derived(a)["accumulated_dep"] for a in assets)
    try:
        start = _date.fromisoformat(start_iso[:10])
        end = _date.fromisoformat(end_iso[:10])
    except Exception:
        return 0
    total = 0.0
    for a in assets:
        try:
            purchase = _date.fromisoformat(a.get("purchase_date", ""))
        except Exception:
            continue
        life = max(1, int(a.get("useful_life_months", 1)))
        salvage = float(a.get("salvage_value", 0))
        cost = float(a.get("purchase_cost", 0))
        monthly = (cost - salvage) / life
        # asset ends depreciating after `life` months
        life_end_year = purchase.year + (purchase.month - 1 + life) // 12
        life_end_month = (purchase.month - 1 + life) % 12 + 1
        life_end = _date(life_end_year, life_end_month, 1)
        eff_start = max(start, purchase)
        eff_end = min(end, life_end)
        if eff_end <= eff_start:
            continue
        months = (eff_end.year - eff_start.year) * 12 + (eff_end.month - eff_start.month)
        total += max(0, months) * monthly
    return round(total, 2)


@api_router.get("/assets")
async def list_assets(user=Depends(admin_only)):
    rows = await db.fixed_assets.find({}, {"_id": 0}).sort("purchase_date", -1).to_list(500)
    for r in rows:
        r["derived"] = _asset_derived(r)
    return rows

@api_router.post("/assets")
async def create_asset(input: AssetInput, user=Depends(admin_only)):
    import re
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", input.purchase_date):
        raise HTTPException(422, "Tanggal beli harus format YYYY-MM-DD")
    if input.purchase_cost <= 0:
        raise HTTPException(422, "Harga perolehan harus positif")
    if input.useful_life_months <= 0:
        raise HTTPException(422, "Masa manfaat harus > 0 bulan")
    if input.salvage_value < 0 or input.salvage_value >= input.purchase_cost:
        raise HTTPException(422, "Nilai sisa harus 0 sampai < harga perolehan")
    doc = {"id": str(uuid.uuid4()), **input.model_dump(), "status": "aktif", "created_at": datetime.now(timezone.utc).isoformat()}
    await db.fixed_assets.insert_one(doc)
    await log_activity(user, "create", "asset", doc["id"], f"Aset {input.name} · {input.category} · {money_str(input.purchase_cost)} · masa manfaat {input.useful_life_months} bln", {"cost": input.purchase_cost, "life_months": input.useful_life_months})
    doc.pop("_id", None)
    doc["derived"] = _asset_derived(doc)
    return doc

@api_router.delete("/assets/{asset_id}")
async def delete_asset(asset_id: str, user=Depends(admin_only)):
    existing = await db.fixed_assets.find_one({"id": asset_id}, {"_id": 0})
    r = await db.fixed_assets.delete_one({"id": asset_id})
    if r.deleted_count != 1:
        raise HTTPException(404, "Aset tidak ditemukan")
    if existing:
        await log_activity(user, "delete", "asset", asset_id, f"Aset {existing.get('name')} dihapus")
    return {"deleted": asset_id}



@api_router.get("/activity-logs")
async def activity_logs(action: str = Query(""), entity: str = Query(""), user_email: str = Query(""), limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), user=Depends(admin_only)):
    q = {}
    if action:
        q["action"] = action
    if entity:
        q["entity"] = entity
    if user_email:
        q["user_email"] = user_email
    total = await db.activity_logs.count_documents(q)
    rows = await db.activity_logs.find(q, {"_id": 0}).sort("created_at", -1).skip(offset).limit(limit).to_list(limit)
    return {"total": total, "limit": limit, "offset": offset, "rows": rows}

@api_router.get("/activity-logs/export.csv")
async def export_activity_logs(action: str = Query(""), entity: str = Query(""), user_email: str = Query(""), user=Depends(admin_only)):
    import csv, io
    q = {}
    if action:
        q["action"] = action
    if entity:
        q["entity"] = entity
    if user_email:
        q["user_email"] = user_email
    rows = await db.activity_logs.find(q, {"_id": 0}).sort("created_at", -1).to_list(50000)
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(["created_at", "user_email", "user_role", "action", "entity", "entity_id", "summary", "details"])
    for r in rows:
        writer.writerow([r.get("created_at", ""), r.get("user_email", ""), r.get("user_role", ""), r.get("action", ""), r.get("entity", ""), r.get("entity_id", ""), r.get("summary", ""), json.dumps(r.get("details", {}), ensure_ascii=False)])
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"liniar-audit-{ts}.csv"
    return Response(content=buf.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"'})

BACKUP_COLLECTIONS = ["purchases", "production", "sales_transactions", "inventory", "operating_expenses"]

@api_router.post("/backups")
async def create_backup(user=Depends(admin_only)):
    """Create a full business-data snapshot and upload to Emergent Object Storage."""
    snapshot = {"generated_at": datetime.now(timezone.utc).isoformat(), "app": APP_NAME, "collections": {}}
    counts = {}
    for coll in BACKUP_COLLECTIONS:
        rows = await db[coll].find({}, {"_id": 0}).to_list(100000)
        snapshot["collections"][coll] = rows
        counts[coll] = len(rows)
    payload = json.dumps(snapshot, ensure_ascii=False, default=str).encode("utf-8")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"liniar-{ts}.json"
    path = f"{APP_NAME}/backups/{filename}"
    try:
        result = await asyncio.to_thread(put_object, path, payload, "application/json")
    except Exception as e:
        raise HTTPException(502, f"Gagal upload ke object storage: {e}")
    doc = {"id": str(uuid.uuid4()), "filename": filename, "storage_path": result.get("path", path), "size": result.get("size", len(payload)), "counts": counts, "total_rows": sum(counts.values()), "created_at": datetime.now(timezone.utc).isoformat(), "created_by": user.get("email")}
    await db.backups.insert_one(doc)
    await log_activity(user, "create", "backup", doc["id"], f"Backup {filename} · {doc['total_rows']} baris · {round(doc['size']/1024, 1)} KB", counts)
    doc.pop("_id", None)
    return doc

@api_router.get("/backups")
async def list_backups(user=Depends(admin_only)):
    return await db.backups.find({}, {"_id": 0}).sort("created_at", -1).limit(200).to_list(200)

@api_router.get("/backups/{backup_id}/download")
async def download_backup(backup_id: str, user=Depends(admin_only)):
    rec = await db.backups.find_one({"id": backup_id}, {"_id": 0})
    if not rec:
        raise HTTPException(404, "Backup tidak ditemukan")
    try:
        data, content_type = await asyncio.to_thread(get_object, rec["storage_path"])
    except Exception as e:
        raise HTTPException(502, f"Gagal ambil backup dari storage: {e}")
    return Response(content=data, media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{rec["filename"]}"'})



@api_router.get("/sales-by-channel")
async def sales_by_channel(user=Depends(current_user)):
    id_month = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
    channels = ["Offline", "Bazar", "Shopee", "Tokopedia", "TikTok"]
    now = datetime.now(timezone.utc)
    months = []
    for i in range(5, -1, -1):
        y, m = now.year, now.month - i
        while m <= 0:
            m += 12
            y -= 1
        months.append((y, m))
    rows = []
    for y, m in months:
        start = datetime(y, m, 1, tzinfo=timezone.utc).isoformat()
        end = datetime(y + (m // 12), (m % 12) + 1, 1, tzinfo=timezone.utc).isoformat()
        sales = await db.sales_transactions.find({"created_at": {"$gte": start, "$lt": end}}, {"_id": 0}).to_list(2000)
        by_ch = {c: sum(s.get("revenue", 0) for s in sales if s.get("channel") == c) for c in channels}
        rows.append({"label": id_month[m - 1], "year": y, "month": m, "channels": by_ch, "total": sum(by_ch.values())})
    return {"months": rows, "channels": channels}

@api_router.get("/reports")
async def reports(channel: str = Query("Semua"), granularity: str = Query("all"), period: str = Query(""), user=Depends(admin_only)):
    valid_channels = {"Semua", "Offline", "Bazar", "Shopee", "Tokopedia", "TikTok"}
    if channel not in valid_channels:
        raise HTTPException(422, "Kanal laporan tidak valid")
    if granularity not in {"all", "monthly", "quarterly"}:
        raise HTTPException(422, "Granularity tidak valid")
    if granularity != "all" and not period:
        raise HTTPException(422, "Period wajib diisi untuk granularity monthly/quarterly")
    if granularity == "monthly":
        import re
        if not re.match(r"^\d{4}-(0[1-9]|1[0-2])$", period):
            raise HTTPException(422, "Format period harus YYYY-MM (contoh 2026-08)")
    if granularity == "quarterly":
        import re
        if not re.match(r"^\d{4}-Q[1-4]$", period):
            raise HTTPException(422, "Format period harus YYYY-Qn (contoh 2026-Q3)")
    start, end, label, p_start, p_end, p_label = _period_range(granularity, period)
    channel_q = {} if channel == "Semua" else {"channel": channel}
    def range_q(s, e):
        if not s:
            return {}
        return {"created_at": {"$gte": s, "$lt": e}}
    sales = await db.sales_transactions.find({**channel_q, **range_q(start, end)}, {"_id": 0}).to_list(2000)
    purchases = await db.purchases.find(range_q(start, end), {"_id": 0}).to_list(2000)
    production = await db.production.find(range_q(start, end), {"_id": 0}).to_list(2000)
    opex = await _opex_total(start, end, channel == "Semua")
    all_assets_for_dep = await db.fixed_assets.find({}, {"_id": 0}).to_list(500)
    current_dep = _dep_in_range(all_assets_for_dep, start, end) if channel == "Semua" else 0
    current = _aggregate(sales, purchases, production, opex)
    current["depreciation"] = current_dep
    current["net_profit"] = current["gross_profit"] - current["operating_expense"] - current_dep
    previous = None
    if p_start:
        p_sales = await db.sales_transactions.find({**channel_q, **range_q(p_start, p_end)}, {"_id": 0}).to_list(2000)
        p_purchases = await db.purchases.find(range_q(p_start, p_end), {"_id": 0}).to_list(2000)
        p_production = await db.production.find(range_q(p_start, p_end), {"_id": 0}).to_list(2000)
        p_opex = await _opex_total(p_start, p_end, channel == "Semua")
        p_dep = _dep_in_range(all_assets_for_dep, p_start, p_end) if channel == "Semua" else 0
        previous = _aggregate(p_sales, p_purchases, p_production, p_opex)
        previous["depreciation"] = p_dep
        previous["net_profit"] = previous["gross_profit"] - previous["operating_expense"] - p_dep
    inventory = await db.inventory.find({}, {"_id": 0}).to_list(1000)
    persediaan_bahan = sum(x.get("value", 0) for x in inventory if x.get("type") == "Bahan Baku")
    persediaan_barang_jadi = sum(x.get("value", 0) for x in inventory if x.get("type") == "Barang Jadi")
    inventory_value = persediaan_bahan + persediaan_barang_jadi
    # Cash & bank from manual cash_movements (independent of period for balance sheet snapshot)
    all_cm = await db.cash_movements.find({}, {"_id": 0}).to_list(2000)
    kas_manual_in = sum(m["amount"] for m in all_cm if m.get("account") == "kas" and m.get("direction") == "in")
    kas_manual_out = sum(m["amount"] for m in all_cm if m.get("account") == "kas" and m.get("direction") == "out")
    bank_in = sum(m["amount"] for m in all_cm if m.get("account") == "bank" and m.get("direction") == "in")
    bank_out = sum(m["amount"] for m in all_cm if m.get("account") == "bank" and m.get("direction") == "out")
    # Sales/purchases/production/opex ALL time affect operational kas (regardless of report period filter)
    all_sales = await db.sales_transactions.find({}, {"_id": 0}).to_list(5000)
    all_purchases = await db.purchases.find({}, {"_id": 0}).to_list(5000)
    all_production = await db.production.find({}, {"_id": 0}).to_list(5000)
    all_opex = await db.operating_expenses.find({}, {"_id": 0}).to_list(2000)
    op_cash_in = sum(s.get("revenue", 0) for s in all_sales)
    op_cash_out = sum(p.get("total", 0) for p in all_purchases) + sum(pr.get("total_cost", 0) for pr in all_production) + sum(o.get("amount", 0) for o in all_opex)
    kas = op_cash_in - op_cash_out + kas_manual_in - kas_manual_out
    bank = bank_in - bank_out
    # Fixed assets
    all_assets = await db.fixed_assets.find({}, {"_id": 0}).to_list(500)
    total_perolehan = sum(a.get("purchase_cost", 0) for a in all_assets)
    total_akumulasi_penyusutan = 0
    fixed_asset_items = []
    for a in all_assets:
        d = _asset_derived(a)
        total_akumulasi_penyusutan += d["accumulated_dep"]
        fixed_asset_items.append({"id": a.get("id"), "name": a.get("name"), "category": a.get("category"), "purchase_cost": a.get("purchase_cost", 0), "accumulated_dep": d["accumulated_dep"], "book_value": d["book_value"]})
    total_nilai_buku_aset = total_perolehan - total_akumulasi_penyusutan
    # Aset lancar total
    total_aset_lancar = max(kas, 0) + max(bank, 0) + persediaan_bahan + persediaan_barang_jadi
    total_assets = total_aset_lancar + total_nilai_buku_aset
    # Liabilities from cash_movements
    utang_pinjaman_masuk = sum(m["amount"] for m in all_cm if m.get("category") == "pinjaman_diterima")
    utang_pinjaman_bayar = sum(m["amount"] for m in all_cm if m.get("category") == "bayar_cicilan_pinjaman")
    utang_pinjaman = max(0, utang_pinjaman_masuk - utang_pinjaman_bayar)
    total_liabilities = utang_pinjaman
    # Equity from owner
    modal_masuk = sum(m["amount"] for m in all_cm if m.get("category") == "modal_masuk")
    tarik_pribadi = sum(m["amount"] for m in all_cm if m.get("category") == "tarik_pribadi")
    modal_disetor = modal_masuk - tarik_pribadi
    laba_ditahan = total_assets - total_liabilities - modal_disetor
    balance_detail = {
        "assets": {
            "lancar": {"kas": kas, "bank": bank, "kas_setara_total": max(kas, 0) + max(bank, 0), "persediaan_bahan": persediaan_bahan, "persediaan_barang_jadi": persediaan_barang_jadi, "persediaan_total": persediaan_bahan + persediaan_barang_jadi, "total": total_aset_lancar},
            "tetap": {"items": fixed_asset_items, "total_perolehan": total_perolehan, "total_akumulasi_penyusutan": total_akumulasi_penyusutan, "total_nilai_buku": total_nilai_buku_aset},
            "tidak_lancar_total": total_nilai_buku_aset,
            "total": total_assets,
        },
        "liabilities": {
            "jangka_pendek": {"total": 0},
            "jangka_panjang": {"utang_pinjaman": utang_pinjaman, "total": utang_pinjaman},
            "utang_pinjaman": utang_pinjaman,
            "total": total_liabilities,
        },
        "equity": {"modal_disetor": modal_disetor, "laba_ditahan": laba_ditahan, "total": modal_disetor + laba_ditahan},
    }
    # Keep legacy top-level flat fields for backward compat with old clients
    channel_summary = {}
    if channel == "Semua":
        for name in ["Offline", "Bazar", "Shopee", "Tokopedia", "TikTok"]:
            rows = [t for t in sales if t.get("channel") == name]
            channel_summary[name] = {"revenue": sum(r.get("revenue", 0) for r in rows), "count": len(rows)}
    deltas = None
    if previous:
        deltas = {"revenue_pct": _pct(current["revenue"], previous["revenue"]), "net_profit_pct": _pct(current["net_profit"], previous["net_profit"]), "cash_net_pct": _pct(current["cash_net"], previous["cash_net"])}
    return {"period": label, "previous_period": p_label if previous else None, "granularity": granularity, "channel": channel, "transaction_count": current["transaction_count"], "income": {"revenue": current["revenue"], "cogs": current["cogs"], "gross_profit": current["gross_profit"], "operating_expense": current["operating_expense"], "depreciation": current["depreciation"], "net_profit": current["net_profit"]}, "balance": {"assets": total_assets, "liabilities": total_liabilities, "equity": total_assets - total_liabilities, "detail": balance_detail}, "cash": {"in": current["cash_in"], "out": current["cash_out"], "net": current["cash_net"]}, "previous": previous, "deltas": deltas, "channel_summary": channel_summary}

# Add your routes to the router instead of directly to app
@api_router.get("/")
async def root():
    return {"message": "Hello World"}

@api_router.post("/status", response_model=StatusCheck)
async def create_status_check(input: StatusCheckCreate):
    status_dict = input.model_dump()
    status_obj = StatusCheck(**status_dict)
    
    # Convert to dict and serialize datetime to ISO string for MongoDB
    doc = status_obj.model_dump()
    doc['timestamp'] = doc['timestamp'].isoformat()
    
    _ = await db.status_checks.insert_one(doc)
    return status_obj

@api_router.get("/status", response_model=List[StatusCheck])
async def get_status_checks():
    # Exclude MongoDB's _id field from the query results
    status_checks = await db.status_checks.find({}, {"_id": 0}).to_list(1000)
    
    # Convert ISO string timestamps back to datetime objects
    for check in status_checks:
        if isinstance(check['timestamp'], str):
            check['timestamp'] = datetime.fromisoformat(check['timestamp'])
    
    return status_checks

# ---------- Public catalog (no auth) ----------

async def _compute_retail_price(sku: str, cost_value: float) -> float:
    """Latest sale unit_price for the SKU, else fallback to 1.4x cost per unit."""
    latest = await db.sales_transactions.find_one({"sku": sku}, {"_id": 0, "unit_price": 1}, sort=[("created_at", -1)])
    if latest and latest.get("unit_price"):
        return float(latest["unit_price"])
    return round(cost_value * 1.4, -2) if cost_value else 0

@api_router.get("/public/catalog")
async def public_catalog():
    items = await db.inventory.find({"type": "Barang Jadi", "available": {"$gt": 0}}, {"_id": 0}).to_list(200)
    result = []
    for item in items:
        per_unit_cost = (item.get("value", 0) / item.get("stock", 1)) if item.get("stock") else 0
        price = await _compute_retail_price(item["sku"], per_unit_cost)
        result.append({
            "sku": item["sku"],
            "name": item.get("name"),
            "variant": item.get("variant"),
            "available": item.get("available", 0),
            "unit": item.get("unit", "pcs"),
            "price": price,
            "has_photo": bool(item.get("photo_path")),
        })
    return {"brand": "Liniar", "items": result, "count": len(result)}

@api_router.get("/public/catalog/{sku}/photo")
async def public_catalog_photo(sku: str):
    item = await db.inventory.find_one({"sku": sku, "type": "Barang Jadi"}, {"_id": 0})
    if not item or not item.get("photo_path"):
        raise HTTPException(404, "Foto tidak tersedia")
    try:
        data, content_type = await asyncio.to_thread(get_object, item["photo_path"])
    except Exception as e:
        raise HTTPException(502, f"Gagal ambil foto: {e}")
    return Response(content=data, media_type=item.get("photo_content_type") or content_type, headers={"Cache-Control": "public, max-age=600"})



# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=[os.environ.get("FRONTEND_URL")] if os.environ.get("FRONTEND_URL") else os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()