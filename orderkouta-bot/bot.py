#!/usr/bin/env python3
# bot.py
import asyncio, json, os, random, string, time
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from mutasi_client import fetch_mutasi

CONFIG_PATH = os.environ.get("CONFIG_PATH", "config.json")
UI_PATH     = os.environ.get("UI_PATH", "ui.json")
STATE_PATH  = os.environ.get("STATE_PATH",  "orders_state.json")

# --------- IO helpers ---------
def load_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path: str, obj: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp, path)

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def rupiah(n: int) -> str:
    return f"Rp{n:,}".replace(",", ".")

def gen_order_id(prefix: str = "OK") -> str:
    import time, random, string
    suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f\"{prefix}-{int(time.time())}-{suffix}\"

def calc_unique_amount(base: int, uniq_digits: int) -> int:
    ceil = 10 ** uniq_digits - 1
    return base + random.randint(1, ceil)

def order_summary(o: Dict[str, Any]) -> str:
    exp = datetime.fromisoformat(o["expires_at"]).astimezone()
    return (
        f\"Order ID: {o['order_id']}\\n\"
        f\"Produk: {o['product_code']} - {o['product_name']}\\n\"
        f\"Nominal bayar: {rupiah(o['amount_expected'])}\\n\"
        f\"Kadaluarsa: {exp:%d %b %Y %H:%M:%S %Z}\\n\"
        f\"Status: {o['status']}\"
    )

def build_products_kb(products: Dict[str, Any], ui: Dict[str, Any]) -> InlineKeyboardMarkup:
    rows = []
    for code, p in products.items():
        rows.append([InlineKeyboardButton(f\"{p['name']} • {rupiah(int(p['price']))}\", callback_data=f\"order:{code}\")])
    cancel = ui.get(\"menu_titles\", {}).get(\"cancel\", \"❌ Batal\")
    rows.append([InlineKeyboardButton(cancel, callback_data=\"cancel:0\")])
    return InlineKeyboardMarkup(rows)

def match_tx_for_order(order: Dict[str, Any], tx: Dict[str, Any]) -> bool:
    if tx[\"amount\"] is None:
        return False
    if tx[\"amount\"] != order[\"amount_expected\"]:
        return False
    # amount unique is the main discriminator for static QR
    return True

async def confirm_orders_via_mutasi(state: Dict[str, Any], cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    txs = await fetch_mutasi(cfg)
    confirmed = []
    for oid, o in list(state[\"orders\"].items()):
        if o[\"status\"] != \"PENDING\":
            continue
        if now_utc() > datetime.fromisoformat(o[\"expires_at\"]):
            o[\"status\"] = \"EXPIRED\"
            continue
        for tx in txs:
            if match_tx_for_order(o, tx):
                o[\"status\"] = \"PAID\"
                o[\"paid_ref\"] = tx.get(\"ref\")
                o[\"paid_at\"]  = tx.get(\"time\") or now_utc().isoformat()
                confirmed.append(o)
                break
    if confirmed:
        save_json(STATE_PATH, state)
    return confirmed

# --------- Handlers ---------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ui = context.bot_data[\"ui\"]
    cfg = context.bot_data[\"cfg\"]
    text = ui.get(\"welcome\", \"Pilih produk di bawah.\")
    await update.effective_message.reply_text(text)

    title = ui.get(\"menu_titles\", {}).get(\"pick_package\", \"Pilih paket:\")
    kb = build_products_kb(cfg[\"products\"], ui)
    await update.effective_message.reply_text(title, reply_markup=kb)

async def cmd_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ui = context.bot_data[\"ui\"]
    cfg = context.bot_data[\"cfg\"]
    title = ui.get(\"menu_titles\", {}).get(\"pick_package\", \"Pilih paket:\")
    kb = build_products_kb(cfg[\"products\"], ui)
    await update.effective_message.reply_text(title, reply_markup=kb)

async def cmd_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ui = context.bot_data[\"ui\"]
    await update.effective_message.reply_text(ui.get(\"info\", \"-\"))

async def cmd_tos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ui = context.bot_data[\"ui\"]
    await update.effective_message.reply_text(ui.get(\"tos\", \"-\"))

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ui = context.bot_data[\"ui\"]
    await update.effective_message.reply_text(ui.get(\"help\", \"-\"))

async def cmd_trial(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ui = context.bot_data[\"ui\"]
    trial = ui.get(\"trial\", {})
    if not trial.get(\"enabled\", False):
        await update.effective_message.reply_text(\"Trial tidak tersedia.\")
        return
    await update.effective_message.reply_text(trial.get(\"response\", \"Trial aktif.\"))

async def cb_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = context.bot_data[\"cfg\"]
    ui  = context.bot_data[\"ui\"]
    state = context.bot_data[\"state\"]
    q = update.callback_query
    await q.answer()
    _, code = q.data.split(\":\", 1)
    if code == \"0\":  # cancel
        await q.edit_message_text(\"Dibatalkan.\")
        return

    product = cfg[\"products\"].get(code)
    if not product:
        await q.edit_message_text(\"Produk tidak ditemukan.\")
        return

    base = int(product[\"price\"])
    uniq_digits = int(cfg[\"payments\"].get(\"unique_digits\", 3))
    amount = calc_unique_amount(base, uniq_digits)
    order_id = gen_order_id(cfg[\"payments\"].get(\"order_prefix\", \"OK\"))

    ttl_min = int(cfg[\"payments\"].get(\"payment_window_min\", 15))
    created_at = now_utc()
    expires_at = created_at + timedelta(minutes=ttl_min)

    o = {
        \"order_id\": order_id,
        \"user_id\": update.effective_user.id,
        \"product_code\": code,
        \"product_name\": product[\"name\"],
        \"amount_expected\": amount,
        \"status\": \"PENDING\",
        \"created_at\": created_at.isoformat(),
        \"expires_at\": expires_at.isoformat(),
        \"invite_link\": product[\"invite_link\"],
    }
    state[\"orders\"][order_id] = o
    save_json(STATE_PATH, state)

    qris_info = cfg[\"payments\"].get(\"qris_info\", \"Scan QRIS statis berikut dan bayar sesuai nominal unik.\")
    qris_img  = cfg[\"payments\"].get(\"qris_image_url\")
    hdr = context.bot_data[\"ui\"].get(\"order_texts\", {}).get(\"created_header\", \"✅ Order dibuat.\")
    must= context.bot_data[\"ui\"].get(\"order_texts\", {}).get(\"must_pay_exact\", \"WAJIB bayar sesuai nominal unik:\")

    instr = (
        f\"{hdr}\\n\\n{order_summary(o)}\\n\\n\"
        f\"{qris_info}\\n{must} *{rupiah(amount)}*.\\n\"
        f\"Setelah terdeteksi, bot akan kirim invite link.\"
    )
    if qris_img:
        await q.edit_message_media(media={\"type\": \"photo\", \"media\": qris_img, \"caption\": instr, \"parse_mode\": \"Markdown\"})
    else:
        await q.edit_message_text(instr, parse_mode=\"Markdown\")

async def cmd_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = context.bot_data[\"state\"]
    uid = update.effective_user.id
    user_orders = [o for o in state[\"orders\"].values() if o[\"user_id\"] == uid]
    if not user_orders:
        await update.effective_message.reply_text(\"Tidak ada order Anda.\")
        return
    lines = []
    for o in sorted(user_orders, key=lambda x: x[\"created_at\"], reverse=True)[:10]:
        lines.append(order_summary(o))
    await update.effective_message.reply_text(\"\\n\\n\".join(lines))

async def worker_mutasi(app: Application) -> None:
    cfg = app.bot_data[\"cfg\"]
    state = app.bot_data[\"state\"]
    ui = app.bot_data[\"ui\"]
    interval = int(cfg[\"mutasi\"].get(\"poll_interval_sec\", 20))
    while True:
        try:
            confirmed = await confirm_orders_via_mutasi(state, cfg)
            for o in confirmed:
                try:
                    chat_id = o.get(\"user_id\")
                    msg = (
                        f\"{ui.get('order_texts',{}).get('postpay_msg','Pembayaran TERKONFIRMASI')}\\n\"
                        f\"Order {o['order_id']}\\n\\n\"
                        f\"Produk: {o['product_name']} ({o['product_code']})\\n\"
                        f\"Nominal: {rupiah(o['amount_expected'])}\\n\"
                        f\"Ref: {o.get('paid_ref','-')}\\n\\n\"
                        f\"Invite link Anda:\\n{o['invite_link']}\"
                    )
                    await app.bot.send_message(chat_id, msg, parse_mode=\"Markdown\", disable_web_page_preview=True)
                except Exception as e:
                    print(\"send_message error:\", e)
        except Exception as e:
            print(\"worker_mutasi error:\", e)
        await asyncio.sleep(interval)

def validate_config(cfg: Dict[str, Any]) -> None:
    def getpath(d, path):
        cur = d
        for k in path.split(\".\"):
            cur = cur.get(k) if isinstance(cur, dict) else None
        return cur
    required = [\"telegram_bot_token\", \"mutasi.url\", \"mutasi.auth_username\", \"mutasi.auth_token\", \"products\"]
    for key in required:
        if getpath(cfg, key) in [None, \"\"]:
            raise ValueError(f\"Config '{key}' wajib diisi\")

async def main() -> None:
    # load files
    cfg = load_json(CONFIG_PATH)
    if not cfg:
        raise SystemExit(\"config.json tidak ditemukan. Salin dari config.json.example\")
    ui  = load_json(UI_PATH)
    if not ui:
        raise SystemExit(\"ui.json tidak ditemukan. Salin dari ui.json.example\")
    validate_config(cfg)

    # state file
    state = load_json(STATE_PATH) or {\"orders\": {}}
    save_json(STATE_PATH, state)  # ensure exists

    app = Application.builder().token(cfg[\"telegram_bot_token\"]).build()
    app.bot_data[\"cfg\"] = cfg
    app.bot_data[\"ui\"] = ui
    app.bot_data[\"state\"] = state

    app.add_handler(CommandHandler(\"start\", cmd_start))
    app.add_handler(CommandHandler(\"order\", cmd_order))
    app.add_handler(CommandHandler(\"info\", cmd_info))
    app.add_handler(CommandHandler(\"tos\", cmd_tos))
    app.add_handler(CommandHandler(\"help\", cmd_help))
    app.add_handler(CommandHandler(\"trialvip\", cmd_trial))
    app.add_handler(CommandHandler(\"orders\", cmd_orders))
    app.add_handler(CallbackQueryHandler(cb_order, pattern=r\"^(order|cancel):\"))

    asyncio.create_task(worker_mutasi(app))
    print(\"Bot started.\")
    await app.run_polling(close_loop=False)

if __name__ == \"__main__\":
    asyncio.run(main())
