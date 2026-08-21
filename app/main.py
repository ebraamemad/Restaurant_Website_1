'''
هي بوابة البرنامج الرئيسي للتطبيق، وبتحتوي على كل الروابط الخاصة بالعميل، وروابط تسجيل الدخول، وروابط الأدمن.


التشغيل 

هو بدياة التطبيق، وبيتم استدعاءه من ملف

'''


from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routers import admin, admin_auth, public

BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / "uploads"
# لازم مجلد uploads يكون موجود قبل الـ mount، وإلا التطبيق يكسر عند التشغيل على clone جديد أو deploy.
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
#يعني ينشئ تطبيق FastAPI.
app = FastAPI(title=settings.app_name)
#دي عشان يخلي الملفات الثابتة والصور المرفوعة تتفتح من المتصفح.
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")


#دي معناها: ضيف روابط العميل، وروابط تسجيل الدخول، وروابط الأدمن للتطبيق.
app.include_router(public.router)
app.include_router(admin_auth.router)
app.include_router(admin.router)
