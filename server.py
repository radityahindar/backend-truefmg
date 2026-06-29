import os
import re
import uuid
import random
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, APIRouter, HTTPException
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, EmailStr

from seed_shop import SHOP_CATEGORIES, SHOP_CATEGORY_SLUGS, SHOP_PRODUCTS

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

app = FastAPI(title="True Manufacturing Store API")
api_router = APIRouter(prefix="/api")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SHIPPING_FLAT = 25.0
FREE_SHIPPING_THRESHOLD = 500.0
TAX_RATE = 0.08


# ----------------------- Models -----------------------
class Product(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    slug: str
    name: str
    category: str
    sku: str = ""
    price: float
    short_description: str = ""
    description: str = ""
    features: List[str] = []
    image: str = ""
    in_stock: bool = True
    featured: bool = False


class CartItemIn(BaseModel):
    slug: str
    quantity: int = 1


class CustomerIn(BaseModel):
    name: str
    email: EmailStr
    phone: Optional[str] = ""


class ShippingIn(BaseModel):
    address: str
    city: str
    state: str
    zip: str
    country: str = "United States"


class OrderCreate(BaseModel):
    items: List[CartItemIn]
    customer: CustomerIn
    shipping: ShippingIn
    notes: Optional[str] = ""


class OrderItem(BaseModel):
    slug: str
    name: str
    price: float
    quantity: int
    image: str = ""


class Order(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    order_number: str
    items: List[OrderItem]
    customer: CustomerIn
    shipping: ShippingIn
    notes: Optional[str] = ""
    subtotal: float
    shipping_cost: float
    tax: float
    total: float
    status: str = "confirmed"
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ----------------------- Helpers -----------------------
def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def clean(doc: dict) -> dict:
    doc.pop("_id", None)
    return doc


def gen_order_number() -> str:
    return "TM-" + "".join(random.choices("0123456789", k=7))


# ----------------------- Routes -----------------------
@api_router.get("/")
async def root():
    return {"message": "True Manufacturing Store API"}


@api_router.get("/shop/categories")
async def get_categories():
    result = []
    for cat in SHOP_CATEGORIES:
        count = await db.shop_products.count_documents({"category": cat["slug"]})
        result.append({**cat, "product_count": count})
    return result


@api_router.get("/shop/products", response_model=List[Product])
async def list_products(
    category: Optional[str] = None,
    search: Optional[str] = None,
    featured: Optional[bool] = None,
):
    query: dict = {}
    if category:
        query["category"] = category
    if featured is not None:
        query["featured"] = featured
    if search:
        query["$or"] = [
            {"name": {"$regex": search, "$options": "i"}},
            {"short_description": {"$regex": search, "$options": "i"}},
            {"description": {"$regex": search, "$options": "i"}},
        ]
    docs = await db.shop_products.find(query).to_list(1000)
    return [Product(**clean(d)) for d in docs]


@api_router.get("/shop/products/{slug}", response_model=Product)
async def get_product(slug: str):
    doc = await db.shop_products.find_one({"slug": slug})
    if not doc:
        raise HTTPException(status_code=404, detail="Product not found")
    return Product(**clean(doc))


@api_router.post("/shop/orders", response_model=Order)
async def create_order(payload: OrderCreate):
    if not payload.items:
        raise HTTPException(status_code=400, detail="Cart is empty")

    order_items: List[OrderItem] = []
    subtotal = 0.0
    for ci in payload.items:
        doc = await db.shop_products.find_one({"slug": ci.slug})
        if not doc:
            raise HTTPException(status_code=400, detail=f"Product not found: {ci.slug}")
        qty = max(1, ci.quantity)
        subtotal += doc["price"] * qty
        order_items.append(
            OrderItem(
                slug=doc["slug"], name=doc["name"], price=doc["price"],
                quantity=qty, image=doc.get("image", ""),
            )
        )

    shipping_cost = 0.0 if subtotal >= FREE_SHIPPING_THRESHOLD else SHIPPING_FLAT
    tax = round(subtotal * TAX_RATE, 2)
    total = round(subtotal + shipping_cost + tax, 2)

    order = Order(
        order_number=gen_order_number(),
        items=order_items,
        customer=payload.customer,
        shipping=payload.shipping,
        notes=payload.notes,
        subtotal=round(subtotal, 2),
        shipping_cost=shipping_cost,
        tax=tax,
        total=total,
    )
    await db.shop_orders.insert_one(order.dict())
    return order


@api_router.get("/shop/orders/{order_number}", response_model=Order)
async def get_order(order_number: str):
    doc = await db.shop_orders.find_one({"order_number": order_number})
    if not doc:
        raise HTTPException(status_code=404, detail="Order not found")
    return Order(**clean(doc))


# ----------------------- Startup: Seed -----------------------
@app.on_event("startup")
async def seed_database():
    count = await db.shop_products.count_documents({})
    if count == 0:
        for p in SHOP_PRODUCTS:
            product = Product(slug=slugify(p["name"]), **p)
            await db.shop_products.insert_one(product.dict())
        logger.info("Seeded %d shop products", len(SHOP_PRODUCTS))


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
