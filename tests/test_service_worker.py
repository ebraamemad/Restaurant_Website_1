"""اختبارات سطح الخدمة (service worker) وPWA.

السبب: الـservice worker كان بيتserve من `/static/js/sw.js`، وscope الـworker
بيتحدد بالمسار اللي الملف اتserve منه. يعني الـscope كان `/static/js/` —
ومفيش صفحة HTML في النطاق ده، فالـoffline cache عمليًا ما اشتغلش خالص،
ومحدش كان واخد باله لأن التسجيل كان بيعمل `.catch(() => {})` بصمت.

المرجع: `app/main.py` (مسار `/sw.js`) و`app/static/js/sw.js`.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def sw_client(client: TestClient) -> TestClient:
    return client


class TestServiceWorkerRoute:
    def test_served_from_root(self, sw_client: TestClient) -> None:
        """/sw.js لازم يبقى موجود — دي النقطة اللي بتحدد الـscope."""
        response = sw_client.get("/sw.js")
        assert response.status_code == 200

    def test_correct_javascript_content_type(self, sw_client: TestClient) -> None:
        response = sw_client.get("/sw.js")
        assert "javascript" in response.headers["content-type"]

    def test_service_worker_allowed_header_is_root(self, sw_client: TestClient) -> None:
        response = sw_client.get("/sw.js")
        assert response.headers.get("Service-Worker-Allowed") == "/"

    def test_not_cached_so_updates_are_picked_up(self, sw_client: TestClient) -> None:
        response = sw_client.get("/sw.js")
        assert "no-cache" in response.headers.get("Cache-Control", "")

    def test_body_is_the_worker_source(self, sw_client: TestClient) -> None:
        body = sw_client.get("/sw.js").text
        assert "addEventListener" in body
        assert "caches" in body


class TestServiceWorkerSource:
    """فحص ثابت للملف نفسه — أوضاع أخطاء الـcache سهل نسيانها."""

    @pytest.fixture(scope="class")
    def source(self) -> str:
        from pathlib import Path

        path = Path(__file__).resolve().parent.parent / "app" / "static" / "js" / "sw.js"
        return path.read_text(encoding="utf-8")

    def test_cache_name_was_bumped(self, source: str) -> None:
        """لازم يتغيّر اسم الـcache عشان الـbrowsers تلتقط النسخة الجديدة."""
        assert "restaurant-cache-v3" in source
        assert "restaurant-cache-v2" not in source

    def test_handles_navigation_requests(self, source: str) -> None:
        assert "navigate" in source

    def test_offline_fallback_exists(self, source: str) -> None:
        assert "caches.match" in source

    def test_same_origin_guard(self, source: str) -> None:
        assert "url.origin" in source

    def test_only_get_requests_are_handled(self, source: str) -> None:
        assert "'GET'" in source

    def test_does_not_cache_anything_but_its_own_origin(self, source: str) -> None:
        assert "request.type === 'basic'" in source or "networkResponse.type === 'basic'" in source

    def test_precache_failure_does_not_abort_install(self, source: str) -> None:
        """addAll بتفشل الـinstall كله لو ملف واحد 404."""
        assert "cache.addAll(" not in source

    def test_alpine_is_precached(self, source: str) -> None:
        """Alpine بقى محلي، فلازم يبقى في الـprecache عشان يشتغل اوفلاين."""
        assert "alpine.min.js" in source


class TestTemplateRegistration:
    @pytest.fixture(scope="class")
    def templates(self) -> dict[str, str]:
        from pathlib import Path

        base = Path(__file__).resolve().parent.parent / "app" / "templates"
        return {
            "public": (base / "public" / "base.html").read_text(encoding="utf-8"),
            "admin": (base / "admin" / "base.html").read_text(encoding="utf-8"),
        }

    @pytest.mark.parametrize("name", ["public", "admin"])
    def test_registers_from_root(self, templates: dict[str, str], name: str) -> None:
        assert "register('/sw.js')" in templates[name]
        assert "/static/js/sw.js" not in templates[name]

    def test_public_page_uses_local_alpine(self, templates: dict[str, str]) -> None:
        source = templates["public"]
        assert "/static/js/alpine.min.js" in source
        # مفيش طلب خارجي لمكتبة من متصفح العميل
        assert "unpkg.com" not in source
        assert "cdn.jsdelivr.net" not in source


class TestManifest:
    def test_manifest_is_served(self, sw_client: TestClient) -> None:
        response = sw_client.get("/manifest.json")
        assert response.status_code == 200

    def test_manifest_names_the_icons(self, sw_client: TestClient) -> None:
        response = sw_client.get("/manifest.json")
        assert "icons" in response.json()


class TestVendoredAlpine:
    @pytest.fixture(scope="class")
    def alpine(self) -> str:
        from pathlib import Path

        path = (
            Path(__file__).resolve().parent.parent
            / "app"
            / "static"
            / "js"
            / "alpine.min.js"
        )
        return path.read_text(encoding="utf-8", errors="replace")

    def test_file_is_not_empty(self, alpine: str) -> None:
        assert len(alpine) > 10_000

    def test_declares_its_version(self, alpine: str) -> None:
        # النسخة مضغوطة (minified)، فـmarker شكله version:"x.y.z"
        match = re.search(r"version:\"?([0-9]+\.[0-9]+\.[0-9]+)", alpine)
        assert match, "Alpine version string not found in the vendored bundle"
        assert match.group(1).startswith("3.")

    def test_is_a_bundle_not_a_loader(self, alpine: str) -> None:
        """نسخة CDN بتعمل fetch للملف؛ لازم تكون النسخة الكاملة المضمّنة."""
        assert "cdn.min.js" not in alpine[:2000]

    def test_is_served_by_the_static_mount(self, sw_client: TestClient) -> None:
        response = sw_client.get("/static/js/alpine.min.js")
        assert response.status_code == 200
        assert "javascript" in response.headers["content-type"]
