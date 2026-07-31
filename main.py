from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from bot.config import settings
from bot.database.models import Deal, DealKind, DealStatus, User, Wallet
from bot.database.session import get_session
from bot.emoji import e, esc
from bot.keyboards.inline import (
    back_kb,
    deal_type_kb,
    language_kb,
    main_menu_kb,
    settings_kb,
    share_deal_kb,
    wallet_kb,
)
from bot.locales.texts import t
from bot.services import deal_service

log = logging.getLogger(__name__)
router = Router()

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
@router.message(CommandStart())
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


@router.callback_query(F.data.startswith("lang:"))
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


@router.callback_query(F.data == "menu")
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
@router.callback_query(F.data == "wallet")
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


@router.callback_query(F.data == "wallet:add")
async def wallet_add(call: CallbackQuery, state: FSMContext):
    lang = await get_lang(call.from_user.id)
    await state.set_state(WalletForm.address)
    await call.message.edit_text(
        t("wallet_ask_address", lang, **emo()),
        reply_markup=back_kb(lang),
        parse_mode="HTML",
    )
    await call.answer()


@router.message(WalletForm.address)
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
@router.callback_query(F.data == "deal:new")
async def deal_new(call: CallbackQuery):
    lang = await get_lang(call.from_user.id)
    await call.message.edit_text(
        t("deal_choose_type", lang, **emo()),
        reply_markup=deal_type_kb(lang),
        parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(F.data.startswith("deal:kind:"))
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


@router.message(DealForm.description)
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


@router.message(DealForm.amount)
async def deal_amount(message: Message, state: FSMContext):
    lang = await get_lang(message.from_user.id)
    raw = (message.text or "").strip().replace(",", ".")

    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        await message.answer(t("deal_bad_amount", lang, **emo()), parse_mode="HTML")
        return

    try:
        deal_service.validate_amount(amount)
    except deal_service.DealError as exc:
        await message.answer(f"{e('cross')} {esc(str(exc))}", parse_mode="HTML")
        return

    await state.update_data(amount=str(amount))
    await state.set_state(DealForm.wallet)
    await message.answer(
        t("deal_ask_wallet", lang, **emo()),
        reply_markup=back_kb(lang),
        parse_mode="HTML",
    )


@router.message(DealForm.wallet)
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
            deal = await deal_service.create_deal(
                s,
                seller_id=message.from_user.id,
                description=data["description"],
                amount=Decimal(data["amount"]),
                kind=DealKind(data.get("kind", "gift")),
                seller_wallet=address,
            )
            deal_id, code = deal.id, deal.payment_code
            amount, fee, total = deal.amount_ton, deal.fee_ton, deal.total_ton
    except deal_service.DealError as exc:
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
            deal = await deal_service.attach_buyer(s, deal.id, message.from_user.id)
        except deal_service.DealError as exc:
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
@router.callback_query(F.data == "settings")
async def show_settings(call: CallbackQuery):
    lang = await get_lang(call.from_user.id)
    await call.message.edit_text(
        t("settings_title", lang, **emo()),
        reply_markup=settings_kb(lang),
        parse_mode="HTML",
    )
    await call.answer()


@router.callback_query(F.data == "language")
async def change_language(call: CallbackQuery):
    lang = await get_lang(call.from_user.id)
    await call.message.edit_text(
        t("choose_language", lang), reply_markup=language_kb()
    )
    await call.answer()


@router.callback_query(F.data == "noop")
async def noop(call: CallbackQuery):
    await call.answer()
