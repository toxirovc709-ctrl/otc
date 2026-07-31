"""Gift Garant Bot — bitta fayldagi to'liq versiya.

Telegram sovg'a, kanal va akkaunt savdolari uchun garant (escrow) bot.

Ishga tushirish:
    pip install aiogram aiohttp aiosqlite SQLAlchemy pydantic pydantic-settings tonutils
    python bot.py

Sozlamalar shu papkadagi .env faylidan o'qiladi. Namuna uchun
README'ga qarang.

DIQQAT: mainnet'ga chiqishdan oldin testnet'da to'liq sinovdan
o'tkazing. .env faylini hech qachon git'ga qo'ymang.
"""
from __future__ import annotations


from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from decimal import Decimal
from decimal import InvalidOperation
from decimal import ROUND_UP
from html import escape
from pathlib import Path
import asyncio
import enum
import json
import logging
import os
import re
import secrets
import sys

from aiogram import Bot
from aiogram import Dispatcher
from aiogram import F
from aiogram import Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.filters import CommandObject
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State
from aiogram.fsm.state import StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery
from aiogram.types import InlineKeyboardButton
from aiogram.types import InlineKeyboardMarkup
from aiogram.types import Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from pydantic import Field
from pydantic import field_validator
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict
from sqlalchemy import BigInteger
from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import Enum
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import Numeric
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint
from sqlalchemy import event
from sqlalchemy import func
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship
from tonutils.client import ToncenterV3Client
from tonutils.wallet import WalletV5R1
import aiohttp


# ====================================================================
# SOZLAMALAR
# ====================================================================

BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Telegram ---
    bot_token: str
    admin_ids: str = ""

    # --- Database ---
    db_path: str = str(BASE_DIR / "data" / "guarant.db")

    # --- TON ---
    ton_api_base: str = "https://toncenter.com/api/v3"
    ton_api_key: str = ""
    # Hamyon manzili — bu yerga xaridorlar to'lov yuboradi
    escrow_wallet_address: str = ""
    # Chiqim uchun mnemonic. BOSH SERVERDA OCHIQ SAQLAMA.
    escrow_mnemonic: str = ""
    ton_is_testnet: bool = True

    # --- Komissiya ---
    fee_percent: Decimal = Decimal("3.0")
    fee_min_ton: Decimal = Decimal("0.5")
    # Katta bitimlarda pasaytirilgan stavka
    fee_large_threshold_ton: Decimal = Decimal("500")
    fee_large_percent: Decimal = Decimal("1.5")

    # --- Xavfsizlik chegaralari ---
    # Bitta bitimda ruxsat etilgan eng katta summa
    max_deal_ton: Decimal = Decimal("100")
    # 10 TON dan past bitimlarda minimal komissiya foizni haddan tashqari
    # oshirib yuboradi (1 TON'lik bitimda 50%), shuning uchun chegara shu.
    min_deal_ton: Decimal = Decimal("10")
    # Bir kunda avtomatik chiqariladigan eng katta umumiy summa
    daily_payout_cap_ton: Decimal = Decimal("500")
    # Shu summadan katta chiqimlar admin tasdig'ini kutadi
    manual_approval_above_ton: Decimal = Decimal("50")
    # Butun avtomatik chiqimni to'xtatuvchi kalit
    payouts_enabled: bool = True

    # --- Taymerlar (soniyada) ---
    payment_window_sec: int = 30 * 60
    gift_window_sec: int = 30 * 60
    watcher_interval_sec: int = 8

    # To'lov summasi mos kelishi uchun ruxsat etilgan farq
    amount_tolerance_ton: Decimal = Decimal("0.05")

    @field_validator("fee_percent", "fee_large_percent")
    @classmethod
    def _sane_percent(cls, v: Decimal) -> Decimal:
        if not (Decimal("0") <= v <= Decimal("15")):
            raise ValueError("Komissiya 0-15% oralig'ida bo'lishi kerak")
        return v

    @property
    def admins(self) -> set[int]:
        out: set[int] = set()
        for chunk in self.admin_ids.replace(";", ",").split(","):
            chunk = chunk.strip()
            if chunk.isdigit():
                out.add(int(chunk))
        return out


settings = Settings()  # type: ignore[call-arg]

# ====================================================================
# BAZA MODELLARI
# ====================================================================

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class DealStatus(str, enum.Enum):
    """Bitim holatlari.

    Oqim: DRAFT -> AWAITING_PAYMENT -> PAID -> AWAITING_GIFT
          -> GIFT_HELD -> GIFT_DELIVERED -> PAYOUT_PENDING -> COMPLETED

    Uzilishlar: EXPIRED (to'lov kelmadi), REFUND_PENDING/REFUNDED
                (sovg'a kelmadi), DISPUTED (qo'lda hal qilinadi).
    """

    DRAFT = "draft"
    AWAITING_PAYMENT = "awaiting_payment"
    PAID = "paid"
    AWAITING_GIFT = "awaiting_gift"
    GIFT_HELD = "gift_held"
    GIFT_DELIVERED = "gift_delivered"
    PAYOUT_PENDING = "payout_pending"
    COMPLETED = "completed"
    EXPIRED = "expired"
    REFUND_PENDING = "refund_pending"
    REFUNDED = "refunded"
    DISPUTED = "disputed"
    CANCELLED = "cancelled"


class DealKind(str, enum.Enum):
    GIFT = "gift"
    CHANNEL = "channel"
    ACCOUNT = "account"


class TransferMode(str, enum.Enum):
    #: Sovg'a botning akkauntiga kelib, keyin xaridorga uzatiladi
    CUSTODY = "custody"
    #: Sovg'a sotuvchida qoladi, bot biznes ulanish orqali uzatadi
    BUSINESS = "business"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # telegram id
    username: Mapped[str | None] = mapped_column(String(64))
    language: Mapped[str] = mapped_column(String(8), default="uz")
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)

    referrer_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    referral_balance: Mapped[float] = mapped_column(Numeric(20, 9), default=0)

    deals_completed: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    wallets: Mapped[list["Wallet"]] = relationship(back_populates="user")


class Wallet(Base):
    __tablename__ = "wallets"
    __table_args__ = (UniqueConstraint("user_id", "address", name="uq_user_wallet"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    address: Mapped[str] = mapped_column(String(80))
    label: Mapped[str | None] = mapped_column(String(64))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="wallets")


class Deal(Base):
    __tablename__ = "deals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    #: To'lovni bitimga bog'lash uchun unikal comment. Bu qiymat
    #: takrorlanmasligi kerak, aks holda pul boshqa bitimga yoziladi.
    payment_code: Mapped[str] = mapped_column(String(16), unique=True, index=True)

    kind: Mapped[DealKind] = mapped_column(Enum(DealKind), default=DealKind.GIFT)
    status: Mapped[DealStatus] = mapped_column(
        Enum(DealStatus), default=DealStatus.DRAFT, index=True
    )
    transfer_mode: Mapped[TransferMode] = mapped_column(
        Enum(TransferMode), default=TransferMode.CUSTODY
    )

    seller_id: Mapped[int] = mapped_column(BigInteger, index=True)
    buyer_id: Mapped[int | None] = mapped_column(BigInteger, index=True)

    description: Mapped[str] = mapped_column(Text)

    #: Sotuvchi oladigan sof summa
    amount_ton: Mapped[float] = mapped_column(Numeric(20, 9))
    #: Bizning komissiya
    fee_ton: Mapped[float] = mapped_column(Numeric(20, 9))
    #: Xaridor to'laydigan umumiy summa = amount + fee
    total_ton: Mapped[float] = mapped_column(Numeric(20, 9))

    seller_wallet: Mapped[str | None] = mapped_column(String(80))
    #: Pul qaysi manzildan keldi — qaytarish faqat shu manzilga ketadi
    buyer_wallet: Mapped[str | None] = mapped_column(String(80))

    #: Sovg'ani identifikatsiya qilish uchun
    gift_slug: Mapped[str | None] = mapped_column(String(128))
    owned_gift_id: Mapped[str | None] = mapped_column(String(128))
    business_connection_id: Mapped[str | None] = mapped_column(String(128))

    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ProcessedTx(Base):
    """Blokcheyndan o'qilgan har bir tranzaksiya shu yerga yoziladi.

    Bu jadval bitta maqsad uchun: bitta to'lovni ikki marta hisoblab
    yubormaslik. Hash bo'yicha unikal cheklov — himoyaning asosi.
    """

    __tablename__ = "processed_tx"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tx_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    deal_id: Mapped[int | None] = mapped_column(ForeignKey("deals.id"), index=True)
    from_address: Mapped[str | None] = mapped_column(String(80))
    amount_ton: Mapped[float] = mapped_column(Numeric(20, 9))
    comment: Mapped[str | None] = mapped_column(String(256))
    #: Bitimga bog'lanmagan to'lovlar shu yerda "orphan" bo'lib qoladi
    matched: Mapped[bool] = mapped_column(Boolean, default=False)
    lt: Mapped[int | None] = mapped_column(BigInteger, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Payout(Base):
    """Chiqim yozuvi. Har doim TON yuborishdan OLDIN yaratiladi.

    Agar bot yuborish paytida qulasa, qayta ishga tushganda bu yozuv
    'sending' holatida turadi va qo'lda tekshiriladi — pul ikki marta
    ketmaydi.
    """

    __tablename__ = "payouts"
    __table_args__ = (
        UniqueConstraint("deal_id", "purpose", name="uq_deal_purpose"),
        Index("ix_payout_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("deals.id"), index=True)
    #: "payout" (sotuvchiga) yoki "refund" (xaridorga)
    purpose: Mapped[str] = mapped_column(String(16))
    to_address: Mapped[str] = mapped_column(String(80))
    amount_ton: Mapped[float] = mapped_column(Numeric(20, 9))
    #: pending -> approved -> sending -> sent | failed
    status: Mapped[str] = mapped_column(String(16), default="pending")
    tx_hash: Mapped[str | None] = mapped_column(String(128))
    error: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DealEvent(Base):
    """Bitimning to'liq tarixi. Nizo chiqqanda yagona dalil shu."""

    __tablename__ = "deal_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deal_id: Mapped[int] = mapped_column(ForeignKey("deals.id"), index=True)
    event: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    payload: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

# ====================================================================
# BAZAGA ULANISH
# ====================================================================

os.makedirs(os.path.dirname(settings.db_path), exist_ok=True)

engine = create_async_engine(
    f"sqlite+aiosqlite:///{settings.db_path}",
    echo=False,
    pool_pre_ping=True,
)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _record) -> None:
    cursor = dbapi_connection.cursor()
    # WAL — parallel o'qish uchun; watcher va handlerlar bir vaqtda ishlaydi
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


session_factory = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("PRAGMA journal_mode=WAL"))

# ====================================================================
# PREMIUM EMOJI
# ====================================================================

log = logging.getLogger(__name__)

_CONFIG = Path(__file__).resolve().parent / "premium_emoji.json"

#: Zaxira emojilar. Premium ID topilmasa yoki foydalanuvchida Premium
#: bo'lmasa, aynan shular ko'rinadi — ya'ni bot hech qachon "buzilgan"
#: holatga tushmaydi.
FALLBACK: dict[str, str] = {
    "shield": "🛡",
    "gift": "🎁",
    "wallet": "👛",
    "money": "💰",
    "ton": "💎",
    "check": "✅",
    "cross": "❌",
    "clock": "⏳",
    "warning": "⚠️",
    "handshake": "🤝",
    "rocket": "🚀",
    "star": "⭐️",
    "user": "👤",
    "settings": "⚙️",
    "back": "◀️",
    "channel": "📢",
    "account": "🆔",
    "list": "📋",
    "link": "🔗",
    "fire": "🔥",
    "lock": "🔒",
    "bell": "🔔",
    "globe": "🌐",
    "info": "ℹ️",
}

_ids: dict[str, str] = {}


def load_ids() -> None:
    """premium_emoji.json dan custom emoji ID'larini o'qiydi.

    Fayl bo'lmasa yoki buzuq bo'lsa — bot baribir ishlaydi, faqat
    oddiy emojilar bilan. Emoji tufayli xizmat to'xtamasligi kerak.
    """
    global _ids
    if not _CONFIG.exists():
        log.info("premium_emoji.json topilmadi, oddiy emojilar ishlatiladi")
        return
    try:
        data = json.loads(_CONFIG.read_text(encoding="utf-8"))
        _ids = {k: str(v) for k, v in data.items() if v}
        log.info("%s ta premium emoji yuklandi", len(_ids))
    except Exception:
        log.exception("premium_emoji.json o'qilmadi")


def e(name: str) -> str:
    """Premium emoji HTML tegini qaytaradi.

    Foydalanish: `f"{e('shield')} Xavfsiz bitim"` va parse_mode="HTML".

    Premium'i yo'q foydalanuvchi teg ichidagi oddiy emojini ko'radi —
    Telegram buni avtomatik hal qiladi.
    """
    fallback = FALLBACK.get(name, "•")
    emoji_id = _ids.get(name)
    if not emoji_id:
        return fallback
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def plain(name: str) -> str:
    """Tugma matnlari uchun — tugmalarda custom emoji ishlamaydi."""
    return FALLBACK.get(name, "•")


def esc(text: str) -> str:
    """Foydalanuvchi kiritgan matnni HTML uchun xavfsizlaydi.

    Buni O'TKAZIB YUBORMA: tavsifga `<b>` yozgan odam xabar
    formatlashini buzadi, yomonrog'i — soxta matn ko'rsatishi mumkin.
    """
    return escape(text, quote=False)

# ====================================================================
# MATNLAR (UZ / RU / EN)
# ====================================================================

LANGS = {"uz": "🇺🇿 O'zbekcha", "ru": "🇷🇺 Русский", "en": "🇬🇧 English"}
DEFAULT_LANG = "uz"

TEXTS: dict[str, dict[str, str]] = {
    "choose_language": {
        "uz": "Botdan foydalanishdan oldin tilni tanlang:",
        "ru": "Выберите язык бота перед началом использования:",
        "en": "Choose the bot's language before you start:",
    },
    "welcome": {
        "uz": (
            "{shield} <b>Gift Garant</b>\n\n"
            "Sovg'a, kanal va akkaunt savdolarida o'rtada turamiz — "
            "pul bizda saqlanadi, mol yetkazilgach sotuvchiga o'tadi.\n\n"
            "{ton} Komissiya: <b>{fee}%</b> (eng kami {fee_min} TON)\n"
            "{lock} Bitim chegarasi: {max_deal} TON gacha\n"
            "{clock} Har bosqichda taymer bor\n\n"
            "<i>Sovg'a haqiqatan o'tganini bot o'zi tekshiradi — "
            "hech kim hech narsani \"tasdiqlash\" bilan aldab bo'lmaydi.</i>"
        ),
        "ru": (
            "{shield} <b>Gift Garant</b>\n\n"
            "Мы выступаем гарантом в сделках с подарками, каналами и "
            "аккаунтами — деньги хранятся у нас и уходят продавцу только "
            "после доставки.\n\n"
            "{ton} Комиссия: <b>{fee}%</b> (минимум {fee_min} TON)\n"
            "{lock} Лимит сделки: до {max_deal} TON\n"
            "{clock} На каждом этапе есть таймер\n\n"
            "<i>Бот сам проверяет факт перевода подарка — подтверждением "
            "на словах обмануть невозможно.</i>"
        ),
        "en": (
            "{shield} <b>Gift Guarant</b>\n\n"
            "We hold the middle ground in gift, channel and account deals — "
            "funds stay with us and reach the seller only after delivery.\n\n"
            "{ton} Fee: <b>{fee}%</b> (minimum {fee_min} TON)\n"
            "{lock} Deal limit: up to {max_deal} TON\n"
            "{clock} Every stage has a timer\n\n"
            "<i>The bot verifies the actual transfer itself — no one can "
            "cheat by simply claiming it happened.</i>"
        ),
    },
    "menu_wallet": {"uz": "Hamyonim", "ru": "Мой кошелёк", "en": "My wallet"},
    "menu_create": {"uz": "Bitim yaratish", "ru": "Создать сделку", "en": "Create deal"},
    "menu_deals": {"uz": "Bitimlarim", "ru": "Мои сделки", "en": "My deals"},
    "menu_settings": {"uz": "Sozlamalar", "ru": "Настройки", "en": "Settings"},
    "menu_back": {"uz": "Menyuga", "ru": "В меню", "en": "To menu"},
    "wallet_title": {
        "uz": "{wallet} <b>Hamyonim</b>\n\nRo'yxatdan tanlang yoki yangisini qo'shing:",
        "ru": "{wallet} <b>Мой кошелёк</b>\n\nВыберите из списка или добавьте новый:",
        "en": "{wallet} <b>My wallet</b>\n\nChoose from the list or add a new one:",
    },
    "wallet_empty": {
        "uz": "Hozircha bo'sh",
        "ru": "Пока пусто",
        "en": "Nothing here",
    },
    "wallet_add": {"uz": "Hamyon qo'shish", "ru": "Добавить кошелёк", "en": "Add wallet"},
    "wallet_ask_address": {
        "uz": "{wallet} <b>Hamyon qo'shish</b>\n\nQo'shmoqchi bo'lgan <i>TON</i> hamyon manzilini yuboring:",
        "ru": "{wallet} <b>Добавление кошелька</b>\n\nОтправьте адрес <i>TON</i> кошелька:",
        "en": "{wallet} <b>Adding wallet</b>\n\nSend the address of the <i>TON</i> wallet:",
    },
    "wallet_bad_address": {
        "uz": "{cross} Bu TON manziliga o'xshamaydi. Qayta yuboring.",
        "ru": "{cross} Это не похоже на TON-адрес. Отправьте ещё раз.",
        "en": "{cross} That doesn't look like a TON address. Try again.",
    },
    "wallet_added": {
        "uz": "{check} Hamyon qo'shildi:\n<code>{address}</code>",
        "ru": "{check} Кошелёк добавлен:\n<code>{address}</code>",
        "en": "{check} Wallet added:\n<code>{address}</code>",
    },
    "deal_choose_type": {
        "uz": "{list} <b>Bitim turini tanlang</b>",
        "ru": "{list} <b>Выберите тип сделки</b>",
        "en": "{list} <b>Choose type of deal</b>",
    },
    "type_gift": {"uz": "Sovg'alar", "ru": "Подарки", "en": "Gifts"},
    "type_channel": {"uz": "Kanal", "ru": "Канал", "en": "Channel"},
    "type_account": {"uz": "Akkaunt", "ru": "Аккаунт", "en": "Account"},
    "deal_ask_description": {
        "uz": (
            "{handshake} <b>Bitim yaratish</b>\n\n"
            "Nima taklif qilayotganingizni yozing.\n"
            "Masalan: <i>Plush Pepe, Cap fon</i>"
        ),
        "ru": (
            "{handshake} <b>Создание сделки</b>\n\n"
            "Укажите, что вы предлагаете в сделке.\n"
            "Например: <i>Plush Pepe, фон Cap</i>"
        ),
        "en": (
            "{handshake} <b>Creating a deal</b>\n\n"
            "Specify what you are offering in the deal.\n"
            "For example: <i>Plush Pepe, Cap background</i>"
        ),
    },
    "deal_ask_amount": {
        "uz": "{ton} Narxni <b>TON</b> da yozing (masalan <code>25.5</code>):",
        "ru": "{ton} Укажите цену в <b>TON</b> (например <code>25.5</code>):",
        "en": "{ton} Enter the price in <b>TON</b> (e.g. <code>25.5</code>):",
    },
    "deal_bad_amount": {
        "uz": "{cross} Summani raqam bilan yozing. Masalan: <code>25.5</code>",
        "ru": "{cross} Введите сумму числом. Например: <code>25.5</code>",
        "en": "{cross} Enter the amount as a number. For example: <code>25.5</code>",
    },
    "deal_ask_wallet": {
        "uz": "{wallet} Pul qaysi hamyonga tushsin? Manzilni yuboring:",
        "ru": "{wallet} На какой кошелёк отправить деньги? Отправьте адрес:",
        "en": "{wallet} Which wallet should receive the funds? Send the address:",
    },
    "deal_created": {
        "uz": (
            "{check} <b>Bitim #{id} yaratildi</b>\n\n"
            "{gift} {description}\n"
            "{ton} Sotuvchi oladi: <b>{amount} TON</b>\n"
            "{money} Xaridor to'laydi: <b>{total} TON</b>\n"
            "<i>(komissiya {fee} TON)</i>\n\n"
            "Quyidagi havolani xaridorga yuboring:\n{link}\n\n"
            "{warning} Bitim boshlangach 30 daqiqa ichida sovg'ani "
            "o'tkazishingiz kerak, aks holda pul xaridorga qaytadi."
        ),
        "ru": (
            "{check} <b>Сделка #{id} создана</b>\n\n"
            "{gift} {description}\n"
            "{ton} Продавец получит: <b>{amount} TON</b>\n"
            "{money} Покупатель платит: <b>{total} TON</b>\n"
            "<i>(комиссия {fee} TON)</i>\n\n"
            "Отправьте покупателю эту ссылку:\n{link}\n\n"
            "{warning} После начала сделки у вас 30 минут на перевод "
            "подарка, иначе деньги вернутся покупателю."
        ),
        "en": (
            "{check} <b>Deal #{id} created</b>\n\n"
            "{gift} {description}\n"
            "{ton} Seller receives: <b>{amount} TON</b>\n"
            "{money} Buyer pays: <b>{total} TON</b>\n"
            "<i>(fee {fee} TON)</i>\n\n"
            "Send this link to the buyer:\n{link}\n\n"
            "{warning} Once the deal starts you have 30 minutes to transfer "
            "the gift, otherwise the funds return to the buyer."
        ),
    },
    "deal_payment_instructions": {
        "uz": (
            "{money} <b>Bitim #{id} — to'lov</b>\n\n"
            "{gift} {description}\n"
            "Sotuvchi: {seller}\n\n"
            "Quyidagi manzilga <b>aniq {total} TON</b> yuboring:\n"
            "<code>{address}</code>\n\n"
            "{warning} <b>Izoh (comment) maydoniga albatta yozing:</b>\n"
            "<code>{code}</code>\n\n"
            "<i>Izohsiz yuborilgan pul avtomatik topilmaydi va qo'lda "
            "qidirishga to'g'ri keladi.</i>\n\n"
            "{clock} Muddat: {minutes} daqiqa"
        ),
        "ru": (
            "{money} <b>Сделка #{id} — оплата</b>\n\n"
            "{gift} {description}\n"
            "Продавец: {seller}\n\n"
            "Отправьте <b>ровно {total} TON</b> на адрес:\n"
            "<code>{address}</code>\n\n"
            "{warning} <b>Обязательно укажите в комментарии:</b>\n"
            "<code>{code}</code>\n\n"
            "<i>Без комментария платёж не будет найден автоматически.</i>\n\n"
            "{clock} Срок: {minutes} минут"
        ),
        "en": (
            "{money} <b>Deal #{id} — payment</b>\n\n"
            "{gift} {description}\n"
            "Seller: {seller}\n\n"
            "Send <b>exactly {total} TON</b> to:\n"
            "<code>{address}</code>\n\n"
            "{warning} <b>You must include this comment:</b>\n"
            "<code>{code}</code>\n\n"
            "<i>Without the comment the payment won't be matched "
            "automatically.</i>\n\n"
            "{clock} Time limit: {minutes} minutes"
        ),
    },
    "notify_seller_paid": {
        "uz": (
            "{check} <b>Bitim #{id}: to'lov keldi!</b>\n\n"
            "Xaridor {total} TON to'ladi. Pul bizda saqlanmoqda.\n\n"
            "{gift} Endi sovg'ani o'tkazing.\n"
            "{clock} Muddat: {minutes} daqiqa\n\n"
            "{warning} Ulgurmasangiz pul xaridorga qaytariladi."
        ),
        "ru": (
            "{check} <b>Сделка #{id}: оплата получена!</b>\n\n"
            "Покупатель внёс {total} TON. Деньги у нас.\n\n"
            "{gift} Теперь переведите подарок.\n"
            "{clock} Срок: {minutes} минут\n\n"
            "{warning} Если не успеете, деньги вернутся покупателю."
        ),
        "en": (
            "{check} <b>Deal #{id}: payment received!</b>\n\n"
            "The buyer paid {total} TON. Funds are held by us.\n\n"
            "{gift} Now transfer the gift.\n"
            "{clock} Time limit: {minutes} minutes\n\n"
            "{warning} If you miss it, funds return to the buyer."
        ),
    },
    "deal_completed_buyer": {
        "uz": "{check} <b>Bitim #{id} yakunlandi!</b>\n\n{gift} Sovg'a sizga o'tkazildi.",
        "ru": "{check} <b>Сделка #{id} завершена!</b>\n\n{gift} Подарок переведён вам.",
        "en": "{check} <b>Deal #{id} completed!</b>\n\n{gift} The gift has been transferred to you.",
    },
    "deal_completed_seller": {
        "uz": "{check} <b>Bitim #{id} yakunlandi!</b>\n\n{ton} {amount} TON hamyoningizga yuborildi.",
        "ru": "{check} <b>Сделка #{id} завершена!</b>\n\n{ton} {amount} TON отправлены на ваш кошелёк.",
        "en": "{check} <b>Deal #{id} completed!</b>\n\n{ton} {amount} TON sent to your wallet.",
    },
    "deal_refunded": {
        "uz": (
            "{warning} <b>Bitim #{id} bekor qilindi</b>\n\n"
            "Sotuvchi belgilangan vaqtda sovg'ani o'tkazmadi. "
            "Pul hamyoningizga qaytarilmoqda."
        ),
        "ru": (
            "{warning} <b>Сделка #{id} отменена</b>\n\n"
            "Продавец не перевёл подарок в срок. Деньги возвращаются "
            "на ваш кошелёк."
        ),
        "en": (
            "{warning} <b>Deal #{id} cancelled</b>\n\n"
            "The seller did not transfer the gift in time. Funds are being "
            "returned to your wallet."
        ),
    },
    "deal_expired": {
        "uz": "{clock} Bitim #{id} muddati tugadi — to'lov kelmadi.",
        "ru": "{clock} Срок сделки #{id} истёк — оплата не поступила.",
        "en": "{clock} Deal #{id} expired — no payment received.",
    },
    "deals_empty": {
        "uz": "{list} Sizda hali bitim yo'q.",
        "ru": "{list} У вас пока нет сделок.",
        "en": "{list} You have no deals yet.",
    },
    "settings_title": {
        "uz": "{settings} <b>Sozlamalar</b>",
        "ru": "{settings} <b>Настройки</b>",
        "en": "{settings} <b>Settings</b>",
    },
    "settings_referrals": {"uz": "Referallar", "ru": "Рефералы", "en": "Referrals"},
    "settings_language": {"uz": "Bot tili", "ru": "Язык бота", "en": "Bot language"},
    "referral_info": {
        "uz": (
            "{user} <b>Referal tizimi</b>\n\n"
            "Sizning ulushingiz: <b>{percent}%</b>\n"
            "Taklif qilinganlar: <b>{count}</b>\n"
            "Balans: <b>{balance} TON</b>\n\n"
            "{link} Havolangiz:\n{url}"
        ),
        "ru": (
            "{user} <b>Реферальная система</b>\n\n"
            "Ваш процент: <b>{percent}%</b>\n"
            "Приглашено: <b>{count}</b>\n"
            "Баланс: <b>{balance} TON</b>\n\n"
            "{link} Ваша ссылка:\n{url}"
        ),
        "en": (
            "{user} <b>Referral system</b>\n\n"
            "Your percentage: <b>{percent}%</b>\n"
            "Users invited: <b>{count}</b>\n"
            "Balance: <b>{balance} TON</b>\n\n"
            "{link} Your link:\n{url}"
        ),
    },
    "error_generic": {
        "uz": "{cross} Xatolik yuz berdi. Qayta urinib ko'ring.",
        "ru": "{cross} Произошла ошибка. Попробуйте снова.",
        "en": "{cross} Something went wrong. Please try again.",
    },
    "deal_not_found": {
        "uz": "{cross} Bitim topilmadi yoki muddati tugagan.",
        "ru": "{cross} Сделка не найдена или истекла.",
        "en": "{cross} Deal not found or expired.",
    },
    "deal_own": {
        "uz": "{cross} O'z bitimingizga xaridor bo'la olmaysiz.",
        "ru": "{cross} Вы не можете быть покупателем своей сделки.",
        "en": "{cross} You cannot be the buyer in your own deal.",
    },
}


def t(key: str, lang: str = DEFAULT_LANG, **kwargs) -> str:
    entry = TEXTS.get(key)
    if entry is None:
        return key
    template = entry.get(lang) or entry.get(DEFAULT_LANG) or key
    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError):
            return template
    return template

# ====================================================================
# TUGMALAR
# ====================================================================

def language_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for code, label in LANGS.items():
        kb.button(text=label, callback_data=f"lang:{code}")
    kb.adjust(3)
    return kb.as_markup()


def main_menu_kb(lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=f"{plain('wallet')} {t('menu_wallet', lang)}", callback_data="wallet")
    kb.button(
        text=f"{plain('handshake')} {t('menu_create', lang)}", callback_data="deal:new"
    )
    kb.button(text=f"{plain('list')} {t('menu_deals', lang)}", callback_data="deals")
    kb.button(
        text=f"{plain('settings')} {t('menu_settings', lang)}", callback_data="settings"
    )
    kb.adjust(1, 1, 2)
    return kb.as_markup()


def back_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{plain('back')} {t('menu_back', lang)}",
                    callback_data="menu",
                )
            ]
        ]
    )


def wallet_kb(lang: str, wallets: list) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if wallets:
        for w in wallets:
            short = f"{w.address[:6]}…{w.address[-4:]}"
            kb.button(text=f"{plain('ton')} {short}", callback_data=f"wallet:use:{w.id}")
    else:
        kb.button(text=f"⚪️ {t('wallet_empty', lang)}", callback_data="noop")
    kb.button(text=f"{plain('wallet')} {t('wallet_add', lang)}", callback_data="wallet:add")
    kb.button(text=f"{plain('back')} {t('menu_back', lang)}", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()


def deal_type_kb(lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text=f"{plain('gift')} {t('type_gift', lang)}", callback_data="deal:kind:gift")
    kb.button(
        text=f"{plain('channel')} {t('type_channel', lang)}",
        callback_data="deal:kind:channel",
    )
    kb.button(
        text=f"{plain('account')} {t('type_account', lang)}",
        callback_data="deal:kind:account",
    )
    kb.button(text=f"{plain('back')} {t('menu_back', lang)}", callback_data="menu")
    kb.adjust(2, 1, 1)
    return kb.as_markup()


def settings_kb(lang: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(
        text=f"{plain('user')} {t('settings_referrals', lang)}", callback_data="referrals"
    )
    kb.button(
        text=f"{plain('globe')} {t('settings_language', lang)}", callback_data="language"
    )
    kb.button(text=f"{plain('back')} {t('menu_back', lang)}", callback_data="menu")
    kb.adjust(2, 1)
    return kb.as_markup()


def share_deal_kb(lang: str, bot_username: str, code: str) -> InlineKeyboardMarkup:
    url = f"https://t.me/{bot_username}?start=deal_{code}"
    kb = InlineKeyboardBuilder()
    kb.button(
        text=f"{plain('link')} Ulashish",
        switch_inline_query=f"\n{url}",
    )
    kb.button(text=f"{plain('back')} {t('menu_back', lang)}", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()

# ====================================================================
# BITIM XIZMATI
# ====================================================================

log = logging.getLogger(__name__)

TON = Decimal("0.000000001")


class DealError(Exception):
    """Foydalanuvchiga ko'rsatiladigan xato."""


def calculate_fee(amount: Decimal) -> Decimal:
    """Komissiyani hisoblaydi.

    Kichik bitimlarda foiz gaz haqini ham qoplamaydi, shuning uchun
    minimal chegara bor. Katta bitimlarda stavka pasayadi — aks holda
    yirik savdolar raqobatchiga ketadi.
    """
    if amount >= settings.fee_large_threshold_ton:
        rate = settings.fee_large_percent
    else:
        rate = settings.fee_percent

    fee = (amount * rate / Decimal("100")).quantize(Decimal("0.001"), rounding=ROUND_UP)
    return max(fee, settings.fee_min_ton)


def validate_amount(amount: Decimal) -> None:
    if amount < settings.min_deal_ton:
        raise DealError(f"Eng kam summa: {settings.min_deal_ton} TON")
    if amount > settings.max_deal_ton:
        raise DealError(
            f"Hozircha eng katta summa: {settings.max_deal_ton} TON. "
            "Kattaroq bitim uchun qo'llab-quvvatlashga yozing."
        )


async def create_deal(
    session: AsyncSession,
    *,
    seller_id: int,
    description: str,
    amount: Decimal,
    kind: DealKind = DealKind.GIFT,
    seller_wallet: str | None = None,
    transfer_mode: TransferMode = TransferMode.CUSTODY,
) -> Deal:
    validate_amount(amount)

    fee = calculate_fee(amount)
    total = (amount + fee).quantize(Decimal("0.000000001"))

    for _ in range(5):
        code = secrets.token_hex(4)  # 8 belgi, taxmin qilib bo'lmaydi
        deal = Deal(
            payment_code=code,
            kind=kind,
            status=DealStatus.DRAFT,
            transfer_mode=transfer_mode,
            seller_id=seller_id,
            description=description[:2000],
            amount_ton=amount,
            fee_ton=fee,
            total_ton=total,
            seller_wallet=seller_wallet,
        )
        session.add(deal)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            continue
        session.add(
            DealEvent(deal_id=deal.id, event="created", actor_id=seller_id)
        )
        return deal

    raise DealError("Bitim yaratilmadi, qayta urinib ko'ring")


async def attach_buyer(
    session: AsyncSession, deal_id: int, buyer_id: int
) -> Deal:
    """Xaridorni bitimga bog'laydi va to'lov kutish holatiga o'tkazadi."""
    deal = (
        await session.execute(select(Deal).where(Deal.id == deal_id))
    ).scalar_one_or_none()

    if deal is None:
        raise DealError("Bitim topilmadi")
    if deal.seller_id == buyer_id:
        raise DealError("O'z bitimingizga qo'shila olmaysiz")
    if deal.status != DealStatus.DRAFT:
        raise DealError("Bu bitimga qo'shilib bo'lmaydi")

    result = await session.execute(
        update(Deal)
        .where(Deal.id == deal_id, Deal.status == DealStatus.DRAFT)
        .values(
            buyer_id=buyer_id,
            status=DealStatus.AWAITING_PAYMENT,
            expires_at=utcnow() + timedelta(seconds=settings.payment_window_sec),
            updated_at=utcnow(),
        )
    )
    if result.rowcount == 0:
        raise DealError("Bitim holati o'zgargan, yangilang")

    session.add(DealEvent(deal_id=deal_id, event="buyer_joined", actor_id=buyer_id))
    await session.refresh(deal)
    return deal


async def transition(
    session: AsyncSession,
    deal_id: int,
    *,
    expected: DealStatus,
    new: DealStatus,
    actor_id: int | None = None,
    event: str | None = None,
    payload: str | None = None,
    **extra,
) -> bool:
    """Holatni atomik o'zgartiradi.

    `expected` holati mos kelmasa, hech narsa o'zgarmaydi va False
    qaytadi. Bu ikkita parallel jarayon bir bitimni bir vaqtda
    o'zgartirishidan himoya qiladi — kod boshqa hech qayerda
    `deal.status = ...` deb to'g'ridan-to'g'ri yozmasligi kerak.
    """
    values = {"status": new, "updated_at": utcnow(), **extra}
    result = await session.execute(
        update(Deal).where(Deal.id == deal_id, Deal.status == expected).values(**values)
    )
    if result.rowcount == 0:
        log.warning(
            "Bitim #%s: %s -> %s o'tishi rad etildi", deal_id, expected, new
        )
        return False

    session.add(
        DealEvent(
            deal_id=deal_id,
            event=event or f"{expected.value}->{new.value}",
            actor_id=actor_id,
            payload=payload,
        )
    )
    return True


async def expire_stale_deals(session: AsyncSession) -> list[tuple[int, DealStatus]]:
    """Muddati o'tgan bitimlarni yopadi.

    To'lov kelmagan bo'lsa — shunchaki bekor qilinadi.
    To'lov kelgan, lekin sovg'a kelmagan bo'lsa — qaytarish navbatiga
    qo'yiladi. Bu ikki holatni aralashtirib yubormaslik juda muhim.
    """
    now = utcnow()
    affected: list[tuple[int, DealStatus]] = []

    stale = (
        await session.execute(
            select(Deal).where(
                Deal.expires_at.is_not(None),
                Deal.expires_at < now,
                Deal.status.in_(
                    [DealStatus.AWAITING_PAYMENT, DealStatus.AWAITING_GIFT]
                ),
            )
        )
    ).scalars().all()

    for deal in stale:
        if deal.status == DealStatus.AWAITING_PAYMENT:
            ok = await transition(
                session,
                deal.id,
                expected=DealStatus.AWAITING_PAYMENT,
                new=DealStatus.EXPIRED,
                event="expired_no_payment",
            )
            if ok:
                affected.append((deal.id, DealStatus.EXPIRED))
        else:
            # Pul bizda, sovg'a kelmadi -> xaridorga qaytariladi
            ok = await transition(
                session,
                deal.id,
                expected=DealStatus.AWAITING_GIFT,
                new=DealStatus.REFUND_PENDING,
                event="expired_no_gift",
            )
            if ok:
                affected.append((deal.id, DealStatus.REFUND_PENDING))

    return affected

# ====================================================================
# CHIQIM XIZMATI
# ====================================================================

log = logging.getLogger(__name__)


class PayoutError(Exception):
    pass


async def _daily_sent_total(session: AsyncSession) -> Decimal:
    since = utcnow() - timedelta(days=1)
    total = (
        await session.execute(
            select(func.coalesce(func.sum(Payout.amount_ton), 0)).where(
                Payout.status.in_(["sending", "sent"]),
                Payout.created_at >= since,
            )
        )
    ).scalar()
    return Decimal(str(total or 0))


async def queue_payout(
    session: AsyncSession,
    *,
    deal_id: int,
    purpose: str,
    to_address: str,
    amount: Decimal,
) -> Payout | None:
    """Chiqimni navbatga qo'yadi.

    `(deal_id, purpose)` unikal — bitta bitim uchun ikkinchi marta
    to'lov yozuvi yaratilmaydi. Bu takroriy chiqimdan asosiy himoya.

    Yozuv darhol yuborilmaydi: avval bazaga tushadi, keyin alohida
    ishchi uni oladi. Shu tufayli bot yuborish paytida qulasa ham
    holat bazada saqlanib qoladi.
    """
    if not to_address:
        raise PayoutError("Manzil ko'rsatilmagan")

    status = "pending"
    if amount > settings.manual_approval_above_ton:
        status = "needs_approval"
        log.warning(
            "Bitim #%s: %s TON chiqimi admin tasdig'ini kutmoqda", deal_id, amount
        )

    payout = Payout(
        deal_id=deal_id,
        purpose=purpose,
        to_address=to_address,
        amount_ton=amount,
        status=status,
    )
    session.add(payout)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        log.warning("Bitim #%s uchun '%s' chiqimi allaqachon mavjud", deal_id, purpose)
        return None

    session.add(
        DealEvent(
            deal_id=deal_id,
            event=f"payout_queued:{purpose}",
            payload=f"{amount} TON -> {to_address}",
        )
    )
    return payout


async def claim_next_payout(session: AsyncSession) -> Payout | None:
    """Navbatdan bitta chiqimni oladi va 'sending' holatiga o'tkazadi.

    Shartli UPDATE tufayli bitta yozuvni ikkita ishchi ola olmaydi.
    """
    if not settings.payouts_enabled:
        return None

    candidate = (
        await session.execute(
            select(Payout).where(Payout.status == "pending").order_by(Payout.id).limit(1)
        )
    ).scalar_one_or_none()

    if candidate is None:
        return None

    sent_today = await _daily_sent_total(session)
    if sent_today + Decimal(str(candidate.amount_ton)) > settings.daily_payout_cap_ton:
        log.error(
            "Kunlik chiqim chegarasi to'ldi (%s TON). Chiqim to'xtatildi.", sent_today
        )
        return None

    result = await session.execute(
        update(Payout)
        .where(Payout.id == candidate.id, Payout.status == "pending")
        .values(status="sending")
    )
    if result.rowcount == 0:
        return None

    await session.refresh(candidate)
    return candidate


async def mark_sent(
    session: AsyncSession, payout_id: int, tx_hash: str | None
) -> None:
    await session.execute(
        update(Payout)
        .where(Payout.id == payout_id, Payout.status == "sending")
        .values(status="sent", tx_hash=tx_hash, sent_at=utcnow())
    )


async def mark_failed(session: AsyncSession, payout_id: int, error: str) -> None:
    """Xato bo'lsa 'failed' qilinadi — 'pending' ga QAYTARILMAYDI.

    Avtomatik qayta urinish xavfli: tranzaksiya aslida ketgan, faqat
    javob kelmagan bo'lishi mumkin. Bunday holatni odam tekshirishi
    kerak.
    """
    await session.execute(
        update(Payout)
        .where(Payout.id == payout_id)
        .values(status="failed", error=error[:2000])
    )


# ----------------------------------------------------------------------
# Blokcheynga yuborish
# ----------------------------------------------------------------------
class TonSender:
    """TON yuborish uchun o'ram.

    DIQQAT: bu klass mnemonic bilan ishlaydi. Uni faqat muhit
    o'zgaruvchisidan o'qi, hech qachon kodga yozma va git'ga qo'yma.
    Ishlab chiqarishda alohida "issiq hamyon" tut va unda faqat kunlik
    aylanma uchun yetadigan miqdor saqla.
    """

    def __init__(self) -> None:
        self._wallet = None

    async def _get_wallet(self):
        if self._wallet is not None:
            return self._wallet

        if not settings.escrow_mnemonic:
            raise PayoutError("escrow_mnemonic sozlanmagan")


        client = ToncenterV3Client(
            is_testnet=settings.ton_is_testnet,
            api_key=settings.ton_api_key or None,
        )
        wallet, _pub, _priv, _mnemo = WalletV5R1.from_mnemonic(
            client, settings.escrow_mnemonic.split()
        )
        self._wallet = wallet
        return wallet

    async def send(self, to_address: str, amount: Decimal, comment: str = "") -> str:
        wallet = await self._get_wallet()
        tx_hash = await wallet.transfer(
            destination=to_address,
            amount=float(amount),
            body=comment or "",
        )
        return str(tx_hash)


async def process_payout_queue(session: AsyncSession, sender: TonSender) -> int:
    """Navbatdagi chiqimlarni yuboradi. Yuborilganlar sonini qaytaradi."""
    processed = 0

    while True:
        payout = await claim_next_payout(session)
        if payout is None:
            break

        await session.commit()  # 'sending' holatini darhol qat'iylashtiramiz

        try:
            tx_hash = await sender.send(
                payout.to_address,
                Decimal(str(payout.amount_ton)),
                comment=f"Deal #{payout.deal_id}",
            )
        except Exception as exc:
            log.exception("Chiqim #%s yuborilmadi", payout.id)
            await mark_failed(session, payout.id, str(exc))
            await session.commit()
            continue

        await mark_sent(session, payout.id, tx_hash)
        session.add(
            DealEvent(
                deal_id=payout.deal_id,
                event=f"payout_sent:{payout.purpose}",
                payload=tx_hash,
            )
        )
        await session.commit()
        processed += 1
        log.info("Chiqim #%s yuborildi: %s", payout.id, tx_hash)

    return processed

# ====================================================================
# SOVG'A XIZMATI
# ====================================================================

log = logging.getLogger(__name__)


class GiftError(Exception):
    """Foydalanuvchiga ko'rsatiladigan sovg'a xatosi."""


class GiftCheck:
    """Sovg'a o'tkazishga tayyorligini tekshirish natijasi."""

    def __init__(
        self,
        *,
        ok: bool,
        reason: str = "",
        owned_gift_id: str | None = None,
        transfer_star_count: int = 0,
    ) -> None:
        self.ok = ok
        self.reason = reason
        self.owned_gift_id = owned_gift_id
        self.transfer_star_count = transfer_star_count


async def find_transferable_gift(
    bot: Bot,
    business_connection_id: str,
    *,
    slug_hint: str | None = None,
) -> GiftCheck:
    """Biznes akkauntdagi o'tkazsa bo'ladigan sovg'ani topadi.

    Bitim YARATILAYOTGAN paytda chaqiriladi, oxirida emas. Sabab:
    o'tkazib bo'lmaydigan sovg'a uchun xaridordan pul olib, keyin
    "kechirasiz, bo'lmadi" deyish — bu ishonchni yo'q qiladigan
    ssenariy. Muammoni boshida ushlaymiz.
    """
    try:
        owned = await bot.get_business_account_gifts(
            business_connection_id=business_connection_id,
            exclude_unsaved=False,
        )
    except TelegramAPIError as exc:
        log.warning("get_business_account_gifts xatosi: %s", exc)
        return GiftCheck(ok=False, reason="Sovg'alar ro'yxati olinmadi")

    now = datetime.now(timezone.utc)

    for gift in owned.gifts:
        if getattr(gift, "type", None) != "unique":
            continue

        if slug_hint:
            slug = getattr(getattr(gift, "gift", None), "name", "") or ""
            if slug_hint.lower() not in slug.lower():
                continue

        if not getattr(gift, "can_be_transferred", False):
            return GiftCheck(
                ok=False, reason="Bu sovg'ani hozir o'tkazib bo'lmaydi"
            )

        next_date = getattr(gift, "next_transfer_date", None)
        if next_date and next_date > now:
            return GiftCheck(
                ok=False,
                reason=(
                    "Sovg'a hali cooldown'da. "
                    f"O'tkazish mumkin bo'ladi: {next_date:%Y-%m-%d %H:%M} UTC"
                ),
            )

        return GiftCheck(
            ok=True,
            owned_gift_id=getattr(gift, "owned_gift_id", None),
            transfer_star_count=getattr(gift, "transfer_star_count", 0) or 0,
        )

    return GiftCheck(ok=False, reason="Mos sovg'a topilmadi")


async def transfer_gift(
    bot: Bot,
    *,
    business_connection_id: str,
    owned_gift_id: str,
    new_owner_chat_id: int,
    star_count: int = 0,
) -> None:
    """Sovg'ani xaridorga o'tkazadi.

    Muhim: `new_owner_chat_id` oxirgi 24 soatda faol bo'lishi shart,
    aks holda Telegram xato qaytaradi. Shuning uchun xaridor bitimda
    yaqinda harakat qilgan bo'lishi kerak — bizning oqimda u endigina
    to'lov qilgan, demak shart bajarilgan.
    """
    try:
        await bot.transfer_gift(
            business_connection_id=business_connection_id,
            owned_gift_id=owned_gift_id,
            new_owner_chat_id=new_owner_chat_id,
            star_count=star_count or None,
        )
    except TelegramAPIError as exc:
        log.exception("transfer_gift muvaffaqiyatsiz")
        raise GiftError(f"Sovg'a o'tkazilmadi: {exc}") from exc


async def has_connection_rights(bot: Bot, business_connection_id: str) -> bool:
    """Bot kerakli huquqlarga egaligini tekshiradi."""
    try:
        conn = await bot.get_business_connection(
            business_connection_id=business_connection_id
        )
    except TelegramAPIError:
        return False

    rights = getattr(conn, "rights", None)
    if rights is None:
        return bool(getattr(conn, "is_enabled", False))

    return bool(
        getattr(rights, "can_transfer_and_upgrade_gifts", False)
        and getattr(rights, "can_view_gifts_and_stars", False)
    )

# ====================================================================
# TON KUZATUVCHI
# ====================================================================

log = logging.getLogger(__name__)

NANO = Decimal("1000000000")


def nano_to_ton(value: int | str) -> Decimal:
    return (Decimal(str(value)) / NANO).quantize(Decimal("0.000000001"))


class TonWatcher:
    """Escrow hamyoniga kirgan to'lovlarni kuzatadi va bitimlarga bog'laydi.

    Ishonchlilikning uchta ustuni:

    1. Har bir tranzaksiya `processed_tx` jadvaliga hash bo'yicha unikal
       yoziladi. Bir xil hash ikkinchi marta yozilmaydi, demak bitta
       to'lov ikki marta hisoblanmaydi.

    2. Bitim holati shartli UPDATE bilan o'zgartiriladi
       (`WHERE status = 'awaiting_payment'`). Ikkita parallel jarayon
       bir vaqtda urinsa, faqat bittasi o'tadi.

    3. Bog'lanmagan to'lovlar o'chirilmaydi — `matched=False` bo'lib
       qoladi va admin ularni qo'lda ko'rib chiqadi.
    """

    def __init__(self, on_paid=None) -> None:
        self._on_paid = on_paid
        self._session: aiohttp.ClientSession | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={"X-API-Key": settings.ton_api_key} if settings.ton_api_key else {},
        )
        log.info("TON watcher ishga tushdi, hamyon: %s", settings.escrow_wallet_address)
        while self._running:
            try:
                await self._tick()
            except Exception:
                log.exception("Watcher tsiklida xato")
            await asyncio.sleep(settings.watcher_interval_sec)

    async def stop(self) -> None:
        self._running = False
        if self._session:
            await self._session.close()

    # ------------------------------------------------------------------
    async def _last_lt(self) -> int:
        async with get_session() as s:
            result = await s.execute(select(func.max(ProcessedTx.lt)))
            return result.scalar() or 0

    async def _fetch(self, start_lt: int) -> list[dict]:
        assert self._session is not None
        params = {
            "account": settings.escrow_wallet_address,
            "limit": "50",
            "sort": "asc",
        }
        if start_lt:
            params["start_lt"] = str(start_lt + 1)

        url = f"{settings.ton_api_base}/transactions"
        async with self._session.get(url, params=params) as resp:
            if resp.status != 200:
                body = await resp.text()
                log.warning("TON API %s: %s", resp.status, body[:300])
                return []
            data = await resp.json()
        return data.get("transactions", []) or []

    @staticmethod
    def _extract_incoming(tx: dict) -> dict | None:
        """Kiruvchi to'lovni ajratib oladi. Chiquvchilarni e'tiborsiz qoldiradi."""
        in_msg = tx.get("in_msg") or {}
        source = in_msg.get("source")
        destination = in_msg.get("destination")
        value = in_msg.get("value")

        # Tashqaridan kelgan, qiymati bor xabar bo'lishi shart
        if not source or not value or not destination:
            return None
        if int(value) <= 0:
            return None

        comment = None
        content = in_msg.get("message_content") or {}
        decoded = content.get("decoded") or {}
        if isinstance(decoded, dict):
            comment = decoded.get("comment")

        return {
            "hash": tx.get("hash"),
            "lt": int(tx.get("lt") or 0),
            "source": source,
            "amount": nano_to_ton(value),
            "comment": (comment or "").strip(),
        }

    # ------------------------------------------------------------------
    async def _tick(self) -> None:
        last_lt = await self._last_lt()
        txs = await self._fetch(last_lt)
        if not txs:
            return

        for tx in txs:
            payment = self._extract_incoming(tx)
            if not payment or not payment["hash"]:
                continue
            try:
                await self._handle_payment(payment)
            except Exception:
                log.exception("To'lovni qayta ishlashda xato: %s", payment.get("hash"))

    async def _handle_payment(self, payment: dict) -> None:
        # 1-qadam: tranzaksiyani band qilamiz. Agar bu hash allaqachon
        # bo'lsa, IntegrityError chiqadi va biz chiqib ketamiz.
        async with get_session() as s:
            record = ProcessedTx(
                tx_hash=payment["hash"],
                lt=payment["lt"],
                from_address=payment["source"],
                amount_ton=payment["amount"],
                comment=payment["comment"] or None,
                matched=False,
            )
            s.add(record)
            try:
                await s.flush()
            except IntegrityError:
                await s.rollback()
                return  # allaqachon ko'rilgan
            record_id = record.id

        code = payment["comment"]
        if not code:
            log.warning(
                "Commentsiz to'lov: %s TON, %s — qo'lda ko'rib chiqilsin",
                payment["amount"],
                payment["source"],
            )
            return

        # 2-qadam: bitimni topamiz
        async with get_session() as s:
            deal = (
                await s.execute(select(Deal).where(Deal.payment_code == code))
            ).scalar_one_or_none()

            if deal is None:
                log.warning("Noma'lum comment '%s' — bitim topilmadi", code)
                return

            if deal.status != DealStatus.AWAITING_PAYMENT:
                log.warning(
                    "Bitim #%s to'lov kutmayapti (holat: %s)", deal.id, deal.status
                )
                return

            expected = Decimal(str(deal.total_ton))
            paid = payment["amount"]

            if paid + settings.amount_tolerance_ton < expected:
                log.warning(
                    "Bitim #%s: kam to'landi (%s < %s)", deal.id, paid, expected
                )
                await self._log_event(
                    s, deal.id, "underpaid", None, f"{paid}/{expected}"
                )
                return

            # 3-qadam: holatni shartli UPDATE bilan o'zgartiramiz
            result = await s.execute(
                update(Deal)
                .where(
                    Deal.id == deal.id,
                    Deal.status == DealStatus.AWAITING_PAYMENT,
                )
                .values(
                    status=DealStatus.AWAITING_GIFT,
                    buyer_wallet=payment["source"],
                    expires_at=utcnow() + timedelta(seconds=settings.gift_window_sec),
                    updated_at=utcnow(),
                )
            )
            if result.rowcount == 0:
                log.warning("Bitim #%s holati boshqa jarayon tomonidan band", deal.id)
                return

            await s.execute(
                update(ProcessedTx)
                .where(ProcessedTx.id == record_id)
                .values(matched=True, deal_id=deal.id)
            )
            await self._log_event(
                s, deal.id, "payment_received", deal.buyer_id, f"{paid} TON"
            )
            deal_id = deal.id

        log.info("Bitim #%s uchun to'lov qabul qilindi: %s TON", deal_id, paid)

        if self._on_paid:
            try:
                await self._on_paid(deal_id)
            except Exception:
                log.exception("on_paid callback xatosi, bitim #%s", deal_id)

    @staticmethod
    async def _log_event(s, deal_id: int, event: str, actor: int | None, payload: str):

        s.add(DealEvent(deal_id=deal_id, event=event, actor_id=actor, payload=payload))

# ====================================================================
# ADMIN PANEL
# ====================================================================

log = logging.getLogger(__name__)
admin_router = Router()

#: Ish vaqtida o'zgartiriladigan kalit. settings.payouts_enabled
#: bilan birga tekshiriladi — ikkalasi ham yoqiq bo'lishi shart.
RUNTIME_PAYOUTS_ENABLED = True


def is_admin(user_id: int) -> bool:
    return user_id in settings.admins


def admin_menu_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="📊 Statistika", callback_data="adm:stats")
    kb.button(text="❓ Bog'lanmagan to'lovlar", callback_data="adm:orphans")
    kb.button(text="⚠️ Muammoli chiqimlar", callback_data="adm:failed")
    kb.button(text="✋ Tasdiq kutayotganlar", callback_data="adm:approvals")
    kb.button(text="🔴 Chiqimni to'xtatish", callback_data="adm:kill")
    kb.button(text="🟢 Chiqimni yoqish", callback_data="adm:resume")
    kb.adjust(1, 1, 1, 1, 2)
    return kb.as_markup()


@admin_router.message(Command("admin"))
async def admin_entry(message: Message):
    if not is_admin(message.from_user.id):
        return  # javob bermaymiz — panel borligini bildirmaslik uchun

    state = "🟢 yoqiq" if RUNTIME_PAYOUTS_ENABLED else "🔴 to'xtatilgan"
    await message.answer(
        f"<b>Admin panel</b>\n\nAvtomatik chiqim: {state}",
        reply_markup=admin_menu_kb(),
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data == "adm:stats")
async def admin_stats(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer()

    async with get_session() as s:
        users = (await s.execute(select(func.count(User.id)))).scalar() or 0

        by_status = (
            await s.execute(select(Deal.status, func.count(Deal.id)).group_by(Deal.status))
        ).all()

        completed_volume = (
            await s.execute(
                select(func.coalesce(func.sum(Deal.amount_ton), 0)).where(
                    Deal.status == DealStatus.COMPLETED
                )
            )
        ).scalar() or 0

        earned = (
            await s.execute(
                select(func.coalesce(func.sum(Deal.fee_ton), 0)).where(
                    Deal.status == DealStatus.COMPLETED
                )
            )
        ).scalar() or 0

        orphans = (
            await s.execute(
                select(func.count(ProcessedTx.id)).where(ProcessedTx.matched.is_(False))
            )
        ).scalar() or 0

        failed = (
            await s.execute(
                select(func.count(Payout.id)).where(Payout.status.in_(["failed", "sending"]))
            )
        ).scalar() or 0

    lines = [
        "<b>📊 Statistika</b>",
        "",
        f"Foydalanuvchilar: <b>{users}</b>",
        f"Yakunlangan hajm: <b>{completed_volume} TON</b>",
        f"Komissiya daromadi: <b>{earned} TON</b>",
        "",
        "<b>Bitimlar holati bo'yicha:</b>",
    ]
    for status, count in by_status:
        lines.append(f"  {status.value}: {count}")

    lines += [
        "",
        f"{'⚠️' if orphans else '✅'} Bog'lanmagan to'lovlar: <b>{orphans}</b>",
        f"{'⚠️' if failed else '✅'} Muammoli chiqimlar: <b>{failed}</b>",
    ]

    await call.message.edit_text(
        "\n".join(lines), reply_markup=admin_menu_kb(), parse_mode="HTML"
    )
    await call.answer()


@admin_router.callback_query(F.data == "adm:orphans")
async def admin_orphans(call: CallbackQuery):
    """Bitimga bog'lanmagan to'lovlar.

    Ko'pincha sababi: xaridor comment yozishni unutgan. Pul hamyonda
    turibdi, lekin qaysi bitimga tegishli ekani noma'lum. Bu ro'yxatni
    muntazam ko'rib turish shart — aks holda odam "pulim yo'qoldi" deb
    yozguncha xabaringiz bo'lmaydi.
    """
    if not is_admin(call.from_user.id):
        return await call.answer()

    async with get_session() as s:
        rows = (
            await s.execute(
                select(ProcessedTx)
                .where(ProcessedTx.matched.is_(False))
                .order_by(ProcessedTx.id.desc())
                .limit(10)
            )
        ).scalars().all()

    if not rows:
        text = "✅ Bog'lanmagan to'lov yo'q."
    else:
        parts = ["<b>❓ Bog'lanmagan to'lovlar</b>\n"]
        for tx in rows:
            parts.append(
                f"<code>{tx.amount_ton} TON</code>\n"
                f"Kimdan: <code>{esc(tx.from_address or '—')}</code>\n"
                f"Izoh: <code>{esc(tx.comment or 'YO‘Q')}</code>\n"
                f"Vaqt: {tx.created_at:%Y-%m-%d %H:%M}\n"
                f"Hash: <code>{esc((tx.tx_hash or '')[:24])}…</code>\n"
            )
        text = "\n".join(parts)

    await call.message.edit_text(text, reply_markup=admin_menu_kb(), parse_mode="HTML")
    await call.answer()


@admin_router.callback_query(F.data == "adm:failed")
async def admin_failed(call: CallbackQuery):
    """Xato bergan yoki 'sending' da qotib qolgan chiqimlar.

    'sending' holati ayniqsa muhim: bu bot TON yuborayotgan paytda
    qulaganini bildiradi. Pul ketgan yoki ketmagan bo'lishi mumkin —
    blokcheyndan QO'LDA tekshirish kerak. Avtomatik qayta urinish
    ataylab qilinmagan.
    """
    if not is_admin(call.from_user.id):
        return await call.answer()

    async with get_session() as s:
        rows = (
            await s.execute(
                select(Payout)
                .where(Payout.status.in_(["failed", "sending"]))
                .order_by(Payout.id.desc())
                .limit(10)
            )
        ).scalars().all()

    if not rows:
        text = "✅ Muammoli chiqim yo'q."
    else:
        parts = ["<b>⚠️ Muammoli chiqimlar</b>\n"]
        for p in rows:
            warn = "🔴 QO'LDA TEKSHIRING" if p.status == "sending" else "❌ xato"
            parts.append(
                f"{warn}\n"
                f"Bitim #{p.deal_id} · {p.purpose}\n"
                f"<code>{p.amount_ton} TON</code> → <code>{esc(p.to_address)}</code>\n"
                f"Xato: <code>{esc((p.error or '—')[:120])}</code>\n"
            )
        text = "\n".join(parts)

    await call.message.edit_text(text, reply_markup=admin_menu_kb(), parse_mode="HTML")
    await call.answer()


@admin_router.callback_query(F.data == "adm:approvals")
async def admin_approvals(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer()

    async with get_session() as s:
        rows = (
            await s.execute(
                select(Payout)
                .where(Payout.status == "needs_approval")
                .order_by(Payout.id)
                .limit(10)
            )
        ).scalars().all()

    if not rows:
        await call.message.edit_text(
            "✅ Tasdiq kutayotgan chiqim yo'q.",
            reply_markup=admin_menu_kb(),
            parse_mode="HTML",
        )
        return await call.answer()

    kb = InlineKeyboardBuilder()
    parts = ["<b>✋ Tasdiq kutmoqda</b>\n"]
    for p in rows:
        parts.append(
            f"#{p.id} · bitim {p.deal_id} · <b>{p.amount_ton} TON</b>\n"
            f"→ <code>{esc(p.to_address)}</code>\n"
        )
        kb.button(text=f"✅ #{p.id} tasdiqlash", callback_data=f"adm:ok:{p.id}")
    kb.button(text="◀️ Orqaga", callback_data="adm:back")
    kb.adjust(1)

    await call.message.edit_text(
        "\n".join(parts), reply_markup=kb.as_markup(), parse_mode="HTML"
    )
    await call.answer()


@admin_router.callback_query(F.data.startswith("adm:ok:"))
async def admin_approve(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer()

    payout_id = int(call.data.split(":")[-1])

    async with get_session() as s:
        result = await s.execute(
            update(Payout)
            .where(Payout.id == payout_id, Payout.status == "needs_approval")
            .values(status="pending", approved_by=call.from_user.id)
        )
        if result.rowcount:
            payout = (
                await s.execute(select(Payout).where(Payout.id == payout_id))
            ).scalar_one()
            s.add(
                DealEvent(
                    deal_id=payout.deal_id,
                    event="payout_approved",
                    actor_id=call.from_user.id,
                    payload=f"payout #{payout_id}",
                )
            )
            await call.answer("Tasdiqlandi, navbatga qo'yildi", show_alert=True)
        else:
            await call.answer("Holat o'zgargan", show_alert=True)

    await admin_approvals(call)


@admin_router.callback_query(F.data == "adm:kill")
async def admin_kill(call: CallbackQuery):
    """Avtomatik chiqimni darhol to'xtatadi.

    Shubhali narsa sezilganda birinchi bosiladigan tugma. Botni
    o'chirishdan afzal: bitimlar ochiq qoladi, faqat pul chiqmaydi.
    """
    global RUNTIME_PAYOUTS_ENABLED
    if not is_admin(call.from_user.id):
        return await call.answer()

    RUNTIME_PAYOUTS_ENABLED = False
    log.critical("CHIQIM TO'XTATILDI, admin: %s", call.from_user.id)

    await call.answer("🔴 Chiqim to'xtatildi", show_alert=True)
    await call.message.edit_text(
        "<b>Admin panel</b>\n\nAvtomatik chiqim: 🔴 <b>to'xtatilgan</b>\n\n"
        "<i>Navbatdagi chiqimlar yuborilmaydi. Bitimlar ochiq qoladi.</i>",
        reply_markup=admin_menu_kb(),
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data == "adm:resume")
async def admin_resume(call: CallbackQuery):
    global RUNTIME_PAYOUTS_ENABLED
    if not is_admin(call.from_user.id):
        return await call.answer()

    RUNTIME_PAYOUTS_ENABLED = True
    log.warning("Chiqim qayta yoqildi, admin: %s", call.from_user.id)

    await call.answer("🟢 Chiqim yoqildi", show_alert=True)
    await call.message.edit_text(
        "<b>Admin panel</b>\n\nAvtomatik chiqim: 🟢 <b>yoqiq</b>",
        reply_markup=admin_menu_kb(),
        parse_mode="HTML",
    )


@admin_router.callback_query(F.data == "adm:back")
async def admin_back(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return await call.answer()
    state = "🟢 yoqiq" if RUNTIME_PAYOUTS_ENABLED else "🔴 to'xtatilgan"
    await call.message.edit_text(
        f"<b>Admin panel</b>\n\nAvtomatik chiqim: {state}",
        reply_markup=admin_menu_kb(),
        parse_mode="HTML",
    )
    await call.answer()


@admin_router.message(Command("deal"))
async def admin_deal_log(message: Message):
    """/deal 12 — bitimning to'liq tarixi. Nizolarda shu ishlatiladi."""
    if not is_admin(message.from_user.id):
        return

    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Foydalanish: <code>/deal 12</code>", parse_mode="HTML")
        return

    deal_id = int(parts[1])

    async with get_session() as s:
        deal = (
            await s.execute(select(Deal).where(Deal.id == deal_id))
        ).scalar_one_or_none()
        if deal is None:
            await message.answer("Bitim topilmadi")
            return

        events = (
            await s.execute(
                select(DealEvent)
                .where(DealEvent.deal_id == deal_id)
                .order_by(DealEvent.id)
            )
        ).scalars().all()

    lines = [
        f"<b>Bitim #{deal.id}</b>",
        f"Holat: <code>{deal.status.value}</code>",
        f"Tavsif: {esc(deal.description[:200])}",
        f"Summa: {deal.amount_ton} + {deal.fee_ton} = {deal.total_ton} TON",
        f"Sotuvchi: <code>{deal.seller_id}</code>",
        f"Xaridor: <code>{deal.buyer_id}</code>",
        f"Kod: <code>{deal.payment_code}</code>",
        "",
        "<b>Tarix:</b>",
    ]
    for ev in events:
        payload = f" — {esc(ev.payload)}" if ev.payload else ""
        lines.append(f"<code>{ev.created_at:%m-%d %H:%M}</code> {ev.event}{payload}")

    text = "\n".join(lines)
    await message.answer(text[:4000], parse_mode="HTML")

# ====================================================================
# ASOSIY HANDLERLAR
# ====================================================================

log = logging.getLogger(__name__)
user_router = Router()

TON_ADDRESS = re.compile(r"^(?:[EU]Q[A-Za-z0-9_-]{46}|0:[a-fA-F0-9]{64})$")


class DealForm(StatesGroup):
    description = State()
    amount = State()
    wallet = State()


class WalletForm(StatesGroup):
    address = State()


async def get_or_create_user(user_id: int, username: str | None, ref: int | None = None):
    async with get_session() as s:
        user = (
            await s.execute(select(User).where(User.id == user_id))
        ).scalar_one_or_none()
        if user is None:
            user = User(id=user_id, username=username, referrer_id=ref)
            s.add(user)
            await s.flush()
        elif username and user.username != username:
            user.username = username
        return user


def emo() -> dict[str, str]:
    """Shablonlarga uzatiladigan emoji to'plami."""
    return {
        name: e(name)
        for name in (
            "shield gift wallet money ton check cross clock warning "
            "handshake star user settings back channel account list link lock"
        ).split()
    }


# ----------------------------------------------------------------------
@user_router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    payload = command.args or ""

    ref = None
    if payload.startswith("ref"):
        raw = payload[3:]
        if raw.isdigit():
            ref = int(raw)

    user = await get_or_create_user(
        message.from_user.id, message.from_user.username, ref
    )

    # Bitim havolasi orqali kelgan bo'lsa
    if payload.startswith("deal_"):
        await join_deal(message, payload[5:], user.language)
        return

    if not payload and user.deals_completed == 0 and not user.username:
        pass

    await message.answer(
        t("choose_language", user.language), reply_markup=language_kb()
    )


@user_router.callback_query(F.data.startswith("lang:"))
async def set_language(call: CallbackQuery):
    lang = call.data.split(":", 1)[1]
    async with get_session() as s:
        user = (
            await s.execute(select(User).where(User.id == call.from_user.id))
        ).scalar_one_or_none()
        if user:
            user.language = lang
    await show_menu(call, lang)
    await call.answer()


async def show_menu(call: CallbackQuery, lang: str):
    text = t(
        "welcome",
        lang,
        fee=settings.fee_percent,
        fee_min=settings.fee_min_ton,
        max_deal=settings.max_deal_ton,
        **emo(),
    )
    await call.message.edit_text(
        text, reply_markup=main_menu_kb(lang), parse_mode="HTML"
    )


@user_router.callback_query(F.data == "menu")
async def back_to_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    lang = await get_lang(call.from_user.id)
    await show_menu(call, lang)
    await call.answer()


async def get_lang(user_id: int) -> str:
    async with get_session() as s:
        user = (await s.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        return user.language if user else "uz"


# ----------------------------------------------------------------------
# Hamyon
# ----------------------------------------------------------------------
@user_router.callback_query(F.data == "wallet")
async def show_wallet(call: CallbackQuery):
    lang = await get_lang(call.from_user.id)
    async with get_session() as s:
        wallets = (
            await s.execute(select(Wallet).where(Wallet.user_id == call.from_user.id))
        ).scalars().all()

    await call.message.edit_text(
        t("wallet_title", lang, **emo()),
        reply_markup=wallet_kb(lang, list(wallets)),
        parse_mode="HTML",
    )
    await call.answer()


@user_router.callback_query(F.data == "wallet:add")
async def wallet_add(call: CallbackQuery, state: FSMContext):
    lang = await get_lang(call.from_user.id)
    await state.set_state(WalletForm.address)
    await call.message.edit_text(
        t("wallet_ask_address", lang, **emo()),
        reply_markup=back_kb(lang),
        parse_mode="HTML",
    )
    await call.answer()


@user_router.message(WalletForm.address)
async def wallet_save(message: Message, state: FSMContext):
    lang = await get_lang(message.from_user.id)
    address = (message.text or "").strip()

    if not TON_ADDRESS.match(address):
        await message.answer(t("wallet_bad_address", lang, **emo()), parse_mode="HTML")
        return

    async with get_session() as s:
        exists = (
            await s.execute(
                select(Wallet).where(
                    Wallet.user_id == message.from_user.id, Wallet.address == address
                )
            )
        ).scalar_one_or_none()
        if exists is None:
            s.add(Wallet(user_id=message.from_user.id, address=address))

    await state.clear()
    await message.answer(
        t("wallet_added", lang, address=esc(address), **emo()),
        reply_markup=back_kb(lang),
        parse_mode="HTML",
    )


# ----------------------------------------------------------------------
# Bitim yaratish
# ----------------------------------------------------------------------
@user_router.callback_query(F.data == "deal:new")
async def deal_new(call: CallbackQuery):
    lang = await get_lang(call.from_user.id)
    await call.message.edit_text(
        t("deal_choose_type", lang, **emo()),
        reply_markup=deal_type_kb(lang),
        parse_mode="HTML",
    )
    await call.answer()


@user_router.callback_query(F.data.startswith("deal:kind:"))
async def deal_kind(call: CallbackQuery, state: FSMContext):
    lang = await get_lang(call.from_user.id)
    kind = call.data.split(":")[-1]
    await state.update_data(kind=kind)
    await state.set_state(DealForm.description)
    await call.message.edit_text(
        t("deal_ask_description", lang, **emo()),
        reply_markup=back_kb(lang),
        parse_mode="HTML",
    )
    await call.answer()


@user_router.message(DealForm.description)
async def deal_description(message: Message, state: FSMContext):
    lang = await get_lang(message.from_user.id)
    text = (message.text or "").strip()
    if len(text) < 3:
        await message.answer(t("error_generic", lang, **emo()), parse_mode="HTML")
        return

    await state.update_data(description=text)
    await state.set_state(DealForm.amount)
    await message.answer(
        t("deal_ask_amount", lang, **emo()),
        reply_markup=back_kb(lang),
        parse_mode="HTML",
    )


@user_router.message(DealForm.amount)
async def deal_amount(message: Message, state: FSMContext):
    lang = await get_lang(message.from_user.id)
    raw = (message.text or "").strip().replace(",", ".")

    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        await message.answer(t("deal_bad_amount", lang, **emo()), parse_mode="HTML")
        return

    try:
        validate_amount(amount)
    except DealError as exc:
        await message.answer(f"{e('cross')} {esc(str(exc))}", parse_mode="HTML")
        return

    await state.update_data(amount=str(amount))
    await state.set_state(DealForm.wallet)
    await message.answer(
        t("deal_ask_wallet", lang, **emo()),
        reply_markup=back_kb(lang),
        parse_mode="HTML",
    )


@user_router.message(DealForm.wallet)
async def deal_finish(message: Message, state: FSMContext):
    lang = await get_lang(message.from_user.id)
    address = (message.text or "").strip()

    if not TON_ADDRESS.match(address):
        await message.answer(t("wallet_bad_address", lang, **emo()), parse_mode="HTML")
        return

    data = await state.get_data()
    await state.clear()

    try:
        async with get_session() as s:
            deal = await create_deal(
                s,
                seller_id=message.from_user.id,
                description=data["description"],
                amount=Decimal(data["amount"]),
                kind=DealKind(data.get("kind", "gift")),
                seller_wallet=address,
            )
            deal_id, code = deal.id, deal.payment_code
            amount, fee, total = deal.amount_ton, deal.fee_ton, deal.total_ton
    except DealError as exc:
        await message.answer(f"{e('cross')} {esc(str(exc))}", parse_mode="HTML")
        return

    me = await message.bot.me()
    link = f"https://t.me/{me.username}?start=deal_{code}"

    await message.answer(
        t(
            "deal_created",
            lang,
            id=deal_id,
            description=esc(data["description"]),
            amount=amount,
            total=total,
            fee=fee,
            link=link,
            **emo(),
        ),
        reply_markup=share_deal_kb(lang, me.username, code),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


# ----------------------------------------------------------------------
# Bitimga qo'shilish
# ----------------------------------------------------------------------
async def join_deal(message: Message, code: str, lang: str):
    async with get_session() as s:
        deal = (
            await s.execute(select(Deal).where(Deal.payment_code == code))
        ).scalar_one_or_none()

        if deal is None or deal.status != DealStatus.DRAFT:
            await message.answer(t("deal_not_found", lang, **emo()), parse_mode="HTML")
            return

        if deal.seller_id == message.from_user.id:
            await message.answer(t("deal_own", lang, **emo()), parse_mode="HTML")
            return

        try:
            deal = await attach_buyer(s, deal.id, message.from_user.id)
        except DealError as exc:
            await message.answer(f"{e('cross')} {esc(str(exc))}", parse_mode="HTML")
            return

        seller = (
            await s.execute(select(User).where(User.id == deal.seller_id))
        ).scalar_one_or_none()

        deal_id = deal.id
        description = deal.description
        total = deal.total_ton

    seller_name = f"@{seller.username}" if seller and seller.username else "—"

    await message.answer(
        t(
            "deal_payment_instructions",
            lang,
            id=deal_id,
            description=esc(description),
            seller=esc(seller_name),
            total=total,
            address=settings.escrow_wallet_address,
            code=code,
            minutes=settings.payment_window_sec // 60,
            **emo(),
        ),
        reply_markup=back_kb(lang),
        parse_mode="HTML",
    )


# ----------------------------------------------------------------------
@user_router.callback_query(F.data == "settings")
async def show_settings(call: CallbackQuery):
    lang = await get_lang(call.from_user.id)
    await call.message.edit_text(
        t("settings_title", lang, **emo()),
        reply_markup=settings_kb(lang),
        parse_mode="HTML",
    )
    await call.answer()


@user_router.callback_query(F.data == "language")
async def change_language(call: CallbackQuery):
    lang = await get_lang(call.from_user.id)
    await call.message.edit_text(
        t("choose_language", lang), reply_markup=language_kb()
    )
    await call.answer()


@user_router.callback_query(F.data == "noop")
async def noop(call: CallbackQuery):
    await call.answer()

# ====================================================================
# ISHGA TUSHIRISH
# ====================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("guarant")


async def notify_seller_paid(bot: Bot, deal_id: int) -> None:
    """To'lov kelganda sotuvchini xabardor qiladi."""
    async with get_session() as s:
        deal = (await s.execute(select(Deal).where(Deal.id == deal_id))).scalar_one()
        seller = (
            await s.execute(select(User).where(User.id == deal.seller_id))
        ).scalar_one_or_none()
        lang = seller.language if seller else "uz"
        seller_id, total = deal.seller_id, deal.total_ton

    try:
        await bot.send_message(
            seller_id,
            t(
                "notify_seller_paid",
                lang,
                id=deal_id,
                total=total,
                minutes=settings.gift_window_sec // 60,
                **emo(),
            ),
            parse_mode="HTML",
        )
    except Exception:
        # Sotuvchi botni bloklagan bo'lishi mumkin — bu bitimni
        # to'xtatmasligi kerak. Taymer baribir ishlaydi.
        log.exception("Sotuvchiga xabar yuborilmadi, bitim #%s", deal_id)


async def expiry_worker(bot: Bot) -> None:
    """Muddati o'tgan bitimlarni yopadi va qaytarishni navbatga qo'yadi."""
    while True:
        try:
            async with get_session() as s:
                affected = await expire_stale_deals(s)

                for deal_id, new_status in affected:
                    deal = (
                        await s.execute(select(Deal).where(Deal.id == deal_id))
                    ).scalar_one()

                    if new_status == DealStatus.REFUND_PENDING and deal.buyer_wallet:
                        await queue_payout(
                            s,
                            deal_id=deal_id,
                            purpose="refund",
                            to_address=deal.buyer_wallet,
                            amount=Decimal(str(deal.total_ton)),
                        )

            for deal_id, new_status in affected:
                await _notify_expiry(bot, deal_id, new_status)

        except Exception:
            log.exception("expiry_worker xatosi")

        await asyncio.sleep(30)


async def _notify_expiry(bot: Bot, deal_id: int, status: DealStatus) -> None:
    async with get_session() as s:
        deal = (await s.execute(select(Deal).where(Deal.id == deal_id))).scalar_one()
        targets = [x for x in (deal.buyer_id, deal.seller_id) if x]
        users = (
            await s.execute(select(User).where(User.id.in_(targets)))
        ).scalars().all()

    key = "deal_refunded" if status == DealStatus.REFUND_PENDING else "deal_expired"
    for user in users:
        try:
            await bot.send_message(
                user.id,
                t(key, user.language, id=deal_id, **emo()),
                parse_mode="HTML",
            )
        except Exception:
            log.warning("Xabar yuborilmadi: %s", user.id)


async def payout_worker() -> None:
    sender = TonSender()
    while True:
        try:
            # Ikkala kalit ham yoqiq bo'lishi shart: .env dagi doimiy
            # sozlama va admin panelidagi ish vaqtidagi kalit.
            if settings.payouts_enabled and RUNTIME_PAYOUTS_ENABLED:
                async with get_session() as s:
                    await process_payout_queue(s, sender)
        except Exception:
            log.exception("payout_worker xatosi")
        await asyncio.sleep(15)


async def main() -> None:
    load_ids()
    await init_db()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(admin_router)
    dp.include_router(user_router)

    watcher = TonWatcher(on_paid=lambda deal_id: notify_seller_paid(bot, deal_id))

    tasks = [
        asyncio.create_task(watcher.start()),
        asyncio.create_task(expiry_worker(bot)),
        asyncio.create_task(payout_worker()),
    ]

    log.info("Bot ishga tushdi")
    if settings.ton_is_testnet:
        log.warning("TESTNET rejimida ishlamoqda")
    else:
        log.warning("MAINNET rejimi — real pul bilan ishlanmoqda")

    try:
        await dp.start_polling(bot)
    finally:
        await watcher.stop()
        for task in tasks:
            task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("To'xtatildi")
