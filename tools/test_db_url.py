"""أداة: تطبع رابط داتابيز الاختبار.

بتستخدمها من PowerShell كده عشان تبعت DATABASE_URL لـalembic من غير ما
تكتب الـpassword في أي ملف أو history:

    $env:DATABASE_URL = python -m tools.test_db_url
    python -m alembic upgrade head
"""

from __future__ import annotations

from tools.ensure_test_db import test_database_url

if __name__ == "__main__":
    print(test_database_url())
