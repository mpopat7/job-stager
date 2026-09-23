"""Resume storage: local disk for a personal install, a private B2 bucket for a shared one."""

import json

import httpx
import pytest

from core.config.schema import ResumesConfig
from core.store import files


@pytest.fixture
def local(tmp_path, monkeypatch):
    for var in ("B2_KEY_ID", "B2_APPLICATION_KEY", "B2_BUCKET_NAME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(files, "LOCAL_ROOT", tmp_path / "resumes")
    return tmp_path


def test_a_local_upload_lands_in_that_users_own_directory(local):
    ref = files.save_resume(7, 2028, "My Resume.pdf", b"%PDF one")
    assert ref == str((local / "resumes" / "user_7" / "2028_My_Resume.pdf").resolve())
    assert files.read_resume(ref) == b"%PDF one"
    assert files.local_copy(ref).read_bytes() == b"%PDF one"


def test_replacing_a_resume_never_deletes_a_file_the_person_pointed_at_themselves(local):
    """profile.yaml can name a resume anywhere on disk; only our own uploads are ours to remove."""
    own = local / "OneDrive" / "resume.pdf"
    own.parent.mkdir()
    own.write_bytes(b"%PDF mine")
    files.delete_resume(7, str(own))
    assert own.exists()

    uploaded = files.save_resume(7, 2028, "a.pdf", b"x")
    files.delete_resume(8, uploaded)  # another user's id
    assert files.available(uploaded)
    files.delete_resume(7, uploaded)
    assert not files.available(uploaded)


def test_the_profile_keeps_a_reference_and_resolves_it_to_a_file(local):
    ref = files.save_resume(1, 2029, "r.pdf", b"%PDF 29")
    resumes = ResumesConfig(variants={"2029": ref})
    assert resumes.resume_ref(2029) == ref
    assert resumes.resolve_resume(2029).read_bytes() == b"%PDF 29"
    assert ResumesConfig(variants={"2029": str(local / "gone.pdf")}).resolve_resume(2029) is None


class FakeB2:
    """Just enough of B2's native API to hold files in a dict."""

    def __init__(self):
        self.files: dict[str, list[tuple[str, bytes]]] = {}
        self.auth_calls = 0
        self.expire_next = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("b2_authorize_account"):
            self.auth_calls += 1
            return httpx.Response(200, json={
                "accountId": "acct", "authorizationToken": f"tok{self.auth_calls}",
                "apiInfo": {"storageApi": {
                    "apiUrl": "https://api.b2.test", "downloadUrl": "https://f.b2.test",
                    "allowed": {"buckets": [{"id": "bkt1", "name": "resumes-test"}]},
                }},
            })
        if self.expire_next and request.headers.get("Authorization") == "tok1":
            self.expire_next = False
            return httpx.Response(401, json={"code": "expired_auth_token"})
        if path.endswith("b2_get_upload_url"):
            return httpx.Response(200, json={"uploadUrl": "https://up.b2.test/u", "authorizationToken": "up"})
        if path == "/u":
            name = request.headers["X-Bz-File-Name"]
            self.files.setdefault(name, []).append((f"id{len(self.files)}", request.content))
            return httpx.Response(200, json={"fileId": "x"})
        if path.endswith("b2_list_file_versions"):
            prefix = json.loads(request.content)["prefix"]
            return httpx.Response(200, json={"files": [
                {"fileName": n, "fileId": fid} for n, vs in self.files.items()
                if n.startswith(prefix) for fid, _ in vs
            ]})
        if path.endswith("b2_delete_file_version"):
            body = json.loads(request.content)
            self.files[body["fileName"]] = [v for v in self.files[body["fileName"]] if v[0] != body["fileId"]]
            return httpx.Response(200, json={"fileId": body["fileId"]})
        if path.startswith("/file/resumes-test/"):
            versions = self.files.get(path[len("/file/resumes-test/"):])
            return httpx.Response(200, content=versions[-1][1]) if versions else httpx.Response(404)
        return httpx.Response(404)


@pytest.fixture
def b2(monkeypatch, tmp_path):
    fake = FakeB2()
    monkeypatch.setenv("B2_KEY_ID", "kid")
    monkeypatch.setenv("B2_APPLICATION_KEY", "K-secret")
    monkeypatch.setenv("B2_BUCKET_NAME", "resumes-test")
    monkeypatch.delenv("B2_BUCKET_ID", raising=False)
    monkeypatch.setattr(files, "_bucket", files.B2Bucket(
        "kid", "K-secret", "resumes-test", client=httpx.Client(transport=httpx.MockTransport(fake.handler))
    ))
    monkeypatch.setattr(files.tempfile, "gettempdir", lambda: str(tmp_path))
    return fake


def test_with_b2_configured_an_upload_goes_to_the_bucket_not_the_disk(local, b2):
    ref = files.save_resume(3, 2028, "cv.pdf", b"%PDF b2")
    assert ref == "b2://resumes-test/user_3/2028_cv.pdf"
    assert not (local / "resumes").exists()
    assert files.available(ref)
    assert files.read_resume(ref) == b"%PDF b2"
    # A browser upload control needs a real file, named as the person named it.
    copy = files.local_copy(ref)
    assert copy.name == "2028_cv.pdf" and copy.read_bytes() == b"%PDF b2"


def test_re_uploading_under_the_same_name_leaves_one_copy_and_no_stale_cache(b2):
    ref = files.save_resume(3, 2028, "cv.pdf", b"old")
    assert files.local_copy(ref).read_bytes() == b"old"
    files.save_resume(3, 2028, "cv.pdf", b"new")
    assert len(b2.files["user_3/2028_cv.pdf"]) == 1
    assert files.local_copy(ref).read_bytes() == b"new"


def test_deleting_is_scoped_to_the_owner(b2):
    ref = files.save_resume(3, 2028, "cv.pdf", b"x")
    files.delete_resume(4, ref)
    assert b2.files["user_3/2028_cv.pdf"]
    files.delete_resume(3, ref)
    assert not b2.files["user_3/2028_cv.pdf"]


def test_an_expired_token_is_renewed_once_rather_than_failing_the_request(b2):
    ref = files.save_resume(3, 2028, "cv.pdf", b"x")
    b2.expire_next = True
    assert files.read_resume(ref) == b"x"
    assert b2.auth_calls == 2


def test_a_shared_deployment_will_not_boot_without_somewhere_durable_for_resumes(monkeypatch):
    import web.auth as auth

    monkeypatch.setenv("JOBSTAGER_MULTI_TENANT", "1")
    monkeypatch.setenv("JOBSTAGER_SECURE_COOKIES", "1")
    monkeypatch.setenv("JOBSTAGER_SECRET_KEY", "x" * 32)
    monkeypatch.delenv("B2_KEY_ID", raising=False)
    with pytest.raises(RuntimeError, match="B2_KEY_ID"):
        auth.check_deployment_config()
