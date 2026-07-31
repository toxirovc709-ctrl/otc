from __future__ import annotations

import json
import logging
from html import escape
from pathlib import Path

log = logging.getLogger(__name__)

_CONFIG = Path(__file__).resolve().parent.parent / "premium_emoji.json"

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
