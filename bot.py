"""
======================================================================
  RESTORAN / CAFE uchun TELEGRAM BOT — DEMO
======================================================================

Bu bot nima qiladi:
  • Mijozga menyuni ko'rsatadi (bo'limlar bo'yicha)
  • Savatga taom qo'shadi, jami summani hisoblaydi
  • Mijozdan ism va telefon raqamini oladi
  • Buyurtmani bazaga (SQLite) saqlaydi
  • Restoran egasiga avtomatik xabar yuboradi  <-- sotuvning eng kuchli qismi

ISHGA TUSHIRISH:
  1) pip install python-telegram-bot
  2) @BotFather dan bot oching, TOKEN oling
  3) Pastdagi BOT_TOKEN va OWNER_CHAT_ID ni to'ldiring
     (OWNER_CHAT_ID — egasining Telegram chat ID si.
      Buni bilish uchun egasi @userinfobot ga /start yozsa, ID chiqadi.)
  4) python bot.py

HAR YANGI MIJOZ UCHUN: faqat RESTAURANT_NAME va MENU ni o'zgartirasiz.
======================================================================
"""

import os
import logging
import sqlite3
from datetime import datetime

# .env faylidan sozlamalarni o'qiydi (lokal ishlatishda).
# Render serverida sozlamalar dashboarddan beriladi — bu zarar qilmaydi.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ---------------------------------------------------------------------
# SOZLAMALAR  (har mijoz uchun shu yerni o'zgartirasiz)
# ---------------------------------------------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_CHAT_ID = os.environ.get("OWNER_CHAT_ID", "")  # egasining chat ID si

if not BOT_TOKEN:
    raise SystemExit(
        "BOT_TOKEN topilmadi! .env faylga BOT_TOKEN=... yozing "
        "(yoki Render'da Environment bo'limiga qo'shing)."
    )

# DATABASE_URL bo'lsa — Neon (Postgres). Bo'lmasa — lokal SQLite (orders.db).
DATABASE_URL = os.environ.get("DATABASE_URL", "")
USE_POSTGRES = bool(DATABASE_URL)
RESTAURANT_NAME = "Osh Markazi"

# Menyu — bo'limlar va taomlar. Narx — so'mda.
# Qo'shimchalar (extras) — ixtiyoriy. Taomga "extras" qo'shsangiz,
# o'sha taom tanlanganda mijoz ulardan belgilab qo'sha oladi.
MENU = {
    "🍲 Taomlar": [
        {"id": "osh",     "name": "Osh",      "price": 35000, "extras": [
            {"id": "tuxum", "name": "Tuxum",       "price":  3000},
            {"id": "qazi",  "name": "Qazi",        "price": 15000},
            {"id": "gosht", "name": "Qo'shimcha go'sht", "price": 12000},
        ]},
        {"id": "lagman",  "name": "Lag'mon",  "price": 32000},
        {"id": "manti",   "name": "Manti",    "price": 28000},
        {"id": "shorva",  "name": "Sho'rva",  "price": 30000},
        {"id": "norin",   "name": "Norin",    "price": 33000},
    ],
    "🥤 Ichimliklar": [
        {"id": "choy",    "name": "Choy",     "price":  5000, "vol": "1 l"},
        {"id": "kofe",    "name": "Kofe",     "price": 15000, "vol": "0.2 l"},
        {"id": "kola",    "name": "Kola",     "price": 10000, "vol": "0.5 l"},
        {"id": "ayron",   "name": "Ayron",    "price":  8000, "vol": "0.5 l"},
    ],
    "🍰 Shirinliklar": [
        {"id": "chakchak", "name": "Chak-chak", "price": 18000},
        {"id": "tort",     "name": "Tort bo'lagi", "price": 22000},
    ],
}

# Taomni id bo'yicha tez topish uchun jadval
ITEM_LOOKUP = {item["id"]: item for items in MENU.values() for item in items}

# Suhbat bosqichlari (buyurtma berishda)
ASK_NAME, ASK_PHONE = range(2)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)


# ---------------------------------------------------------------------
# YORDAMCHI FUNKSIYALAR
# ---------------------------------------------------------------------
def fmt(n: int) -> str:
    """35000 -> '35 000' (o'qishga qulay)"""
    return f"{n:,}".replace(",", " ")


def item_label(item: dict) -> str:
    """Taom nomi (ichimlik bo'lsa litri bilan): 'Kola 0.5 l'."""
    if item.get("vol"):
        return f"{item['name']} {item['vol']}"
    return item["name"]


def _add_to_cart(cart: list, entry: dict):
    """Bir xil yozuv bo'lsa sonini oshiradi, bo'lmasa yangi qator qo'shadi."""
    for e in cart:
        if e["name"] == entry["name"] and e["price"] == entry["price"]:
            e["qty"] += 1
            return
    entry["qty"] = 1
    cart.append(entry)


def format_items(cart: list):
    """Savatdagi taomlar ro'yxati va jami summani qaytaradi.

    cart — yozuvlar ro'yxati. Har bir yozuv:
      {"name": "Osh (Tuxum x2)", "price": 41000, "qty": 1}
      (price — bitta dona narxi)
    """
    lines = []
    total = 0
    for e in cart:
        line_total = e["price"] * e["qty"]
        total += line_total
        lines.append(f"• {e['name']} x{e['qty']} — {fmt(line_total)} so'm")
    return "\n".join(lines), total


def get_conn():
    """Neon (Postgres) yoki lokal SQLite ulanishini qaytaradi."""
    if USE_POSTGRES:
        import psycopg2
        return psycopg2.connect(DATABASE_URL)
    return sqlite3.connect("orders.db")


def init_db():
    id_col = "SERIAL PRIMARY KEY" if USE_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        f"""CREATE TABLE IF NOT EXISTS orders (
            id            {id_col},
            created_at    TEXT,
            customer_name TEXT,
            phone         TEXT,
            items         TEXT,
            total         INTEGER,
            telegram_user TEXT
        )"""
    )
    conn.commit()
    conn.close()


def save_order(name, phone, items_text, total, tg_user) -> int:
    ph = "%s" if USE_POSTGRES else "?"
    values = (datetime.now().isoformat(timespec="seconds"), name, phone, items_text, total, tg_user)
    conn = get_conn()
    cur = conn.cursor()
    sql = (
        f"INSERT INTO orders (created_at, customer_name, phone, items, total, telegram_user) "
        f"VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph})"
    )
    if USE_POSTGRES:
        cur.execute(sql + " RETURNING id", values)
        order_id = cur.fetchone()[0]
    else:
        cur.execute(sql, values)
        order_id = cur.lastrowid
    conn.commit()
    conn.close()
    return order_id


# ---------------------------------------------------------------------
# HANDLERLAR
# ---------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("📋 Menyu", callback_data="menu")]]
    await update.message.reply_text(
        f"Assalomu alaykum! \"{RESTAURANT_NAME}\" ga xush kelibsiz 👋\n\n"
        f"Menyuni ko'rish va buyurtma berish uchun pastdagi tugmani bosing.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    buttons = [[InlineKeyboardButton(cat, callback_data=f"cat:{cat}")] for cat in MENU]
    buttons.append([InlineKeyboardButton("🛒 Savat", callback_data="cart")])
    await query.edit_message_text("Bo'limni tanlang:", reply_markup=InlineKeyboardMarkup(buttons))


async def show_items(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat = query.data.split(":", 1)[1]
    buttons = []
    for it in MENU.get(cat, []):
        label = f"{item_label(it)} — {fmt(it['price'])} so'm"
        buttons.append([InlineKeyboardButton(label, callback_data=f"add:{it['id']}")])
    buttons.append([
        InlineKeyboardButton("⬅️ Orqaga", callback_data="menu"),
        InlineKeyboardButton("🛒 Savat", callback_data="cart"),
    ])
    await query.edit_message_text(f"{cat}:", reply_markup=InlineKeyboardMarkup(buttons))


def _build_entry(item_id: str, chosen):
    """Tanlangan taom + qo'shimchalardan savat yozuvini quradi.

    chosen — {extra_id: soni} ko'rinishidagi lug'at.
    """
    item = ITEM_LOOKUP[item_id]
    price = item["price"]
    extra_names = []
    for ex in item.get("extras", []):
        qty = chosen.get(ex["id"], 0)
        if qty > 0:
            price += ex["price"] * qty
            extra_names.append(ex["name"] if qty == 1 else f"{ex['name']} x{qty}")
    name = item_label(item)
    if extra_names:
        name += " (" + ", ".join(extra_names) + ")"
    return {"name": name, "price": price}


async def _render_extras(query, item_id, chosen):
    """Qo'shimcha tanlash sahifasi: har biriga ➖ / soni / ➕ tugmalari."""
    item = ITEM_LOOKUP[item_id]
    buttons = []
    for ex in item.get("extras", []):
        qty = chosen.get(ex["id"], 0)
        # Sonni oldinga qo'yamiz — Telegram uzun yorliqni qisqartirsa ham ko'rinadi
        prefix = f"{qty}× " if qty > 0 else ""
        label = f"{prefix}{ex['name']} +{fmt(ex['price'])}"
        buttons.append([
            InlineKeyboardButton("➖", callback_data=f"exm:{ex['id']}"),
            InlineKeyboardButton(label, callback_data=f"exp:{ex['id']}"),
            InlineKeyboardButton("➕", callback_data=f"exp:{ex['id']}"),
        ])
    entry = _build_entry(item_id, chosen)
    buttons.append([InlineKeyboardButton(f"✅ Savatga qo'shish — {fmt(entry['price'])} so'm",
                                         callback_data="addcart")])
    buttons.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="menu")])
    await query.edit_message_text(
        f"{item['name']} — {fmt(item['price'])} so'm\n\n"
        f"Qo'shimchalarni tanlang (➕ bosib sonini ko'paytiring):",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def add_to_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    item_id = query.data.split(":", 1)[1]
    item = ITEM_LOOKUP[item_id]

    # Qo'shimchasi bor taom — avval tanlash sahifasini ochamiz
    if item.get("extras"):
        context.user_data["selecting"] = {"item_id": item_id, "extras": {}}
        await query.answer()
        await _render_extras(query, item_id, {})
        return

    # Qo'shimchasiz taom — to'g'ridan-to'g'ri savatga
    cart = context.user_data.setdefault("cart", [])
    _add_to_cart(cart, _build_entry(item_id, {}))
    await query.answer(f"{item_label(item)} savatga qo'shildi ✅")


async def change_extra(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """➕ (exp:) sonini oshiradi, ➖ (exm:) kamaytiradi."""
    query = update.callback_query
    sel = context.user_data.get("selecting")
    if not sel:
        await query.answer()
        return
    action, extra_id = query.data.split(":", 1)
    chosen = sel["extras"]
    qty = chosen.get(extra_id, 0) + (1 if action == "exp" else -1)
    if qty <= 0:
        chosen.pop(extra_id, None)
    else:
        chosen[extra_id] = qty
    await query.answer()
    await _render_extras(query, sel["item_id"], chosen)


async def add_selected_to_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    sel = context.user_data.get("selecting")
    if not sel:
        await query.answer()
        return
    cart = context.user_data.setdefault("cart", [])
    entry = _build_entry(sel["item_id"], sel["extras"])
    _add_to_cart(cart, entry)
    context.user_data.pop("selecting", None)
    await query.answer(f"{entry['name']} savatga qo'shildi ✅")
    # Bo'limlarga qaytamiz
    buttons = [[InlineKeyboardButton(cat, callback_data=f"cat:{cat}")] for cat in MENU]
    buttons.append([InlineKeyboardButton("🛒 Savat", callback_data="cart")])
    await query.edit_message_text("Bo'limni tanlang:", reply_markup=InlineKeyboardMarkup(buttons))


async def _render_cart(query, context: ContextTypes.DEFAULT_TYPE):
    """Savatni har bir qatorga ➖/➕ tahrir tugmalari bilan chizadi."""
    cart = context.user_data.get("cart", [])
    if not cart:
        buttons = [[InlineKeyboardButton("📋 Menyu", callback_data="menu")]]
        await query.edit_message_text("Savatingiz bo'sh.", reply_markup=InlineKeyboardMarkup(buttons))
        return
    items_text, total = format_items(cart)
    text = f"🛒 Sizning buyurtmangiz:\n\n{items_text}\n\n💰 Jami: {fmt(total)} so'm"
    buttons = []
    for i, e in enumerate(cart):
        buttons.append([
            InlineKeyboardButton("➖", callback_data=f"cm:{i}"),
            InlineKeyboardButton(f"{e['name']} ×{e['qty']}", callback_data="noop"),
            InlineKeyboardButton("➕", callback_data=f"cp:{i}"),
        ])
    buttons.append([InlineKeyboardButton("✅ Buyurtma berish", callback_data="checkout")])
    buttons.append([
        InlineKeyboardButton("🗑 Tozalash", callback_data="clear"),
        InlineKeyboardButton("📋 Menyu", callback_data="menu"),
    ])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def show_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _render_cart(query, context)


async def cart_change(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Savatdagi qator sonini o'zgartiradi: cp: +1, cm: -1 (0 da o'chadi)."""
    query = update.callback_query
    cart = context.user_data.get("cart", [])
    action, idx = query.data.split(":", 1)
    i = int(idx)
    if 0 <= i < len(cart):
        if action == "cp":
            cart[i]["qty"] += 1
        else:
            cart[i]["qty"] -= 1
            if cart[i]["qty"] <= 0:
                cart.pop(i)
    await query.answer()
    await _render_cart(query, context)


async def noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Faqat nom ko'rsatuvchi tugma — hech narsa qilmaydi
    await update.callback_query.answer()


async def clear_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Savat tozalandi")
    context.user_data["cart"] = []
    buttons = [[InlineKeyboardButton("📋 Menyu", callback_data="menu")]]
    await query.edit_message_text("Savat tozalandi.", reply_markup=InlineKeyboardMarkup(buttons))


# ---- Buyurtma berish suhbati (ism -> telefon -> tasdiq) ----
async def checkout_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not context.user_data.get("cart"):
        await query.edit_message_text("Savatingiz bo'sh.")
        return ConversationHandler.END
    await query.edit_message_text("Buyurtmani rasmiylashtiramiz.\n\nIsmingizni yozing:")
    return ASK_NAME


async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["customer_name"] = update.message.text.strip()
    button = KeyboardButton("📱 Raqamni yuborish", request_contact=True)
    markup = ReplyKeyboardMarkup([[button]], resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(
        "Endi telefon raqamingizni yuboring (tugmani bosing yoki qo'lda yozing):",
        reply_markup=markup,
    )
    return ASK_PHONE


async def finish_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.contact:
        phone = update.message.contact.phone_number
    else:
        phone = update.message.text.strip()

    name = context.user_data.get("customer_name", "Noma'lum")
    cart = context.user_data.get("cart", [])
    items_text, total = format_items(cart)
    tg_user = update.effective_user.username or str(update.effective_user.id)

    order_id = save_order(name, phone, items_text, total, tg_user)

    # Restoran egasiga avtomatik xabar
    if OWNER_CHAT_ID:
        owner_msg = (
            f"🔔 YANGI BUYURTMA  #{order_id}\n\n"
            f"👤 {name}\n"
            f"📞 {phone}\n\n"
            f"{items_text}\n\n"
            f"💰 Jami: {fmt(total)} so'm"
        )
        try:
            await context.bot.send_message(chat_id=int(OWNER_CHAT_ID), text=owner_msg)
        except Exception as e:
            logging.warning(f"Egaga xabar yuborilmadi: {e}")

    await update.message.reply_text(
        f"Rahmat, {name}! Buyurtmangiz qabul qilindi ✅\n\n"
        f"Buyurtma raqami: #{order_id}\n"
        f"💰 Jami: {fmt(total)} so'm\n\n"
        f"Tez orada siz bilan bog'lanamiz.",
        reply_markup=ReplyKeyboardRemove(),
    )
    context.user_data["cart"] = []
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bekor qilindi.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ---------------------------------------------------------------------
# ASOSIY
# ---------------------------------------------------------------------
def main():
    init_db()
    # Sekin/beqaror ulanishda uzilib qolmasligi uchun kutish vaqtlari oshirilgan
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .build()
    )

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(checkout_start, pattern="^checkout$")],
        states={
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_phone)],
            ASK_PHONE: [MessageHandler((filters.CONTACT | filters.TEXT) & ~filters.COMMAND, finish_order)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(show_categories, pattern="^menu$"))
    app.add_handler(CallbackQueryHandler(show_items, pattern="^cat:"))
    app.add_handler(CallbackQueryHandler(add_to_cart, pattern="^add:"))
    app.add_handler(CallbackQueryHandler(change_extra, pattern="^ex[pm]:"))
    app.add_handler(CallbackQueryHandler(add_selected_to_cart, pattern="^addcart$"))
    app.add_handler(CallbackQueryHandler(show_cart, pattern="^cart$"))
    app.add_handler(CallbackQueryHandler(cart_change, pattern="^c[mp]:"))
    app.add_handler(CallbackQueryHandler(noop, pattern="^noop$"))
    app.add_handler(CallbackQueryHandler(clear_cart, pattern="^clear$"))

    # Render (yoki boshqa web hosting) PORT va tashqi URL beradi -> webhook rejimi.
    # Aks holda (lokal kompyuter) -> polling rejimi.
    port = int(os.environ.get("PORT", "0"))
    webhook_base = os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("WEBHOOK_URL", "")

    if port and webhook_base:
        print(f"Webhook rejimi: {webhook_base}")
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=BOT_TOKEN,
            webhook_url=f"{webhook_base.rstrip('/')}/{BOT_TOKEN}",
        )
    else:
        print("Polling rejimi (lokal)...")
        app.run_polling()


if __name__ == "__main__":
    main()
