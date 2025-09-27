import os
import asyncio
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from sqlalchemy import select, delete
from app.models import SessionLocal, User, Account, LastSnapshot, SymbolSnapshot
from tzlocal import get_localzone
import secrets


# ==========================
# Очередь сообщений
# ==========================
message_queue = asyncio.Queue()


async def message_worker(bot: Bot):
    """Фоновая задача для отправки сообщений с учётом лимитов Telegram."""
    while True:
        try:
            chat_id, text, kwargs = await message_queue.get()
            try:
                await bot.send_message(chat_id=chat_id, text=text, **kwargs)
            except Exception as e:
                print(f"[MessageWorker] Ошибка отправки: {e}")
            finally:
                message_queue.task_done()

            await asyncio.sleep(0.05)  # не более ~20 msg/сек
        except Exception as e:
            print(f"[MessageWorker] Критическая ошибка: {e}, перезапуск...")
            await asyncio.sleep(1)


async def send_queued_message(chat_id: str, text: str, **kwargs):
    """Поставить сообщение в очередь."""
    await message_queue.put((chat_id, text, kwargs))


def get_drawdown_color(dd: float) -> str:
    if dd < -5:
        return "🔴"
    elif dd < -2:
        return "🟠"
    else:
        return "🟢"


# ==========================
# /start
# ==========================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.chat_id == chat_id))
        if not u:
            u = User(
                chat_id=chat_id,
                api_key=os.urandom(16).hex(),
                short_id=secrets.token_urlsafe(8),
            )
            s.add(u)
            s.commit()
    await cmd_accounts_menu(update, context)


# ==========================
# Меню счетов
# ==========================
async def cmd_accounts_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.chat_id == chat_id))
        if not u:
            await send_queued_message(chat_id, "Сначала /start")
            return

        accounts = s.scalars(select(Account).where(Account.api_key == u.api_key)).all()
        accounts.sort(key=lambda x: x.name.lower())

        buttons = []
        for acc in accounts:
            snap = s.scalar(
                select(LastSnapshot)
                .where(LastSnapshot.api_key == u.api_key)
                .where(LastSnapshot.account_id == acc.account_id)
                .order_by(LastSnapshot.last_seen.desc())
            )

            if not snap or (
                snap.last_seen
                and datetime.utcnow().replace(tzinfo=timezone.utc)
                - snap.last_seen.replace(tzinfo=timezone.utc)
                > timedelta(minutes=1)
            ):
                status_icon = "⚠️"
            else:
                status_icon = ""

            label = f"{status_icon} {acc.name}".strip()
            buttons.append(
                [InlineKeyboardButton(label, callback_data=f"acc:{acc.account_id}")]
            )

        host = os.getenv("WEB_HOST", "mtmonitor.ru:8000")
        scheme = "http"
        web_url = f"{scheme}://{host}/w/{u.short_id}"
        buttons.append(
            [
                InlineKeyboardButton("📊 Статус", callback_data="showstatus"),
                InlineKeyboardButton("🌐 Web", url=web_url),
                InlineKeyboardButton("⚙️ Настройки", callback_data="settings"),
            ]
        )

        reply_markup = InlineKeyboardMarkup(buttons)
        if update.callback_query:
            await update.callback_query.message.reply_text(
                "📂 Выбери счёт:", reply_markup=reply_markup
            )
        else:
            await update.message.reply_text(
                "📂 Выбери счёт:", reply_markup=reply_markup
            )


# ==========================
# /status
# ==========================
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.chat_id == chat_id))
        if not u:
            await send_queued_message(chat_id, "Сначала /start")
            return

        snaps = s.scalars(
            select(LastSnapshot)
            .where(LastSnapshot.api_key == u.api_key)
            .order_by(LastSnapshot.last_seen.desc())
        ).all()

        if not snaps:
            await send_queued_message(chat_id, "Нет данных по счётам. Подключите советника.")
            return

        text = ""
        for snap in snaps:
            symbols = s.scalars(
                select(SymbolSnapshot)
                .where(SymbolSnapshot.api_key == u.api_key)
                .where(SymbolSnapshot.account_id == snap.account_id)
            ).all()

            dd_account = (
                (snap.balance - snap.equity) / snap.balance * 100 if snap.balance else 0
            )
            utc_time = snap.last_seen.replace(tzinfo=timezone.utc)
            local_time = utc_time.astimezone(get_localzone())

            acc = s.scalar(
                select(Account)
                .where(Account.api_key == u.api_key)
                .where(Account.account_id == snap.account_id)
            )

            factor = 0.01 if acc and acc.is_cent else 1.0
            if acc and acc.name and acc.name != str(snap.account_id):
                acc_header = f"📊 Статус счёта <b>{acc.name}</b> ({snap.account_id})\n"
            else:
                acc_header = f"📊 Статус счёта <b>{acc.name}</b>\n"

            text += (
                acc_header
                + f"Центовый: {'Да' if acc.is_cent else 'Нет'}\n"
                f"Equity: <b>${snap.equity * factor:.2f}</b>\n"
                f"Balance: <b>${snap.balance * factor:.2f}</b>\n"
                f"Margin Level: {snap.margin_level:.2f}%\n"
                f"Просадка по счёту: {dd_account:.2f}%\n"
                f"Обновлено: {local_time:%Y-%m-%d %H:%M:%S}\n"
            )

            if symbols:
                text += "<pre>"
                for sym in symbols:
                    color = get_drawdown_color(sym.dd_percent)
                    text += (
                        f"{color} "
                        f"{sym.symbol:<6} "
                        f"{sym.price:<6.5f} "
                        f"{sym.dd_percent:+7.2f}% "
                        f"{sym.buy_lots:>5.2f}/{sym.buy_count:<1}▲"
                        f"{sym.sell_lots:>5.2f}/{sym.sell_count:<1}▼\n"
                    )
                text += "</pre>\n"
            else:
                text += "<i>нет открытых позиций</i>\n\n"

        await send_queued_message(chat_id, text, parse_mode="HTML")


# ==========================
# Build bot
# ==========================
def build_bot() -> Application:
    load_dotenv("config.env", override=True)
    token = os.getenv("BOT_TOKEN")
    if not token or ":" not in token:
        raise RuntimeError("BOT_TOKEN не найден или неверный. Проверь .env (BOT_TOKEN=...)")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("web", cmd_status))
    app.add_handler(CommandHandler("accounts", cmd_accounts_menu))
    app.add_handler(CommandHandler("setaccount", lambda u, c: None))

    app.add_handler(CallbackQueryHandler(cmd_accounts_menu, pattern="^acc:"))
    app.add_handler(CallbackQueryHandler(cmd_status, pattern="^showstatus$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: None))

    return app
