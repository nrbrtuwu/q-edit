"""TikTok account linking and Direct Post upload for q-edit. Standard library only.

Login: OAuth 2 with PKCE, the browser redirects back to a one-shot server on 127.0.0.1.
Posting: Content Posting API, Direct Post with FILE_UPLOAD (chunked PUT), then status polling.
Keys and tokens are stored encrypted with Windows DPAPI (only this Windows user can read them).
"""

import base64
import ctypes
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com/v2/"
SCOPES = "user.info.basic,video.publish"
PORTAL = "https://developers.tiktok.com/apps/"
MUSIC_URL = "https://www.tiktok.com/legal/page/global/music-usage-confirmation/en"
BRANDED_URL = "https://www.tiktok.com/legal/page/global/bc-policy/en"
REDIRECT_HELP = "http://127.0.0.1:*/callback/"
MiB = 1024 * 1024
CHUNK = 10 * MiB          # TikTok: chunks 5-64 MB, the last one may be up to 128 MB
MAX_SIZE = 4096 * MiB
MIME = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}

ERRORS = {  # friendlier text for the codes users actually hit
    "unaudited_client_can_only_post_to_private_accounts":
        "Your TikTok app isn't reviewed yet, so it can only post to a private account. "
        "Set the account to private in TikTok (Settings > Privacy), post, then make it public again.",
    "spam_risk_too_many_posts": "TikTok says this account has posted too much today. Try again later.",
    "spam_risk_user_banned_from_posting": "TikTok has blocked this account from posting.",
    "reached_active_user_cap": "Your TikTok app reached its daily user limit. Try again tomorrow.",
    "privacy_level_option_mismatch": "That privacy option isn't available for this account.",
    "access_token_invalid": "The TikTok login expired. Connect the account again.",
    "scope_not_authorized": "The account didn't grant posting permission. Connect it again.",
    "rate_limit_exceeded": "Too many requests to TikTok. Wait a minute and try again.",
    "login_required": "Connect a TikTok account first.",
    "auth_removed": "The account removed q-edit's access. Connect it again.",
}


class TikTokError(Exception):
    def __init__(self, code, message=""):
        self.code = code
        super().__init__(ERRORS.get(code) or message or code)


# ---------------------------------------------------------------- DPAPI
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.c_void_p)]


def _dpapi(data, encrypt):
    if os.name != "nt":
        return data
    buf = ctypes.create_string_buffer(data, len(data))
    src, out = _Blob(len(data), ctypes.cast(buf, ctypes.c_void_p)), _Blob()
    crypt = ctypes.windll.crypt32
    fn = crypt.CryptProtectData if encrypt else crypt.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(out)):
        raise OSError("could not decrypt the TikTok login")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.c_void_p(out.pbData))


# ---------------------------------------------------------------- client
class TikTok:
    """One linked account. data keys: client_key, client_secret, access_token, refresh_token,
    expires_at, refresh_expires_at, open_id."""

    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        try:
            self.data = json.loads(_dpapi(base64.b64decode(path.read_bytes()), False))
        except (OSError, ValueError):
            self.data = {}

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_bytes(base64.b64encode(_dpapi(json.dumps(self.data).encode(), True)))
        except OSError:
            pass

    @property
    def has_keys(self):
        return bool(self.data.get("client_key") and self.data.get("client_secret"))

    @property
    def connected(self):
        return bool(self.data.get("refresh_token")) and \
            self.data.get("refresh_expires_at", 0) > time.time()

    def set_keys(self, key, secret):
        if key != self.data.get("client_key"):
            self.forget()
        self.data.update(client_key=key, client_secret=secret)
        self.save()

    def forget(self):
        for k in ("access_token", "refresh_token", "expires_at", "refresh_expires_at", "open_id"):
            self.data.pop(k, None)
        self.save()

    # ---------- HTTP ----------
    @staticmethod
    def _send(req, timeout=60):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()
        except (urllib.error.URLError, OSError) as e:
            raise TikTokError("network", f"Could not reach TikTok: {getattr(e, 'reason', e)}")

    def _token(self, params):
        params = dict(params, client_key=self.data["client_key"],
                      client_secret=self.data["client_secret"])
        req = urllib.request.Request(
            API + "oauth/token/", data=urllib.parse.urlencode(params).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        _, body = self._send(req)
        try:
            j = json.loads(body)
        except ValueError:
            raise TikTokError("bad_response", "TikTok sent an unexpected reply.")
        if "access_token" not in j:
            raise TikTokError(j.get("error", "token_failed"), j.get("error_description", ""))
        now = time.time()
        self.data.update(access_token=j["access_token"], refresh_token=j["refresh_token"],
                         expires_at=now + int(j.get("expires_in", 86400)) - 120,
                         refresh_expires_at=now + int(j.get("refresh_expires_in", 31536000)) - 120,
                         open_id=j.get("open_id", ""))
        self.save()

    def access_token(self):
        with self.lock:
            if self.data.get("access_token") and self.data.get("expires_at", 0) > time.time():
                return self.data["access_token"]
            if not self.connected:
                raise TikTokError("login_required")
            self._token({"grant_type": "refresh_token", "refresh_token": self.data["refresh_token"]})
            return self.data["access_token"]

    def api(self, path, body=None, retry=True):
        req = urllib.request.Request(
            API + path, method="POST", data=b"" if body is None else json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.access_token()}",
                     "Content-Type": "application/json; charset=UTF-8"})
        status, raw = self._send(req)
        try:
            j = json.loads(raw)
        except ValueError:
            raise TikTokError("bad_response", f"TikTok sent an unexpected reply (HTTP {status}).")
        err = j.get("error") or {}
        code = err.get("code", "ok") if isinstance(err, dict) else str(err)
        if code == "access_token_invalid" and retry:
            self.data["expires_at"] = 0   # force a refresh, then try once more
            return self.api(path, body, retry=False)
        if code != "ok":
            raise TikTokError(code, err.get("message", "") if isinstance(err, dict) else "")
        return j.get("data") or {}

    # ---------- login ----------
    def login(self, cancel, timeout=300):
        """Opens the TikTok login in the browser and waits for the redirect. Blocks."""
        if not self.has_keys:
            raise TikTokError("no_keys", "Add your TikTok developer keys first.")
        verifier = secrets.token_urlsafe(48)                         # 64 unreserved characters
        challenge = hashlib.sha256(verifier.encode()).hexdigest()    # TikTok wants hex, not base64
        state = secrets.token_urlsafe(16)
        got, done = {}, threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                url = urllib.parse.urlsplit(self.path)
                if url.path.rstrip("/") != "/callback":
                    self.send_error(404)
                    return
                q = {k: v[0] for k, v in urllib.parse.parse_qs(url.query).items()}
                ok = "code" in q and q.get("state") == state
                msg = ("TikTok is connected. You can close this tab and go back to q-edit."
                       if ok else "TikTok login didn't finish. Go back to q-edit and try again.")
                page = (f"<!doctype html><meta charset=utf-8><title>q-edit</title>"
                        f"<body style='font:16px Segoe UI,sans-serif;background:#16161a;color:#e8e8ee;"
                        f"display:grid;place-items:center;height:90vh'><p>{msg}</p>").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
                if not done.is_set():
                    got.update(q)
                    done.set()

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), Handler)
        redirect = f"http://127.0.0.1:{srv.server_address[1]}/callback/"
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            webbrowser.open(AUTH_URL + "?" + urllib.parse.urlencode({
                "client_key": self.data["client_key"], "scope": SCOPES, "response_type": "code",
                "redirect_uri": redirect, "state": state, "code_challenge": challenge,
                "code_challenge_method": "S256"}))
            end = time.time() + timeout
            while not done.wait(0.25):
                if cancel.is_set():
                    raise TikTokError("cancelled", "Login cancelled.")
                if time.time() > end:
                    raise TikTokError("timeout", "The TikTok login timed out.")
        finally:
            srv.shutdown()
            srv.server_close()
        if got.get("state") != state:
            raise TikTokError("state_mismatch", "The login reply didn't match. Try again.")
        if "code" not in got:
            raise TikTokError(got.get("error", "denied"),
                              got.get("error_description") or "The login was cancelled in TikTok.")
        self._token({"grant_type": "authorization_code", "code": got["code"],
                     "redirect_uri": redirect, "code_verifier": verifier})

    def disconnect(self):
        if self.data.get("access_token"):
            try:
                req = urllib.request.Request(API + "oauth/revoke/", data=urllib.parse.urlencode({
                    "client_key": self.data["client_key"], "client_secret": self.data["client_secret"],
                    "token": self.data["access_token"]}).encode(),
                    headers={"Content-Type": "application/x-www-form-urlencoded"})
                self._send(req, timeout=10)
            except TikTokError:
                pass
        self.forget()

    # ---------- posting ----------
    def creator_info(self):
        """nickname, username, avatar url, privacy options, disabled interactions, max duration."""
        return self.api("post/publish/creator_info/query/")

    def post_video(self, path, post_info, progress, cancel):
        """Uploads a video with Direct Post and waits until TikTok has it.
        progress(text, fraction) is called from this thread. Returns the final status dict."""
        size = os.path.getsize(path)
        if size > MAX_SIZE:
            raise TikTokError("too_big", "TikTok accepts videos up to 4 GB.")
        chunk = size if size < 2 * CHUNK else CHUNK
        count = max(1, size // chunk)
        data = self.api("post/publish/video/init/", {
            "post_info": post_info,
            "source_info": {"source": "FILE_UPLOAD", "video_size": size,
                            "chunk_size": chunk, "total_chunk_count": count}})
        pid, url = data["publish_id"], data["upload_url"]
        mime = MIME.get(os.path.splitext(path)[1].lower(), "video/mp4")
        with open(path, "rb") as f:
            for i in range(count):
                if cancel.is_set():
                    raise TikTokError("cancelled", "Upload cancelled.")
                first = i * chunk
                last = size - 1 if i == count - 1 else first + chunk - 1
                part = f.read(last - first + 1)
                req = urllib.request.Request(url, data=part, method="PUT", headers={
                    "Content-Type": mime, "Content-Length": str(len(part)),
                    "Content-Range": f"bytes {first}-{last}/{size}"})
                status, body = self._send(req, timeout=600)
                if status not in (200, 201, 206):
                    raise TikTokError("upload_failed",
                                      f"Upload failed (HTTP {status}). {body[:200].decode(errors='ignore')}")
                progress(f"Uploading...  {(last + 1) / size:.0%}", (last + 1) / size)
        progress("TikTok is processing the video...", 1.0)
        end = time.time() + 900
        while time.time() < end:
            st = self.api("post/publish/status/fetch/", {"publish_id": pid})
            s = st.get("status")
            if s == "PUBLISH_COMPLETE":
                return st
            if s == "FAILED":
                reason = st.get("fail_reason", "unknown")
                raise TikTokError(reason, f"TikTok rejected the video ({reason}).")
            time.sleep(4)   # status limit is 30 requests a minute
        return {"status": "PROCESSING", "publish_id": pid}


def open_url(url):
    webbrowser.open(url)
