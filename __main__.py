from __future__ import annotations

import asyncio
import logging
import sys
from decimal import Decimal

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy import select

from bot.config import settings
from bot.database.models import Deal, DealStatus, User
from bot.database.session import get_session, init_db
from bot.emoji import load_ids
from bot.handlers.main import emo, router
from bot.locales.texts import t
from bot.services import deal_service, payout_service
from bot.services.payout_service import TonSender, process_payout_queue
from bot.services.ton_watcher import TonWatcher

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
                affected = await deal_service.expire_stale_deals(s)

                for deal_id, new_status in affected:
                    deal = (
                        await s.execute(select(Deal).where(Deal.id == deal_id))
                    ).scalar_one()

                    if new_status == DealStatus.REFUND_PENDING and deal.buyer_wallet:
                        await payout_service.queue_payout(
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
            if settings.payouts_enabled:
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
    dp.include_router(router)

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
