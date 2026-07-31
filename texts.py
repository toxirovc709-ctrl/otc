from __future__ import annotations

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
