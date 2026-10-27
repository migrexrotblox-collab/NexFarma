import base64
import csv
import hashlib
import html
import io
import json
import os
import re
import secrets
import smtplib
import sqlite3
import ssl
import unicodedata
import uuid
import requests
from datetime import datetime, timedelta, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr
from functools import wraps
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from dotenv import load_dotenv
from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

try:
    from reportlab.lib import colors as pdf_colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
except ImportError:
    pdf_colors = None
    A4 = None
    ParagraphStyle = getSampleStyleSheet = None
    Paragraph = SimpleDocTemplate = Spacer = Table = TableStyle = None
    TA_CENTER = TA_RIGHT = None
    mm = None

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE_DIR, "nexafarma.db")
UPLOAD = os.path.join(BASE_DIR, "static", "uploads")
os.makedirs(UPLOAD, exist_ok=True)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", ""),
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "0") == "1",
)


def save_product_image(file):
    """Valida una imagen real y la guarda bajo static/uploads para servirla en producción."""
    filename = secure_filename(file.filename or "")
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError("Formato de imagen no permitido. Usa PNG, JPG, WEBP o GIF.")
    try:
        from PIL import Image, UnidentifiedImageError
        file.stream.seek(0)
        with Image.open(file.stream) as image:
            if image.format.lower() not in {"png", "jpeg", "webp", "gif"}:
                raise ValueError("El archivo no es una imagen compatible.")
            if image.width < 1 or image.height < 1 or image.width * image.height > 40_000_000:
                raise ValueError("La imagen tiene dimensiones inválidas o demasiado grandes.")
            image.verify()
        file.stream.seek(0)
    except ImportError as exc:
        raise RuntimeError("Falta Pillow para validar las imágenes del catálogo.") from exc
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError("El archivo seleccionado no contiene una imagen válida.") from exc
    saved_name = f"{secrets.token_hex(12)}.{extension}"
    file.save(os.path.join(UPLOAD, saved_name))
    return f"uploads/{saved_name}"


def remove_product_image(image_path):
    """Elimina únicamente archivos propios de static/uploads."""
    if not image_path or not str(image_path).startswith("uploads/"):
        return
    filename = os.path.basename(str(image_path))
    path = os.path.abspath(os.path.join(UPLOAD, filename))
    if os.path.dirname(path) == os.path.abspath(UPLOAD) and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            app.logger.warning("No se pudo borrar el archivo de imagen %s", filename)
if not app.config["SECRET_KEY"]:
    app.config["SECRET_KEY"] = "nexafarma-dev-only-" + secrets.token_hex(24)

CONFIG = {
    "name": os.getenv("BUSINESS_NAME", "NexaFarma"),
    "currency": "COP",
    "discount": max(0, min(100, int(os.getenv("WELCOME_DISCOUNT", "15")))),
    "code_prefix": os.getenv("WELCOME_CODE_PREFIX", "NEXAFARMA"),
    "free_delivery_from": max(0, int(os.getenv("FREE_DELIVERY_FROM", "0"))),
    "delivery_fee": max(0, int(os.getenv("DELIVERY_FEE", "5000"))),
    "whatsapp": re.sub(r"\D", "", os.getenv("WHATSAPP_NUMBER", "")),
    "instagram": os.getenv("INSTAGRAM_URL", "https://instagram.com/"),
    "tiktok": os.getenv("TIKTOK_URL", "https://tiktok.com/"),
    "facebook": os.getenv("FACEBOOK_URL", "https://facebook.com/"),
    "address": os.getenv("BUSINESS_ADDRESS", "Barranquilla, Atlántico, Colombia"),
    "business_phone": os.getenv("BUSINESS_PHONE", ""),
    "legal_name": os.getenv("LEGAL_BUSINESS_NAME", "NexaFarma"),
    "nit": os.getenv("BUSINESS_NIT", ""),
}

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@nexafarma.com").strip().lower()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()

WOMPI_PUBLIC_KEY = os.getenv("WOMPI_PUBLIC_KEY", "").strip()
WOMPI_INTEGRITY_SECRET = os.getenv("WOMPI_INTEGRITY_SECRET", os.getenv("WOMPI_INTEGRITY_KEY", "")).strip()
WOMPI_EVENTS_SECRET = os.getenv("WOMPI_EVENTS_SECRET", "").strip()
WOMPI_BASE_URL = os.getenv("WOMPI_BASE_URL", "https://checkout.wompi.co/p/").strip()
WOMPI_TEMPORARILY_UNAVAILABLE = os.getenv("WOMPI_TEMPORARILY_UNAVAILABLE", "1").strip() == "1"
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:5000").rstrip("/")


def public_base_url():
    """Usa la URL pública configurada; protege enlaces de correo contra URLs localhost en producción."""
    parsed = urlparse(BASE_URL)
    if parsed.scheme == "https" and parsed.hostname and parsed.hostname not in {"localhost", "127.0.0.1"}:
        return BASE_URL
    # PythonAnywhere sirve NexaFarma en este dominio. Se valida el host exacto para
    # no construir enlaces de recuperación a partir de un Host header arbitrario.
    try:
        request_host = (request.host or "").split(":", 1)[0].lower().rstrip(".")
    except RuntimeError:
        request_host = ""
    if request_host == "migrex.pythonanywhere.com":
        return "https://migrex.pythonanywhere.com"
    return BASE_URL
WOMPI_ENVIRONMENT = "prod" if WOMPI_PUBLIC_KEY.startswith("pub_prod_") else "test"

MERCADOPAGO_ACCESS_TOKEN = os.getenv("MERCADOPAGO_ACCESS_TOKEN", "").strip()
MERCADOPAGO_WEBHOOK_SECRET = os.getenv("MERCADOPAGO_WEBHOOK_SECRET", "").strip()
MERCADOPAGO_API_BASE_URL = "https://api.mercadopago.com"

SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USERNAME).strip()
EMAIL_FROM_NAME = os.getenv("EMAIL_FROM_NAME", CONFIG.get("name", "NexaFarma")).strip() or "NexaFarma"
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "1") == "1"
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "0") == "1"
MAIL_ADMIN = os.getenv("MAIL_ADMIN", ADMIN_EMAIL).strip()
EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "auto").strip().lower()
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
RESEND_FROM = os.getenv("RESEND_FROM", SMTP_FROM or "").strip()
# Gmail API (HTTPS): alternativa a SMTP cuando Render Free bloquea puertos SMTP.
GMAIL_API_CLIENT_ID = os.getenv("GMAIL_API_CLIENT_ID", "").strip()
GMAIL_API_CLIENT_SECRET = os.getenv("GMAIL_API_CLIENT_SECRET", "").strip()
GMAIL_API_REFRESH_TOKEN = os.getenv("GMAIL_API_REFRESH_TOKEN", "").strip()
GMAIL_API_FROM = os.getenv("GMAIL_API_FROM", "").strip()
GMAIL_API_REPLY_TO = os.getenv("GMAIL_API_REPLY_TO", "").strip()
GMAIL_API_TOKEN_URL = "https://oauth2.googleapis.com/token"
GMAIL_API_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"

GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "").strip()
GOOGLE_OAUTH_CLIENT_SECRET = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
GOOGLE_OAUTH_REDIRECT_URI = os.getenv("GOOGLE_OAUTH_REDIRECT_URI", "").strip()
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_TOKEN_INFO_URL = "https://oauth2.googleapis.com/tokeninfo"


# Configuración de Groq
CHATBOT_MODEL = os.getenv("CHATBOT_MODEL", "openai/gpt-oss-20b").strip()
AGENT_MODEL = os.getenv("AGENT_MODEL", CHATBOT_MODEL).strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}
VALID_ORDER_STATUSES = {"received", "paid", "preparing", "on_the_way", "delivering", "delivered", "cancelled"}
STATUS_ES = {
    "received": "Pedido recibido",
    "paid": "Pago confirmado",
    "preparing": "En preparación",
    "on_the_way": "En camino",
    "delivering": "En camino",
    "delivered": "Entregado",
    "cancelled": "Cancelado",
}
PAYMENT_ES = {
    "contra_entrega": "Contra entrega",
    "transferencia": "Transferencia bancaria",
    "wompi": "Wompi · tarjetas / Nequi / Bancolombia / PSE",
    "mercadopago": "Mercado Pago · Checkout Pro",
}
PAYMENT_STATUS_ES = {
    "pending": "Pendiente",
    "approved": "Aprobado",
    "declined": "Rechazado",
    "error": "Error",
    "voided": "Anulado",
    "refunded": "Reembolsado",
}
WOMPI_METHODS = [
    ("💳", "Tarjetas", "Visa, Mastercard y American Express"),
    ("📱", "Nequi", "Pago desde la app Nequi"),
    ("🏦", "Bancolombia", "Botón de transferencia Bancolombia"),
    ("🔐", "PSE", "Transferencia desde otros bancos"),
]
FINAL_PAYMENT_STATUSES = {"APPROVED", "DECLINED", "ERROR", "VOIDED"}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except ValueError:
        return None


def money(value):
    return f"${int(value or 0):,.0f}".replace(",", ".")


def price_per_liter(price, volume_ml):
    try:
        amount, volume = int(price), int(volume_ml)
        if amount < 0 or volume <= 0:
            return ""
        return f"{money(round(amount * 1000 / volume))} / L"
    except (TypeError, ValueError):
        return ""


def safe_local_target(target):
    target = (target or "").strip()
    if not target or "\\" in target or not target.startswith("/") or target.startswith("//"):
        return ""
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return ""
    return target


app.jinja_env.filters["money"] = money
app.jinja_env.filters["pum"] = price_per_liter


def db():
    conn = sqlite3.connect(DB, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def ensure_column(conn, table, column, definition):
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    fresh_database = not os.path.exists(DB)
    conn = db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            phone TEXT,
            password_hash TEXT NOT NULL,
            google_sub TEXT,
            address TEXT,
            city TEXT,
            neighborhood TEXT,
            is_admin INTEGER DEFAULT 0,
            is_active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS categories(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            slug TEXT UNIQUE,
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS products(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER,
            name TEXT NOT NULL,
            description TEXT,
            price INTEGER NOT NULL,
            image TEXT,
            active INTEGER DEFAULT 1,
            featured INTEGER DEFAULT 0,
            stock INTEGER DEFAULT 99,
            requires_prescription INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            FOREIGN KEY(category_id) REFERENCES categories(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS toppings(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            price INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sizes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            price INTEGER DEFAULT 0,
            volume_ml INTEGER,
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS coupons(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            code TEXT UNIQUE NOT NULL,
            percent INTEGER NOT NULL,
            expires_at TEXT,
            used INTEGER DEFAULT 0,
            min_purchase INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            max_uses INTEGER DEFAULT 1,
            uses_count INTEGER DEFAULT 0,
            created_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_no TEXT UNIQUE NOT NULL,
            user_id INTEGER,
            customer_name TEXT NOT NULL,
            email TEXT,
            phone TEXT NOT NULL,
            address TEXT NOT NULL,
            neighborhood TEXT,
            city TEXT,
            reference TEXT,
            delivery_method TEXT NOT NULL,
            subtotal INTEGER NOT NULL,
            discount INTEGER DEFAULT 0,
            delivery_fee INTEGER DEFAULT 0,
            total INTEGER NOT NULL,
            payment_method TEXT NOT NULL,
            payment_status TEXT DEFAULT 'pending',
            status TEXT DEFAULT 'received',
            coupon_code TEXT,
            wompi_reference TEXT,
            wompi_transaction_id TEXT,
            mercadopago_order_id TEXT,
            mercadopago_payment_id TEXT,
            mercadopago_idempotency_key TEXT,
            paid_at TEXT,
            invoice_email_sent_at TEXT,
            terms_accepted_at TEXT,
            notes TEXT,
            stock_restored INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS order_items(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            product_id INTEGER,
            product_name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            unit_price INTEGER NOT NULL,
            size TEXT,
            flavor TEXT,
            toppings TEXT,
            FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS favorites(
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            PRIMARY KEY(user_id, product_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS password_resets(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_hash TEXT UNIQUE NOT NULL,
            expires_at TEXT NOT NULL,
            used INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS audit_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT NOT NULL,
            entity TEXT,
            entity_id INTEGER,
            details TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS contact_messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT,
            subject TEXT,
            message TEXT NOT NULL,
            status TEXT DEFAULT 'new',
            created_at TEXT NOT NULL,
            replied_at TEXT
        );
        CREATE TABLE IF NOT EXISTS reviews(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            order_id INTEGER,
            rating INTEGER NOT NULL,
            comment TEXT,
            active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, product_id, order_id),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE,
            FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS site_settings(
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )

    migrations = [
        ("users", "google_sub", "TEXT"),
        ("users", "city", "TEXT"), ("users", "neighborhood", "TEXT"),
        ("users", "is_active", "INTEGER DEFAULT 1"), ("users", "updated_at", "TEXT"),
        ("categories", "slug", "TEXT"), ("categories", "active", "INTEGER DEFAULT 1"),
        ("products", "featured", "INTEGER DEFAULT 0"), ("products", "stock", "INTEGER DEFAULT 99"),
        ("products", "updated_at", "TEXT"), ("products", "requires_prescription", "INTEGER NOT NULL DEFAULT 0"), ("toppings", "active", "INTEGER DEFAULT 1"),
        ("toppings", "created_at", "TEXT"), ("sizes", "active", "INTEGER DEFAULT 1"),
        ("sizes", "created_at", "TEXT"), ("sizes", "volume_ml", "INTEGER"), ("coupons", "max_uses", "INTEGER DEFAULT 1"),
        ("coupons", "uses_count", "INTEGER DEFAULT 0"), ("coupons", "created_at", "TEXT"),
        ("orders", "email", "TEXT"), ("orders", "coupon_code", "TEXT"),
        ("orders", "wompi_reference", "TEXT"), ("orders", "wompi_transaction_id", "TEXT"),
        ("orders", "mercadopago_order_id", "TEXT"), ("orders", "mercadopago_idempotency_key", "TEXT"),
        ("orders", "mercadopago_payment_id", "TEXT"),
        ("orders", "paid_at", "TEXT"), ("orders", "invoice_email_sent_at", "TEXT"),
        ("orders", "terms_accepted_at", "TEXT"), ("orders", "notes", "TEXT"),
        ("orders", "stock_restored", "INTEGER DEFAULT 0"), ("orders", "updated_at", "TEXT"),
    ]
    for table, column, definition in migrations:
        ensure_column(conn, table, column, definition)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_sub ON users(google_sub) WHERE google_sub IS NOT NULL")

    categories = [
        "Cuidado personal", "Vitaminas y bienestar", "Primeros auxilios",
        "Higiene y protección", "Cuidado de la piel", "Maternidad y bebé",
        "Dispositivos de salud", "Nutrición y cuidado diario",
    ]
    for name in categories:
        slug = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()).strip("-")
        conn.execute(
            "INSERT OR IGNORE INTO categories(name,slug,active,created_at) VALUES(?,?,1,?)",
            (name, slug, now_iso()),
        )

    # This academic demo starts with general-care products, not prescription medicines.
    # Every item uses a COP price and basic stock to allow end-to-end test orders.
    count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    if fresh_database and count == 0:
        cat = {r["name"]: r["id"] for r in conn.execute("SELECT id,name FROM categories")}
        demo = [
            ("Cuidado personal", "Gel antibacterial 250 ml", "Gel para higiene de manos. Presentación demostrativa; revisa el envase y las advertencias antes de usar.", 8900, 1, 28),
            ("Primeros auxilios", "Curitas adhesivas surtidas", "Apósito adhesivo para protección básica de pequeñas rozaduras. No sustituye la valoración profesional si la lesión es importante.", 6500, 1, 40),
            ("Dispositivos de salud", "Termómetro digital", "Dispositivo de medición de temperatura corporal. Lee las instrucciones del fabricante para su uso correcto.", 24900, 1, 14),
            ("Cuidado de la piel", "Protector solar FPS 50", "Protección solar de uso diario. Sigue las instrucciones de la etiqueta y reaplica según indique el fabricante.", 38900, 1, 20),
            ("Higiene y protección", "Tapabocas desechables x 10", "Paquete de mascarillas desechables para uso cotidiano. Verifica el empaque y las recomendaciones del producto.", 9900, 0, 45),
            ("Cuidado personal", "Crema humectante 200 ml", "Crema para el cuidado diario de la piel. Suspende el uso si presentas irritación y consulta a un profesional.", 18900, 0, 24),
            ("Maternidad y bebé", "Toallitas húmedas x 80", "Toallitas para higiene cotidiana. Revisa los ingredientes y las indicaciones del empaque.", 12500, 0, 32),
            ("Nutrición y cuidado diario", "Botella reutilizable 600 ml", "Accesorio reutilizable para llevar agua y apoyar hábitos de hidratación.", 15900, 0, 18),
            ("Primeros auxilios", "Gasa estéril x 10", "Paquete de gasas estériles para botiquín. Usa material estéril según las indicaciones del empaque.", 7500, 0, 35),
            ("Dispositivos de salud", "Oxímetro de pulso", "Dispositivo de medición orientativa de saturación de oxígeno y pulso. No sustituye una evaluación médica.", 68900, 1, 8),
            ("Cuidado personal", "Jabón líquido 250 ml", "Jabón líquido para la limpieza diaria. Consulta la etiqueta si tienes sensibilidad a algún ingrediente.", 10900, 0, 30),
            ("Cuidado de la piel", "Bálsamo labial hidratante", "Bálsamo para el cuidado de los labios. Verifica la lista de ingredientes antes de usar.", 8900, 0, 25),
        ]
        for category, name, desc, price, featured, stock in demo:
            conn.execute(
                """INSERT INTO products(category_id,name,description,price,image,active,featured,stock,requires_prescription,created_at)
                   VALUES(?,?,?,?,'',1,?,?,0,?)""",
                (cat.get(category), name, desc, price, featured, stock, now_iso()),
            )

    defaults = {
        "business_notice": "Catálogo de cuidado personal y bienestar. Consulta a un profesional de salud para orientación individualizada.",
        "store_status": "open",
    }
    for key, value in defaults.items():
        conn.execute("INSERT OR IGNORE INTO site_settings(key,value) VALUES(?,?)", (key, value))
    conn.commit()
    conn.close()


def bootstrap_admin():
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        return
    conn = db()
    existing = conn.execute("SELECT id,is_admin FROM users WHERE email=?", (ADMIN_EMAIL,)).fetchone()
    if not existing:
        conn.execute(
            """INSERT INTO users(first_name,last_name,email,password_hash,is_admin,is_active,created_at)
               VALUES(?,?,?,?,1,1,?)""",
            ("Admin", "NexaFarma", ADMIN_EMAIL, generate_password_hash(ADMIN_PASSWORD), now_iso()),
        )
        conn.commit()
    elif not existing["is_admin"]:
        conn.execute(
            "UPDATE users SET is_admin=1,password_hash=?,is_active=1,updated_at=? WHERE id=?",
            (generate_password_hash(ADMIN_PASSWORD), now_iso(), existing["id"]),
        )
        conn.commit()
    conn.close()


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE id=? AND is_active=1", (uid,)).fetchone()
    conn.close()
    return user


def audit(action, entity=None, entity_id=None, details=None, user_id=None):
    try:
        conn = db()
        conn.execute(
            "INSERT INTO audit_log(user_id,action,entity,entity_id,details,created_at) VALUES(?,?,?,?,?,?)",
            (user_id or session.get("user_id"), action, entity, entity_id, json.dumps(details or {}, ensure_ascii=False), now_iso()),
        )
        conn.commit()
        conn.close()
    except Exception:
        app.logger.exception("No se pudo escribir audit log")


def admin_required(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user or not user["is_admin"]:
            flash("Debes iniciar sesión como administrador.", "error")
            return redirect(url_for("login", next=request.path))
        return func(*args, **kwargs)
    return wrapped


def login_required(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login", next=request.path))
        return func(*args, **kwargs)
    return wrapped


def password_strength_error(password):
    if len(password) < 12:
        return "Usa al menos 12 caracteres."
    groups = sum((bool(re.search(pattern, password)) for pattern in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]")))
    if groups < 3:
        return "Combina al menos 3 de estos grupos: minúsculas, mayúsculas, números y símbolos."
    if re.search(r"(.)\1{3,}", password) or password.lower() in {"password123!", "nexafarma2026!"}:
        return "Evita repeticiones o contraseñas previsibles."
    return ""


def csrf_token_value():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def verify_csrf():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    if request.path in {"/webhooks/wompi", "/webhooks/mercadopago"}:
        return
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRFToken")
    expected = session.get("csrf_token")
    if not sent or not expected or not secrets.compare_digest(sent, expected):
        abort(400, description="Token de seguridad inválido o expirado.")


@app.before_request
def global_guards():
    verify_csrf()
    if session.get("must_change_password") and request.endpoint not in {"change_password", "logout", "static"}:
        return redirect(url_for("change_password"))


def safe_site_settings():
    settings = {"store_status": "open", "business_notice": "Catálogo de cuidado personal, higiene, botiquín y bienestar. Consulta a un profesional para orientación sanitaria individual."}
    try:
        conn = db()
        rows = conn.execute("SELECT key,value FROM site_settings").fetchall()
        conn.close()
        settings.update({r["key"]: r["value"] for r in rows})
    except Exception:
        app.logger.exception("No se pudieron cargar site_settings")
    return settings


@app.context_processor
def ctx():
    user = None
    favorite_ids = set()
    try:
        user = current_user()
        if user:
            conn = db()
            favorite_ids = {row[0] for row in conn.execute("SELECT product_id FROM favorites WHERE user_id=?", (user["id"],))}
            conn.close()
    except Exception:
        app.logger.exception("No se pudo construir el contexto de usuario")
        user = None
    try:
        cart_count = sum(max(0, int(x.get("qty", 0))) for x in session.get("cart", []) if isinstance(x, dict))
    except Exception:
        cart_count = 0
    return {
        "user": user,
        "config": CONFIG,
        "site_settings": safe_site_settings(),
        "csrf_token": csrf_token_value(),
        "cart_count": cart_count,
        "favorite_ids": favorite_ids,
        "google_oauth_ready": google_oauth_ready(),
        "chatbot_ai": bool(GROQ_API_KEY),
        "agent_ai": bool(GROQ_API_KEY),
        "agent_model": AGENT_MODEL,
        "mail_admin": MAIL_ADMIN,
        "wompi_enabled": wompi_is_available(),
        "wompi_temporarily_unavailable": WOMPI_TEMPORARILY_UNAVAILABLE,
        "mercadopago_enabled": mercadopago_is_configured(),
        "wompi_methods": WOMPI_METHODS,
        "payment_es": PAYMENT_ES,
        "payment_status_es": PAYMENT_STATUS_ES,
        "email_provider": email_provider_name(),
        "status_es": STATUS_ES,
        "year": datetime.now().year,
    }


def cart_items_from_session():
    raw_items = session.get("cart", [])
    if not isinstance(raw_items, list):
        session["cart"] = []
        return []
    normalized = []
    conn = db()
    changed = False
    for raw in raw_items:
        try:
            pid = int(raw.get("product_id"))
            qty = max(1, min(20, int(raw.get("qty", 1))))
        except (TypeError, ValueError, AttributeError):
            changed = True
            continue
        product_row = conn.execute("SELECT * FROM products WHERE id=? AND active=1", (pid,)).fetchone()
        if not product_row or int(product_row["stock"] or 0) <= 0 or int(product_row["requires_prescription"] or 0):
            changed = True
            continue
        try:
            size_id = int(raw.get("size_id")) if raw.get("size_id") else None
        except (TypeError, ValueError):
            size_id = None
        size_row = conn.execute("SELECT id,name,price FROM sizes WHERE id=? AND active=1", (size_id,)).fetchone() if size_id else None
        if not size_row:
            size_row = conn.execute("SELECT id,name,price FROM sizes WHERE name=? AND active=1 ORDER BY id LIMIT 1", (raw.get("size", "Pequeño"),)).fetchone()
        topping_ids = raw.get("topping_ids", [])
        if not isinstance(topping_ids, list):
            topping_ids = []
        safe_ids = []
        for x in topping_ids:
            try:
                safe_ids.append(int(x))
            except (TypeError, ValueError):
                pass
        toppings = []
        if safe_ids:
            placeholders = ",".join("?" for _ in safe_ids)
            toppings = conn.execute(
                f"SELECT id,name,price FROM toppings WHERE active=1 AND id IN ({placeholders}) ORDER BY id", safe_ids
            ).fetchall()
        base = int(product_row["price"])
        size_price = int(size_row["price"]) if size_row else 0
        topping_price = sum(int(t["price"]) for t in toppings)
        unit = base + size_price + topping_price
        item = {
            "product_id": pid,
            "qty": qty,
            "name": product_row["name"],
            "base": base,
            "size_id": size_row["id"] if size_row else None,
            "size": size_row["name"] if size_row else "",
            "size_price": size_price,
            "flavor": str(raw.get("flavor", "")).strip()[:120],
            "topping_ids": [t["id"] for t in toppings],
            "toppings": [dict(t) for t in toppings],
            "unit": unit,
            "stock": int(product_row["stock"] or 0),
        }
        if item != raw:
            changed = True
        normalized.append(item)
    conn.close()
    if changed:
        session["cart"] = normalized
        session.modified = True
    return normalized


def cart_subtotal(items=None):
    items = items if items is not None else cart_items_from_session()
    return sum(int(i["unit"]) * int(i["qty"]) for i in items)


def delivery_for(amount, delivery_method="domicilio"):
    if delivery_method == "recoger":
        return 0
    if CONFIG["free_delivery_from"] and amount >= CONFIG["free_delivery_from"]:
        return 0
    return CONFIG["delivery_fee"]


def gen_code(prefix=None):
    prefix = prefix or CONFIG["code_prefix"]
    for _ in range(30):
        code = prefix + "-" + secrets.token_hex(3).upper()
        conn = db()
        exists = conn.execute("SELECT 1 FROM coupons WHERE code=?", (code,)).fetchone()
        conn.close()
        if not exists:
            return code
    return prefix + "-" + secrets.token_hex(6).upper()


def validate_coupon(code, user_id, subtotal, connection=None):
    if not code:
        return None, 0, ""
    owns_connection = connection is None
    connection = connection or db()
    row = connection.execute(
        "SELECT * FROM coupons WHERE code=? AND active=1 AND (user_id IS NULL OR user_id=?)",
        (code.strip().upper(), user_id),
    ).fetchone()
    if owns_connection:
        connection.close()
    if not row:
        return None, 0, "El código no existe o no está habilitado."
    expires = parse_dt(row["expires_at"])
    if expires and expires < datetime.now(timezone.utc):
        return None, 0, "El código ya venció."
    if int(row["used"] or 0) or int(row["uses_count"] or 0) >= int(row["max_uses"] or 1):
        return None, 0, "El código ya fue utilizado."
    if subtotal < int(row["min_purchase"] or 0):
        return None, 0, f"La compra mínima para este cupón es {money(row['min_purchase'])}."
    discount = round(subtotal * int(row["percent"]) / 100)
    return row, discount, "Cupón aplicado correctamente."


def generate_order_no():
    conn = db()
    for _ in range(40):
        order_no = "FR-" + datetime.now().strftime("%y%m%d") + "-" + secrets.token_hex(3).upper()
        if not conn.execute("SELECT 1 FROM orders WHERE order_no=?", (order_no,)).fetchone():
            conn.close()
            return order_no
    conn.close()
    return "NF-" + secrets.token_hex(8).upper()


def wompi_is_configured():
    return bool(WOMPI_PUBLIC_KEY and WOMPI_INTEGRITY_SECRET and WOMPI_EVENTS_SECRET)


def wompi_is_available():
    return wompi_is_configured() and not WOMPI_TEMPORARILY_UNAVAILABLE


def mercadopago_is_configured():
    parsed = urlparse(BASE_URL)
    return bool(MERCADOPAGO_ACCESS_TOKEN and MERCADOPAGO_WEBHOOK_SECRET and (parsed.scheme == "https" or parsed.hostname in {"localhost", "127.0.0.1"}))


def wompi_signature(reference, amount):
    if not WOMPI_INTEGRITY_SECRET:
        return ""
    amount_cents = int(amount) * 100
    raw = f"{reference}{amount_cents}COP{WOMPI_INTEGRITY_SECRET}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def dotted_value(data, path):
    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return ""
    return "" if current is None else current


def verify_wompi_event(payload):
    if not WOMPI_EVENTS_SECRET:
        return False
    signature = payload.get("signature") or {}
    properties = signature.get("properties") or []
    checksum = signature.get("checksum") or request.headers.get("X-Event-Checksum", "")
    timestamp = payload.get("timestamp")
    if not properties or not checksum or timestamp is None:
        return False
    concatenated = "".join(str(dotted_value(payload.get("data") or {}, p)) for p in properties)
    expected = hashlib.sha256(f"{concatenated}{timestamp}{WOMPI_EVENTS_SECRET}".encode("utf-8")).hexdigest()
    return secrets.compare_digest(expected.lower(), str(checksum).lower())


LAST_EMAIL_ERROR = ""


def smtp_configured():
    return bool(SMTP_HOST and SMTP_FROM)


def resend_configured():
    return bool(RESEND_API_KEY and RESEND_FROM)


def email_provider_name():
    """Devuelve el proveedor de correo seleccionado y si tiene configuración mínima."""
    provider = EMAIL_PROVIDER if EMAIL_PROVIDER in {"smtp", "resend", "gmail_api"} else "auto"
    if provider == "gmail_api":
        return "Gmail API" if gmail_api_configured() else "Gmail API (sin configurar)"
    if provider == "resend":
        return "Resend API" if resend_configured() else "Resend (sin configurar)"
    if provider == "smtp":
        return "SMTP" if smtp_configured() else "SMTP (sin configurar)"
    if resend_configured():
        return "Resend API"
    if gmail_api_configured():
        return "Gmail API"
    if smtp_configured():
        return "SMTP"
    return "Sin proveedor"


def _send_email_resend(to_email, subject, html_body, text_body=None, attachment_bytes=None, attachment_name=None):
    global LAST_EMAIL_ERROR
    if not resend_configured():
        return False
    payload = {
        "from": _branded_sender(RESEND_FROM),
        "to": [to_email],
        "subject": subject,
        "html": html_body,
        "text": text_body or re.sub(r"<[^>]+>", " ", html_body),
    }
    if attachment_bytes and attachment_name:
        payload["attachments"] = [{
            "filename": attachment_name,
            "content": base64.b64encode(attachment_bytes).decode("ascii"),
        }]
    try:
        body = json.dumps(payload).encode("utf-8")
        req = Request(
            "https://api.resend.com/emails",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
                "User-Agent": "NexaFarma-Store/1.0",
            },
        )
        with urlopen(req, timeout=20) as response:
            if 200 <= response.status < 300:
                LAST_EMAIL_ERROR = ""
                return True
            LAST_EMAIL_ERROR = f"Resend HTTP {response.status}"
    except HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "ignore")[:500]
        except Exception:
            pass
        LAST_EMAIL_ERROR = f"Resend HTTP {exc.code}: {detail}".strip()
        app.logger.error("Resend rechazó el correo: %s", LAST_EMAIL_ERROR)
    except (URLError, TimeoutError, OSError) as exc:
        LAST_EMAIL_ERROR = f"Resend conexión: {exc}"
        app.logger.error("Resend no pudo conectar: %s", LAST_EMAIL_ERROR)
    except Exception as exc:
        LAST_EMAIL_ERROR = f"Resend: {exc}"
        app.logger.exception("Error inesperado con Resend")
    return False


def _sender_email_and_domain(sender):
    """Extrae email y dominio del remitente para el diagnóstico del panel."""
    value = str(sender or "").strip()
    match = re.search(r"<([^<>]+)>", value)
    email = (match.group(1) if match else value).strip().lower()
    domain = email.rsplit("@", 1)[-1] if "@" in email else ""
    return email, domain


def _branded_sender(sender):
    """Keeps the provider address while showing the configured store name to recipients."""
    _, address = parseaddr(str(sender or "").strip())
    return formataddr((EMAIL_FROM_NAME, address)) if address else str(sender or "").strip()


def gmail_api_configured():
    """Comprueba que estén presentes las credenciales necesarias para Gmail API."""
    return bool(GMAIL_API_CLIENT_ID and GMAIL_API_CLIENT_SECRET and GMAIL_API_REFRESH_TOKEN and GMAIL_API_FROM)


def _gmail_api_access_token():
    """Renueva el token de acceso usando el refresh token; nunca registra secretos."""
    global LAST_EMAIL_ERROR
    if not gmail_api_configured():
        LAST_EMAIL_ERROR = (
            "Gmail API no está configurada. Faltan GMAIL_API_CLIENT_ID, "
            "GMAIL_API_CLIENT_SECRET, GMAIL_API_REFRESH_TOKEN o GMAIL_API_FROM."
        )
        return None
    try:
        response = requests.post(
            GMAIL_API_TOKEN_URL,
            data={
                "client_id": GMAIL_API_CLIENT_ID,
                "client_secret": GMAIL_API_CLIENT_SECRET,
                "refresh_token": GMAIL_API_REFRESH_TOKEN,
                "grant_type": "refresh_token",
            },
            timeout=15,
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code != 200 or not payload.get("access_token"):
            error = payload.get("error_description") or payload.get("error") or "respuesta OAuth no válida"
            LAST_EMAIL_ERROR = f"Gmail OAuth HTTP {response.status_code}: {str(error)[:300]}"
            app.logger.error("Gmail API no pudo renovar credenciales: %s", LAST_EMAIL_ERROR)
            return None
        LAST_EMAIL_ERROR = ""
        return payload["access_token"]
    except requests.RequestException as exc:
        LAST_EMAIL_ERROR = f"Gmail OAuth HTTPS: {type(exc).__name__}: {exc}"
        app.logger.error("Gmail API no pudo conectar con OAuth: %s", LAST_EMAIL_ERROR)
        return None
    except Exception as exc:
        LAST_EMAIL_ERROR = f"Gmail OAuth: {type(exc).__name__}: {exc}"
        app.logger.exception("Error inesperado al renovar token de Gmail API")
        return None


def _send_email_gmail_api(to_email, subject, html_body, text_body=None, attachment_bytes=None, attachment_name=None, reply_to=None):
    """Envía correo por Gmail API HTTPS, admitiendo HTML, texto, Reply-To y adjuntos."""
    global LAST_EMAIL_ERROR
    access_token = _gmail_api_access_token()
    if not access_token:
        return False
    try:
        clean_subject = re.sub(r"[\r\n]+", " ", str(subject or "NexaFarma"))[:200]
        msg = MIMEMultipart("mixed")
        msg["Subject"] = clean_subject
        msg["From"] = _branded_sender(GMAIL_API_FROM)
        msg["To"] = to_email
        effective_reply_to = (reply_to or GMAIL_API_REPLY_TO).strip()
        if effective_reply_to and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", effective_reply_to):
            msg["Reply-To"] = effective_reply_to

        alternative = MIMEMultipart("alternative")
        alternative.attach(MIMEText(text_body or re.sub(r"<[^>]+>", " ", html_body or ""), "plain", "utf-8"))
        alternative.attach(MIMEText(html_body or "", "html", "utf-8"))
        msg.attach(alternative)
        if attachment_bytes and attachment_name:
            safe_name = secure_filename(attachment_name) or "NexaFarma-adjunto.pdf"
            part = MIMEApplication(attachment_bytes, _subtype="pdf" if safe_name.lower().endswith(".pdf") else "octet-stream", Name=safe_name)
            part["Content-Disposition"] = f'attachment; filename="{safe_name}"'
            msg.attach(part)

        raw_message = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii").rstrip("=")
        response = requests.post(
            GMAIL_API_SEND_URL,
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={"raw": raw_message},
            timeout=20,
        )
        try:
            result = response.json()
        except ValueError:
            result = {}
        if response.status_code in (200, 202) and result.get("id"):
            LAST_EMAIL_ERROR = ""
            app.logger.info("Correo aceptado por Gmail API: destino=%s, asunto=%s", to_email, clean_subject)
            return True

        detail = result.get("error", {}).get("message", "respuesta no válida") if isinstance(result.get("error"), dict) else "respuesta no válida"
        LAST_EMAIL_ERROR = f"Gmail API HTTP {response.status_code}: {str(detail)[:350]}"
        app.logger.error("Gmail API rechazó el correo: %s", LAST_EMAIL_ERROR)
    except requests.RequestException as exc:
        LAST_EMAIL_ERROR = f"Gmail API HTTPS: {type(exc).__name__}: {exc}"
        app.logger.error("Gmail API no pudo conectar: %s", LAST_EMAIL_ERROR)
    except Exception as exc:
        LAST_EMAIL_ERROR = f"Gmail API: {type(exc).__name__}: {exc}"
        app.logger.exception("Error inesperado enviando correo por Gmail API")
    return False


def _send_email_smtp(to_email, subject, html_body, text_body=None, attachment_bytes=None, attachment_name=None):
    global LAST_EMAIL_ERROR
    if not smtp_configured():
        return False
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = _branded_sender(SMTP_FROM)
    msg["To"] = to_email
    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText(text_body or re.sub(r"<[^>]+>", " ", html_body), "plain", "utf-8"))
    alternative.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(alternative)
    if attachment_bytes and attachment_name:
        part = MIMEApplication(attachment_bytes, _subtype="pdf" if attachment_name.lower().endswith(".pdf") else "octet-stream", Name=attachment_name)
        part["Content-Disposition"] = f'attachment; filename="{attachment_name}"'
        msg.attach(part)
    try:
        if SMTP_USE_SSL:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20, context=ssl.create_default_context()) as server:
                if SMTP_USERNAME and SMTP_PASSWORD:
                    server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.sendmail(parseaddr(SMTP_FROM)[1] or SMTP_FROM, [to_email], msg.as_string())
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
                server.ehlo()
                if SMTP_USE_TLS:
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                if SMTP_USERNAME and SMTP_PASSWORD:
                    server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.sendmail(parseaddr(SMTP_FROM)[1] or SMTP_FROM, [to_email], msg.as_string())
        LAST_EMAIL_ERROR = ""
        return True
    except smtplib.SMTPException as exc:
        LAST_EMAIL_ERROR = f"SMTP: {exc}"
        app.logger.error("SMTP rechazó el correo a %s: %s", to_email, LAST_EMAIL_ERROR)
    except (OSError, TimeoutError) as exc:
        LAST_EMAIL_ERROR = f"SMTP conexión: {exc}"
        app.logger.error("SMTP no pudo conectar: %s", LAST_EMAIL_ERROR)
    except Exception as exc:
        LAST_EMAIL_ERROR = f"SMTP: {exc}"
        app.logger.exception("Error inesperado enviando correo a %s", to_email)
    return False


def send_email(to_email, subject, html_body, text_body=None, attachment_bytes=None, attachment_name=None, reply_to=None):
    """Punto único de envío para notificaciones; Gmail API usa HTTPS y evita SMTP."""
    global LAST_EMAIL_ERROR
    if not to_email or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", str(to_email).strip()):
        LAST_EMAIL_ERROR = "Correo destinatario inválido."
        return False
    to_email = str(to_email).strip()
    provider = EMAIL_PROVIDER if EMAIL_PROVIDER in {"smtp", "resend", "gmail_api"} else "auto"

    if provider == "gmail_api":
        return _send_email_gmail_api(to_email, subject, html_body, text_body, attachment_bytes, attachment_name, reply_to)
    if provider == "resend":
        return _send_email_resend(to_email, subject, html_body, text_body, attachment_bytes, attachment_name)
    if provider == "smtp":
        return _send_email_smtp(to_email, subject, html_body, text_body, attachment_bytes, attachment_name)

    # En modo auto, intenta proveedores HTTPS; solo usa SMTP como último recurso.
    if resend_configured() and _send_email_resend(to_email, subject, html_body, text_body, attachment_bytes, attachment_name):
        return True
    if gmail_api_configured() and _send_email_gmail_api(to_email, subject, html_body, text_body, attachment_bytes, attachment_name, reply_to):
        return True
    if smtp_configured() and _send_email_smtp(to_email, subject, html_body, text_body, attachment_bytes, attachment_name):
        return True

    if not LAST_EMAIL_ERROR:
        LAST_EMAIL_ERROR = "No hay proveedor de correo configurado. Configura Gmail API, Resend API o SMTP."
    app.logger.warning("Correo no enviado: %s (destino=%s, asunto=%s)", LAST_EMAIL_ERROR, to_email, subject)
    return False


def email_diagnostics():
    """Comprueba configuración y autenticación del proveedor seleccionado sin enviar correo."""
    provider = EMAIL_PROVIDER if EMAIL_PROVIDER in {"smtp", "resend", "gmail_api"} else "auto"
    use_resend = provider == "resend" or (provider == "auto" and resend_configured())
    use_gmail = provider == "gmail_api" or (provider == "auto" and not resend_configured() and gmail_api_configured())
    use_smtp = provider == "smtp" or (provider == "auto" and not resend_configured() and not gmail_api_configured() and smtp_configured())
    chosen_sender = GMAIL_API_FROM if use_gmail else (RESEND_FROM if use_resend else SMTP_FROM)
    _, sender_domain = _sender_email_and_domain(chosen_sender)
    result = {
        "provider": email_provider_name(),
        "smtp_configured": smtp_configured(),
        "resend_configured": resend_configured(),
        "gmail_api_configured": gmail_api_configured(),
        "smtp_connection": False,
        "resend_api": False,
        "gmail_api": False,
        "resend_sender_domain": sender_domain if use_resend else "",
        "resend_sender_verified": False,
        "resend_sandbox_sender": use_resend and sender_domain == "resend.dev",
        "error": "",
    }

    if use_gmail:
        if not gmail_api_configured():
            result["error"] = "Gmail API seleccionada pero faltan una o más variables GMAIL_API_*."
        elif _gmail_api_access_token():
            result["gmail_api"] = True
        else:
            result["error"] = LAST_EMAIL_ERROR

    if use_resend:
        if not resend_configured():
            result["error"] = "Faltan RESEND_API_KEY y/o RESEND_FROM en las variables de entorno."
        else:
            try:
                req = Request(
                    "https://api.resend.com/domains",
                    method="GET",
                    headers={"Authorization": f"Bearer {RESEND_API_KEY}", "User-Agent": "NexaFarma-Store/1.1"},
                )
                with urlopen(req, timeout=8) as response:
                    result["resend_api"] = 200 <= response.status < 300
                    body = json.loads(response.read().decode("utf-8", "ignore") or "{}")
                domains = body.get("data", []) if isinstance(body, dict) else []
                for domain in domains:
                    if str(domain.get("name", "")).lower() == sender_domain and str(domain.get("status", "")).lower() == "verified":
                        result["resend_sender_verified"] = True
                        break
                if result["resend_api"] and not result["resend_sender_verified"] and not result["resend_sandbox_sender"]:
                    result["error"] = "La API de Resend responde, pero el dominio del remitente no aparece verificado."
                elif result["resend_sandbox_sender"]:
                    result["error"] = "Remitente de pruebas de Resend; no es el envío transaccional definitivo a clientes."
            except HTTPError as exc:
                result["error"] = f"Resend HTTP {exc.code}: revisa la API key y permisos."
            except Exception as exc:
                result["error"] = f"Resend HTTPS: {type(exc).__name__}: {exc}"

    if use_smtp:
        try:
            if SMTP_USE_SSL:
                with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=8, context=ssl.create_default_context()) as server:
                    server.ehlo()
                    if SMTP_USERNAME and SMTP_PASSWORD:
                        server.login(SMTP_USERNAME, SMTP_PASSWORD)
            else:
                with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=8) as server:
                    server.ehlo()
                    if SMTP_USE_TLS:
                        server.starttls(context=ssl.create_default_context())
                        server.ehlo()
                    if SMTP_USERNAME and SMTP_PASSWORD:
                        server.login(SMTP_USERNAME, SMTP_PASSWORD)
            result["smtp_connection"] = True
        except Exception as exc:
            result["error"] = f"SMTP: {type(exc).__name__}: {exc}"

    if not use_resend and not use_gmail and not use_smtp:
        result["error"] = "Configura EMAIL_PROVIDER y las variables del proveedor de correo."
    return result


def invoice_html(order, items):
    rows = "".join(
        f"<tr><td>{html.escape(str(i['quantity']))} × {html.escape(i['product_name'])}<br><small>{html.escape(i['size'] or '')} {('· ' + html.escape(i['toppings'])) if i['toppings'] else ''}</small></td><td style='text-align:right'>{money(i['unit_price'] * i['quantity'])}</td></tr>"
        for i in items
    )
    return f"""
    <div style=\"font-family:Arial,sans-serif;background:#f2faf8;padding:28px;color:#193536\">
      <div style=\"max-width:700px;margin:auto;background:#fff;border:1px solid #d6e9e5;border-radius:22px;overflow:hidden\">
        <div style=\"padding:28px;background:linear-gradient(135deg,#e4f5f2,#e9f2ff)\">
          <div style=\"font-size:28px;font-weight:800;color:#087f83\">NexaFarma</div>
          <div style=\"margin-top:5px;color:#526b6b\">Comprobante de compra · #{html.escape(order['order_no'])}</div>
        </div>
        <div style=\"padding:28px\">
          <p>Hola <b>{html.escape(order['customer_name'])}</b>, gracias por comprar en NexaFarma.</p>
          <p style=\"color:#526b6b\">Fecha: {html.escape(order['created_at'][:19].replace('T',' '))}<br>Estado: <b>{html.escape(STATUS_ES.get(order['status'], order['status']))}</b><br>Pago: <b>{html.escape(PAYMENT_ES.get(order['payment_method'], order['payment_method']))}</b></p>
          <table width=\"100%\" cellpadding=\"10\" cellspacing=\"0\" style=\"border-collapse:collapse;margin:20px 0\"><thead><tr style=\"border-bottom:1px solid #d6e9e5\"><th align=\"left\">Producto</th><th align=\"right\">Total</th></tr></thead><tbody>{rows}</tbody></table>
          <div style=\"border-top:1px solid #d6e9e5;padding-top:16px\"><p style=\"display:flex;justify-content:space-between\">Subtotal <b>{money(order['subtotal'])}</b></p><p style=\"display:flex;justify-content:space-between\">Descuento <b>-{money(order['discount'])}</b></p><p style=\"display:flex;justify-content:space-between\">Domicilio <b>{money(order['delivery_fee'])}</b></p><p style=\"font-size:22px;display:flex;justify-content:space-between\">Total <b>{money(order['total'])}</b></p></div>
          <div style=\"margin-top:20px;padding:16px;border-radius:14px;background:#e4f5f2\"><b>Entrega</b><br>{html.escape(order['address'])}{(', ' + html.escape(order['neighborhood'])) if order['neighborhood'] else ''}{(', ' + html.escape(order['city'])) if order['city'] else ''}</div>
          <p style=\"color:#526b6b;margin-top:24px\">Este correo es el comprobante electrónico de la compra. Para dudas, responde a este correo o escribe por WhatsApp.</p>
        </div>
      </div>
    </div>"""


def order_status_email(order_no, status):
    conn = db()
    order = conn.execute("SELECT * FROM orders WHERE order_no=?", (order_no,)).fetchone()
    conn.close()
    if not order or not order["email"]:
        return False
    labels = {
        "received": ("Pedido recibido", "Tu pedido ya está registrado y lo tenemos en cola."),
        "paid": ("Pago confirmado", "Tu pago fue confirmado y el pedido puede pasar a preparación."),
        "preparing": ("Tu pedido está en preparación", "Nuestro equipo ya está preparando tu pedido."),
        "on_the_way": ("Tu pedido va en camino", "Tu pedido salió y se dirige a la dirección indicada."),
        "delivering": ("Tu pedido va en camino", "Tu pedido está en ruta de entrega."),
        "delivered": ("Pedido entregado", "Gracias por utilizar NexaFarma. Esperamos que disfrutes tu compra."),
        "cancelled": ("Pedido cancelado", "Tu pedido fue cancelado. Puedes contactarnos si necesitas revisar el caso."),
    }
    title, message = labels.get(status, ("Actualización de pedido", STATUS_ES.get(status, status)))
    html_body = f"""<div style='font-family:Arial,sans-serif;background:#f2faf8;padding:28px'><div style='max-width:620px;margin:auto;background:#fff;border:1px solid #d6e9e5;border-radius:24px;padding:30px'><div style='font-size:12px;letter-spacing:.14em;font-weight:800;color:#087f83'>NEXAFARMA · ACTUALIZACIÓN</div><h1 style='margin:8px 0 14px;color:#261b21'>{html.escape(title)}</h1><p>Hola <b>{html.escape(order['customer_name'])}</b>,</p><p>{html.escape(message)}</p><div style='background:#e4f5f2;border-radius:16px;padding:16px;margin:18px 0'><b>Pedido #{html.escape(order['order_no'])}</b><br/>Estado: {html.escape(STATUS_ES.get(status,status))}<br/>Total: {money(order['total'])}</div><p style='color:#526b6b'>Puedes entrar a tu cuenta para ver el seguimiento y comprobante.</p></div></div>"""
    return send_email(order["email"], f"NexaFarma · {title} · #{order_no}", html_body)


def admin_new_order_email(order_no):
    """Notifica al administrador sobre un pedido recién creado, sin bloquear el pedido si falla el correo."""
    if not MAIL_ADMIN:
        return False
    conn = db()
    order = conn.execute("SELECT * FROM orders WHERE order_no=?", (order_no,)).fetchone()
    conn.close()
    if not order:
        return False
    body = f"""<div style='font-family:Arial,sans-serif;background:#f2faf8;padding:24px'><div style='max-width:620px;margin:auto;background:#fff;padding:26px;border-radius:18px;border:1px solid #d6e9e5'><h2 style='color:#087f83'>Nuevo pedido en NexaFarma</h2><p><b>Pedido:</b> #{html.escape(order['order_no'])}</p><p><b>Cliente:</b> {html.escape(order['customer_name'])}</p><p><b>Total:</b> {money(order['total'])}</p><p><b>Pago:</b> {html.escape(PAYMENT_ES.get(order['payment_method'], order['payment_method']))}</p><p><b>Estado:</b> {html.escape(STATUS_ES.get(order['status'], order['status']))}</p><p>Entra al panel de administración para revisar el pedido.</p></div></div>"""
    return send_email(MAIL_ADMIN, f"NexaFarma · Nuevo pedido #{order_no}", body)


def payment_status_email(order_no, payment_status):
    """Notifica estados finales o fallidos de pago por correo al cliente."""
    conn = db()
    order = conn.execute("SELECT * FROM orders WHERE order_no=?", (order_no,)).fetchone()
    conn.close()
    if not order or not order['email']:
        return False
    labels = {
        'approved': ('Pago confirmado', 'Wompi confirmó el pago de tu pedido.'),
        'declined': ('Pago no aprobado', 'El pago no fue aprobado. Puedes revisar las opciones disponibles e intentar de nuevo si el pedido sigue habilitado.'),
        'error': ('No se pudo completar el pago', 'El proveedor reportó un error al procesar el pago. Revisa el estado del pedido antes de volver a intentarlo.'),
        'voided': ('Pago anulado', 'El intento de pago fue anulado. Si deseas comprar, revisa el estado del pedido en tu cuenta.'),
        'refunded': ('Reembolso registrado', 'El reembolso de tu pedido fue registrado por nuestro equipo. El tiempo de reflejo depende del medio de pago.'),
        'pending': ('Pago pendiente', 'El pago todavía está pendiente de confirmación.'),
    }
    title, text = labels.get(payment_status, ('Actualización de pago', 'El estado del pago de tu pedido cambió.'))
    body = f"""<div style='font-family:Arial,sans-serif;background:#f2faf8;padding:24px'><div style='max-width:620px;margin:auto;background:#fff;padding:26px;border-radius:18px;border:1px solid #d6e9e5'><h2 style='color:#087f83'>{html.escape(title)}</h2><p>Hola {html.escape(order['customer_name'])},</p><p>{html.escape(text)}</p><div style='background:#e4f5f2;padding:14px;border-radius:12px'><b>Pedido #{html.escape(order['order_no'])}</b><br/>Total: {money(order['total'])}<br/>Estado del pago: {html.escape(payment_status.upper())}</div><p>Puedes consultar los detalles entrando a tu cuenta NexaFarma.</p></div></div>"""
    return send_email(order['email'], f"NexaFarma · {title} · #{order_no}", body)


def contact_ack_email(name, email, subject):
    """Confirma al cliente que su mensaje de contacto fue guardado."""
    body = f"""<div style='font-family:Arial,sans-serif;background:#f2faf8;padding:24px'><div style='max-width:620px;margin:auto;background:#fff;padding:26px;border-radius:18px;border:1px solid #d6e9e5'><h2 style='color:#087f83'>Recibimos tu mensaje</h2><p>Hola {html.escape(name)}, gracias por comunicarte con NexaFarma.</p><p>Tu mensaje{(' sobre “' + html.escape(subject) + '”') if subject else ''} quedó registrado. Nuestro equipo lo revisará.</p><p>Si necesitas una respuesta urgente, puedes escribirnos por WhatsApp.</p></div></div>"""
    return send_email(email, 'NexaFarma · Recibimos tu mensaje', body)


def invoice_pdf(order, items):
    if not SimpleDocTemplate:
        return None
    buffer = io.BytesIO()
    styles = getSampleStyleSheet()
    title = ParagraphStyle("title", parent=styles["Title"], alignment=TA_CENTER, fontSize=20, textColor=pdf_colors.HexColor("#087f83"))
    normal = ParagraphStyle("normal", parent=styles["BodyText"], fontSize=9, leading=12)
    right = ParagraphStyle("right", parent=normal, alignment=TA_RIGHT)
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=18*mm, leftMargin=18*mm, topMargin=18*mm, bottomMargin=18*mm)
    story = [Paragraph("NEXAFARMA", title), Spacer(1, 6), Paragraph(f"Comprobante de compra · #{html.escape(order['order_no'])}", normal), Spacer(1, 14)]
    data = [[Paragraph("Producto", normal), Paragraph("Total", right)]]
    for item in items:
        detail = f"{item['quantity']} × {html.escape(item['product_name'])}<br/><font size='8'>{html.escape(item['size'] or '')} {('· ' + html.escape(item['toppings'])) if item['toppings'] else ''}</font>"
        data.append([Paragraph(detail, normal), Paragraph(money(item['unit_price']*item['quantity']), right)])
    data += [[Paragraph("Subtotal", normal), Paragraph(money(order["subtotal"]), right)], [Paragraph("Descuento", normal), Paragraph("-" + money(order["discount"]), right)], [Paragraph("Domicilio", normal), Paragraph(money(order["delivery_fee"]), right)], [Paragraph("TOTAL", normal), Paragraph(money(order["total"]), right)]]
    table = Table(data, colWidths=[120*mm, 45*mm])
    table.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.35, pdf_colors.HexColor("#d6e9e5")),
        ("BACKGROUND", (0,0), (-1,0), pdf_colors.HexColor("#e4f5f2")),
        ("FONTNAME", (0,-4), (-1,-1), "Helvetica-Bold"),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("BOTTOMPADDING", (0,0), (-1,-1), 7),
        ("TOPPADDING", (0,0), (-1,-1), 7),
    ]))
    story.append(table)
    story.append(Spacer(1, 14))
    story.append(Paragraph(f"Cliente: {html.escape(order['customer_name'])}<br/>Correo: {html.escape(order['email'] or '')}<br/>Dirección: {html.escape(order['address'])}", normal))
    doc.build(story)
    return buffer.getvalue()


def send_order_invoice(order_no, force=False):
    conn = db()
    try:
        # Serializa reintentos concurrentes y evita volver a enviar al avanzar el estado del pedido.
        conn.execute("BEGIN IMMEDIATE")
        order = conn.execute("SELECT * FROM orders WHERE order_no=?", (order_no,)).fetchone()
        if not order or not order["email"]:
            conn.rollback()
            return False
        if order["invoice_email_sent_at"] and not force:
            conn.rollback()
            return True
        items = conn.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (order["id"],)).fetchall()
        pdf = invoice_pdf(order, items)
        subject = f"NexaFarma · Factura / comprobante de compra #{order_no}"
        sent = send_email(order["email"], subject, invoice_html(order, items), attachment_bytes=pdf, attachment_name=f"NexaFarma-{order_no}.pdf" if pdf else None)
        if sent:
            conn.execute("UPDATE orders SET invoice_email_sent_at=? WHERE id=?", (now_iso(), order["id"]))
            conn.commit()
        else:
            conn.rollback()
    except Exception:
        conn.rollback()
        app.logger.exception("No se pudo generar o enviar el comprobante del pedido %s", order_no)
        sent = False
    finally:
        conn.close()
    if sent:
        audit("invoice_sent", "order", order["id"], {"email": order["email"], "forced_resend": bool(force)})
    return sent


def reset_email(user, token):
    reset_url = f"{public_base_url()}{url_for('reset_password', token=token)}"
    html_body = f"""
    <div style=\"font-family:Arial,sans-serif;background:#f2faf8;padding:28px\"><div style=\"max-width:620px;margin:auto;background:#fff;border-radius:22px;padding:32px;border:1px solid #d6e9e5\"><h1 style=\"color:#087f83\">Restablecer contraseña</h1><p>Hola {html.escape(user['first_name'])}, recibimos una solicitud para cambiar la contraseña de tu cuenta NexaFarma.</p><p style=\"margin:30px 0\"><a href=\"{html.escape(reset_url)}\" style=\"background:#0e9290;color:#fff;text-decoration:none;padding:14px 20px;border-radius:12px;font-weight:700\">Crear nueva contraseña</a></p><p style=\"color:#526b6b\">Este enlace vence en 60 minutos. Si no pediste este cambio, puedes ignorar este correo.</p></div></div>"""
    return send_email(user["email"], "NexaFarma · Restablecimiento de contraseña", html_body)


def welcome_email(user, coupon_code):
    html_body = f"""<div style='font-family:Arial,sans-serif;background:#f2faf8;padding:28px'><div style='max-width:640px;margin:auto;background:#fff;border:1px solid #d6e9e5;border-radius:24px;overflow:hidden'><div style='padding:28px;background:linear-gradient(135deg,#dff5f0,#e9f2ff)'><div style='font-size:28px;font-weight:900;color:#087f83'>✚ NexaFarma</div><div style='color:#526b6b;margin-top:5px'>Tu espacio para cuidado personal y bienestar.</div></div><div style='padding:28px'><p>Hola <b>{html.escape(user['first_name'])}</b></p><p>Tu cuenta ya está lista. Como bienvenida, tienes un cupón de <b>{CONFIG['discount']}%</b>:</p><div style='font-size:28px;font-weight:900;letter-spacing:.08em;background:#e4f5f2;border-radius:16px;padding:18px;text-align:center;color:#087f83'>{html.escape(coupon_code)}</div><p style='color:#526b6b'>Válido durante 30 días. Puedes usarlo desde el checkout.</p><p style='margin-top:24px'><a href='{html.escape(public_base_url() + url_for("menu"))}' style='background:#0e9290;color:white;text-decoration:none;padding:13px 18px;border-radius:12px;font-weight:800'>Ver catálogo</a></p></div></div></div>"""
    return send_email(user["email"], f"NexaFarma · Bienvenido, {user['first_name']}", html_body)


def fallback_home_html():
    return """<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>NexaFarma | Salud y bienestar</title><style>body{margin:0;font-family:system-ui,sans-serif;background:#f7fbfa;color:#193536}.wrap{max-width:1120px;margin:auto;padding:28px 20px}.nav{display:flex;align-items:center;justify-content:space-between;gap:18px}.brand{font-weight:900;font-size:1.6rem;color:#087f83}.links a{margin-left:15px;text-decoration:none;color:#193536;font-weight:700}.hero{margin-top:52px;padding:55px 0}.eyebrow{font-size:.75rem;letter-spacing:.12em;font-weight:800;color:#087f83}.hero h1{font-size:clamp(2.7rem,7vw,5rem);line-height:.98;margin:14px 0}.hero p{color:#526b6b;max-width:680px;font-size:1.1rem}.btn{padding:13px 18px;border-radius:13px;text-decoration:none;font-weight:800;display:inline-block;border:1px solid #d6e9e5;margin:8px 8px 0 0}.primary{background:#087f83;color:#fff;border-color:#087f83}.foot{margin-top:65px;padding:24px 0;border-top:1px solid #d6e9e5;color:#526b6b}</style></head><body><div class="wrap"><nav class="nav"><div class="brand">✚ NexaFarma</div><div class="links"><a href="/menu">Catálogo</a><a href="/contacto">Contacto</a><a href="/login">Entrar</a></div></nav><section class="hero"><span class="eyebrow">SALUD · CUIDADO · BIENESTAR</span><h1>Tu bienestar,<br><span style="color:#087f83">más cerca.</span></h1><p>La página principal encontró un problema temporal. Puedes seguir explorando el catálogo o contactar al equipo de la tienda de demostración.</p><a class="btn primary" href="/menu">Explorar catálogo →</a><a class="btn" href="/registro">Crear cuenta</a><div class="foot">NexaFarma es una marca ficticia para una demostración académica y no sustituye la orientación de profesionales de salud.</div></section></div></body></html>"""


@app.route("/")
def home():
    products, categories, popular, testimonials, default_size = [], [], [], [], None
    try:
        conn = db()
        products = conn.execute(
            """SELECT p.*,c.name category FROM products p LEFT JOIN categories c ON c.id=p.category_id
               WHERE p.active=1 AND p.stock>0 ORDER BY p.featured DESC,p.id DESC LIMIT 8"""
        ).fetchall()
        default_size = conn.execute("SELECT name,price,volume_ml FROM sizes WHERE active=1 ORDER BY price,id LIMIT 1").fetchone()
        categories = conn.execute("SELECT * FROM categories WHERE active=1 ORDER BY name LIMIT 8").fetchall()
        popular = conn.execute(
            """SELECT oi.product_id,oi.product_name,SUM(oi.quantity) sold,MAX(p.image) image,
                      MAX(p.price)+COALESCE((SELECT s.price FROM sizes s WHERE s.active=1 ORDER BY s.price,s.id LIMIT 1),0) price
               FROM order_items oi JOIN orders o ON o.id=oi.order_id LEFT JOIN products p ON p.id=oi.product_id
               WHERE p.active=1 AND p.stock>0 AND o.status!='cancelled' GROUP BY oi.product_id,oi.product_name
               ORDER BY sold DESC LIMIT 4"""
        ).fetchall()
        testimonials = conn.execute(
            """SELECT r.rating,r.comment,u.first_name,p.name product_name
               FROM reviews r JOIN users u ON u.id=r.user_id JOIN products p ON p.id=r.product_id
               WHERE r.active=1 AND length(trim(COALESCE(r.comment,'')))>0
               ORDER BY r.id DESC LIMIT 3"""
        ).fetchall()
        conn.close()
        return render_template("home.html", products=products, categories=categories, popular=popular, testimonials=testimonials, default_size=default_size)
    except Exception:
        app.logger.exception("Error cargando la página principal")
        try:
            return render_template("home.html", products=products, categories=categories, popular=popular, testimonials=testimonials, default_size=default_size)
        except Exception:
            app.logger.exception("Fallback Jinja de home también falló")
            return fallback_home_html(), 200


@app.get("/menu")
def menu():
    conn = db()
    categories = conn.execute("SELECT * FROM categories WHERE active=1 ORDER BY name").fetchall()
    default_size = conn.execute("SELECT name,price,volume_ml FROM sizes WHERE active=1 ORDER BY price,id LIMIT 1").fetchone()
    category = request.args.get("category", "").strip()
    query = request.args.get("q", "").strip()[:80]
    sort = request.args.get("sort", "newest")
    sql = """SELECT p.*,c.name category FROM products p LEFT JOIN categories c ON c.id=p.category_id WHERE p.active=1"""
    args = []
    if category:
        sql += " AND c.name=?"; args.append(category)
    if query:
        sql += " AND (p.name LIKE ? OR p.description LIKE ? OR c.name LIKE ?)"; needle = f"%{query}%"; args += [needle, needle, needle]
    order_by = {"price_asc":"p.price ASC,p.name ASC","price_desc":"p.price DESC,p.name ASC","name":"p.name COLLATE NOCASE ASC","newest":"p.featured DESC,p.id DESC"}.get(sort,"p.featured DESC,p.id DESC")
    products = conn.execute(f"{sql} ORDER BY {order_by}", args).fetchall()
    conn.close()
    return render_template("menu.html", products=products, categories=categories, default_size=default_size)


@app.get("/agente-ia")
def agent_page():
    return render_template("agent.html")


@app.get("/producto/<int:pid>")
def product(pid):
    conn = db()
    product_row = conn.execute("""SELECT p.*,c.name category FROM products p LEFT JOIN categories c ON c.id=p.category_id WHERE p.id=? AND p.active=1""", (pid,)).fetchone()
    toppings = conn.execute("SELECT * FROM toppings WHERE active=1 ORDER BY name").fetchall()
    sizes = conn.execute("SELECT * FROM sizes WHERE active=1 ORDER BY price").fetchall()
    related = conn.execute("""SELECT p.*,c.name category FROM products p LEFT JOIN categories c ON c.id=p.category_id WHERE p.active=1 AND p.id!=? AND p.category_id=(SELECT category_id FROM products WHERE id=?) ORDER BY p.featured DESC,p.id DESC LIMIT 4""", (pid,pid)).fetchall()
    reviews = conn.execute("""SELECT r.*,u.first_name,u.last_name FROM reviews r JOIN users u ON u.id=r.user_id WHERE r.product_id=? AND r.active=1 ORDER BY r.id DESC LIMIT 10""", (pid,)).fetchall()
    avg_rating = conn.execute("SELECT COALESCE(AVG(rating),0) FROM reviews WHERE product_id=? AND active=1", (pid,)).fetchone()[0]
    conn.close()
    if not product_row:
        return render_template("error.html", code=404, title="Producto no encontrado", message="Este producto ya no está disponible."), 404
    return render_template("product.html", product=product_row, toppings=toppings, sizes=sizes, related=related, reviews=reviews, avg_rating=avg_rating)


@app.get("/api/producto/<int:pid>")
def product_api(pid):
    conn = db(); row = conn.execute("""SELECT p.id,p.name,p.description,
        p.price+COALESCE((SELECT s.price FROM sizes s WHERE s.active=1 ORDER BY s.price,s.id LIMIT 1),0) price,
        p.image,p.stock,p.requires_prescription,c.name category FROM products p LEFT JOIN categories c ON c.id=p.category_id WHERE p.id=? AND p.active=1""", (pid,)).fetchone(); conn.close()
    if not row: return jsonify({"error":"Producto no encontrado"}),404
    item = dict(row); item["price_label"] = "Desde " + money(item["price"]); item["image_url"] = url_for("static", filename=item["image"]) if item.get("image") else None; return jsonify(item)


@app.post("/carrito/agregar")
def add_cart():
    try:
        pid = int(request.form["product_id"]); quantity = int(request.form.get("quantity",1))
    except (KeyError,ValueError,TypeError):
        flash("No pudimos agregar ese producto.","error"); return redirect(url_for("menu"))
    if quantity < 1 or quantity > 20:
        flash("La cantidad debe estar entre 1 y 20.","error"); return redirect(url_for("product",pid=pid))
    conn = db(); product_row = conn.execute("SELECT * FROM products WHERE id=? AND active=1",(pid,)).fetchone()
    if not product_row: conn.close(); flash("Producto no disponible.","error"); return redirect(url_for("menu"))
    if int(product_row["requires_prescription"] or 0):
        conn.close(); flash("Este artículo requiere validación profesional y no está habilitado para compra en línea en esta versión de demostración.", "warning"); return redirect(url_for("product", pid=pid))
    if int(product_row["stock"] or 0) < quantity: conn.close(); flash("No hay suficiente stock para esa cantidad.","error"); return redirect(url_for("product",pid=pid))
    try: size_id = int(request.form.get("size")) if request.form.get("size") else None
    except ValueError: size_id = None
    size = conn.execute("SELECT * FROM sizes WHERE id=? AND active=1",(size_id,)).fetchone() if size_id else None
    if not size:
        size = conn.execute("SELECT * FROM sizes WHERE active=1 ORDER BY price LIMIT 1").fetchone()
    top_raw = request.form.getlist("toppings")
    top_ids=[]
    for x in top_raw:
        try: top_ids.append(int(x))
        except ValueError: pass
    toppings=[]
    if top_ids:
        placeholders=",".join("?"*len(top_ids)); toppings=conn.execute(f"SELECT id,name,price FROM toppings WHERE active=1 AND id IN ({placeholders})",top_ids).fetchall()
    item={"product_id":pid,"qty":quantity,"size_id":size["id"] if size else None,"flavor":request.form.get("flavor","").strip()[:120],"topping_ids":[t["id"] for t in toppings]}
    items=session.get("cart",[])
    merged=False
    stock_limited=False
    signature=(pid,item["size_id"],item["flavor"],tuple(item["topping_ids"]))
    for existing in items:
        existing_signature=(existing.get("product_id"),existing.get("size_id"),existing.get("flavor",""),tuple(existing.get("topping_ids",[])))
        if signature==existing_signature:
            requested_total=int(existing.get("qty",1))+quantity
            existing["qty"]=min(20,int(product_row["stock"] or 0),requested_total)
            stock_limited=existing["qty"]<requested_total
            merged=True
            break
    if not merged:
        items.append(item)
    session["cart"]=items; session.modified=True
    conn.close()
    if stock_limited:
        flash(f"Se ajustó la cantidad de {product_row['name']} al inventario disponible.","warning")
    else:
        flash(f"{product_row['name']} se agregó a tu carrito.","success")
    return redirect(safe_local_target(request.form.get("next")) or url_for("cart_view"))


@app.get("/carrito")
def cart_view():
    items=cart_items_from_session(); subtotal=cart_subtotal(items); return render_template("cart.html",items=items,subtotal=subtotal)


@app.post("/carrito/eliminar/<int:index>")
def remove_cart(index):
    items=session.get("cart",[])
    if 0<=index<len(items):
        removed=items.pop(index); session["cart"]=items; session.modified=True; flash(f"{removed.get('product_id','Producto')} fue eliminado del carrito.","success")
    return redirect(url_for("cart_view"))


@app.post("/carrito/cantidad/<int:index>")
def qty_cart(index):
    items=cart_items_from_session()
    if 0<=index<len(items):
        try:
            requested=max(1,min(20,int(request.form.get("qty",1))))
            items[index]["qty"]=min(requested,int(items[index]["stock"]))
            if requested>int(items[index]["stock"]): flash(f"Solo hay {items[index]['stock']} unidades disponibles; ajustamos la cantidad.","warning")
        except (ValueError,TypeError): flash("Cantidad no válida.","error")
        session["cart"]=items; session.modified=True
    return redirect(url_for("cart_view"))


@app.post("/carrito/limpiar")
def clear_cart():
    session["cart"]=[]; session.modified=True; flash("Carrito limpiado.","success"); return redirect(url_for("cart_view"))


def google_oauth_ready():
    return bool(GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET and GOOGLE_OAUTH_REDIRECT_URI)


@app.get("/login/google")
def google_login():
    if not google_oauth_ready():
        flash("El inicio de sesión con Google todavía no está configurado.", "error")
        return redirect(url_for("login"))
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    session["google_oauth_state"] = state
    session["google_oauth_nonce"] = nonce
    return redirect(GOOGLE_AUTH_URL + "?" + requests.compat.urlencode({
        "client_id": GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "prompt": "select_account",
    }))


@app.get("/login/google/callback")
def google_login_callback():
    expected_state = session.pop("google_oauth_state", "")
    expected_nonce = session.pop("google_oauth_nonce", "")
    received_state = request.args.get("state", "")
    if not expected_state or not received_state or not secrets.compare_digest(expected_state, received_state):
        flash("No se pudo validar el inicio de sesión con Google. Inténtalo de nuevo.", "error")
        return redirect(url_for("login"))
    if request.args.get("error"):
        flash("Cancelaste el inicio de sesión con Google.", "error")
        return redirect(url_for("login"))
    code = request.args.get("code", "")
    if not code or not google_oauth_ready():
        flash("Google no devolvió una autorización válida.", "error")
        return redirect(url_for("login"))
    try:
        token_response = requests.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
            "redirect_uri": GOOGLE_OAUTH_REDIRECT_URI,
            "grant_type": "authorization_code",
        }, timeout=10)
        token_response.raise_for_status()
        id_token = token_response.json().get("id_token")
        if not id_token:
            raise ValueError("Google no devolvió id_token")
        identity_response = requests.get(GOOGLE_TOKEN_INFO_URL, params={"id_token": id_token}, timeout=10)
        identity_response.raise_for_status()
        identity = identity_response.json()
        if identity.get("aud") != GOOGLE_OAUTH_CLIENT_ID or identity.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
            raise ValueError("Audiencia o emisor de Google inválido")
        if not expected_nonce or not secrets.compare_digest(str(identity.get("nonce", "")), expected_nonce):
            raise ValueError("Nonce OIDC inválido")
        if identity.get("email_verified") not in {True, "true"} or int(identity.get("exp", 0)) <= int(datetime.now(timezone.utc).timestamp()):
            raise ValueError("Identidad Google no verificada o expirada")
        email = identity.get("email", "").strip().lower()
        google_sub = identity.get("sub", "").strip()
        if not google_sub or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("Perfil Google incompleto")
    except (requests.RequestException, ValueError, TypeError, KeyError):
        app.logger.warning("Falló la validación OAuth de Google", exc_info=True)
        flash("No fue posible validar tu cuenta de Google. Inténtalo de nuevo.", "error")
        return redirect(url_for("login"))

    conn = db()
    user = conn.execute("SELECT * FROM users WHERE google_sub=? OR email=?", (google_sub, email)).fetchone()
    existing_account = user is not None
    if user and (not user["is_active"] or (user["google_sub"] and user["google_sub"] != google_sub)):
        conn.close()
        flash("Esta cuenta está inactiva o vinculada a otra identidad. Contacta con soporte.", "error")
        return redirect(url_for("login"))
    if user:
        conn.execute("UPDATE users SET google_sub=?,updated_at=? WHERE id=?", (google_sub, now_iso(), user["id"]))
        user_id = user["id"]
        is_admin = user["is_admin"]
    else:
        first = (identity.get("given_name") or identity.get("name", "Cliente").split()[0])[:60]
        last = (identity.get("family_name") or " ".join(identity.get("name", "").split()[1:]) or "Google")[:60]
        random_password_hash = generate_password_hash(secrets.token_urlsafe(48))
        try:
            conn.execute("INSERT INTO users(first_name,last_name,email,password_hash,google_sub,is_admin,is_active,created_at,updated_at) VALUES(?,?,?,?,?,0,1,?,?)",
                         (first, last, email, random_password_hash, google_sub, now_iso(), now_iso()))
            user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            welcome_code = CONFIG["code_prefix"] + "-" + secrets.token_hex(3).upper()
            conn.execute("INSERT INTO coupons(user_id,code,percent,expires_at,used,min_purchase,active,max_uses,uses_count,created_at) VALUES(?,?,?,?,0,0,1,1,0,?)",
                         (user_id, welcome_code, CONFIG["discount"], (datetime.now(timezone.utc)+timedelta(days=30)).isoformat(), now_iso()))
            conn.commit()
            created_user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            welcome_mail_sent = welcome_email(created_user, welcome_code)
            is_admin = 0
        except sqlite3.IntegrityError:
            conn.rollback()
            conn.close()
            flash("No se pudo crear la cuenta con Google. Inténtalo de nuevo.", "error")
            return redirect(url_for("login"))
    conn.commit()
    conn.close()
    session.clear()
    session["user_id"] = user_id
    csrf_token_value()
    audit("google_login", "user", user_id, {"provider": "google", "email": email}, user_id)
    if existing_account:
        flash("Iniciaste sesión con tu cuenta existente. No se vuelve a crear ni enviar el cupón de bienvenida; revisa los cupones disponibles en Mi cuenta.", "success")
    elif not welcome_mail_sent:
        flash("Tu cuenta y cupón quedaron creados en Mi cuenta, pero no pudimos enviar el correo de bienvenida. Revisa la configuración de correo o Spam.", "warning")
    return redirect(url_for("admin" if is_admin else "account"))


@app.route("/registro",methods=["GET","POST"])
def register():
    if request.method=="POST":
        f=request.form; first=f.get("first_name","").strip()[:60]; last=f.get("last_name","").strip()[:60]; email=f.get("email","").strip().lower(); phone=re.sub(r"\D","",f.get("phone","") or "")[:15]; password=f.get("password",""); password2=f.get("password2",""); address=f.get("address","").strip()[:180]; city=f.get("city","").strip()[:80]; neighborhood=f.get("neighborhood","").strip()[:80]
        strength_error = password_strength_error(password)
        if not first or not last or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",email) or len(phone)<7 or strength_error: flash("Revisa nombre, correo y teléfono. " + (strength_error or ""),"error"); return redirect(url_for("register"))
        if password!=password2: flash("Las contraseñas no coinciden.","error"); return redirect(url_for("register"))
        conn=db()
        try:
            conn.execute("INSERT INTO users(first_name,last_name,email,phone,password_hash,address,city,neighborhood,is_admin,is_active,created_at,updated_at) VALUES(?,?,?,?,?,?,?, ?,0,1,?,?)",(first,last,email,phone,generate_password_hash(password),address,city,neighborhood,now_iso(),now_iso()))
            uid=conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            code = None
            for _ in range(30):
                candidate = CONFIG["code_prefix"] + "-" + secrets.token_hex(3).upper()
                if not conn.execute("SELECT 1 FROM coupons WHERE code=?", (candidate,)).fetchone():
                    code = candidate
                    break
            code = code or (CONFIG["code_prefix"] + "-" + secrets.token_hex(6).upper())
            conn.execute("INSERT INTO coupons(user_id,code,percent,expires_at,used,min_purchase,active,max_uses,uses_count,created_at) VALUES(?,?,?,?,0,0,1,1,0,?)",(uid,code,CONFIG["discount"],(datetime.now(timezone.utc)+timedelta(days=30)).isoformat(),now_iso()))
            conn.commit()
            row=conn.execute("SELECT * FROM users WHERE id=?",(uid,)).fetchone()
        except sqlite3.IntegrityError:
            conn.rollback(); conn.close(); flash("Ese correo ya está registrado.","error"); return redirect(url_for("register"))
        conn.close(); session.clear(); session["user_id"]=row["id"]; csrf_token_value(); audit("user_registered","user",row["id"],{"email":email},row["id"])
        if welcome_email(row, code):
            flash(f"Bienvenido a NexaFarma, {first}. Tu cupón de bienvenida está en Mi cuenta y enviamos un correo.","success")
        else:
            flash(f"Bienvenido a NexaFarma, {first}. Tu cupón está en Mi cuenta, pero no pudimos enviar el correo. Revisa la configuración de correo o Spam.","warning")
        return redirect(url_for("account"))
    return render_template("auth.html",mode="register")


@app.route("/login",methods=["GET","POST"])
def login():
    next_url=request.args.get("next","") if request.method=="GET" else request.form.get("next","")
    if request.method=="POST":
        email=request.form.get("email","").strip().lower(); password=request.form.get("password","")
        conn=db(); user=conn.execute("SELECT * FROM users WHERE email=? AND is_active=1",(email,)).fetchone(); conn.close()
        if user and check_password_hash(user["password_hash"],password):
            if password_strength_error(password):
                session.clear(); session["user_id"]=user["id"]; session["must_change_password"]=True; csrf_token_value()
                flash("Tu contraseña actual es válida, pero debes crear una nueva antes de continuar.","warning")
                return redirect(url_for("change_password"))
            session.clear(); session["user_id"]=user["id"]; csrf_token_value(); audit("login","user",user["id"],{"email":email},user["id"])
            safe_next=safe_local_target(next_url)
            return redirect(safe_next or (url_for("admin") if user["is_admin"] else url_for("account")))
        flash("Correo o contraseña incorrectos.","error")
    return render_template("auth.html",mode="login",next_url=next_url)


@app.route("/cambiar-contrasena",methods=["GET","POST"])
@login_required
def change_password():
    user=current_user()
    if request.method=="POST":
        current_password=request.form.get("current_password","")
        new_password=request.form.get("password","")
        password2=request.form.get("password2","")
        if not check_password_hash(user["password_hash"],current_password):
            flash("La contraseña actual no coincide.","error")
            return redirect(url_for("change_password"))
        strength_error=password_strength_error(new_password)
        if strength_error or new_password!=password2:
            flash("La nueva contraseña debe cumplir los requisitos y coincidir en ambos campos. " + (strength_error or ""),"error")
            return redirect(url_for("change_password"))
        conn=db()
        conn.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?",(generate_password_hash(new_password),now_iso(),user["id"]))
        conn.commit(); conn.close()
        session.pop("must_change_password",None)
        audit("password_changed","user",user["id"],{"forced":True},user["id"])
        flash("Contraseña actualizada. Ya puedes usar tu cuenta.","success")
        return redirect(url_for("admin" if user["is_admin"] else "account"))
    return render_template("auth.html",mode="change_password")


@app.route("/recuperar",methods=["GET","POST"])
def forgot_password():
    if request.method=="POST":
        email=request.form.get("email","").strip().lower(); conn=db(); user=conn.execute("SELECT * FROM users WHERE email=? AND is_active=1",(email,)).fetchone()
        if user:
            raw=secrets.token_urlsafe(32); token_hash=hashlib.sha256(raw.encode()).hexdigest(); conn.execute("UPDATE password_resets SET used=1 WHERE user_id=?",(user["id"],)); conn.execute("INSERT INTO password_resets(user_id,token_hash,expires_at,used,created_at) VALUES(?,?,?,0,?)",(user["id"],token_hash,(datetime.now(timezone.utc)+timedelta(minutes=60)).isoformat(),now_iso())); conn.commit(); reset_email(user,raw)
        conn.close(); flash("Si el correo existe, recibirás un enlace para restablecer la contraseña.","success"); return redirect(url_for("login"))
    return render_template("auth.html",mode="forgot")


@app.route("/reset-password/<token>",methods=["GET","POST"])
def reset_password(token):
    token_hash=hashlib.sha256(token.encode()).hexdigest(); conn=db(); reset=conn.execute("SELECT * FROM password_resets WHERE token_hash=? AND used=0",(token_hash,)).fetchone()
    valid=bool(reset and (parse_dt(reset["expires_at"]) or datetime.now(timezone.utc))>datetime.now(timezone.utc))
    if request.method=="POST":
        password=request.form.get("password",""); password2=request.form.get("password2","")
        if not valid: conn.close(); return render_template("error.html",code=400,title="Enlace inválido",message="El enlace de recuperación ya no es válido."),400
        strength_error = password_strength_error(password)
        if strength_error or password!=password2: conn.close(); flash("Las contraseñas deben coincidir. " + (strength_error or ""),"error"); return redirect(url_for("reset_password",token=token))
        conn.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?",(generate_password_hash(password),now_iso(),reset["user_id"])); conn.execute("UPDATE password_resets SET used=1 WHERE id=?",(reset["id"],)); conn.commit(); conn.close(); flash("Contraseña actualizada. Ya puedes iniciar sesión.","success"); return redirect(url_for("login"))
    conn.close()
    if not valid: return render_template("error.html",code=400,title="Enlace inválido",message="El enlace de recuperación ya no es válido."),400
    return render_template("auth.html",mode="reset",token=token)


@app.post("/logout")
def logout():
    session.clear()
    flash("Has cerrado sesión.", "success")
    return redirect(url_for("home"))


@app.route("/mi-cuenta",methods=["GET","POST"])
@login_required
def account():
    user=current_user(); conn=db()
    if request.method=="POST":
        f=request.form; first=f.get("first_name","").strip()[:60]; last=f.get("last_name","").strip()[:60]; phone=re.sub(r"\D","",f.get("phone","") or "")[:15]; address=f.get("address","").strip()[:180]; city=f.get("city","").strip()[:80]; neighborhood=f.get("neighborhood","").strip()[:80]
        if not first or not last: conn.close(); flash("Nombre y apellido son obligatorios.","error"); return redirect(url_for("account"))
        conn.execute("UPDATE users SET first_name=?,last_name=?,phone=?,address=?,city=?,neighborhood=?,updated_at=? WHERE id=?",(first,last,phone,address,city,neighborhood,now_iso(),user["id"])); conn.commit(); conn.close(); flash("Perfil actualizado.","success"); return redirect(url_for("account"))
    orders=conn.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC",(user["id"],)).fetchall(); coupons=conn.execute("SELECT * FROM coupons WHERE user_id=? ORDER BY id DESC",(user["id"],)).fetchall(); favorites=conn.execute("SELECT p.* FROM favorites f JOIN products p ON p.id=f.product_id WHERE f.user_id=? ORDER BY p.id DESC",(user["id"],)).fetchall(); total_spent=conn.execute("SELECT COALESCE(SUM(total),0) FROM orders WHERE user_id=? AND status!='cancelled'",(user["id"],)).fetchone()[0]; conn.close()
    return render_template("account.html",orders=orders,coupons=coupons,favorites=favorites,total_spent=total_spent)


@app.get("/mis-pedidos")
@login_required
def order_history():
    user=current_user(); conn=db(); orders=conn.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC",(user["id"],)).fetchall(); conn.close(); return render_template("orders.html",orders=orders)


@app.get("/favoritos")
@login_required
def favorites_page():
    user=current_user(); conn=db(); favorites=conn.execute("SELECT p.*,c.name category FROM favorites f JOIN products p ON p.id=f.product_id LEFT JOIN categories c ON c.id=p.category_id WHERE f.user_id=? AND p.active=1 ORDER BY p.id DESC",(user["id"],)).fetchall(); conn.close(); return render_template("favorites.html",favorites=favorites)


@app.post("/favorito/<int:pid>")
def favorite(pid):
    user=current_user()
    if not user: return redirect(url_for("login",next=request.referrer or url_for("menu")))
    conn=db(); product_exists=conn.execute("SELECT 1 FROM products WHERE id=? AND active=1",(pid,)).fetchone()
    if not product_exists: conn.close(); flash("Producto no encontrado.","error"); return redirect(request.referrer or url_for("menu"))
    exists=conn.execute("SELECT 1 FROM favorites WHERE user_id=? AND product_id=?",(user["id"],pid)).fetchone()
    if exists: conn.execute("DELETE FROM favorites WHERE user_id=? AND product_id=?",(user["id"],pid)); fav=False; msg="Producto eliminado de favoritos."
    else: conn.execute("INSERT OR IGNORE INTO favorites(user_id,product_id) VALUES(?,?)",(user["id"],pid)); fav=True; msg="Producto guardado en favoritos."
    conn.commit(); conn.close()
    if request.headers.get("X-Requested-With")=="XMLHttpRequest": return jsonify({"ok":True,"favorite":fav,"message":msg})
    flash(msg,"success"); return redirect(request.referrer or url_for("menu"))


@app.post("/favoritos/vaciar")
@login_required
def clear_favorites():
    user=current_user(); conn=db(); conn.execute("DELETE FROM favorites WHERE user_id=?",(user["id"],)); conn.commit(); conn.close(); flash("Favoritos limpiados.","success"); return redirect(url_for("favorites_page"))


@app.route("/checkout",methods=["GET","POST"])
@login_required
def checkout():
    user=current_user(); items=cart_items_from_session()
    if not items: flash("Tu carrito está vacío.","error"); return redirect(url_for("menu"))
    subtotal=cart_subtotal(items); form_data=dict(user); discount=0; coupon=None; delivery_method=request.form.get("delivery_method","domicilio") if request.method=="POST" else "domicilio"
    if request.method=="POST":
        form_data.update(request.form.to_dict(flat=True)); code=request.form.get("coupon","").strip().upper(); coupon,discount,msg=validate_coupon(code,user["id"],subtotal)
        if code: flash(msg,"success" if coupon else "error")
    delivery=delivery_for(subtotal-discount,delivery_method)
    conn=db()
    delivered_order_count=conn.execute("SELECT COUNT(*) FROM orders WHERE user_id=? AND status='delivered'", (user["id"],)).fetchone()[0]
    conn.close()
    return render_template("checkout.html",items=items,subtotal=subtotal,discount=discount,delivery=delivery,coupon=coupon,form_data=form_data,wompi_methods=WOMPI_METHODS,delivered_order_count=delivered_order_count)


@app.post("/api/coupon/validate")
@login_required
def api_coupon_validate():
    items=cart_items_from_session(); subtotal=cart_subtotal(items); code=(request.form.get("coupon") or "").strip().upper(); coupon,discount,msg=validate_coupon(code,current_user()["id"],subtotal)
    delivery_method=request.form.get("delivery_method","domicilio"); delivery=delivery_for(subtotal-discount,delivery_method)
    return jsonify({"ok":bool(coupon),"message":msg,"discount":discount,"delivery":delivery,"total":subtotal-discount+delivery,"coupon":coupon["code"] if coupon else None,"subtotal":subtotal})


@app.post("/pedido")
@login_required
def create_order():
    user=current_user(); items=cart_items_from_session()
    status_conn=db(); site_status=status_conn.execute("SELECT value FROM site_settings WHERE key='store_status'").fetchone(); status_conn.close()
    if site_status and site_status[0] == 'closed':
        flash("La tienda está cerrada temporalmente y no recibe nuevos pedidos.","error")
        return redirect(url_for("menu"))
    if request.form.get("accept_terms") != "yes":
        flash("Para continuar, acepta las condiciones de compra y revisa el aviso de privacidad.","error")
        return redirect(url_for("checkout"))
    if not items: flash("Tu carrito está vacío.","error"); return redirect(url_for("menu"))
    f=request.form; delivery_method=f.get("delivery_method","domicilio")
    if delivery_method not in {"domicilio","recoger"}: flash("Método de entrega inválido.","error"); return redirect(url_for("checkout"))
    address=f.get("address","").strip()[:180]
    if delivery_method=="recoger": address="Recoger en tienda"
    phone=re.sub(r"\D","",f.get("phone","") or "")[:15]
    customer_name=f.get("customer_name",f"{user['first_name']} {user['last_name']}").strip()[:120]
    if len(phone)<7 or not address or not customer_name: flash("Completa los datos de contacto y entrega.","error"); return redirect(url_for("checkout"))
    payment=f.get("payment_method","").strip()
    if payment not in {"contra_entrega","transferencia","wompi","mercadopago"}: flash("Método de pago no válido.","error"); return redirect(url_for("checkout"))
    if payment=="wompi" and not wompi_is_available():
        flash("Wompi no está disponible temporalmente. Elige Mercado Pago o transferencia.","warning")
        return redirect(url_for("checkout"))
    if payment=="contra_entrega":
        conn=db()
        delivered_order_count=conn.execute("SELECT COUNT(*) FROM orders WHERE user_id=? AND status='delivered'", (user["id"],)).fetchone()[0]
        conn.close()
        if delivered_order_count < 10:
            flash(f"El pago contra entrega se habilita después de completar 10 pedidos. Tienes {delivered_order_count} de 10.", "error")
            return redirect(url_for("checkout"))
    if payment=="mercadopago" and not mercadopago_is_configured(): flash("Mercado Pago todavía no está configurado para pagos seguros.","error"); return redirect(url_for("checkout"))
    subtotal=cart_subtotal(items); code=f.get("coupon","").strip().upper(); coupon,discount,_=validate_coupon(code,user["id"],subtotal); delivery=delivery_for(subtotal-discount,delivery_method); total=subtotal-discount+delivery; order_no=generate_order_no(); wompi_reference=order_no if payment=="wompi" else None
    conn=db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if code:
            coupon, discount, coupon_message = validate_coupon(code, user["id"], subtotal, connection=conn)
            if not coupon:
                raise ValueError(coupon_message or "El cupón ya no está disponible.")
            delivery=delivery_for(subtotal-discount,delivery_method)
            total=subtotal-discount+delivery
        requested_stock = {}
        names_by_product = {}
        for item in items:
            pid = int(item["product_id"])
            requested_stock[pid] = requested_stock.get(pid, 0) + int(item["qty"])
            names_by_product[pid] = item["name"]
        for pid, requested_qty in requested_stock.items():
            row=conn.execute("SELECT id,name,price,stock,active FROM products WHERE id=?",(pid,)).fetchone()
            if not row or not row["active"] or int(row["stock"] or 0)<requested_qty:
                raise ValueError(f"No hay stock suficiente para {names_by_product.get(pid, 'este producto')}. Ajusta las cantidades del carrito.")
        notes = f.get("notes", "").strip()[:1000]
        conn.execute(
            "INSERT INTO orders(order_no,user_id,customer_name,email,phone,address,neighborhood,city,reference,delivery_method,subtotal,discount,delivery_fee,total,payment_method,payment_status,status,coupon_code,wompi_reference,mercadopago_idempotency_key,terms_accepted_at,notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (order_no,user["id"],customer_name,user["email"],phone,address,f.get("neighborhood","").strip()[:80],f.get("city","").strip()[:80],f.get("reference","").strip()[:160],delivery_method,subtotal,discount,delivery,total,payment,"pending","received",coupon["code"] if coupon else None,wompi_reference,str(uuid.uuid4()) if payment=="mercadopago" else None,now_iso(),notes,now_iso(),now_iso()),
        )
        oid=conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        for item in items:
            conn.execute("INSERT INTO order_items(order_id,product_id,product_name,quantity,unit_price,size,flavor,toppings) VALUES(?,?,?,?,?,?,?,?)",(oid,item["product_id"],item["name"],item["qty"],item["unit"],item["size"],item["flavor"],", ".join(t["name"] for t in item["toppings"])))
            conn.execute("UPDATE products SET stock=stock-?,updated_at=? WHERE id=?",(item["qty"],now_iso(),item["product_id"]))
        if coupon:
            conn.execute("UPDATE coupons SET uses_count=uses_count+1,used=CASE WHEN uses_count+1>=max_uses THEN 1 ELSE 0 END WHERE id=?",(coupon["id"],))
        conn.commit()
    except ValueError as exc:
        conn.rollback(); conn.close(); flash(str(exc),"error"); return redirect(url_for("checkout"))
    except Exception:
        conn.rollback(); conn.close(); app.logger.exception("Error creando pedido"); flash("No pudimos crear el pedido. Inténtalo otra vez.","error"); return redirect(url_for("checkout"))
    conn.close(); session["cart"]=[]; session.modified=True; audit("order_created","order",oid,{"order_no":order_no,"payment":payment})
    # Las notificaciones por correo no deben impedir que el pedido quede creado.
    admin_new_order_email(order_no)
    order_status_email(order_no, "received")
    if payment=="wompi": return redirect(url_for("wompi_checkout",order_no=order_no))
    if payment=="mercadopago": return redirect(url_for("mercadopago_checkout",order_no=order_no))
    send_order_invoice(order_no)
    flash("Pedido creado correctamente. Enviamos el comprobante por correo si el proveedor está configurado.","success")
    return redirect(url_for("order_detail",order_no=order_no))


@app.get("/pago/wompi/<order_no>")
@login_required
def wompi_checkout(order_no):
    user=current_user(); conn=db(); order=conn.execute("SELECT * FROM orders WHERE order_no=?",(order_no,)).fetchone(); conn.close()
    if not order or (order["user_id"]!=user["id"] and not user["is_admin"]): abort(404)
    if order["payment_method"]!="wompi": return redirect(url_for("order_detail",order_no=order_no))
    if order["payment_status"] != "pending" or order["stock_restored"]:
        flash("Este intento de pago ya finalizó. Para volver a comprar, crea un nuevo pedido desde el carrito.", "warning")
        return redirect(url_for("order_detail",order_no=order_no))
    if not wompi_is_available(): return render_template("error.html",code=503,title="Wompi no disponible",message="Wompi está temporalmente deshabilitado. Vuelve al detalle del pedido y elige otro método de pago."),503
    return render_template("wompi_checkout.html",order=order,public_key=WOMPI_PUBLIC_KEY,integrity=wompi_signature(order["wompi_reference"] or order["order_no"],order["total"]),redirect_url=f"{public_base_url()}{url_for('wompi_result',order_no=order_no)}",checkout_url=WOMPI_BASE_URL)


@app.get("/pago/resultado/<order_no>")
@login_required
def wompi_result(order_no):
    user=current_user(); conn=db(); order=conn.execute("SELECT * FROM orders WHERE order_no=?",(order_no,)).fetchone(); conn.close()
    if not order or (order["user_id"]!=user["id"] and not user["is_admin"]): abort(404)
    return render_template("payment_result.html",order=order)


@app.get("/pago/mercadopago/<order_no>")
@login_required
def mercadopago_checkout(order_no):
    user=current_user(); conn=db(); order=conn.execute("SELECT * FROM orders WHERE order_no=?",(order_no,)).fetchone(); conn.close()
    if not order or (order["user_id"]!=user["id"] and not user["is_admin"]): abort(404)
    if order["payment_method"]!="mercadopago": return redirect(url_for("order_detail",order_no=order_no))
    if order["payment_status"]!="pending" or order["stock_restored"]:
        flash("Este intento de pago ya finalizó.","warning"); return redirect(url_for("order_detail",order_no=order_no))
    if not mercadopago_is_configured():
        return render_template("error.html",code=503,title="Mercado Pago no configurado",message="Configura las credenciales de producción y el secreto de notificaciones en PythonAnywhere."),503
    try:
        idempotency_key=order["mercadopago_idempotency_key"] or str(uuid.uuid4())
        if not order["mercadopago_idempotency_key"]:
            conn=db()
            conn.execute("UPDATE orders SET mercadopago_idempotency_key=?,updated_at=? WHERE id=? AND payment_status='pending'", (idempotency_key,now_iso(),order["id"]))
            conn.commit(); conn.close()
        headers={"Authorization":f"Bearer {MERCADOPAGO_ACCESS_TOKEN}","Content-Type":"application/json","X-Idempotency-Key":idempotency_key}
        if order["mercadopago_order_id"]:
            response=requests.get(f"{MERCADOPAGO_API_BASE_URL}/v1/orders/{order['mercadopago_order_id']}",headers=headers,timeout=15)
        else:
            # Los importes de COP son pesos enteros. Enviar ".00" hace que
            # Orders API rechace total_amount y unit_price con HTTP 400.
            amount=str(int(order["total"]))
            if int(order["total"]) <= 0:
                raise RuntimeError("El total del pedido debe ser mayor que cero")
            body={"type":"online","processing_mode":"manual","capture_mode":"automatic_async","total_amount":amount,"external_reference":order_no,
                "payer":{"email":order["email"]},"items":[{"title":f"Pedido NexaFarma #{order_no}","quantity":1,"unit_price":amount}]}
            # Mercado Pago cannot return the buyer to localhost/127.0.0.1.
            # Omit optional return URLs in local development; production uses the public HTTPS BASE_URL.
            public_base=urlparse(public_base_url())
            if public_base.scheme=="https" and public_base.hostname and public_base.hostname not in {"localhost","127.0.0.1"}:
                callback=f"{public_base_url()}{url_for('mercadopago_result',order_no=order_no)}"
                body["config"]={"online":{"success_url":callback,"failure_url":callback,"pending_url":callback,"auto_return":"approved"}}
            response=requests.post(f"{MERCADOPAGO_API_BASE_URL}/v1/orders",headers=headers,json=body,timeout=20)
        if response.status_code not in (200,201):
            try:
                error_data=response.json()
            except ValueError:
                error_data=None
            if isinstance(error_data,dict):
                detail={key:error_data.get(key) for key in ("error","message","errors","cause","status","status_detail") if key in error_data}
            elif isinstance(error_data,list):
                detail=error_data[:5]
            else:
                detail={"response":(response.text or "")[:1000]}
            detail_text=json.dumps(detail,ensure_ascii=False,default=str)[:1400]
            app.logger.error("Mercado Pago rechazó la order: HTTP %s, request_id=%s, detalles=%s",
                response.status_code,response.headers.get("x-request-id"),detail_text)
            raise RuntimeError(f"Mercado Pago rechazó la solicitud (HTTP {response.status_code})")
        data=response.json(); checkout_url=data.get("checkout_url"); remote_id=data.get("id")
        parsed=urlparse(checkout_url or "")
        if not remote_id or parsed.scheme!="https" or not (parsed.hostname or "").endswith(("mercadopago.com.co","mercadopago.com")):
            raise RuntimeError("Respuesta de checkout inválida")
        if data.get("external_reference") not in (None,order_no):
            raise RuntimeError("La referencia del checkout no coincide")
        conn=db(); conn.execute("UPDATE orders SET mercadopago_order_id=?,updated_at=? WHERE id=? AND payment_status='pending'",(remote_id,now_iso(),order["id"])); conn.commit(); conn.close()
        return redirect(checkout_url)
    except (requests.RequestException,ValueError,RuntimeError,KeyError):
        app.logger.exception("Fallo al iniciar checkout de Mercado Pago para %s",order_no)
        return render_template("error.html",code=502,title="No se pudo abrir el pago",message="El pedido quedó pendiente. Intenta nuevamente desde el detalle del pedido."),502


@app.get("/pago/mercadopago/resultado/<order_no>")
@login_required
def mercadopago_result(order_no):
    user=current_user(); conn=db(); order=conn.execute("SELECT * FROM orders WHERE order_no=?",(order_no,)).fetchone(); conn.close()
    if not order or (order["user_id"]!=user["id"] and not user["is_admin"]): abort(404)
    # El retorno del navegador nunca confirma el pago; solo el webhook firmado lo hace.
    return render_template("payment_result.html",order=order)


@app.post("/webhooks/wompi")
def wompi_webhook():
    payload = request.get_json(silent=True) or {}
    if payload.get("event") != "transaction.updated":
        return jsonify({"ok": True, "ignored": True})
    if not verify_wompi_event(payload):
        return jsonify({"error": "invalid signature"}), 401
    if payload.get("environment") and payload.get("environment") != WOMPI_ENVIRONMENT:
        return jsonify({"error": "wrong environment"}), 400

    tx = (payload.get("data") or {}).get("transaction") or {}
    reference = tx.get("reference")
    status = str(tx.get("status") or "").upper()
    txid = tx.get("id")
    if not reference or status not in FINAL_PAYMENT_STATUSES:
        return jsonify({"ok": True, "ignored": True})
    try:
        amount = int(tx.get("amount_in_cents") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid amount"}), 400

    payment_map = {"APPROVED": "approved", "DECLINED": "declined", "ERROR": "error", "VOIDED": "voided"}
    new_payment = payment_map[status]
    conn = db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        order = conn.execute("SELECT * FROM orders WHERE wompi_reference=?", (reference,)).fetchone()
        if not order:
            conn.rollback()
            conn.close()
            return jsonify({"ok": True, "ignored": True})

        if order["payment_status"] != "pending":
            conn.rollback()
            conn.close()
            return jsonify({"ok": True, "ignored": True, "final_status": True})

        expected_amount = int(order["total"]) * 100
        if amount != expected_amount or tx.get("currency") != "COP":
            conn.rollback()
            conn.close()
            return jsonify({"error": "amount mismatch"}), 400

        conn.execute(
            "UPDATE orders SET wompi_transaction_id=?,payment_status=?,status=CASE WHEN ?='approved' AND status='received' THEN 'paid' ELSE status END,paid_at=CASE WHEN ?='approved' THEN ? ELSE paid_at END,updated_at=? WHERE id=?",
            (txid, new_payment, new_payment, new_payment, now_iso(), now_iso(), order["id"]),
        )
        if new_payment in {"declined", "error", "voided"} and not order["stock_restored"]:
            for item in conn.execute("SELECT product_id,quantity FROM order_items WHERE order_id=?", (order["id"],)).fetchall():
                if item["product_id"]:
                    conn.execute("UPDATE products SET stock=stock+?,updated_at=? WHERE id=?", (item["quantity"], now_iso(), item["product_id"]))
            if order["coupon_code"]:
                conn.execute("UPDATE coupons SET uses_count=MAX(0,uses_count-1),used=CASE WHEN MAX(0,uses_count-1)<max_uses THEN 0 ELSE 1 END WHERE code=?", (order["coupon_code"],))
            conn.execute("UPDATE orders SET stock_restored=1,status=CASE WHEN status='received' THEN 'cancelled' ELSE status END WHERE id=?", (order["id"],))
        conn.commit()
    except Exception:
        conn.rollback()
        app.logger.exception("No se pudo procesar el webhook de Wompi")
        conn.close()
        return jsonify({"error": "processing failed"}), 500
    conn.close()

    audit("wompi_status", "order", order["id"], {"status": status, "transaction_id": txid})
    if new_payment == "approved":
        send_order_invoice(order["order_no"])
        order_status_email(order["order_no"], "paid")
    else:
        payment_status_email(order["order_no"], new_payment)
    return jsonify({"ok": True})


def verify_mercadopago_signature(data_id):
    if not MERCADOPAGO_WEBHOOK_SECRET or not data_id:
        return False
    signature=request.headers.get("x-signature",""); request_id=request.headers.get("x-request-id","")
    fields={}
    for part in signature.split(","):
        key,sep,value=part.strip().partition("=")
        if sep: fields[key]=value
    ts=fields.get("ts"); supplied=fields.get("v1")
    if not ts or not supplied or not request_id: return False
    manifest=f"id:{data_id};request-id:{request_id};ts:{ts};"
    import hmac
    expected=hmac.new(MERCADOPAGO_WEBHOOK_SECRET.encode(),manifest.encode(),hashlib.sha256).hexdigest()
    return secrets.compare_digest(expected.lower(),supplied.lower())


@app.post("/webhooks/mercadopago")
def mercadopago_webhook():
    payload=request.get_json(silent=True) or {}
    data_id=str((payload.get("data") or {}).get("id") or request.args.get("data.id") or "")
    if not verify_mercadopago_signature(data_id): return jsonify({"error":"invalid signature"}),401
    if payload.get("type") not in {"order",None} and request.args.get("type") not in {"order",None}:
        return jsonify({"ok":True,"ignored":True})
    try:
        response=requests.get(f"{MERCADOPAGO_API_BASE_URL}/v1/orders/{data_id}",headers={"Authorization":f"Bearer {MERCADOPAGO_ACCESS_TOKEN}"},timeout=15)
        if response.status_code!=200: return jsonify({"error":"provider lookup failed"}),503
        remote=response.json()
    except (requests.RequestException,ValueError):
        app.logger.exception("No se pudo consultar la orden de Mercado Pago")
        return jsonify({"error":"provider lookup failed"}),503
    if not isinstance(remote,dict):
        app.logger.warning("Mercado Pago devolvió una respuesta de orden con formato inválido")
        return jsonify({"error":"invalid provider response"}),503
    order_no=remote.get("external_reference")
    if not order_no or remote.get("id") is None: return jsonify({"ok":True,"ignored":True})
    conn=db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        order=conn.execute("SELECT * FROM orders WHERE order_no=? AND payment_method='mercadopago'",(order_no,)).fetchone()
        if not order or (order["mercadopago_order_id"] and str(order["mercadopago_order_id"])!=str(remote["id"])):
            conn.rollback(); return jsonify({"ok":True,"ignored":True})
        if order["payment_status"]!="pending": conn.rollback(); return jsonify({"ok":True,"duplicate":True})
        try: paid=int(float(remote.get("total_paid_amount") or 0)); total=int(float(remote.get("total_amount") or 0))
        except (TypeError,ValueError): conn.rollback(); return jsonify({"error":"invalid amount"}),400
        currency=remote.get("currency_id") or remote.get("currency")
        if total!=int(order["total"]) or currency!="COP": conn.rollback(); return jsonify({"error":"amount mismatch"}),400
        status=str(remote.get("status") or "").lower()
        if status=="processed" and paid>=int(order["total"]):
            payments=((remote.get("transactions") or {}).get("payments") or [])
            payment_id=str((payments[0] or {}).get("id") or remote["id"]) if payments else str(remote["id"])
            conn.execute("UPDATE orders SET mercadopago_order_id=?,mercadopago_payment_id=?,payment_status='approved',status=CASE WHEN status='received' THEN 'paid' ELSE status END,paid_at=?,updated_at=? WHERE id=?",(remote["id"],payment_id,now_iso(),now_iso(),order["id"]))
            outcome="approved"
        elif status in {"cancelled","expired"}:
            conn.execute("UPDATE orders SET mercadopago_order_id=?,payment_status='voided',stock_restored=1,status=CASE WHEN status='received' THEN 'cancelled' ELSE status END,updated_at=? WHERE id=?",(remote["id"],now_iso(),order["id"]))
            for item in conn.execute("SELECT product_id,quantity FROM order_items WHERE order_id=?",(order["id"],)).fetchall():
                if item["product_id"]: conn.execute("UPDATE products SET stock=stock+?,updated_at=? WHERE id=?",(item["quantity"],now_iso(),item["product_id"]))
            if order["coupon_code"]:
                conn.execute("UPDATE coupons SET uses_count=MAX(0,uses_count-1),used=CASE WHEN MAX(0,uses_count-1)<max_uses THEN 0 ELSE 1 END WHERE code=?",(order["coupon_code"],))
            outcome="voided"
        else:
            conn.rollback(); return jsonify({"ok":True,"pending":True})
        conn.commit()
    except Exception:
        conn.rollback(); app.logger.exception("No se pudo aplicar el webhook de Mercado Pago"); return jsonify({"error":"processing failed"}),500
    finally: conn.close()
    audit("mercadopago_status","order",order["id"],{"status":outcome,"provider_order_id":str(remote["id"])})
    if outcome=="approved":
        send_order_invoice(order_no); order_status_email(order_no,"paid")
    else: payment_status_email(order_no,outcome)
    return jsonify({"ok":True})


@app.get("/pedido/<order_no>")
@login_required
def order_detail(order_no):
    user=current_user(); conn=db(); order=conn.execute("SELECT * FROM orders WHERE order_no=?",(order_no,)).fetchone()
    if not order or (order["user_id"]!=user["id"] and not user["is_admin"]): conn.close(); return render_template("error.html",code=404,title="Pedido no encontrado",message="No pudimos localizar este pedido."),404
    items=conn.execute("SELECT * FROM order_items WHERE order_id=?",(order["id"],)).fetchall(); reviewable=conn.execute("SELECT DISTINCT p.id,p.name FROM order_items oi JOIN products p ON p.id=oi.product_id WHERE oi.order_id=?",(order["id"],)).fetchall(); conn.close(); return render_template("order.html",order=order,items=items,reviewable=reviewable)


@app.get("/factura/<order_no>")
@login_required
def invoice_page(order_no):
    user=current_user(); conn=db(); order=conn.execute("SELECT * FROM orders WHERE order_no=?",(order_no,)).fetchone(); items=conn.execute("SELECT * FROM order_items WHERE order_id=?",(order["id"],)).fetchall() if order else [] ; conn.close()
    if not order or (order["user_id"]!=user["id"] and not user["is_admin"]): abort(404)
    return render_template("invoice.html",order=order,items=items)


@app.get("/factura/<order_no>/pdf")
@login_required
def invoice_pdf_route(order_no):
    user=current_user(); conn=db(); order=conn.execute("SELECT * FROM orders WHERE order_no=?",(order_no,)).fetchone(); items=conn.execute("SELECT * FROM order_items WHERE order_id=?",(order["id"],)).fetchall() if order else []; conn.close()
    if not order or (order["user_id"]!=user["id"] and not user["is_admin"]): abort(404)
    pdf=invoice_pdf(order,items)
    if not pdf: return redirect(url_for("invoice_page",order_no=order_no))
    return app.response_class(pdf,mimetype="application/pdf",headers={"Content-Disposition":f"attachment; filename=NexaFarma-{order_no}.pdf"})


@app.post("/pedido/<order_no>/repetir")
@login_required
def reorder(order_no):
    user=current_user(); conn=db(); order=conn.execute("SELECT * FROM orders WHERE order_no=?",(order_no,)).fetchone(); items=conn.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id",(order["id"],)).fetchall() if order else []; conn.close()
    if not order or (order["user_id"]!=user["id"] and not user["is_admin"]): abort(404)
    cart=[]; conn=db()
    for item in items:
        if not item["product_id"]: continue
        p=conn.execute("SELECT id,active,stock FROM products WHERE id=?",(item["product_id"],)).fetchone()
        if not p or not p["active"] or not p["stock"]: continue
        size=conn.execute("SELECT id FROM sizes WHERE name=? AND active=1 ORDER BY id LIMIT 1",(item["size"],)).fetchone(); top_ids=[]
        if item["toppings"]:
            for name in [x.strip() for x in item["toppings"].split(",")]:
                t=conn.execute("SELECT id FROM toppings WHERE name=? AND active=1",(name,)).fetchone();
                if t: top_ids.append(t["id"])
        cart.append({"product_id":item["product_id"],"qty":min(int(item["quantity"]),int(p["stock"])),"size_id":size["id"] if size else None,"flavor":item["flavor"] or "","topping_ids":top_ids})
    conn.close(); session["cart"]=cart; session.modified=True; flash("Tu pedido fue cargado nuevamente al carrito.","success"); return redirect(url_for("cart_view"))


@app.post("/pedido/<int:oid>/reseña")
@login_required
def create_review(oid):
    user=current_user()
    try:
        product_id=int(request.form.get("product_id",0) or 0)
        rating=max(1,min(5,int(request.form.get("rating",5) or 5)))
    except (TypeError, ValueError):
        flash("La calificación enviada no es válida.", "error")
        return redirect(url_for("order_history"))
    comment=request.form.get("comment","").strip()[:600]
    conn=db(); order=conn.execute("SELECT id FROM orders WHERE id=? AND user_id=? AND status='delivered'",(oid,user["id"])).fetchone(); purchased=conn.execute("SELECT 1 FROM order_items WHERE order_id=? AND product_id=?",(oid,product_id)).fetchone()
    if not order or not purchased: conn.close(); flash("Solo puedes reseñar productos de pedidos entregados.","error"); return redirect(url_for("order_detail",order_no=request.form.get("order_no","")))
    try: conn.execute("INSERT INTO reviews(user_id,product_id,order_id,rating,comment,active,created_at) VALUES(?,?,?,?,?,1,?)",(user["id"],product_id,oid,rating,comment,now_iso())); conn.commit(); flash("Gracias por tu reseña.","success")
    except sqlite3.IntegrityError: conn.rollback(); flash("Ya dejaste una reseña para este producto en ese pedido.","error")
    conn.close(); return redirect(url_for("order_detail",order_no=request.form.get("order_no","")))


@app.get("/nosotros")
def about(): return render_template("about.html")


def _render_legal_policy(kind):
    policies = {
        "privacidad": {
            "title": "Política de privacidad",
            "eyebrow": "DATOS PERSONALES",
            "intro": "Esta política explica qué información recibe NexaFarma cuando navegas, creas una cuenta, compras o contactas a la tienda, y cómo solicitar su consulta o corrección.",
            "sections": [
                {"title":"Responsable y contacto","text":f"{CONFIG['legal_name']} opera esta tienda en línea. Ubicación publicada: {CONFIG['address']}. Para consultas, solicitudes sobre tus datos o reclamos, usa el formulario de contacto o escribe a {MAIL_ADMIN or 'nuestro canal de contacto publicado'}. Identificación tributaria: {CONFIG['nit'] or 'no publicada en esta versión del sitio'}."},
                {"title":"Datos que usamos","text":"Podemos tratar nombre, correo electrónico, teléfono, dirección de entrega, ciudad, referencias del pedido, productos comprados, preferencias que compartas, mensajes de soporte y datos técnicos mínimos necesarios para mantener tu sesión y proteger el sitio. Las contraseñas se almacenan protegidas mediante hash; NexaFarma no solicita ni almacena la clave de tu banco."},
                {"title":"Para qué los usamos","bullets":["Crear y proteger tu cuenta, mantener el carrito y procesar pedidos, pagos, entregas, comprobantes y solicitudes de soporte.","Enviar mensajes transaccionales, como bienvenida, recuperación de cuenta, confirmaciones y novedades de un pedido.","Prevenir fraude, resolver disputas, cumplir obligaciones aplicables y mantener registros operativos.","Si usas el agente IA, enviar el mensaje y el contexto necesario de la tienda a Groq para generar la respuesta. Si preguntas por tus pedidos, el agente consulta los datos de tu propia sesión y puede incluir el resultado necesario en la solicitud al proveedor."]},
                {"title":"Proveedores externos","text":"Para operar funciones solicitadas, el sitio puede intercambiar la información mínima necesaria con proveedores como Wompi (pagos), Google Gmail API u otro proveedor configurado (correo) y Groq (agente IA). El copiloto administrativo envía a Groq la instrucción y el contexto de catálogo/operación necesario para responder, pero no datos de pedidos de clientes en sus herramientas actuales. Cada proveedor procesa datos según sus propios términos y políticas. El pago puede abrir una experiencia alojada por Wompi."},
                {"title":"Conservación y seguridad","text":"Conservamos la información mientras sea necesaria para administrar la cuenta, pedidos, atención, seguridad y obligaciones aplicables. Aplicamos controles técnicos razonables; ningún servicio en Internet puede prometer riesgo cero. No compartas contraseñas, números de tarjeta ni códigos bancarios en el chat o en los formularios."},
                {"title":"Tus solicitudes","text":"Puedes solicitar acceso, actualización, corrección o eliminación de datos, y presentar consultas o reclamos por los canales de contacto de NexaFarma. Indica el correo asociado a la cuenta y el pedido si aplica. Es posible que debamos conservar cierta información por obligaciones o disputas legítimas."},
                {"title":"Cambios","text":"Podemos actualizar esta política cuando cambien la tienda, los proveedores o las prácticas de datos. Publicaremos la versión vigente en esta página."}
            ]
        },
        "cookies": {
            "title": "Política de cookies y preferencias",
            "eyebrow": "TU PRIVACIDAD EN EL NAVEGADOR",
            "intro": "NexaFarma usa almacenamiento esencial para que la tienda funcione y recuerda tu elección de cookies en este navegador. Puedes cambiar tu preferencia desde el enlace al pie de página.",
            "sections": [
                {"title":"Necesarias","text":"La cookie de sesión de la aplicación mantiene el inicio de sesión, el carrito y protecciones de seguridad como CSRF. Estas funciones son necesarias para prestar el servicio que solicitas."},
                {"title":"Preferencias del navegador","text":"El tema claro/oscuro y la decisión del panel de cookies se guardan en almacenamiento local del navegador (localStorage), no se usan para publicidad y pueden borrarse desde la configuración del navegador."},
                {"title":"Analítica y publicidad","text":"En la versión actual no activamos herramientas propias de analítica ni publicidad basadas en cookies. Si NexaFarma incorpora estas herramientas, deberán quedar descritas aquí y mantenerse desactivadas hasta que el visitante otorgue la preferencia correspondiente."},
                {"title":"Servicios externos","text":"Wompi y otros sitios de terceros pueden utilizar tecnologías propias cuando abres sus servicios. Su uso se rige por sus políticas y controles."},
                {"title":"Cambiar elección","text":"Selecciona “Preferencias de cookies” al pie de cualquier página para aceptar todas las categorías opcionales disponibles o mantener solo las necesarias. También puedes borrar almacenamiento local desde tu navegador; el panel volverá a aparecer."}
            ]
        },
        "condiciones": {
            "title": "Condiciones de compra y políticas de NexaFarma",
            "eyebrow": "COMPRA Y SERVICIO",
            "intro": "Estas condiciones describen cómo funciona el catálogo y la compra en NexaFarma. Antes de pagar, revisa el producto, las opciones, la dirección y el total mostrado en el checkout.",
            "sections": [
                {"title":"Productos, opciones y precios","text":"El catálogo muestra productos y disponibilidad orientativa. La existencia puede cambiar con el inventario. El checkout presenta el subtotal, descuentos, entrega y total antes de enviar el pedido."},
                {"title":"Pedidos y pagos","text":"Un pedido queda registrado al completar el checkout. El estado del pago puede ser pendiente hasta que el proveedor o el equipo de NexaFarma lo confirme. Los pagos en línea se procesan mediante el proveedor mostrado durante el checkout; NexaFarma no recibe la contraseña bancaria ni el número completo de tarjeta."},
                {"title":"Entrega y seguimiento","text":"Revisa la dirección y los datos de contacto antes de confirmar. La zona atendida, el método y el costo de entrega se muestran durante la compra. Puedes consultar el estado en tu cuenta o comunicarte con NexaFarma con el número del pedido."},
                {"title":"Cambios, cancelaciones y reembolsos","text":"Si necesitas corregir o cancelar un pedido, contacta a NexaFarma cuanto antes e incluye su número. La posibilidad de cambio depende del estado de preparación, despacho, pago y de las normas aplicables. Cuando corresponda un reembolso, se informa el canal y la referencia; el tiempo de reflejo depende del medio de pago."},
                {"title":"Cuenta y uso responsable","text":"Mantén actualizados tus datos de entrega y protege tu contraseña. No uses la tienda para fraude, automatización abusiva, suplantación o para interferir con su funcionamiento."},
                {"title":"Agente IA y contenido","text":"El asistente ayuda a navegar el catálogo y revisar el carrito, pero no da consejos médicos, no recomienda medicamentos y no indica dosis. Revisa precios y total antes de comprar. El asistente no confirma pedidos ni realiza pagos. Las propuestas del copiloto administrativo requieren revisión y confirmación de un administrador."},
                {"title":"Atención y actualización de políticas","text":f"Para dudas, cambios o reclamos, utiliza Contacto o escribe a {MAIL_ADMIN or 'nuestro canal publicado'}; también puedes usar el WhatsApp publicado en la tienda. NexaFarma publicará aquí cualquier actualización de estas condiciones."}
            ]
        },
    }
    policy = policies.get(kind)
    if not policy:
        abort(404)
    return render_template("legal.html", policy=policy)


@app.get("/politicas/privacidad")
def privacy_policy():
    return _render_legal_policy("privacidad")


@app.get("/politicas/cookies")
def cookie_policy():
    return _render_legal_policy("cookies")


@app.get("/politicas/condiciones")
def terms_policy():
    return _render_legal_policy("condiciones")


@app.route("/contacto",methods=["GET","POST"])
def contact():
    if request.method=="POST":
        name=request.form.get("name","").strip()[:100]; email=request.form.get("email","").strip().lower()[:120]; phone=re.sub(r"\D","",request.form.get("phone","") or "")[:15]; subject=request.form.get("subject","").strip()[:140]; message=request.form.get("message","").strip()[:1500]
        if not name or "@" not in email or not message: flash("Completa nombre, correo y mensaje.","error"); return redirect(url_for("contact"))
        conn=db(); conn.execute("INSERT INTO contact_messages(name,email,phone,subject,message,created_at) VALUES(?,?,?,?,?,?)",(name,email,phone,subject,message,now_iso())); conn.commit(); conn.close()
        html_body=f"<div style='font-family:Arial,sans-serif'><h2>Nuevo contacto NexaFarma</h2><p><b>Nombre:</b> {html.escape(name)}<br/><b>Correo:</b> {html.escape(email)}<br/><b>Teléfono:</b> {html.escape(phone)}<br/><b>Asunto:</b> {html.escape(subject)}</p><div style='padding:14px;background:#e4f5f2;border-radius:12px'>{html.escape(message).replace(chr(10),'<br/>')}</div></div>"
        admin_mail_sent = bool(MAIL_ADMIN and send_email(MAIL_ADMIN,f"NexaFarma · Nuevo contacto · {subject or 'Consulta'}",html_body))
        customer_mail_sent = contact_ack_email(name, email, subject)
        if admin_mail_sent and customer_mail_sent:
            flash("Mensaje recibido. Te enviamos una confirmación y el equipo recibió la alerta.","success")
        elif admin_mail_sent or customer_mail_sent:
            flash("Mensaje recibido y guardado. Una de las notificaciones por correo no está disponible todavía.","success")
        else:
            flash("Mensaje recibido y guardado. Las notificaciones por correo todavía no están disponibles.","success")
        return redirect(url_for("contact"))
    return render_template("contact.html")


# ---------------- ADMIN ----------------
@app.get("/admin")
@admin_required
def admin():
    conn = db()
    stats = {
        # Los timestamps se guardan en UTC; el dashboard presenta el día comercial de Colombia (UTC-5).
        "sales_today": conn.execute("SELECT COALESCE(SUM(total),0) FROM orders WHERE payment_status='approved' AND status!='cancelled' AND date(created_at,'-5 hours')=date('now','-5 hours')").fetchone()[0],
        "orders_today": conn.execute("SELECT COUNT(*) FROM orders WHERE status!='cancelled' AND date(created_at,'-5 hours')=date('now','-5 hours')").fetchone()[0],
        "orders": conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
        "clients": conn.execute("SELECT COUNT(*) FROM users WHERE is_admin=0").fetchone()[0],
        "active_orders": conn.execute("SELECT COUNT(*) FROM orders WHERE status IN ('received','paid','preparing','on_the_way','delivering')").fetchone()[0],
        "new_orders": conn.execute("SELECT COUNT(*) FROM orders WHERE status='received'").fetchone()[0],
        "unpaid": conn.execute("SELECT COUNT(*) FROM orders WHERE payment_status='pending' AND status!='cancelled'").fetchone()[0],
        "low_stock": conn.execute("SELECT COUNT(*) FROM products WHERE active=1 AND stock<=5").fetchone()[0],
        "messages": conn.execute("SELECT COUNT(*) FROM contact_messages WHERE status='new'").fetchone()[0],
        "approved_sales": conn.execute("SELECT COALESCE(SUM(total),0) FROM orders WHERE payment_status='approved' AND status!='cancelled'").fetchone()[0],
        "sales_month": conn.execute("SELECT COALESCE(SUM(total),0) FROM orders WHERE payment_status='approved' AND status!='cancelled' AND strftime('%Y-%m',datetime(created_at,'-5 hours'))=strftime('%Y-%m',datetime('now','-5 hours'))").fetchone()[0],
    }
    monthly_sales = conn.execute("""SELECT strftime('%Y-%m',datetime(created_at,'-5 hours')) label,COALESCE(SUM(total),0) total
        FROM orders WHERE payment_status='approved' AND status!='cancelled'
        GROUP BY label ORDER BY label DESC LIMIT 6""").fetchall()
    max_monthly_sales = max((int(row["total"] or 0) for row in monthly_sales), default=0)
    top_products = conn.execute("""SELECT oi.product_name name,SUM(oi.quantity) quantity
        FROM order_items oi JOIN orders o ON o.id=oi.order_id
        WHERE o.status!='cancelled' AND (o.payment_status='approved' OR o.status='delivered')
        GROUP BY oi.product_name ORDER BY quantity DESC LIMIT 6""").fetchall()
    recent = conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT 10").fetchall()
    low_stock = conn.execute("SELECT * FROM products WHERE active=1 AND stock<=5 ORDER BY stock ASC,name LIMIT 8").fetchall()
    store_status = conn.execute("SELECT value FROM site_settings WHERE key='store_status'").fetchone()
    conn.close()
    return render_template("admin.html", stats=stats, recent=recent, monthly_sales=monthly_sales,
                           max_monthly_sales=max_monthly_sales, top_products=top_products,
                           low_stock=low_stock, store_status=store_status[0] if store_status else "open")


@app.route("/admin/productos",methods=["GET","POST"])
@admin_required
def admin_products():
    conn=db()
    if request.method=="POST":
        name=request.form.get("name","").strip()[:120]; description=request.form.get("description","").strip()[:500]
        try: price=int(request.form.get("price","0")); stock=int(request.form.get("stock","99")); category_id=int(request.form.get("category_id")) if request.form.get("category_id") else None
        except ValueError: conn.close(); flash("Precio, stock o categoría no válidos.","error"); return redirect(url_for("admin_products"))
        if not 0<=price<=10_000_000 or not 0<=stock<=1_000_000:
            conn.close(); flash("Precio o inventario fuera del rango permitido.","error"); return redirect(url_for("admin_products"))
        if category_id and not conn.execute("SELECT 1 FROM categories WHERE id=?",(category_id,)).fetchone():
            conn.close(); flash("La categoría seleccionada no existe.","error"); return redirect(url_for("admin_products"))
        if not name: conn.close(); flash("El nombre es obligatorio.","error"); return redirect(url_for("admin_products"))
        image_path=""; file=request.files.get("image")
        if file and file.filename:
            try:
                image_path=save_product_image(file)
            except (ValueError, RuntimeError) as exc:
                conn.close(); flash(str(exc),"error"); return redirect(url_for("admin_products"))
        try:
            cursor=conn.execute("INSERT INTO products(category_id,name,description,price,image,active,featured,stock,requires_prescription,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?,?,?,?)",(category_id,name,description,price,image_path,1 if request.form.get("featured") else 0,stock,1 if request.form.get("requires_prescription") else 0,now_iso(),now_iso()))
            product_id=cursor.lastrowid; conn.commit(); conn.close()
        except sqlite3.IntegrityError:
            conn.rollback(); conn.close(); remove_product_image(image_path)
            flash("No se pudo crear el producto. Revisa sus datos y la categoría.","error"); return redirect(url_for("admin_products"))
        flash("Producto creado.","success"); audit("product_created","product",product_id,{"name":name}); return redirect(url_for("admin_products"))
    products=conn.execute("SELECT p.*,c.name category FROM products p LEFT JOIN categories c ON c.id=p.category_id ORDER BY p.id DESC").fetchall(); categories=conn.execute("SELECT * FROM categories ORDER BY name").fetchall(); conn.close(); return render_template("admin_products.html",products=products,categories=categories)


@app.get("/admin/agente")
@admin_required
def admin_agent_page():
    conn = db()
    categories = conn.execute("SELECT id,name FROM categories WHERE active=1 ORDER BY name").fetchall()
    conn.close()
    return render_template("admin_agent.html", categories=categories)


ADMIN_AGENT_TOOLS = [
    {"type":"function","function":{"name":"get_admin_overview","description":"Consulta métricas resumidas de pedidos, pagos e inventario de la tienda.","parameters":{"type":"object","properties":{},"required":[],"additionalProperties":False}}},
    {"type":"function","function":{"name":"search_admin_products","description":"Busca productos del catálogo por nombre o descripción, activos o inactivos.","parameters":{"type":"object","properties":{"query":{"type":"string","maxLength":80}},"required":["query"],"additionalProperties":False}}},
    {"type":"function","function":{"name":"prepare_new_product","description":"Prepara una propuesta editable de producto. Nunca la guarda ni la publica; el administrador debe revisar y confirmar por separado.","parameters":{"type":"object","properties":{"name":{"type":"string","maxLength":120},"description":{"type":"string","maxLength":500},"price":{"type":"integer","minimum":0,"maximum":10000000},"stock":{"type":"integer","minimum":0,"maximum":10000},"category":{"type":"string","maxLength":80},"featured":{"type":"boolean"}},"required":["name","description","price","stock","category","featured"],"additionalProperties":False}}},
]


def _admin_agent_tool(name, args):
    if name == "get_admin_overview":
        conn = db()
        result = {
            "orders_total": conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
            "orders_to_prepare": conn.execute("SELECT COUNT(*) FROM orders WHERE status='received'").fetchone()[0],
            "payments_pending": conn.execute("SELECT COUNT(*) FROM orders WHERE payment_status='pending' AND status!='cancelled'").fetchone()[0],
            "products_active": conn.execute("SELECT COUNT(*) FROM products WHERE active=1").fetchone()[0],
            "products_low_stock": conn.execute("SELECT COUNT(*) FROM products WHERE active=1 AND stock<=5").fetchone()[0],
            "store_status": (conn.execute("SELECT value FROM site_settings WHERE key='store_status'").fetchone() or ["open"])[0],
        }
        conn.close()
        return {"ok": True, "overview": result}
    if name == "search_admin_products":
        query = str(args.get("query") or "").strip()[:80]
        if not query:
            return {"ok": False, "message": "Escribe el nombre o descripción para buscar."}
        conn = db()
        rows = conn.execute(
            "SELECT p.id,p.name,p.description,p.price,p.stock,p.active,c.name category FROM products p LEFT JOIN categories c ON c.id=p.category_id WHERE p.name LIKE ? OR p.description LIKE ? ORDER BY p.name LIMIT 10",
            (f"%{query}%", f"%{query}%"),
        ).fetchall()
        conn.close()
        return {"ok": True, "products": [dict(row) for row in rows]}
    if name == "prepare_new_product":
        try:
            price, stock = int(args.get("price")), int(args.get("stock"))
        except (TypeError, ValueError):
            return {"ok": False, "message": "El precio y el inventario deben ser números enteros."}
        product_name = str(args.get("name") or "").strip()[:120]
        description = str(args.get("description") or "").strip()[:500]
        category_name = str(args.get("category") or "").strip()[:80]
        if not product_name or not description or price < 0 or price > 10_000_000 or stock < 0 or stock > 10_000:
            return {"ok": False, "message": "La propuesta necesita nombre, descripción y valores válidos para precio e inventario."}
        conn = db()
        category = conn.execute("SELECT id,name FROM categories WHERE active=1 AND lower(name)=lower(?)", (category_name,)).fetchone()
        conn.close()
        return {
            "ok": True,
            "product_draft": {
                "name": product_name, "description": description, "price": price, "stock": stock,
                "category_id": category["id"] if category else None,
                "category_name": category["name"] if category else "",
                "featured": bool(args.get("featured")),
            },
        }
    return {"ok": False, "message": "Esa acción administrativa no está disponible."}


@app.post("/api/admin/agent")
@admin_required
def api_admin_agent():
    data = request.get_json(silent=True) or {}
    message = str(data.get("message") or "").strip()[:700]
    if not message:
        return jsonify({"error": "Escribe una instrucción.", "mode": "invalid"}), 400
    if not GROQ_API_KEY:
        return jsonify({"error": "El agente administrativo necesita una clave Groq válida.", "mode": "unavailable"}), 503
    conn = db()
    categories = [row["name"] for row in conn.execute("SELECT name FROM categories WHERE active=1 ORDER BY name").fetchall()]
    conn.close()
    system = (
        "Eres el copiloto administrativo de NexaFarma, una farmacia ficticia de demostración. Responde en español claro. No presentes el sistema como una farmacia real autorizada. No redactes consejos de tratamiento ni publiques productos con receta sin validación profesional. "
        "Usa herramientas para consultar los datos actuales. Si el administrador pide crear un producto, "
        "propón nombre, descripción, precio COP, stock, categoría existente y si debe destacarse. "
        "La herramienta solo prepara una propuesta; no afirma que el producto ya exista ni intenta guardarlo. "
        "El administrador revisará y confirmará la publicación en otra acción. No inventes métricas ni inventario. "
        f"Categorías activas disponibles: {', '.join(categories) or 'ninguna'}. "
        "No ejecutes pagos, reembolsos, cambios de pedidos, cuentas ni acciones destructivas."
    )
    payload = {
        "model": AGENT_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": message}],
        "tools": ADMIN_AGENT_TOOLS,
        "tool_choice": "auto",
        "parallel_tool_calls": False,
        "temperature": 0.1,
        "max_completion_tokens": 700,
    }
    if "gpt-oss" in AGENT_MODEL.lower():
        payload["reasoning_effort"] = "low"
    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {GROQ_API_KEY}"},
            json=payload,
            timeout=(5, 25),
        )
        if response.status_code != 200:
            app.logger.warning("Agente admin Groq respondió HTTP %s", response.status_code)
            return jsonify({"error": "La IA administrativa no respondió correctamente. Inténtalo de nuevo.", "mode": "provider-error"}), 502
        choices = response.json().get("choices") or []
        assistant = (choices[0].get("message") or {}) if choices else {}
        tool_calls = assistant.get("tool_calls") or []
        if not tool_calls:
            return jsonify({"reply": str(assistant.get("content") or "No recibí una respuesta completa. Reformula la instrucción."), "mode": "admin-agent"})
        tool_call = tool_calls[0]
        function = tool_call.get("function") or {}
        tool_name = str(function.get("name") or "")
        if tool_name not in {item["function"]["name"] for item in ADMIN_AGENT_TOOLS}:
            return jsonify({"error": "La IA propuso una acción que no está permitida.", "mode": "blocked"}), 400
        args = json.loads(function.get("arguments") or "{}")
        if not isinstance(args, dict):
            return jsonify({"error": "La IA devolvió parámetros inválidos.", "mode": "invalid"}), 502
        result = _admin_agent_tool(tool_name, args)
        if result.get("product_draft"):
            draft = result["product_draft"]
            reply = f"Preparé una propuesta para “{draft['name']}”. Revisa y corrige los campos antes de confirmar; todavía no se ha añadido al catálogo."
            return jsonify({"reply": reply, "mode": "product-draft", "product_draft": draft})
        if tool_name == "get_admin_overview":
            return jsonify({"reply": "Aquí tienes el resumen actual de la tienda.", "mode": "admin-tool", **result})
        if tool_name == "search_admin_products":
            return jsonify({"reply": f"Encontré {len(result.get('products', []))} resultado(s).", "mode": "admin-tool", **result})
        return jsonify({"reply": result.get("message", "No pude completar la consulta."), "mode": "admin-tool", **result})
    except (requests.RequestException, ValueError, TypeError, KeyError, AttributeError):
        app.logger.exception("Error procesando el agente administrativo")
        return jsonify({"error": "No pude procesar la respuesta de la IA. No se guardó ningún cambio.", "mode": "error"}), 502


@app.post("/api/admin/agent/products")
@admin_required
def api_admin_agent_create_product():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()[:120]
    description = str(data.get("description") or "").strip()[:500]
    try:
        price, stock, category_id = int(data.get("price")), int(data.get("stock")), int(data.get("category_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "Revisa precio, stock y categoría."}), 400
    featured = data.get("featured") is True
    if data.get("confirmed") is not True or not name or not description or not 0 <= price <= 10_000_000 or not 0 <= stock <= 10_000:
        return jsonify({"error": "Confirma la publicación y revisa los datos del producto."}), 400
    conn = db()
    conn.execute("BEGIN IMMEDIATE")
    category = conn.execute("SELECT id FROM categories WHERE id=? AND active=1", (category_id,)).fetchone()
    if not category:
        conn.rollback()
        conn.close()
        return jsonify({"error": "Selecciona una categoría activa."}), 400
    duplicate = conn.execute("SELECT id FROM products WHERE lower(trim(name))=lower(trim(?)) LIMIT 1", (name,)).fetchone()
    if duplicate:
        conn.rollback()
        conn.close()
        return jsonify({"error": "Ya existe un producto con ese nombre. Edita el existente desde Productos."}), 409
    try:
        cursor = conn.execute(
            "INSERT INTO products(category_id,name,description,price,image,active,featured,stock,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?,?,?)",
            (category_id, name, description, price, "", 1 if featured else 0, stock, now_iso(), now_iso()),
        )
        product_id = cursor.lastrowid
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback(); conn.close()
        return jsonify({"error": "No se pudo añadir el producto. Comprueba si ya existe."}), 409
    conn.close()
    audit("admin_agent_product_created", "product", product_id, {"name": name})
    return jsonify({"ok": True, "product_id": product_id, "message": f"Producto “{name}” añadido al catálogo."}), 201


@app.route("/admin/productos/<int:pid>/editar",methods=["GET","POST"])
@admin_required
def admin_product_edit(pid):
    conn=db(); product_row=conn.execute("SELECT * FROM products WHERE id=?",(pid,)).fetchone(); categories=conn.execute("SELECT * FROM categories ORDER BY name").fetchall()
    if not product_row: conn.close(); abort(404)
    if request.method=="POST":
        name=request.form.get("name","").strip()[:120]; description=request.form.get("description","").strip()[:500]
        try: price=int(request.form.get("price","0")); stock=int(request.form.get("stock","0")); category_id=int(request.form.get("category_id")) if request.form.get("category_id") else None
        except ValueError: conn.close(); flash("Datos inválidos.","error"); return redirect(url_for("admin_product_edit",pid=pid))
        if not 0<=price<=10_000_000 or not 0<=stock<=1_000_000 or (category_id and not conn.execute("SELECT 1 FROM categories WHERE id=?",(category_id,)).fetchone()):
            conn.close(); flash("Revisa precio, inventario y categoría.","error"); return redirect(url_for("admin_product_edit",pid=pid))
        old_image=product_row["image"] or ""; image_path=old_image; file=request.files.get("image")
        if file and file.filename:
            try:
                image_path=save_product_image(file)
            except (ValueError, RuntimeError) as exc:
                conn.close(); flash(str(exc),"error"); return redirect(url_for("admin_product_edit",pid=pid))
        if not name:
            conn.close(); flash("El nombre del producto es obligatorio.","error"); return redirect(url_for("admin_product_edit",pid=pid))
        try:
            conn.execute("UPDATE products SET category_id=?,name=?,description=?,price=?,image=?,featured=?,stock=?,requires_prescription=?,updated_at=? WHERE id=?",(category_id,name,description,price,image_path,1 if request.form.get("featured") else 0,stock,1 if request.form.get("requires_prescription") else 0,now_iso(),pid)); conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback(); conn.close()
            if image_path != old_image: remove_product_image(image_path)
            flash("No se pudo guardar el producto. Revisa sus datos.","error"); return redirect(url_for("admin_product_edit",pid=pid))
        conn.close()
        if image_path != old_image: remove_product_image(old_image)
        flash("Producto actualizado.","success"); return redirect(url_for("admin_products"))
    conn.close(); return render_template("admin_product_edit.html",product=product_row,categories=categories)


@app.post("/admin/productos/<int:pid>/estado")
@admin_required
def product_toggle(pid):
    conn=db(); conn.execute("UPDATE products SET active=1-active,updated_at=? WHERE id=?",(now_iso(),pid)); conn.commit(); conn.close(); flash("Estado del producto actualizado.","success"); return redirect(url_for("admin_products"))


@app.post("/admin/productos/<int:pid>/eliminar")
@admin_required
def product_delete(pid):
    conn=db(); product=conn.execute("SELECT name,image FROM products WHERE id=?",(pid,)).fetchone()
    if not product: conn.close(); abort(404)
    conn.execute("DELETE FROM products WHERE id=?",(pid,)); conn.commit(); conn.close()
    remove_product_image(product["image"])
    audit("product_deleted","product",pid,{"name":product["name"]})
    flash("Producto eliminado.","success"); return redirect(url_for("admin_products"))


@app.get("/admin/pedidos")
@admin_required
def admin_orders():
    conn = db()
    q = request.args.get("q", "").strip()[:80]
    requested_status = request.args.get("status", "")
    status = requested_status if requested_status in VALID_ORDER_STATUSES else ""
    requested_payment = request.args.get("payment", "")
    payment = requested_payment if requested_payment in {"pending", "approved", "declined", "error", "voided", "refunded"} else ""
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    page_size = 30
    sql = " FROM orders WHERE 1=1"
    args = []
    if q:
        sql += " AND (order_no LIKE ? OR customer_name LIKE ? OR phone LIKE ? OR email LIKE ?)"
        needle = f"%{q}%"
        args += [needle] * 4
    if status:
        sql += " AND status=?"
        args.append(status)
    if payment:
        sql += " AND payment_status=?"
        args.append(payment)
    total = int(conn.execute("SELECT COUNT(*)" + sql, args).fetchone()[0])
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, pages)
    orders = conn.execute("SELECT *" + sql + " ORDER BY id DESC LIMIT ? OFFSET ?", args + [page_size, (page - 1) * page_size]).fetchall()
    conn.close()
    return render_template("admin_orders.html", orders=orders, filter_q=q, filter_status=status,
                           filter_payment=payment, page=page, pages=pages, total=total)


@app.get("/admin/pedidos/<int:oid>")
@admin_required
def admin_order_detail(oid):
    conn=db(); order=conn.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone(); items=conn.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id",(oid,)).fetchall(); conn.close();
    if not order: abort(404)
    return render_template("admin_order_detail.html",order=order,items=items)


@app.post("/admin/pedidos/<int:oid>/estado")
@admin_required
def admin_status(oid):
    status=request.form.get("status","received")
    if status not in VALID_ORDER_STATUSES: flash("Estado no válido.","error"); return redirect(url_for("admin_orders"))
    conn=db()
    conn.execute("BEGIN IMMEDIATE")
    order=conn.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone()
    if not order:
        conn.rollback(); conn.close(); abort(404)
    previous_status = order["status"]
    if status == previous_status:
        conn.rollback(); conn.close()
        flash("El pedido ya tiene ese estado.", "info")
        return redirect(url_for("admin_order_detail", oid=oid))
    transitions = {
        "received": {"preparing", "cancelled"},
        "paid": {"preparing", "cancelled"},
        "preparing": {"on_the_way", "delivering", "delivered", "cancelled"},
        "on_the_way": {"delivering", "delivered"},
        "delivering": {"delivered"},
        "delivered": set(),
        "cancelled": set(),
    }
    if status == "paid":
        conn.rollback(); conn.close()
        flash("Confirma el pago desde el apartado de pago; el estado se actualizará automáticamente.", "warning")
        return redirect(url_for("admin_order_detail", oid=oid))
    if status not in transitions.get(previous_status, set()):
        conn.rollback(); conn.close()
        flash("Ese cambio no está permitido para el estado actual del pedido.", "warning")
        return redirect(url_for("admin_order_detail", oid=oid))
    if status == "preparing" and order["payment_method"] in {"wompi","mercadopago"} and order["payment_status"] != "approved":
        conn.rollback(); conn.close()
        flash("No inicies la preparación hasta que el proveedor confirme el pago.", "warning")
        return redirect(url_for("admin_order_detail", oid=oid))
    if status == "cancelled" and order["payment_method"] in {"wompi","mercadopago"} and order["payment_status"] == "pending":
        conn.rollback(); conn.close()
        flash("El pago online aún puede confirmarse. Espera el resultado final antes de cancelar.", "warning")
        return redirect(url_for("admin_order_detail", oid=oid))
    if status == "cancelled" and order["payment_status"] == "approved":
        conn.rollback(); conn.close()
        flash("El pago ya fue aprobado. Registra primero el reembolso desde esta ficha para cancelar el pedido con trazabilidad.", "warning")
        return redirect(url_for("admin_order_detail", oid=oid))
    if status=="cancelled" and not order["stock_restored"]:
        for item in conn.execute("SELECT product_id,quantity FROM order_items WHERE order_id=?",(oid,)).fetchall():
            if item["product_id"]: conn.execute("UPDATE products SET stock=stock+?,updated_at=? WHERE id=?",(item["quantity"],now_iso(),item["product_id"]))
        if order["coupon_code"]:
            conn.execute("UPDATE coupons SET uses_count=MAX(0,uses_count-1),used=CASE WHEN MAX(0,uses_count-1)<max_uses THEN 0 ELSE 1 END WHERE code=?",(order["coupon_code"],))
        conn.execute("UPDATE orders SET stock_restored=1 WHERE id=?",(oid,))
    conn.execute("UPDATE orders SET status=?,updated_at=? WHERE id=?",(status,now_iso(),oid)); conn.commit(); order=conn.execute("SELECT order_no,email FROM orders WHERE id=?",(oid,)).fetchone(); conn.close(); audit("order_status","order",oid,{"status":status,"previous_status":previous_status});
    if order and order["email"]:
        order_status_email(order["order_no"], status)
    flash("Estado del pedido actualizado y se intentó notificar al cliente por correo.","success"); return redirect(url_for("admin_order_detail",oid=oid))


@app.post("/admin/pedidos/<int:oid>/pago")
@admin_required
def admin_payment(oid):
    payment_status=request.form.get("payment_status","")
    allowed={"pending","approved","declined","error","voided"}
    if payment_status not in allowed:
        flash("Estado de pago inválido.","error")
        return redirect(url_for("admin_order_detail",oid=oid))
    conn=db()
    conn.execute("BEGIN IMMEDIATE")
    order=conn.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone()
    if not order:
        conn.rollback()
        conn.close()
        abort(404)
    if order["payment_status"] != "pending":
        conn.rollback()
        conn.close()
        flash("Este pago ya tiene un estado final. Revisa la conciliación antes de intentar cambiarlo.", "warning")
        return redirect(url_for("admin_order_detail",oid=oid))
    if order["payment_method"] in {"wompi","mercadopago"}:
        conn.rollback()
        conn.close()
        flash("Los pagos online solo se actualizan con la confirmación firmada del proveedor.", "warning")
        return redirect(url_for("admin_order_detail",oid=oid))
    if order["status"] == "cancelled":
        conn.rollback()
        conn.close()
        flash("No se puede registrar un pago para un pedido cancelado.", "warning")
        return redirect(url_for("admin_order_detail",oid=oid))
    if payment_status != "approved" and order["status"] != "received":
        conn.rollback()
        conn.close()
        flash("Solo puedes registrar un rechazo antes de preparar o despachar el pedido. Verifica primero la transacción.", "warning")
        return redirect(url_for("admin_order_detail",oid=oid))
    if payment_status == "approved":
        conn.execute("UPDATE orders SET payment_status=?,status=CASE WHEN status='received' THEN 'paid' ELSE status END,paid_at=COALESCE(paid_at,?),updated_at=? WHERE id=?",(payment_status,now_iso(),now_iso(),oid))
    else:
        conn.execute("UPDATE orders SET payment_status=?,updated_at=? WHERE id=?",(payment_status,now_iso(),oid))
        if payment_status in {"declined","error","voided"} and not order["stock_restored"]:
            for item in conn.execute("SELECT product_id,quantity FROM order_items WHERE order_id=?",(oid,)).fetchall():
                if item["product_id"]:
                    conn.execute("UPDATE products SET stock=stock+?,updated_at=? WHERE id=?",(item["quantity"],now_iso(),item["product_id"]))
            if order["coupon_code"]:
                conn.execute("UPDATE coupons SET uses_count=MAX(0,uses_count-1),used=CASE WHEN MAX(0,uses_count-1)<max_uses THEN 0 ELSE 1 END WHERE code=?",(order["coupon_code"],))
            conn.execute("UPDATE orders SET stock_restored=1,status=CASE WHEN status='received' THEN 'cancelled' ELSE status END WHERE id=?",(oid,))
    conn.commit()
    order=conn.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone()
    conn.close()
    if order and order["email"]:
        if payment_status=="approved":
            send_order_invoice(order["order_no"])
            if order["status"] == "paid":
                order_status_email(order["order_no"], "paid")
            else:
                payment_status_email(order["order_no"], "approved")
        elif payment_status in {"declined", "error", "voided"}:
            payment_status_email(order["order_no"], payment_status)
    audit("payment_status","order",oid,{"payment_status":payment_status})
    flash("Estado del pago actualizado.","success")
    return redirect(url_for("admin_order_detail",oid=oid))


@app.post("/admin/pedidos/<int:oid>/reembolso")
@admin_required
def admin_refund(oid):
    reference = request.form.get("refund_reference", "").strip()[:100]
    if not reference or not request.form.get("refund_confirmed"):
        flash("Confirma que el reembolso externo ya se realizó e indica su referencia.", "error")
        return redirect(url_for("admin_order_detail", oid=oid))
    conn = db()
    conn.execute("BEGIN IMMEDIATE")
    order = conn.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    if not order:
        conn.rollback(); conn.close(); abort(404)
    if order["payment_status"] != "approved":
        conn.rollback(); conn.close()
        flash("Solo se puede registrar el reembolso de un pago aprobado.", "warning")
        return redirect(url_for("admin_order_detail", oid=oid))
    if order["status"] not in {"delivered", "cancelled"}:
        if not order["stock_restored"] and order["status"] not in {"on_the_way", "delivering"}:
            for item in conn.execute("SELECT product_id,quantity FROM order_items WHERE order_id=?", (oid,)).fetchall():
                if item["product_id"]:
                    conn.execute("UPDATE products SET stock=stock+?,updated_at=? WHERE id=?", (item["quantity"], now_iso(), item["product_id"]))
            if order["coupon_code"]:
                conn.execute("UPDATE coupons SET uses_count=MAX(0,uses_count-1),used=CASE WHEN MAX(0,uses_count-1)<max_uses THEN 0 ELSE 1 END WHERE code=?", (order["coupon_code"],))
            conn.execute("UPDATE orders SET stock_restored=1 WHERE id=?", (oid,))
        conn.execute("UPDATE orders SET status='cancelled',updated_at=? WHERE id=?", (now_iso(), oid))
    conn.execute("UPDATE orders SET payment_status='refunded',updated_at=? WHERE id=?", (now_iso(), oid))
    conn.commit()
    order_no = order["order_no"]
    conn.close()
    audit("payment_refunded", "order", oid, {"reference": reference, "external_refund_confirmed": True})
    if order["email"]:
        payment_status_email(order_no, "refunded")
    flash("Reembolso externo registrado con trazabilidad. Si el pedido ya estaba en reparto o entregado, el inventario no se modificó.", "success")
    return redirect(url_for("admin_order_detail", oid=oid))


@app.post("/admin/pedidos/<int:oid>/reenviar-factura")
@admin_required
def admin_resend_invoice(oid):
    conn=db(); order=conn.execute("SELECT order_no FROM orders WHERE id=?",(oid,)).fetchone(); conn.close();
    if not order or not send_order_invoice(order["order_no"], force=True): flash(f"No se pudo enviar el correo. Revisa la configuración de {email_provider_name()} y el correo del cliente. {LAST_EMAIL_ERROR}","error")
    else: flash("Comprobante enviado al correo del cliente.","success")
    return redirect(url_for("admin_order_detail",oid=oid))


@app.get("/admin/clientes")
@admin_required
def admin_clients():
    query=request.args.get("q", "").strip()[:100]
    conn=db(); clients=conn.execute("""SELECT u.id,u.first_name,u.last_name,u.email,u.phone,u.is_active,u.created_at,
        COUNT(o.id) orders,COALESCE(SUM(CASE WHEN o.status!='cancelled' THEN o.total ELSE 0 END),0) spent
        FROM users u LEFT JOIN orders o ON o.user_id=u.id WHERE u.is_admin=0
        AND (?='' OR u.first_name LIKE ? OR u.last_name LIKE ? OR u.email LIKE ? OR u.phone LIKE ?)
        GROUP BY u.id ORDER BY u.id DESC""",(query,f"%{query}%",f"%{query}%",f"%{query}%",f"%{query}%")).fetchall(); conn.close(); return render_template("admin_clients.html",clients=clients,query=query)


@app.get("/admin/clientes/<int:uid>")
@admin_required
def admin_client_detail(uid):
    conn=db(); client=conn.execute("SELECT id,first_name,last_name,email,phone,address,city,neighborhood,is_active,created_at,updated_at FROM users WHERE id=? AND is_admin=0",(uid,)).fetchone(); orders=conn.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC",(uid,)).fetchall(); conn.close();
    if not client: abort(404)
    return render_template("admin_client_detail.html",client=client,orders=orders)


@app.post("/admin/clientes/<int:uid>/editar")
@admin_required
def admin_client_update(uid):
    form=request.form
    first=form.get("first_name", "").strip()[:60]
    last=form.get("last_name", "").strip()[:60]
    email=form.get("email", "").strip().lower()[:254]
    phone=re.sub(r"\D", "", form.get("phone", ""))[:15]
    address=form.get("address", "").strip()[:180]
    city=form.get("city", "").strip()[:80]
    neighborhood=form.get("neighborhood", "").strip()[:80]
    if not first or not last or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+",email):
        flash("Nombre, apellido y correo válido son obligatorios.","error")
        return redirect(url_for("admin_client_detail",uid=uid))
    conn=db()
    try:
        cursor=conn.execute("UPDATE users SET first_name=?,last_name=?,email=?,phone=?,address=?,city=?,neighborhood=?,updated_at=? WHERE id=? AND is_admin=0",(first,last,email,phone,address,city,neighborhood,now_iso(),uid))
        if cursor.rowcount != 1:
            conn.rollback(); conn.close(); abort(404)
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback(); conn.close(); flash("Ese correo ya pertenece a otra cuenta.","error")
        return redirect(url_for("admin_client_detail",uid=uid))
    conn.close(); audit("client_updated","user",uid,{"email":email})
    flash("Datos del cliente actualizados.","success")
    return redirect(url_for("admin_client_detail",uid=uid))


@app.post("/admin/clientes/<int:uid>/estado")
@admin_required
def admin_client_toggle(uid):
    conn=db(); conn.execute("UPDATE users SET is_active=1-is_active,updated_at=? WHERE id=? AND is_admin=0",(now_iso(),uid)); conn.commit(); conn.close(); flash("Estado del cliente actualizado.","success"); return redirect(url_for("admin_client_detail",uid=uid))


@app.post("/admin/clientes/<int:uid>/eliminar")
@admin_required
def admin_client_delete(uid):
    if request.form.get("confirm_delete") != "yes":
        flash("Confirma la eliminación de la cuenta para continuar.","error")
        return redirect(url_for("admin_client_detail",uid=uid))
    conn=db()
    client=conn.execute("SELECT id,email FROM users WHERE id=? AND is_admin=0",(uid,)).fetchone()
    if not client:
        conn.close(); abort(404)
    email=client["email"]
    conn.execute("DELETE FROM users WHERE id=? AND is_admin=0",(uid,))
    conn.commit(); conn.close()
    audit("client_deleted","user",uid,{"email":email})
    flash("Cuenta eliminada. El historial de pedidos se conserva sin vincularlo al cliente.","success")
    return redirect(url_for("admin_clients"))


@app.route("/admin/descuentos",methods=["GET","POST"])
@admin_required
def admin_coupons():
    conn=db()
    if request.method=="POST":
        code=request.form.get("code","").strip().upper() or gen_code("NEXA")
        try:
            percent=int(request.form.get("percent","1")); min_purchase=int(request.form.get("min_purchase","0")); max_uses=int(request.form.get("max_uses","1"))
        except ValueError:
            conn.close(); flash("Datos inválidos.","error"); return redirect(url_for("admin_coupons"))
        if not re.fullmatch(r"[A-Z0-9_-]{3,40}",code) or not 1<=percent<=100 or min_purchase<0 or not 1<=max_uses<=1_000_000:
            conn.close(); flash("Revisa el código, porcentaje, compra mínima y cantidad de usos.","error"); return redirect(url_for("admin_coupons"))
        expires_at=request.form.get("expires_at","").strip()[:16] or None
        try:
            cursor=conn.execute("INSERT INTO coupons(code,percent,expires_at,used,min_purchase,active,max_uses,uses_count,created_at) VALUES(?,?,?,0,?,1,?,0,?)",(code,percent,expires_at,min_purchase,max_uses,now_iso()))
            conn.commit(); audit("coupon_created","coupon",cursor.lastrowid,{"code":code}); flash("Cupón creado.","success")
        except sqlite3.IntegrityError:
            conn.rollback(); flash("Ese código ya existe.","error")
    coupons=conn.execute("SELECT cp.*,u.email FROM coupons cp LEFT JOIN users u ON u.id=cp.user_id ORDER BY cp.id DESC").fetchall(); conn.close(); return render_template("admin_coupons.html",coupons=coupons)


@app.route("/admin/descuentos/<int:cid>/editar",methods=["GET","POST"])
@admin_required
def admin_coupon_edit(cid):
    conn=db(); coupon=conn.execute("SELECT * FROM coupons WHERE id=?",(cid,)).fetchone()
    if not coupon: conn.close(); abort(404)
    if request.method=="POST":
        code=request.form.get("code","").strip().upper()
        try:
            percent=int(request.form.get("percent","0")); min_purchase=int(request.form.get("min_purchase","-1")); max_uses=int(request.form.get("max_uses","0"))
        except ValueError:
            conn.close(); flash("Datos inválidos.","error"); return redirect(url_for("admin_coupon_edit",cid=cid))
        if (not re.fullmatch(r"[A-Z0-9_-]{3,40}",code) or not 1<=percent<=100 or min_purchase<0
                or max_uses<max(1,int(coupon["uses_count"] or 0)) or max_uses>1_000_000):
            conn.close(); flash("Revisa los datos. Los usos máximos no pueden ser menores a los ya utilizados.","error"); return redirect(url_for("admin_coupon_edit",cid=cid))
        expires_at=request.form.get("expires_at","").strip()[:16] or None
        try:
            conn.execute("UPDATE coupons SET code=?,percent=?,expires_at=?,min_purchase=?,max_uses=?,used=CASE WHEN uses_count>=? THEN 1 ELSE 0 END WHERE id=?",
                         (code,percent,expires_at,min_purchase,max_uses,max_uses,cid))
            conn.commit(); conn.close(); audit("coupon_updated","coupon",cid,{"code":code})
            flash("Cupón actualizado.","success"); return redirect(url_for("admin_coupons"))
        except sqlite3.IntegrityError:
            conn.rollback(); conn.close(); flash("Ese código ya está en uso por otro cupón.","error"); return redirect(url_for("admin_coupon_edit",cid=cid))
    conn.close(); return render_template("admin_coupon_edit.html",coupon=coupon)


@app.post("/admin/descuentos/<int:cid>/eliminar")
@admin_required
def admin_coupon_delete(cid):
    conn=db(); coupon=conn.execute("SELECT code FROM coupons WHERE id=?",(cid,)).fetchone()
    if not coupon: conn.close(); abort(404)
    conn.execute("DELETE FROM coupons WHERE id=?",(cid,)); conn.commit(); conn.close()
    audit("coupon_deleted","coupon",cid,{"code":coupon["code"]})
    flash("Cupón eliminado. Los pedidos anteriores conservan el valor y código registrados.","success")
    return redirect(url_for("admin_coupons"))


@app.post("/admin/descuentos/<int:cid>/estado")
@admin_required
def admin_coupon_toggle(cid):
    conn=db(); cursor=conn.execute("UPDATE coupons SET active=1-active WHERE id=?",(cid,)); conn.commit(); conn.close()
    flash("Estado del cupón actualizado." if cursor.rowcount else "No encontré ese cupón.","success" if cursor.rowcount else "error")
    return redirect(url_for("admin_coupons"))


@app.route("/admin/catalogo",methods=["GET","POST"])
@admin_required
def admin_catalog():
    conn=db()
    if request.method=="POST":
        kind=request.form.get("kind"); name=request.form.get("name","").strip()[:80]
        table="categories" if kind=="category" else "toppings" if kind=="topping" else "sizes" if kind=="size" else None
        if not table or not name: conn.close(); flash("Datos de catálogo inválidos.","error"); return redirect(url_for("admin_catalog"))
        try: price=int(request.form.get("price","0")) if kind!="category" else 0
        except ValueError: conn.close(); flash("El recargo debe ser un valor numérico.","error"); return redirect(url_for("admin_catalog"))
        if price<0 or price>10_000_000: conn.close(); flash("El recargo debe estar entre 0 y 10.000.000 COP.","error"); return redirect(url_for("admin_catalog"))
        try:
            if table=="categories":
                slug=re.sub(r"[^a-z0-9]+","-",unicodedata.normalize("NFKD",name).encode("ascii","ignore").decode().lower()).strip("-")
                conn.execute("INSERT INTO categories(name,slug,active,created_at) VALUES(?,?,1,?)",(name,slug,now_iso()))
            elif table=="sizes":
                volume_ml=int(request.form.get("volume_ml", "0"))
                if volume_ml < 1 or volume_ml > 10000:
                    conn.close(); flash("Registra el volumen real del tamaño entre 1 y 10.000 ml.","error"); return redirect(url_for("admin_catalog"))
                conn.execute("INSERT INTO sizes(name,price,volume_ml,active,created_at) VALUES(?,?,?,1,?)",(name,price,volume_ml,now_iso()))
            else: conn.execute(f"INSERT INTO {table}(name,price,active,created_at) VALUES(?,?,1,?)",(name,price,now_iso()))
            conn.commit(); flash("Elemento agregado.","success")
        except sqlite3.IntegrityError: conn.rollback(); flash("Ya existe un elemento con ese nombre.","error")
    categories=conn.execute("SELECT * FROM categories ORDER BY name").fetchall(); toppings=conn.execute("SELECT * FROM toppings ORDER BY name").fetchall(); sizes=conn.execute("SELECT * FROM sizes ORDER BY price").fetchall(); conn.close(); return render_template("admin_catalog.html",categories=categories,toppings=toppings,sizes=sizes)


@app.post("/admin/catalogo/size/<int:item_id>/volumen")
@admin_required
def admin_size_volume(item_id):
    try:
        volume_ml = int(request.form.get("volume_ml", "0"))
    except (TypeError, ValueError):
        volume_ml = 0
    if volume_ml < 1 or volume_ml > 10000:
        flash("Registra el volumen real entre 1 y 10.000 ml.", "error")
        return redirect(url_for("admin_catalog"))
    conn = db()
    cursor = conn.execute("UPDATE sizes SET volume_ml=? WHERE id=?", (volume_ml, item_id))
    conn.commit(); conn.close()
    flash("Volumen del tamaño actualizado. El PUM se calcula con este dato.", "success" if cursor.rowcount else "error")
    return redirect(url_for("admin_catalog"))


@app.route("/admin/catalogo/<string:kind>/<int:item_id>/editar",methods=["GET","POST"])
@admin_required
def admin_catalog_edit(kind,item_id):
    tables={"category":"categories","topping":"toppings","size":"sizes"}
    table=tables.get(kind)
    if not table: abort(404)
    conn=db(); item=conn.execute(f"SELECT * FROM {table} WHERE id=?",(item_id,)).fetchone()
    if not item: conn.close(); abort(404)
    if request.method=="POST":
        name=request.form.get("name","").strip()[:80]
        try: price=int(request.form.get("price","0")) if kind!="category" else 0
        except ValueError: price=-1
        try: volume=int(request.form.get("volume_ml","0")) if kind=="size" else None
        except ValueError: volume=0
        if not name or price<0 or price>10_000_000 or (kind=="size" and (volume is None or volume<1 or volume>10000)):
            conn.close(); flash("Revisa el nombre, recargo y volumen real del elemento.","error"); return redirect(url_for("admin_catalog_edit",kind=kind,item_id=item_id))
        try:
            if kind=="category":
                slug=re.sub(r"[^a-z0-9]+","-",unicodedata.normalize("NFKD",name).encode("ascii","ignore").decode().lower()).strip("-")
                conn.execute("UPDATE categories SET name=?,slug=? WHERE id=?",(name,slug,item_id))
            elif kind=="size": conn.execute("UPDATE sizes SET name=?,price=?,volume_ml=? WHERE id=?",(name,price,volume,item_id))
            else: conn.execute("UPDATE toppings SET name=?,price=? WHERE id=?",(name,price,item_id))
            conn.commit(); conn.close(); audit("catalog_item_updated",kind,item_id,{"name":name})
            flash("Elemento de catálogo actualizado.","success"); return redirect(url_for("admin_catalog"))
        except sqlite3.IntegrityError:
            conn.rollback(); conn.close(); flash("Ya existe una categoría o elemento con ese nombre/slug.","error"); return redirect(url_for("admin_catalog_edit",kind=kind,item_id=item_id))
    conn.close(); return render_template("admin_catalog_edit.html",kind=kind,item=item)


@app.post("/admin/catalogo/<string:kind>/<int:item_id>/eliminar")
@admin_required
def admin_catalog_delete(kind,item_id):
    tables={"category":"categories","topping":"toppings","size":"sizes"}
    table=tables.get(kind)
    if not table: abort(404)
    conn=db(); item=conn.execute(f"SELECT name FROM {table} WHERE id=?",(item_id,)).fetchone()
    if not item: conn.close(); abort(404)
    conn.execute(f"DELETE FROM {table} WHERE id=?",(item_id,)); conn.commit(); conn.close()
    audit("catalog_item_deleted",kind,item_id,{"name":item["name"]})
    flash("Elemento eliminado. Los productos quedan sin categoría o los pedidos conservan sus opciones históricas.","success")
    return redirect(url_for("admin_catalog"))


@app.post("/admin/catalogo/<string:kind>/<int:item_id>/estado")
@admin_required
def admin_catalog_toggle(kind,item_id):
    table="categories" if kind=="category" else "toppings" if kind=="topping" else "sizes" if kind=="size" else None
    if not table: abort(404)
    conn=db(); cursor=conn.execute(f"UPDATE {table} SET active=1-active WHERE id=?",(item_id,)); conn.commit(); conn.close()
    flash("Estado actualizado." if cursor.rowcount else "No encontré ese elemento.","success" if cursor.rowcount else "error")
    return redirect(url_for("admin_catalog"))


@app.get("/admin/mensajes")
@admin_required
def admin_messages():
    conn=db(); messages=conn.execute("SELECT * FROM contact_messages ORDER BY id DESC").fetchall(); conn.close(); return render_template("admin_messages.html",messages=messages)


@app.post("/admin/mensajes/<int:mid>/estado")
@admin_required
def admin_message_status(mid):
    status=request.form.get("status","read")
    if status not in {"new","read","closed"}: status="read"
    conn=db(); conn.execute("UPDATE contact_messages SET status=?,replied_at=CASE WHEN ?='closed' THEN ? ELSE replied_at END WHERE id=?",(status,status,now_iso(),mid)); conn.commit(); conn.close(); flash("Estado del mensaje actualizado.","success"); return redirect(url_for("admin_messages"))


@app.get("/admin/resenas")
@admin_required
def admin_reviews():
    conn=db(); reviews=conn.execute("SELECT r.*,p.name product,u.email FROM reviews r JOIN products p ON p.id=r.product_id JOIN users u ON u.id=r.user_id ORDER BY r.id DESC").fetchall(); conn.close(); return render_template("admin_reviews.html",reviews=reviews)


@app.post("/admin/resenas/<int:rid>/estado")
@admin_required
def admin_review_toggle(rid):
    conn=db(); conn.execute("UPDATE reviews SET active=1-active WHERE id=?",(rid,)); conn.commit(); conn.close(); flash("Visibilidad de la reseña actualizada.","success"); return redirect(url_for("admin_reviews"))


@app.get("/admin/auditoria")
@admin_required
def admin_audit():
    conn=db(); logs=conn.execute("SELECT a.*,u.email FROM audit_log a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 250").fetchall(); conn.close(); return render_template("admin_audit.html",logs=logs)


@app.get("/admin/export/orders.csv")
@admin_required
def export_orders():
    conn=db(); rows=conn.execute("SELECT order_no,created_at,customer_name,email,phone,city,delivery_method,subtotal,discount,delivery_fee,total,payment_method,payment_status,status FROM orders ORDER BY id DESC").fetchall(); conn.close(); out=io.StringIO(); writer=csv.writer(out); writer.writerow(["Pedido","Fecha","Cliente","Correo","Teléfono","Ciudad","Entrega","Subtotal","Descuento","Domicilio","Total","Pago","Estado pago","Estado pedido"]); writer.writerows([list(r) for r in rows]); return app.response_class("\ufeff"+out.getvalue(),mimetype="text/csv",headers={"Content-Disposition":"attachment; filename=nexafarma-orders.csv"})


@app.route("/admin/configuracion",methods=["GET","POST"])
@admin_required
def admin_config():
    if request.method=="POST":
        updates={"business_notice":request.form.get("business_notice","").strip()[:500],"store_status":request.form.get("store_status","open") if request.form.get("store_status") in {"open","closed"} else "open"}
        conn=db(); conn.executemany("INSERT INTO site_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",updates.items()); conn.commit(); conn.close(); flash("Configuración guardada.","success")
    return render_template("admin_config.html",chatbot_model=CHATBOT_MODEL,smtp_enabled=smtp_configured(),resend_enabled=resend_configured(),email_provider=email_provider_name(),email_diagnostics=email_diagnostics(),wompi_enabled=wompi_is_available(),wompi_temporarily_unavailable=WOMPI_TEMPORARILY_UNAVAILABLE,mercadopago_enabled=mercadopago_is_configured(),wompi_environment=WOMPI_ENVIRONMENT,mail_from=GMAIL_API_FROM or RESEND_FROM or SMTP_FROM)


@app.post("/admin/configuracion/correo-prueba")
@admin_required
def admin_email_test():
    target = request.form.get("test_email", "").strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", target):
        flash("Escribe un correo válido para la prueba.", "error")
        return redirect(url_for("admin_config"))
    body = f"""<div style='font-family:Arial,sans-serif;background:#f2faf8;padding:28px'><div style='max-width:620px;margin:auto;background:#fff;border:1px solid #d6e9e5;border-radius:22px;padding:28px'><div style='font-size:28px;font-weight:900;color:#087f83'>NexaFarma</div><h2>Correo de prueba correcto</h2><p>El sistema de correo de tu tienda puede enviar mensajes transaccionales.</p><p><b>Proveedor detectado:</b> {html.escape(email_provider_name())}</p><p style='color:#526b6b'>Este mensaje fue generado desde el panel administrativo.</p></div></div>"""
    if send_email(target, "NexaFarma · Prueba de correo ✅", body):
        audit("email_test_sent", "system", None, {"recipient": target, "provider": email_provider_name()})
        flash(f"Prueba enviada a {target}.", "success")
    else:
        flash(f"No se pudo enviar la prueba. {LAST_EMAIL_ERROR}", "error")
    return redirect(url_for("admin_config"))


@app.get("/api/config")
def api_config():
    return jsonify({"name":CONFIG["name"],"currency":"COP","whatsapp":CONFIG["whatsapp"],"wompi_enabled":wompi_is_available()})


# ---------------- CHATBOT CON GROQ ----------------
def chatbot_catalog():
    conn=db(); products=conn.execute("SELECT p.id,p.name,p.description,p.price,c.name category FROM products p LEFT JOIN categories c ON c.id=p.category_id WHERE p.active=1 ORDER BY c.name,p.name").fetchall(); sizes=conn.execute("SELECT name,price FROM sizes WHERE active=1 ORDER BY price").fetchall(); toppings=conn.execute("SELECT name,price FROM toppings WHERE active=1 ORDER BY name").fetchall(); conn.close(); return products,sizes,toppings


def chatbot_catalog_text():
    products,sizes,toppings=chatbot_catalog(); catalog="\n".join([f"- {p['name']}: {money(p['price'])} · {p['description'] or ''}" for p in products]) or "(sin productos)"; size_text=", ".join([f"{s['name']} (+{money(s['price'])})" if s['price'] else s['name'] for s in sizes]) or "presentaciones indicadas en la ficha"; topping_text=", ".join([f"{t['name']} (+{money(t['price'])})" for t in toppings]) or "sin personalizaciones adicionales"; return catalog,size_text,topping_text


CHATBOT_PAGE_KNOWLEDGE = [
    {
        "title": "Quiénes somos",
        "keywords": "nosotros quienes somos NexaFarma historia misión visión farmacia bienestar catálogo cuidado personal",
        "text": "NexaFarma es una marca ficticia creada para demostrar una tienda virtual de farmacia y bienestar. Permite explorar un catálogo de productos generales de cuidado personal, higiene, botiquín y dispositivos, revisar disponibilidad, gestionar carrito y pedidos, y enviar consultas. No es una farmacia real ni sustituye la orientación de médicos o farmacéuticos."
    },
]


def chatbot_knowledge_text(query=None):
    """Lee el manual local y devuelve solo los fragmentos relacionados con la pregunta."""
    knowledge_path = os.path.join(BASE_DIR, "nexafarma_knowledge.txt")
    try:
        with open(knowledge_path, "r", encoding="utf-8") as knowledge_file:
            full_text = knowledge_file.read().strip()
    except FileNotFoundError:
        app.logger.warning("No se encontró nexafarma_knowledge.txt; el chatbot usará solo datos dinámicos de la tienda.")
        full_text = ""
    except OSError:
        app.logger.exception("No se pudo leer nexafarma_knowledge.txt")
        full_text = ""

    if not query:
        return full_text[:2400]

    # División por secciones numeradas: la búsqueda es local y no usa tokens/API.
    chunks = [part.strip() for part in re.split(r"(?=^\d+(?:\.\d+)*\s+[^\n]+$)", full_text, flags=re.MULTILINE) if part.strip()]
    query_words = set(re.findall(r"[a-záéíóúñ0-9]+", query.lower()))
    stop_words = {"que", "qué", "como", "cómo", "dice", "del", "de", "la", "el", "en", "un", "una", "por", "para", "es", "hay", "me", "puedes", "pagina", "página", "apartado"}
    query_words -= stop_words
    ranked = []
    for index, chunk in enumerate(chunks):
        haystack = chunk.lower()
        score = sum(1 for word in query_words if word in haystack)
        if score:
            ranked.append((score, index, chunk))
    for page in CHATBOT_PAGE_KNOWLEDGE:
        haystack = (page["keywords"] + " " + page["text"]).lower()
        score = sum(1 for word in query_words if word in haystack)
        if score:
            ranked.append((score + 2, len(chunks), f"{page['title']}: {page['text']}"))
    if not ranked:
        return full_text[:900]
    ranked.sort(key=lambda item: (-item[0], item[1]))
    selected = []
    for _, _, chunk in ranked:
        if chunk not in selected:
            selected.append(chunk[:1500])
        if len(selected) == 2:
            break
    return "\n\n".join(selected)


def chatbot_system_prompt(message=""):
    catalog, size_text, topping_text = chatbot_catalog_text()
    knowledge = chatbot_knowledge_text(message)
    delivery_info = f"El domicilio puede ser gratis desde {money(CONFIG['free_delivery_from'])}, según el checkout." if CONFIG["free_delivery_from"] else "No hay un mínimo de envío gratis configurado. No prometas envío gratuito."
    return f"""Eres el asistente virtual de {CONFIG['name']}, una farmacia ficticia construida para fines académicos.

IDENTIDAD Y FUNCIONAMIENTO:
{knowledge if knowledge else 'No hay información adicional disponible. Reconoce los límites y no inventes datos.'}

DATOS DINÁMICOS DE LA TIENDA:
- Moneda: pesos colombianos (COP).
- Domicilio configurado: {money(CONFIG['delivery_fee'])}.
- Envío: {delivery_info}
- Descuento de bienvenida: {CONFIG['discount']}% si está habilitado en el checkout.
- WhatsApp de contacto: https://wa.me/{CONFIG['whatsapp']}.
- Catálogo actual: \n{catalog}

REGLAS OBLIGATORIAS DE SEGURIDAD SANITARIA:
- No diagnostiques síntomas ni indiques qué medicamento tomar, dosis, duración de tratamiento o combinación de fármacos.
- No recomiendes productos como sustituto de evaluación médica. Para dudas sobre síntomas, interacciones, embarazo, alergias, enfermedades o medicación, remite a un médico o farmacéutico habilitado.
- Nunca sugieras medicamentos sujetos a fórmula ni ayudes a eludir requisitos de prescripción. Si un producto está marcado como sujeto a fórmula, no está habilitado para compra online en esta demo.
- No presentes la tienda como una farmacia real autorizada. Es una demostración de software y su catálogo es ilustrativo.
- Ante una urgencia médica, recomienda contactar los servicios locales de emergencia.

ESTILO Y OPERACIÓN:
- Responde en español colombiano, con claridad, empatía y brevedad.
- Usa solo productos, precios, existencias, domicilios y métodos de pago consultados en los datos de la aplicación.
- Puedes explicar la navegación, el catálogo general, el carrito, las cuentas y el seguimiento del pedido.
- No afirmes haber consultado o cambiado pedidos, enviado correos, aplicado pagos ni realizado acciones sin confirmación técnica.
- Nunca solicites contraseñas, códigos, tokens ni datos completos de tarjetas.
- Si falta información, dilo y ofrece el canal de contacto configurado.
"""


def local_chatbot_reply(message, history=None):
    text = re.sub(r"\s+", " ", str(message or "").lower()).strip()
    norm = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    products, sizes, toppings = chatbot_catalog()
    medical_terms = ["que medicamento", "que medicina", "que me tomo", "que puedo tomar", "dosis", "cada cuantas horas", "sintomas", "síntomas", "dolor", "fiebre", "infeccion", "infección", "embarazo", "alergia", "interaccion", "interacción", "antibiotico", "antibiótico", "recetar", "tratamiento"]
    if any(unicodedata.normalize("NFKD", term).encode("ascii", "ignore").decode("ascii") in norm for term in medical_terms):
        return "No puedo indicar medicamentos, dosis ni tratamientos. Para elegir algo seguro según tus síntomas, alergias y antecedentes, consulta con un médico o farmacéutico habilitado. NexaFarma es una demostración académica, no una farmacia real."
    if any(term in norm for term in ["nosotros", "quienes somos", "historia", "mision", "vision", "que es nexafarma"]):
        return CHATBOT_PAGE_KNOWLEDGE[0]["text"]
    if any(word in norm for word in ["catalogo", "productos", "que venden", "inventario", "disponible"]):
        if not products:
            return "Todavía no hay productos publicados en el catálogo."
        listing = ", ".join(f"{p['name']} ({money(p['price'])})" for p in products[:8])
        return "En el catálogo de demostración hay productos generales como: " + listing + ". Revisa cada ficha para consultar descripción y disponibilidad."
    if any(word in norm for word in ["precio", "cuesta", "valor", "vale"]):
        for product in products:
            if _agent_norm(product["name"]) in _agent_norm(norm):
                return f"{product['name']} aparece con precio de {money(product['price'])}. Verifica el valor final en el carrito antes de confirmar."
        return "Los precios vigentes aparecen en las fichas del catálogo y se vuelven a validar en el checkout."
    if any(word in norm for word in ["domicilio", "envio", "entrega", "recoger"]):
        reply = f"El domicilio configurado es {money(CONFIG['delivery_fee'])}."
        if CONFIG["free_delivery_from"]:
            reply += f" Puede variar según el subtotal de {money(CONFIG['free_delivery_from'])} y las condiciones del checkout."
        return reply + " Verifica cobertura y valor final antes de confirmar."
    if any(word in norm for word in ["descuento", "cupon", "oferta", "promo", "bienvenida"]):
        return f"El descuento de bienvenida configurado es {CONFIG['discount']}%. Si se encuentra habilitado, puedes consultar el cupón desde tu cuenta y validarlo en el checkout."
    if any(word in norm for word in ["pago", "pagar", "mercado pago", "mercadopago", "wompi", "transferencia"]):
        if any(word in norm for word in ["error", "fallo", "pendiente", "rechaz"]):
            return "Entra a Mi cuenta → Mis pedidos y revisa el estado antes de intentar de nuevo. Si sigue pendiente, contacta al administrador con el número de pedido."
        enabled = "Mercado Pago está configurado." if mercadopago_is_configured() else "Mercado Pago está pendiente de configurar."
        return "El checkout muestra los métodos realmente habilitados. " + enabled + " Wompi solo se habilitará cuando el administrador configure y pruebe las credenciales y notificaciones."
    if any(word in norm for word in ["pedido", "orden", "seguimiento", "rastrear"]):
        return "Inicia sesión con la cuenta usada para comprar y abre Mi cuenta → Mis pedidos para consultar el estado y el comprobante."
    if any(word in norm for word in ["cuenta", "registro", "registrarme", "contrasena", "contraseña"]):
        return "Puedes crear una cuenta o iniciar sesión desde el menú superior. No compartas tu contraseña con el personal de soporte ni con el chatbot."
    if any(word in norm for word in ["receta", "formula medica", "fórmula médica", "prescripcion", "prescripción"]):
        return "Los productos marcados como sujetos a fórmula no se pueden comprar en línea en esta demostración. Consulta una farmacia habilitada y sigue la indicación del profesional que te atiende."
    if any(word in norm for word in ["contacto", "whatsapp", "hablar", "ayuda"]):
        return (f"Puedes escribirnos por WhatsApp: https://wa.me/{CONFIG['whatsapp']} o utilizar el formulario de Contacto.") if CONFIG["whatsapp"] else "El WhatsApp aún no está configurado. Mientras tanto, utiliza el formulario de Contacto de la tienda."
    return "Puedo ayudarte a navegar el catálogo general, revisar precios, inventario, domicilios, pagos, cuentas y pedidos. No puedo indicar medicamentos ni tratamientos; para eso consulta con un profesional de salud."


def _agent_search_products(args):
    query = _agent_norm(args.get("query", ""))[:100]
    category = _agent_norm(args.get("category", ""))[:60]
    try:
        max_price = max(0, min(1000000, int(args.get("max_price", 0) or 0)))
    except (TypeError, ValueError):
        max_price = 0
    try:
        limit = max(1, min(8, int(args.get("limit", 5) or 5)))
    except (TypeError, ValueError):
        limit = 5
    conn = db()
    rows = conn.execute("""SELECT p.id,p.name,p.description,p.price,p.stock,c.name AS category,
                                  COALESCE((SELECT s.price FROM sizes s WHERE s.active=1 ORDER BY s.price,s.id LIMIT 1),0) AS default_size_price,
                                  (SELECT s.volume_ml FROM sizes s WHERE s.active=1 ORDER BY s.price,s.id LIMIT 1) AS default_size_volume
                           FROM products p LEFT JOIN categories c ON c.id=p.category_id
                           WHERE p.active=1 AND p.stock>0 AND COALESCE(p.requires_prescription,0)=0 ORDER BY p.featured DESC,c.name,p.name""").fetchall()
    conn.close()
    matched = []
    terms = [x for x in query.split() if len(x) > 1]
    for row in rows:
        norm_name = _agent_norm(row["name"])
        norm_desc = _agent_norm(row["description"])
        norm_cat = _agent_norm(row["category"])
        if category and category not in norm_cat:
            continue
        starting_price = int(row["price"] or 0) + int(row["default_size_price"] or 0)
        if max_price and starting_price > max_price:
            continue
        if terms and not all(term in (norm_name + " " + norm_desc + " " + norm_cat) for term in terms):
            continue
        matched.append({"id": int(row["id"]), "name": row["name"], "description": row["description"] or "", "price": starting_price, "price_label": "Desde " + money(starting_price), "volume_ml": int(row["default_size_volume"] or 0), "pum": price_per_liter(starting_price, row["default_size_volume"]), "stock": int(row["stock"] or 0), "category": row["category"] or "Sin categoría"})
    return {"ok": True, "count": len(matched[:limit]), "products": matched[:limit]}


def _agent_add_product_to_cart(args):
    name = str(args.get("product_name") or "").strip()[:100]
    if not name:
        return {"ok": False, "message": "Indica el nombre del producto que quieres agregar."}
    try:
        quantity = int(args.get("quantity", 1))
    except (TypeError, ValueError):
        return {"ok": False, "message": "Indica una cantidad válida entre 1 y 20; no modifiqué el carrito."}
    if quantity < 1 or quantity > 20:
        return {"ok": False, "message": "La cantidad debe estar entre 1 y 20; no modifiqué el carrito."}
    wanted_name = _agent_norm(name)
    conn = db()
    rows = conn.execute("SELECT id,name,description,price,stock FROM products WHERE active=1 AND stock>0 AND COALESCE(requires_prescription,0)=0 ORDER BY featured DESC,name").fetchall()
    # No elegir por coincidencia parcial: un pedido ambigua podría añadir otro artículo.
    matches = [row for row in rows if _agent_norm(row["name"]) == wanted_name]
    if not matches:
        conn.close()
        return {"ok": False, "message": f"No encontré un producto activo que coincida con '{name}'. Usa search_products para ver opciones."}
    if len(matches) > 1:
        choices = [{"name": row["name"], "price": money(row["price"])} for row in matches[:5]]
        conn.close()
        return {"ok": False, "needs_choice": True, "message": "Encontré varias opciones; no agregué nada todavía.", "choices": choices}
    product = matches[0]
    stock = int(product["stock"] or 0)
    size_name = _agent_norm(args.get("size", ""))[:40]
    size_rows = conn.execute("SELECT id,name,price FROM sizes WHERE active=1 ORDER BY price,id").fetchall()
    size = None
    if size_name:
        size_matches = [row for row in size_rows if size_name == _agent_norm(row["name"]) or size_name in _agent_norm(row["name"])]
        if not size_matches:
            conn.close()
            return {"ok": False, "message": "Ese tamaño no está disponible. Puedo consultar los tamaños actuales si me indicas que busque el catálogo."}
        if len(size_matches) > 1:
            conn.close()
            return {"ok": False, "message": "El nombre del tamaño es ambiguo; indica pequeño, mediano o grande según las opciones del menú."}
        size = size_matches[0]
    elif size_rows:
        size = size_rows[0]
    raw_toppings = args.get("toppings", [])
    if isinstance(raw_toppings, str):
        raw_toppings = [item.strip() for item in raw_toppings.split(",") if item.strip()]
    if not isinstance(raw_toppings, list):
        raw_toppings = []
    if len(raw_toppings) > 5:
        conn.close()
        return {"ok": False, "message": "Los artículos se venden según su ficha; no se admiten personalizaciones. No se modificó el carrito."}
    active_toppings = conn.execute("SELECT id,name,price FROM toppings WHERE active=1 ORDER BY name").fetchall()
    topping_ids = []
    selected_toppings = []
    for requested in raw_toppings:
        term = _agent_norm(requested)
        found = [row for row in active_toppings if term and (term == _agent_norm(row["name"]) or term in _agent_norm(row["name"]))]
        if len(found) != 1:
            conn.close()
            return {"ok": False, "message": "La tienda no tiene opciones de personalización configuradas. No se modificó el carrito."}
        if found[0]["id"] not in topping_ids:
            topping_ids.append(int(found[0]["id"]))
            selected_toppings.append(found[0]["name"])
    conn.close()

    items = cart_items_from_session()
    current_qty = sum(int(item.get("qty", 0)) for item in items if int(item.get("product_id", 0) or 0) == int(product["id"]))
    if current_qty + quantity > stock:
        return {"ok": False, "message": f"Solo quedan {stock} unidades disponibles de {product['name']} y ya tienes {current_qty} en el carrito. No agregué el producto."}
    size_id = int(size["id"]) if size else None
    flavor = str(args.get("flavor", "") or "").strip()[:120]
    signature = (int(product["id"]), size_id, flavor, tuple(topping_ids))
    merged = False
    for existing in items:
        existing_signature = (int(existing.get("product_id", 0) or 0), existing.get("size_id"), str(existing.get("flavor", "") or ""), tuple(existing.get("topping_ids", [])))
        if existing_signature == signature:
            existing["qty"] = int(existing.get("qty", 0)) + quantity
            merged = True
            break
    if not merged:
        items.append({"product_id": int(product["id"]), "qty": quantity, "size_id": size_id, "flavor": flavor, "topping_ids": topping_ids})
    session["cart"] = items
    session.modified = True
    normalized = cart_items_from_session()
    session["cart"] = normalized
    session.modified = True
    added_item = next((item for item in normalized if int(item.get("product_id", 0)) == int(product["id"]) and item.get("size_id") == size_id and str(item.get("flavor", "") or "") == flavor and tuple(item.get("topping_ids", [])) == tuple(topping_ids)), None)
    return {"ok": True, "action": "add_to_cart", "message": f"Agregué {quantity} × {product['name']} al carrito.", "product": product["name"], "quantity_added": quantity, "size": size["name"] if size else "Sin tamaño", "toppings": selected_toppings, "cart_subtotal": money(cart_subtotal(normalized)), "cart_url": url_for("cart_view"), "item_unit_price": money(added_item["unit"] if added_item else product["price"])}


def _agent_cart_summary(_args):
    items = cart_items_from_session()
    subtotal = cart_subtotal(items)
    if not items:
        # Un carrito vacío no tiene costo de entrega ni total de compra.
        return {"ok": True, "items": [], "subtotal": money(0), "delivery_home": money(0), "total_home": money(0), "total_pickup": money(0), "cart_url": url_for("cart_view"), "message": "Tu carrito está vacío."}
    result_items = [{"name": item["name"], "quantity": int(item["qty"]), "size": item.get("size", ""), "toppings": [top["name"] for top in item.get("toppings", [])], "unit_price": money(item["unit"]), "line_total": money(int(item["unit"]) * int(item["qty"]))} for item in items[:20]]
    delivery_home = delivery_for(subtotal, "domicilio")
    return {"ok": True, "items": result_items, "subtotal": money(subtotal), "delivery_home": money(delivery_home), "total_home": money(subtotal + delivery_home), "total_pickup": money(subtotal), "cart_url": url_for("cart_view"), "checkout_url": url_for("checkout"), "message": "Resumen actualizado del carrito. No se ha creado ni pagado ningún pedido."}


def _agent_manage_cart(args):
    """Modifica solo el carrito actual y requiere una operación inequívoca y validada."""
    operation = _agent_norm(args.get("operation", ""))
    if operation == "clear":
        session["cart"] = []
        session.modified = True
        return {"ok": True, "action": "clear_cart", "message": "Vacié tu carrito. No se creó ningún pedido."}

    items = cart_items_from_session()
    if not items:
        return {"ok": False, "message": "Tu carrito está vacío; no hice cambios."}
    wanted = _agent_norm(args.get("product_name", ""))[:100]
    if not wanted:
        return {"ok": False, "message": "Indica el nombre del producto que quieres modificar."}
    matches = [i for i, item in enumerate(items) if wanted == _agent_norm(item["name"])]
    if not matches:
        matches = [i for i, item in enumerate(items) if wanted in _agent_norm(item["name"])]
    if not matches:
        return {"ok": False, "message": f"No encontré {args.get('product_name')} en tu carrito; no hice cambios."}
    size_filter = _agent_norm(args.get("size", ""))[:40]
    if size_filter:
        matches = [i for i in matches if size_filter in _agent_norm(items[i].get("size", ""))]
    if len(matches) > 1:
        options = [f"{items[i]['name']} ({items[i].get('size') or 'sin tamaño'}, cantidad {items[i]['qty']})" for i in matches]
        return {"ok": False, "needs_choice": True, "message": "Hay varias líneas de ese producto. Indica cuál quieres modificar: " + "; ".join(options)}
    if not matches:
        return {"ok": False, "message": "No encontré esa presentación del producto en tu carrito; no hice cambios."}
    index = matches[0]
    item = items[index]
    if operation == "remove":
        items.pop(index)
        action = "remove_cart_item"
        message = f"Quité {item['name']} del carrito."
    elif operation == "set_quantity":
        try:
            quantity = int(args.get("quantity"))
        except (TypeError, ValueError):
            return {"ok": False, "message": "Indica una cantidad válida entre 1 y 20."}
        if quantity < 1 or quantity > 20:
            return {"ok": False, "message": "La cantidad debe estar entre 1 y 20; no hice cambios."}
        if quantity > int(item["stock"]):
            return {"ok": False, "message": f"Solo hay {item['stock']} unidades disponibles de {item['name']}; no hice cambios."}
        item["qty"] = quantity
        action = "update_cart_quantity"
        message = f"Actualicé {item['name']} a {quantity} unidad(es)."
    else:
        return {"ok": False, "message": "Esa operación de carrito no está permitida."}
    session["cart"] = [{"product_id": i["product_id"], "qty": i["qty"], "size_id": i.get("size_id"), "flavor": i.get("flavor", ""), "topping_ids": i.get("topping_ids", [])} for i in items]
    session.modified = True
    normalized = cart_items_from_session()
    subtotal = cart_subtotal(normalized)
    return {"ok": True, "action": action, "message": message, "subtotal": money(subtotal), "cart_url": url_for("cart_view")}


def _agent_product_options(_args):
    """La tienda de demostración no usa personalizaciones de tamaño o ingredientes."""
    return {"ok": True, "sizes": [], "toppings": [],
            "message": "Los productos se venden tal como aparecen descritos en su ficha; no hay opciones de personalización configuradas."}


def _agent_my_orders(_args):
    user = current_user()
    if not user:
        return {"ok": False, "login_required": True, "login_url": url_for("login"), "message": "Para consultar pedidos propios, inicia sesión en NexaFarma. Después abre Mi cuenta → Mis pedidos."}
    conn = db()
    rows = conn.execute("SELECT order_no,status,payment_status,total,created_at FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 5", (user["id"],)).fetchall()
    conn.close()
    return {"ok": True, "count": len(rows), "orders": [{"order_no": row["order_no"], "status": STATUS_ES.get(row["status"], row["status"]), "payment_status": row["payment_status"], "total": money(row["total"]), "created_at": str(row["created_at"] or "")[:19].replace("T", " "), "url": url_for("order_detail", order_no=row["order_no"])} for row in rows], "orders_url": url_for("order_history"), "message": "Solo se consultaron los pedidos de la cuenta que inició sesión."}


def _agent_store_info(_args):
    settings = safe_site_settings()
    free_delivery = CONFIG["free_delivery_from"]
    return {"ok": True, "name": CONFIG["name"], "address": CONFIG["address"], "phone": CONFIG["business_phone"] or "No hay teléfono adicional configurado", "whatsapp": ("https://wa.me/" + CONFIG["whatsapp"]) if CONFIG["whatsapp"] else "", "delivery_fee": money(CONFIG["delivery_fee"]), "free_delivery_from": money(free_delivery) if free_delivery else None, "store_status": "Cerrada" if settings.get("store_status") == "closed" else "Abierta", "business_notice": settings.get("business_notice", ""), "message": "La ubicación solo es la registrada en configuración; no inventes dirección, horarios ni puntos de referencia."}


def _agent_navigation_help(args):
    topic = _agent_norm(args.get("topic", ""))
    if any(k in topic for k in ["pedido", "orden", "seguimiento"]):
        return {"ok": True, "where": "Mi cuenta → Mis pedidos", "url": url_for("order_history"), "requires_login": True, "message": "Inicia sesión y abre Mi cuenta → Mis pedidos para ver tus compras."}
    if any(k in topic for k in ["cuenta", "perfil", "direccion", "datos"]):
        return {"ok": True, "where": "Mi cuenta", "url": url_for("account"), "requires_login": True}
    if any(k in topic for k in ["carrito", "compra", "checkout", "pagar"]):
        return {"ok": True, "where": "Carrito", "url": url_for("cart_view"), "message": "El agente puede ayudarte a preparar el carrito, pero tú revisas y confirmas el pedido y el pago."}
    if any(k in topic for k in ["contacto", "hablar", "ayuda", "whatsapp"]):
        return {"ok": True, "where": "Contacto", "url": url_for("contact"), "whatsapp": ("https://wa.me/" + CONFIG["whatsapp"]) if CONFIG["whatsapp"] else ""}
    return {"ok": True, "where": "Catálogo", "url": url_for("menu"), "message": "Explora el catálogo y abre la ficha de cada producto para revisar detalles."}


AGENT_TOOLS = [
    {"type": "function", "function": {"name": "search_products", "description": "Busca productos activos con stock por nombre, descripción, categoría o precio máximo.", "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "category": {"type": "string"}, "max_price": {"type": "integer", "description": "Precio máximo COP; 0 significa sin límite."}, "limit": {"type": "integer", "minimum": 1, "maximum": 8}}, "required": [], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "add_product_to_cart", "description": "Añade al carrito un producto cuyo nombre del catálogo coincida exactamente, solo si el cliente lo pide claramente. Valida stock y opciones. Nunca crea un pedido ni efectúa el pago.", "parameters": {"type": "object", "properties": {"product_name": {"type": "string"}, "quantity": {"type": "integer", "minimum": 1, "maximum": 20}}, "required": ["product_name"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "get_cart_summary", "description": "Consulta el carrito actual y calcula subtotal y total aproximado con domicilio o recogida.", "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "manage_cart", "description": "Cambia una línea de carrito (remove o set_quantity) o vacía el carrito (clear). Úsala solo si el mensaje actual pide claramente ese cambio.", "parameters": {"type": "object", "properties": {"operation": {"type": "string", "enum": ["remove", "set_quantity", "clear"]}, "product_name": {"type": "string"}, "size": {"type": "string"}, "quantity": {"type": "integer", "minimum": 1, "maximum": 20}}, "required": ["operation"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "get_product_options", "description": "Comprueba si hay opciones adicionales configuradas para los productos generales.", "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "get_my_orders", "description": "Consulta hasta los cinco pedidos recientes del usuario autenticado. Nunca consultar pedidos de otra persona.", "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "get_store_info", "description": "Obtiene la ubicación, WhatsApp, domicilio y estado de tienda configurados realmente.", "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "get_navigation_help", "description": "Indica la ruta real dentro de NexaFarma para pedidos, cuenta, carrito o contacto.", "parameters": {"type": "object", "properties": {"topic": {"type": "string"}}, "required": ["topic"], "additionalProperties": False}}},
]


def _agent_execute_tool(name, args):
    if name == "search_products": return _agent_search_products(args)
    if name == "add_product_to_cart": return _agent_add_product_to_cart(args)
    if name == "get_cart_summary": return _agent_cart_summary(args)
    if name == "manage_cart": return _agent_manage_cart(args)
    if name == "get_product_options": return _agent_product_options(args)
    if name == "get_my_orders": return _agent_my_orders(args)
    if name == "get_store_info": return _agent_store_info(args)
    if name == "get_navigation_help": return _agent_navigation_help(args)
    return {"ok": False, "message": "Herramienta no permitida."}


def _agent_fallback(message):
    """Ayuda determinista del catálogo y la compra, sin consejos clínicos."""
    text = re.sub(r"\s+", " ", str(message or "")).strip()
    norm = _agent_norm(text)
    if _agent_cart_management_is_explicit(text, "clear"):
        result = _agent_manage_cart({"operation": "clear"})
        return result["message"], [result["action"]] if result.get("ok") else [], "agent-action-fallback"
    remove_match = re.search(r"\b(?:quita|saca|borra|elimina)\s+(.+?)\s+(?:del|de mi)\s+carrito\b", norm)
    if remove_match and _agent_cart_management_is_explicit(text, "remove"):
        result = _agent_manage_cart({"operation": "remove", "product_name": remove_match.group(1).strip()})
        return result["message"], [result["action"]] if result.get("ok") else [], "agent-action-fallback"
    quantity_match = re.search(r"\b(?:cambia|actualiza|deja|pon|ajusta)\s+(?:(?:la\s+)?cantidad de\s+)?(.+?)\s+(?:a|en)\s+(\d+)\b", norm)
    if quantity_match and _agent_cart_management_is_explicit(text, "set_quantity"):
        result = _agent_manage_cart({"operation": "set_quantity", "product_name": quantity_match.group(1).strip(), "quantity": quantity_match.group(2)})
        return result["message"], [result["action"]] if result.get("ok") else [], "agent-action-fallback"
    add_match = re.search(r"\b(?:agrega|anade|añade|mete|pon)\s+(?:(\d+)\s+)?(.+?)\s+(?:al|en el)\s+carrito\s*(?:\.)?$", text, re.IGNORECASE)
    if add_match:
        name = add_match.group(2).strip(" .,!¿?¡")
        if re.search(r"\b(?:tamano|talla|con|sabor|presentacion)\b", _agent_norm(name)):
            return "Para elegir una presentación, abre la ficha del producto y revisa las opciones disponibles. No agregué nada al carrito.", [], "agent-action-fallback"
        result = _agent_add_product_to_cart({"product_name": name, "quantity": int(add_match.group(1) or 1)})
        if result.get("ok"):
            return result.get("message", "Producto agregado al carrito."), ["add_to_cart"], "agent-action-fallback"
        if result.get("choices"):
            options = ", ".join(item["name"] for item in result["choices"])
            return f"Encontré varias opciones y no agregué nada: {options}. Escribe el nombre exacto del producto.", [], "agent-action-fallback"
        return result.get("message", "No pude agregar el producto."), [], "agent-action-fallback"
    if any(k in norm for k in ["mi carrito", "ver carrito", "resumen del carrito", "cuanto llevo", "cuanto cuesta todo"]):
        result = _agent_cart_summary({})
        if not result.get("items"):
            return "Tu carrito está vacío. Puedes decirme el nombre exacto de un producto general del catálogo que quieras agregar.", [], "agent-action-fallback"
        lines = [f"{i['quantity']} × {i['name']} ({i['line_total']})" for i in result["items"]]
        return "Tu carrito:\n" + "\n".join(lines) + f"\nSubtotal: {result['subtotal']}\nTotal con domicilio: {result['total_home']}\nTotal recogiendo: {result['total_pickup']}\nRevisa aquí: {result['cart_url']}", [], "agent-action-fallback"
    if any(k in norm for k in ["donde estan mis pedidos", "donde veo mis pedidos", "no sale mis pedidos", "mis pedidos"]):
        result = _agent_navigation_help({"topic": "pedidos"})
        return f"Tus pedidos están en Mi cuenta → Mis pedidos: {result['url']}. Inicia sesión con la cuenta utilizada al comprar.", [], "agent-action-fallback"
    if any(k in norm for k in ["estado del pedido", "como va mi pedido", "seguimiento de pedido", "mis ordenes"]):
        result = _agent_my_orders({})
        if result.get("login_required"):
            return result["message"], [], "agent-action-fallback"
        if not result.get("orders"):
            return "No encontré pedidos asociados a la cuenta con sesión iniciada.", [], "agent-action-fallback"
        return "Estos son tus pedidos recientes:\n" + "\n".join(f"#{o['order_no']}: {o['status']} · {o['total']} · pago {o['payment_status']}" for o in result["orders"]), [], "agent-action-fallback"
    if any(k in norm for k in ["donde queda", "ubicacion", "direccion", "donde estan ubicados"]):
        info = _agent_store_info({})
        return f"La ubicación configurada para NexaFarma es: {info['address']}. Es una tienda ficticia de demostración. WhatsApp: {info['whatsapp']}", [], "agent-action-fallback"
    if any(k in norm for k in ["catalogo", "menu", "productos", "que venden", "disponibles", "inventario", "buscar"]):
        result = _agent_search_products({"limit": 6})
        if result["products"]:
            return "Estos productos generales aparecen disponibles en el catálogo:\n" + "\n".join(f"• {p['name']} — {p['price_label']} ({p['category']})" for p in result["products"]) + "\nAbre cada ficha para consultar la información completa.", [], "agent-action-fallback"
        return "No hay productos generales disponibles en este momento. Revisa el catálogo o contacta al administrador.", [], "agent-action-fallback"
    if any(k in norm for k in ["precio", "cuesta", "vale", "valor"]):
        result = _agent_search_products({"query": text, "limit": 5})
        if result["products"]:
            return "Coincidencias del catálogo:\n" + "\n".join(f"• {p['name']}: {p['price_label']}" for p in result["products"]), [], "agent-action-fallback"
        return "Consulta el precio vigente en la ficha del producto. El total definitivo aparece en el carrito antes de confirmar.", [], "agent-action-fallback"
    if any(k in norm for k in ["pago", "pagar", "mercado pago", "mercadopago", "wompi", "transferencia"]):
        enabled = "Mercado Pago detectado como configurado." if mercadopago_is_configured() else "Mercado Pago está pendiente de configuración."
        return "El checkout solo debe mostrar los métodos habilitados por el servidor. " + enabled + " Wompi debe configurarse y probarse en ambiente de pruebas antes de habilitar cobros reales.", [], "agent-action-fallback"
    if any(k in norm for k in ["domicilio", "envio", "entrega", "recoger"]):
        return f"El costo de domicilio configurado actualmente es {money(CONFIG['delivery_fee'])}. Confirma la cobertura y el total en el checkout.", [], "agent-action-fallback"
    if any(k in norm for k in ["whatsapp", "contacto", "hablar con alguien", "ayuda"]):
        return (f"Puedes contactar a NexaFarma por WhatsApp: https://wa.me/{CONFIG['whatsapp']} o abrir la sección Contacto." if CONFIG["whatsapp"] else "WhatsApp aún no está configurado. Puedes abrir la sección Contacto."), [], "agent-action-fallback"
    if any(k in norm for k in ["receta", "formula", "prescripcion"]):
        return "Los artículos marcados como sujetos a fórmula están bloqueados para compra online en esta demo. No tenemos un proceso de validación de recetas; consulta una farmacia habilitada y a un profesional de salud.", [], "agent-action-fallback"
    return "Soy el asistente de NexaFarma. Puedo ayudarte con el catálogo general, precios publicados, carrito, pedidos, pagos y navegación. No doy diagnósticos, recomendaciones de medicamentos ni dosis. Esta tienda es una demostración académica, no una farmacia real.", [], "agent-action-fallback"


def _agent_is_medical_advice_request(message):
    """Detecta consultas clínicas para no enviarlas al modelo comercial ni ejecutar herramientas."""
    norm = _agent_norm(str(message or ""))
    phrases = (
        "que medicamento", "que medicina", "que me tomo", "que puedo tomar", "que debo tomar",
        "que antibiotico", "que dosis", "cuantas pastillas", "cada cuantas horas", "como tratar",
        "que tratamiento", "sirve para curar", "para curar", "tengo fiebre", "tengo dolor",
        "me duele", "tengo sintomas", "tengo una infeccion", "interaccion de medicamentos",
        "efectos secundarios", "estoy embarazada", "que puedo darle",
        "diagnostico", "dosificacion", "posologia", "antibiotico", "medicamento", "medicina", "pastilla", "dosis", "tratamiento medico", "fiebre", "dolor", "tos", "gripa", "gripe", "alergia", "infeccion", "diarrea", "vomito", "vomitos", "embarazo", "acidez",
    )
    return any(phrase in norm for phrase in phrases)


def _agent_cart_add_is_denied(message):
    """Detecta negaciones claras para que nunca se interpreten como permiso de añadir al carrito."""
    normalized = _agent_norm(message)
    denied_patterns = (
        r"\bno\s+(?:quiero|deseo)\s+(?:que\s+)?(?:me\s+)?(?:agregues|anadas|metas|pongas|agregar|anadir|meter|poner)\b",
        r"\bno\s+(?:agregues|anadas|metas|pongas|agregar|anadir|meter|poner)\b",
        r"\bno\s+(?:lo|la|los|las)\s+(?:agregues|anadas|metas|pongas|hagas)\b",
        r"\bno\s+(?:lo\s+)?hagas\b",
        r"\bno\s+necesito\s+que\s+(?:agregues|anadas|metas|pongas)\b",
        r"\b(?:pero|aunque|espera)\s+no\b",
    )
    return any(re.search(pattern, normalized, re.IGNORECASE) for pattern in denied_patterns)


def _agent_cart_management_is_explicit(message, operation):
    """Evita que una llamada del modelo borre o edite el carrito por inferencia."""
    text = _agent_norm(message)
    if re.search(r"\bno\s+(?:vacies|limpies|borres|elimines|quites|cambies|modifiques|actualices|reduzcas|aumentes)\b", text):
        return False
    if operation == "clear":
        return bool(re.search(r"\b(?:vacia\w*|vaciar|limpia\w*|limpiar|borra\w*|borrar|elimina\w*|eliminar)\b.{0,60}\bcarrito\b", text))
    if operation == "remove":
        return bool(re.search(r"\b(?:quita|quitar|saca|sacar|borra|borrar|elimina|eliminar)\b.{0,120}\b(?:carrito|producto|articulo|articulos)\b", text))
    if operation == "set_quantity":
        return "carrito" in text and bool(re.search(r"\b(?:cambia|cambiar|actualiza|actualizar|deja|pon|ajusta|ajustar)\b.{0,120}\b\d+\b", text))
    return False


def _agent_explicit_cart_add(message):
    """Ejecuta peticiones inequívocas de añadir al carrito sin depender de que el modelo infiera la acción."""
    normalized = _agent_norm(message)
    if _agent_cart_add_is_denied(normalized):
        return None
    patterns = (
        r"\b(?:agrega|anade|mete|pon)\s+(?:(\d+)\s+)?(.+?)\s+(?:al|en el)\s+carrito(?P<trailing>.*)$",
        r"\b(?:quiero|por favor)\s+(?:agregar|anadir|meter|poner)\s+(?:(\d+)\s+)?(.+?)\s+(?:al|en el)\s+carrito(?P<trailing>.*)$",
        r"\bquiero que\s+(?:agregues|anadas|metas|pongas)\s+(?:(\d+)\s+)?(.+?)\s+(?:al|en el)\s+carrito(?P<trailing>.*)$",
    )
    match = None
    for pattern in patterns:
        match = re.search(pattern, normalized, re.IGNORECASE)
        if match:
            break
    if not match:
        return None
    trailing = (match.groupdict().get("trailing") or "").strip(" .,!¿?¡\"'")
    quantity = int(match.group(1) or 1)
    product_name = match.group(2).strip(" .,!¿?¡\"'")
    product_name = re.sub(r"^(?:un|una|el|la|los|las)\s+", "", product_name)
    # Delega al modelo los modificadores en lenguaje natural; el backend seguirá validando
    # el nombre exacto y el inventario antes de modificar el carrito.
    if trailing or re.search(r"\b(?:tamano|talla|con|sabor)\b", _agent_norm(product_name)):
        return {"ok": False, "requires_ai_options": True, "message": "No agregué nada todavía: voy a validar el tamaño y los extras que pediste."}
    return _agent_add_product_to_cart({"product_name": product_name, "quantity": quantity})


def _agent_summarize_tool_results(results):
    """Construye una respuesta usando exclusivamente datos devueltos por las herramientas."""
    summaries = []
    for result in results:
        if not isinstance(result, dict):
            continue
        if result.get("message"):
            summaries.append(str(result["message"]))
        elif result.get("products"):
            summaries.append("Productos disponibles en el catálogo: " + ", ".join(
                f"{p.get('name', 'Producto')} ({p.get('price_label', 'precio en ficha')})"
                for p in result["products"][:5]
            ) + ". Abre la ficha para revisar descripción y existencias.")
        elif "items" in result:
            if result["items"]:
                summaries.append(
                    "Tu carrito: " + "; ".join(
                        f"{item.get('quantity', 1)} × {item.get('name', 'Producto')} ({item.get('line_total', '')})"
                        for item in result["items"][:8]
                    ) + f". Subtotal: {result.get('subtotal', '$0')}. Total estimado con domicilio: {result.get('total_home', result.get('subtotal', '$0'))}."
                )
            else:
                summaries.append("Tu carrito está vacío. Revisa el catálogo para elegir un artículo general.")
        elif result.get("where"):
            summaries.append(f"Puedes encontrarlo en {result['where']}: {result.get('url', '')}")
        elif result.get("sizes") is not None and result.get("toppings") is not None:
            summaries.append(result.get("message") or "Los productos se venden según la ficha publicada, sin personalizaciones adicionales.")
        elif result.get("orders") is not None:
            if result["orders"]:
                summaries.append("Tus pedidos recientes: " + "; ".join(
                    f"#{o.get('order_no')}: {o.get('status')} · {o.get('total')}"
                    for o in result["orders"][:5]
                ))
            else:
                summaries.append("No encontré pedidos asociados a la cuenta con sesión iniciada.")
    return "\n".join(summaries)


def _agent_groq_reply(message, history):
    system = f"""Eres el asistente de una tienda ficticia de farmacia y bienestar llamada {CONFIG['name']}, creada para una demostración académica.
Puedes ayudar con el catálogo general de cuidado personal, higiene, botiquín y dispositivos; consultar precios y existencias; explicar la navegación, el carrito, los pedidos, los métodos de pago y el contacto. Consulta las herramientas para datos actuales y nunca inventes disponibilidad, precios ni estados.
SEGURIDAD SANITARIA OBLIGATORIA: No diagnostiques, no recomiendes medicamentos, no indiques dosis ni duración, no expliques interacciones ni sugieras tratamientos. Si preguntan por síntomas, embarazo, alergias, medicamentos o una urgencia, no uses herramientas de productos ni cambies el carrito; indica que consulten a un médico o farmacéutico habilitado y, ante urgencia, a emergencias locales. Los artículos marcados como sujetos a fórmula están excluidos de la búsqueda y de la compra online. La demo no valida recetas ni pretende ser una farmacia real autorizada.
Para añadir o modificar el carrito exige una instrucción explícita en el mensaje actual; no confirmes pedidos ni realices pagos. Respeta el stock. No pidas contraseñas, datos bancarios, tokens ni información médica sensible. Responde en español colombiano con tono amable y concreto. WhatsApp configurado: {("https://wa.me/" + CONFIG['whatsapp']) if CONFIG['whatsapp'] else 'no configurado; deriva al formulario de Contacto'}."""
    messages = [{"role": "system", "content": system}]
    for entry in (history or [])[-6:]:
        role = entry.get("role")
        content = str(entry.get("content", ""))[:450]
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": str(message)[:700]})

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {GROQ_API_KEY}"}
    action_labels = []
    last_tool_results = []
    normalized = _agent_norm(message)
    allow_cart_action = (not _agent_cart_add_is_denied(normalized)) and bool(re.search(
        r"\b(?:agrega|anade|mete|pon|agregar|anadir|quiero|podrias|puedes)\b.{0,140}\bcarrito\b",
        normalized,
    ))

    for _turn in range(2):
        payload = {
            "model": AGENT_MODEL,
            "messages": messages,
            "tools": AGENT_TOOLS,
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "temperature": 0.1,
            "max_completion_tokens": 900,
        }
        # GPT-OSS puede consumir tokens en razonamiento; limitarlo a bajo deja espacio a tool calls y respuesta.
        if "gpt-oss" in AGENT_MODEL.lower():
            payload["reasoning_effort"] = "low"

        try:
            response = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=(5, 30),
            )
        except requests.RequestException as exc:
            app.logger.warning("No se pudo conectar con Groq para el agente (%s).", type(exc).__name__)
            raise RuntimeError("No se pudo conectar con el proveedor de IA") from exc

        if response.status_code != 200:
            provider_error = ""
            try:
                error_payload = response.json()
                provider_error = str((error_payload.get("error") or {}).get("message") or "")[:180]
            except (ValueError, AttributeError):
                pass
            app.logger.warning(
                "Groq agente respondió HTTP %s%s",
                response.status_code,
                f": {provider_error}" if provider_error else "",
            )
            raise RuntimeError(f"Groq HTTP {response.status_code}")

        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError("Groq devolvió JSON inválido") from exc
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("Groq devolvió una respuesta vacía al agente")
        assistant_message = choices[0].get("message") or {}
        tool_calls = assistant_message.get("tool_calls") or []
        content = str(assistant_message.get("content") or "").strip()

        if not tool_calls:
            if content:
                return content, action_labels, "agent-ai"
            # El modelo puede usar el presupuesto disponible en razonamiento. En ese caso no descartamos
            # resultados reales de herramientas que ya se ejecutaron.
            summary = _agent_summarize_tool_results(last_tool_results)
            if summary:
                return summary, action_labels, "agent-ai-tools"
            raise RuntimeError("La respuesta de Groq llegó vacía")

        # Mantener las llamadas de herramienta y sus respuestas en correspondencia exacta.
        selected_calls = tool_calls[:3]
        messages.append({
            "role": "assistant",
            "content": content or "",
            "tool_calls": selected_calls,
        })

        for tool_call in selected_calls:
            call_id = str(tool_call.get("id") or "")
            function = tool_call.get("function") or {}
            name = str(function.get("name") or "")
            if not call_id or not name:
                raise RuntimeError("Groq devolvió una llamada de herramienta incompleta")
            try:
                args = json.loads(function.get("arguments") or "{}")
                if not isinstance(args, dict):
                    args = {}
            except (ValueError, TypeError):
                result = {"ok": False, "message": "La IA generó parámetros inválidos; no se ejecutó esa acción."}
            else:
                if name == "add_product_to_cart" and not allow_cart_action:
                    result = {
                        "ok": False,
                        "message": "No agregué nada porque el mensaje actual no solicita modificar el carrito. Si quieres hacerlo, di: ‘Agrega 1 Gel antibacterial 250 ml al carrito’."
                    }
                elif name == "manage_cart" and not _agent_cart_management_is_explicit(message, args.get("operation", "")):
                    result = {"ok": False, "message": "No cambié el carrito porque el mensaje actual no solicita esa acción de forma explícita."}
                else:
                    result = _agent_execute_tool(name, args)
            last_tool_results.append(result)
            if result.get("action"):
                action_labels.append(result["action"])
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "name": name,
                "content": json.dumps(result, ensure_ascii=False)[:4000],
            })

    # No se inicia una cadena ilimitada de herramientas: se presenta el resultado real comprobado.
    summary = _agent_summarize_tool_results(last_tool_results)
    return summary or "Revisé las herramientas disponibles, pero no obtuve una respuesta completa. Prueba de nuevo o abre el menú.", action_labels, "agent-ai-tools"


@app.post("/api/agent")
def api_agent():
    data = request.get_json(silent=True) or {}
    message = str(data.get("message") or "").strip()[:700]
    if not message:
        return jsonify({"error": "Mensaje vacío", "mode": "agent-invalid"}), 400

    history = session.get("agent_history", [])
    history = history if isinstance(history, list) else []
    actions = []
    ai_error = False
    try:
        if _agent_is_medical_advice_request(message):
            reply = "No puedo diagnosticar ni indicar medicamentos, dosis o tratamientos. Para elegir una opción segura según tus circunstancias, consulta a un médico o farmacéutico habilitado. NexaFarma es una demostración académica y no una farmacia real."
            mode = "agent-health-safety"
        elif _agent_cart_add_is_denied(message):
            # Una negación explícita siempre prevalece: no consulta al modelo ni cambia el carrito.
            reply = "Entendido. No haré cambios en el carrito. Si cambiaste de idea, envía una nueva instrucción afirmativa."
            mode = "agent-no-action"
        else:
            # Las órdenes simples de añadir se validan directamente en Python para que no dependan
            # de que el modelo decida emitir una llamada de herramienta.
            add_result = _agent_explicit_cart_add(message)
            if add_result and add_result.get("requires_ai_options") and GROQ_API_KEY:
                add_result = None
            if add_result is not None:
                reply = str(add_result.get("message") or "No pude procesar la petición de carrito.")
                if add_result.get("ok"):
                    actions = [str(add_result.get("action") or "add_to_cart")]
                    reply += "\nLos productos se agregan según la presentación descrita en su ficha."
                    reply += f"\nSubtotal del carrito: {add_result.get('cart_subtotal', 'no disponible')}. Puedes revisarlo antes de continuar: {add_result.get('cart_url', url_for('cart_view'))}"
                    mode = "agent-action"
                else:
                    if add_result.get("choices"):
                        reply += " Opciones: " + ", ".join(
                            f"{item.get('name')} ({item.get('price')})" for item in add_result["choices"]
                        ) + ". Dime el nombre exacto y no agregaré nada hasta identificarlo."
                    mode = "agent-action-failed"
            elif GROQ_API_KEY:
                try:
                    reply, actions, mode = _agent_groq_reply(message, history)
                except Exception as exc:
                    app.logger.warning("Agente IA no disponible (%s); se usará ayuda local.", type(exc).__name__)
                    reply, actions, mode = _agent_fallback(message)
                    mode = "agent-local-fallback"
                    ai_error = True
            else:
                reply, actions, mode = _agent_fallback(message)
                mode = "agent-local-only"

        history.extend([{"role": "user", "content": message}, {"role": "assistant", "content": reply}])
        session["agent_history"] = [
            {"role": item.get("role"), "content": str(item.get("content", ""))[:450]}
            for item in history[-6:]
            if isinstance(item, dict)
        ]
        session.modified = True
        return jsonify({
            "reply": reply,
            "mode": mode,
            "actions": actions,
            "ai_error": ai_error,
            "ai_configured": bool(GROQ_API_KEY),
        })
    except Exception:
        app.logger.exception("Error inesperado en /api/agent")
        return jsonify({
            "reply": "El agente tuvo un problema interno al procesar la solicitud. Puedes abrir el menú o contactar a NexaFarma por WhatsApp.",
            "mode": "agent-error",
            "actions": [],
            "ai_configured": bool(GROQ_API_KEY),
        }), 500

@app.post("/api/agent/reset")
def api_agent_reset():
    session.pop("agent_history", None)
    return jsonify({"ok": True})


@app.get("/salud")
def health():
    conn=db()
    try:
        conn.execute("SELECT 1")
    finally:
        conn.close()
    return jsonify({"ok":True,"service":"NexaFarma","wompi_enabled":wompi_is_available(),"wompi_temporarily_unavailable":WOMPI_TEMPORARILY_UNAVAILABLE,"mercadopago_enabled":mercadopago_is_configured()})


@app.errorhandler(400)
def bad_request(error): return render_template("error.html",code=400,title="Solicitud no válida",message=getattr(error,"description","Revisa los datos enviados.")),400


@app.errorhandler(404)
def page_not_found(error): return render_template("error.html",code=404,title="Página no encontrada",message="La página que buscas no existe o cambió de lugar."),404


@app.errorhandler(403)
def forbidden(error): return render_template("error.html",code=403,title="Acceso restringido",message="No tienes permisos para entrar aquí."),403


@app.errorhandler(413)
def too_large(error): return render_template("error.html",code=413,title="Archivo demasiado grande",message="La imagen supera el límite permitido de 5 MB."),413


@app.errorhandler(500)
def internal_error(error):
    app.logger.exception("Error 500 no controlado")
    try:
        return render_template("error.html", code=500, title="Algo salió mal", message="Tuvimos un problema interno. Inténtalo de nuevo."), 500
    except Exception:
        return (
            "<!doctype html><html lang='es'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>NexaFarma · Error 500</title><style>body{font-family:system-ui;background:#f7fbfa;color:#193536;padding:50px;text-align:center}"
            ".box{max-width:680px;margin:auto;padding:35px;border:1px solid #d6e9e5;border-radius:24px;background:#fff}a{color:#087f83;font-weight:800}</style>"
            "<div class='box'><h1>No pudimos cargar esta página</h1><p>El servidor sigue activo. Intenta volver al inicio o abrir el menú.</p>"
            "<p><a href='/'>Inicio</a> · <a href='/menu'>Menú</a> · <a href='/salud'>Estado del sistema</a></p></div></html>",
            500,
        )


init_db(); bootstrap_admin()

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","5000")),debug=False)
