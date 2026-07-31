# Gift Garant Bot

Telegram sovg'a, kanal va akkaunt savdolari uchun garant (escrow) bot.
Pul xaridordan olinadi, mol yetkazilgach sotuvchiga o'tkaziladi.

---

## Asosiy g'oya

Ko'p garant botlar "yubordim" / "oldim" tugmalariga tayanadi. Bu ochiq
teshik: xaridor sovg'ani olib, "kelmadi" deyishi mumkin va isbot yo'q.

Bu loyihada **hech kim hech narsani tasdiqlamaydi**. Bot ikkala hodisani
ham o'zi tekshiradi:

| Hodisa | Qanday tekshiriladi |
|---|---|
| To'lov keldimi | Blokcheyndan, unikal comment bo'yicha |
| Sovg'a o'tdimi | Telegram API orqali, `transferGift` natijasidan |

Yolg'on gapirishning texnik imkoni yo'q, demak nizo ham deyarli chiqmaydi.

---

## Bitim oqimi

```
DRAFT              sotuvchi bitim yaratdi, havola tarqatilmoqda
  ↓ xaridor havolani bosdi
AWAITING_PAYMENT   to'lov kutilmoqda (30 daq)
  ↓ watcher blokcheyndan to'lovni topdi
AWAITING_GIFT      sotuvchi sovg'ani o'tkazishi kerak (30 daq)
  ↓ transferGift muvaffaqiyatli
GIFT_DELIVERED
  ↓
PAYOUT_PENDING     chiqim navbatda
  ↓
COMPLETED
```

Uzilishlar: `EXPIRED` (to'lov kelmadi), `REFUND_PENDING` → `REFUNDED`
(pul keldi, sovg'a kelmadi), `DISPUTED` (qo'lda hal qilinadi).

---

## Ishonchlilik mexanizmlari

Bu qismlarni **o'zgartirishdan oldin yaxshilab o'ylang** — har biri aniq
bir xato turini oldini oladi.

**1. Takroriy to'lov hisoblanishi.**
`processed_tx.tx_hash` unikal. Watcher har bir tranzaksiyani avval shu
jadvalga yozadi; ikkinchi marta yozib bo'lmaydi, demak bir to'lov ikki
marta hisoblanmaydi.

**2. Parallel holat o'zgarishi.**
Bitim holati faqat `deal_service.transition()` orqali, shartli UPDATE
bilan o'zgaradi (`WHERE status = expected`). Kod hech qayerda
`deal.status = ...` deb to'g'ridan-to'g'ri yozmasligi kerak.

**3. Takroriy chiqim.**
`payouts` jadvalida `(deal_id, purpose)` unikal. Bitta bitim uchun
ikkinchi "payout" yozuvi yaratilmaydi.

**4. Yuborish paytida qulash.**
Chiqim TON yuborilishidan **oldin** `sending` holatiga o'tkaziladi va
commit qilinadi. Bot qulasa, yozuv `sending` da qoladi — avtomatik qayta
urinilmaydi, odam tekshiradi. Bu ataylab shunday: javob kelmagani pul
ketmaganini anglatmaydi.

**5. Chegaralar.**
`MAX_DEAL_TON`, `DAILY_PAYOUT_CAP_TON`, `MANUAL_APPROVAL_ABOVE_TON` va
`PAYOUTS_ENABLED` — to'rttasi ham xato tarqalishini cheklaydi.
`PAYOUTS_ENABLED=false` butun avtomatik chiqimni darhol to'xtatadi.

---

## O'rnatish

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # to'ldiring
python -m bot
```

---

## Mainnet'ga chiqishdan oldin majburiy tekshiruv

Har bandni **testnet'da** bajaring va natijani yozib boring.

- [ ] Oddiy bitim to'liq o'tdi: yaratish → to'lov → sovg'a → chiqim
- [ ] To'lov comment'siz yuborildi → `matched=False` bo'lib qoldi, pul
      yo'qolmadi
- [ ] Kam summa yuborildi → bitim `AWAITING_PAYMENT` da qoldi
- [ ] Ortiqcha summa yuborildi → qabul qilindi, farq loglandi
- [ ] Bitta to'lov ikki marta o'qildi (watcher qayta ishga tushirildi) →
      ikkinchi marta hisoblanmadi
- [ ] Sotuvchi sovg'ani yubormadi → 30 daqiqadan keyin qaytarish navbatga
      tushdi
- [ ] Qaytarish faqat **pul kelgan** manzilga ketdi
- [ ] Bot chiqim yuborish paytida to'xtatildi → qayta ishga tushganda
      ikkinchi marta yubormadi
- [ ] `PAYOUTS_ENABLED=false` chiqimni darhol to'xtatdi
- [ ] Kunlik chegara to'lganda chiqim to'xtadi
- [ ] Bir vaqtda 5 ta bitim ochildi → comment'lar aralashmadi

---

## Hali yozilmagan qismlar

Ochiq aytilsin — bu tugallangan mahsulot emas:

- ~~Admin paneli~~ ✅ yozildi (`/admin`, `/deal <id>`)
- **Sovg'a yetkazilishini yakuniy tasdiqlash oqimi** to'liq ulanmagan.
  `gift_service` tayyor, lekin uni biznes ulanish handleri bilan bog'lash
  kerak (`business_connection` update'ini qabul qilish).
- **Referal to'lovlari** hisoblanadi, lekin chiqarilmaydi.
- **Alembic migratsiyalari** sozlanmagan — hozircha `create_all`.
- **Testlar yo'q.** Kamida `ton_watcher` va `payout_service` uchun
  yozilishi shart.

---

## Xavflar

**Serverdagi mnemonic.** `ESCROW_MNEMONIC` — bu hamyondagi pulni to'liq
boshqaradi. Server buzilsa, pul ketadi. Shuning uchun: alohida "issiq
hamyon" tuting, unda faqat kunlik aylanmaga yetadigan miqdor saqlang,
qolganini alohida sovuq hamyonga o'tkazib turing.

**Huquqiy tomon.** Siz boshqa odamlarning mablag'ini vaqtincha ushlab
turasiz. Bu ko'p mamlakatlarda tartibga solingan faoliyat. Hajm o'sishidan
oldin o'z hududingizdagi holatni aniqlab oling.

**Obro'.** Garant xizmatida bitta noto'g'ri hal qilingan nizo yetarli.
`deal_events` jadvalini hech qachon o'chirmang — nizoda yagona dalilingiz
o'sha.

---

## Admin panel

`ADMIN_IDS` ro'yxatidagi foydalanuvchilar uchun:

| Buyruq | Vazifa |
|---|---|
| `/admin` | Asosiy panel |
| `/deal 12` | Bitimning to'liq tarixi (nizolarda ishlatiladi) |

Panel bo'limlari:

- **Statistika** — foydalanuvchilar, hajm, komissiya daromadi, bitimlar holati
- **Bog'lanmagan to'lovlar** — comment'siz kelgan pullar. Muntazam
  ko'rib turing, aks holda odam "pulim yo'qoldi" deb yozguncha
  xabaringiz bo'lmaydi
- **Muammoli chiqimlar** — `failed` va `sending` holatidagilar.
  `sending` degani bot TON yuborayotganda qulagan: pul ketgan yoki
  ketmagan bo'lishi mumkin, blokcheyndan qo'lda tekshiring
- **Tasdiq kutayotganlar** — `MANUAL_APPROVAL_ABOVE_TON` dan katta chiqimlar
- **🔴 Chiqimni to'xtatish** — shubhali narsa sezsangiz birinchi
  bosiladigan tugma. Botni o'chirishdan afzal: bitimlar ochiq qoladi,
  faqat pul chiqmaydi

---

## GitHub'ga joylash

Web interfeysdagi "Add files via upload" papkalarni saqlamaydi —
fayllar tekis tushadi va importlar buziladi. Git ishlating:

```bash
bash setup_git.sh https://github.com/FOYDALANUVCHI/REPO.git
```

Skript push'dan oldin `.env` commit'ga tushmayotganini tekshiradi va
tushayotgan bo'lsa to'xtatadi.

**Nega bu muhim:** `.env` ichida `ESCROW_MNEMONIC` bor — hamyondagi
pulni to'liq boshqaradigan 24 so'z. Ochiq repolarni botlar doimiy
skanerlaydi. Bir marta ko'ringan mnemonic — o'lgan mnemonic, uni
almashtirishdan boshqa yo'l yo'q.
