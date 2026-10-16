"""UI probe (dev-only): renders admin pages headlessly, captures console errors,
network failures, computed styles, and screenshots. Not part of the test suite."""
from __future__ import annotations

import json
import pathlib
import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8017"
OUT = pathlib.Path(r"C:\Users\Abdo El Saied\AppData\Local\Temp\opencode\admin-ui-probe")
OUT.mkdir(parents=True, exist_ok=True)

ADMIN = "testadmin"
PASSWORD = "correct-horse-battery"

PAGES = [
    "/admin",
    "/admin/orders",
    "/admin/customers",
    "/admin/products",
    "/admin/categories",
    "/admin/branches",
    "/admin/delivery-options",
    "/admin/payment-methods",
    "/admin/coupons",
    "/admin/marketing",
    "/admin/marketing/campaigns",
    "/admin/marketing/history",
    "/admin/marketing/import",
    "/admin/marketing/whatsapp",
    "/admin/settings",
]

ASSETS = ["/static/css/app.css", "/static/css/tailwind.css", "/static/js/alpine.min.js",
          "/static/admin-manifest.json", "/static/favicon.png"]


def url(path: str) -> str:
    return BASE + path


def main() -> None:
    report: dict = {"pages": [], "assets": {}, "console_errors": [], "status": "incomplete"}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)

        # ---- asset pass (fresh context, no cache) ----
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        captured: list[str] = []
        page.on("requestfailed", lambda r: captured.append(f"REQFAIL {r.url} :: {r.failure}"))
        page.on("response", lambda r: captured.append(f"{r.status} {r.request.resource_type} {r.url}"))
        page.goto(url("/admin/login"), wait_until="networkidle")
        for line in captured:
            if "/static/" in line or "/uploads/" in line:
                status = line.split(" ", 1)[0]
                asset = line.split(" ", 2)[-1]
                if asset.startswith(("/static/", "/uploads/")):
                    report["assets"][asset] = status
        page.screenshot(path=str(OUT / "01_login.png"))
        body_font = page.evaluate("getComputedStyle(document.body).fontFamily")
        body_bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
        report["login_font"] = body_font
        report["login_body_bg"] = body_bg
        ctx.close()

        # ---- login ----
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.goto(url("/admin/login"), wait_until="networkidle")
        page.fill("#login-username", ADMIN)
        page.fill("#login-password", PASSWORD)
        page.click("button[type=submit]", timeout=10000)
        page.wait_for_load_state("networkidle")
        if "/admin/login" in page.url:
            print("LOGIN FAILED", flush=True)
            sys.exit(2)

        # ---- per-page pass, desktop ----
        for path in PAGES:
            per_page = {"console": [], "failed": [], "status": None}
            page.on("console", lambda m, pp=per_page: pp["console"].append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
            page.on("requestfailed", lambda r, pp=per_page: pp["failed"].append(r.url))
            page.goto(url(path), wait_until="networkidle")
            status = page.evaluate("document.title")
            # geometry probe
            side_rect = page.evaluate("""(() => {
                const el = document.querySelector('.admin-side');
                if (!el) return null;
                const r = el.getBoundingClientRect();
                const cs = getComputedStyle(el);
                return {left: r.left, right: r.right, top: r.top, width: r.width, height: r.height,
                        position: cs.position, transform: cs.transform,
                        background: cs.backgroundImage.slice(0, 40)};
            })()""")
            main_rect = page.evaluate("""(() => {
                const el = document.querySelector('.admin-main');
                if (!el) return null;
                const r = el.getBoundingClientRect();
                const cs = getComputedStyle(el);
                return {left: r.left, right: r.right, width: r.width,
                        marginRight: cs.marginRight, marginLeft: cs.marginLeft, padding: cs.padding};
            })()""")
            overlap = None
            if side_rect and main_rect:
                overlap = main_rect["left"] < side_rect["right"] and main_rect["right"] > side_rect["left"]
            page.screenshot(path=str(OUT / ("page-" + path.strip("/").replace("/", "_") + ".png")))
            per_page["status"] = page.url
            per_page["side"] = side_rect
            per_page["main"] = main_rect
            per_page["overlap"] = overlap
            report["pages"].append({"path": path, **per_page})
            print(f"{path} ok overlap={overlap}", {"side": side_rect, "main": main_rect}, flush=True)

        # ---- mobile pass ----
        ctx.close()
        ctx = browser.new_context(viewport={"width": 390, "height": 844})
        page = ctx.new_page()
        page.goto(url("/admin/login"), wait_until="networkidle")
        page.fill("#login-username", ADMIN)
        page.fill("#login-password", PASSWORD)
        page.click("button[type=submit]", timeout=10000)
        page.wait_for_load_state("networkidle")
        before = page.evaluate("""(() => {
            const el = document.querySelector('.admin-side');
            if (!el) return null;
            const cs = getComputedStyle(el);
            const r = el.getBoundingClientRect();
            return {transform: cs.transform, right: r.right, width: r.width};
        })()""")
        page.click("#sidebar-toggle")
        page.wait_for_timeout(500)
        after = page.evaluate("""(() => {
            const el = document.querySelector('.admin-side');
            const cs = getComputedStyle(el);
            const r = el.getBoundingClientRect();
            const bd = document.getElementById('sidebar-backdrop');
            return {transform: cs.transform, right: r.right, width: r.width,
                    backdropHidden: bd ? bd.hidden : null,
                    htmlOpen: document.documentElement.classList.contains('sidebar-open')};
        })()""")
        page.screenshot(path=str(OUT / "mobile-drawer-open.png"))
        # overflow check
        overflow = page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        report["mobile"] = {"before": before, "after": after, "no_hscroll": overflow}
        print("mobile:", json.dumps({"before": before, "after": after, "no_hscroll": overflow}, ensure_ascii=False), flush=True)

        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        escaped = page.evaluate("document.documentElement.classList.contains('sidebar-open')")

        ctx.close()
        browser.close()
    report["status"] = "complete"
    with open(OUT / "report.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print("REPORT:", OUT / "report.json", flush=True)


if __name__ == "__main__":
    main()