# app/main.py
from fastapi import HTTPException
from pydantic import BaseModel
from datetime import datetime
from app.models import Base, engine
from app.bot import build_bot
from dotenv import load_dotenv
from pathlib import Path
import threading, os, asyncio
from typing import Dict, Optional
from fastapi import FastAPI, Request, Header, Query
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from app.models import SessionLocal, User, LastSnapshot, SymbolSnapshot, Account
from datetime import timezone

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=True)

Base.metadata.create_all(bind=engine)
app = FastAPI(title="FXMonitor Local")

def _run_bot():
    tg_app = build_bot()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(tg_app.run_polling(drop_pending_updates=True))

threading.Thread(target=_run_bot, daemon=True).start()

class SymbolData(BaseModel):
    price: float
    dd_percent: float
    buy_lots: float
    buy_count: int
    sell_lots: float
    sell_count: int

class Ingest(BaseModel):
    account_id: int
    timestamp: datetime
    equity: float
    margin_level: float
    pnl_daily: float
    balance: float | None = None
    symbols: Optional[Dict[str, SymbolData]] = None

@app.post("/ingest")
async def ingest(p: Ingest, request: Request, x_api_key: str = Header(default=None)):
    if not x_api_key:
        raise HTTPException(401, "Missing X-API-KEY")

    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.api_key == x_api_key))
        if not u:
            raise HTTPException(403, "Invalid key")

        # Проверяем, есть ли аккаунт в таблице Account
        acc = s.scalar(
            select(Account)
            .where(Account.api_key == x_api_key)
            .where(Account.account_id == p.account_id)
        )
        if not acc:
            # создаём новый аккаунт, имя = его ID
            acc = Account(
                api_key=x_api_key,
                account_id=p.account_id,
                name=str(p.account_id),
                is_cent=False
            )
            s.add(acc)
            s.commit()

            # уведомляем пользователя и показываем меню со счетами
            from telegram import Bot
            from app.bot import cmd_accounts_menu
            from telegram.ext import ContextTypes

            BOT_TOKEN = os.getenv("BOT_TOKEN")
            if BOT_TOKEN and u and u.chat_id:
                bot = Bot(token=BOT_TOKEN)

                import asyncio

                async def notify():
                    try:
                        await bot.send_message(
                            chat_id=u.chat_id,
                            text=f"➕ Добавлен новый счёт {p.account_id}"
                        )
                        # сразу вывести меню
                        fake_update = type(
                            "obj",
                            (object,),
                            {"effective_chat": type("obj2", (object,), {"id": u.chat_id})()}
                        )()
                        await cmd_accounts_menu(fake_update, ContextTypes.DEFAULT_TYPE())
                    except Exception as e:
                        print(f"Ошибка отправки уведомления в Telegram: {e}")

                asyncio.create_task(notify())

        # обновляем/создаём LastSnapshot
        snap = s.scalar(
            select(LastSnapshot)
            .where(LastSnapshot.api_key == x_api_key)
            .where(LastSnapshot.account_id == p.account_id)
        )
        if not snap:
            snap = LastSnapshot(api_key=x_api_key, account_id=p.account_id)
            s.add(snap)

        snap.account_id = p.account_id
        snap.equity = p.equity
        snap.margin_level = p.margin_level
        snap.pnl_daily = p.pnl_daily
        snap.balance = p.balance
        snap.ts = p.timestamp
        snap.last_seen = datetime.utcnow()
        s.commit()

        # 🔹 всегда очищаем старые символы по счёту
        s.query(SymbolSnapshot).filter(SymbolSnapshot.api_key == x_api_key,SymbolSnapshot.account_id == p.account_id).delete()
        s.commit()

        # 🔹 добавляем новые символы, если есть
        if p.symbols:
            for sym, data in p.symbols.items():
                rec = SymbolSnapshot(
                    api_key=x_api_key,
                    account_id=p.account_id,
                    symbol=sym,
                    price=data.price,
                    dd_percent=data.dd_percent,
                    buy_lots=data.buy_lots,
                    buy_count=data.buy_count,
                    sell_lots=data.sell_lots,
                    sell_count=data.sell_count,
                )
                s.add(rec)
            s.commit()

    return {"status": "ok"}

@app.get("/api/status")
async def api_status(x_api_key: str = Header(default=None)):
    if not x_api_key:
        raise HTTPException(401, "Missing X-API-KEY")
    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.api_key == x_api_key))
        if not u:
            return JSONResponse({"error": "invalid api_key"}, status_code=403)

        # Берём только аккаунты, по которым есть снапшоты
        snaps = s.scalars(
            select(LastSnapshot)
            .where(LastSnapshot.api_key == x_api_key)
            .order_by(LastSnapshot.last_seen.desc())
        ).all()

        result = []
        for snap in snaps:
            acc = s.scalar(
                select(Account)
                .where(Account.api_key == x_api_key)
                .where(Account.account_id == snap.account_id)
            )

            factor = 0.01 if (acc and acc.is_cent) else 1.0
            acc_name = acc.name if (acc and acc.name) else str(snap.account_id)

            dd_account = ((snap.balance - snap.equity) / snap.balance * 100) if snap.balance else 0

            symbols = s.scalars(
                select(SymbolSnapshot)
                .where(SymbolSnapshot.api_key == x_api_key)
                .where(SymbolSnapshot.account_id == snap.account_id)
            ).all()

            result.append({
                "account_id": snap.account_id,
                "account_name": acc_name,
                "equity": snap.equity * factor,
                "balance": snap.balance * factor,
                "margin_level": snap.margin_level,
                "pnl_daily": snap.pnl_daily * factor,
                "drawdown": dd_account,
                "last_seen": snap.last_seen.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if snap.last_seen else None,
                "symbols": [
                    {
                        "symbol": sym.symbol,
                        "price": sym.price,
                        "dd_percent": sym.dd_percent,
                        "buy_lots": sym.buy_lots,
                        "buy_count": sym.buy_count,
                        "sell_lots": sym.sell_lots,
                        "sell_count": sym.sell_count,
                    }
                    for sym in symbols
                ]
            })

        # сортируем: сначала с позициями, потом пустые, внутри по имени
        result.sort(key=lambda x: (0 if len(x["symbols"]) > 0 else 1, x["account_name"].lower()))
        return JSONResponse(result)


class AccountData(BaseModel):
    account_id: str
    name: str
    is_cent: bool = False

@app.post("/api/add_account")
async def add_account(acc: AccountData, x_api_key: str = Header(default=None)):
    if not x_api_key:
        raise HTTPException(401, "Missing X-API-KEY")
    with SessionLocal() as s:
        u = s.scalar(select(User).where(User.api_key == x_api_key))
        if not u:
            raise HTTPException(403, "Invalid key")

        # проверка дубликата
        exists = s.scalar(
            select(Account)
            .where(Account.api_key == x_api_key)
            .where(Account.account_id == acc.account_id)
        )
        if exists:
            raise HTTPException(400, "Account already exists")

        new_acc = Account(
            api_key=x_api_key,
            account_id=acc.account_id,
            name=acc.name,
            is_cent=acc.is_cent,
        )
        s.add(new_acc)
        s.commit()
        return {"status": "ok", "account_id": new_acc.account_id}

@app.get("/api/accounts")
async def list_accounts(x_api_key: str = Header(default=None)):
    if not x_api_key:
        raise HTTPException(401, "Missing X-API-KEY")
    with SessionLocal() as s:
        accounts = s.scalars(select(Account).where(Account.api_key == x_api_key)).all()
        return [
            {"account_id": a.account_id, "name": a.name, "is_cent": a.is_cent}
            for a in accounts
        ]

class AccountUpdate(BaseModel):
    name: str | None = None
    is_cent: bool | None = None

@app.post("/api/update_account")
async def update_account(acc: AccountUpdate, account_id: str, x_api_key: str = Header(default=None)):
    if not x_api_key:
        raise HTTPException(401, "Missing X-API-KEY")
    with SessionLocal() as s:
        account = s.scalar(
            select(Account)
            .where(Account.api_key == x_api_key)
            .where(Account.account_id == account_id)
        )
        if not account:
            raise HTTPException(404, "Account not found")

        if acc.name is not None:
            account.name = acc.name
        if acc.is_cent is not None:
            account.is_cent = acc.is_cent
        s.commit()
        return {"status": "updated", "account_id": account.account_id}

@app.get("/web")
async def web(api_key: str = Query(...)):
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>MTMonitor Web</title>
        <style>
            body {{ font-family: Arial, sans-serif; background:#f5f7fa; margin:0; padding:0; }}
            .header {{ background:#003366; color:#fff; padding:12px; font-size:20px; font-weight:bold; }}
            .account-card {{
                background:#fff; margin:15px; padding:20px; border-radius:8px;
                box-shadow:0 2px 5px rgba(0,0,0,0.2);
            }}
            .row {{ display:flex; flex-wrap:wrap; align-items:center; margin-bottom:15px; gap:20px; }}
            .big-red {{ color:#c00; font-size:22px; font-weight:bold; }}
            .big-green {{ color:#060; font-size:22px; font-weight:bold; }}
            .tile-row {{ display:flex; gap:15px; margin-bottom:15px; }}
            .tile {{
                flex:1; background:#1976d2; color:#fff; padding:15px; text-align:center;
                border-radius:6px; font-size:20px; font-weight:bold;
            }}
            .symbols-grid {{
                display:grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
                gap:10px;
            }}
            .symbol {{
                border-radius:6px; text-align:center; font-weight:bold; padding:6px;
                color:#000;
            }}
            .symbol.green  {{ background-color:#4CAF50; }}
            .symbol.yellow {{ background-color:#FFC107; }}
            .symbol.orange {{ background-color:#FF9800; }}
            .symbol.red    {{ background-color:#F44336; }}
            .symbol-name {{ font-size:14px; margin-bottom:4px; }}
            .price-box {{
                background:#111; color:#fff;
                padding:4px; font-size:16px; font-weight:bold;
                margin-bottom:4px; border-radius:4px;
            }}
            .dd {{ font-size:14px; margin-bottom:4px; }}
            .stat-small {{ font-size:12px; font-weight:normal; }}
            .footer {{ margin-top:15px; font-size:12px; color:#555; text-align:center; }}
        </style>
    </head>
    <body>
        <div class="header">📊 MTMonitor Web</div>
        <div id="content">Loading...</div>
        <script>
            const apiKey = "{api_key}";
            async function loadData() {{
                let res = await fetch('/api/status', {{ headers: {{"X-API-KEY": apiKey}} }});
                if (!res.ok) {{
                    document.getElementById("content").innerHTML = "Auth error. Проверь API key.";
                    return;
                }}
                let data = await res.json();

                // 🔹 сортировка по имени
                data.sort((a, b) => a.account_name.localeCompare(b.account_name));

                data.sort((a, b) => {{
                    if (a.symbols.length > 0 && b.symbols.length === 0) return -1;
                    if (a.symbols.length === 0 && b.symbols.length > 0) return 1;
                    return a.account_name.localeCompare(b.account_name);
                }});

                let html = "";
                for (let acc of data) {{
                    html += `<div class="account-card">
                        <div class="row">
                            <div><b>Account:</b> ${{acc.account_name}}</div>
                            <div class="${{acc.drawdown<0?'big-red':'big-green'}}">Drawdown: ${{acc.drawdown.toFixed(2)}}%</div>
                            <div class="big-green">Margin: ${{acc.margin_level?.toFixed(2) ?? "-"}}%</div>
                        </div>
                        <div class="tile-row">
                            <div class="tile">Balance<br>${{acc.balance?.toFixed(2) ?? "-"}}</div>
                            <div class="tile">Equity<br>${{acc.equity?.toFixed(2) ?? "-"}}</div>
                        </div>
                        <div class="row">
                            <div>Day: <b>${{acc.pnl_daily?.toFixed(2) ?? "-"}}</b></div>
                            <div>Week: <b>—</b></div>
                            <div>Month: <b>—</b></div>
                        </div>`;

                    if (acc.symbols && acc.symbols.length > 0) {{
                        html += `<div class="symbols-grid">`;
                        for (let s of acc.symbols) {{
                            let cls = "symbol green";
                            if (s.dd_percent <= -25) cls = "symbol red";
                            else if (s.dd_percent <= -10) cls = "symbol orange";
                            else if (s.dd_percent < -1) cls = "symbol yellow";
                            else cls = "symbol green";

                            html += `<div class="${{cls}}">
                                <div class="symbol-name">${{s.symbol}}</div>
                                <div class="price-box">${{s.price.toFixed(5)}}</div>
                                <div class="dd">${{s.dd_percent.toFixed(2)}}%</div>
                                <div class="stat-small">▲ ${{s.buy_lots.toFixed(2)}} (${{s.buy_count}})</div>
                                <div class="stat-small">▼ ${{s.sell_lots.toFixed(2)}} (${{s.sell_count}})</div>
                            </div>`;
                        }}
                        html += `</div>`;
                    }} else {{
                        html += `<div>нет открытых позиций</div>`;
                    }}

                    html += `<div class="footer">Updated: ${{acc.last_seen ? new Date(acc.last_seen).toLocaleString() : "-"}}</div>
                    </div>`;
                }}
                document.getElementById("content").innerHTML = html;
            }}
            setInterval(loadData, 5000);
            loadData();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

