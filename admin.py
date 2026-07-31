from __future__ import annotations

import logging
from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select, update

from bot.config import settings
from bot.database.models import (
    Deal,
    DealEvent,
    DealStatus,
    Payout,
    ProcessedTx,
    User,
    utcnow,
)
from bot.database.session import get_session
from bot.emoji import esc

log = logging.getLogger(__name__)
router = Router()

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


@router.message(Command("admin"))
async def admin_entry(message: Message):
    if not is_admin(message.from_user.id):
        return  # javob bermaymiz — panel borligini bildirmaslik uchun

    state = "🟢 yoqiq" if RUNTIME_PAYOUTS_ENABLED else "🔴 to'xtatilgan"
    await message.answer(
        f"<b>Admin panel</b>\n\nAvtomatik chiqim: {state}",
        reply_markup=admin_menu_kb(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "adm:stats")
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


@router.callback_query(F.data == "adm:orphans")
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


@router.callback_query(F.data == "adm:failed")
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


@router.callback_query(F.data == "adm:approvals")
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


@router.callback_query(F.data.startswith("adm:ok:"))
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


@router.callback_query(F.data == "adm:kill")
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


@router.callback_query(F.data == "adm:resume")
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


@router.callback_query(F.data == "adm:back")
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


@router.message(Command("deal"))
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
