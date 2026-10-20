"""Endura Human · API de gestión.

Inventario por lotes con vencimiento (FEFO), ventas con cartera, patrocinios y salidas sin venta,
compras con coste aterrizado, gastos, aportes de socios y resultados mensuales.
"""
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone, date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Literal, List

import jwt
import psycopg
from psycopg.rows import dict_row
from psycopg.errors import UniqueViolation
from fastapi import FastAPI, Form, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from pwdlib import PasswordHash

DATABASE_URL = os.environ["DATABASE_URL"]
SECRET = os.environ["SESSION_SECRET"]
COOKIE = "endura_session"
HOURS = 10
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "true").lower() != "false"
hasher = PasswordHash.recommended()
Q2 = Decimal("0.01")

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ_NAME = os.environ.get("APP_TIMEZONE", "America/Bogota")
    LOCAL_TZ = ZoneInfo(LOCAL_TZ_NAME)
except Exception:  # imagen sin datos de zonas horarias
    LOCAL_TZ_NAME = "America/Bogota"
    LOCAL_TZ = timezone(timedelta(hours=-5))


def local_today() -> date:
    return datetime.now(LOCAL_TZ).date()


def money(v) -> Decimal:
    return Decimal(v).quantize(Q2, ROUND_HALF_UP)


def connect():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


# ------------------------------- CATÁLOGOS -------------------------------
PRODUCT_CATEGORIES = {
    "gel": "Geles", "drink_mix": "Bebidas y drink mix", "electrolytes": "Electrolitos y sales",
    "chews": "Gomitas y barras", "recovery": "Recuperación", "performance": "Rendimiento",
    "accessory": "Accesorios", "other": "Otro",
}
PAYMENT_METHODS = {"bancolombia": "Bancolombia", "nequi": "Nequi", "cash": "Efectivo", "card": "Tarjeta", "other": "Otro"}
GIVEAWAY_REASONS = {
    "ambassador": "Embajador / atleta", "event": "Activación o evento", "alliance": "Intercambio o alianza",
    "internal": "Uso interno o degustación", "damage": "Daño o vencimiento", "other": "Otro",
}
EXPENSE_CATEGORIES = {
    "delivery": "Domicilios", "shipping": "Transportadoras y envíos", "marketing": "Marketing y patrocinios",
    "comms": "Comunicaciones y redes", "logistics": "Logística y empaques", "obligations": "Obligaciones e impuestos",
    "platforms": "Plataformas y software", "other": "Otros",
}
CAPITAL_KINDS = {"cash": "Aporte en dinero", "in_kind": "Aporte en especie", "withdrawal": "Retiro o reintegro"}
MOVEMENT_LABELS = {
    "initial": "Saldo inicial", "purchase": "Entrada por compra", "purchase_reversal": "Reversión de compra",
    "manual_in": "Entrada manual", "manual_out": "Salida manual", "sale": "Venta", "sale_void": "Venta anulada",
    "giveaway": "Patrocinio / salida", "giveaway_void": "Patrocinio anulado",
}
EXPIRY_WARNING_DAYS = 90

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS app_users(
        id BIGSERIAL PRIMARY KEY, username VARCHAR(80) UNIQUE NOT NULL, password_hash TEXT NOT NULL,
        role VARCHAR(20) NOT NULL DEFAULT 'user' CHECK(role IN ('admin','user')),
        is_active BOOLEAN NOT NULL DEFAULT TRUE, session_version INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
    """CREATE TABLE IF NOT EXISTS business_settings(
        id SMALLINT PRIMARY KEY DEFAULT 1 CHECK(id=1), name VARCHAR(160) NOT NULL DEFAULT 'Endura Human',
        owner VARCHAR(160) NOT NULL DEFAULT '', tax_id VARCHAR(60) NOT NULL DEFAULT '', address VARCHAR(200) NOT NULL DEFAULT '',
        phone VARCHAR(60) NOT NULL DEFAULT '', email VARCHAR(160) NOT NULL DEFAULT '', invoice_prefix VARCHAR(10) NOT NULL DEFAULT 'EH',
        invoice_note TEXT NOT NULL DEFAULT '', updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
    "INSERT INTO business_settings(id) VALUES(1) ON CONFLICT DO NOTHING",
    """CREATE TABLE IF NOT EXISTS brands(
        id BIGSERIAL PRIMARY KEY, name VARCHAR(100) UNIQUE NOT NULL, is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
    """CREATE TABLE IF NOT EXISTS products(
        id BIGSERIAL PRIMARY KEY, sku VARCHAR(40) UNIQUE NOT NULL, name VARCHAR(200) NOT NULL,
        brand_id BIGINT NOT NULL REFERENCES brands(id), category VARCHAR(30) NOT NULL DEFAULT 'other',
        units_per_box INTEGER NOT NULL DEFAULT 1 CHECK(units_per_box>=1),
        quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity>=0), min_stock INTEGER NOT NULL DEFAULT 0 CHECK(min_stock>=0),
        unit_cost NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK(unit_cost>=0),
        sale_price NUMERIC(14,2) CHECK(sale_price IS NULL OR sale_price>=0),
        notes TEXT NOT NULL DEFAULT '', is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
    """CREATE TABLE IF NOT EXISTS purchases(
        id BIGSERIAL PRIMARY KEY, purchase_date DATE NOT NULL, supplier VARCHAR(120) NOT NULL,
        funded_by VARCHAR(80) NOT NULL DEFAULT '', origin VARCHAR(60) NOT NULL DEFAULT '',
        currency VARCHAR(3) NOT NULL DEFAULT 'COP' CHECK(currency IN ('COP','USD','EUR','GBP')),
        fx_rate NUMERIC(14,4) NOT NULL DEFAULT 1 CHECK(fx_rate>0),
        invoice_no VARCHAR(80) NOT NULL DEFAULT '', tracking VARCHAR(120) NOT NULL DEFAULT '', carrier VARCHAR(80) NOT NULL DEFAULT '',
        shipping_cost NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK(shipping_cost>=0),
        other_costs NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK(other_costs>=0),
        goods_total NUMERIC(14,2) NOT NULL DEFAULT 0, total_cop NUMERIC(14,2) NOT NULL DEFAULT 0,
        status VARCHAR(20) NOT NULL DEFAULT 'ordered' CHECK(status IN ('ordered','received','cancelled')),
        imported BOOLEAN NOT NULL DEFAULT FALSE, note TEXT NOT NULL DEFAULT '',
        received_at TIMESTAMPTZ, received_by BIGINT REFERENCES app_users(id),
        created_by BIGINT REFERENCES app_users(id), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
    """CREATE TABLE IF NOT EXISTS purchase_items(
        id BIGSERIAL PRIMARY KEY, purchase_id BIGINT NOT NULL REFERENCES purchases(id) ON DELETE CASCADE,
        product_id BIGINT NOT NULL REFERENCES products(id), quantity INTEGER NOT NULL CHECK(quantity>0),
        unit_price NUMERIC(14,4) NOT NULL CHECK(unit_price>=0), expiry_date DATE, landed_unit_cost NUMERIC(14,2))""",
    """CREATE TABLE IF NOT EXISTS lots(
        id BIGSERIAL PRIMARY KEY, product_id BIGINT NOT NULL REFERENCES products(id), expiry_date DATE,
        quantity INTEGER NOT NULL CHECK(quantity>=0), initial_quantity INTEGER NOT NULL CHECK(initial_quantity>=0),
        unit_cost NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK(unit_cost>=0), source VARCHAR(20) NOT NULL,
        purchase_id BIGINT REFERENCES purchases(id), note TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
    "CREATE INDEX IF NOT EXISTS lots_product_idx ON lots(product_id)",
    """CREATE TABLE IF NOT EXISTS sales(
        id BIGSERIAL PRIMARY KEY, kind VARCHAR(10) NOT NULL DEFAULT 'sale' CHECK(kind IN ('sale','giveaway')),
        number INTEGER NOT NULL, sale_date DATE NOT NULL,
        customer_name VARCHAR(160) NOT NULL DEFAULT '', customer_contact VARCHAR(160) NOT NULL DEFAULT '',
        customer_doc VARCHAR(40) NOT NULL DEFAULT '', customer_address VARCHAR(200) NOT NULL DEFAULT '',
        customer_city VARCHAR(80) NOT NULL DEFAULT '', referrer VARCHAR(80) NOT NULL DEFAULT '', reason VARCHAR(20),
        subtotal NUMERIC(14,2) NOT NULL CHECK(subtotal>=0), discount_percent NUMERIC(7,3) NOT NULL DEFAULT 0,
        discount NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK(discount>=0), delivery_fee NUMERIC(14,2) NOT NULL DEFAULT 0 CHECK(delivery_fee>=0),
        total NUMERIC(14,2) NOT NULL CHECK(total>=0), cost_total NUMERIC(14,2) NOT NULL DEFAULT 0,
        delivery_status VARCHAR(12) NOT NULL DEFAULT 'delivered' CHECK(delivery_status IN ('delivered','pending')),
        status VARCHAR(12) NOT NULL DEFAULT 'completed' CHECK(status IN ('completed','voided')),
        imported BOOLEAN NOT NULL DEFAULT FALSE, note TEXT NOT NULL DEFAULT '', client_ref VARCHAR(64) UNIQUE,
        created_by BIGINT REFERENCES app_users(id), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        voided_by BIGINT REFERENCES app_users(id), voided_at TIMESTAMPTZ, void_reason TEXT NOT NULL DEFAULT '',
        UNIQUE(kind,number), CHECK(discount<=subtotal))""",
    "CREATE INDEX IF NOT EXISTS sales_date_idx ON sales(sale_date)",
    """CREATE TABLE IF NOT EXISTS sale_items(
        id BIGSERIAL PRIMARY KEY, sale_id BIGINT NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
        product_id BIGINT NOT NULL REFERENCES products(id), quantity INTEGER NOT NULL CHECK(quantity>0),
        unit_price NUMERIC(14,2) NOT NULL CHECK(unit_price>=0), line_total NUMERIC(14,2) NOT NULL,
        unit_cost NUMERIC(14,2) NOT NULL DEFAULT 0, line_cost NUMERIC(14,2) NOT NULL DEFAULT 0,
        cost_estimated BOOLEAN NOT NULL DEFAULT FALSE)""",
    """CREATE TABLE IF NOT EXISTS sale_item_lots(
        id BIGSERIAL PRIMARY KEY, sale_item_id BIGINT NOT NULL REFERENCES sale_items(id) ON DELETE CASCADE,
        lot_id BIGINT NOT NULL REFERENCES lots(id), quantity INTEGER NOT NULL CHECK(quantity>0),
        unit_cost NUMERIC(14,2) NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS sale_payments(
        id BIGSERIAL PRIMARY KEY, sale_id BIGINT NOT NULL REFERENCES sales(id) ON DELETE CASCADE,
        payment_date DATE NOT NULL, amount NUMERIC(14,2) NOT NULL CHECK(amount>0),
        method VARCHAR(20) NOT NULL, note TEXT NOT NULL DEFAULT '',
        created_by BIGINT REFERENCES app_users(id), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
    """CREATE TABLE IF NOT EXISTS movements(
        id BIGSERIAL PRIMARY KEY, product_id BIGINT NOT NULL REFERENCES products(id), lot_id BIGINT REFERENCES lots(id),
        user_id BIGINT REFERENCES app_users(id), movement_type VARCHAR(30) NOT NULL,
        quantity_delta INTEGER NOT NULL, quantity_after INTEGER NOT NULL, unit_cost NUMERIC(14,2),
        note TEXT NOT NULL DEFAULT '', sale_id BIGINT REFERENCES sales(id), purchase_id BIGINT REFERENCES purchases(id),
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
    "CREATE INDEX IF NOT EXISTS movements_product_idx ON movements(product_id, created_at DESC)",
    """CREATE TABLE IF NOT EXISTS expenses(
        id BIGSERIAL PRIMARY KEY, expense_date DATE NOT NULL, category VARCHAR(20) NOT NULL,
        description VARCHAR(200) NOT NULL, amount NUMERIC(14,2) NOT NULL CHECK(amount>0),
        paid_by VARCHAR(80) NOT NULL DEFAULT '', payment_method VARCHAR(20) NOT NULL DEFAULT 'bancolombia',
        note TEXT NOT NULL DEFAULT '', imported BOOLEAN NOT NULL DEFAULT FALSE,
        created_by BIGINT REFERENCES app_users(id), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        voided_by BIGINT REFERENCES app_users(id), voided_at TIMESTAMPTZ, void_reason TEXT NOT NULL DEFAULT '')""",
    """CREATE TABLE IF NOT EXISTS partners(
        id BIGSERIAL PRIMARY KEY, name VARCHAR(80) UNIQUE NOT NULL, is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""",
    """CREATE TABLE IF NOT EXISTS capital_movements(
        id BIGSERIAL PRIMARY KEY, partner_id BIGINT NOT NULL REFERENCES partners(id), movement_date DATE NOT NULL,
        kind VARCHAR(12) NOT NULL CHECK(kind IN ('cash','in_kind','withdrawal')),
        amount NUMERIC(14,2) NOT NULL CHECK(amount>0), note TEXT NOT NULL DEFAULT '',
        created_by BIGINT REFERENCES app_users(id), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        voided_at TIMESTAMPTZ, voided_by BIGINT REFERENCES app_users(id))""",
]


def ensure_schema():
    """Crea las tablas que falten. Aditivo e idempotente: nunca borra datos."""
    with psycopg.connect(DATABASE_URL) as c:
        for ddl in SCHEMA:
            c.execute(ddl)


@asynccontextmanager
async def lifespan(app):
    ensure_schema()
    yield


app = FastAPI(title="Endura Human API", version="1.0.0", lifespan=lifespan)


# ------------------------------- AUTENTICACIÓN -------------------------------
def user_from(request: Request):
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Debes iniciar sesión")
    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"])
        uid = int(payload["sub"])
        sv = int(payload.get("sv", 0))
    except (jwt.InvalidTokenError, KeyError, ValueError, TypeError):
        raise HTTPException(401, "Sesión inválida o vencida")
    with connect() as c:
        u = c.execute("SELECT id,username,is_active,role,session_version FROM app_users WHERE id=%s", (uid,)).fetchone()
    if not u or not u["is_active"]:
        raise HTTPException(401, "Usuario inactivo")
    if sv != u["session_version"]:
        raise HTTPException(401, "La sesión fue revocada. Inicia sesión de nuevo.")
    return {"id": u["id"], "username": u["username"], "role": u["role"]}


def require_admin(request: Request):
    u = user_from(request)
    if u["role"] != "admin":
        raise HTTPException(403, "Esta acción requiere permisos de administrador")
    return u


@app.get("/health")
def health():
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3) as c:
            c.execute("SELECT 1")
        return {"status": "ok", "database": "connected"}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "error", "database": "unavailable"})


@app.post("/auth/login")
def login(response: Response, username: str = Form(...), password: str = Form(...)):
    with connect() as c:
        u = c.execute("SELECT id,username,password_hash,is_active,role,session_version FROM app_users WHERE username=%s",
                      (username.strip(),)).fetchone()
    if not u or not u["is_active"] or not hasher.verify(password, u["password_hash"]):
        raise HTTPException(401, "Usuario o contraseña incorrectos")
    now = datetime.now(timezone.utc)
    token = jwt.encode({"sub": str(u["id"]), "sv": u["session_version"], "iat": now, "exp": now + timedelta(hours=HOURS)},
                       SECRET, algorithm="HS256")
    response.set_cookie(COOKIE, token, max_age=HOURS * 3600, httponly=True, secure=COOKIE_SECURE, samesite="lax", path="/")
    return {"message": "Sesión iniciada", "username": u["username"], "role": u["role"]}


@app.get("/auth/me")
def me(request: Request):
    return user_from(request)


@app.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/", secure=COOKIE_SECURE, httponly=True, samesite="lax")
    return {"message": "Sesión cerrada"}


# ------------------------------- USUARIOS -------------------------------
class AdminUserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=10, max_length=256)


class AdminPasswordReset(BaseModel):
    password: str = Field(min_length=10, max_length=256)


class AdminUserStatus(BaseModel):
    is_active: bool


class AdminUserRole(BaseModel):
    role: Literal["admin", "user"]


def _user_out(r):
    return {"id": r["id"], "username": r["username"], "role": r["role"], "is_active": r["is_active"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None}


@app.get("/admin/users")
def admin_list_users(request: Request):
    require_admin(request)
    with connect() as c:
        rows = c.execute("SELECT id,username,role,is_active,created_at FROM app_users ORDER BY created_at DESC,id DESC").fetchall()
    return {"items": [_user_out(r) for r in rows]}


@app.post("/admin/users", status_code=201)
def admin_create_user(d: AdminUserCreate, request: Request):
    require_admin(request)
    try:
        with connect() as c:
            r = c.execute("""INSERT INTO app_users(username,password_hash) VALUES(%s,%s)
                RETURNING id,username,role,is_active,created_at""", (d.username.strip(), hasher.hash(d.password))).fetchone()
    except UniqueViolation:
        raise HTTPException(409, "Ese nombre de usuario ya existe")
    return _user_out(r)


@app.patch("/admin/users/{user_id}/password")
def admin_reset_password(user_id: int, d: AdminPasswordReset, request: Request):
    require_admin(request)
    with connect() as c:
        r = c.execute("UPDATE app_users SET password_hash=%s,session_version=session_version+1 WHERE id=%s RETURNING username",
                      (hasher.hash(d.password), user_id)).fetchone()
    if not r:
        raise HTTPException(404, "Usuario no encontrado")
    return {"message": f"Contraseña restablecida para {r['username']}. Sus sesiones anteriores se cerraron."}


def _guard_last_admin(c, target):
    if target["role"] == "admin" and target["is_active"]:
        n = c.execute("SELECT COUNT(*) n FROM app_users WHERE role='admin' AND is_active").fetchone()["n"]
        if n <= 1:
            raise HTTPException(400, "Debe quedar al menos un administrador activo")


@app.patch("/admin/users/{user_id}/status")
def admin_set_user_status(user_id: int, d: AdminUserStatus, request: Request):
    actor = require_admin(request)
    if user_id == actor["id"] and not d.is_active:
        raise HTTPException(400, "No puedes desactivar tu propia cuenta")
    with connect() as c:
        with c.transaction():
            c.execute("LOCK TABLE app_users IN EXCLUSIVE MODE")
            t = c.execute("SELECT id,username,role,is_active FROM app_users WHERE id=%s", (user_id,)).fetchone()
            if not t:
                raise HTTPException(404, "Usuario no encontrado")
            if not d.is_active:
                _guard_last_admin(c, t)
            r = c.execute("UPDATE app_users SET is_active=%s,session_version=session_version+1 WHERE id=%s RETURNING username,is_active",
                          (d.is_active, user_id)).fetchone()
    return {"message": ("Usuario activado" if d.is_active else "Usuario desactivado") + f": {r['username']}", **r}


@app.patch("/admin/users/{user_id}/role")
def admin_set_user_role(user_id: int, d: AdminUserRole, request: Request):
    actor = require_admin(request)
    if user_id == actor["id"] and d.role != "admin":
        raise HTTPException(400, "No puedes quitarte tus propios permisos de administrador")
    with connect() as c:
        with c.transaction():
            c.execute("LOCK TABLE app_users IN EXCLUSIVE MODE")
            t = c.execute("SELECT id,username,role,is_active FROM app_users WHERE id=%s", (user_id,)).fetchone()
            if not t:
                raise HTTPException(404, "Usuario no encontrado")
            if t["role"] == d.role:
                return {"message": f"{t['username']} ya tiene ese permiso", "username": t["username"], "role": t["role"]}
            _guard_last_admin(c, t)
            r = c.execute("UPDATE app_users SET role=%s WHERE id=%s RETURNING username,role", (d.role, user_id)).fetchone()
    return {"message": f"{r['username']} ahora es {'Administrador' if r['role'] == 'admin' else 'Usuario'}", **r}


# ------------------------------- DATOS DEL NEGOCIO -------------------------------
class BusinessSettings(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    owner: str = Field(default="", max_length=160)
    tax_id: str = Field(default="", max_length=60)
    address: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=60)
    email: str = Field(default="", max_length=160)
    invoice_prefix: str = Field(default="EH", max_length=10)
    invoice_note: str = Field(default="", max_length=1000)


SETTINGS_COLS = "name,owner,tax_id,address,phone,email,invoice_prefix,invoice_note"


@app.get("/settings")
def get_settings(request: Request):
    user_from(request)
    with connect() as c:
        return c.execute(f"SELECT {SETTINGS_COLS} FROM business_settings WHERE id=1").fetchone()


@app.put("/settings")
def save_settings(d: BusinessSettings, request: Request):
    require_admin(request)
    with connect() as c:
        return c.execute(f"""UPDATE business_settings SET name=%s,owner=%s,tax_id=%s,address=%s,phone=%s,email=%s,
            invoice_prefix=%s,invoice_note=%s,updated_at=NOW() WHERE id=1 RETURNING {SETTINGS_COLS}""",
                         (d.name.strip(), d.owner.strip(), d.tax_id.strip(), d.address.strip(), d.phone.strip(),
                          d.email.strip(), d.invoice_prefix.strip(), d.invoice_note.strip())).fetchone()


@app.get("/catalogs")
def catalogs(request: Request):
    user_from(request)
    return {"product_categories": PRODUCT_CATEGORIES, "payment_methods": PAYMENT_METHODS,
            "giveaway_reasons": GIVEAWAY_REASONS, "expense_categories": EXPENSE_CATEGORIES,
            "capital_kinds": CAPITAL_KINDS, "movement_labels": MOVEMENT_LABELS, "expiry_warning_days": EXPIRY_WARNING_DAYS}


# ------------------------------- MARCAS -------------------------------
class BrandIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@app.get("/brands")
def list_brands(request: Request):
    user_from(request)
    with connect() as c:
        rows = c.execute("""SELECT b.id,b.name,COUNT(p.id) FILTER (WHERE p.is_active) products,
            COALESCE(SUM(p.quantity) FILTER (WHERE p.is_active),0) units
            FROM brands b LEFT JOIN products p ON p.brand_id=b.id WHERE b.is_active GROUP BY b.id ORDER BY b.name""").fetchall()
    return {"items": rows}


@app.post("/brands", status_code=201)
def create_brand(d: BrandIn, request: Request):
    require_admin(request)
    name = d.name.strip()
    if not name:
        raise HTTPException(422, "El nombre de la marca es obligatorio")
    with connect() as c:
        old = c.execute("SELECT id,is_active FROM brands WHERE lower(name)=lower(%s)", (name,)).fetchone()
        if old and old["is_active"]:
            raise HTTPException(409, "Ya existe una marca con ese nombre")
        if old:
            return c.execute("UPDATE brands SET is_active=TRUE,name=%s WHERE id=%s RETURNING id,name", (name, old["id"])).fetchone()
        return c.execute("INSERT INTO brands(name) VALUES(%s) RETURNING id,name", (name,)).fetchone()


@app.patch("/brands/{brand_id}")
def rename_brand(brand_id: int, d: BrandIn, request: Request):
    require_admin(request)
    try:
        with connect() as c:
            r = c.execute("UPDATE brands SET name=%s WHERE id=%s AND is_active RETURNING id,name", (d.name.strip(), brand_id)).fetchone()
    except UniqueViolation:
        raise HTTPException(409, "Ya existe una marca con ese nombre")
    if not r:
        raise HTTPException(404, "Marca no encontrada")
    return r


@app.delete("/brands/{brand_id}")
def delete_brand(brand_id: int, request: Request):
    require_admin(request)
    with connect() as c:
        n = c.execute("SELECT COUNT(*) n FROM products WHERE brand_id=%s AND is_active", (brand_id,)).fetchone()["n"]
        if n:
            raise HTTPException(409, f"No se puede eliminar: {n} producto(s) son de esta marca.")
        if not c.execute("UPDATE brands SET is_active=FALSE WHERE id=%s AND is_active RETURNING id", (brand_id,)).fetchone():
            raise HTTPException(404, "Marca no encontrada")
    return {"message": "Marca eliminada"}


# ------------------------------- INVENTARIO -------------------------------
def refresh_product(c, pid: int):
    """La existencia del producto es siempre la suma de sus lotes; el coste, su promedio ponderado."""
    r = c.execute("SELECT COALESCE(SUM(quantity),0) q, COALESCE(SUM(quantity*unit_cost),0) v FROM lots WHERE product_id=%s AND quantity>0",
                  (pid,)).fetchone()
    if r["q"] > 0:
        c.execute("UPDATE products SET quantity=%s,unit_cost=%s,updated_at=NOW() WHERE id=%s", (r["q"], money(r["v"] / r["q"]), pid))
    else:
        c.execute("UPDATE products SET quantity=0,updated_at=NOW() WHERE id=%s", (pid,))
    return r["q"]


def add_lot(c, pid, qty, unit_cost, expiry, source, user_id, mtype, note, purchase_id=None, sale_id=None):
    lot = c.execute("""INSERT INTO lots(product_id,expiry_date,quantity,initial_quantity,unit_cost,source,purchase_id,note)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""", (pid, expiry, qty, qty, money(unit_cost), source, purchase_id, note)).fetchone()["id"]
    after = refresh_product(c, pid)
    c.execute("""INSERT INTO movements(product_id,lot_id,user_id,movement_type,quantity_delta,quantity_after,unit_cost,note,sale_id,purchase_id)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (pid, lot, user_id, mtype, qty, after, money(unit_cost), note, sale_id, purchase_id))
    return lot


def take_stock(c, pid, qty, user_id, mtype, note, sale_id=None, lot_id=None):
    """Descuenta unidades por FEFO (primero lo que vence antes). Devuelve [(lot_id, cantidad, coste)]."""
    p = c.execute("SELECT id,name,quantity FROM products WHERE id=%s FOR UPDATE", (pid,)).fetchone()
    if p["quantity"] < qty:
        raise HTTPException(409, f"Stock insuficiente de {p['name']}: hay {p['quantity']}, se piden {qty}")
    if lot_id:
        lots = c.execute("SELECT id,quantity,unit_cost FROM lots WHERE id=%s AND product_id=%s AND quantity>0 FOR UPDATE",
                         (lot_id, pid)).fetchall()
    else:
        lots = c.execute("""SELECT id,quantity,unit_cost FROM lots WHERE product_id=%s AND quantity>0
            ORDER BY expiry_date NULLS LAST,id FOR UPDATE""", (pid,)).fetchall()
    remaining, taken = qty, []
    for lot in lots:
        if remaining == 0:
            break
        t = min(remaining, lot["quantity"])
        c.execute("UPDATE lots SET quantity=quantity-%s WHERE id=%s", (t, lot["id"]))
        taken.append((lot["id"], t, lot["unit_cost"]))
        remaining -= t
    if remaining:
        raise HTTPException(409, f"El lote elegido de {p['name']} no tiene suficientes unidades")
    after = p["quantity"]
    for lid, t, cost in taken:
        after -= t
        c.execute("""INSERT INTO movements(product_id,lot_id,user_id,movement_type,quantity_delta,quantity_after,unit_cost,note,sale_id)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (pid, lid, user_id, mtype, -t, after, cost, note, sale_id))
    refresh_product(c, pid)
    return taken


class ProductIn(BaseModel):
    sku: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=200)
    brand_id: int
    category: str = Field(default="other")
    units_per_box: int = Field(default=1, ge=1, le=10000)
    min_stock: int = Field(default=0, ge=0, le=100000)
    unit_cost: Decimal = Field(default=Decimal(0), ge=0, le=999999999)
    sale_price: Optional[Decimal] = Field(default=None, ge=0, le=999999999)
    notes: str = Field(default="", max_length=2000)
    initial_quantity: int = Field(default=0, ge=0, le=1000000)
    initial_expiry: Optional[date] = None


class StockEntry(BaseModel):
    quantity: int = Field(gt=0, le=1000000)
    unit_cost: Decimal = Field(ge=0, le=999999999)
    expiry_date: Optional[date] = None
    note: str = Field(default="", max_length=500)


class StockExit(BaseModel):
    quantity: int = Field(gt=0, le=1000000)
    lot_id: Optional[int] = None
    note: str = Field(min_length=3, max_length=500)


class LotUpdate(BaseModel):
    expiry_date: Optional[date] = None


PRODUCT_SELECT = """SELECT p.id,p.sku,p.name,p.brand_id,b.name brand,p.category,p.units_per_box,p.quantity,p.min_stock,
    p.unit_cost,p.sale_price,p.notes,p.updated_at,
    (SELECT MIN(l.expiry_date) FROM lots l WHERE l.product_id=p.id AND l.quantity>0) next_expiry,
    (SELECT COALESCE(SUM(l.quantity),0) FROM lots l WHERE l.product_id=p.id AND l.quantity>0
        AND l.expiry_date>=%(today)s AND l.expiry_date<=%(warn)s) expiring_qty,
    (SELECT COALESCE(SUM(l.quantity),0) FROM lots l WHERE l.product_id=p.id AND l.quantity>0 AND l.expiry_date<%(today)s) expired_qty
    FROM products p JOIN brands b ON b.id=p.brand_id"""


def _product_out(r):
    r = dict(r)
    r["category_label"] = PRODUCT_CATEGORIES.get(r["category"], r["category"])
    price, cost = r["sale_price"], r["unit_cost"]
    r["margin_pct"] = (((price - cost) / price) * 100).quantize(Decimal("0.1")) if price else None
    return r


def _dates():
    t = local_today()
    return {"today": t, "warn": t + timedelta(days=EXPIRY_WARNING_DAYS)}


def _check_category(cat):
    if cat not in PRODUCT_CATEGORIES:
        raise HTTPException(422, "Categoría no válida")


@app.get("/inventory")
def inventory(request: Request, search: str = Query("", max_length=120), brand_id: Optional[int] = None,
              category: str = Query("all", max_length=30),
              status: str = Query("all", pattern="^(all|ok|low|out|expiring|expired)$")):
    user_from(request)
    params = {**_dates(), "s": search.strip(), "term": f"%{search.strip()}%", "brand": brand_id, "cat": category}
    with connect() as c:
        rows = c.execute(PRODUCT_SELECT + """ WHERE p.is_active
            AND (%(s)s='' OR p.sku ILIKE %(term)s OR p.name ILIKE %(term)s OR b.name ILIKE %(term)s)
            AND (%(brand)s::bigint IS NULL OR p.brand_id=%(brand)s) AND (%(cat)s='all' OR p.category=%(cat)s)
            ORDER BY b.name,p.sku""", params).fetchall()
        s = c.execute("""SELECT COUNT(*) refs, COALESCE(SUM(quantity),0) units, COALESCE(SUM(quantity*unit_cost),0) value_cost,
            COALESCE(SUM(quantity*COALESCE(sale_price,0)),0) value_price,
            COUNT(*) FILTER (WHERE quantity=0) out_of_stock, COUNT(*) FILTER (WHERE quantity>0 AND quantity<=min_stock) low
            FROM products WHERE is_active""").fetchone()
        e = c.execute("""SELECT COALESCE(SUM(l.quantity) FILTER (WHERE l.expiry_date>=%(today)s AND l.expiry_date<=%(warn)s),0) expiring_units,
            COALESCE(SUM(l.quantity*l.unit_cost) FILTER (WHERE l.expiry_date>=%(today)s AND l.expiry_date<=%(warn)s),0) expiring_value,
            COALESCE(SUM(l.quantity) FILTER (WHERE l.expiry_date<%(today)s),0) expired_units
            FROM lots l JOIN products p ON p.id=l.product_id WHERE p.is_active AND l.quantity>0""", params).fetchone()
    items = [_product_out(r) for r in rows]
    if status == "ok":
        items = [i for i in items if i["quantity"] > i["min_stock"]]
    elif status == "low":
        items = [i for i in items if 0 < i["quantity"] <= i["min_stock"]]
    elif status == "out":
        items = [i for i in items if i["quantity"] == 0]
    elif status == "expiring":
        items = [i for i in items if i["expiring_qty"] > 0]
    elif status == "expired":
        items = [i for i in items if i["expired_qty"] > 0]
    return {"items": items, "summary": {**s, **e}, "expiry_warning_days": EXPIRY_WARNING_DAYS}


@app.get("/inventory/{pid}")
def product_detail(pid: int, request: Request):
    user_from(request)
    with connect() as c:
        r = c.execute(PRODUCT_SELECT + " WHERE p.id=%(pid)s AND p.is_active", {**_dates(), "pid": pid}).fetchone()
        if not r:
            raise HTTPException(404, "Producto no encontrado")
        lots = c.execute("""SELECT l.id,l.expiry_date,l.quantity,l.initial_quantity,l.unit_cost,l.source,l.purchase_id,l.note,l.created_at
            FROM lots l WHERE l.product_id=%s AND l.quantity>0 ORDER BY l.expiry_date NULLS LAST,l.id""", (pid,)).fetchall()
        sold = c.execute("""SELECT COALESCE(SUM(si.quantity),0) q FROM sale_items si JOIN sales s ON s.id=si.sale_id
            WHERE si.product_id=%s AND s.kind='sale' AND s.status='completed' AND s.sale_date>=%s""",
                         (pid, local_today() - timedelta(days=90))).fetchone()["q"]
    return {**_product_out(r), "lots": lots, "sold_90d": sold}


@app.post("/inventory", status_code=201)
def create_product(d: ProductIn, request: Request):
    u = user_from(request)
    _check_category(d.category)
    try:
        with connect() as c:
            if not c.execute("SELECT 1 FROM brands WHERE id=%s AND is_active", (d.brand_id,)).fetchone():
                raise HTTPException(422, "Elige una marca válida")
            pid = c.execute("""INSERT INTO products(sku,name,brand_id,category,units_per_box,min_stock,unit_cost,sale_price,notes)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                            (d.sku.strip().upper(), d.name.strip(), d.brand_id, d.category, d.units_per_box, d.min_stock,
                             money(d.unit_cost), d.sale_price, d.notes.strip())).fetchone()["id"]
            if d.initial_quantity:
                add_lot(c, pid, d.initial_quantity, d.unit_cost, d.initial_expiry, "initial", u["id"], "initial", "Existencia inicial")
    except UniqueViolation:
        raise HTTPException(409, "Ya existe un producto con esa referencia")
    return product_detail(pid, request)


@app.patch("/inventory/{pid}")
def update_product(pid: int, d: ProductIn, request: Request):
    user_from(request)
    _check_category(d.category)
    try:
        with connect() as c:
            if not c.execute("SELECT 1 FROM brands WHERE id=%s AND is_active", (d.brand_id,)).fetchone():
                raise HTTPException(422, "Elige una marca válida")
            r = c.execute("""UPDATE products SET sku=%s,name=%s,brand_id=%s,category=%s,units_per_box=%s,min_stock=%s,
                sale_price=%s,notes=%s,updated_at=NOW() WHERE id=%s AND is_active RETURNING id""",
                          (d.sku.strip().upper(), d.name.strip(), d.brand_id, d.category, d.units_per_box, d.min_stock,
                           d.sale_price, d.notes.strip(), pid)).fetchone()
            if not r:
                raise HTTPException(404, "Producto no encontrado")
            # El coste solo se edita a mano mientras no hay existencias; con stock lo definen los lotes.
            c.execute("UPDATE products SET unit_cost=%s WHERE id=%s AND quantity=0", (money(d.unit_cost), pid))
    except UniqueViolation:
        raise HTTPException(409, "Ya existe un producto con esa referencia")
    return product_detail(pid, request)


@app.delete("/inventory/{pid}")
def deactivate_product(pid: int, request: Request):
    require_admin(request)
    with connect() as c:
        p = c.execute("SELECT quantity FROM products WHERE id=%s AND is_active FOR UPDATE", (pid,)).fetchone()
        if not p:
            raise HTTPException(404, "Producto no encontrado")
        if p["quantity"] > 0:
            raise HTTPException(409, "Solo se puede retirar del catálogo un producto sin existencias")
        c.execute("UPDATE products SET is_active=FALSE,updated_at=NOW() WHERE id=%s", (pid,))
    return {"message": "Producto retirado del catálogo. Su historial se conserva."}


@app.post("/inventory/{pid}/entry")
def stock_entry(pid: int, d: StockEntry, request: Request):
    u = user_from(request)
    with connect() as c:
        if not c.execute("SELECT 1 FROM products WHERE id=%s AND is_active FOR UPDATE", (pid,)).fetchone():
            raise HTTPException(404, "Producto no encontrado")
        add_lot(c, pid, d.quantity, d.unit_cost, d.expiry_date, "manual", u["id"], "manual_in", d.note.strip() or "Entrada manual")
    return product_detail(pid, request)


@app.post("/inventory/{pid}/exit")
def stock_exit(pid: int, d: StockExit, request: Request):
    u = user_from(request)
    with connect() as c:
        if not c.execute("SELECT 1 FROM products WHERE id=%s AND is_active", (pid,)).fetchone():
            raise HTTPException(404, "Producto no encontrado")
        take_stock(c, pid, d.quantity, u["id"], "manual_out", d.note.strip(), lot_id=d.lot_id)
    return product_detail(pid, request)


@app.patch("/lots/{lot_id}")
def update_lot(lot_id: int, d: LotUpdate, request: Request):
    user_from(request)
    with connect() as c:
        r = c.execute("UPDATE lots SET expiry_date=%s WHERE id=%s RETURNING product_id", (d.expiry_date, lot_id)).fetchone()
    if not r:
        raise HTTPException(404, "Lote no encontrado")
    return product_detail(r["product_id"], request)


@app.get("/expiring")
def expiring(request: Request, days: int = Query(EXPIRY_WARNING_DAYS, ge=1, le=730)):
    user_from(request)
    t = local_today()
    with connect() as c:
        rows = c.execute("""SELECT l.id lot_id,l.expiry_date,l.quantity,l.unit_cost,p.id product_id,p.sku,p.name,b.name brand,p.sale_price
            FROM lots l JOIN products p ON p.id=l.product_id JOIN brands b ON b.id=p.brand_id
            WHERE p.is_active AND l.quantity>0 AND l.expiry_date IS NOT NULL AND l.expiry_date<=%s
            ORDER BY l.expiry_date,p.name""", (t + timedelta(days=days),)).fetchall()
    for r in rows:
        r["expired"] = r["expiry_date"] < t
        r["days_left"] = (r["expiry_date"] - t).days
    return {"items": rows}


@app.get("/movements")
def list_movements(request: Request, product_id: Optional[int] = None, search: str = Query("", max_length=120),
                   movement_type: str = Query("all", max_length=30)):
    user_from(request)
    term = f"%{search.strip()}%"
    with connect() as c:
        rows = c.execute("""SELECT m.id,m.movement_type,m.quantity_delta,m.quantity_after,m.unit_cost,m.note,m.created_at,
            u.username,p.id product_id,p.sku,p.name product_name,l.expiry_date,m.sale_id,s.kind sale_kind,s.number sale_number,m.purchase_id
            FROM movements m JOIN products p ON p.id=m.product_id LEFT JOIN app_users u ON u.id=m.user_id
            LEFT JOIN lots l ON l.id=m.lot_id LEFT JOIN sales s ON s.id=m.sale_id
            WHERE (%(pid)s::bigint IS NULL OR m.product_id=%(pid)s) AND (%(type)s='all' OR m.movement_type=%(type)s)
              AND (%(s)s='' OR p.name ILIKE %(term)s OR p.sku ILIKE %(term)s OR m.note ILIKE %(term)s OR COALESCE(u.username,'') ILIKE %(term)s)
            ORDER BY m.created_at DESC,m.id DESC LIMIT 1000""",
                         {"pid": product_id, "type": movement_type, "s": search.strip(), "term": term}).fetchall()
    for r in rows:
        r["type_label"] = MOVEMENT_LABELS.get(r["movement_type"], r["movement_type"])
    return {"items": rows}


# ------------------------------- VENTAS Y PATROCINIOS -------------------------------
PaymentMethod = Literal["bancolombia", "nequi", "cash", "card", "other"]


class SaleItemIn(BaseModel):
    product_id: int
    quantity: int = Field(gt=0, le=100000)
    unit_price: Decimal = Field(ge=0, le=999999999)


class PaymentIn(BaseModel):
    amount: Decimal = Field(gt=0, le=999999999)
    method: PaymentMethod = "bancolombia"
    payment_date: Optional[date] = None
    note: str = Field(default="", max_length=300)


class SaleCreate(BaseModel):
    kind: Literal["sale", "giveaway"] = "sale"
    sale_date: Optional[date] = None
    customer_name: str = Field(default="", max_length=160)
    customer_contact: str = Field(default="", max_length=160)
    customer_doc: str = Field(default="", max_length=40)
    customer_address: str = Field(default="", max_length=200)
    customer_city: str = Field(default="", max_length=80)
    referrer: str = Field(default="", max_length=80)
    reason: Optional[Literal["ambassador", "event", "alliance", "internal", "damage", "other"]] = None
    items: List[SaleItemIn] = Field(min_length=1, max_length=60)
    discount_percent: Decimal = Field(default=Decimal(0), ge=0, le=100)
    discount: Optional[Decimal] = Field(default=None, ge=0, le=999999999)
    delivery_fee: Decimal = Field(default=Decimal(0), ge=0, le=999999999)
    payment: Optional[PaymentIn] = None
    delivery_status: Literal["delivered", "pending"] = "delivered"
    note: str = Field(default="", max_length=1000)
    client_ref: Optional[str] = Field(default=None, min_length=8, max_length=64, pattern=r"^[A-Za-z0-9-]+$")


class SaleVoid(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class DeliveryUpdate(BaseModel):
    delivery_status: Literal["delivered", "pending"]


SALE_SELECT = """SELECT s.*, cu.username created_by_name, vu.username voided_by_name,
    COALESCE((SELECT SUM(amount) FROM sale_payments sp WHERE sp.sale_id=s.id),0) paid
    FROM sales s LEFT JOIN app_users cu ON cu.id=s.created_by LEFT JOIN app_users vu ON vu.id=s.voided_by"""


def _sale_out(r, items=None, payments=None):
    d = dict(r)
    d.pop("client_ref", None)
    net = d["subtotal"] - d["discount"]
    d["net_revenue"] = net if d["kind"] == "sale" else Decimal(0)
    d["margin"] = net - d["cost_total"] if d["kind"] == "sale" else -d["cost_total"]
    d["balance"] = d["total"] - d["paid"]
    if d["kind"] != "sale" or d["status"] != "completed":
        d["payment_status"] = "n/a"
    elif d["balance"] <= 0:
        d["payment_status"] = "paid"
    elif d["paid"] > 0:
        d["payment_status"] = "partial"
    else:
        d["payment_status"] = "due"
    d["reason_label"] = GIVEAWAY_REASONS.get(d["reason"] or "", "")
    if items is not None:
        d["items"] = items
    if payments is not None:
        d["payments"] = payments
    return d


def _items_for(c, ids):
    if not ids:
        return {}
    rows = c.execute("""SELECT si.id,si.sale_id,si.product_id,p.sku,p.name,b.name brand,si.quantity,si.unit_price,si.line_total,
        si.unit_cost,si.line_cost,si.cost_estimated FROM sale_items si JOIN products p ON p.id=si.product_id
        JOIN brands b ON b.id=p.brand_id WHERE si.sale_id=ANY(%s) ORDER BY si.id""", (list(ids),)).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r.pop("sale_id"), []).append(r)
    return out


def _payments_for(c, sale_id):
    rows = c.execute("""SELECT sp.id,sp.payment_date,sp.amount,sp.method,sp.note,u.username created_by,sp.created_at
        FROM sale_payments sp LEFT JOIN app_users u ON u.id=sp.created_by WHERE sp.sale_id=%s ORDER BY sp.payment_date,sp.id""",
                     (sale_id,)).fetchall()
    for r in rows:
        r["method_label"] = PAYMENT_METHODS.get(r["method"], r["method"])
    return rows


def _load_sale(c, sid):
    r = c.execute(SALE_SELECT + " WHERE s.id=%s", (sid,)).fetchone()
    if not r:
        raise HTTPException(404, "Registro no encontrado")
    return _sale_out(r, _items_for(c, [sid]).get(sid, []), _payments_for(c, sid))


def _month_range(month: str):
    try:
        start = date.fromisoformat(month + "-01")
    except ValueError:
        raise HTTPException(422, "El mes debe tener formato AAAA-MM")
    return start, _shift_month(start, 1)


def _shift_month(d: date, n: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 + n, 12)
    return date(y, m + 1, 1)


@app.get("/sales/options")
def sales_options(request: Request):
    user_from(request)
    with connect() as c:
        products = c.execute("""SELECT p.id,p.sku,p.name,b.name brand,p.quantity,p.sale_price,p.unit_cost,
            (SELECT MIN(l.expiry_date) FROM lots l WHERE l.product_id=p.id AND l.quantity>0) next_expiry
            FROM products p JOIN brands b ON b.id=p.brand_id WHERE p.is_active ORDER BY b.name,p.name""").fetchall()
        customers = c.execute("""SELECT DISTINCT ON (lower(customer_name)) customer_name name,customer_contact contact,customer_doc doc,
            customer_address address,customer_city city FROM sales WHERE customer_name<>'' AND kind='sale'
            ORDER BY lower(customer_name),sale_date DESC,id DESC""").fetchall()
        recipients = c.execute("""SELECT DISTINCT customer_name FROM sales WHERE customer_name<>'' AND kind='giveaway' ORDER BY 1""").fetchall()
        referrers = c.execute("""SELECT referrer,COUNT(*) n FROM sales WHERE referrer<>'' GROUP BY referrer ORDER BY n DESC LIMIT 30""").fetchall()
    return {"products": products, "customers": customers, "recipients": [r["customer_name"] for r in recipients],
            "referrers": [r["referrer"] for r in referrers], "payment_methods": PAYMENT_METHODS, "giveaway_reasons": GIVEAWAY_REASONS}


@app.get("/sales")
def list_sales(request: Request, kind: str = Query("sale", pattern="^(sale|giveaway)$"),
               month: str = Query("", pattern=r"^(\d{4}-\d{2})?$"), status: str = Query("all", pattern="^(all|completed|voided)$"),
               payment: str = Query("all", pattern="^(all|paid|due)$"), search: str = Query("", max_length=120)):
    user_from(request)
    start, end = _month_range(month) if month else (None, None)
    term = f"%{search.strip()}%"
    params = {"kind": kind, "start": start, "end": end, "status": status, "s": search.strip(), "term": term}
    where = """WHERE s.kind=%(kind)s AND (%(start)s::date IS NULL OR (s.sale_date>=%(start)s AND s.sale_date<%(end)s))
        AND (%(status)s='all' OR s.status=%(status)s)
        AND (%(s)s='' OR s.customer_name ILIKE %(term)s OR s.customer_contact ILIKE %(term)s OR s.referrer ILIKE %(term)s
             OR s.note ILIKE %(term)s OR s.number::text=%(s)s
             OR EXISTS(SELECT 1 FROM sale_items si JOIN products p ON p.id=si.product_id
                       WHERE si.sale_id=s.id AND (p.name ILIKE %(term)s OR p.sku ILIKE %(term)s)))"""
    with connect() as c:
        rows = c.execute(f"{SALE_SELECT} {where} ORDER BY s.sale_date DESC,s.number DESC LIMIT 600", params).fetchall()
        items = _items_for(c, [r["id"] for r in rows])
        sales = [_sale_out(r, items.get(r["id"], [])) for r in rows]
        if payment == "paid":
            sales = [x for x in sales if x["payment_status"] in ("paid", "n/a")]
        elif payment == "due":
            sales = [x for x in sales if x["payment_status"] in ("due", "partial")]
    valid = [x for x in sales if x["status"] == "completed"]
    summary = {
        "count": len(valid),
        "units": sum((i["quantity"] for x in valid for i in x["items"]), 0),
        "subtotal": sum((x["subtotal"] for x in valid), Decimal(0)),
        "discounts": sum((x["discount"] for x in valid), Decimal(0)),
        "net_revenue": sum((x["net_revenue"] for x in valid), Decimal(0)),
        "delivery_fees": sum((x["delivery_fee"] for x in valid), Decimal(0)),
        "cost": sum((x["cost_total"] for x in valid), Decimal(0)),
        "balance": sum((x["balance"] for x in valid if x["payment_status"] in ("due", "partial")), Decimal(0)),
    }
    summary["margin"] = summary["net_revenue"] - summary["cost"]
    return {"items": sales, "summary": summary}


@app.get("/sales/{sid}")
def sale_detail(sid: int, request: Request):
    user_from(request)
    with connect() as c:
        return _load_sale(c, sid)


@app.post("/sales", status_code=201)
def create_sale(d: SaleCreate, request: Request, response: Response):
    """Registra una venta o un patrocinio y descuenta el stock (FEFO) en una sola transacción."""
    u = user_from(request)
    ids = [i.product_id for i in d.items]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "No repitas un producto; ajusta la cantidad en una sola línea")
    sale_date = d.sale_date or local_today()
    if sale_date > local_today():
        raise HTTPException(422, "La fecha no puede estar en el futuro")
    giveaway = d.kind == "giveaway"
    if giveaway and not d.reason:
        raise HTTPException(422, "Indica el motivo del patrocinio o salida")
    if giveaway and not d.customer_name.strip():
        raise HTTPException(422, "Indica a quién se entrega")
    with connect() as c:
        if d.client_ref:
            prev = c.execute("SELECT id FROM sales WHERE client_ref=%s", (d.client_ref,)).fetchone()
            if prev:
                response.status_code = 200
                return {**_load_sale(c, prev["id"]), "duplicate": True}
        prods = {}
        for pid in sorted(ids):
            p = c.execute("SELECT id,name,quantity,is_active FROM products WHERE id=%s FOR UPDATE", (pid,)).fetchone()
            if not p or not p["is_active"]:
                raise HTTPException(404, f"El producto {pid} no existe o fue retirado")
            prods[pid] = p
        for it in d.items:
            p = prods[it.product_id]
            if p["quantity"] < it.quantity:
                raise HTTPException(409, f"Stock insuficiente de {p['name']}: hay {p['quantity']}, se piden {it.quantity}")
        subtotal = sum((money(it.quantity * it.unit_price) for it in d.items), Decimal(0))
        if giveaway:
            discount, pct, delivery = subtotal, Decimal(100), Decimal(0)
        else:
            pct = d.discount_percent
            discount = money(d.discount) if d.discount is not None else money(subtotal * pct / 100)
            if d.discount is not None and subtotal:
                pct = (discount / subtotal * 100).quantize(Decimal("0.001"))
            delivery = money(d.delivery_fee)
        if discount > subtotal:
            raise HTTPException(422, "El descuento no puede ser mayor que el valor de los productos")
        total = subtotal - discount + delivery
        if d.payment and giveaway:
            raise HTTPException(422, "Un patrocinio no lleva pago")
        if d.payment and money(d.payment.amount) > total:
            raise HTTPException(422, "El pago no puede superar el total a cobrar")
        if d.payment and d.payment.payment_date and d.payment.payment_date > local_today():
            raise HTTPException(422, "La fecha del pago no puede estar en el futuro")
        c.execute("LOCK TABLE sales IN SHARE ROW EXCLUSIVE MODE")
        number = c.execute("SELECT COALESCE(MAX(number),0)+1 n FROM sales WHERE kind=%s", (d.kind,)).fetchone()["n"]
        try:
            sid = c.execute("""INSERT INTO sales(kind,number,sale_date,customer_name,customer_contact,customer_doc,customer_address,
                customer_city,referrer,reason,subtotal,discount_percent,discount,delivery_fee,total,delivery_status,note,client_ref,created_by)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                            (d.kind, number, sale_date, d.customer_name.strip(), d.customer_contact.strip(), d.customer_doc.strip(),
                             d.customer_address.strip(), d.customer_city.strip(), d.referrer.strip(), d.reason if giveaway else None,
                             subtotal, pct, discount, delivery, total, d.delivery_status, d.note.strip(), d.client_ref, u["id"])).fetchone()["id"]
        except UniqueViolation:
            raise HTTPException(409, "Este registro ya se estaba guardando. Actualiza la lista para verlo.")
        label = f"Venta #{number}" if not giveaway else f"Patrocinio #{number} · {GIVEAWAY_REASONS[d.reason]}"
        cost_total = Decimal(0)
        for it in d.items:
            taken = take_stock(c, it.product_id, it.quantity, u["id"], d.kind, f"{label} · {d.customer_name.strip()}".strip(" ·"), sale_id=sid)
            line_cost = money(sum((q * cost for _, q, cost in taken), Decimal(0)))
            cost_total += line_cost
            item_id = c.execute("""INSERT INTO sale_items(sale_id,product_id,quantity,unit_price,line_total,unit_cost,line_cost)
                VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                                (sid, it.product_id, it.quantity, money(it.unit_price), money(it.quantity * it.unit_price),
                                 money(line_cost / it.quantity), line_cost)).fetchone()["id"]
            for lid, q, cost in taken:
                c.execute("INSERT INTO sale_item_lots(sale_item_id,lot_id,quantity,unit_cost) VALUES(%s,%s,%s,%s)", (item_id, lid, q, cost))
        c.execute("UPDATE sales SET cost_total=%s WHERE id=%s", (cost_total, sid))
        if d.payment:
            c.execute("INSERT INTO sale_payments(sale_id,payment_date,amount,method,note,created_by) VALUES(%s,%s,%s,%s,%s,%s)",
                      (sid, d.payment.payment_date or sale_date, money(d.payment.amount), d.payment.method, d.payment.note.strip(), u["id"]))
        return _load_sale(c, sid)


@app.post("/sales/{sid}/payments", status_code=201)
def add_payment(sid: int, d: PaymentIn, request: Request):
    u = user_from(request)
    with connect() as c:
        s = c.execute("SELECT id,kind,status,total FROM sales WHERE id=%s FOR UPDATE", (sid,)).fetchone()
        if not s:
            raise HTTPException(404, "Venta no encontrada")
        if s["kind"] != "sale" or s["status"] != "completed":
            raise HTTPException(409, "Solo se registran pagos en ventas vigentes")
        paid = c.execute("SELECT COALESCE(SUM(amount),0) p FROM sale_payments WHERE sale_id=%s", (sid,)).fetchone()["p"]
        if money(d.amount) > s["total"] - paid:
            raise HTTPException(422, f"El pago supera el saldo pendiente ({money(s['total'] - paid)})")
        pdate = d.payment_date or local_today()
        if pdate > local_today():
            raise HTTPException(422, "La fecha del pago no puede estar en el futuro")
        c.execute("INSERT INTO sale_payments(sale_id,payment_date,amount,method,note,created_by) VALUES(%s,%s,%s,%s,%s,%s)",
                  (sid, pdate, money(d.amount), d.method, d.note.strip(), u["id"]))
        return _load_sale(c, sid)


@app.delete("/sales/{sid}/payments/{payment_id}")
def delete_payment(sid: int, payment_id: int, request: Request):
    require_admin(request)
    with connect() as c:
        if not c.execute("DELETE FROM sale_payments WHERE id=%s AND sale_id=%s RETURNING id", (payment_id, sid)).fetchone():
            raise HTTPException(404, "Pago no encontrado")
        return _load_sale(c, sid)


@app.patch("/sales/{sid}/delivery")
def update_delivery(sid: int, d: DeliveryUpdate, request: Request):
    user_from(request)
    with connect() as c:
        if not c.execute("UPDATE sales SET delivery_status=%s WHERE id=%s RETURNING id", (d.delivery_status, sid)).fetchone():
            raise HTTPException(404, "Venta no encontrada")
        return _load_sale(c, sid)


@app.post("/sales/{sid}/void")
def void_sale(sid: int, d: SaleVoid, request: Request):
    """Anula una venta o patrocinio: las unidades vuelven a sus lotes y el registro se conserva."""
    u = require_admin(request)
    with connect() as c:
        s = c.execute("SELECT id,kind,number,status FROM sales WHERE id=%s FOR UPDATE", (sid,)).fetchone()
        if not s:
            raise HTTPException(404, "Registro no encontrado")
        if s["status"] != "completed":
            raise HTTPException(409, "Este registro ya fue anulado")
        mtype = "sale_void" if s["kind"] == "sale" else "giveaway_void"
        label = ("Anulación de venta #" if s["kind"] == "sale" else "Anulación de patrocinio #") + f"{s['number']}: {d.reason.strip()}"
        items = c.execute("SELECT id,product_id,quantity,unit_cost FROM sale_items WHERE sale_id=%s ORDER BY product_id", (sid,)).fetchall()
        for it in items:
            p = c.execute("SELECT quantity FROM products WHERE id=%s FOR UPDATE", (it["product_id"],)).fetchone()
            allocs = c.execute("SELECT lot_id,quantity,unit_cost FROM sale_item_lots WHERE sale_item_id=%s", (it["id"],)).fetchall()
            if allocs:
                after = p["quantity"]
                for a in allocs:
                    c.execute("UPDATE lots SET quantity=quantity+%s WHERE id=%s", (a["quantity"], a["lot_id"]))
                    after += a["quantity"]
                    c.execute("""INSERT INTO movements(product_id,lot_id,user_id,movement_type,quantity_delta,quantity_after,unit_cost,note,sale_id)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (it["product_id"], a["lot_id"], u["id"], mtype, a["quantity"], after, a["unit_cost"], label, sid))
                refresh_product(c, it["product_id"])
            else:
                # Registro importado del Excel: sus unidades ya estaban fuera del saldo inicial; vuelven como lote de devolución.
                add_lot(c, it["product_id"], it["quantity"], it["unit_cost"], None, "return", u["id"], mtype, label, sale_id=sid)
        c.execute("UPDATE sales SET status='voided',voided_by=%s,voided_at=NOW(),void_reason=%s WHERE id=%s", (u["id"], d.reason.strip(), sid))
        paid = c.execute("SELECT COALESCE(SUM(amount),0) p FROM sale_payments WHERE sale_id=%s", (sid,)).fetchone()["p"]
    msg = f"Registro #{s['number']} anulado. Las unidades volvieron al inventario."
    if paid > 0:
        msg += f" Tenía pagos por {money(paid)}: recuerda gestionar la devolución."
    return {"id": sid, "status": "voided", "message": msg}


@app.get("/receivables")
def receivables(request: Request):
    user_from(request)
    with connect() as c:
        rows = c.execute(SALE_SELECT + " WHERE s.kind='sale' AND s.status='completed' ORDER BY s.sale_date,s.number").fetchall()
    out = [_sale_out(r) for r in rows]
    out = [x for x in out if x["balance"] > 0]
    t = local_today()
    for x in out:
        x["days"] = (t - x["sale_date"]).days
    return {"items": out, "total": sum((x["balance"] for x in out), Decimal(0))}


# ------------------------------- COMPRAS -------------------------------
class PurchaseItemIn(BaseModel):
    product_id: int
    quantity: int = Field(gt=0, le=1000000)
    unit_price: Decimal = Field(ge=0, le=999999999)
    expiry_date: Optional[date] = None


class PurchaseIn(BaseModel):
    purchase_date: date
    supplier: str = Field(min_length=1, max_length=120)
    funded_by: str = Field(default="", max_length=80)
    origin: str = Field(default="", max_length=60)
    currency: Literal["COP", "USD", "EUR", "GBP"] = "COP"
    fx_rate: Decimal = Field(default=Decimal(1), gt=0, le=100000)
    invoice_no: str = Field(default="", max_length=80)
    tracking: str = Field(default="", max_length=120)
    carrier: str = Field(default="", max_length=80)
    shipping_cost: Decimal = Field(default=Decimal(0), ge=0, le=999999999)
    other_costs: Decimal = Field(default=Decimal(0), ge=0, le=999999999)
    note: str = Field(default="", max_length=1000)
    items: List[PurchaseItemIn] = Field(min_length=1, max_length=200)


class ReceiveItem(BaseModel):
    id: int
    expiry_date: Optional[date] = None


class PurchaseReceive(BaseModel):
    items: List[ReceiveItem] = Field(default_factory=list, max_length=200)


PURCHASE_STATUS = {"ordered": "Pedida", "received": "Recibida", "cancelled": "Cancelada"}


def _purchase_out(c, r, with_items=True):
    d = dict(r)
    d["status_label"] = PURCHASE_STATUS[d["status"]]
    if with_items:
        d["items"] = c.execute("""SELECT pi.id,pi.product_id,p.sku,p.name,b.name brand,pi.quantity,pi.unit_price,pi.expiry_date,pi.landed_unit_cost
            FROM purchase_items pi JOIN products p ON p.id=pi.product_id JOIN brands b ON b.id=p.brand_id
            WHERE pi.purchase_id=%s ORDER BY pi.id""", (d["id"],)).fetchall()
    return d


def _purchase_totals(d: PurchaseIn):
    fx = Decimal(1) if d.currency == "COP" else d.fx_rate
    goods = money(sum((it.quantity * it.unit_price * fx for it in d.items), Decimal(0)))
    return fx, goods, goods + money(d.shipping_cost) + money(d.other_costs)


def _validate_purchase(c, d: PurchaseIn):
    if d.purchase_date > local_today():
        raise HTTPException(422, "La fecha de la compra no puede estar en el futuro")
    ids = [i.product_id for i in d.items]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "No repitas un producto en la misma compra")
    for pid in ids:
        if not c.execute("SELECT 1 FROM products WHERE id=%s AND is_active", (pid,)).fetchone():
            raise HTTPException(422, f"El producto {pid} no existe o fue retirado")


@app.get("/purchases")
def list_purchases(request: Request, status: str = Query("all", pattern="^(all|ordered|received|cancelled)$"),
                   month: str = Query("", pattern=r"^(\d{4}-\d{2})?$"), search: str = Query("", max_length=120)):
    user_from(request)
    start, end = _month_range(month) if month else (None, None)
    term = f"%{search.strip()}%"
    with connect() as c:
        rows = c.execute("""SELECT pu.*,(SELECT COUNT(*) FROM purchase_items pi WHERE pi.purchase_id=pu.id) lines,
            (SELECT COALESCE(SUM(quantity),0) FROM purchase_items pi WHERE pi.purchase_id=pu.id) units
            FROM purchases pu WHERE (%(st)s='all' OR pu.status=%(st)s)
            AND (%(start)s::date IS NULL OR (pu.purchase_date>=%(start)s AND pu.purchase_date<%(end)s))
            AND (%(s)s='' OR pu.supplier ILIKE %(term)s OR pu.funded_by ILIKE %(term)s OR pu.invoice_no ILIKE %(term)s
                 OR pu.tracking ILIKE %(term)s OR pu.note ILIKE %(term)s)
            ORDER BY pu.purchase_date DESC,pu.id DESC LIMIT 500""",
                         {"st": status, "start": start, "end": end, "s": search.strip(), "term": term}).fetchall()
        items = [_purchase_out(c, r, with_items=False) for r in rows]
    valid = [i for i in items if i["status"] != "cancelled"]
    by_funder = {}
    for i in valid:
        k = i["funded_by"] or "Sin indicar"
        by_funder[k] = by_funder.get(k, Decimal(0)) + i["total_cop"]
    return {"items": items, "summary": {
        "count": len(valid), "total": sum((i["total_cop"] for i in valid), Decimal(0)),
        "pending": sum((i["total_cop"] for i in valid if i["status"] == "ordered"), Decimal(0)),
        "by_funder": [{"name": k, "total": v} for k, v in sorted(by_funder.items(), key=lambda x: -x[1])]}}


@app.get("/purchases/{pid}")
def purchase_detail(pid: int, request: Request):
    user_from(request)
    with connect() as c:
        r = c.execute("SELECT * FROM purchases WHERE id=%s", (pid,)).fetchone()
        if not r:
            raise HTTPException(404, "Compra no encontrada")
        return _purchase_out(c, r)


@app.post("/purchases", status_code=201)
def create_purchase(d: PurchaseIn, request: Request):
    u = user_from(request)
    fx, goods, total = _purchase_totals(d)
    with connect() as c:
        _validate_purchase(c, d)
        pid = c.execute("""INSERT INTO purchases(purchase_date,supplier,funded_by,origin,currency,fx_rate,invoice_no,tracking,carrier,
            shipping_cost,other_costs,goods_total,total_cop,note,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (d.purchase_date, d.supplier.strip(), d.funded_by.strip(), d.origin.strip(), d.currency, fx, d.invoice_no.strip(),
                         d.tracking.strip(), d.carrier.strip(), money(d.shipping_cost), money(d.other_costs), goods, total,
                         d.note.strip(), u["id"])).fetchone()["id"]
        for it in d.items:
            c.execute("INSERT INTO purchase_items(purchase_id,product_id,quantity,unit_price,expiry_date) VALUES(%s,%s,%s,%s,%s)",
                      (pid, it.product_id, it.quantity, it.unit_price, it.expiry_date))
        return _purchase_out(c, c.execute("SELECT * FROM purchases WHERE id=%s", (pid,)).fetchone())


@app.patch("/purchases/{pid}")
def update_purchase(pid: int, d: PurchaseIn, request: Request):
    user_from(request)
    fx, goods, total = _purchase_totals(d)
    with connect() as c:
        r = c.execute("SELECT status FROM purchases WHERE id=%s FOR UPDATE", (pid,)).fetchone()
        if not r:
            raise HTTPException(404, "Compra no encontrada")
        if r["status"] != "ordered":
            raise HTTPException(409, "Solo se editan compras que aún no se han recibido")
        _validate_purchase(c, d)
        c.execute("""UPDATE purchases SET purchase_date=%s,supplier=%s,funded_by=%s,origin=%s,currency=%s,fx_rate=%s,invoice_no=%s,
            tracking=%s,carrier=%s,shipping_cost=%s,other_costs=%s,goods_total=%s,total_cop=%s,note=%s WHERE id=%s""",
                  (d.purchase_date, d.supplier.strip(), d.funded_by.strip(), d.origin.strip(), d.currency, fx, d.invoice_no.strip(),
                   d.tracking.strip(), d.carrier.strip(), money(d.shipping_cost), money(d.other_costs), goods, total, d.note.strip(), pid))
        c.execute("DELETE FROM purchase_items WHERE purchase_id=%s", (pid,))
        for it in d.items:
            c.execute("INSERT INTO purchase_items(purchase_id,product_id,quantity,unit_price,expiry_date) VALUES(%s,%s,%s,%s,%s)",
                      (pid, it.product_id, it.quantity, it.unit_price, it.expiry_date))
        return _purchase_out(c, c.execute("SELECT * FROM purchases WHERE id=%s", (pid,)).fetchone())


@app.post("/purchases/{pid}/receive")
def receive_purchase(pid: int, request: Request, d: Optional[PurchaseReceive] = None):
    """Recibe la compra: crea un lote por producto con su vencimiento y coste aterrizado (precio × tasa + envío y otros
    costes repartidos según el valor de cada línea)."""
    u = user_from(request)
    d = d or PurchaseReceive()
    with connect() as c:
        pu = c.execute("SELECT * FROM purchases WHERE id=%s FOR UPDATE", (pid,)).fetchone()
        if not pu:
            raise HTTPException(404, "Compra no encontrada")
        if pu["status"] != "ordered":
            raise HTTPException(409, "Esta compra ya fue recibida o cancelada")
        expiry = {i.id: i.expiry_date for i in d.items}
        items = c.execute("SELECT id,product_id,quantity,unit_price,expiry_date FROM purchase_items WHERE purchase_id=%s ORDER BY product_id",
                          (pid,)).fetchall()
        if not items:
            raise HTTPException(422, "La compra no tiene productos")
        fx = pu["fx_rate"]
        goods = sum((i["quantity"] * i["unit_price"] * fx for i in items), Decimal(0))
        extra = pu["shipping_cost"] + pu["other_costs"]
        units = sum(i["quantity"] for i in items)
        for i in items:
            line = i["quantity"] * i["unit_price"] * fx
            share = (extra * line / goods) if goods > 0 else (extra * i["quantity"] / units)
            landed = money((line + share) / i["quantity"])
            exp = expiry.get(i["id"], i["expiry_date"])
            c.execute("SELECT 1 FROM products WHERE id=%s FOR UPDATE", (i["product_id"],))
            add_lot(c, i["product_id"], i["quantity"], landed, exp, "purchase", u["id"], "purchase",
                    f"Compra #{pid} · {pu['supplier']}", purchase_id=pid)
            c.execute("UPDATE purchase_items SET landed_unit_cost=%s,expiry_date=%s WHERE id=%s", (landed, exp, i["id"]))
        c.execute("UPDATE purchases SET status='received',received_at=NOW(),received_by=%s WHERE id=%s", (u["id"], pid))
        return _purchase_out(c, c.execute("SELECT * FROM purchases WHERE id=%s", (pid,)).fetchone())


@app.post("/purchases/{pid}/unreceive")
def unreceive_purchase(pid: int, request: Request):
    """Revierte una recepción por error, solo si ninguna unidad de esos lotes se ha movido."""
    u = require_admin(request)
    with connect() as c:
        pu = c.execute("SELECT status,imported FROM purchases WHERE id=%s FOR UPDATE", (pid,)).fetchone()
        if not pu:
            raise HTTPException(404, "Compra no encontrada")
        if pu["status"] != "received" or pu["imported"]:
            raise HTTPException(409, "Solo se revierte la recepción de compras recibidas en la plataforma")
        lots = c.execute("SELECT id,product_id,quantity,initial_quantity,unit_cost FROM lots WHERE purchase_id=%s FOR UPDATE", (pid,)).fetchall()
        if any(l["quantity"] != l["initial_quantity"] for l in lots):
            raise HTTPException(409, "No se puede revertir: ya se vendieron o movieron unidades de esta compra")
        for l in lots:
            c.execute("UPDATE lots SET quantity=0 WHERE id=%s", (l["id"],))
            after = refresh_product(c, l["product_id"])
            c.execute("""INSERT INTO movements(product_id,lot_id,user_id,movement_type,quantity_delta,quantity_after,unit_cost,note,purchase_id)
                VALUES(%s,%s,%s,'purchase_reversal',%s,%s,%s,%s,%s)""",
                      (l["product_id"], l["id"], u["id"], -l["quantity"], after, l["unit_cost"], f"Reversión de recepción · Compra #{pid}", pid))
        c.execute("UPDATE purchase_items SET landed_unit_cost=NULL WHERE purchase_id=%s", (pid,))
        c.execute("UPDATE purchases SET status='ordered',received_at=NULL,received_by=NULL WHERE id=%s", (pid,))
        return _purchase_out(c, c.execute("SELECT * FROM purchases WHERE id=%s", (pid,)).fetchone())


@app.post("/purchases/{pid}/cancel")
def cancel_purchase(pid: int, request: Request):
    user_from(request)
    with connect() as c:
        r = c.execute("UPDATE purchases SET status='cancelled' WHERE id=%s AND status='ordered' RETURNING id", (pid,)).fetchone()
        if not r:
            raise HTTPException(409, "Solo se cancelan compras pedidas que no se han recibido")
    return {"id": pid, "status": "cancelled", "message": f"Compra #{pid} cancelada"}


# ------------------------------- GASTOS -------------------------------
class ExpenseIn(BaseModel):
    expense_date: date
    category: Literal["delivery", "shipping", "marketing", "comms", "logistics", "obligations", "platforms", "other"]
    description: str = Field(min_length=2, max_length=200)
    amount: Decimal = Field(gt=0, le=999999999)
    paid_by: str = Field(default="", max_length=80)
    payment_method: PaymentMethod = "bancolombia"
    note: str = Field(default="", max_length=1000)


EXPENSE_SELECT = """SELECT e.*,cu.username created_by_name,vu.username voided_by_name FROM expenses e
    LEFT JOIN app_users cu ON cu.id=e.created_by LEFT JOIN app_users vu ON vu.id=e.voided_by"""


def _expense_out(r):
    d = dict(r)
    d["category_label"] = EXPENSE_CATEGORIES.get(d["category"], d["category"])
    d["method_label"] = PAYMENT_METHODS.get(d["payment_method"], d["payment_method"])
    return d


@app.get("/expenses")
def list_expenses(request: Request, month: str = Query("", pattern=r"^(\d{4}-\d{2})?$"), category: str = Query("all", max_length=20),
                  search: str = Query("", max_length=120), include_voided: bool = False):
    user_from(request)
    start, end = _month_range(month) if month else (None, None)
    term = f"%{search.strip()}%"
    with connect() as c:
        rows = c.execute(EXPENSE_SELECT + """ WHERE (%(start)s::date IS NULL OR (e.expense_date>=%(start)s AND e.expense_date<%(end)s))
            AND (%(cat)s='all' OR e.category=%(cat)s) AND (%(iv)s OR e.voided_at IS NULL)
            AND (%(s)s='' OR e.description ILIKE %(term)s OR e.paid_by ILIKE %(term)s OR e.note ILIKE %(term)s)
            ORDER BY e.expense_date DESC,e.id DESC LIMIT 1000""",
                         {"start": start, "end": end, "cat": category, "iv": include_voided, "s": search.strip(), "term": term}).fetchall()
    items = [_expense_out(r) for r in rows]
    valid = [i for i in items if not i["voided_at"]]
    by_cat = {}
    for i in valid:
        by_cat[i["category"]] = by_cat.get(i["category"], Decimal(0)) + i["amount"]
    return {"items": items, "summary": {"count": len(valid), "total": sum((i["amount"] for i in valid), Decimal(0)),
            "by_category": [{"category": k, "label": EXPENSE_CATEGORIES[k], "total": v} for k, v in sorted(by_cat.items(), key=lambda x: -x[1])]}}


def _validate_expense(d):
    if d.expense_date > local_today():
        raise HTTPException(422, "La fecha del gasto no puede estar en el futuro")


@app.post("/expenses", status_code=201)
def create_expense(d: ExpenseIn, request: Request):
    u = user_from(request)
    _validate_expense(d)
    with connect() as c:
        eid = c.execute("""INSERT INTO expenses(expense_date,category,description,amount,paid_by,payment_method,note,created_by)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""", (d.expense_date, d.category, d.description.strip(), money(d.amount),
                                                             d.paid_by.strip(), d.payment_method, d.note.strip(), u["id"])).fetchone()["id"]
        return _expense_out(c.execute(EXPENSE_SELECT + " WHERE e.id=%s", (eid,)).fetchone())


@app.patch("/expenses/{eid}")
def update_expense(eid: int, d: ExpenseIn, request: Request):
    require_admin(request)
    _validate_expense(d)
    with connect() as c:
        r = c.execute("""UPDATE expenses SET expense_date=%s,category=%s,description=%s,amount=%s,paid_by=%s,payment_method=%s,note=%s
            WHERE id=%s AND voided_at IS NULL RETURNING id""", (d.expense_date, d.category, d.description.strip(), money(d.amount),
                                                               d.paid_by.strip(), d.payment_method, d.note.strip(), eid)).fetchone()
        if not r:
            raise HTTPException(404, "Gasto no encontrado o anulado")
        return _expense_out(c.execute(EXPENSE_SELECT + " WHERE e.id=%s", (eid,)).fetchone())


@app.post("/expenses/{eid}/void")
def void_expense(eid: int, d: SaleVoid, request: Request):
    u = require_admin(request)
    with connect() as c:
        r = c.execute("UPDATE expenses SET voided_at=NOW(),voided_by=%s,void_reason=%s WHERE id=%s AND voided_at IS NULL RETURNING id",
                      (u["id"], d.reason.strip(), eid)).fetchone()
    if not r:
        raise HTTPException(409, "El gasto no existe o ya estaba anulado")
    return {"id": eid, "message": "Gasto anulado. Se conserva en el historial."}


# ------------------------------- SOCIOS -------------------------------
class PartnerIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)


class CapitalIn(BaseModel):
    partner_id: int
    movement_date: date
    kind: Literal["cash", "in_kind", "withdrawal"]
    amount: Decimal = Field(gt=0, le=9999999999)
    note: str = Field(default="", max_length=500)


@app.get("/partners")
def list_partners(request: Request):
    user_from(request)
    with connect() as c:
        partners = c.execute("""SELECT p.id,p.name,
            COALESCE(SUM(m.amount) FILTER (WHERE m.kind='cash'),0) cash,
            COALESCE(SUM(m.amount) FILTER (WHERE m.kind='in_kind'),0) in_kind,
            COALESCE(SUM(m.amount) FILTER (WHERE m.kind='withdrawal'),0) withdrawals
            FROM partners p LEFT JOIN capital_movements m ON m.partner_id=p.id AND m.voided_at IS NULL
            WHERE p.is_active GROUP BY p.id ORDER BY p.name""").fetchall()
        movements = c.execute("""SELECT m.id,m.partner_id,p.name partner,m.movement_date,m.kind,m.amount,m.note,u.username created_by
            FROM capital_movements m JOIN partners p ON p.id=m.partner_id LEFT JOIN app_users u ON u.id=m.created_by
            WHERE m.voided_at IS NULL ORDER BY m.movement_date DESC,m.id DESC""").fetchall()
        funded = c.execute("""SELECT funded_by name,COALESCE(SUM(total_cop),0) total FROM purchases WHERE status<>'cancelled'
            GROUP BY funded_by ORDER BY 2 DESC""").fetchall()
    for p in partners:
        p["contributed"] = p["cash"] + p["in_kind"]
        p["net"] = p["contributed"] - p["withdrawals"]
    total = sum((p["contributed"] for p in partners), Decimal(0))
    for p in partners:
        p["share_pct"] = (p["contributed"] / total * 100).quantize(Decimal("0.1")) if total else Decimal(0)
    for m in movements:
        m["kind_label"] = CAPITAL_KINDS[m["kind"]]
    return {"partners": partners, "movements": movements, "purchases_by_funder": funded,
            "summary": {"cash": sum((p["cash"] for p in partners), Decimal(0)), "in_kind": sum((p["in_kind"] for p in partners), Decimal(0)),
                        "withdrawals": sum((p["withdrawals"] for p in partners), Decimal(0)), "contributed": total}}


@app.post("/partners", status_code=201)
def create_partner(d: PartnerIn, request: Request):
    require_admin(request)
    try:
        with connect() as c:
            return c.execute("INSERT INTO partners(name) VALUES(%s) RETURNING id,name", (d.name.strip(),)).fetchone()
    except UniqueViolation:
        raise HTTPException(409, "Ya existe un socio con ese nombre")


@app.post("/capital", status_code=201)
def create_capital(d: CapitalIn, request: Request):
    u = require_admin(request)
    if d.movement_date > local_today():
        raise HTTPException(422, "La fecha no puede estar en el futuro")
    with connect() as c:
        if not c.execute("SELECT 1 FROM partners WHERE id=%s AND is_active", (d.partner_id,)).fetchone():
            raise HTTPException(422, "Socio no encontrado")
        r = c.execute("""INSERT INTO capital_movements(partner_id,movement_date,kind,amount,note,created_by)
            VALUES(%s,%s,%s,%s,%s,%s) RETURNING id""", (d.partner_id, d.movement_date, d.kind, money(d.amount), d.note.strip(), u["id"])).fetchone()
    return {"id": r["id"], "message": "Movimiento de capital registrado"}


@app.delete("/capital/{mid}")
def void_capital(mid: int, request: Request):
    u = require_admin(request)
    with connect() as c:
        if not c.execute("UPDATE capital_movements SET voided_at=NOW(),voided_by=%s WHERE id=%s AND voided_at IS NULL RETURNING id",
                         (u["id"], mid)).fetchone():
            raise HTTPException(404, "Movimiento no encontrado")
    return {"message": "Movimiento anulado"}


# ------------------------------- RESULTADOS -------------------------------
def _month_figures(c, start, end):
    s = c.execute("""SELECT COUNT(*) n,COALESCE(SUM(subtotal),0) gross,COALESCE(SUM(discount),0) disc,COALESCE(SUM(delivery_fee),0) deliv,
        COALESCE(SUM(cost_total),0) cost FROM sales WHERE kind='sale' AND status='completed' AND sale_date>=%s AND sale_date<%s""",
                  (start, end)).fetchone()
    g = c.execute("""SELECT COUNT(*) n,COALESCE(SUM(subtotal),0) value,COALESCE(SUM(cost_total),0) cost FROM sales
        WHERE kind='giveaway' AND status='completed' AND sale_date>=%s AND sale_date<%s""", (start, end)).fetchone()
    e = c.execute("SELECT COALESCE(SUM(amount),0) t FROM expenses WHERE voided_at IS NULL AND expense_date>=%s AND expense_date<%s",
                  (start, end)).fetchone()["t"]
    adj = c.execute("""SELECT COALESCE(SUM(-quantity_delta*COALESCE(unit_cost,0)),0) t FROM movements WHERE movement_type='manual_out'
        AND (created_at AT TIME ZONE %s)::date>=%s AND (created_at AT TIME ZONE %s)::date<%s""", (LOCAL_TZ_NAME, start, LOCAL_TZ_NAME, end)).fetchone()["t"]
    net = s["gross"] - s["disc"]
    gross_margin = net - s["cost"]
    result = gross_margin + s["deliv"] - g["cost"] - e - adj
    return {"sales": s["n"], "gross": s["gross"], "discounts": s["disc"], "net_revenue": net, "delivery_fees": s["deliv"],
            "cost": s["cost"], "gross_margin": gross_margin,
            "gross_margin_pct": (gross_margin / net * 100).quantize(Decimal("0.1")) if net else None,
            "giveaways": g["n"], "giveaway_value": g["value"], "giveaway_cost": g["cost"],
            "expenses": e, "adjustments": money(adj), "result": money(result)}


@app.get("/reports/summary")
def report_summary(request: Request, month: str = Query(..., pattern=r"^\d{4}-\d{2}$")):
    user_from(request)
    start, end = _month_range(month)
    t = local_today()
    with connect() as c:
        fig = _month_figures(c, start, end)
        fig["units"] = c.execute("""SELECT COALESCE(SUM(si.quantity),0) q FROM sale_items si JOIN sales s ON s.id=si.sale_id
            WHERE s.kind='sale' AND s.status='completed' AND s.sale_date>=%s AND s.sale_date<%s""", (start, end)).fetchone()["q"]
        line_net = """SUM(si.line_total - CASE WHEN s.subtotal>0 THEN s.discount*si.line_total/s.subtotal ELSE 0 END)"""
        by_brand = c.execute(f"""SELECT b.name brand,SUM(si.quantity) units,{line_net} net,SUM(si.line_cost) cost
            FROM sale_items si JOIN sales s ON s.id=si.sale_id JOIN products p ON p.id=si.product_id JOIN brands b ON b.id=p.brand_id
            WHERE s.kind='sale' AND s.status='completed' AND s.sale_date>=%s AND s.sale_date<%s GROUP BY b.name ORDER BY 3 DESC""",
                             (start, end)).fetchall()
        by_product = c.execute(f"""SELECT p.id,p.sku,p.name,b.name brand,SUM(si.quantity) units,{line_net} net,SUM(si.line_cost) cost,
            BOOL_OR(si.cost_estimated) estimated
            FROM sale_items si JOIN sales s ON s.id=si.sale_id JOIN products p ON p.id=si.product_id JOIN brands b ON b.id=p.brand_id
            WHERE s.kind='sale' AND s.status='completed' AND s.sale_date>=%s AND s.sale_date<%s
            GROUP BY p.id,p.sku,p.name,b.name ORDER BY 6 DESC LIMIT 20""", (start, end)).fetchall()
        for rows in (by_brand, by_product):
            for r in rows:
                r["net"] = money(r["net"] or 0)
                r["margin"] = r["net"] - r["cost"]
        by_referrer = c.execute("""SELECT COALESCE(NULLIF(referrer,''),'Sin indicar') referrer,COUNT(*) sales,SUM(subtotal-discount) net
            FROM sales WHERE kind='sale' AND status='completed' AND sale_date>=%s AND sale_date<%s GROUP BY 1 ORDER BY 3 DESC""",
                                (start, end)).fetchall()
        giveaways = c.execute("""SELECT reason,COUNT(*) n,SUM(cost_total) cost,SUM(subtotal) value FROM sales WHERE kind='giveaway'
            AND status='completed' AND sale_date>=%s AND sale_date<%s GROUP BY reason ORDER BY 3 DESC""", (start, end)).fetchall()
        for r in giveaways:
            r["label"] = GIVEAWAY_REASONS.get(r["reason"] or "other", "Otro")
        expenses = c.execute("""SELECT category,SUM(amount) total FROM expenses WHERE voided_at IS NULL AND expense_date>=%s
            AND expense_date<%s GROUP BY category ORDER BY 2 DESC""", (start, end)).fetchall()
        for r in expenses:
            r["label"] = EXPENSE_CATEGORIES.get(r["category"], r["category"])
        collected = c.execute("""SELECT sp.method,SUM(sp.amount) total FROM sale_payments sp JOIN sales s ON s.id=sp.sale_id
            WHERE s.status='completed' AND sp.payment_date>=%s AND sp.payment_date<%s GROUP BY sp.method ORDER BY 2 DESC""",
                              (start, end)).fetchall()
        for r in collected:
            r["label"] = PAYMENT_METHODS.get(r["method"], r["method"])
        purchases = c.execute("""SELECT COUNT(*) n,COALESCE(SUM(total_cop),0) total FROM purchases WHERE status<>'cancelled'
            AND purchase_date>=%s AND purchase_date<%s""", (start, end)).fetchone()
        rec = c.execute("""SELECT COUNT(*) n,COALESCE(SUM(s.total-p.paid),0) total FROM sales s
            JOIN LATERAL (SELECT COALESCE(SUM(amount),0) paid FROM sale_payments WHERE sale_id=s.id) p ON TRUE
            WHERE s.kind='sale' AND s.status='completed' AND s.total>p.paid""").fetchone()
        inv = c.execute("""SELECT COALESCE(SUM(quantity),0) units,COALESCE(SUM(quantity*unit_cost),0) value_cost,
            COALESCE(SUM(quantity*COALESCE(sale_price,0)),0) value_price FROM products WHERE is_active""").fetchone()
        exp = c.execute("""SELECT COALESCE(SUM(l.quantity) FILTER (WHERE l.expiry_date<%(t)s),0) expired_units,
            COALESCE(SUM(l.quantity*l.unit_cost) FILTER (WHERE l.expiry_date<%(t)s),0) expired_value,
            COALESCE(SUM(l.quantity) FILTER (WHERE l.expiry_date>=%(t)s AND l.expiry_date<=%(w)s),0) expiring_units,
            COALESCE(SUM(l.quantity*l.unit_cost) FILTER (WHERE l.expiry_date>=%(t)s AND l.expiry_date<=%(w)s),0) expiring_value
            FROM lots l JOIN products p ON p.id=l.product_id WHERE p.is_active AND l.quantity>0""",
                        {"t": t, "w": t + timedelta(days=EXPIRY_WARNING_DAYS)}).fetchone()
        first = _shift_month(start, -5)
        trend = []
        for i in range(6):
            m = _shift_month(first, i)
            f = _month_figures(c, m, _shift_month(m, 1))
            trend.append({"month": m.strftime("%Y-%m"), "net_revenue": f["net_revenue"], "gross_margin": f["gross_margin"],
                          "giveaway_cost": f["giveaway_cost"], "expenses": f["expenses"], "result": f["result"]})
        estimated = c.execute("""SELECT COUNT(*) n FROM sale_items si JOIN sales s ON s.id=si.sale_id WHERE si.cost_estimated
            AND s.status='completed' AND s.sale_date>=%s AND s.sale_date<%s""", (start, end)).fetchone()["n"]
    return {"month": month, "figures": fig, "by_brand": by_brand, "by_product": by_product, "by_referrer": by_referrer,
            "giveaways": giveaways, "expenses": expenses, "collected": collected,
            "collected_total": sum((r["total"] for r in collected), Decimal(0)), "purchases": purchases,
            "receivables": rec, "inventory": {**inv, **exp}, "trend": trend, "estimated_cost_lines": estimated,
            "expiry_warning_days": EXPIRY_WARNING_DAYS}
