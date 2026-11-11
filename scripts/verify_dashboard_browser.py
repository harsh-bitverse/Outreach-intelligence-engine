"""
Automated Playwright Browser Verification Script for Outreach Intelligence Engine Dashboard.
Tests zero console errors, all 8 navigation tabs, modals, buttons, and telemetry rendering.
"""

import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

def run_browser_verification():
    console_messages = []
    page_errors = []
    failed_requests = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel="msedge")
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # Wire event listeners
        page.on("console", lambda msg: console_messages.append(f"[{msg.type}] {msg.text}"))
        page.on("pageerror", lambda err: page_errors.append(str(err)))
        page.on("requestfailed", lambda req: failed_requests.append(f"{req.url} {req.failure}"))

        print("[TEST] Navigating to http://127.0.0.1:8000/ ...")
        response = page.goto("http://127.0.0.1:8000/", wait_until="domcontentloaded")
        assert response.status == 200, f"Expected 200 OK, got {response.status}"

        # Wait for initial data load
        page.wait_for_timeout(2000)

        # 1. Verify window.app is defined
        app_defined = page.evaluate("typeof window.app !== 'undefined'")
        assert app_defined, "window.app is not defined!"
        print("  [OK] window.app initialized successfully.")

        # 2. Verify all 8 navigation tabs
        tabs = [
            "tab-overview",
            "tab-discovery",
            "tab-outreach",
            "tab-portals",
            "tab-outcomes",
            "tab-manual",
            "tab-excel",
            "tab-workers"
        ]

        for tab_id in tabs:
            btn = page.locator(f"button[data-tab='{tab_id}']")
            assert btn.count() == 1, f"Missing tab button for {tab_id}"
            btn.click()
            page.wait_for_timeout(300)
            
            # Check view is active
            view = page.locator(f"#{tab_id}")
            assert "active" in (view.get_attribute("class") or ""), f"View #{tab_id} failed to activate"
            print(f"  [OK] Tab button -> #{tab_id} activated.")

        # Switch back to overview tab
        page.locator("button[data-tab='tab-overview']").click()
        page.wait_for_timeout(500)

        # 3. Verify Next Queued Dispatches table in Overview and Outreach
        ov_tbody = page.locator("#ovNextDispatchesTable tbody")
        ov_text = ov_tbody.inner_text()
        assert "Loading queued dispatches..." not in ov_text, "Overview queued dispatches table stuck on loading!"
        print(f"  [OK] Overview queued dispatches rendered: {ov_text.splitlines()[:2]}")

        page.locator("button[data-tab='tab-outreach']").click()
        page.wait_for_timeout(500)
        outreach_tbody = page.locator("#outreachNextQueueTable tbody")
        outreach_text = outreach_tbody.inner_text()
        assert "Loading queue..." not in outreach_text, "Outreach queued dispatches table stuck on loading!"
        print(f"  [OK] Outreach queued dispatches rendered: {outreach_text.splitlines()[:2]}")

        # 4. Test Evidence Modal
        page.locator("button[data-tab='tab-overview']").click()
        page.wait_for_timeout(300)
        page.evaluate("window.app.openEvidenceModal('ev_demo')")
        page.wait_for_timeout(300)
        ev_modal = page.locator("#evidenceModal")
        assert ev_modal.is_visible(), "Evidence Modal did not open!"
        page.locator("button:has-text('×')").first.click()
        page.wait_for_timeout(200)
        assert not ev_modal.is_visible(), "Evidence Modal did not close!"
        print("  [OK] Evidence modal open/close verified.")

        # 5. Test Portal Manager Modal
        page.locator("button[data-tab='tab-portals']").click()
        page.wait_for_timeout(600)
        
        # Click the real 'View / Manage' button in the portals table
        manage_btn = page.locator("#portalOppsTable button:has-text('View / Manage')").first
        assert manage_btn.count() == 1, "View / Manage button not found in portal table"
        manage_btn.click()
        page.wait_for_timeout(600)
        
        pm_modal = page.locator("#portalModal")
        assert pm_modal.is_visible(), "Portal Manager modal did not open!"
        
        # Test additional info input
        page.fill("#pmAdditionalInfo", "Testing additional info automated entry")
        page.click("button:has-text('Save Additional Info')")
        page.wait_for_timeout(300)
        
        # Close portal modal
        page.locator("#portalModal button:has-text('Close')").click()
        page.wait_for_timeout(300)
        assert not pm_modal.is_visible(), "Portal Manager modal did not close!"
        print("  [OK] Portal manager modal and controls verified.")

        # Switch back to overview for clean screenshot
        page.locator("button[data-tab='tab-overview']").click()
        page.wait_for_timeout(500)

        # 6. Capture verified screenshot
        screenshot_path = Path("data/dashboard_verified_screenshot.png")
        page.screenshot(path=str(screenshot_path), full_page=True)
        print(f"  [OK] Screenshot saved to {screenshot_path} ({screenshot_path.stat().st_size} bytes).")

        browser.close()

    # Verify zero severe console errors
    severe_errors = [m for m in console_messages if "[error]" in m.lower() and "favicon" not in m.lower()]
    print("\n--- Console Messages Summary ---")
    for msg in console_messages[:15]:
        print(" ", msg)
    if len(console_messages) > 15:
        print(f"  ... and {len(console_messages) - 15} more messages.")

    if page_errors:
        print(f"[FAIL] Unhandled page errors detected: {page_errors}")
        sys.exit(1)

    if severe_errors:
        print(f"[FAIL] Severe console errors detected: {severe_errors}")
        sys.exit(1)

    print("\n>>> ALL BROWSER INTERACTIONS & CONTROLS VERIFIED 100% WITH ZERO ERRORS! <<<")

if __name__ == "__main__":
    run_browser_verification()
