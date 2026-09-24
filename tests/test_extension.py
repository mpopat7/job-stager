"""The browser extension, loaded into a real Chromium against a real server.

The server runs in this process on a free port with its own database and the fictional
test profile, so nothing here reads the operator's profile.yaml. The extension is copied
and taught that port, since its host permissions name fixed addresses.
"""

import asyncio
import json
from pathlib import Path
import shutil
import socket
import threading
import time

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXTENSION = ROOT / "extension"
FIXTURE_PROFILE = Path(__file__).parent / "fixtures" / "profile.test.yaml"
POSTING = "https://jobs.ashbyhq.com/testco/application"

# Shaped like the forms the extension meets: a label[for] field, a field named only by a
# label-ish div (Lever), a radio group under a legend, a select with a placeholder
# option, a lone consent checkbox, a Yes/No button group (Ashby), and a resume upload.
FORM = """<!doctype html><html><body>
<h1>Software Engineer Intern, Summer 2027</h1>
<form>
  <div class="field"><label for="first">First name *</label><input id="first" required></div>
  <div class="field"><label for="email">Email</label><input id="email" type="email"></div>
  <div class="application-question">
    <div class="application-label">LinkedIn profile</div><input name="urls[LinkedIn]">
  </div>
  <fieldset><legend>Will you now or in the future require visa sponsorship?</legend>
    <label><input type="radio" name="sponsor" value="y"> Yes</label>
    <label><input type="radio" name="sponsor" value="n"> No</label>
  </fieldset>
  <div class="field"><label for="auth">Are you legally authorized to work in the US?</label>
    <select id="auth"><option value="">Select...</option><option>Yes</option><option>No</option></select></div>
  <div class="field"><label><input type="checkbox" name="consent"> I agree to the privacy policy</label></div>
  <div class="question"><label>Are you open to relocation?</label>
    <button type="button">Yes</button><button type="button">No</button></div>
  <div class="field"><label for="resume">Resume</label><input id="resume" type="file"></div>
  <input type="hidden" name="csrf" value="x">
  <button type="submit">Submit application</button>
</form></body></html>"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path, monkeypatch):
    import uvicorn
    import web.auth as auth
    import web.server as web_server
    from core.config.loader import load_profile
    from core.store.profiles import ProfileStore
    from core.store.users import UserStore

    db = tmp_path / "ext.db"
    monkeypatch.setattr("core.store.db.DEFAULT_DB_PATH", db)
    auth.users = web_server.users = UserStore(db)
    auth.profiles = web_server.profiles = ProfileStore(db)
    user_id = auth.users.create_user("exttest", "ext-test-password")
    auth.profiles.save(user_id, load_profile(FIXTURE_PROFILE))

    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(web_server.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield {"url": f"http://127.0.0.1:{port}", "port": port, "users": auth.users, "user_id": user_id}
    srv.should_exit = True
    thread.join(timeout=5)


def _extension_for(port: int, dest: Path) -> Path:
    """A copy of the extension that is allowed to reach the test server's port."""
    shutil.copytree(EXTENSION, dest)
    origin = f"http://127.0.0.1:{port}"
    manifest = json.loads((dest / "manifest.json").read_text())
    manifest["host_permissions"].append(f"{origin}/*")
    manifest["content_scripts"][0]["matches"].append(f"{origin}/connect-extension*")
    (dest / "manifest.json").write_text(json.dumps(manifest))
    bg = dest / "background.js"
    bg.write_text(bg.read_text().replace(
        "const CONNECT_PAGES = [", f"const CONNECT_PAGES = [\n  '{origin}/connect-extension',"
    ))
    return dest


async def _launch(p, ext: Path, profile_dir: Path):
    context = await p.chromium.launch_persistent_context(
        str(profile_dir),
        channel="chromium",
        headless=True,
        args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}"],
    )
    worker = context.service_workers[0] if context.service_workers else await context.wait_for_event("serviceworker")
    return context, worker


@pytest.mark.asyncio
async def test_connect_page_hands_the_extension_its_own_token(server, tmp_path):
    from playwright.async_api import async_playwright

    ext = _extension_for(server["port"], tmp_path / "ext")
    dashboard = server["users"].start_session(server["user_id"])
    async with async_playwright() as p:
        context, worker = await _launch(p, ext, tmp_path / "profile")
        try:
            await context.add_cookies([{
                "name": "jobstager_session", "value": dashboard, "url": server["url"],
            }])
            page = await context.new_page()
            await page.goto(f"{server['url']}/connect-extension")
            await page.click("#connect")
            await page.wait_for_selector("#status.ok", timeout=10000)

            stored = await worker.evaluate("chrome.storage.local.get(['token', 'server'])")
            assert stored["server"] == server["url"]
            assert stored["token"] and stored["token"] != dashboard
            assert server["users"].user_for_session(stored["token"]) == server["user_id"]
        finally:
            await context.close()


@pytest.mark.asyncio
async def test_scan_describes_every_control_and_the_server_answers_them(server, tmp_path):
    from playwright.async_api import async_playwright

    ext = _extension_for(server["port"], tmp_path / "ext")
    token = server["users"].start_session(server["user_id"])
    async with async_playwright() as p:
        context, worker = await _launch(p, ext, tmp_path / "profile")
        try:
            await worker.evaluate(
                "([token, server]) => chrome.storage.local.set({ token, server })",
                [token, server["url"]],
            )
            await context.route(POSTING, lambda route: route.fulfill(
                status=200, content_type="text/html", body=FORM))
            page = await context.new_page()
            await page.goto(POSTING)

            result = await worker.evaluate(f"""async () => {{
                const [tab] = await chrome.tabs.query({{ url: '{POSTING}' }});
                return handlers.scan({{ tabId: tab.id }});
            }}""")
        finally:
            await context.close()

    by_question = {f["question"]: f for f in result["fields"]}
    assert by_question["First name *"]["kind"] == "text"
    assert by_question["First name *"]["required"] is True
    assert by_question["LinkedIn profile"]["kind"] == "text"
    sponsor = by_question["Will you now or in the future require visa sponsorship?"]
    assert sponsor["kind"] == "radio" and sponsor["offered"] == ["Yes", "No"]
    auth_q = by_question["Are you legally authorized to work in the US?"]
    assert auth_q["kind"] == "select" and auth_q["offered"] == ["Yes", "No"]
    assert by_question["I agree to the privacy policy"]["kind"] == "checkbox"
    relocate = by_question["Are you open to relocation?"]
    assert relocate["kind"] == "button" and relocate["offered"] == ["Yes", "No"]
    # The hidden input and the submit button are not questions.
    assert len(result["fields"]) == 7
    assert [f["question"] for f in result["files"]] == ["Resume"]

    answers = {a["ref"]: a for a in result["resolved"]["answers"]}
    assert answers[sponsor["ref"]]["value"] in ("Yes", "No")
    assert answers[auth_q["ref"]]["value"] in ("Yes", "No")
    assert answers[by_question["First name *"]["ref"]]["value"]
