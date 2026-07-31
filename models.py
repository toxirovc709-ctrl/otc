from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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
