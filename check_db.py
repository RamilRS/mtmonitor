import sys
from app.models import Base, engine, SessionLocal, User, LastSnapshot, SymbolSnapshot

clear_mode = "--clear" in sys.argv

with SessionLocal() as s:
    if clear_mode:
        # Дропаем таблицу и пересоздаём по модели
        SymbolSnapshot.__table__.drop(bind=engine, checkfirst=True)
        SymbolSnapshot.__table__.create(bind=engine, checkfirst=True)
        print("⚠️ Таблица symbol_snapshots пересоздана по модели (с UniqueConstraint).")
    else:
        print("=== Users ===")
        for u in s.query(User).all():
            print(u.id, u.chat_id, u.api_key)

        print("\n=== Last Snapshots ===")
        for snap in s.query(LastSnapshot).all():
            print(
                f"account_id={snap.account_id}, "
                f"equity={snap.equity}, "
                f"balance={snap.balance}, "
                f"pnl_daily={snap.pnl_daily}, "
                f"last_seen={snap.last_seen}"
            )

        print("\n=== Symbol Snapshots ===")
        for sym in s.query(SymbolSnapshot).all():
            print(
                f"account_id={sym.account_id}, "
                f"symbol={sym.symbol}, "
                f"price={sym.price}, "
                f"dd%={sym.dd_percent}, "
                f"buy={sym.buy_lots}/{sym.buy_count}, "
                f"sell={sym.sell_lots}/{sym.sell_count}, "
                f"ts={sym.ts}"
            )
