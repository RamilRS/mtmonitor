import os
import asyncio
import secrets
import time
import logging
from collections import deque
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
from sqlalchemy import select, delete, func
from app.models import SessionLocal, User, Account, LastSnapshot, SymbolSnapshot
from tzlocal import get_localzone
from app.logger import logger

ADMIN = "Ramil1234567"

# ==========================
# Очередь сообщений с контролем лимитов
# ==========================
message_queue = asyncio.Queue()
sent_timestamps = deque()         # лимит 30/сек
user_timestamps = {}              # лимит 20/мин на пользователя


async def message_worker(bot: Bot):
    """Фоновая задача для отправки сообщений с учётом лимитов Telegram."""
    global sent_timestamps, user_timestamps
    while True:
        chat_id, text, kwargs = await message_queue.get()
        try:
            now = time.time()

            # --- лимит 30/сек ---
            while sent_timestamps and now - sent_timestamps[0] > 1:
                sent_timestamps.popleft()
            if len(sent_timestamps) >= 29:
                wait_time = 1 - (now - sent_timestamps[0])
                if wait_time > 0:
                    await asyncio.sleep(wait_time)

            # --- лимит 20/мин на пользователя ---
            if chat_id not in user_timestamps:
                user_timestamps[chat_id] = deque()
            while user_timestamps[chat_id] and now - user_timestamps[chat_id][0] > 60:
                user_timestamps[chat_id].popleft()
            if len(user_timestamps[chat_id]) >= 19:
                wait_time = 60 - (now - user_timestamps[chat_id][0])
                if wait_time > 0:
                    logger.info(f"[RateLimit] Задержка {wait_time:.2f}с для chat_id={chat_id}")
                    await asyncio.sleep(wait_time)

            # --- отправка ---
            await bot.send_message(chat_id=chat_id, text=text, **kwargs)

            sent_timestamps.append(time.time())
            user_timestamps[chat_id].append(time.time())

        except Exception as e:
            logger.info(f"[MessageWorker] Ошибка отправки: {e}")
        finally:
            message_queue.task_done()


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

ADMIN = "Ramil1234567"

async def cmd_admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.effective_user.username if update.effective_user else None
    if username != ADMIN:
        return await send_queued_message(str(update.effective_chat.id), "❌ Нет доступа")

    with SessionLocal() as s:
        from app.main import subscribers
        active_pages = sum(len(v) for v in subscribers.values())
        users_count = s.query(User).count()
        accounts = s.query(Account).all()

        # учитываем центовые счета
        total_equity = 0
        total_balance = 0
        for acc in accounts:
            snap = s.scalar(
                select(LastSnapshot)
                .where(LastSnapshot.api_key == acc.api_key)
                .where(LastSnapshot.account_id == acc.account_id)
                .order_by(LastSnapshot.last_seen.desc())
            )
            if not snap:
                continue

            factor = 0.01 if acc.is_cent else 1.0
            if snap.equity:
                total_equity += snap.equity * factor
            if snap.balance:
                total_balance += snap.balance * factor

    text = (
        f"📊 <b>Админ-статистика</b>\n\n"
        f"Активных веб-страниц: {active_pages}\n"
        f"Пользователей: {users_count}\n"
        f"Счетов: {len(accounts)}\n"
        f"Сумма Equity: ${total_equity:,.2f}\n"
        f"Сумма Balance: ${total_balance:,.2f}"
    )
    await send_queued_message(str(update.effective_chat.id), text, parse_mode="HTML")
    await cmd_accounts_menu(update, context)

async def cmd_admin_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.effective_user.username if update.effective_user else None
    if username != ADMIN:
        return await send_queued_message(str(update.effective_chat.id), "❌ Нет доступа")

    with SessionLocal() as s:
        accounts = s.query(Account).all()

        if not accounts:
            return await send_queued_message(str(update.effective_chat.id), "Нет аккаунтов")

        text = "📋 <b>Все аккаунты</b>\n<pre>"
        header = (
            f"{'User':<12}"
            f"{'Счёт':<10}"
            f"{'Balance':>12}"
            f"{'Equity':>12}"
            f"{'DD%':>8}"
            f"{'MT обновлено':>21}"
            f"{'WEB просмотр':>21}"
        )
        text += header + "\n" + "-" * len(header) + "\n"

        for acc in accounts:
            snap = s.scalar(
                select(LastSnapshot)
                .where(LastSnapshot.api_key == acc.api_key)
                .where(LastSnapshot.account_id == acc.account_id)
                .order_by(LastSnapshot.last_seen.desc())
            )
            last_seen = snap.last_seen.strftime("%Y-%m-%d %H:%M:%S") if snap and snap.last_seen else "—"

            owner = s.scalar(select(User).where(User.api_key == acc.api_key))
            last_web_seen = owner.last_web_seen.strftime("%Y-%m-%d %H:%M:%S") if owner and owner.last_web_seen else "—"

            factor = 0.01 if acc.is_cent else 1.0
            balance = snap.balance * factor if snap and snap.balance else 0
            equity = snap.equity * factor if snap and snap.equity else 0
            dd_account = ((snap.balance - snap.equity) / snap.balance * 100) if snap and snap.balance else 0

            username = owner.short_id if owner else "—"
            acc_name = (acc.name[:10]) if acc and acc.name else "—"

            text += (
                f"{username:<12}"
                f"{acc_name:<10}"
                f"{balance:>12.2f}"
                f"{equity:>12.2f}"
                f"{dd_account:>7.2f}%"
                f"{last_seen:>21}"
                f"{last_web_seen:>21}\n"
            )

        text += "</pre>"

    await send_queued_message(str(update.effective_chat.id), text, parse_mode="HTML")
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

        if update.effective_user and update.effective_user.username == ADMIN:
            buttons.append(
                [
                    InlineKeyboardButton("📊 Админ-статистика", callback_data="admin_stats"),
                    InlineKeyboardButton("📋 Аккаунты", callback_data="admin_accounts"),
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
# Колбэки для кнопок
# ==========================
async def callback_accounts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    account_id = query.data.split(":")[1]

    with SessionLocal() as s:
        acc = s.scalar(select(Account).where(Account.account_id == account_id))
        text = (
            f"Счёт {acc.account_id}: {acc.name}\n"
            f"Центовый: {'Да' if acc.is_cent else 'Нет'}"
        )

        buttons = [
            [InlineKeyboardButton("🔤 Переименовать", callback_data=f"rename:{account_id}")],
            [InlineKeyboardButton(
                "💰 Сделать обычным" if acc.is_cent else "💵 Сделать центовым",
                callback_data=f"togglecent:{account_id}"
            )],
            [InlineKeyboardButton("🗑 Удалить счёт", callback_data=f"delete:{account_id}")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="backtomain")],
        ]

        await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def callback_actions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    logger.info(f"[ACTION] {data}")

    if data == "showstatus":
        await cmd_status(update, context)
        await cmd_accounts_menu(update, context)

    # 🔹 новые условия для админа
    elif data == "admin_stats":
        await cmd_admin_stats(update, context)

    elif data == "admin_accounts":
        await cmd_admin_accounts(update, context)

    elif data == "settings":
        settings_buttons = [
            [InlineKeyboardButton("➕ Добавить счёт", callback_data="addaccount")],
            [InlineKeyboardButton("💳 Оплата", callback_data="payment")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="backtomain")],
        ]
        await query.message.reply_text(
            "⚙️ Настройки:", reply_markup=InlineKeyboardMarkup(settings_buttons)
        )

    elif data == "backtomain":
        await cmd_accounts_menu(update, context)

    elif data == "payment":
        await query.message.reply_text("💳 Для вас 3 месяца бесплатного пользования 🚀")
        await cmd_accounts_menu(update, context)

    elif data.startswith("rename:"):
        account_id = data.split(":")[1]
        context.user_data["rename_account"] = account_id
        await query.message.reply_text("Введите новое имя для счёта:")

    elif data.startswith("togglecent:"):
        account_id = data.split(":")[1]
        with SessionLocal() as s:
            acc = s.scalar(select(Account).where(Account.account_id == account_id))
            if acc:
                acc.is_cent = not acc.is_cent
                s.commit()
                status = "Центовый" if acc.is_cent else "Обычный"
                await query.message.reply_text(f"✅ Счёт {acc.name} теперь {status}")
            else:
                await query.message.reply_text("❌ Счёт не найден")
        await cmd_accounts_menu(update, context)

    elif data.startswith("delete:"):
        account_id_str = data.split(":")[1]
        logger.info(f"[DELETE] Запрос на удаление счёта {account_id_str}")

        chat_id = str(update.effective_chat.id)
        with SessionLocal() as s:
            u = s.scalar(select(User).where(User.chat_id == chat_id))
            if not u:
                logger.warning(f"[DELETE] Пользователь {chat_id} не найден в users")
                await update.callback_query.message.reply_text("❌ Сначала /start")
                return

            logger.info(f"[DELETE] Пользователь {chat_id} найден, api_key={u.api_key}")

            acc = s.scalar(
                select(Account)
                .where(Account.api_key == u.api_key)
                .where(Account.account_id == account_id_str)   # сравнение как строка
            )
            if not acc:
                logger.warning(f"[DELETE] Счёт {account_id_str} не найден у api_key={u.api_key}")
                await update.callback_query.message.reply_text("❌ Счёт не найден")
                return

            logger.info(f"[DELETE] Найден счёт {acc.account_id}, начинаем удаление снапшотов")

            # удаляем связанные снапшоты
            deleted_symbols = s.execute(
                delete(SymbolSnapshot)
                .where(SymbolSnapshot.api_key == u.api_key)
                .where(SymbolSnapshot.account_id == account_id_str)
            ).rowcount
            logger.info(f"[DELETE] Удалено {deleted_symbols} строк из SymbolSnapshot")

            deleted_snaps = s.execute(
                delete(LastSnapshot)
                .where(LastSnapshot.api_key == u.api_key)
                .where(LastSnapshot.account_id == account_id_str)
            ).rowcount
            logger.info(f"[DELETE] Удалено {deleted_snaps} строк из LastSnapshot")

            s.delete(acc)
            logger.info(f"[DELETE] Удалена запись из accounts: {acc.account_id}")
            s.commit()

        await update.callback_query.message.reply_text(f"🗑 Счёт {account_id_str} удалён")
        logger.info(f"[DELETE] ✅ Счёт {account_id_str} успешно удалён")
        await cmd_accounts_menu(update, context)


async def handle_rename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if "rename_account" not in context.user_data:
        return

    account_id = context.user_data.pop("rename_account")
    new_name = update.message.text.strip()

    with SessionLocal() as s:
        acc = s.scalar(select(Account).where(Account.account_id == account_id))
        if acc:
            acc.name = new_name
            s.commit()
            await send_queued_message(chat_id, f"✅ Счёт {account_id} переименован в «{new_name}»")
        else:
            await send_queued_message(chat_id, "❌ Счёт не найден")


async def callback_addaccount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = str(update.effective_chat.id)

    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.chat_id == chat_id))
        if not u:
            await query.message.reply_text("Сначала /start")
            return

        instruction = f"""
<b>➕ Как добавить новый счёт:</b>

1️⃣ Скачайте эксперта <b>MTMonitor</b> (ниже кнопка «📥 Скачать эксперта»).

2️⃣ Скопируйте файл <code>mtmonitor.mq4</code> в папку <b>MQL4/Experts</b> вашего терминала MetaTrader 4.
▫ В MetaTrader: <i>Файл → Открыть каталог данных → MQL4 → Experts</i>

3️⃣ Перезапустите MetaTrader или обновите список экспертов.

4️⃣ Перетащите советника <b>MTMonitor</b> на график любого <u>неиспользуемого инструмента</u>.

5️⃣ В настройках советника:
▫ Введите ваш <b>API-ключ</b>:
<pre>{u.api_key}</pre>
▫ Обязательно включите опцию <b>«Разрешить импорт функций DLL»</b>.

6️⃣ Нажмите <b>ОК</b> — эксперт начнёт отправлять данные на сервер.

7️⃣ После первой отправки счёт появится в списке в боте и на веб-панели.
"""

        buttons = [
            [InlineKeyboardButton("📥 Скачать эксперта", callback_data="sendexpert")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="backtomain")]
        ]

        await query.message.reply_html(instruction, reply_markup=InlineKeyboardMarkup(buttons))


async def callback_sendexpert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    file_path = os.path.join(os.path.dirname(__file__), "mtmonitor.mq4")

    try:
        await query.message.reply_document(document=open(file_path, "rb"), filename="mtmonitor.mq4")
    except Exception as e:
        await query.message.reply_text(f"Ошибка при отправке файла: {e}")


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
    # 🔹 админские команды
    app.add_handler(CommandHandler("admin_stats", cmd_admin_stats))
    app.add_handler(CommandHandler("admin_accounts", cmd_admin_accounts))

    app.add_handler(CallbackQueryHandler(callback_accounts, pattern="^acc:"))
    app.add_handler(
        CallbackQueryHandler(
            callback_actions,
            pattern="^(rename:.*|togglecent:.*|delete:.*|showstatus|settings|backtomain|payment|admin_stats|admin_accounts)$",
        )
    )

    app.add_handler(CallbackQueryHandler(callback_addaccount, pattern="^addaccount$"))
    app.add_handler(CallbackQueryHandler(callback_sendexpert, pattern="^sendexpert$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_rename))

    return app
