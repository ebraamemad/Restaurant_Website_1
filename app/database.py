'''
ده مسئول عن الاتصال بقاعدة البيانات.

أنا بسميه

بوابة PostgreSQL.

'''



from __future__ import annotations
'''
دي ميزة في بايثون لتأجيل تقييم بعض الـ type hints، وبتسهل كتابة العلاقات بين الكلاسات بدون مشاكل. مش لازم تقلق منها دلوقتي.




'''

from collections.abc import Generator# علشان الدالة get_db() هترجع Generator، وده مجرد Type Hint.

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

# لأازم كل الجداول ترث منه عشان SQLAlchemy يعرفها. وتحوليه الي جداول في قاعدة البيانات.
class Base(DeclarativeBase):
    pass



engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
''' 

SessionLocal
دي أهم فكرة في SQLAlchemy.

كل Request

بيعمل Session جديدة.

بعد ما يخلص

تقفل.

وده يمنع المشاكل بين المستخدمين.
'''


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

from app import models
# إنشاء الجداول إذا لم تكن موجودة
Base.metadata.create_all(bind=engine)