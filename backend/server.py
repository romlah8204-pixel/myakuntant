from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, Depends, Query
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

class SaleInput(BaseModel):
    channel: str
    sku: str
    quantity: int
    unit_price: float
    customer: str = "Pelanggan umum"
    order_ref: str = ""

def public_user(user):
    return {"id": user["id"], "email": user["email"], "name": user["name"], "role": user["role"]}

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
    doc.pop("_id", None)
    return doc

@api_router.post("/purchases")
async def create_purchase(input: PurchaseInput, user=Depends(current_user)):
    total = input.quantity * input.unit_cost
    doc = {"id":str(uuid.uuid4()), "po":f"PO-{datetime.now().strftime('%y%m')}-{str(uuid.uuid4())[:4].upper()}", "supplier":input.supplier, "material":input.material, "quantity":input.quantity, "unit":input.unit, "total":total, "status":"Menunggu", "created_at": datetime.now(timezone.utc).isoformat()}
    await db.purchases.insert_one(doc)
    doc.pop("_id", None)
    return doc

@api_router.post("/production")
async def create_production(input: ProductionInput, user=Depends(current_user)):
    total = input.material_cost + input.labor_cost + input.overhead_cost
    doc = {"id":str(uuid.uuid4()), "batch":f"BTH-{datetime.now().strftime('%y%m%d')}-{str(uuid.uuid4())[:3].upper()}", **input.model_dump(), "total_cost":total, "hpp":round(total / input.output_qty, 2), "status":"Draft", "created_at": datetime.now(timezone.utc).isoformat()}
    await db.production.insert_one(doc)
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

def _aggregate(sales_rows, purchase_rows, production_rows, channel_filter_all: bool):
    revenue = sum(item.get("revenue", 0) for item in sales_rows)
    cogs = sum(item.get("cogs", 0) for item in sales_rows)
    cash_out = sum(item.get("total", 0) for item in purchase_rows) + sum(item.get("total_cost", 0) for item in production_rows)
    operating_expense = 3840000 if channel_filter_all else 0
    gross_profit = revenue - cogs
    net_profit = gross_profit - operating_expense
    cash_net = revenue - cash_out
    return {"revenue": revenue, "cogs": cogs, "gross_profit": gross_profit, "operating_expense": operating_expense, "net_profit": net_profit, "cash_in": revenue, "cash_out": cash_out, "cash_net": cash_net, "transaction_count": len(sales_rows)}

def _pct(current, previous):
    if previous == 0:
        return None if current == 0 else 100.0 if current > 0 else -100.0
    return round((current - previous) / abs(previous) * 100, 1)

@api_router.get("/reports")
async def reports(channel: str = Query("Semua"), granularity: str = Query("all"), period: str = Query(""), user=Depends(current_user)):
    valid_channels = {"Semua", "Offline", "Bazar", "Shopee", "Tokopedia", "TikTok"}
    if channel not in valid_channels:
        raise HTTPException(422, "Kanal laporan tidak valid")
    if granularity not in {"all", "monthly", "quarterly"}:
        raise HTTPException(422, "Granularity tidak valid")
    if granularity != "all" and not period:
        raise HTTPException(422, "Period wajib diisi untuk granularity monthly/quarterly")
    start, end, label, p_start, p_end, p_label = _period_range(granularity, period)
    channel_q = {} if channel == "Semua" else {"channel": channel}
    def range_q(s, e):
        if not s:
            return {}
        return {"created_at": {"$gte": s, "$lt": e}}
    sales = await db.sales_transactions.find({**channel_q, **range_q(start, end)}, {"_id": 0}).to_list(2000)
    purchases = await db.purchases.find(range_q(start, end), {"_id": 0}).to_list(2000)
    production = await db.production.find(range_q(start, end), {"_id": 0}).to_list(2000)
    current = _aggregate(sales, purchases, production, channel == "Semua")
    previous = None
    if p_start:
        p_sales = await db.sales_transactions.find({**channel_q, **range_q(p_start, p_end)}, {"_id": 0}).to_list(2000)
        p_purchases = await db.purchases.find(range_q(p_start, p_end), {"_id": 0}).to_list(2000)
        p_production = await db.production.find(range_q(p_start, p_end), {"_id": 0}).to_list(2000)
        previous = _aggregate(p_sales, p_purchases, p_production, channel == "Semua")
    inventory = await db.inventory.find({}, {"_id": 0}).to_list(1000)
    inventory_value = sum(item.get("value", 0) for item in inventory)
    assets = inventory_value + max(current["cash_net"], 0)
    liabilities = 21800000
    channel_summary = {}
    if channel == "Semua":
        for name in ["Offline", "Bazar", "Shopee", "Tokopedia", "TikTok"]:
            rows = [t for t in sales if t.get("channel") == name]
            channel_summary[name] = {"revenue": sum(r.get("revenue", 0) for r in rows), "count": len(rows)}
    deltas = None
    if previous:
        deltas = {"revenue_pct": _pct(current["revenue"], previous["revenue"]), "net_profit_pct": _pct(current["net_profit"], previous["net_profit"]), "cash_net_pct": _pct(current["cash_net"], previous["cash_net"])}
    return {"period": label, "previous_period": p_label if previous else None, "granularity": granularity, "channel": channel, "transaction_count": current["transaction_count"], "income": {"revenue": current["revenue"], "cogs": current["cogs"], "gross_profit": current["gross_profit"], "operating_expense": current["operating_expense"], "net_profit": current["net_profit"]}, "balance": {"assets": assets, "liabilities": liabilities, "equity": assets - liabilities}, "cash": {"in": current["cash_in"], "out": current["cash_out"], "net": current["cash_net"]}, "previous": previous, "deltas": deltas, "channel_summary": channel_summary}

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