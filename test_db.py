from sqlalchemy import text
from app.database import SessionLocal

db = SessionLocal()

try:
    print("Database connection successful")

    tables = db.execute(
        text("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name
        """)
    ).fetchall()

    print("Tables:", [table[0] for table in tables])

finally:
    db.close()