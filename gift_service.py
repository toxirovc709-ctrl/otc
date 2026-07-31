from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

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
