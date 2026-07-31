from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from decimal import Decimal, ROUND_UP

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.database.models import (
    Deal,
    DealEvent,
    DealKind,
    DealStatus,
    TransferMode,
    utcnow,
)

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
