# scripts/backfill_shortids.py
import secrets
from app.models import SessionLocal, User
from sqlalchemy import select

with SessionLocal() as s:
    users = s.scalars(select(User)).all()
    changed = 0
    for u in users:
        if not getattr(u, "short_id", None):
            u.short_id = secrets.token_urlsafe(8)
            changed += 1
    s.commit()
    print(f"Backfilled {changed} users")
