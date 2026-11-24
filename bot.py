import asyncio
import logging
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)
from aiogram.utils.deep_linking import create_start_link

# ================= الإعدادات الأساسية =================
BOT_TOKEN = os.getenv("BOT_TOKEN", "9241681663:AAGewOZ0iVaZ78eAawyWlLYCMM7UHP7WWnA")
OWNER_ID = int(os.getenv("OWNER_ID", "8704088385"))
DB_PATH = os.getenv("DB_PATH", "data/point_market.db")
DEFAULT_REFERRAL_REWARD = 1  # نقطة واحدة لكل صديق جديد
DEFAULT_LISTING_FEE = 2  # سحب 2 نقطة عند عرض شيء للبيع من طرف المستخدم
DEFAULT_PROMO_COST_PER_MEMBER = 1  # 1 نقطة لكل عضو في تمويل القنوات
DEFAULT_PROMO_REWARD_PER_JOIN = 1  # 1 نقطة لكل عضو ينضم لقناة ممولة
DEFAULT_SUPPORT_CONTACT = "@NIRKAGE"

WELCOME_TRUST_TEXT = (
    "✨ <b>أهلاً بك في وجهتك الموثوقة للنقاط والمنتجات الرقمية!</b>\n"
    "تجربة سريعة، تسليم مضمون، وشفافية كاملة في كل خطوة.\n"
    "👇 اختر وجهتك من القائمة أدناه:"
)

SELLER_PAYMENT_OPTIONS: list[tuple[str, str]] = [
    ("💵 دولار ($)", "دولار ($)"),
    ("🇩🇿 دينار جزائري (دج)", "دينار جزائري (دج)"),
    ("📱 فليكسي", "فليكسي"),
    ("💳 باي بال (PayPal)", "باي بال (PayPal)"),
    ("🟡 بينانس (Binance)", "بينانس (Binance)"),
    ("⭐ نجوم تيليجرام", "نجوم تيليجرام"),
]

DEFAULT_POINT_PRICES: dict[str, str] = {
    "price_stars": "كل 1 نجمة = 1 نقطة",
    "price_flexy": "كل 10,000 دج = 50 نقطة",
    "price_paypal": "كل 1$ = 100 نقطة",
    "price_binance": "كل 1$ = 80 نقطة",
    "price_gplay": "كل 1$ = 80 نقطة",
}

POINT_PRICE_LABELS: dict[str, tuple[str, str]] = {
    "price_stars": ("⭐ نجوم تيليجرام", "نجوم تيليجرام"),
    "price_flexy": ("📱 فليكسي", "فليكسي"),
    "price_paypal": ("💳 دولارات باي بال", "دولارات باي بال"),
    "price_binance": ("🟡 دولار بينانس", "دولار بينانس"),
    "price_gplay": ("🎮 دولار قوقل بلاي (رابط دفع)", "دولار قوقل بلاي (رابط دفع)"),
}

BUY_METHOD_CONFIG: list[tuple[str, str, str]] = [
    ("price_stars", "buy_user_stars", "⭐ شراء عبر نجوم تيليجرام"),
    ("price_flexy", "buy_user_flexy", "📱 شراء عبر فليكسي"),
    ("price_paypal", "buy_user_paypal", "💳 شراء عبر باي بال"),
    ("price_binance", "buy_user_binance", "🟡 شراء عبر بينانس"),
    ("price_gplay", "buy_user_gplay", "🎮 شراء عبر قوقل بلاي"),
]

BUY_USER_LABELS: dict[str, str] = {
    "buy_user_stars": "⭐ زر نجوم تيليجرام",
    "buy_user_flexy": "📱 زر فليكسي",
    "buy_user_paypal": "💳 زر باي بال",
    "buy_user_binance": "🟡 زر بينانس",
    "buy_user_gplay": "🎮 زر قوقل بلاي",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("point_market")


def utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def parse_delivery_payload(raw_text: str) -> list[str]:
    """يحفظ الرسالة كاملة كما أُرسلت (بكل سطورها) دون تقطيع الأسطر."""
    text = raw_text.strip("\r\n")
    if not text.strip():
        return []
    if "\n---\n" in text:
        return [chunk.strip("\r\n") for chunk in text.split("\n---\n") if chunk.strip()]
    return [text]


def build_contact_url(contact: str) -> str:
    raw = (contact or "").strip() or DEFAULT_SUPPORT_CONTACT
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    username = raw.lstrip("@").strip()
    return f"https://t.me/{username or 'NIRKAGE'}"


def format_seller_handle(username: Optional[str], user_id: int) -> str:
    if username and username.strip():
        clean = username.strip().lstrip("@")
        return f"@{clean}"
    return f"<code>{user_id}</code>"


def build_seller_contact_url(username: Optional[str], user_id: int) -> str:
    if username and username.strip():
        clean = username.strip().lstrip("@")
        return f"https://t.me/{clean}"
    return f"tg://user?id={user_id}"


def parse_channel_target(raw_input: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    يستخرج (chat_target_for_api, join_url, display_label)
    من @username أو https://t.me/username أو https://t.me/+invite أو -100...
    """
    text = (raw_input or "").strip()
    if not text:
        return None, None, None

    if re.fullmatch(r"-?\d+", text):
        chat_id_str = text if text.startswith("-") else f"-100{text}"
        return chat_id_str, None, chat_id_str

    if text.startswith("@"):
        uname = text.lstrip("@").split("/")[0].split("?")[0].strip()
        if not uname:
            return None, None, None
        return f"@{uname}", f"https://t.me/{uname}", f"@{uname}"

    m_invite = re.search(r"(?:https?://)?(?:t\.me|telegram\.me)/\+([A-Za-z0-9_-]+)", text)
    if m_invite:
        url = f"https://t.me/+{m_invite.group(1)}"
        return None, url, url

    m_joinchat = re.search(r"(?:https?://)?(?:t\.me|telegram\.me)/joinchat/([A-Za-z0-9_-]+)", text)
    if m_joinchat:
        url = f"https://t.me/joinchat/{m_joinchat.group(1)}"
        return None, url, url

    m_pub = re.search(r"(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]+)", text)
    if m_pub:
        uname = m_pub.group(1)
        if uname.lower() not in {"s", "c"}:
            return f"@{uname}", f"https://t.me/{uname}", f"@{uname}"

    return None, None, None


def build_buy_points_text(support_contact: str, prices: dict[str, str]) -> str:
    owner_tag = support_contact.strip() or DEFAULT_SUPPORT_CONTACT
    if not owner_tag.startswith("@") and not owner_tag.startswith("http"):
        owner_tag = f"@{owner_tag}"

    p_stars = prices.get("price_stars", DEFAULT_POINT_PRICES["price_stars"])
    p_flexy = prices.get("price_flexy", DEFAULT_POINT_PRICES["price_flexy"])
    p_paypal = prices.get("price_paypal", DEFAULT_POINT_PRICES["price_paypal"])
    p_binance = prices.get("price_binance", DEFAULT_POINT_PRICES["price_binance"])
    p_gplay = prices.get("price_gplay", DEFAULT_POINT_PRICES["price_gplay"])

    return (
        "💎 <b>شراء النقاط — طرق الدفع والأسعار</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "يمكنك شحن رصيدك من النقاط بسهولة عبر:\n"
        "• ⭐ <b>نجوم تيليجرام (Telegram Stars)</b>\n"
        "• 📱 <b>فليكسي (Flexy)</b>\n"
        "• 💳 <b>دولارات باي بال (PayPal USD)</b>\n"
        "• 🟡 <b>بينانس (Binance USD)</b>\n"
        "• 🎮 <b>دولارات قوقل بلاي (Google Play USD)</b> عبر رابط دفع\n\n"
        "📋 <b>قائمة الأسعار المعتمدة:</b>\n"
        f"• ⭐ <b>نجوم تيليجرام:</b> <b>{p_stars}</b>\n"
        f"• 📱 <b>فليكسي:</b> <b>{p_flexy}</b>\n"
        f"• 💳 <b>دولارات باي بال:</b> <b>{p_paypal}</b>\n"
        f"• 🟡 <b>دولار بينانس:</b> <b>{p_binance}</b>\n"
        f"• 🎮 <b>دولار قوقل بلاي (رابط دفع):</b> <b>{p_gplay}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📩 للطلب والشحن الفوري، يُرجى التواصل مع المالك: <b>{owner_tag}</b>"
    )


def user_screen_kb(uid: int) -> InlineKeyboardMarkup:
    """أزرار المستخدمين تظهر على الشاشة داخل الرسالة وليس في لوحة المفاتيح السفلية."""
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="🛍️ المتجر", callback_data="u_secs")],
        [InlineKeyboardButton(text="📢 عرض شيء للبيع", callback_data="u_sell_start")],
        [
            InlineKeyboardButton(text="💰 محفظتي", callback_data="u_wallet"),
            InlineKeyboardButton(text="🎁 جمع النقاط", callback_data="u_earn"),
        ],
        [
            InlineKeyboardButton(text="📣 تمويل قناة", callback_data="u_promo_add"),
            InlineKeyboardButton(text="📺 قنوات التمويل", callback_data="u_promo_list:0"),
        ],
        [
            InlineKeyboardButton(text="👥 دعوة الأصدقاء", callback_data="u_ref"),
            InlineKeyboardButton(text="📦 طلباتي ومشترياتي", callback_data="u_orders"),
        ],
        [
            InlineKeyboardButton(text="📊 إحصائياتي", callback_data="u_stats"),
            InlineKeyboardButton(text="📞 الدعم الفني", callback_data="u_support"),
        ],
    ]
    if uid == OWNER_ID:
        rows.append([InlineKeyboardButton(text="⚙️ لوحة تحكم المالك", callback_data="a_back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def user_cancel_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🏠 إلغاء والعودة للقائمة الرئيسية", callback_data="u_home")]]
    )


def seller_payment_methods_kb() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for idx in range(0, len(SELLER_PAYMENT_OPTIONS), 2):
        pair = [
            InlineKeyboardButton(text=label, callback_data=f"u_sell_pm:{idx}")
            for idx, (label, _) in enumerate(SELLER_PAYMENT_OPTIONS[idx : idx + 2], start=idx)
        ]
        rows.append(pair)
    rows.append([InlineKeyboardButton(text="✍️ كتابة طريقة بيع مخصصة", callback_data="u_sell_pm_custom")])
    rows.append([InlineKeyboardButton(text="🏠 إلغاء والعودة للقائمة الرئيسية", callback_data="u_home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_earn_points_kb(buy_contacts: dict[str, str], fallback_contact: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for _, setting_key, btn_text in BUY_METHOD_CONFIG:
        target = buy_contacts.get(setting_key, fallback_contact)
        rows.append([InlineKeyboardButton(text=btn_text, url=build_contact_url(target))])
    rows.append([InlineKeyboardButton(text="📺 قنوات التمويل (+نقاط)", callback_data="u_promo_list:0")])
    rows.append([InlineKeyboardButton(text="🎟️ استخدام كود نقاط", callback_data="u_redeem_promo")])
    rows.append([InlineKeyboardButton(text="🏠 القائمة الرئيسية", callback_data="u_home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_home_btn() -> InlineKeyboardButton:
    return InlineKeyboardButton(text="🏠 القائمة الرئيسية", callback_data="a_back")


def admin_cancel_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🏠 إلغاء والعودة للقائمة الرئيسية", callback_data="a_back")]]
    )


# ================= طبقة قاعدة البيانات (SQLite) =================
class Database:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = asyncio.Lock()

    def _connect_sync(self) -> sqlite3.Connection:
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    async def init_db(self) -> None:
        async with self._lock:
            def _run() -> None:
                with self._connect_sync() as conn:
                    conn.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS users (
                            user_id INTEGER PRIMARY KEY,
                            username TEXT,
                            full_name TEXT NOT NULL,
                            balance INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0),
                            earned_points INTEGER NOT NULL DEFAULT 0 CHECK (earned_points >= 0),
                            spent_points INTEGER NOT NULL DEFAULT 0 CHECK (spent_points >= 0),
                            referrer_id INTEGER,
                            referral_rewarded INTEGER NOT NULL DEFAULT 0,
                            is_banned INTEGER NOT NULL DEFAULT 0,
                            created_at TEXT NOT NULL,
                            FOREIGN KEY (referrer_id) REFERENCES users(user_id) ON DELETE SET NULL
                        );

                        CREATE TABLE IF NOT EXISTS sections (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            name TEXT NOT NULL UNIQUE,
                            is_active INTEGER NOT NULL DEFAULT 1,
                            sort_order INTEGER NOT NULL DEFAULT 0,
                            created_at TEXT NOT NULL
                        );

                        CREATE TABLE IF NOT EXISTS products (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            section_id INTEGER NOT NULL,
                            seller_id INTEGER,
                            seller_username TEXT,
                            seller_name TEXT,
                            payment_method TEXT,
                            custom_price_text TEXT,
                            name TEXT NOT NULL,
                            description TEXT NOT NULL,
                            photo_file_id TEXT,
                            price_points INTEGER NOT NULL CHECK (price_points >= 0),
                            delivery_type TEXT NOT NULL CHECK (delivery_type IN ('auto', 'manual')),
                            visibility TEXT NOT NULL CHECK (visibility IN ('public', 'hidden_details', 'completely_hidden')),
                            special_code TEXT UNIQUE,
                            is_active INTEGER NOT NULL DEFAULT 1,
                            created_at TEXT NOT NULL,
                            FOREIGN KEY (section_id) REFERENCES sections(id) ON DELETE CASCADE,
                            FOREIGN KEY (seller_id) REFERENCES users(user_id) ON DELETE SET NULL
                        );

                        CREATE TABLE IF NOT EXISTS inventory (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            product_id INTEGER NOT NULL,
                            delivery_data TEXT NOT NULL,
                            is_sold INTEGER NOT NULL DEFAULT 0,
                            sold_to_user_id INTEGER,
                            sold_at TEXT,
                            created_at TEXT NOT NULL,
                            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
                            FOREIGN KEY (sold_to_user_id) REFERENCES users(user_id) ON DELETE SET NULL
                        );

                        CREATE TABLE IF NOT EXISTS orders (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            order_uid TEXT NOT NULL UNIQUE,
                            user_id INTEGER NOT NULL,
                            product_id INTEGER,
                            product_name TEXT NOT NULL,
                            price_points INTEGER NOT NULL CHECK (price_points >= 0),
                            delivery_type TEXT NOT NULL CHECK (delivery_type IN ('auto', 'manual')),
                            delivery_content TEXT,
                            status TEXT NOT NULL CHECK (status IN ('pending', 'completed', 'rejected')),
                            admin_note TEXT,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL,
                            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE SET NULL
                        );

                        CREATE TABLE IF NOT EXISTS transactions (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            user_id INTEGER NOT NULL,
                            tx_type TEXT NOT NULL,
                            amount INTEGER NOT NULL,
                            balance_after INTEGER NOT NULL CHECK (balance_after >= 0),
                            reason TEXT NOT NULL,
                            related_order_id INTEGER,
                            created_at TEXT NOT NULL,
                            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                            FOREIGN KEY (related_order_id) REFERENCES orders(id) ON DELETE SET NULL
                        );

                        CREATE TABLE IF NOT EXISTS referrals (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            referrer_id INTEGER NOT NULL,
                            referred_id INTEGER NOT NULL UNIQUE,
                            reward_points INTEGER NOT NULL DEFAULT 0,
                            status TEXT NOT NULL CHECK (status IN ('rewarded', 'rejected')),
                            created_at TEXT NOT NULL,
                            FOREIGN KEY (referrer_id) REFERENCES users(user_id) ON DELETE CASCADE,
                            FOREIGN KEY (referred_id) REFERENCES users(user_id) ON DELETE CASCADE
                        );

                        CREATE TABLE IF NOT EXISTS promo_codes (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            code TEXT NOT NULL UNIQUE,
                            points INTEGER NOT NULL CHECK (points > 0),
                            max_uses INTEGER NOT NULL CHECK (max_uses > 0),
                            used_count INTEGER NOT NULL DEFAULT 0 CHECK (used_count >= 0),
                            is_active INTEGER NOT NULL DEFAULT 1,
                            created_at TEXT NOT NULL
                        );

                        CREATE TABLE IF NOT EXISTS promo_code_uses (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            promo_id INTEGER NOT NULL,
                            user_id INTEGER NOT NULL,
                            used_at TEXT NOT NULL,
                            UNIQUE(promo_id, user_id),
                            FOREIGN KEY (promo_id) REFERENCES promo_codes(id) ON DELETE CASCADE,
                            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                        );

                        CREATE TABLE IF NOT EXISTS forced_channels (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            title TEXT NOT NULL,
                            chat_target TEXT NOT NULL,
                            join_url TEXT NOT NULL,
                            is_active INTEGER NOT NULL DEFAULT 1,
                            created_at TEXT NOT NULL
                        );

                        CREATE TABLE IF NOT EXISTS channel_promotions (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            owner_user_id INTEGER NOT NULL,
                            chat_id INTEGER NOT NULL,
                            title TEXT NOT NULL,
                            join_url TEXT NOT NULL,
                            target_members INTEGER NOT NULL CHECK (target_members > 0),
                            completed_members INTEGER NOT NULL DEFAULT 0 CHECK (completed_members >= 0),
                            cost_per_member INTEGER NOT NULL CHECK (cost_per_member > 0),
                            reward_per_join INTEGER NOT NULL CHECK (reward_per_join > 0),
                            is_active INTEGER NOT NULL DEFAULT 1,
                            created_at TEXT NOT NULL,
                            FOREIGN KEY (owner_user_id) REFERENCES users(user_id) ON DELETE CASCADE
                        );

                        CREATE TABLE IF NOT EXISTS channel_promotion_claims (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            promo_id INTEGER NOT NULL,
                            user_id INTEGER NOT NULL,
                            reward_points INTEGER NOT NULL,
                            claimed_at TEXT NOT NULL,
                            UNIQUE(promo_id, user_id),
                            FOREIGN KEY (promo_id) REFERENCES channel_promotions(id) ON DELETE CASCADE,
                            FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
                        );

                        CREATE TABLE IF NOT EXISTS settings (
                            key TEXT PRIMARY KEY,
                            value TEXT NOT NULL
                        );
                        """
                    )

                    cols = [r["name"] for r in conn.execute("PRAGMA table_info(products)").fetchall()]
                    if "photo_file_id" not in cols:
                        conn.execute("ALTER TABLE products ADD COLUMN photo_file_id TEXT;")
                    if "seller_id" not in cols:
                        conn.execute("ALTER TABLE products ADD COLUMN seller_id INTEGER;")
                    if "seller_username" not in cols:
                        conn.execute("ALTER TABLE products ADD COLUMN seller_username TEXT;")
                    if "seller_name" not in cols:
                        conn.execute("ALTER TABLE products ADD COLUMN seller_name TEXT;")
                    if "payment_method" not in cols:
                        conn.execute("ALTER TABLE products ADD COLUMN payment_method TEXT;")
                    if "custom_price_text" not in cols:
                        conn.execute("ALTER TABLE products ADD COLUMN custom_price_text TEXT;")

                    ord_cols = [r["name"] for r in conn.execute("PRAGMA table_info(orders)").fetchall()]
                    if "product_name" not in ord_cols:
                        conn.execute("ALTER TABLE orders ADD COLUMN product_name TEXT NOT NULL DEFAULT 'منتج';")

                    fk_rows = conn.execute("PRAGMA foreign_key_list(orders)").fetchall()
                    needs_orders_rebuild = any(
                        r["table"] == "products" and r["from"] == "product_id" and str(r["on_delete"]).upper() != "SET NULL"
                        for r in fk_rows
                    )
                    if needs_orders_rebuild:
                        conn.execute("PRAGMA foreign_keys = OFF;")
                        conn.execute(
                            """
                            CREATE TABLE IF NOT EXISTS orders_new (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                order_uid TEXT NOT NULL UNIQUE,
                                user_id INTEGER NOT NULL,
                                product_id INTEGER,
                                product_name TEXT NOT NULL,
                                price_points INTEGER NOT NULL CHECK (price_points >= 0),
                                delivery_type TEXT NOT NULL CHECK (delivery_type IN ('auto', 'manual')),
                                delivery_content TEXT,
                                status TEXT NOT NULL CHECK (status IN ('pending', 'completed', 'rejected')),
                                admin_note TEXT,
                                created_at TEXT NOT NULL,
                                updated_at TEXT NOT NULL,
                                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
                                FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE SET NULL
                            );
                            """
                        )
                        conn.execute(
                            """
                            INSERT INTO orders_new (
                                id, order_uid, user_id, product_id, product_name, price_points,
                                delivery_type, delivery_content, status, admin_note, created_at, updated_at
                            )
                            SELECT
                                o.id, o.order_uid, o.user_id, o.product_id,
                                COALESCE(p.name, 'منتج محذوف'),
                                o.price_points, o.delivery_type, o.delivery_content, o.status,
                                o.admin_note, o.created_at, o.updated_at
                            FROM orders o
                            LEFT JOIN products p ON o.product_id = p.id;
                            """
                        )
                        conn.execute("DROP TABLE orders;")
                        conn.execute("ALTER TABLE orders_new RENAME TO orders;")
                        conn.execute("PRAGMA foreign_keys = ON;")

                    defaults = (
                        [
                            ("store_enabled", "1"),
                            ("referral_reward", str(DEFAULT_REFERRAL_REWARD)),
                            ("listing_fee", str(DEFAULT_LISTING_FEE)),
                            ("promo_cost_per_member", str(DEFAULT_PROMO_COST_PER_MEMBER)),
                            ("promo_reward_per_join", str(DEFAULT_PROMO_REWARD_PER_JOIN)),
                            ("support_contact", DEFAULT_SUPPORT_CONTACT),
                        ]
                        + list(DEFAULT_POINT_PRICES.items())
                        + [(setting_key, DEFAULT_SUPPORT_CONTACT) for _, setting_key, _ in BUY_METHOD_CONFIG]
                    )
                    for k, v in defaults:
                        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

                    conn.execute(
                        "INSERT OR IGNORE INTO sections (name, is_active, sort_order, created_at) VALUES (?, 1, 99, ?)",
                        ("📢 عروض الأعضاء", utc_now_str()),
                    )
                    conn.commit()

            await asyncio.to_thread(_run)

    async def get_setting(self, key: str, default: str = "") -> str:
        def _run() -> str:
            with self._connect_sync() as conn:
                row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
                return str(row["value"]) if row else default

        return await asyncio.to_thread(_run)

    async def set_setting(self, key: str, value: str) -> None:
        async with self._lock:
            def _run() -> None:
                with self._connect_sync() as conn:
                    conn.execute(
                        """
                        INSERT INTO settings (key, value) VALUES (?, ?)
                        ON CONFLICT(key) DO UPDATE SET value = excluded.value
                        """,
                        (key, value),
                    )
                    conn.commit()

            await asyncio.to_thread(_run)

    async def get_listing_fee(self) -> int:
        val = await self.get_setting("listing_fee", str(DEFAULT_LISTING_FEE))
        return int(val) if val.isdigit() else DEFAULT_LISTING_FEE

    async def get_promo_cost_per_member(self) -> int:
        val = await self.get_setting("promo_cost_per_member", str(DEFAULT_PROMO_COST_PER_MEMBER))
        return int(val) if val.isdigit() and int(val) > 0 else DEFAULT_PROMO_COST_PER_MEMBER

    async def get_promo_reward_per_join(self) -> int:
        val = await self.get_setting("promo_reward_per_join", str(DEFAULT_PROMO_REWARD_PER_JOIN))
        return int(val) if val.isdigit() and int(val) > 0 else DEFAULT_PROMO_REWARD_PER_JOIN

    # ================= الاشتراك الإجباري =================
    async def add_forced_channel(self, title: str, chat_target: str, join_url: str) -> tuple[bool, str]:
        clean_title = title.strip()[:80]
        clean_target = chat_target.strip()
        clean_url = join_url.strip()
        if not clean_title or not clean_target or not clean_url:
            return False, "بيانات القناة/المجموعة غير مكتملة."
        now = utc_now_str()
        async with self._lock:
            def _run() -> tuple[bool, str]:
                with self._connect_sync() as conn:
                    conn.execute(
                        """
                        INSERT INTO forced_channels (title, chat_target, join_url, is_active, created_at)
                        VALUES (?, ?, ?, 1, ?)
                        """,
                        (clean_title, clean_target, clean_url, now),
                    )
                    conn.commit()
                    return True, f"تمت إضافة «{clean_title}» إلى الاشتراك الإجباري."

            return await asyncio.to_thread(_run)

    async def get_forced_channels(self, active_only: bool = False) -> list[dict[str, Any]]:
        def _run() -> list[dict[str, Any]]:
            with self._connect_sync() as conn:
                sql = "SELECT * FROM forced_channels WHERE is_active = 1 ORDER BY id ASC" if active_only else "SELECT * FROM forced_channels ORDER BY id ASC"
                return [dict(r) for r in conn.execute(sql).fetchall()]

        return await asyncio.to_thread(_run)

    async def toggle_forced_channel(self, ch_id: int) -> None:
        async with self._lock:
            def _run() -> None:
                with self._connect_sync() as conn:
                    row = conn.execute("SELECT is_active FROM forced_channels WHERE id = ?", (ch_id,)).fetchone()
                    if row:
                        nxt = 0 if int(row["is_active"]) == 1 else 1
                        conn.execute("UPDATE forced_channels SET is_active = ? WHERE id = ?", (nxt, ch_id))
                        conn.commit()

            await asyncio.to_thread(_run)

    async def delete_forced_channel(self, ch_id: int) -> None:
        async with self._lock:
            def _run() -> None:
                with self._connect_sync() as conn:
                    conn.execute("DELETE FROM forced_channels WHERE id = ?", (ch_id,))
                    conn.commit()

            await asyncio.to_thread(_run)

    # ================= تمويل القنوات =================
    async def create_channel_promotion(
        self,
        owner_user_id: int,
        chat_id: int,
        title: str,
        join_url: str,
        target_members: int,
    ) -> dict[str, Any]:
        if target_members <= 0:
            return {"ok": False, "error": "عدد الأعضاء يجب أن يكون أكبر من 0."}

        now = utc_now_str()
        async with self._lock:
            def _run() -> dict[str, Any]:
                with self._connect_sync() as conn:
                    conn.execute("BEGIN IMMEDIATE;")
                    try:
                        user = conn.execute("SELECT * FROM users WHERE user_id = ?", (owner_user_id,)).fetchone()
                        if not user or int(user["is_banned"]) == 1:
                            conn.rollback()
                            return {"ok": False, "error": "الحساب غير متاح."}

                        cost_row = conn.execute("SELECT value FROM settings WHERE key = 'promo_cost_per_member'").fetchone()
                        rew_row = conn.execute("SELECT value FROM settings WHERE key = 'promo_reward_per_join'").fetchone()
                        cost_per = int(cost_row["value"]) if cost_row and str(cost_row["value"]).isdigit() and int(cost_row["value"]) > 0 else DEFAULT_PROMO_COST_PER_MEMBER
                        rew_per = int(rew_row["value"]) if rew_row and str(rew_row["value"]).isdigit() and int(rew_row["value"]) > 0 else DEFAULT_PROMO_REWARD_PER_JOIN

                        total_cost = target_members * cost_per
                        bal = int(user["balance"])
                        if bal < total_cost:
                            conn.rollback()
                            return {
                                "ok": False,
                                "error": f"تمويل {target_members} عضو يتطلب <b>{total_cost}</b> نقطة (رصيدك الحالي: <b>{bal}</b> نقطة).",
                            }

                        new_bal = bal - total_cost
                        conn.execute(
                            "UPDATE users SET balance = ?, spent_points = spent_points + ? WHERE user_id = ? AND balance >= ?",
                            (new_bal, total_cost, owner_user_id, total_cost),
                        )

                        cur = conn.execute(
                            """
                            INSERT INTO channel_promotions (
                                owner_user_id, chat_id, title, join_url, target_members,
                                completed_members, cost_per_member, reward_per_join, is_active, created_at
                            ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, 1, ?)
                            """,
                            (owner_user_id, chat_id, title.strip()[:80], join_url.strip(), target_members, cost_per, rew_per, now),
                        )
                        promo_id = int(cur.lastrowid)

                        conn.execute(
                            """
                            INSERT INTO transactions (user_id, tx_type, amount, balance_after, reason, related_order_id, created_at)
                            VALUES (?, 'channel_promo', ?, ?, ?, NULL, ?)
                            """,
                            (owner_user_id, -total_cost, new_bal, f"تمويل قناة ({title}) لـ {target_members} عضو", now),
                        )
                        conn.commit()
                        row = dict(conn.execute("SELECT * FROM channel_promotions WHERE id = ?", (promo_id,)).fetchone())
                        return {
                            "ok": True,
                            "promo": row,
                            "total_cost": total_cost,
                            "new_balance": new_bal,
                        }
                    except Exception:
                        conn.rollback()
                        raise

            return await asyncio.to_thread(_run)

    async def get_active_channel_promotions(self, limit: int = 10, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        def _run() -> tuple[list[dict[str, Any]], int]:
            with self._connect_sync() as conn:
                total = int(
                    conn.execute(
                        "SELECT COUNT(*) AS c FROM channel_promotions WHERE is_active = 1 AND completed_members < target_members"
                    ).fetchone()["c"]
                )
                rows = conn.execute(
                    """
                    SELECT * FROM channel_promotions
                    WHERE is_active = 1 AND completed_members < target_members
                    ORDER BY id DESC LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                ).fetchall()
                return [dict(r) for r in rows], total

        return await asyncio.to_thread(_run)

    async def get_channel_promotion(self, promo_id: int) -> Optional[dict[str, Any]]:
        def _run() -> Optional[dict[str, Any]]:
            with self._connect_sync() as conn:
                row = conn.execute("SELECT * FROM channel_promotions WHERE id = ?", (promo_id,)).fetchone()
                return dict(row) if row else None

        return await asyncio.to_thread(_run)

    async def claim_channel_promotion(self, promo_id: int, user_id: int) -> dict[str, Any]:
        now = utc_now_str()
        async with self._lock:
            def _run() -> dict[str, Any]:
                with self._connect_sync() as conn:
                    conn.execute("BEGIN IMMEDIATE;")
                    try:
                        user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
                        if not user or int(user["is_banned"]) == 1:
                            conn.rollback()
                            return {"ok": False, "error": "الحساب غير متاح."}

                        promo = conn.execute("SELECT * FROM channel_promotions WHERE id = ?", (promo_id,)).fetchone()
                        if not promo:
                            conn.rollback()
                            return {"ok": False, "error": "التمويل غير موجود."}

                        if int(promo["is_active"]) != 1 or int(promo["completed_members"]) >= int(promo["target_members"]):
                            conn.execute("UPDATE channel_promotions SET is_active = 0 WHERE id = ?", (promo_id,))
                            conn.commit()
                            return {"ok": False, "error": "اكتمل العدد المطلوب لهذا التمويل."}

                        already = conn.execute(
                            "SELECT id FROM channel_promotion_claims WHERE promo_id = ? AND user_id = ?",
                            (promo_id, user_id),
                        ).fetchone()
                        if already:
                            conn.rollback()
                            return {"ok": False, "error": "لقد حصلت مسبقاً على نقاط هذه القناة."}

                        rew = int(promo["reward_per_join"])
                        new_comp = int(promo["completed_members"]) + 1
                        new_active = 1 if new_comp < int(promo["target_members"]) else 0

                        conn.execute(
                            """
                            INSERT INTO channel_promotion_claims (promo_id, user_id, reward_points, claimed_at)
                            VALUES (?, ?, ?, ?)
                            """,
                            (promo_id, user_id, rew, now),
                        )
                        conn.execute(
                            "UPDATE channel_promotions SET completed_members = ?, is_active = ? WHERE id = ?",
                            (new_comp, new_active, promo_id),
                        )
                        conn.execute(
                            "UPDATE users SET balance = balance + ?, earned_points = earned_points + ? WHERE user_id = ?",
                            (rew, rew, user_id),
                        )
                        new_bal = int(conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()["balance"])
                        conn.execute(
                            """
                            INSERT INTO transactions (user_id, tx_type, amount, balance_after, reason, related_order_id, created_at)
                            VALUES (?, 'earn_promo_channel', ?, ?, ?, NULL, ?)
                            """,
                            (user_id, rew, new_bal, f"انضمام لقناة ممولة: {promo['title']}", now),
                        )
                        conn.commit()
                        return {
                            "ok": True,
                            "reward": rew,
                            "new_balance": new_bal,
                            "title": str(promo["title"]),
                            "completed": new_comp,
                            "target": int(promo["target_members"]),
                        }
                    except Exception:
                        conn.rollback()
                        raise

            return await asyncio.to_thread(_run)

    # ================= عروض الأعضاء والمتجر =================
    async def get_or_create_user_market_section_id(self) -> int:
        now = utc_now_str()
        async with self._lock:
            def _run() -> int:
                with self._connect_sync() as conn:
                    row = conn.execute("SELECT id FROM sections WHERE name = ?", ("📢 عروض الأعضاء",)).fetchone()
                    if row:
                        conn.execute("UPDATE sections SET is_active = 1 WHERE id = ?", (int(row["id"]),))
                        conn.commit()
                        return int(row["id"])
                    cur = conn.execute(
                        "INSERT INTO sections (name, is_active, sort_order, created_at) VALUES (?, 1, 99, ?)",
                        ("📢 عروض الأعضاء", now),
                    )
                    conn.commit()
                    return int(cur.lastrowid)

            return await asyncio.to_thread(_run)

    async def publish_user_listing(
        self,
        user_id: int,
        name: str,
        description: str,
        payment_method: str,
        custom_price_text: str,
        photo_file_id: Optional[str] = None,
    ) -> dict[str, Any]:
        clean_name = name.strip()
        clean_desc = description.strip()
        clean_method = payment_method.strip()
        clean_price = custom_price_text.strip()

        if len(clean_name) < 2 or len(clean_name) > 100:
            return {"ok": False, "error": "اسم الشيء يجب أن يكون بين حرفين و100 حرف."}
        if not clean_desc:
            return {"ok": False, "error": "يرجى إرسال وصف واضح للشيء."}
        if not clean_method:
            return {"ok": False, "error": "يرجى تحديد طريقة البيع."}
        if not clean_price:
            return {"ok": False, "error": "يرجى إدخال سعر البيع."}

        section_id = await self.get_or_create_user_market_section_id()
        now = utc_now_str()

        async with self._lock:
            def _run() -> dict[str, Any]:
                with self._connect_sync() as conn:
                    conn.execute("BEGIN IMMEDIATE;")
                    try:
                        st = conn.execute("SELECT value FROM settings WHERE key = 'store_enabled'").fetchone()
                        if not st or str(st["value"]) != "1":
                            conn.rollback()
                            return {"ok": False, "error": "المتجر متوقف مؤقتاً."}

                        user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
                        if not user or int(user["is_banned"]) == 1:
                            conn.rollback()
                            return {"ok": False, "error": "الحساب غير متاح أو محظور."}

                        fee_row = conn.execute("SELECT value FROM settings WHERE key = 'listing_fee'").fetchone()
                        fee = int(fee_row["value"]) if fee_row and str(fee_row["value"]).isdigit() else DEFAULT_LISTING_FEE

                        bal = int(user["balance"])
                        if bal < fee:
                            conn.rollback()
                            return {
                                "ok": False,
                                "error": f"تحتاج إلى <b>{fee}</b> نقطة لعرض شيء للبيع، ورصيدك الحالي <b>{bal}</b> نقطة.",
                            }

                        new_bal = bal - fee
                        conn.execute(
                            "UPDATE users SET balance = ?, spent_points = spent_points + ? WHERE user_id = ? AND balance >= ?",
                            (new_bal, fee, user_id, fee),
                        )

                        seller_uname = str(user["username"]) if user["username"] else None
                        seller_fname = str(user["full_name"])

                        cur = conn.execute(
                            """
                            INSERT INTO products (
                                section_id, seller_id, seller_username, seller_name,
                                payment_method, custom_price_text,
                                name, description, photo_file_id, price_points,
                                delivery_type, visibility, special_code, is_active, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'manual', 'public', NULL, 1, ?)
                            """,
                            (
                                section_id,
                                user_id,
                                seller_uname,
                                seller_fname,
                                clean_method,
                                clean_price,
                                clean_name,
                                clean_desc,
                                photo_file_id,
                                now,
                            ),
                        )
                        prod_id = int(cur.lastrowid)

                        conn.execute(
                            """
                            INSERT INTO transactions (user_id, tx_type, amount, balance_after, reason, related_order_id, created_at)
                            VALUES (?, 'listing_fee', ?, ?, ?, NULL, ?)
                            """,
                            (user_id, -fee, new_bal, f"رسوم عرض شيء للبيع: {clean_name}", now),
                        )

                        conn.commit()
                        prod_row = dict(conn.execute("SELECT * FROM products WHERE id = ?", (prod_id,)).fetchone())
                        return {
                            "ok": True,
                            "product": prod_row,
                            "fee": fee,
                            "new_balance": new_bal,
                        }
                    except Exception:
                        conn.rollback()
                        raise

            return await asyncio.to_thread(_run)

    async def delete_seller_product(self, product_id: int, requester_id: int) -> tuple[bool, str]:
        async with self._lock:
            def _run() -> tuple[bool, str]:
                with self._connect_sync() as conn:
                    prod = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
                    if not prod:
                        return False, "الإعلان غير موجود أو تم حذفه مسبقاً."
                    seller_id = prod["seller_id"]
                    if requester_id != OWNER_ID and (seller_id is None or int(seller_id) != requester_id):
                        return False, "لا تملك صلاحية حذف هذا الإعلان."
                    conn.execute("DELETE FROM inventory WHERE product_id = ?", (product_id,))
                    conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
                    conn.commit()
                    return True, "تم حذف إعلانك من المتجر بنجاح."

            return await asyncio.to_thread(_run)

    async def get_point_prices(self) -> dict[str, str]:
        def _run() -> dict[str, str]:
            result = dict(DEFAULT_POINT_PRICES)
            with self._connect_sync() as conn:
                for key in DEFAULT_POINT_PRICES:
                    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
                    if row and str(row["value"]).strip():
                        result[key] = str(row["value"]).strip()
            return result

        return await asyncio.to_thread(_run)

    async def get_buy_button_contacts(self, fallback: str) -> dict[str, str]:
        def _run() -> dict[str, str]:
            result: dict[str, str] = {}
            with self._connect_sync() as conn:
                for _, setting_key, _ in BUY_METHOD_CONFIG:
                    row = conn.execute("SELECT value FROM settings WHERE key = ?", (setting_key,)).fetchone()
                    val = str(row["value"]).strip() if row and str(row["value"]).strip() else fallback
                    result[setting_key] = val
            return result

        return await asyncio.to_thread(_run)

    async def is_store_enabled(self) -> bool:
        return (await self.get_setting("store_enabled", "1")) == "1"

    async def get_referral_reward(self) -> int:
        val = await self.get_setting("referral_reward", str(DEFAULT_REFERRAL_REWARD))
        return int(val) if val.isdigit() else DEFAULT_REFERRAL_REWARD

    async def create_promo_code(self, code: str, points: int, max_uses: int) -> tuple[bool, str, Optional[dict[str, Any]]]:
        clean_code = code.strip()
        if not clean_code or len(clean_code) > 40 or any(ch.isspace() for ch in clean_code):
            return False, "الكود يجب أن يكون بدون مسافات وطوله بين 1 و40 حرفاً.", None
        if points <= 0 or max_uses <= 0:
            return False, "عدد النقاط وعدد الأشخاص يجب أن يكون أكبر من 0.", None

        now = utc_now_str()
        async with self._lock:
            def _run() -> tuple[bool, str, Optional[dict[str, Any]]]:
                with self._connect_sync() as conn:
                    exists = conn.execute(
                        "SELECT id FROM promo_codes WHERE LOWER(code) = LOWER(?)",
                        (clean_code,),
                    ).fetchone()
                    if exists:
                        return False, f"الكود «{clean_code}» موجود مسبقاً.", None

                    cur = conn.execute(
                        """
                        INSERT INTO promo_codes (code, points, max_uses, used_count, is_active, created_at)
                        VALUES (?, ?, ?, 0, 1, ?)
                        """,
                        (clean_code, points, max_uses, now),
                    )
                    conn.commit()
                    row = dict(conn.execute("SELECT * FROM promo_codes WHERE id = ?", (cur.lastrowid,)).fetchone())
                    return True, "تم إنشاء كود النقاط بنجاح.", row

            return await asyncio.to_thread(_run)

    async def get_promo_codes(self) -> list[dict[str, Any]]:
        def _run() -> list[dict[str, Any]]:
            with self._connect_sync() as conn:
                rows = conn.execute("SELECT * FROM promo_codes ORDER BY id DESC").fetchall()
                return [dict(r) for r in rows]

        return await asyncio.to_thread(_run)

    async def delete_promo_code(self, promo_id: int) -> bool:
        async with self._lock:
            def _run() -> bool:
                with self._connect_sync() as conn:
                    cur = conn.execute("DELETE FROM promo_codes WHERE id = ?", (promo_id,))
                    conn.commit()
                    return cur.rowcount > 0

            return await asyncio.to_thread(_run)

    async def redeem_promo_code(self, user_id: int, raw_code: str) -> dict[str, Any]:
        clean_code = raw_code.strip()
        if not clean_code:
            return {"ok": False, "error": "أدخل كوداً صالحاً."}

        now = utc_now_str()
        async with self._lock:
            def _run() -> dict[str, Any]:
                with self._connect_sync() as conn:
                    conn.execute("BEGIN IMMEDIATE;")
                    try:
                        user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
                        if not user or int(user["is_banned"]) == 1:
                            conn.rollback()
                            return {"ok": False, "error": "الحساب غير متاح أو محظور."}

                        promo = conn.execute(
                            "SELECT * FROM promo_codes WHERE LOWER(code) = LOWER(?)",
                            (clean_code,),
                        ).fetchone()
                        if not promo:
                            conn.rollback()
                            return {"ok": False, "error": "الكود غير موجود."}

                        if int(promo["is_active"]) != 1:
                            conn.rollback()
                            return {"ok": False, "error": "هذا الكود غير نشط."}

                        used_cnt = int(promo["used_count"])
                        max_u = int(promo["max_uses"])
                        if used_cnt >= max_u:
                            conn.rollback()
                            return {"ok": False, "error": "تم استنفاد العدد المسموح لهذا الكود."}

                        already_used = conn.execute(
                            "SELECT id FROM promo_code_uses WHERE promo_id = ? AND user_id = ?",
                            (int(promo["id"]), user_id),
                        ).fetchone()
                        if already_used:
                            conn.rollback()
                            return {"ok": False, "error": "لقد استخدمت هذا الكود مسبقاً."}

                        pts = int(promo["points"])
                        new_used = used_cnt + 1
                        new_active = 1 if new_used < max_u else 0

                        conn.execute(
                            "INSERT INTO promo_code_uses (promo_id, user_id, used_at) VALUES (?, ?, ?)",
                            (int(promo["id"]), user_id, now),
                        )
                        conn.execute(
                            "UPDATE promo_codes SET used_count = ?, is_active = ? WHERE id = ?",
                            (new_used, new_active, int(promo["id"])),
                        )
                        conn.execute(
                            "UPDATE users SET balance = balance + ?, earned_points = earned_points + ? WHERE user_id = ?",
                            (pts, pts, user_id),
                        )
                        new_bal = int(conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()["balance"])
                        conn.execute(
                            """
                            INSERT INTO transactions (user_id, tx_type, amount, balance_after, reason, related_order_id, created_at)
                            VALUES (?, 'promo_code', ?, ?, ?, NULL, ?)
                            """,
                            (user_id, pts, new_bal, f"استخدام كود نقاط: {promo['code']}", now),
                        )
                        conn.commit()
                        return {
                            "ok": True,
                            "code": str(promo["code"]),
                            "points": pts,
                            "new_balance": new_bal,
                            "used_count": new_used,
                            "max_uses": max_u,
                        }
                    except Exception:
                        conn.rollback()
                        raise

            return await asyncio.to_thread(_run)

    async def register_or_update_user(
        self,
        user_id: int,
        username: Optional[str],
        full_name: str,
        referrer_candidate: Optional[int] = None,
    ) -> dict[str, Any]:
        clean_name = (full_name or "مستخدم").strip()[:120]
        clean_username = username.strip().lstrip("@") if username else None
        now = utc_now_str()

        async with self._lock:
            def _run() -> dict[str, Any]:
                ref_outcome: dict[str, Any] = {"rewarded": False, "referrer_id": None, "reward_points": 0}
                with self._connect_sync() as conn:
                    conn.execute("BEGIN IMMEDIATE;")
                    try:
                        existing = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
                        if not existing:
                            valid_ref: Optional[int] = None
                            if referrer_candidate and referrer_candidate != user_id:
                                ref_row = conn.execute(
                                    "SELECT user_id, is_banned FROM users WHERE user_id = ?",
                                    (referrer_candidate,),
                                ).fetchone()
                                if ref_row and int(ref_row["is_banned"]) == 0:
                                    valid_ref = int(ref_row["user_id"])

                            conn.execute(
                                """
                                INSERT INTO users (
                                    user_id, username, full_name, balance, earned_points, spent_points,
                                    referrer_id, referral_rewarded, is_banned, created_at
                                ) VALUES (?, ?, ?, 0, 0, 0, ?, 0, 0, ?)
                                """,
                                (user_id, clean_username, clean_name, valid_ref, now),
                            )

                            if valid_ref is not None:
                                rew_row = conn.execute("SELECT value FROM settings WHERE key = 'referral_reward'").fetchone()
                                reward = int(rew_row["value"]) if rew_row and str(rew_row["value"]).isdigit() else DEFAULT_REFERRAL_REWARD
                                already = conn.execute("SELECT id FROM referrals WHERE referred_id = ?", (user_id,)).fetchone()
                                if not already and reward > 0:
                                    conn.execute(
                                        """
                                        INSERT INTO referrals (referrer_id, referred_id, reward_points, status, created_at)
                                        VALUES (?, ?, ?, 'rewarded', ?)
                                        """,
                                        (valid_ref, user_id, reward, now),
                                    )
                                    conn.execute(
                                        "UPDATE users SET balance = balance + ?, earned_points = earned_points + ? WHERE user_id = ?",
                                        (reward, reward, valid_ref),
                                    )
                                    new_bal = int(conn.execute("SELECT balance FROM users WHERE user_id = ?", (valid_ref,)).fetchone()["balance"])
                                    conn.execute(
                                        """
                                        INSERT INTO transactions (user_id, tx_type, amount, balance_after, reason, related_order_id, created_at)
                                        VALUES (?, 'earn_referral', ?, ?, ?, NULL, ?)
                                        """,
                                        (valid_ref, reward, new_bal, f"مكافأة إحالة الصديق {user_id}", now),
                                    )
                                    conn.execute("UPDATE users SET referral_rewarded = 1 WHERE user_id = ?", (user_id,))
                                    ref_outcome = {"rewarded": True, "referrer_id": valid_ref, "reward_points": reward}
                        else:
                            conn.execute(
                                "UPDATE users SET username = ?, full_name = ? WHERE user_id = ?",
                                (clean_username, clean_name, user_id),
                            )
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        raise

                    user_row = dict(conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone())
                    return {"user": user_row, "referral": ref_outcome}

            return await asyncio.to_thread(_run)

    async def get_user(self, user_id: int) -> Optional[dict[str, Any]]:
        def _run() -> Optional[dict[str, Any]]:
            with self._connect_sync() as conn:
                row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
                return dict(row) if row else None

        return await asyncio.to_thread(_run)

    async def get_users_paginated(self, limit: int = 10, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        def _run() -> tuple[list[dict[str, Any]], int]:
            with self._connect_sync() as conn:
                total = int(conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"])
                rows = conn.execute(
                    "SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
                return [dict(r) for r in rows], total

        return await asyncio.to_thread(_run)

    async def get_all_user_ids(self) -> list[int]:
        def _run() -> list[int]:
            with self._connect_sync() as conn:
                rows = conn.execute("SELECT user_id FROM users WHERE is_banned = 0").fetchall()
                return [int(r["user_id"]) for r in rows]

        return await asyncio.to_thread(_run)

    async def get_user_transactions(self, user_id: int, limit: int = 12) -> list[dict[str, Any]]:
        def _run() -> list[dict[str, Any]]:
            with self._connect_sync() as conn:
                rows = conn.execute(
                    "SELECT * FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
                return [dict(r) for r in rows]

        return await asyncio.to_thread(_run)

    async def get_user_referral_count(self, user_id: int) -> int:
        def _run() -> int:
            with self._connect_sync() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM referrals WHERE referrer_id = ? AND status = 'rewarded'",
                    (user_id,),
                ).fetchone()
                return int(row["c"]) if row else 0

        return await asyncio.to_thread(_run)

    async def adjust_balance_admin(self, user_id: int, delta: int, reason: str) -> tuple[bool, str, Optional[dict[str, Any]]]:
        if delta == 0:
            return False, "القيمة يجب أن تكون غير صفر.", None
        clean_reason = reason.strip() or "تعديل إداري للرصيد"
        now = utc_now_str()

        async with self._lock:
            def _run() -> tuple[bool, str, Optional[dict[str, Any]]]:
                with self._connect_sync() as conn:
                    conn.execute("BEGIN IMMEDIATE;")
                    try:
                        user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
                        if not user:
                            conn.rollback()
                            return False, "المستخدم غير موجود.", None
                        cur_bal = int(user["balance"])
                        new_bal = cur_bal + delta
                        if new_bal < 0:
                            conn.rollback()
                            return False, f"الرصيد الحالي ({cur_bal}) لا يكفي لخصم ({abs(delta)}) نقطة.", None

                        if delta > 0:
                            conn.execute(
                                "UPDATE users SET balance = ?, earned_points = earned_points + ? WHERE user_id = ?",
                                (new_bal, delta, user_id),
                            )
                            tx_type = "admin_add"
                        else:
                            conn.execute(
                                "UPDATE users SET balance = ?, spent_points = spent_points + ? WHERE user_id = ?",
                                (new_bal, abs(delta), user_id),
                            )
                            tx_type = "admin_deduct"

                        conn.execute(
                            """
                            INSERT INTO transactions (user_id, tx_type, amount, balance_after, reason, related_order_id, created_at)
                            VALUES (?, ?, ?, ?, ?, NULL, ?)
                            """,
                            (user_id, tx_type, delta, new_bal, clean_reason, now),
                        )
                        conn.commit()
                        updated = dict(conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone())
                        return True, "تم تحديث الرصيد وتسجيل العملية.", updated
                    except Exception:
                        conn.rollback()
                        raise

            return await asyncio.to_thread(_run)

    async def create_section(self, name: str) -> tuple[bool, str]:
        clean = name.strip()
        if len(clean) < 2 or len(clean) > 80:
            return False, "اسم القسم يجب أن يكون بين حرفين و80 حرفاً."
        now = utc_now_str()

        async with self._lock:
            def _run() -> tuple[bool, str]:
                with self._connect_sync() as conn:
                    try:
                        nxt = int(conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 AS m FROM sections").fetchone()["m"])
                        conn.execute(
                            "INSERT INTO sections (name, is_active, sort_order, created_at) VALUES (?, 1, ?, ?)",
                            (clean, nxt, now),
                        )
                        conn.commit()
                        return True, f"تم إنشاء القسم «{clean}»."
                    except sqlite3.IntegrityError:
                        return False, "هذا القسم موجود مسبقاً."

            return await asyncio.to_thread(_run)

    async def get_sections(self, active_only: bool = False) -> list[dict[str, Any]]:
        def _run() -> list[dict[str, Any]]:
            with self._connect_sync() as conn:
                sql = "SELECT * FROM sections WHERE is_active = 1 ORDER BY sort_order ASC, id ASC" if active_only else "SELECT * FROM sections ORDER BY sort_order ASC, id ASC"
                return [dict(r) for r in conn.execute(sql).fetchall()]

        return await asyncio.to_thread(_run)

    async def get_section(self, section_id: int) -> Optional[dict[str, Any]]:
        def _run() -> Optional[dict[str, Any]]:
            with self._connect_sync() as conn:
                row = conn.execute("SELECT * FROM sections WHERE id = ?", (section_id,)).fetchone()
                return dict(row) if row else None

        return await asyncio.to_thread(_run)

    async def update_section_name(self, section_id: int, new_name: str) -> tuple[bool, str]:
        clean = new_name.strip()
        if len(clean) < 2 or len(clean) > 80:
            return False, "الاسم غير صالح."

        async with self._lock:
            def _run() -> tuple[bool, str]:
                with self._connect_sync() as conn:
                    try:
                        cur = conn.execute("UPDATE sections SET name = ? WHERE id = ?", (clean, section_id))
                        conn.commit()
                        return (True, "تم تعديل الاسم.") if cur.rowcount else (False, "القسم غير موجود.")
                    except sqlite3.IntegrityError:
                        return False, "الاسم مستخدم لقسم آخر."

            return await asyncio.to_thread(_run)

    async def toggle_section(self, section_id: int) -> Optional[bool]:
        async with self._lock:
            def _run() -> Optional[bool]:
                with self._connect_sync() as conn:
                    row = conn.execute("SELECT is_active FROM sections WHERE id = ?", (section_id,)).fetchone()
                    if not row:
                        return None
                    new_v = 0 if int(row["is_active"]) == 1 else 1
                    conn.execute("UPDATE sections SET is_active = ? WHERE id = ?", (new_v, section_id))
                    conn.commit()
                    return bool(new_v)

            return await asyncio.to_thread(_run)

    async def delete_section(self, section_id: int) -> bool:
        async with self._lock:
            def _run() -> bool:
                with self._connect_sync() as conn:
                    cur = conn.execute("DELETE FROM sections WHERE id = ?", (section_id,))
                    conn.commit()
                    return cur.rowcount > 0

            return await asyncio.to_thread(_run)

    async def create_product(
        self,
        section_id: int,
        name: str,
        description: str,
        price_points: int,
        delivery_type: str,
        visibility: str,
        photo_file_id: Optional[str] = None,
        seller_id: Optional[int] = None,
        seller_username: Optional[str] = None,
        seller_name: Optional[str] = None,
        payment_method: Optional[str] = None,
        custom_price_text: Optional[str] = None,
    ) -> tuple[bool, str, Optional[dict[str, Any]]]:
        clean_name = name.strip()
        clean_desc = description.strip()
        if len(clean_name) < 2 or (price_points <= 0 and not custom_price_text):
            return False, "بيانات المنتج غير صالحة.", None
        code = f"PM-{uuid.uuid4().hex[:8].upper()}" if visibility == "completely_hidden" else None
        now = utc_now_str()

        async with self._lock:
            def _run() -> tuple[bool, str, Optional[dict[str, Any]]]:
                with self._connect_sync() as conn:
                    if not conn.execute("SELECT id FROM sections WHERE id = ?", (section_id,)).fetchone():
                        return False, "القسم غير موجود.", None
                    cur = conn.execute(
                        """
                        INSERT INTO products (
                            section_id, seller_id, seller_username, seller_name,
                            payment_method, custom_price_text,
                            name, description, photo_file_id, price_points, delivery_type,
                            visibility, special_code, is_active, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                        """,
                        (
                            section_id,
                            seller_id,
                            seller_username,
                            seller_name,
                            payment_method,
                            custom_price_text,
                            clean_name,
                            clean_desc,
                            photo_file_id,
                            max(0, price_points),
                            delivery_type,
                            visibility,
                            code,
                            now,
                        ),
                    )
                    conn.commit()
                    row = dict(conn.execute("SELECT * FROM products WHERE id = ?", (cur.lastrowid,)).fetchone())
                    return True, "تمت إضافة المنتج بنجاح.", row

            return await asyncio.to_thread(_run)

    async def get_product(self, product_id: int) -> Optional[dict[str, Any]]:
        def _run() -> Optional[dict[str, Any]]:
            with self._connect_sync() as conn:
                row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
                return dict(row) if row else None

        return await asyncio.to_thread(_run)

    async def get_product_by_code(self, code: str) -> Optional[dict[str, Any]]:
        def _run() -> Optional[dict[str, Any]]:
            with self._connect_sync() as conn:
                row = conn.execute("SELECT * FROM products WHERE UPPER(special_code) = ?", (code.strip().upper(),)).fetchone()
                return dict(row) if row else None

        return await asyncio.to_thread(_run)

    async def get_storefront_products(self, section_id: int) -> list[dict[str, Any]]:
        def _run() -> list[dict[str, Any]]:
            with self._connect_sync() as conn:
                rows = conn.execute(
                    """
                    SELECT p.* FROM products p
                    WHERE p.section_id = ?
                      AND p.is_active = 1
                      AND p.visibility IN ('public', 'hidden_details')
                    ORDER BY p.id DESC
                    """,
                    (section_id,),
                ).fetchall()
                return [dict(r) for r in rows]

        return await asyncio.to_thread(_run)

    async def get_all_products(self) -> list[dict[str, Any]]:
        def _run() -> list[dict[str, Any]]:
            with self._connect_sync() as conn:
                rows = conn.execute(
                    """
                    SELECT p.*, s.name AS section_name
                    FROM products p JOIN sections s ON p.section_id = s.id
                    ORDER BY p.id DESC
                    """
                ).fetchall()
                return [dict(r) for r in rows]

        return await asyncio.to_thread(_run)

    async def update_product(self, product_id: int, field: str, value: Any) -> tuple[bool, str]:
        if field not in {"name", "description", "photo_file_id", "price_points", "custom_price_text", "payment_method", "visibility", "is_active"}:
            return False, "حقل غير مسموح."

        async with self._lock:
            def _run() -> tuple[bool, str]:
                with self._connect_sync() as conn:
                    prod = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
                    if not prod:
                        return False, "المنتج غير موجود."
                    extra = ""
                    params: list[Any] = [value]
                    if field == "visibility":
                        if value == "completely_hidden":
                            extra = ", special_code = ?"
                            params.append(prod["special_code"] or f"PM-{uuid.uuid4().hex[:8].upper()}")
                        else:
                            extra = ", special_code = NULL"
                    params.append(product_id)
                    conn.execute(f"UPDATE products SET {field} = ?{extra} WHERE id = ?", tuple(params))
                    conn.commit()
                    return True, "تم التحديث بنجاح."

            return await asyncio.to_thread(_run)

    async def delete_product(self, product_id: int) -> bool:
        async with self._lock:
            def _run() -> bool:
                with self._connect_sync() as conn:
                    conn.execute("DELETE FROM inventory WHERE product_id = ?", (product_id,))
                    cur = conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
                    conn.commit()
                    return cur.rowcount > 0

            return await asyncio.to_thread(_run)

    async def add_inventory(self, product_id: int, items: list[str]) -> tuple[bool, str, int]:
        cleaned = [x for x in items if x.strip()]
        if not cleaned:
            return False, "لا توجد عناصر صالحة.", 0
        now = utc_now_str()

        async with self._lock:
            def _run() -> tuple[bool, str, int]:
                with self._connect_sync() as conn:
                    if not conn.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone():
                        return False, "المنتج غير موجود.", 0
                    for item in cleaned:
                        conn.execute(
                            "INSERT INTO inventory (product_id, delivery_data, is_sold, created_at) VALUES (?, ?, 0, ?)",
                            (product_id, item, now),
                        )
                    conn.commit()
                    return True, f"تمت إضافة {len(cleaned)} عنصر تسليم كامل.", len(cleaned)

            return await asyncio.to_thread(_run)

    async def get_inventory_counts(self, product_id: int) -> dict[str, int]:
        def _run() -> dict[str, int]:
            with self._connect_sync() as conn:
                av = int(conn.execute("SELECT COUNT(*) AS c FROM inventory WHERE product_id = ? AND is_sold = 0", (product_id,)).fetchone()["c"])
                so = int(conn.execute("SELECT COUNT(*) AS c FROM inventory WHERE product_id = ? AND is_sold = 1", (product_id,)).fetchone()["c"])
                return {"available": av, "sold": so}

        return await asyncio.to_thread(_run)

    async def create_purchase(self, user_id: int, product_id: int, allow_hidden: bool = False) -> dict[str, Any]:
        now = utc_now_str()
        order_uid = f"ORD-{uuid.uuid4().hex[:10].upper()}"

        async with self._lock:
            def _run() -> dict[str, Any]:
                with self._connect_sync() as conn:
                    conn.execute("BEGIN IMMEDIATE;")
                    try:
                        st = conn.execute("SELECT value FROM settings WHERE key = 'store_enabled'").fetchone()
                        if not st or str(st["value"]) != "1":
                            conn.rollback()
                            return {"ok": False, "error": "المتجر متوقف مؤقتاً."}

                        user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
                        if not user or int(user["is_banned"]) == 1:
                            conn.rollback()
                            return {"ok": False, "error": "الحساب غير متاح."}

                        prod = conn.execute(
                            """
                            SELECT p.*, s.is_active AS sec_active, s.name AS sec_name
                            FROM products p JOIN sections s ON p.section_id = s.id
                            WHERE p.id = ?
                            """,
                            (product_id,),
                        ).fetchone()
                        if not prod or int(prod["is_active"]) != 1 or int(prod["sec_active"]) != 1:
                            conn.rollback()
                            return {"ok": False, "error": "هذا المنتج تم شراؤه وحذفه من المتجر مسبقاً."}

                        if prod["seller_id"] is not None or prod["custom_price_text"]:
                            conn.rollback()
                            return {
                                "ok": False,
                                "error": "هذا عرض مباشر من عضو؛ تواصل مع البائع مباشرة لإتمام الدفع والشراء.",
                            }

                        if prod["visibility"] == "completely_hidden" and not allow_hidden:
                            conn.rollback()
                            return {"ok": False, "error": "يتطلب رمزاً خاصاً للشراء."}

                        price = int(prod["price_points"])
                        bal = int(user["balance"])
                        if bal < price:
                            conn.rollback()
                            return {"ok": False, "error": f"رصيدك غير كافٍ ({bal} / {price} نقطة)."}

                        prod_name = str(prod["name"])
                        delivery_content: Optional[str] = None
                        status = "pending"

                        if prod["delivery_type"] == "auto":
                            inv_rows = conn.execute(
                                "SELECT id, delivery_data FROM inventory WHERE product_id = ? AND is_sold = 0 ORDER BY id ASC",
                                (product_id,),
                            ).fetchall()
                            if not inv_rows:
                                conn.execute("DELETE FROM inventory WHERE product_id = ?", (product_id,))
                                conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
                                conn.commit()
                                return {"ok": False, "error": "تم شراء هذا المنتج مسبقاً وحذفه من المتجر."}
                            delivery_content = "\n\n".join(str(r["delivery_data"]) for r in inv_rows)
                            status = "completed"

                        new_bal = bal - price
                        conn.execute(
                            "UPDATE users SET balance = ?, spent_points = spent_points + ? WHERE user_id = ? AND balance >= ?",
                            (new_bal, price, user_id, price),
                        )

                        ord_cur = conn.execute(
                            """
                            INSERT INTO orders (
                                order_uid, user_id, product_id, product_name, price_points, delivery_type,
                                delivery_content, status, admin_note, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                            """,
                            (
                                order_uid,
                                user_id,
                                product_id,
                                prod_name,
                                price,
                                str(prod["delivery_type"]),
                                delivery_content,
                                status,
                                now,
                                now,
                            ),
                        )
                        order_id = int(ord_cur.lastrowid)

                        if prod["delivery_type"] == "auto":
                            conn.execute("DELETE FROM inventory WHERE product_id = ?", (product_id,))
                            conn.execute("DELETE FROM products WHERE id = ?", (product_id,))

                        conn.execute(
                            """
                            INSERT INTO transactions (user_id, tx_type, amount, balance_after, reason, related_order_id, created_at)
                            VALUES (?, 'purchase', ?, ?, ?, ?, ?)
                            """,
                            (user_id, -price, new_bal, f"شراء وحذف نهائي: {prod_name}", order_id, now),
                        )
                        conn.commit()
                        return {
                            "ok": True,
                            "order_id": order_id,
                            "order_uid": order_uid,
                            "product_name": prod_name,
                            "price_points": price,
                            "delivery_type": str(prod["delivery_type"]),
                            "delivery_content": delivery_content,
                            "new_balance": new_bal,
                        }
                    except Exception:
                        conn.rollback()
                        raise

            return await asyncio.to_thread(_run)

    async def approve_order(self, order_id: int, content: str) -> tuple[bool, str, Optional[dict[str, Any]]]:
        clean = content.strip("\r\n")
        if not clean.strip():
            return False, "محتوى التسليم مطلوب.", None
        now = utc_now_str()

        async with self._lock:
            def _run() -> tuple[bool, str, Optional[dict[str, Any]]]:
                with self._connect_sync() as conn:
                    conn.execute("BEGIN IMMEDIATE;")
                    try:
                        order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
                        if not order:
                            conn.rollback()
                            return False, "الطلب غير موجود.", None
                        if str(order["status"]) != "pending":
                            conn.rollback()
                            return False, "تمت معالجة هذا الطلب مسبقاً.", None
                        conn.execute(
                            "UPDATE orders SET status = 'completed', delivery_content = ?, updated_at = ? WHERE id = ? AND status = 'pending'",
                            (clean, now, order_id),
                        )
                        if order["product_id"] is not None:
                            pid = int(order["product_id"])
                            conn.execute("DELETE FROM inventory WHERE product_id = ?", (pid,))
                            conn.execute("DELETE FROM products WHERE id = ?", (pid,))
                        conn.commit()
                        return True, "تم قبول الطلب وتسليمه وحذف المنتج من المتجر.", dict(order)
                    except Exception:
                        conn.rollback()
                        raise

            return await asyncio.to_thread(_run)

    async def reject_order(self, order_id: int, reason: str) -> tuple[bool, str, Optional[dict[str, Any]]]:
        clean_reason = reason.strip() or "مرفوض من الإدارة"
        now = utc_now_str()

        async with self._lock:
            def _run() -> tuple[bool, str, Optional[dict[str, Any]]]:
                with self._connect_sync() as conn:
                    conn.execute("BEGIN IMMEDIATE;")
                    try:
                        order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
                        if not order:
                            conn.rollback()
                            return False, "الطلب غير موجود.", None
                        if str(order["status"]) != "pending":
                            conn.rollback()
                            return False, "تمت معالجة هذا الطلب مسبقاً.", None

                        conn.execute(
                            "UPDATE orders SET status = 'rejected', admin_note = ?, updated_at = ? WHERE id = ? AND status = 'pending'",
                            (clean_reason, now, order_id),
                        )
                        amt = int(order["price_points"])
                        uid = int(order["user_id"])
                        if amt > 0:
                            conn.execute(
                                "UPDATE users SET balance = balance + ?, spent_points = MAX(0, spent_points - ?) WHERE user_id = ?",
                                (amt, amt, uid),
                            )
                            new_bal = int(conn.execute("SELECT balance FROM users WHERE user_id = ?", (uid,)).fetchone()["balance"])
                            conn.execute(
                                """
                                INSERT INTO transactions (user_id, tx_type, amount, balance_after, reason, related_order_id, created_at)
                                VALUES (?, 'refund', ?, ?, ?, ?, ?)
                                """,
                                (uid, amt, new_bal, f"إرجاع نقاط طلب مرفوض ({order['order_uid']}): {clean_reason}", order_id, now),
                            )
                        conn.commit()
                        return True, "تم رفض الطلب وإعادة النقاط.", dict(order)
                    except Exception:
                        conn.rollback()
                        raise

            return await asyncio.to_thread(_run)

    async def get_user_orders(self, user_id: int, limit: int = 12) -> list[dict[str, Any]]:
        def _run() -> list[dict[str, Any]]:
            with self._connect_sync() as conn:
                rows = conn.execute(
                    "SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
                return [dict(r) for r in rows]

        return await asyncio.to_thread(_run)

    async def get_pending_orders(self, limit: int = 15) -> list[dict[str, Any]]:
        def _run() -> list[dict[str, Any]]:
            with self._connect_sync() as conn:
                rows = conn.execute(
                    """
                    SELECT o.*, u.full_name
                    FROM orders o JOIN users u ON o.user_id = u.user_id
                    WHERE o.status = 'pending' ORDER BY o.id ASC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                return [dict(r) for r in rows]

        return await asyncio.to_thread(_run)

    async def get_stats(self) -> dict[str, int]:
        def _run() -> dict[str, int]:
            with self._connect_sync() as conn:
                users_c = int(conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"])
                refs_c = int(conn.execute("SELECT COUNT(*) AS c FROM referrals WHERE status = 'rewarded'").fetchone()["c"])
                secs_c = int(conn.execute("SELECT COUNT(*) AS c FROM sections").fetchone()["c"])
                prods_c = int(conn.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"])
                inv_c = int(conn.execute("SELECT COUNT(*) AS c FROM inventory WHERE is_sold = 0").fetchone()["c"])
                comp_row = conn.execute("SELECT COUNT(*) AS c, COALESCE(SUM(price_points), 0) AS s FROM orders WHERE status = 'completed'").fetchone()
                pend_c = int(conn.execute("SELECT COUNT(*) AS c FROM orders WHERE status = 'pending'").fetchone()["c"])
                promos_c = int(conn.execute("SELECT COUNT(*) AS c FROM channel_promotions WHERE is_active = 1").fetchone()["c"])
                return {
                    "users": users_c,
                    "referrals": refs_c,
                    "sections": secs_c,
                    "products": prods_c,
                    "inventory": inv_c,
                    "completed_orders": int(comp_row["c"]),
                    "sales_points": int(comp_row["s"]),
                    "pending_orders": pend_c,
                    "active_promos": promos_c,
                }

        return await asyncio.to_thread(_run)


# ================= التحقق من الاشتراك الإجباري =================
async def _is_member_of_chat(bot: Bot, chat_target: str, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=chat_target, user_id=user_id)
        return member.status in {
            ChatMemberStatus.CREATOR,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.RESTRICTED,
        }
    except Exception:
        return False


async def check_forced_subscriptions(bot: Bot, db: Database, user_id: int) -> list[dict[str, Any]]:
    if user_id == OWNER_ID:
        return []
    channels = await db.get_forced_channels(active_only=True)
    missing: list[dict[str, Any]] = []
    for ch in channels:
        ok = await _is_member_of_chat(bot, str(ch["chat_target"]), user_id)
        if not ok:
            missing.append(ch)
    return missing


def build_forced_sub_kb(missing_channels: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for ch in missing_channels:
        rows.append([InlineKeyboardButton(text=f"📢 اشترك في: {ch['title']}", url=str(ch["join_url"]))])
    rows.append([InlineKeyboardButton(text="✅ تحقق من الاشتراك", callback_data="u_check_fsub")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ================= الحالات والأزرار =================
class UserStates(StatesGroup):
    special_code = State()
    redeem_promo = State()
    sell_name = State()
    sell_desc = State()
    sell_photo = State()
    sell_custom_method = State()
    sell_price = State()
    promo_ch_link = State()
    promo_ch_members = State()


class AdminStates(StatesGroup):
    add_sec = State()
    ren_sec = State()
    np_name = State()
    np_desc = State()
    np_photo = State()
    np_price = State()
    np_stock = State()
    edit_prod = State()
    add_stock = State()
    ord_acc = State()
    ord_rej = State()
    bal_uid = State()
    bal_amt = State()
    bal_rsn = State()
    set_ref = State()
    set_listing_fee = State()
    set_promo_cost = State()
    set_promo_reward = State()
    set_support = State()
    set_buy_user = State()
    set_point_price = State()
    promo_points = State()
    promo_users = State()
    promo_code = State()
    fsub_title = State()
    fsub_target = State()
    fsub_url = State()
    search_uid = State()
    broadcast = State()


async def admin_kb(db: Database) -> InlineKeyboardMarkup:
    enabled = await db.is_store_enabled()
    fee = await db.get_listing_fee()
    p_cost = await db.get_promo_cost_per_member()
    p_rew = await db.get_promo_reward_per_join()
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ إضافة قسم", callback_data="a_sec_add"),
                InlineKeyboardButton(text="📂 إدارة الأقسام", callback_data="a_sec_mng"),
            ],
            [
                InlineKeyboardButton(text="🆕 إضافة منتج", callback_data="a_pr_add"),
                InlineKeyboardButton(text="✏️ تعديل/حذف المنتجات", callback_data="a_pr_list"),
            ],
            [
                InlineKeyboardButton(text="📦 إدارة المخزون", callback_data="a_inv"),
                InlineKeyboardButton(text="📥 الطلبات المعلقة", callback_data="a_ord_p"),
            ],
            [
                InlineKeyboardButton(text="➕ إضافة نقاط", callback_data="a_bal:add"),
                InlineKeyboardButton(text="➖ خصم نقاط", callback_data="a_bal:sub"),
            ],
            [
                InlineKeyboardButton(text="🎟️ أكواد النقاط", callback_data="a_promo"),
                InlineKeyboardButton(text="💱 أسعار النقاط", callback_data="a_prices"),
            ],
            [
                InlineKeyboardButton(text="🔒 الاشتراك الإجباري", callback_data="a_fsub"),
                InlineKeyboardButton(text=f"📢 رسوم النشر ({fee} ن)", callback_data="a_lfee"),
            ],
            [
                InlineKeyboardButton(text=f"💸 سعر التمويل ({p_cost} ن)", callback_data="a_pcost"),
                InlineKeyboardButton(text=f"🎁 مكافأة الانضمام ({p_rew} ن)", callback_data="a_prew"),
            ],
            [
                InlineKeyboardButton(text="🎁 مكافأة الإحالة", callback_data="a_ref"),
                InlineKeyboardButton(text="📞 إدارة الدعم الفني", callback_data="a_sup"),
            ],
            [
                InlineKeyboardButton(text="👤 يوزرات الشراء", callback_data="a_buy_users"),
                InlineKeyboardButton(text="👥 المستخدمون", callback_data="a_users:0"),
            ],
            [
                InlineKeyboardButton(text="🔍 بحث ID", callback_data="a_find"),
                InlineKeyboardButton(text="📣 إشعار جماعي", callback_data="a_bcast"),
            ],
            [
                InlineKeyboardButton(text="📈 الإحصائيات", callback_data="a_stats"),
                InlineKeyboardButton(text="⏸️ تعطيل المتجر" if enabled else "▶️ تفعيل المتجر", callback_data="a_tog_st"),
            ],
            [
                InlineKeyboardButton(text="🏠 واجهة المستخدم", callback_data="u_home"),
            ],
        ]
    )


async def buy_users_menu_kb(db: Database) -> InlineKeyboardMarkup:
    fallback = await db.get_setting("support_contact", DEFAULT_SUPPORT_CONTACT)
    contacts = await db.get_buy_button_contacts(fallback)
    rows: list[list[InlineKeyboardButton]] = []
    for setting_key, label in BUY_USER_LABELS.items():
        cur_u = contacts.get(setting_key, fallback)
        rows.append([InlineKeyboardButton(text=f"{label}: {cur_u}", callback_data=f"a_bu_set:{setting_key}")])
    rows.append([admin_home_btn()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def prices_menu_kb(db: Database) -> InlineKeyboardMarkup:
    prices = await db.get_point_prices()
    rows = [
        [
            InlineKeyboardButton(
                text=f"{label}: {prices.get(key, DEFAULT_POINT_PRICES[key])}",
                callback_data=f"a_pr_set:{key}",
            )
        ]
        for key, (label, _) in POINT_PRICE_LABELS.items()
    ]
    rows.append([admin_home_btn()])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def promo_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ إنشاء كود نقاط", callback_data="a_prm_add")],
            [InlineKeyboardButton(text="📋 عرض/إدارة الأكواد", callback_data="a_prm_list")],
            [admin_home_btn()],
        ]
    )


router = Router()


async def _build_home_message(db: Database, user: dict[str, Any]) -> str:
    sup = await db.get_setting("support_contact", DEFAULT_SUPPORT_CONTACT)
    prices = await db.get_point_prices()
    buy_section = build_buy_points_text(sup, prices)
    return (
        f"أهلاً بك في <b>POINT MARKET | متجر النقاط</b> 🛍️\n\n"
        f"👤 الاسم: <b>{user['full_name']}</b>\n"
        f"💰 رصيدك الحالي: <b>{user['balance']}</b> نقطة\n\n"
        f"{buy_section}"
    )


def _format_product_price_and_method(p: dict[str, Any]) -> tuple[str, str]:
    method_str = str(p.get("payment_method") or "").strip()
    custom_price = str(p.get("custom_price_text") or "").strip()
    if custom_price:
        price_display = f"<b>{custom_price}</b>"
    else:
        price_display = f"<b>{p['price_points']}</b> نقطة"
    method_display = f"<b>{method_str}</b>" if method_str else "<b>نقاط البوت</b>"
    return price_display, method_display


async def _guard_forced_sub_message(message: Message, db: Database) -> bool:
    if not message.from_user or not message.bot:
        return True
    missing = await check_forced_subscriptions(message.bot, db, message.from_user.id)
    if not missing:
        return True
    await message.answer(
        "🔒 <b>الاشتراك الإجباري</b>\n"
        "للاستمرار واستخدام البوت، يرجى الاشتراك أولاً في القنوات/المجموعات التالية ثم الضغط على زر التحقق:",
        reply_markup=build_forced_sub_kb(missing),
    )
    return False


async def _guard_forced_sub_callback(cb: CallbackQuery, db: Database) -> bool:
    if not cb.from_user or not cb.bot:
        return True
    if cb.data == "u_check_fsub":
        return True
    missing = await check_forced_subscriptions(cb.bot, db, cb.from_user.id)
    if not missing:
        return True
    try:
        await cb.message.answer(
            "🔒 <b>الاشتراك الإجباري</b>\n"
            "يرجى الاشتراك أولاً في القنوات/المجموعات المطلوبة ثم الضغط على <b>تحقق من الاشتراك</b>:",
            reply_markup=build_forced_sub_kb(missing),
        )
    except Exception:
        pass
    await cb.answer("يجب الاشتراك في القنوات المطلوبة أولاً.", show_alert=True)
    return False


@router.callback_query(F.data == "u_check_fsub")
async def cb_u_check_fsub(cb: CallbackQuery, db: Database) -> None:
    if not cb.from_user or not cb.bot:
        return
    missing = await check_forced_subscriptions(cb.bot, db, cb.from_user.id)
    if missing:
        await cb.answer("لم يتم رصد اشتراكك في جميع القنوات بعد!", show_alert=True)
        try:
            await cb.message.edit_reply_markup(reply_markup=build_forced_sub_kb(missing))
        except Exception:
            pass
        return
    u = await db.get_user(cb.from_user.id)
    if not u:
        reg = await db.register_or_update_user(cb.from_user.id, cb.from_user.username, cb.from_user.full_name)
        u = reg["user"]
    await cb.answer("تم التحقق من اشتراكك بنجاح!")
    home_text = await _build_home_message(db, u)
    await cb.message.answer(home_text, reply_markup=ReplyKeyboardRemove())
    await cb.message.answer(WELCOME_TRUST_TEXT, reply_markup=user_screen_kb(cb.from_user.id))


@router.message(CommandStart())
async def on_start(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    if not message.from_user:
        return

    ref_id: Optional[int] = None
    special_code: Optional[str] = None
    parts = (message.text or "").strip().split(maxsplit=1)
    if len(parts) == 2:
        payload = parts[1].strip()
        if payload.startswith("ref_") and payload[4:].isdigit():
            ref_id = int(payload[4:])
        elif payload.upper().startswith("PM-"):
            special_code = payload.upper()

    reg = await db.register_or_update_user(message.from_user.id, message.from_user.username, message.from_user.full_name, ref_id)
    user = reg["user"]
    if int(user["is_banned"]) == 1:
        await message.answer("⛔ حسابك محظور.", reply_markup=ReplyKeyboardRemove())
        return

    if not await _guard_forced_sub_message(message, db):
        return

    if reg["referral"]["rewarded"] and message.bot:
        try:
            await message.bot.send_message(
                int(reg["referral"]["referrer_id"]),
                f"🎉 انضم صديق جديد عبر رابطك: <b>{message.from_user.full_name}</b>\n💎 حصلت على <b>{reg['referral']['reward_points']}</b> نقطة.",
            )
        except Exception:
            pass

    home_text = await _build_home_message(db, user)
    await message.answer(home_text, reply_markup=ReplyKeyboardRemove())
    await message.answer(WELCOME_TRUST_TEXT, reply_markup=user_screen_kb(message.from_user.id))

    if special_code:
        await _send_special_product(message, db, special_code)


@router.callback_query(F.data == "u_home")
async def cb_u_home(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    if not await _guard_forced_sub_callback(cb, db):
        return
    await state.clear()
    if not cb.from_user:
        return
    u = await db.get_user(cb.from_user.id)
    if not u:
        return
    home_text = await _build_home_message(db, u)
    try:
        await cb.message.edit_text(home_text, reply_markup=user_screen_kb(cb.from_user.id))
    except Exception:
        await cb.message.answer(home_text, reply_markup=user_screen_kb(cb.from_user.id))
    await cb.answer()


# ================= تمويل القنوات وقنوات التمويل =================
@router.callback_query(F.data == "u_promo_add")
async def cb_u_promo_add(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    if not await _guard_forced_sub_callback(cb, db):
        return
    if not cb.from_user:
        return
    await db.register_or_update_user(cb.from_user.id, cb.from_user.username, cb.from_user.full_name)
    u = await db.get_user(cb.from_user.id)
    cost_per = await db.get_promo_cost_per_member()
    rew_per = await db.get_promo_reward_per_join()
    bal = int(u["balance"]) if u else 0

    await state.clear()
    await state.set_state(UserStates.promo_ch_link)
    await cb.message.answer(
        f"📣 <b>تمويل قناة بالنقاط</b>\n"
        f"• تكلفة كل عضو: <b>{cost_per}</b> نقطة\n"
        f"• مكافأة المنضم للقناة: <b>{rew_per}</b> نقطة\n"
        f"• رصيدك الحالي: <b>{bal}</b> نقطة\n\n"
        f"⚠️ <i>يجب أن يكون البوت مشرفاً في القناة لكي يتحقق من انضمام الأعضاء تلقائياً.</i>\n\n"
        f"1️⃣ <b>أرسل رابط القناة أو <code>@username</code>:</b>",
        reply_markup=user_cancel_home_kb(),
    )
    await cb.answer()


@router.message(UserStates.promo_ch_link)
async def msg_u_promo_ch_link(message: Message, state: FSMContext) -> None:
    if not message.bot:
        return
    api_target, join_url, label = parse_channel_target(message.text or "")
    if not api_target and not join_url:
        await message.answer(
            "⚠️ أرسل رابط قناة صحيح أو <code>@username</code> (مثال: <code>@mychannel</code> أو <code>https://t.me/mychannel</code>):",
            reply_markup=user_cancel_home_kb(),
        )
        return

    if not api_target:
        await message.answer(
            "⚠️ لكي يتمكن البوت من التحقق التلقائي من انضمام الأعضاء، أرسل <code>@username</code> أو رابط قناة عامة (أو ارفع البوت مشرفاً وأرسل معرّف القناة).",
            reply_markup=user_cancel_home_kb(),
        )
        return

    try:
        chat_obj = await message.bot.get_chat(api_target)
        me = await message.bot.get_me()
        bot_member = await message.bot.get_chat_member(chat_id=chat_obj.id, user_id=me.id)
        if bot_member.status not in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR}:
            await message.answer(
                "⚠️ البوت ليس مشرفاً في هذه القناة بعد! ارفع البوت كمشرف في القناة ثم أرسل الرابط مرة أخرى:",
                reply_markup=user_cancel_home_kb(),
            )
            return
    except Exception:
        await message.answer(
            "⚠️ تعذر الوصول إلى القناة أو التحقق من صلاحيات البوت فيها. تأكد أن البوت مشرف في القناة ثم أعد المحاولة:",
            reply_markup=user_cancel_home_kb(),
        )
        return

    final_url = join_url or (f"https://t.me/{chat_obj.username}" if chat_obj.username else (label or ""))
    if not final_url:
        await message.answer(
            "⚠️ يرجى إرسال رابط دعوة مباشر للقناة (مثل <code>https://t.me/...</code>):",
            reply_markup=user_cancel_home_kb(),
        )
        return

    await state.update_data(
        promo_chat_id=int(chat_obj.id),
        promo_title=str(chat_obj.title or label or "قناة"),
        promo_join_url=final_url,
    )
    await state.set_state(UserStates.promo_ch_members)
    await message.answer(
        f"✅ تم التحقق من القناة: <b>{chat_obj.title or label}</b>\n\n"
        f"2️⃣ <b>أدخل عدد الأعضاء المطلوب تمويلهم</b> (مثلاً: <code>100</code>):",
        reply_markup=user_cancel_home_kb(),
    )


@router.message(UserStates.promo_ch_members)
async def msg_u_promo_ch_members(message: Message, state: FSMContext, db: Database) -> None:
    if not message.from_user:
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("⚠️ أدخل عدداً صحيحاً للأعضاء أكبر من 0 (مثلاً: <code>100</code>):", reply_markup=user_cancel_home_kb())
        return

    target_members = int(raw)
    data = await state.get_data()
    await db.register_or_update_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    res = await db.create_channel_promotion(
        owner_user_id=message.from_user.id,
        chat_id=int(data["promo_chat_id"]),
        title=str(data["promo_title"]),
        join_url=str(data["promo_join_url"]),
        target_members=target_members,
    )
    await state.clear()

    if not res.get("ok"):
        await message.answer(
            f"⚠️ {res.get('error', 'تعذر إنشاء التمويل.')}",
            reply_markup=user_screen_kb(message.from_user.id),
        )
        return

    pr = res["promo"]
    await message.answer(
        f"✅ <b>تم إطلاق تمويل القناة بنجاح!</b>\n\n"
        f"📢 القناة: <b>{pr['title']}</b>\n"
        f"👥 العدد المطلوب: <b>{pr['target_members']}</b> عضو\n"
        f"🎁 مكافأة كل منضم: <b>{pr['reward_per_join']}</b> نقطة\n"
        f"💸 المخصوم من رصيدك: <b>{res['total_cost']}</b> نقطة\n"
        f"💰 رصيدك المتبقي: <b>{res['new_balance']}</b> نقطة",
        reply_markup=user_screen_kb(message.from_user.id),
    )


@router.callback_query(F.data.startswith("u_promo_list:"))
async def cb_u_promo_list(cb: CallbackQuery, db: Database) -> None:
    if not await _guard_forced_sub_callback(cb, db):
        return
    page = int(cb.data.split(":")[1])
    promos, total = await db.get_active_channel_promotions(limit=8, offset=page * 8)

    rows: list[list[InlineKeyboardButton]] = []
    if not promos:
        txt = "📺 <b>قنوات التمويل</b>\n\nلا توجد قنوات ممولة متاحة حالياً."
    else:
        lines = ["📺 <b>قنوات التمويل المتاحة</b>\nاختر قناة للانضمام والحصول على النقاط:"]
        for pr in promos:
            rem = int(pr["target_members"]) - int(pr["completed_members"])
            lines.append(
                f"• <b>{pr['title']}</b> | 🎁 <b>+{pr['reward_per_join']}</b> ن | متبقي: <b>{rem}</b> عضو"
            )
            rows.append([InlineKeyboardButton(text=f"📺 {pr['title']} (+{pr['reward_per_join']} ن)", callback_data=f"u_promo_view:{pr['id']}")])
        txt = "\n".join(lines)

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ السابق", callback_data=f"u_promo_list:{page - 1}"))
    if (page + 1) * 8 < total:
        nav.append(InlineKeyboardButton(text="التالي ➡️", callback_data=f"u_promo_list:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="📣 تمويل قناتي", callback_data="u_promo_add")])
    rows.append([InlineKeyboardButton(text="🏠 القائمة الرئيسية", callback_data="u_home")])

    try:
        await cb.message.edit_text(txt, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except Exception:
        await cb.message.answer(txt, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()


@router.callback_query(F.data.startswith("u_promo_view:"))
async def cb_u_promo_view(cb: CallbackQuery, db: Database) -> None:
    if not await _guard_forced_sub_callback(cb, db):
        return
    promo_id = int(cb.data.split(":")[1])
    pr = await db.get_channel_promotion(promo_id)
    if not pr or int(pr["is_active"]) != 1 or int(pr["completed_members"]) >= int(pr["target_members"]):
        await cb.answer("هذا التمويل انتهى أو لم يعد متاحاً.", show_alert=True)
        return

    rem = int(pr["target_members"]) - int(pr["completed_members"])
    txt = (
        f"📺 <b>تمويل قناة: {pr['title']}</b>\n\n"
        f"🎁 المكافأة عند الانضمام: <b>+{pr['reward_per_join']}</b> نقطة\n"
        f"👥 المنضمون: <b>{pr['completed_members']} / {pr['target_members']}</b> (متبقي: <b>{rem}</b>)\n\n"
        f"1️⃣ اضغط على <b>انضمام للقناة</b>.\n"
        f"2️⃣ ثم اضغط على <b>تحقق واستلم النقاط</b>."
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 انضمام للقناة", url=str(pr["join_url"]))],
            [InlineKeyboardButton(text="✅ تحقق واستلم النقاط", callback_data=f"u_promo_claim:{promo_id}")],
            [InlineKeyboardButton(text="⬅️ رجوع لقنوات التمويل", callback_data="u_promo_list:0")],
            [InlineKeyboardButton(text="🏠 القائمة الرئيسية", callback_data="u_home")],
        ]
    )
    try:
        await cb.message.edit_text(txt, reply_markup=kb)
    except Exception:
        await cb.message.answer(txt, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("u_promo_claim:"))
async def cb_u_promo_claim(cb: CallbackQuery, db: Database) -> None:
    if not await _guard_forced_sub_callback(cb, db):
        return
    if not cb.from_user or not cb.bot:
        return
    promo_id = int(cb.data.split(":")[1])
    pr = await db.get_channel_promotion(promo_id)
    if not pr:
        await cb.answer("التمويل غير موجود.", show_alert=True)
        return

    await db.register_or_update_user(cb.from_user.id, cb.from_user.username, cb.from_user.full_name)
    joined = await _is_member_of_chat(cb.bot, str(pr["chat_id"]), cb.from_user.id)
    if not joined:
        await cb.answer("لم يتم تأكيد انضمامك للقناة بعد! انضم أولاً ثم اضغط تحقق.", show_alert=True)
        return

    res = await db.claim_channel_promotion(promo_id, cb.from_user.id)
    if not res.get("ok"):
        await cb.answer(res.get("error", "تعذر استلام النقاط."), show_alert=True)
        return

    await cb.answer(f"مبروك! حصلت على +{res['reward']} نقطة.", show_alert=True)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📺 متابعة قنوات التمويل", callback_data="u_promo_list:0")],
            [InlineKeyboardButton(text="🏠 القائمة الرئيسية", callback_data="u_home")],
        ]
    )
    done_txt = (
        f"✅ <b>تم استلام النقاط بنجاح!</b>\n\n"
        f"📢 القناة: <b>{res['title']}</b>\n"
        f"💎 المكافأة: <b>+{res['reward']}</b> نقطة\n"
        f"💰 رصيدك الحالي: <b>{res['new_balance']}</b> نقطة"
    )
    try:
        await cb.message.edit_text(done_txt, reply_markup=kb)
    except Exception:
        await cb.message.answer(done_txt, reply_markup=kb)


# ================= عرض شيء للبيع وحذفه من طرف المستخدم =================
@router.callback_query(F.data == "u_sell_start")
async def cb_u_sell_start(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    if not await _guard_forced_sub_callback(cb, db):
        return
    if not cb.from_user:
        return
    if not await db.is_store_enabled():
        await cb.answer("🛠️ المتجر متوقف مؤقتاً للصيانة.", show_alert=True)
        return

    await db.register_or_update_user(cb.from_user.id, cb.from_user.username, cb.from_user.full_name)
    user = await db.get_user(cb.from_user.id)
    fee = await db.get_listing_fee()
    bal = int(user["balance"]) if user else 0

    if bal < fee:
        await cb.answer(f"تحتاج إلى {fee} نقطة لعرض شيء للبيع (رصيدك: {bal} نقطة).", show_alert=True)
        return

    await state.clear()
    await state.set_state(UserStates.sell_name)
    await cb.message.answer(
        f"📢 <b>عرض شيء للبيع في المتجر</b>\n"
        f"💎 رسوم النشر: <b>{fee}</b> نقطة (رصيدك الحالي: <b>{bal}</b> نقطة)\n\n"
        f"1️⃣ <b>أدخل اسم الشيء الذي تريد بيعه:</b>",
        reply_markup=user_cancel_home_kb(),
    )
    await cb.answer()


@router.message(UserStates.sell_name)
async def msg_u_sell_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if len(name) < 2 or len(name) > 100:
        await message.answer("⚠️ أرسل اسماً واضحاً للشيء (من حرفين إلى 100 حرف):", reply_markup=user_cancel_home_kb())
        return
    await state.update_data(sell_name=name)
    await state.set_state(UserStates.sell_desc)
    await message.answer(
        "2️⃣ <b>أبعث وصفاً للشيء الذي تريد بيعه:</b>",
        reply_markup=user_cancel_home_kb(),
    )


@router.message(UserStates.sell_desc)
async def msg_u_sell_desc(message: Message, state: FSMContext) -> None:
    desc = (message.text or "").strip()
    if not desc:
        await message.answer("⚠️ أرسل وصفاً للشيء أولاً:", reply_markup=user_cancel_home_kb())
        return
    await state.update_data(sell_desc=desc)
    await state.set_state(UserStates.sell_photo)
    await message.answer(
        "3️⃣ <b>أرسل صورة الشيء، أو اكتب <code>تخطي</code>:</b>",
        reply_markup=user_cancel_home_kb(),
    )


@router.message(UserStates.sell_photo)
async def msg_u_sell_photo(message: Message, state: FSMContext) -> None:
    photo_id: Optional[str] = None
    if message.photo:
        photo_id = message.photo[-1].file_id
    else:
        txt = (message.text or "").strip().lower()
        if txt not in {"تخطي", "/skip", "skip"}:
            await message.answer(
                "⚠️ أرسل صورة للشيء أو اكتب <code>تخطي</code>:",
                reply_markup=user_cancel_home_kb(),
            )
            return

    await state.update_data(sell_photo=photo_id)
    await message.answer(
        "4️⃣ <b>اختر طريقة البيع التي تفضلها:</b>",
        reply_markup=seller_payment_methods_kb(),
    )


@router.callback_query(F.data.startswith("u_sell_pm:"))
async def cb_u_sell_pm(cb: CallbackQuery, state: FSMContext) -> None:
    idx = int(cb.data.split(":")[1])
    if idx < 0 or idx >= len(SELLER_PAYMENT_OPTIONS):
        await cb.answer("خيار غير صالح.", show_alert=True)
        return
    method_label = SELLER_PAYMENT_OPTIONS[idx][1]
    await state.update_data(sell_payment_method=method_label)
    await state.set_state(UserStates.sell_price)
    await cb.message.answer(
        f"5️⃣ <b>أدخل سعر البيع كما تحب</b> (طريقة البيع: <b>{method_label}</b>)\n"
        f"مثال: <code>10$</code> أو <code>5000 دج</code> أو <code>2000 فليكسي</code>:",
        reply_markup=user_cancel_home_kb(),
    )
    await cb.answer()


@router.callback_query(F.data == "u_sell_pm_custom")
async def cb_u_sell_pm_custom(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(UserStates.sell_custom_method)
    await cb.message.answer(
        "✍️ <b>اكتب طريقة البيع التي تريدها</b> (مثال: <code>دولار + فليكسي</code> أو <code>تحويل بنكي</code>):",
        reply_markup=user_cancel_home_kb(),
    )
    await cb.answer()


@router.message(UserStates.sell_custom_method)
async def msg_u_sell_custom_method(message: Message, state: FSMContext) -> None:
    method_txt = (message.text or "").strip()
    if len(method_txt) < 2 or len(method_txt) > 80:
        await message.answer("⚠️ اكتب طريقة بيع واضحة (من حرفين إلى 80 حرفاً):", reply_markup=user_cancel_home_kb())
        return
    await state.update_data(sell_payment_method=method_txt)
    await state.set_state(UserStates.sell_price)
    await message.answer(
        f"5️⃣ <b>أدخل سعر البيع كما تحب</b> (طريقة البيع: <b>{method_txt}</b>):",
        reply_markup=user_cancel_home_kb(),
    )


@router.message(UserStates.sell_price)
async def msg_u_sell_price(message: Message, state: FSMContext, db: Database) -> None:
    if not message.from_user:
        return
    price_txt = (message.text or "").strip()
    if not price_txt or len(price_txt) > 80:
        await message.answer("⚠️ أرسل سعر البيع كما تحب (مثال: <code>15$</code> أو <code>3000 دج</code>):", reply_markup=user_cancel_home_kb())
        return

    data = await state.get_data()
    method_txt = str(data.get("sell_payment_method") or "تواصل مع البائع")
    photo_id = data.get("sell_photo")

    await db.register_or_update_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
    res = await db.publish_user_listing(
        user_id=message.from_user.id,
        name=str(data.get("sell_name", "")),
        description=str(data.get("sell_desc", "")),
        payment_method=method_txt,
        custom_price_text=price_txt,
        photo_file_id=str(photo_id) if photo_id else None,
    )
    await state.clear()

    if not res.get("ok"):
        await message.answer(
            f"⚠️ {res.get('error', 'تعذر نشر العرض.')}",
            reply_markup=user_screen_kb(message.from_user.id),
        )
        return

    prod = res["product"]
    seller_handle = format_seller_handle(prod.get("seller_username"), message.from_user.id)
    seller_url = build_seller_contact_url(prod.get("seller_username"), message.from_user.id)

    card_text = (
        f"📢 <b>عرض جديد من عضو</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏷️ <b>الشيء:</b> <b>{prod['name']}</b>\n"
        f"📝 <b>الوصف:</b> {prod['description']}\n"
        f"💳 <b>طريقة البيع:</b> <b>{prod['payment_method']}</b>\n"
        f"💲 <b>سعر البيع:</b> <b>{prod['custom_price_text']}</b>\n"
        f"👤 <b>البائع:</b> {seller_handle}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📩 للتواصل المباشر مع البائع وإتمام البيع: {seller_handle}"
    )
    card_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📩 تواصل مع البائع مباشرة", url=seller_url)],
            [InlineKeyboardButton(text="🗑️ حذف إعلاني", callback_data=f"u_del_ad:{prod['id']}")],
            [InlineKeyboardButton(text="🏠 القائمة الرئيسية", callback_data="u_home")],
        ]
    )

    if prod.get("photo_file_id"):
        await message.answer_photo(photo=str(prod["photo_file_id"]), caption=card_text, reply_markup=card_kb)
    else:
        await message.answer(card_text, reply_markup=card_kb)

    await message.answer(
        f"✅ تم خصم <b>{res['fee']}</b> نقطة وإضافة <b>«{prod['name']}»</b> إلى المتجر بنجاح!\n"
        f"💰 رصيدك المتبقي: <b>{res['new_balance']}</b> نقطة.",
        reply_markup=user_screen_kb(message.from_user.id),
    )


@router.callback_query(F.data.startswith("u_del_ad:"))
async def cb_u_del_ad(cb: CallbackQuery, db: Database) -> None:
    if not cb.from_user:
        return
    pid = int(cb.data.split(":")[1])
    ok, msg = await db.delete_seller_product(pid, cb.from_user.id)
    if not ok:
        await cb.answer(msg, show_alert=True)
        return
    await cb.answer(msg, show_alert=True)
    try:
        await cb.message.delete()
    except Exception:
        pass
    await cb.message.answer(
        f"✅ {msg}",
        reply_markup=user_screen_kb(cb.from_user.id),
    )


async def _send_special_product(message: Message, db: Database, code: str) -> None:
    prod = await db.get_product_by_code(code)
    if not prod or int(prod["is_active"]) != 1:
        await message.answer("⚠️ الرمز الخاص غير صحيح أو تم شراء المنتج وحذفه.")
        return
    inv = await db.get_inventory_counts(int(prod["id"]))
    stock_txt = f"📦 المخزون: <b>{inv['available']}</b>" if prod["delivery_type"] == "auto" else "🛠️ تسليم يدوي"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🛒 شراء الآن", callback_data=f"buy_sp:{prod['id']}")]]
    )
    caption = (
        f"🔐 <b>منتج خاص</b> (<code>{prod['special_code']}</code>)\n"
        f"🏷️ <b>{prod['name']}</b>\n"
        f"📝 {prod['description']}\n"
        f"💎 السعر: <b>{prod['price_points']}</b> نقطة | {stock_txt}"
    )
    if prod.get("photo_file_id"):
        await message.answer_photo(photo=str(prod["photo_file_id"]), caption=caption, reply_markup=kb)
    else:
        await message.answer(caption, reply_markup=kb)


@router.callback_query(F.data == "u_secs")
async def cb_u_secs(cb: CallbackQuery, db: Database) -> None:
    if not await _guard_forced_sub_callback(cb, db):
        return
    if not await db.is_store_enabled():
        await cb.answer("🛠️ المتجر متوقف مؤقتاً للصيانة.", show_alert=True)
        return
    secs = await db.get_sections(active_only=True)
    rows = [[InlineKeyboardButton(text=f"📂 {s['name']}", callback_data=f"u_sec:{s['id']}")] for s in secs]
    rows.append([InlineKeyboardButton(text="🔐 إدخال رمز منتج مخفي", callback_data="u_code")])
    rows.append([InlineKeyboardButton(text="🏠 القائمة الرئيسية", callback_data="u_home")])
    try:
        await cb.message.edit_text("🛍️ <b>أقسام المتجر</b> — اختر قسماً:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except Exception:
        await cb.message.answer("🛍️ <b>أقسام المتجر</b> — اختر قسماً:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()


@router.callback_query(F.data.startswith("u_sec:"))
async def cb_u_sec(cb: CallbackQuery, db: Database) -> None:
    if not await _guard_forced_sub_callback(cb, db):
        return
    sec_id = int(cb.data.split(":")[1])
    sec = await db.get_section(sec_id)
    if not sec or int(sec["is_active"]) != 1:
        await cb.answer("القسم غير متاح.", show_alert=True)
        return
    prods = await db.get_storefront_products(sec_id)
    rows = []
    for p in prods:
        price_display, _ = _format_product_price_and_method(p)
        clean_price = price_display.replace("<b>", "").replace("</b>", "")
        rows.append([InlineKeyboardButton(text=f"🏷️ {p['name']} — {clean_price}", callback_data=f"u_pr:{p['id']}")])
    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="u_secs")])
    rows.append([InlineKeyboardButton(text="🏠 القائمة الرئيسية", callback_data="u_home")])
    msg_txt = f"📂 <b>{sec['name']}</b>" if prods else f"📂 <b>{sec['name']}</b>\n\nلا توجد منتجات متاحة حالياً."
    try:
        await cb.message.edit_text(msg_txt, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except Exception:
        await cb.message.answer(msg_txt, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()


@router.callback_query(F.data.startswith("u_pr:"))
async def cb_u_pr(cb: CallbackQuery, db: Database) -> None:
    if not await _guard_forced_sub_callback(cb, db):
        return
    pid = int(cb.data.split(":")[1])
    p = await db.get_product(pid)
    if not p or int(p["is_active"]) != 1 or p["visibility"] == "completely_hidden":
        await cb.answer("هذا المنتج تم شراؤه أو حذفه من المتجر.", show_alert=True)
        return

    inv = await db.get_inventory_counts(pid)
    desc = "<i>تفاصيل المحتوى مخفية لحين الشراء.</i>" if p["visibility"] == "hidden_details" else p["description"]
    price_display, method_display = _format_product_price_and_method(p)

    is_member_listing = p.get("seller_id") is not None or bool(p.get("custom_price_text"))
    seller_line = ""
    btn_rows: list[list[InlineKeyboardButton]] = []

    if is_member_listing:
        s_id = int(p["seller_id"]) if p.get("seller_id") else OWNER_ID
        s_handle = format_seller_handle(p.get("seller_username"), s_id)
        s_url = build_seller_contact_url(p.get("seller_username"), s_id)
        seller_line = f"\n💳 طريقة البيع: {method_display}\n👤 البائع: {s_handle} (للتواصل المباشر)"
        btn_rows.append([InlineKeyboardButton(text="📩 تواصل مع البائع مباشرة", url=s_url)])
        if cb.from_user and (cb.from_user.id == s_id or cb.from_user.id == OWNER_ID):
            btn_rows.append([InlineKeyboardButton(text="🗑️ حذف إعلاني", callback_data=f"u_del_ad:{pid}")])
    else:
        del_txt = f"⚡ تلقائي (متاح: <b>{inv['available']}</b>)" if p["delivery_type"] == "auto" else "🛠️ يدوي"
        seller_line = f"\n📦 التسليم: {del_txt}"
        btn_rows.append([InlineKeyboardButton(text="🛒 تأكيد الشراء", callback_data=f"buy_pub:{pid}")])

    btn_rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data=f"u_sec:{p['section_id']}")])
    btn_rows.append([InlineKeyboardButton(text="🏠 القائمة الرئيسية", callback_data="u_home")])

    text_body = (
        f"🏷️ <b>{p['name']}</b>\n"
        f"📝 {desc}\n"
        f"💲 السعر: {price_display}{seller_line}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=btn_rows)
    if p.get("photo_file_id"):
        await cb.message.answer_photo(photo=str(p["photo_file_id"]), caption=text_body, reply_markup=kb)
    else:
        await cb.message.edit_text(text_body, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "u_code")
async def cb_u_code(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    if not await _guard_forced_sub_callback(cb, db):
        return
    await state.set_state(UserStates.special_code)
    await cb.message.answer("🔐 أرسل الرمز الخاص للمنتج (مثال: <code>PM-1234ABCD</code>):", reply_markup=user_cancel_home_kb())
    await cb.answer()


@router.message(UserStates.special_code)
async def msg_u_code(message: Message, state: FSMContext, db: Database) -> None:
    code = (message.text or "").strip().upper()
    await state.clear()
    await _send_special_product(message, db, code)


async def _do_buy(cb: CallbackQuery, db: Database, pid: int, allow_hidden: bool) -> None:
    if not cb.from_user:
        return
    res = await db.create_purchase(cb.from_user.id, pid, allow_hidden=allow_hidden)
    if not res.get("ok"):
        await cb.answer(res.get("error", "تعذر الشراء."), show_alert=True)
        return

    await cb.answer("تم الشراء وحذف المنتج نهائياً من المتجر!")
    back_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 القائمة الرئيسية", callback_data="u_home")]])
    if res["delivery_type"] == "auto":
        done_txt = (
            f"✅ <b>تم الشراء وحُذف المنتج نهائياً من المتجر!</b>\n\n"
            f"🧾 الطلب: <code>{res['order_uid']}</code>\n"
            f"🏷️ المنتج: <b>{res['product_name']}</b>\n"
            f"💎 المخصوم: <b>{res['price_points']}</b> نقطة | الرصيد: <b>{res['new_balance']}</b>\n\n"
            f"🎁 <b>محتوى التسليم:</b>\n<pre>{res['delivery_content']}</pre>"
        )
        try:
            await cb.message.edit_text(done_txt, reply_markup=back_kb)
        except Exception:
            await cb.message.answer(done_txt, reply_markup=back_kb)
    else:
        pend_txt = (
            f"⏳ <b>تم إنشاء طلب يدوي قيد المراجعة</b>\n\n"
            f"🧾 الطلب: <code>{res['order_uid']}</code>\n"
            f"🏷️ المنتج: <b>{res['product_name']}</b>\n"
            f"💎 المخصوم: <b>{res['price_points']}</b> نقطة | الرصيد: <b>{res['new_balance']}</b>"
        )
        try:
            await cb.message.edit_text(pend_txt, reply_markup=back_kb)
        except Exception:
            await cb.message.answer(pend_txt, reply_markup=back_kb)

        if cb.bot:
            try:
                kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(text="✅ قبول وحذف", callback_data=f"a_o_acc:{res['order_id']}"),
                            InlineKeyboardButton(text="❌ رفض وإرجاع", callback_data=f"a_o_rej:{res['order_id']}"),
                        ]
                    ]
                )
                await cb.bot.send_message(
                    OWNER_ID,
                    f"📥 <b>طلب يدوي جديد</b>\n🧾 <code>{res['order_uid']}</code> (#{res['order_id']})\n"
                    f"👤 <b>{cb.from_user.full_name}</b> (<code>{cb.from_user.id}</code>)\n"
                    f"🏷️ <b>{res['product_name']}</b> — <b>{res['price_points']}</b> نقطة",
                    reply_markup=kb,
                )
            except Exception:
                pass


@router.callback_query(F.data.startswith("buy_pub:"))
async def cb_buy_pub(cb: CallbackQuery, db: Database) -> None:
    if not await _guard_forced_sub_callback(cb, db):
        return
    await _do_buy(cb, db, int(cb.data.split(":")[1]), allow_hidden=False)


@router.callback_query(F.data.startswith("buy_sp:"))
async def cb_buy_sp(cb: CallbackQuery, db: Database) -> None:
    if not await _guard_forced_sub_callback(cb, db):
        return
    await _do_buy(cb, db, int(cb.data.split(":")[1]), allow_hidden=True)


@router.callback_query(F.data == "u_wallet")
async def cb_u_wallet(cb: CallbackQuery, db: Database) -> None:
    if not await _guard_forced_sub_callback(cb, db):
        return
    if not cb.from_user:
        return
    u = await db.get_user(cb.from_user.id)
    if not u:
        return
    txs = await db.get_user_transactions(cb.from_user.id, limit=10)
    tx_txt = "\n".join(
        f"• <b>{'+' if int(t['amount']) > 0 else ''}{t['amount']}</b> ن (الرصيد: {t['balance_after']}) — {t['reason']} <i>[{t['created_at']}]</i>"
        for t in txs
    ) or "<i>لا توجد معاملات بعد.</i>"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 القائمة الرئيسية", callback_data="u_home")]])
    txt = (
        f"💰 <b>محفظتي</b>\n\n"
        f"💎 الرصيد الحالي: <b>{u['balance']}</b> نقطة\n"
        f"📈 المكتسبة: <b>{u['earned_points']}</b> | 📉 المصروفة: <b>{u['spent_points']}</b>\n\n"
        f"📜 <b>سجل المعاملات:</b>\n{tx_txt}"
    )
    try:
        await cb.message.edit_text(txt, reply_markup=kb)
    except Exception:
        await cb.message.answer(txt, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "u_earn")
async def cb_u_earn(cb: CallbackQuery, db: Database) -> None:
    if not await _guard_forced_sub_callback(cb, db):
        return
    if not cb.from_user or not cb.bot:
        return
    rew = await db.get_referral_reward()
    p_rew = await db.get_promo_reward_per_join()
    sup = await db.get_setting("support_contact", DEFAULT_SUPPORT_CONTACT)
    buy_contacts = await db.get_buy_button_contacts(sup)
    link = await create_start_link(cb.bot, f"ref_{cb.from_user.id}", encode=False)
    txt = (
        f"🎁 <b>جمع النقاط</b>\n\n"
        f"1️⃣ <b>دعوة الأصدقاء:</b> احصل على <b>{rew}</b> نقطة لكل صديق جديد:\n<code>{link}</code>\n\n"
        f"2️⃣ <b>قنوات التمويل:</b> انضم للقنوات الممولة واحصل على <b>+{p_rew}</b> نقطة لكل قناة.\n\n"
        f"3️⃣ <b>طرق شراء النقاط:</b> اختر وسيلة الدفع من الأزرار أدناه للتواصل المباشر مع المالك، أو استخدم كود نقاط."
    )
    try:
        await cb.message.edit_text(txt, reply_markup=build_earn_points_kb(buy_contacts, sup))
    except Exception:
        await cb.message.answer(txt, reply_markup=build_earn_points_kb(buy_contacts, sup))
    await cb.answer()


@router.callback_query(F.data == "u_redeem_promo")
async def cb_u_redeem_promo(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    if not await _guard_forced_sub_callback(cb, db):
        return
    await state.set_state(UserStates.redeem_promo)
    await cb.message.answer("🎟️ أرسل كود النقاط الذي حصلت عليه:", reply_markup=user_cancel_home_kb())
    await cb.answer()


@router.message(UserStates.redeem_promo)
async def msg_u_redeem_promo(message: Message, state: FSMContext, db: Database) -> None:
    if not message.from_user:
        return
    await state.clear()
    res = await db.redeem_promo_code(message.from_user.id, message.text or "")
    if not res.get("ok"):
        await message.answer(f"⚠️ {res.get('error', 'تعذر استخدام الكود.')}", reply_markup=user_screen_kb(message.from_user.id))
        return
    await message.answer(
        f"✅ تم تفعيل الكود <code>{res['code']}</code> بنجاح!\n"
        f"💎 حصلت على: <b>+{res['points']}</b> نقطة\n"
        f"💰 رصيدك الحالي: <b>{res['new_balance']}</b> نقطة",
        reply_markup=user_screen_kb(message.from_user.id),
    )


@router.callback_query(F.data == "u_ref")
async def cb_u_ref(cb: CallbackQuery, db: Database) -> None:
    if not await _guard_forced_sub_callback(cb, db):
        return
    if not cb.from_user or not cb.bot:
        return
    rew = await db.get_referral_reward()
    cnt = await db.get_user_referral_count(cb.from_user.id)
    link = await create_start_link(cb.bot, f"ref_{cb.from_user.id}", encode=False)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 القائمة الرئيسية", callback_data="u_home")]])
    txt = (
        f"👥 <b>دعوة الأصدقاء</b>\n\n"
        f"🔗 رابطك:\n<code>{link}</code>\n"
        f"🎁 المكافأة: <b>{rew}</b> نقطة لكل صديق | ✅ إحالاتك: <b>{cnt}</b>"
    )
    try:
        await cb.message.edit_text(txt, reply_markup=kb)
    except Exception:
        await cb.message.answer(txt, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "u_orders")
async def cb_u_orders(cb: CallbackQuery, db: Database) -> None:
    if not await _guard_forced_sub_callback(cb, db):
        return
    if not cb.from_user:
        return
    ords = await db.get_user_orders(cb.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 القائمة الرئيسية", callback_data="u_home")]])
    if not ords:
        txt = "📦 لا توجد طلبات حتى الآن."
    else:
        st_map = {"pending": "⏳ معلق", "completed": "✅ مكتمل", "rejected": "❌ مرفوض"}
        blocks = []
        for o in ords:
            extra = f"\n  🎁 التسليم:\n<pre>{o['delivery_content']}</pre>" if o["status"] == "completed" and o["delivery_content"] else ""
            blocks.append(f"🧾 <code>{o['order_uid']}</code> | <b>{o['product_name']}</b> | {st_map.get(o['status'], o['status'])}{extra}")
        txt = "📦 <b>طلباتي ومشترياتي</b>\n\n" + "\n\n".join(blocks)
    try:
        await cb.message.edit_text(txt, reply_markup=kb)
    except Exception:
        await cb.message.answer(txt, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "u_stats")
async def cb_u_stats(cb: CallbackQuery, db: Database) -> None:
    if not await _guard_forced_sub_callback(cb, db):
        return
    if not cb.from_user:
        return
    u = await db.get_user(cb.from_user.id)
    if not u:
        return
    refs = await db.get_user_referral_count(cb.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 القائمة الرئيسية", callback_data="u_home")]])
    txt = (
        f"📊 <b>إحصائياتي</b>\n\n"
        f"💰 الرصيد: <b>{u['balance']}</b> نقطة\n"
        f"📈 المكتسبة: <b>{u['earned_points']}</b> | 📉 المصروفة: <b>{u['spent_points']}</b>\n"
        f"👥 الإحالات الناجحة: <b>{refs}</b>"
    )
    try:
        await cb.message.edit_text(txt, reply_markup=kb)
    except Exception:
        await cb.message.answer(txt, reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data == "u_support")
async def cb_u_support(cb: CallbackQuery, db: Database) -> None:
    if not await _guard_forced_sub_callback(cb, db):
        return
    sup = await db.get_setting("support_contact", DEFAULT_SUPPORT_CONTACT)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🏠 القائمة الرئيسية", callback_data="u_home")]])
    txt = f"📞 <b>الدعم الفني</b>\nتواصل مع المسؤول الحالي للدعم: <b>{sup}</b>"
    try:
        await cb.message.edit_text(txt, reply_markup=kb)
    except Exception:
        await cb.message.answer(txt, reply_markup=kb)
    await cb.answer()


# ================= لوحة تحكم المالك =================
def _is_owner(uid: Optional[int]) -> bool:
    return uid == OWNER_ID


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext, db: Database) -> None:
    if not message.from_user or not _is_owner(message.from_user.id):
        return
    await state.clear()
    await message.answer("⚙️ <b>لوحة تحكم المالك — POINT MARKET</b>", reply_markup=await admin_kb(db))


@router.callback_query(F.data == "a_back")
async def cb_a_back(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    if not cb.from_user or not _is_owner(cb.from_user.id):
        return
    await state.clear()
    try:
        await cb.message.edit_text("⚙️ <b>لوحة تحكم المالك — POINT MARKET</b>", reply_markup=await admin_kb(db))
    except Exception:
        await cb.message.answer("⚙️ <b>لوحة تحكم المالك — POINT MARKET</b>", reply_markup=await admin_kb(db))
    await cb.answer()


# ================= إدارة الاشتراك الإجباري للمالك =================
async def _render_fsub_menu(cb: CallbackQuery, db: Database) -> None:
    chs = await db.get_forced_channels(active_only=False)
    lines = ["🔒 <b>إدارة الاشتراك الإجباري</b>"]
    rows: list[list[InlineKeyboardButton]] = []
    if not chs:
        lines.append("\nلا توجد قنوات أو مجموعات مضافة حالياً.")
    else:
        for ch in chs:
            st_icon = "🟢" if int(ch["is_active"]) == 1 else "🔴"
            lines.append(f"{st_icon} <b>{ch['title']}</b> ({ch['chat_target']})")
            rows.append([
                InlineKeyboardButton(text=f"{'⏸️ إيقاف' if ch['is_active'] else '▶️ تفعيل'} {ch['title']}", callback_data=f"a_fs_tog:{ch['id']}"),
                InlineKeyboardButton(text="🗑️ حذف", callback_data=f"a_fs_del:{ch['id']}"),
            ])
    rows.append([InlineKeyboardButton(text="➕ إضافة قناة / مجموعة", callback_data="a_fs_add")])
    rows.append([admin_home_btn()])
    try:
        await cb.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except Exception:
        await cb.message.answer("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data == "a_fsub")
async def cb_a_fsub(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    if not cb.from_user or not _is_owner(cb.from_user.id):
        return
    await state.clear()
    await _render_fsub_menu(cb, db)
    await cb.answer()


@router.callback_query(F.data == "a_fs_add")
async def cb_a_fs_add(cb: CallbackQuery, state: FSMContext) -> None:
    if not cb.from_user or not _is_owner(cb.from_user.id):
        return
    await state.set_state(AdminStates.fsub_title)
    await cb.message.answer(
        "1️⃣ <b>أرسل اسم القناة أو المجموعة</b> (الذي يظهر للمستخدم على الزر):",
        reply_markup=admin_cancel_home_kb(),
    )
    await cb.answer()


@router.message(AdminStates.fsub_title)
async def msg_a_fs_title(message: Message, state: FSMContext) -> None:
    if not message.from_user or not _is_owner(message.from_user.id):
        return
    title = (message.text or "").strip()
    if len(title) < 2 or len(title) > 80:
        await message.answer("⚠️ أرسل اسماً واضحاً (من حرفين إلى 80 حرفاً):", reply_markup=admin_cancel_home_kb())
        return
    await state.update_data(fsub_title=title)
    await state.set_state(AdminStates.fsub_target)
    await message.answer(
        "2️⃣ <b>أرسل <code>@username</code> القناة/المجموعة أو معرّفها (<code>-100...</code>)</b>\n"
        "<i>(تأكد أن البوت مشرف فيها لكي يتحقق من الاشتراك):</i>",
        reply_markup=admin_cancel_home_kb(),
    )


@router.message(AdminStates.fsub_target)
async def msg_a_fs_target(message: Message, state: FSMContext) -> None:
    if not message.from_user or not _is_owner(message.from_user.id):
        return
    api_target, default_url, label = parse_channel_target(message.text or "")
    if not api_target:
        await message.answer("⚠️ أرسل <code>@username</code> أو <code>ID</code> صالح للقناة/المجموعة:", reply_markup=admin_cancel_home_kb())
        return
    await state.update_data(fsub_target=api_target, fsub_default_url=default_url or "", fsub_label=label or api_target)
    await state.set_state(AdminStates.fsub_url)
    await message.answer(
        "3️⃣ <b>أرسل رابط الانضمام</b> (أو أرسل <code>تلقائي</code> لاستخدام الرابط الافتراضي):",
        reply_markup=admin_cancel_home_kb(),
    )


@router.message(AdminStates.fsub_url)
async def msg_a_fs_url(message: Message, state: FSMContext, db: Database) -> None:
    if not message.from_user or not _is_owner(message.from_user.id):
        return
    raw_u = (message.text or "").strip()
    data = await state.get_data()
    final_url = str(data.get("fsub_default_url") or "")
    if raw_u.lower() not in {"تلقائي", "auto", "/auto"}:
        if not raw_u.startswith(("http://", "https://", "tg://")):
            await message.answer("⚠️ أرسل رابطاً يبدأ بـ <code>https://</code> أو اكتب <code>تلقائي</code>:", reply_markup=admin_cancel_home_kb())
            return
        final_url = raw_u

    if not final_url:
        await message.answer("⚠️ لم يتوفر رابط تلقائي، يرجى إرسال رابط الانضمام يدوياً:", reply_markup=admin_cancel_home_kb())
        return

    ok, txt = await db.add_forced_channel(str(data["fsub_title"]), str(data["fsub_target"]), final_url)
    await state.clear()
    await message.answer(f"{'✅' if ok else '⚠️'} {txt}", reply_markup=await admin_kb(db))


@router.callback_query(F.data.startswith("a_fs_tog:"))
async def cb_a_fs_tog(cb: CallbackQuery, db: Database) -> None:
    if not cb.from_user or not _is_owner(cb.from_user.id):
        return
    await db.toggle_forced_channel(int(cb.data.split(":")[1]))
    await _render_fsub_menu(cb, db)
    await cb.answer("تم تحديث الحالة.")


@router.callback_query(F.data.startswith("a_fs_del:"))
async def cb_a_fs_del(cb: CallbackQuery, db: Database) -> None:
    if not cb.from_user or not _is_owner(cb.from_user.id):
        return
    await db.delete_forced_channel(int(cb.data.split(":")[1]))
    await _render_fsub_menu(cb, db)
    await cb.answer("تم الحذف.")


@router.callback_query(F.data == "a_lfee")
async def cb_a_lfee(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    if not cb.from_user or not _is_owner(cb.from_user.id):
        return
    cur = await db.get_listing_fee()
    await state.set_state(AdminStates.set_listing_fee)
    await cb.message.answer(
        f"📢 رسوم عرض الشيء للبيع الحالية: <b>{cur}</b> نقطة.\nأرسل عدد النقاط الجديد:",
        reply_markup=admin_cancel_home_kb(),
    )
    await cb.answer()


@router.message(AdminStates.set_listing_fee)
async def msg_a_lfee(message: Message, state: FSMContext, db: Database) -> None:
    if not message.from_user or not _is_owner(message.from_user.id):
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("⚠️ أدخل رقماً صحيحاً أكبر من 0:", reply_markup=admin_cancel_home_kb())
        return
    await db.set_setting("listing_fee", str(int(raw)))
    await state.clear()
    await message.answer(f"✅ تم تحديث رسوم عرض شيء للبيع إلى: <b>{int(raw)}</b> نقطة.", reply_markup=await admin_kb(db))


@router.callback_query(F.data == "a_pcost")
async def cb_a_pcost(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    if not cb.from_user or not _is_owner(cb.from_user.id):
        return
    cur = await db.get_promo_cost_per_member()
    await state.set_state(AdminStates.set_promo_cost)
    await cb.message.answer(
        f"💸 تكلفة تمويل العضو الواحد الحالية: <b>{cur}</b> نقطة.\nأرسل عدد النقاط الجديد لكل عضو ممول:",
        reply_markup=admin_cancel_home_kb(),
    )
    await cb.answer()


@router.message(AdminStates.set_promo_cost)
async def msg_a_pcost(message: Message, state: FSMContext, db: Database) -> None:
    if not message.from_user or not _is_owner(message.from_user.id):
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("⚠️ أدخل رقماً صحيحاً أكبر من 0:", reply_markup=admin_cancel_home_kb())
        return
    await db.set_setting("promo_cost_per_member", str(int(raw)))
    await state.clear()
    await message.answer(f"✅ تم تحديث تكلفة تمويل العضو الواحد إلى: <b>{int(raw)}</b> نقطة.", reply_markup=await admin_kb(db))


@router.callback_query(F.data == "a_prew")
async def cb_a_prew(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    if not cb.from_user or not _is_owner(cb.from_user.id):
        return
    cur = await db.get_promo_reward_per_join()
    await state.set_state(AdminStates.set_promo_reward)
    await cb.message.answer(
        f"🎁 مكافأة الانضمام لقناة ممولة الحالية: <b>{cur}</b> نقطة.\nأرسل عدد النقاط الجديد لكل عضو ينضم:",
        reply_markup=admin_cancel_home_kb(),
    )
    await cb.answer()


@router.message(AdminStates.set_promo_reward)
async def msg_a_prew(message: Message, state: FSMContext, db: Database) -> None:
    if not message.from_user or not _is_owner(message.from_user.id):
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("⚠️ أدخل رقماً صحيحاً أكبر من 0:", reply_markup=admin_cancel_home_kb())
        return
    await db.set_setting("promo_reward_per_join", str(int(raw)))
    await state.clear()
    await message.answer(f"✅ تم تحديث مكافأة الانضمام لقناة ممولة إلى: <b>{int(raw)}</b> نقطة.", reply_markup=await admin_kb(db))


# ================= إدارة أكواد النقاط للمالك =================
@router.callback_query(F.data == "a_promo")
async def cb_a_promo(cb: CallbackQuery, state: FSMContext) -> None:
    if not cb.from_user or not _is_owner(cb.from_user.id):
        return
    await state.clear()
    await cb.message.edit_text(
        "🎟️ <b>إدارة أكواد النقاط</b>\nاختر إنشاء كود جديد أو استعراض الأكواد الحالية:",
        reply_markup=promo_menu_kb(),
    )
    await cb.answer()


@router.callback_query(F.data == "a_prm_add")
async def cb_a_prm_add(cb: CallbackQuery, state: FSMContext) -> None:
    if not cb.from_user or not _is_owner(cb.from_user.id):
        return
    await state.clear()
    await state.set_state(AdminStates.promo_points)
    await cb.message.answer(
        "1️⃣ أرسل <b>عدد النقاط</b> التي سيحصل عليها كل مستخدم يستخدم الكود (مثلاً: <code>3</code> أو <code>5</code>):",
        reply_markup=admin_cancel_home_kb(),
    )
    await cb.answer()


@router.message(AdminStates.promo_points)
async def msg_a_prm_points(message: Message, state: FSMContext) -> None:
    if not message.from_user or not _is_owner(message.from_user.id):
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("⚠️ أدخل عدداً صحيحاً للنقاط أكبر من 0:", reply_markup=admin_cancel_home_kb())
        return
    await state.update_data(promo_pts=int(raw))
    await state.set_state(AdminStates.promo_users)
    await message.answer(
        "2️⃣ أرسل <b>عدد الأشخاص</b> الذين سيستفيدون من الكود (مثلاً: <code>2</code> أو <code>1000</code>):",
        reply_markup=admin_cancel_home_kb(),
    )


@router.message(AdminStates.promo_users)
async def msg_a_prm_users(message: Message, state: FSMContext) -> None:
    if not message.from_user or not _is_owner(message.from_user.id):
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("⚠️ أدخل عدداً صحيحاً للأشخاص أكبر من 0:", reply_markup=admin_cancel_home_kb())
        return
    await state.update_data(promo_max_uses=int(raw))
    await state.set_state(AdminStates.promo_code)
    await message.answer(
        "3️⃣ أرسل <b>نص الكود</b> الذي تريد إنشاءه (مثلاً: <code>heke</code>)، أو أرسل <code>تلقائي</code> لتوليد كود عشوائي:",
        reply_markup=admin_cancel_home_kb(),
    )


@router.message(AdminStates.promo_code)
async def msg_a_prm_code(message: Message, state: FSMContext, db: Database) -> None:
    if not message.from_user or not _is_owner(message.from_user.id):
        return
    raw_code = (message.text or "").strip()
    if raw_code.lower() in {"تلقائي", "auto", "/auto"}:
        raw_code = f"CODE-{uuid.uuid4().hex[:6].upper()}"

    data = await state.get_data()
    pts = int(data.get("promo_pts", 0))
    max_u = int(data.get("promo_max_uses", 0))

    ok, txt, promo = await db.create_promo_code(raw_code, pts, max_u)
    if not ok or not promo:
        await message.answer(f"⚠️ {txt}\nأرسل نص الكود مرة أخرى:", reply_markup=admin_cancel_home_kb())
        return

    await state.clear()
    await message.answer(
        f"✅ <b>تم إنشاء كود النقاط بنجاح!</b>\n\n"
        f"🎟️ الكود: <code>{promo['code']}</code>\n"
        f"💎 النقاط لكل مستخدم: <b>{promo['points']}</b> نقطة\n"
        f"👥 عدد الأشخاص المسموح: <b>{promo['max_uses']}</b>",
        reply_markup=promo_menu_kb(),
    )


@router.callback_query(F.data == "a_prm_list")
async def cb_a_prm_list(cb: CallbackQuery, db: Database) -> None:
    if not cb.from_user or not _is_owner(cb.from_user.id):
        return
    codes = await db.get_promo_codes()
    if not codes:
        await cb.message.edit_text(
            "📋 لا توجد أكواد نقاط حالياً.",
            reply_markup=promo_menu_kb(),
        )
        await cb.answer()
        return

    lines = ["📋 <b>أكواد النقاط الحالية:</b>"]
    rows = []
    for c in codes[:20]:
        st_icon = "🟢" if int(c["is_active"]) == 1 and int(c["used_count"]) < int(c["max_uses"]) else "🔴"
        lines.append(
            f"{st_icon} <code>{c['code']}</code> | <b>{c['points']}</b> ن | مستخدم: <b>{c['used_count']}/{c['max_uses']}</b>"
        )
        rows.append([InlineKeyboardButton(text=f"🗑️ حذف {c['code']}", callback_data=f"a_prm_del:{c['id']}")])

    rows.append([InlineKeyboardButton(text="⬅️ رجوع", callback_data="a_promo")])
    rows.append([admin_home_btn()])
    await cb.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()


@router.callback_query(F.data.startswith("a_prm_del:"))
async def cb_a_prm_del(cb: CallbackQuery, db: Database) -> None:
    if not cb.from_user or not _is_owner(cb.from_user.id):
        return
    pid = int(cb.data.split(":")[1])
    await db.delete_promo_code(pid)
    await cb.answer("تم حذف الكود.", show_alert=True)
    await cb_a_prm_list(cb, db)


@router.callback_query(F.data == "a_prices")
async def cb_a_prices(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    if not cb.from_user or not _is_owner(cb.from_user.id):
        return
    await state.clear()
    await cb.message.edit_text(
        "💱 <b>إدارة أسعار شراء النقاط</b>\nاختر الطريقة التي تريد تغيير سعرها:",
        reply_markup=await prices_menu_kb(db),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("a_pr_set:"))
async def cb_a_pr_set(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    if not cb.from_user or not _is_owner(cb.from_user.id):
        return
    key = cb.data.split(":", maxsplit=1)[1]
    if key not in POINT_PRICE_LABELS:
        await cb.answer("طريقة غير معروفة.", show_alert=True)
        return
    _, short_name = POINT_PRICE_LABELS[key]
    cur_val = await db.get_setting(key, DEFAULT_POINT_PRICES[key])
    await state.update_data(price_key=key)
    await state.set_state(AdminStates.set_point_price)
    await cb.message.answer(
        f"💱 تعديل سعر: <b>{short_name}</b>\n"
        f"السعر الحالي: <b>{cur_val}</b>\n\n"
        f"أرسل السعر الجديد كما تريد أن يظهر للمستخدمين (مثال: <code>كل 1$ = 120 نقطة</code>):",
        reply_markup=admin_cancel_home_kb(),
    )
    await cb.answer()


@router.message(AdminStates.set_point_price)
async def msg_a_pr_set(message: Message, state: FSMContext, db: Database) -> None:
    if not message.from_user or not _is_owner(message.from_user.id):
        return
    new_price = (message.text or "").strip()
    if not new_price:
        await message.answer("⚠️ أرسل نص السعر الجديد:", reply_markup=admin_cancel_home_kb())
        return
    data = await state.get_data()
    key = data.get("price_key", "")
    if key not in POINT_PRICE_LABELS:
        await state.clear()
        await message.answer("⚠️ حدث خطأ في اختيار الطريقة.", reply_markup=await admin_kb(db))
        return
    _, short_name = POINT_PRICE_LABELS[key]
    await db.set_setting(key, new_price)
    await state.clear()
    await message.answer(
        f"✅ تم تحديث سعر <b>{short_name}</b> إلى: <b>{new_price}</b>",
        reply_markup=await prices_menu_kb(db),
    )


@router.callback_query(F.data == "a_tog_st")
async def cb_a_tog_st(cb: CallbackQuery, db: Database) -> None:
    if not cb.from_user or not _is_owner(cb.from_user.id):
        return
    cur = await db.is_store_enabled()
    await db.set_setting("store_enabled", "0" if cur else "1")
    await cb.answer("تم تحديث حالة المتجر.", show_alert=True)
    await cb.message.edit_reply_markup(reply_markup=await admin_kb(db))


@router.callback_query(F.data == "a_sup")
async def cb_a_sup(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    if not cb.from_user or not _is_owner(cb.from_user.id):
        return
    cur = await db.get_setting("support_contact", DEFAULT_SUPPORT_CONTACT)
    await state.set_state(AdminStates.set_support)
    await cb.message.answer(
        f"📞 <b>إدارة الدعم الفني</b>\n"
        f"الحساب/الجهة الحالية للدعم: <b>{cur}</b>\n\n"
        f"أرسل الآن <code>@username</code> أو معرّف/رابط الشخص الذي تريد تعيينه في الدعم الفني:",
        reply_markup=admin_cancel_home_kb(),
    )
    await cb.answer()


@router.message(AdminStates.set_support)
async def msg_a_sup(message: Message, state: FSMContext, db: Database) -> None:
    if not message.from_user or not _is_owner(message.from_user.id):
        return
    val = (message.text or "").strip()
    if not val:
        await message.answer("⚠️ أرسل معرفاً أو نصاً صالحاً للدعم الفني:", reply_markup=admin_cancel_home_kb())
        return
    await db.set_setting("support_contact", val)
    await state.clear()
    await message.answer(f"✅ تم تحديث مسؤول الدعم الفني إلى: <b>{val}</b>", reply_markup=await admin_kb(db))


@router.callback_query(F.data == "a_buy_users")
async def cb_a_buy_users(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    if not cb.from_user or not _is_owner(cb.from_user.id):
        return
    await state.clear()
    await cb.message.edit_text(
        "👤 <b>تحديد يوزر مستلم الشراء لكل زر</b>\n"
        "اختر الزر الذي تريد تحديد يوزر الشخص الذي سيستلم الطلبات عند الضغط عليه:",
        reply_markup=await buy_users_menu_kb(db),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("a_bu_set:"))
async def cb_a_bu_set(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    if not cb.from_user or not _is_owner(cb.from_user.id):
        return
    setting_key = cb.data.split(":", maxsplit=1)[1]
    if setting_key not in BUY_USER_LABELS:
        await cb.answer("زر غير معروف.", show_alert=True)
        return
    fallback = await db.get_setting("support_contact", DEFAULT_SUPPORT_CONTACT)
    cur_val = await db.get_setting(setting_key, fallback)
    await state.update_data(buy_user_key=setting_key)
    await state.set_state(AdminStates.set_buy_user)
    await cb.message.answer(
        f"👤 تعديل مستلم <b>{BUY_USER_LABELS[setting_key]}</b>\n"
        f"اليوزر الحالي: <b>{cur_val}</b>\n\n"
        f"أرسل الآن <code>@username</code> أو رابط الشخص الذي تريد أن يأخذه هذا الزر عند الضغط عليه:",
        reply_markup=admin_cancel_home_kb(),
    )
    await cb.answer()


@router.message(AdminStates.set_buy_user)
async def msg_a_bu_set(message: Message, state: FSMContext, db: Database) -> None:
    if not message.from_user or not _is_owner(message.from_user.id):
        return
    raw_val = (message.text or "").strip()
    if not raw_val:
        await message.answer("⚠️ أرسل <code>@username</code> أو رابطاً صالحاً:", reply_markup=admin_cancel_home_kb())
        return
    clean_val = raw_val if raw_val.startswith(("http://", "https://", "@")) else f"@{raw_val.lstrip('@')}"
    data = await state.get_data()
    setting_key = data.get("buy_user_key", "")
    if setting_key not in BUY_USER_LABELS:
        await state.clear()
        await message.answer("⚠️ حدث خطأ في اختيار الزر.", reply_markup=await admin_kb(db))
        return
    await db.set_setting(setting_key, clean_val)
    await state.clear()
    await message.answer(
        f"✅ تم تعيين <b>{clean_val}</b> لـ <b>{BUY_USER_LABELS[setting_key]}</b>.",
        reply_markup=await buy_users_menu_kb(db),
    )


@router.callback_query(F.data == "a_sec_add")
async def cb_a_sec_add(cb: CallbackQuery, state: FSMContext) -> None:
    if not cb.from_user or not _is_owner(cb.from_user.id):
        return
    await state.set_state(AdminStates.add_sec)
    await cb.message.answer("➕ أرسل اسم القسم الجديد:", reply_markup=admin_cancel_home_kb())
    await cb.answer()


@router.message(AdminStates.add_sec)
async def msg_a_sec_add(message: Message, state: FSMContext, db: Database) -> None:
    if not message.from_user or not _is_owner(message.from_user.id):
        return
    ok, txt = await db.create_section(message.text or "")
    await state.clear()
    await message.answer(f"{'✅' if ok else '⚠️'} {txt}", reply_markup=await admin_kb(db))


@router.callback_query(F.data == "a_sec_mng")
async def cb_a_sec_mng(cb: CallbackQuery, db: Database) -> None:
    if not cb.from_user or not _is_owner(cb.from_user.id):
        return
    secs = await db.get_sections(active_only=False)
    rows = [[InlineKeyboardButton(text=f"{'🟢' if s['is_active'] else '🔴'} {s['name']}", callback_data=f"a_s_sel:{s['id']}")] for s in secs]
    rows.append([admin_home_btn()])
    await cb.message.edit_text("📂 <b>إدارة الأقسام</b>:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()


@router.callback_query(F.data.startswith("a_s_sel:"))
async def cb_a_s_sel(cb: CallbackQuery, db: Database) -> None:
    if not cb.from_user or not _is_owner(cb.from_user.id):
        return
    sid = int(cb.data.split(":")[1])
    s = await db.get_section(sid)
    if not s:
        return
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ تعديل الاسم", callback_data=f"a_s_ren:{sid}")],
            [InlineKeyboardButton(text="🔴 تعطيل" if s["is_active"] else "🟢 تفعيل", callback_data=f"a_s_tog:{sid}")],
            [InlineKeyboardButton(text="🗑️ حذف", callback_data=f"a_s_del:{sid}")],
            [InlineKeyboardButton(text="⬅️ رجوع للأقسام", callback_data="a_sec_mng")],
            [admin_home_btn()],
        ]
    )
    await cb.message.edit_text(f"📂 <b>{s['name']}</b>", reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("a_s_ren:"))
async def cb_a_s_ren(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(sid=int(cb.data.split(":")[1]))
    await state.set_state(AdminStates.ren_sec)
    await cb.message.answer("✏️ أرسل الاسم الجديد للقسم:", reply_markup=admin_cancel_home_kb())
    await cb.answer()


@router.message(AdminStates.ren_sec)
async def msg_a_s_ren(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    ok, txt = await db.update_section_name(int(data["sid"]), message.text or "")
    await state.clear()
    await message.answer(f"{'✅' if ok else '⚠️'} {txt}", reply_markup=await admin_kb(db))


@router.callback_query(F.data.startswith("a_s_tog:"))
async def cb_a_s_tog(cb: CallbackQuery, db: Database) -> None:
    await db.toggle_section(int(cb.data.split(":")[1]))
    await cb_a_sec_mng(cb, db)


@router.callback_query(F.data.startswith("a_s_del:"))
async def cb_a_s_del(cb: CallbackQuery, db: Database) -> None:
    await db.delete_section(int(cb.data.split(":")[1]))
    await cb_a_sec_mng(cb, db)


@router.callback_query(F.data == "a_pr_add")
async def cb_a_pr_add(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    if not cb.from_user or not _is_owner(cb.from_user.id):
        return
    secs = await db.get_sections(active_only=False)
    if not secs:
        await cb.answer("أضف قسماً أولاً.", show_alert=True)
        return
    await state.clear()
    rows = [[InlineKeyboardButton(text=s["name"], callback_data=f"a_nps:{s['id']}")] for s in secs]
    rows.append([admin_home_btn()])
    await cb.message.answer("🆕 اختر قسم المنتج:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()


@router.callback_query(F.data.startswith("a_nps:"))
async def cb_a_nps(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(np_sec=int(cb.data.split(":")[1]))
    await state.set_state(AdminStates.np_name)
    await cb.message.answer("1️⃣ أرسل اسم المنتج:", reply_markup=admin_cancel_home_kb())
    await cb.answer()


@router.message(AdminStates.np_name)
async def msg_np_name(message: Message, state: FSMContext) -> None:
    await state.update_data(np_name=(message.text or "").strip())
    await state.set_state(AdminStates.np_desc)
    await message.answer("2️⃣ أرسل وصف المنتج:", reply_markup=admin_cancel_home_kb())


@router.message(AdminStates.np_desc)
async def msg_np_desc(message: Message, state: FSMContext) -> None:
    await state.update_data(np_desc=(message.text or "").strip())
    await state.set_state(AdminStates.np_photo)
    await message.answer(
        "🖼️ أرسل <b>صورة المنتج</b> الآن، أو أرسل <code>تخطي</code> / <code>/skip</code> إذا لم تكن هناك صورة:",
        reply_markup=admin_cancel_home_kb(),
    )


@router.message(AdminStates.np_photo)
async def msg_np_photo(message: Message, state: FSMContext) -> None:
    photo_id: Optional[str] = None
    if message.photo:
        photo_id = message.photo[-1].file_id
    else:
        txt = (message.text or "").strip().lower()
        if txt not in {"تخطي", "/skip", "skip"}:
            await message.answer(
                "⚠️ أرسل صورة للمنتج أو اكتب <code>تخطي</code> للمتابعة بدون صورة:",
                reply_markup=admin_cancel_home_kb(),
            )
            return

    await state.update_data(np_photo=photo_id)
    await state.set_state(AdminStates.np_price)
    await message.answer("3️⃣ أرسل سعر المنتج بالنقاط (رقم أكبر من 0):", reply_markup=admin_cancel_home_kb())


@router.message(AdminStates.np_price)
async def msg_np_price(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("⚠️ أدخل رقماً صحيحاً أكبر من 0:", reply_markup=admin_cancel_home_kb())
        return
    await state.update_data(np_price=int(raw))
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚡ تسليم تلقائي", callback_data="a_npd:auto")],
            [InlineKeyboardButton(text="🛠️ تسليم يدوي", callback_data="a_npd:manual")],
            [admin_home_btn()],
        ]
    )
    await message.answer("4️⃣ اختر طريقة التسليم:", reply_markup=kb)


@router.callback_query(F.data.startswith("a_npd:"))
async def cb_a_npd(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(np_del=cb.data.split(":")[1])
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🌐 علني", callback_data="a_npv:public")],
            [InlineKeyboardButton(text="🙈 مخفي التفاصيل", callback_data="a_npv:hidden_details")],
            [InlineKeyboardButton(text="🔐 مخفي بالكامل", callback_data="a_npv:completely_hidden")],
            [admin_home_btn()],
        ]
    )
    await cb.message.edit_text("5️⃣ اختر وضع العرض:", reply_markup=kb)
    await cb.answer()


@router.callback_query(F.data.startswith("a_npv:"))
async def cb_a_npv(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    vis = cb.data.split(":")[1]
    await state.update_data(np_vis=vis)
    data = await state.get_data()
    if data["np_del"] == "auto":
        await state.set_state(AdminStates.np_stock)
        await cb.message.edit_text(
            "6️⃣ أرسل محتوى التسليم التلقائي كاملاً كما تريد أن يصل للمشتري (بكل أسطره).",
            reply_markup=admin_cancel_home_kb(),
        )
        await cb.answer()
        return

    ok, msg, prod = await db.create_product(
        int(data["np_sec"]),
        data["np_name"],
        data["np_desc"],
        int(data["np_price"]),
        "manual",
        vis,
        photo_file_id=data.get("np_photo"),
    )
    await state.clear()
    code_txt = f"\n🔐 الرمز: <code>{prod['special_code']}</code>" if prod and prod.get("special_code") else ""
    await cb.message.edit_text(f"{'✅' if ok else '⚠️'} {msg}{code_txt}")
    await cb.message.answer("⚙️ <b>لوحة تحكم المالك — POINT MARKET</b>", reply_markup=await admin_kb(db))
    await cb.answer()


@router.message(AdminStates.np_stock)
async def msg_np_stock(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    items = parse_delivery_payload(message.text or "")
    if not items:
        await message.answer("⚠️ أرسل نص التسليم أولاً:", reply_markup=admin_cancel_home_kb())
        return
    ok, msg, prod = await db.create_product(
        int(data["np_sec"]),
        data["np_name"],
        data["np_desc"],
        int(data["np_price"]),
        "auto",
        data["np_vis"],
        photo_file_id=data.get("np_photo"),
    )
    await state.clear()
    if ok and prod:
        _, inv_msg, _ = await db.add_inventory(int(prod["id"]), items)
        code_txt = f"\n🔐 الرمز: <code>{prod['special_code']}</code>" if prod.get("special_code") else ""
        await message.answer(f"✅ {msg} ({inv_msg}){code_txt}", reply_markup=await admin_kb(db))
    else:
        await message.answer(f"⚠️ {msg}", reply_markup=await admin_kb(db))


@router.callback_query(F.data == "a_pr_list")
async def cb_a_pr_list(cb: CallbackQuery, db: Database) -> None:
    prods = await db.get_all_products()
    rows = []
    for p in prods[:25]:
        price_display, _ = _format_product_price_and_method(p)
        clean_price = price_display.replace("<b>", "").replace("</b>", "")
        rows.append([InlineKeyboardButton(text=f"{p['name']} ({clean_price})", callback_data=f"a_p_sel:{p['id']}")])
    rows.append([admin_home_btn()])
    await cb.message.edit_text("✏️ <b>المنتجات</b>:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()


@router.callback_query(F.data.startswith("a_p_sel:"))
async def cb_a_p_sel(cb: CallbackQuery, db: Database) -> None:
    pid = int(cb.data.split(":")[1])
    p = await db.get_product(pid)
    if not p:
        return
    inv = await db.get_inventory_counts(pid)
    price_field = "custom_price_text" if p.get("custom_price_text") else "price_points"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ تعديل السعر", callback_data=f"a_p_ed:{pid}:{price_field}"), InlineKeyboardButton(text="✏️ تعديل الاسم", callback_data=f"a_p_ed:{pid}:name")],
            [InlineKeyboardButton(text="👁️ علني", callback_data=f"a_p_vis:{pid}:public"), InlineKeyboardButton(text="🙈 مخفي التفاصيل", callback_data=f"a_p_vis:{pid}:hidden_details")],
            [InlineKeyboardButton(text="🔐 مخفي بالكامل", callback_data=f"a_p_vis:{pid}:completely_hidden"), InlineKeyboardButton(text="🔄 تفعيل/تعطيل", callback_data=f"a_p_tog:{pid}")],
            [InlineKeyboardButton(text="🗑️ حذف المنتج نهائياً", callback_data=f"a_p_del:{pid}")],
            [InlineKeyboardButton(text="⬅️ رجوع للمنتجات", callback_data="a_pr_list")],
            [admin_home_btn()],
        ]
    )
    price_display, method_display = _format_product_price_and_method(p)
    code_txt = f"\n🔐 الرمز: <code>{p['special_code']}</code>" if p.get("special_code") else ""
    photo_txt = "نعم" if p.get("photo_file_id") else "لا"
    seller_txt = f"\n👤 البائع: {format_seller_handle(p.get('seller_username'), int(p['seller_id']))} | طريقة البيع: {method_display}" if p.get("seller_id") else ""
    await cb.message.edit_text(
        f"🏷️ <b>{p['name']}</b> | السعر: {price_display} | صورة: <b>{photo_txt}</b>\n"
        f"العرض: <b>{p['visibility']}</b> | المخزون: <b>{inv['available']}</b> متاح{code_txt}{seller_txt}",
        reply_markup=kb,
    )
    await cb.answer()


@router.callback_query(F.data.startswith("a_p_vis:"))
async def cb_a_p_vis(cb: CallbackQuery, db: Database) -> None:
    _, pid_s, vis = cb.data.split(":")
    await db.update_product(int(pid_s), "visibility", vis)
    await cb.answer("تم تحديث وضع العرض.", show_alert=True)
    await cb_a_p_sel(cb, db)


@router.callback_query(F.data.startswith("a_p_tog:"))
async def cb_a_p_tog(cb: CallbackQuery, db: Database) -> None:
    pid = int(cb.data.split(":")[1])
    p = await db.get_product(pid)
    if p:
        await db.update_product(pid, "is_active", 0 if int(p["is_active"]) == 1 else 1)
    await cb.answer("تم التبديل.", show_alert=True)
    await cb_a_p_sel(cb, db)


@router.callback_query(F.data.startswith("a_p_ed:"))
async def cb_a_p_ed(cb: CallbackQuery, state: FSMContext) -> None:
    _, pid_s, field = cb.data.split(":")
    await state.update_data(epid=int(pid_s), efield=field)
    await state.set_state(AdminStates.edit_prod)
    await cb.message.answer("أرسل القيمة الجديدة:", reply_markup=admin_cancel_home_kb())
    await cb.answer()


@router.message(AdminStates.edit_prod)
async def msg_a_p_ed(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    val: Any = (message.text or "").strip()
    if data["efield"] == "price_points":
        if not val.isdigit() or int(val) <= 0:
            await message.answer("⚠️ أدخل رقماً صحيحاً أكبر من 0:", reply_markup=admin_cancel_home_kb())
            return
        val = int(val)
    ok, txt = await db.update_product(int(data["epid"]), data["efield"], val)
    await state.clear()
    await message.answer(f"{'✅' if ok else '⚠️'} {txt}", reply_markup=await admin_kb(db))


@router.callback_query(F.data.startswith("a_p_del:"))
async def cb_a_p_del(cb: CallbackQuery, db: Database) -> None:
    await db.delete_product(int(cb.data.split(":")[1]))
    await cb.answer("تم حذف المنتج نهائياً.", show_alert=True)
    await cb_a_pr_list(cb, db)


@router.callback_query(F.data == "a_inv")
async def cb_a_inv(cb: CallbackQuery, db: Database) -> None:
    auto_ps = [p for p in await db.get_all_products() if p["delivery_type"] == "auto"]
    rows = []
    for p in auto_ps:
        inv = await db.get_inventory_counts(int(p["id"]))
        rows.append([InlineKeyboardButton(text=f"📦 {p['name']} (متاح: {inv['available']})", callback_data=f"a_inv_a:{p['id']}")])
    rows.append([admin_home_btn()])
    await cb.message.edit_text("📦 <b>إدارة المخزون</b>:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()


@router.callback_query(F.data.startswith("a_inv_a:"))
async def cb_a_inv_a(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(ipid=int(cb.data.split(":")[1]))
    await state.set_state(AdminStates.add_stock)
    await cb.message.answer("➕ أرسل محتوى التسليم كاملاً كما هو (بكل أسطره):", reply_markup=admin_cancel_home_kb())
    await cb.answer()


@router.message(AdminStates.add_stock)
async def msg_a_inv_a(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    items = parse_delivery_payload(message.text or "")
    ok, txt, _ = await db.add_inventory(int(data["ipid"]), items)
    await state.clear()
    await message.answer(f"{'✅' if ok else '⚠️'} {txt}", reply_markup=await admin_kb(db))


@router.callback_query(F.data == "a_ord_p")
async def cb_a_ord_p(cb: CallbackQuery, db: Database) -> None:
    ords = await db.get_pending_orders()
    if not ords:
        await cb.message.edit_text("📥 لا توجد طلبات معلقة.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[admin_home_btn()]]))
        await cb.answer()
        return
    rows = []
    lines = ["📥 <b>الطلبات المعلقة:</b>"]
    for o in ords:
        lines.append(f"• <code>{o['order_uid']}</code> | {o['product_name']} | <code>{o['user_id']}</code> | {o['price_points']} ن")
        rows.append([
            InlineKeyboardButton(text=f"✅ قبول #{o['id']}", callback_data=f"a_o_acc:{o['id']}"),
            InlineKeyboardButton(text=f"❌ رفض #{o['id']}", callback_data=f"a_o_rej:{o['id']}"),
        ])
    rows.append([admin_home_btn()])
    await cb.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()


@router.callback_query(F.data.startswith("a_o_acc:"))
async def cb_a_o_acc(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(oid=int(cb.data.split(":")[1]))
    await state.set_state(AdminStates.ord_acc)
    await cb.message.answer(
        "✅ أرسل محتوى التسليم كاملاً كما هو لإرساله للمشتري وحذف المنتج نهائياً:",
        reply_markup=admin_cancel_home_kb(),
    )
    await cb.answer()


@router.message(AdminStates.ord_acc)
async def msg_a_o_acc(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    raw_content = (message.text or "").strip("\r\n")
    ok, txt, order = await db.approve_order(int(data["oid"]), raw_content)
    await state.clear()
    await message.answer(f"{'✅' if ok else '⚠️'} {txt}", reply_markup=await admin_kb(db))
    if ok and order and message.bot:
        try:
            await message.bot.send_message(
                int(order["user_id"]),
                f"🎉 تم قبول طلبك <code>{order['order_uid']}</code>:\n<pre>{raw_content}</pre>",
            )
        except Exception:
            pass


@router.callback_query(F.data.startswith("a_o_rej:"))
async def cb_a_o_rej(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(oid=int(cb.data.split(":")[1]))
    await state.set_state(AdminStates.ord_rej)
    await cb.message.answer("❌ أرسل سبب الرفض (سيتم إرجاع النقاط):", reply_markup=admin_cancel_home_kb())
    await cb.answer()


@router.message(AdminStates.ord_rej)
async def msg_a_o_rej(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    rsn = (message.text or "").strip() or "مرفوض"
    ok, txt, order = await db.reject_order(int(data["oid"]), rsn)
    await state.clear()
    await message.answer(f"{'✅' if ok else '⚠️'} {txt}", reply_markup=await admin_kb(db))
    if ok and order and message.bot:
        try:
            await message.bot.send_message(int(order["user_id"]), f"❌ تم رفض طلبك <code>{order['order_uid']}</code> وإرجاع <b>{order['price_points']}</b> نقطة. السبب: {rsn}")
        except Exception:
            pass


@router.callback_query(F.data.startswith("a_bal:"))
async def cb_a_bal(cb: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(bmode=cb.data.split(":")[1])
    await state.set_state(AdminStates.bal_uid)
    await cb.message.answer("🆔 أرسل Telegram ID للمستخدم:", reply_markup=admin_cancel_home_kb())
    await cb.answer()


@router.message(AdminStates.bal_uid)
async def msg_b_uid(message: Message, state: FSMContext, db: Database) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or not await db.get_user(int(raw)):
        await message.answer("⚠️ ID غير صحيح أو المستخدم غير موجود:", reply_markup=admin_cancel_home_kb())
        return
    await state.update_data(buid=int(raw))
    await state.set_state(AdminStates.bal_amt)
    await message.answer("💎 أرسل عدد النقاط:", reply_markup=admin_cancel_home_kb())


@router.message(AdminStates.bal_amt)
async def msg_b_amt(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("⚠️ أدخل رقماً صحيحاً أكبر من 0:", reply_markup=admin_cancel_home_kb())
        return
    await state.update_data(bamt=int(raw))
    await state.set_state(AdminStates.bal_rsn)
    await message.answer("📝 أرسل سبب العملية:", reply_markup=admin_cancel_home_kb())


@router.message(AdminStates.bal_rsn)
async def msg_b_rsn(message: Message, state: FSMContext, db: Database) -> None:
    data = await state.get_data()
    delta = int(data["bamt"]) if data["bmode"] == "add" else -int(data["bamt"])
    rsn = (message.text or "").strip() or "تعديل رصيد"
    ok, txt, u = await db.adjust_balance_admin(int(data["buid"]), delta, rsn)
    await state.clear()
    await message.answer(f"{'✅' if ok else '⚠️'} {txt}", reply_markup=await admin_kb(db))
    if ok and u and message.bot:
        try:
            await message.bot.send_message(int(data["buid"]), f"🔔 تم تحديث رصيدك بمقدار <b>{delta}</b> نقطة. الرصيد الجديد: <b>{u['balance']}</b> | السبب: {rsn}")
        except Exception:
            pass


@router.callback_query(F.data == "a_ref")
async def cb_a_ref(cb: CallbackQuery, state: FSMContext, db: Database) -> None:
    cur = await db.get_referral_reward()
    await state.set_state(AdminStates.set_ref)
    await cb.message.answer(
        f"🎁 المكافأة الحالية: <b>{cur}</b> نقطة لكل صديق. أرسل القيمة الجديدة:",
        reply_markup=admin_cancel_home_kb(),
    )
    await cb.answer()


@router.message(AdminStates.set_ref)
async def msg_a_ref(message: Message, state: FSMContext, db: Database) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("⚠️ أدخل رقماً صحيحاً:", reply_markup=admin_cancel_home_kb())
        return
    await db.set_setting("referral_reward", str(int(raw)))
    await state.clear()
    await message.answer(f"✅ تم تحديد مكافأة الإحالة: <b>{int(raw)}</b> نقطة.", reply_markup=await admin_kb(db))


@router.callback_query(F.data.startswith("a_users:"))
async def cb_a_users(cb: CallbackQuery, db: Database) -> None:
    page = int(cb.data.split(":")[1])
    users, total = await db.get_users_paginated(limit=10, offset=page * 10)
    lines = [f"👥 <b>المستخدمون ({total}):</b>"] + [f"• <code>{u['user_id']}</code> | {u['full_name']} | <b>{u['balance']}</b> ن" for u in users]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ السابق", callback_data=f"a_users:{page - 1}"))
    if (page + 1) * 10 < total:
        nav.append(InlineKeyboardButton(text="التالي ➡️", callback_data=f"a_users:{page + 1}"))
    rows = ([nav] if nav else []) + [[admin_home_btn()]]
    await cb.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()


@router.callback_query(F.data == "a_find")
async def cb_a_find(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.search_uid)
    await cb.message.answer("🔍 أرسل Telegram ID:", reply_markup=admin_cancel_home_kb())
    await cb.answer()


@router.message(AdminStates.search_uid)
async def msg_a_find(message: Message, state: FSMContext, db: Database) -> None:
    raw = (message.text or "").strip()
    await state.clear()
    u = await db.get_user(int(raw)) if raw.isdigit() else None
    if not u:
        await message.answer("❌ غير موجود.", reply_markup=await admin_kb(db))
        return
    await message.answer(
        f"👤 <b>{u['full_name']}</b> (<code>{u['user_id']}</code>)\n💰 الرصيد: <b>{u['balance']}</b> | 📈 المكتسبة: <b>{u['earned_points']}</b> | 📉 المصروفة: <b>{u['spent_points']}</b>",
        reply_markup=await admin_kb(db),
    )


@router.callback_query(F.data == "a_bcast")
async def cb_a_bcast(cb: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminStates.broadcast)
    await cb.message.answer("📣 أرسل نص الإشعار الجماعي:", reply_markup=admin_cancel_home_kb())
    await cb.answer()


@router.message(AdminStates.broadcast)
async def msg_a_bcast(message: Message, state: FSMContext, db: Database) -> None:
    txt = (message.text or "").strip()
    await state.clear()
    if not txt or not message.bot:
        return
    sent = 0
    for uid in await db.get_all_user_ids():
        try:
            await message.bot.send_message(uid, f"📢 <b>إشعار:</b>\n\n{txt}")
            sent += 1
        except Exception:
            pass
    await message.answer(f"✅ تم الإرسال إلى <b>{sent}</b> مستخدم.", reply_markup=await admin_kb(db))


@router.callback_query(F.data == "a_stats")
async def cb_a_stats(cb: CallbackQuery, db: Database) -> None:
    s = await db.get_stats()
    kb = InlineKeyboardMarkup(inline_keyboard=[[admin_home_btn()]])
    await cb.message.edit_text(
        f"📈 <b>إحصائيات النظام</b>\n\n"
        f"👥 المستخدمون: <b>{s['users']}</b> | 🔗 الإحالات: <b>{s['referrals']}</b>\n"
        f"📂 الأقسام: <b>{s['sections']}</b> | 🏷️ المنتجات: <b>{s['products']}</b>\n"
        f"📦 المخزون المتاح: <b>{s['inventory']}</b> | 📺 التمويلات النشطة: <b>{s['active_promos']}</b>\n"
        f"✅ الطلبات المكتملة: <b>{s['completed_orders']}</b> | ⏳ المعلقة: <b>{s['pending_orders']}</b>\n"
        f"💎 إجمالي المبيعات: <b>{s['sales_points']}</b> نقطة",
        reply_markup=kb,
    )
    await cb.answer()


# ================= تشغيل البوت =================
async def main() -> None:
    db = Database(DB_PATH)
    await db.init_db()

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp["db"] = db
    dp.include_router(router)

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
