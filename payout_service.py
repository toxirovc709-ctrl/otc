from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import settings
from bot.database.models import Deal, DealEvent, Payout, utcnow

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

        from tonutils.client import ToncenterV3Client
        from tonutils.wallet import WalletV5R1

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
