"""
PWA basic tests — manifest, service worker, install prompt, app icon.

التشغيل: .venv\\Scripts\\python.exe test_pwa.py
"""
from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.database import engine, get_db
from app.main import app
from app.models import RestaurantSettings

connection = engine.connect()
transaction = connection.begin()
TestingSessionLocal = sessionmaker(
    bind=connection, join_transaction_mode="create_savepoint", autoflush=False, expire_on_commit=False,
)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)

PASS = 0
FAIL = 0


def check(name: str, condition: bool, extra: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def ensure_settings(db):
    s = db.scalar(select(RestaurantSettings))
    if s is None:
        s = RestaurantSettings()
        db.add(s)
        db.flush()
    return s


try:
    print("\n== Dynamic Manifest (/manifest.json) ==")

    r = client.get("/manifest.json")
    check("GET /manifest.json returns 200", r.status_code == 200, r.text[:200])
    check("Content-Type is application/json", r.headers.get("content-type", "").startswith("application/json"))

    try:
        m = r.json()
        check("Manifest is valid JSON", True)
    except Exception:
        m = {}
        check("Manifest is valid JSON", False)

    check("'name' field present", "name" in m)
    check("'short_name' field present", "short_name" in m)
    check("'start_url' field present", "start_url" in m)
    check("'display' field present", "display" in m)
    check("'theme_color' field present", "theme_color" in m)
    check("'icons' field present", "icons" in m)
    check("start_url is '/'", m.get("start_url") == "/")
    check("display is 'standalone'", m.get("display") == "standalone")
    icons = m.get("icons", [])
    check("At least one icon defined", len(icons) >= 1, str(len(icons)))
    check("All icons have src", all(i.get("src") for i in icons), str(icons))

    print("\n== Default App Icon in manifest (no custom icon) ==")

    db = TestingSessionLocal()
    settings = ensure_settings(db)
    settings.app_icon_url = None
    db.add(settings)
    db.commit()
    db.close()

    r = client.get("/manifest.json")
    m = r.json()
    default_icons = [i["src"] for i in m.get("icons", [])]
    check("Default manifest uses static icons",
          any("icon-192.png" in src or "icon-512.png" in src for src in default_icons), str(default_icons))

    print("\n== Static Manifest reference (still served) ==")

    r = client.get("/static/manifest.json")
    check("GET /static/manifest.json returns 200", r.status_code == 200)

    print("\n== Static Icons ==")

    r = client.get("/static/icons/icon-192.png")
    check("icon-192.png accessible", r.status_code == 200)
    check("icon-192.png is image/png", r.headers.get("content-type", "").startswith("image/png"))

    r = client.get("/static/icons/icon-512.png")
    check("icon-512.png accessible", r.status_code == 200)
    check("icon-512.png is image/png", r.headers.get("content-type", "").startswith("image/png"))

    print("\n== Service Worker ==")

    r = client.get("/static/js/sw.js")
    check("Service Worker file accessible (200)", r.status_code == 200)
    sw_content = r.text
    check("Service Worker has install event", "addEventListener('install'" in sw_content or 'addEventListener("install"' in sw_content)
    check("Service Worker has activate event", "addEventListener('activate'" in sw_content or 'addEventListener("activate"' in sw_content)
    check("Service Worker has fetch event", "addEventListener('fetch'" in sw_content or 'addEventListener("fetch"' in sw_content)
    check("Service Worker caches static assets", "cache.addAll" in sw_content or "caches.open" in sw_content)

    print("\n== Install Prompt & standalone in Homepage ==")

    r = client.get("/")
    check("Homepage loads (200)", r.status_code == 200, r.text[:200])
    html = r.text
    check("Manifest link points to dynamic route", 'href="/manifest.json"' in html)
    check("PWA meta tags present", "apple-mobile-web-app-capable" in html)
    check("Service Worker registration present", "serviceWorker.register" in html)
    check("beforeinstallprompt handler present", "beforeinstallprompt" in html)
    check("Install prompt banner element present", 'id="pwa-install-banner"' in html)
    check("installPWA function present", "installPWA" in html)
    check("Install button text present", "ثبّت التطبيق" in html)
    check("standalone check present", "display-mode" in html, "standalone guard missing")
    check("No offline indicator (minimal PWA)", "offline-status" not in html, "offline indicator should be removed")

    print("\n== Service Worker does not cache manifest ==")
    sw_resp = client.get("/static/js/sw.js")
    sw_content = sw_resp.text
    check("Manifest not in SW cache list", "/static/manifest.json" not in sw_content, "manifest should not be cached")
    check("No manifest destination in fetch handler", "request.destination === 'manifest'" not in sw_content, "manifest should not be cached at runtime")

    print("\n== Custom App Icon flows into manifest ==")

    db = TestingSessionLocal()
    settings = ensure_settings(db)
    custom = "/uploads/icons/fake-custom.png"
    settings.app_icon_url = custom
    db.add(settings)
    db.commit()
    db.close()

    r = client.get("/manifest.json")
    m = r.json()
    custom_icons = [i["src"] for i in m.get("icons", [])]
    check("Manifest uses custom app icon", all(src == custom for src in custom_icons), str(custom_icons))

    # تنظيف — الرجوع للـNone عشان ماتستقر على حالتها
    db = TestingSessionLocal()
    settings = ensure_settings(db)
    settings.app_icon_url = None
    db.add(settings)
    db.commit()
    db.close()

    print(f"\n== Summary ==")
    print(f"PASSED: {PASS}")
    print(f"FAILED: {FAIL}")

    if FAIL > 0:
        sys.exit(1)

finally:
    transaction.rollback()
    connection.close()
    app.dependency_overrides.clear()
