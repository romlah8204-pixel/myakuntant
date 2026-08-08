from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, Depends
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

@api_router.get("/sales")
async def sales(user=Depends(current_user)):
    return await db.sales_transactions.find({}, {"_id":0}).sort("created_at", -1).to_list(100)

@api_router.post("/sales")
async def create_sale(input: SaleInput, user=Depends(current_user)):
    if input.channel not in {"Offline", "Bazar", "Marketplace"}:
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
    await db.inventory.update_one({"sku": input.sku}, {"$inc": {"stock": -input.quantity, "available": -input.quantity}})
    await db.sales_transactions.insert_one(doc)
    doc.pop("_id", None)
    return doc

@api_router.post("/purchases")
async def create_purchase(input: PurchaseInput, user=Depends(current_user)):
    total = input.quantity * input.unit_cost
    doc = {"id":str(uuid.uuid4()), "po":f"PO-{datetime.now().strftime('%y%m')}-{str(uuid.uuid4())[:4].upper()}", "supplier":input.supplier, "material":input.material, "quantity":input.quantity, "unit":input.unit, "total":total, "status":"Menunggu"}
    await db.purchases.insert_one(doc)
    doc.pop("_id", None)
    return doc

@api_router.post("/production")
async def create_production(input: ProductionInput, user=Depends(current_user)):
    total = input.material_cost + input.labor_cost + input.overhead_cost
    doc = {"id":str(uuid.uuid4()), "batch":f"BTH-{datetime.now().strftime('%y%m%d')}-{str(uuid.uuid4())[:3].upper()}", **input.model_dump(), "total_cost":total, "hpp":round(total / input.output_qty, 2), "status":"Draft"}
    await db.production.insert_one(doc)
    doc.pop("_id", None)
    return doc

@api_router.get("/reports")
async def reports(user=Depends(current_user)):
    return {"period":"Juni 2024","income":{"revenue":34200000,"cogs":22970000,"gross_profit":11230000,"operating_expense":3840000,"net_profit":7390000},"balance":{"assets":68350000,"liabilities":21800000,"equity":46550000},"cash":{"in":34200000,"out":26700000,"net":7500000}}

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