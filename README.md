# Gift Garant Bot — bitta faylli versiya

Butun bot `bot.py` ichida. Papka strukturasi kerak emas.

## Ishga tushirish

```bash
pip install -r requirements.txt
python bot.py
```

Sozlamalar `.env` faylidan **yoki** muhit o'zgaruvchilaridan o'qiladi.
Railway'da deploy qilsangiz `.env` kerak emas — Variables bo'limiga
yozasiz.

## Kerakli o'zgaruvchilar

| O'zgaruvchi | Nima |
|---|---|
| `BOT_TOKEN` | @BotFather bergan token |
| `ADMIN_IDS` | Admin Telegram ID'lari, vergul bilan |
| `ESCROW_WALLET_ADDRESS` | To'lov keladigan TON hamyon |
| `ESCROW_MNEMONIC` | Chiqim uchun 24 so'z — **hech qachon git'ga qo'ymang** |
| `TON_IS_TESTNET` | Boshida `true` |
| `TON_API_BASE` | testnet: `https://testnet.toncenter.com/api/v3` |

Qolganlari `.env.example` da, standart qiymatlari bilan.

## Buyruqlar

- `/start` — bot menyusi
- `/admin` — admin panel (faqat `ADMIN_IDS` uchun)
- `/deal 12` — bitim tarixi

## Fayl ichidagi bo'limlar

`bot.py` ichida izohli bo'limlar bor, shu tartibda:

1. Sozlamalar
2. Baza modellari
3. Bazaga ulanish
4. Premium emoji
5. Matnlar (UZ / RU / EN)
6. Tugmalar
7. Bitim xizmati
8. Chiqim xizmati
9. Sovg'a xizmati
10. TON kuzatuvchi
11. Admin panel
12. Asosiy handlerlar
13. Ishga tushirish

Kerakli joyni topish uchun `# ====` chizig'ini qidiring.

## Mainnet'dan oldin

`TON_IS_TESTNET=true` bilan quyidagilarni sinang:

- [ ] To'liq bitim: yaratish → to'lov → sovg'a → chiqim
- [ ] Comment'siz to'lov → `/admin` da "bog'lanmagan" bo'limida ko'rindi
- [ ] Kam summa → bitim to'lov kutishda qoldi
- [ ] Bitta to'lov ikki marta o'qildi (botni qayta ishga tushiring) →
      ikkinchi marta hisoblanmadi
- [ ] Sotuvchi sovg'ani yubormadi → 30 daqiqadan keyin qaytarish navbatga tushdi
- [ ] Qaytarish faqat pul kelgan manzilga ketdi
- [ ] Chiqim yuborilayotganda `Ctrl+C` → qayta ishga tushganda ikki marta yubormadi
- [ ] `/admin` → "Chiqimni to'xtatish" darhol ishladi
