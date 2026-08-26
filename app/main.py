'''
هي بوابة البرنامج الرئيسي للتطبيق، وبتحتوي على كل الروابط الخاصة بالعميل، وروابط تسجيل الدخول، وروابط الأدمن.


التشغيل

هو بدياة التطبيق، وبيتم استدعاءه من ملف

'''


from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routers import admin, admin_auth, api, public
from app.security import CSRF_COOKIE_NAME, create_csrf_token, verify_csrf_token

BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / "uploads"
# لازم مجلد uploads يكون موجود قبل الـ mount، وإلا التطبيق يكسر عند التشغيل على clone جديد أو deploy.
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
#يعني ينشئ تطبيق FastAPI.
app = FastAPI(title=settings.app_name)
#دي عشان يخلي الملفات الثابتة والصور المرفوعة تتفتح من المتصفح.
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")


def _is_admin_path(path: str) -> bool:
    return path == "/admin" or path.startswith("/admin/")


@app.middleware("http")
async def admin_csrf_cookie(request: Request, call_next):
    # Middleware لا يقرأ body إطلاقًا — فقط يصدر كوكي CSRF ويجعل التوكن
    # متاحًا للقوالب عبر request.state. التحقق نفسه يتم في deps.verify_admin_csrf
    # لأن قراءة الـform داخل middleware تستهلك body وتكسر باقى التطبيق.
    path = request.url.path
    state_token = None
    need_cookie = False
    if _is_admin_path(path):
        cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
        if cookie_token and verify_csrf_token(cookie_token):
            state_token = cookie_token
        else:
            state_token = create_csrf_token()
            need_cookie = True
        request.state.csrf_token = state_token

    response = await call_next(request)

    if need_cookie and state_token:
        response.set_cookie(
            CSRF_COOKIE_NAME,
            state_token,
            httponly=False,
            secure=settings.cookie_secure,
            samesite="lax",
        )
    return response


#دي معناها: ضيف روابط العميل، وروابط تسجيل الدخول، وروابط الأدمن للتطبيق.
app.include_router(public.router)
app.include_router(api.router)
app.include_router(admin_auth.router)
app.include_router(admin.router)
