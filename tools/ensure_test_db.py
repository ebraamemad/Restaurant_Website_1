"""أداة مساعدة: تعمل داتابيز الاختبار لو مش موجودة.

بتقرأ DATABASE_URL من .env، وبتعمل نسخة منه بنفس الـhost/user/password بس
باسم داتابيز مختلف. مفيش أي password بيتطبع.

    python -m tools.ensure_test_db
"""

from __future__ import annotations

import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import settings

TEST_DB_NAME = "restaurant_store_test"


def test_database_url() -> str:
    """رابط داتابيز الاختبار (بيستخدم نفس بيانات .env مع اسم داتابيز تاني)."""
    return make_url(settings.database_url).set(database=TEST_DB_NAME).render_as_string(
        hide_password=False
    )


def main() -> int:
    base = make_url(settings.database_url)
    print(f"server  : {base.host}:{base.port}")
    print(f"test db : {TEST_DB_NAME}")

    # بنستخدم SQLAlchemy نفسه هنا عشان هو اللي فاهم الـdialect
    # (postgresql+psycopg)، مش psycopg مباشرة.
    admin_engine = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("select 1 from pg_database where datname = :name"), {"name": TEST_DB_NAME}
            ).scalar()
            if exists:
                print("result  : already exists")
            else:
                # اسم الداتابيز ثابت ومش جاي من input، فـidentifier literal آمن.
                conn.execute(text(f'create database "{TEST_DB_NAME}"'))
                print("result  : created")
    except Exception:
        # بنطبع الرسالة من غير الـURL عشان ما نكشفش الـpassword.
        print("result  : FAILED — مفيش صلاحية أو الـserver مش شغال", file=sys.stderr)
        return 1
    finally:
        admin_engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
