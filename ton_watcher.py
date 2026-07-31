from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from decimal import Decimal

import aiohttp
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from bot.config import settings
from bot.database.models import Deal, DealStatus, ProcessedTx, utcnow
from bot.database.session import get_session

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
        from bot.database.models import DealEvent

        s.add(DealEvent(deal_id=deal_id, event=event, actor_id=actor, payload=payload))
