"""Spotify: its tracks, Spotify Matches and Playlists, and the Web API client behind a single interface.

The client signs in through the listener's own Development Mode app with Authorization Code and PKCE, so it needs
only a Client ID, and remembers the sign-in. It uses the endpoints left after Spotify's February 2026 changes.
"""

import asyncio
import base64
import hashlib
import os
import secrets
import threading
import time
import webbrowser
from collections.abc import Awaitable, Callable, Mapping
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlencode, urlparse

import httpx2
from pydantic import BaseModel, ValidationError

from djset.tracklist import Track

API = "https://api.spotify.com/v1"
ACCOUNTS = "https://accounts.spotify.com"
REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPE = "playlist-modify-private"
SEARCH_LIMIT = 10
"""Spotify's maximum."""
CLIENT_ID_SETTING = "SPOTIFY_CLIENT_ID"
TOKEN_FILE = "spotify-token.json"
REFRESH_EARLY = 60
"""Refresh an access token this many seconds before it expires, so it doesn't expire mid-request."""
SIGN_IN_TIMEOUT = 300.0

SETUP = f"""\
Making a Spotify playlist needs your own Spotify app (free, but its owner needs Spotify Premium):
  1. Open https://developer.spotify.com/dashboard and create an app.
     Redirect URI: {REDIRECT_URI}    APIs used: Web API
  2. Copy its Client ID and add `export {CLIENT_ID_SETTING}=<Client ID>` to your shell profile.
If you sign in with a different Spotify account from the app's owner, add it under the app's User Management."""

FORBIDDEN = (
    "Spotify refused the request ({message}). A Development Mode app works only while its owner has Spotify "
    "Premium, and only for the Spotify accounts added under the app's User Management in "
    "https://developer.spotify.com/dashboard."
)


class SpotifyTrack(BaseModel):
    """A track in Spotify's catalogue, as search reports it."""

    uri: str
    url: str
    name: str
    artists: list[str]

    def __str__(self) -> str:
        return f"{', '.join(self.artists)} – {self.name}"


class SpotifyMatch(SpotifyTrack):
    """The Spotify track chosen to stand in for a Track."""

    original: bool = False
    """It is the Track's Original, because the exact version isn't on Spotify."""


class Search(BaseModel):
    """What searching Spotify found for one Track: its Spotify Match, or nothing."""

    track: Track
    match: SpotifyMatch | None


class SpotifyPlaylist(BaseModel):
    """A playlist just created on Spotify."""

    id: str
    url: str


class Playlist(BaseModel):
    """The private Spotify playlist made for one Set."""

    id: str
    url: str
    name: str
    tracks: list[SpotifyMatch]
    """The chosen Spotify Matches, in Tracklist order."""
    added: int = 0
    """How many of `tracks` are on Spotify so far. Fewer than all when a run stopped part-way."""


class SpotifyBusy(Exception):
    """Spotify turned a request away for now (rate limit, quota or outage). Worth retrying later."""

    def __init__(self, reason: str, retry_after: float | None = None):
        super().__init__(reason)
        self.retry_after = retry_after


class SpotifyUnavailable(RuntimeError):
    """Spotify can't be used: the app isn't set up, sign-in failed, or Spotify refused the app."""


class Spotify(Protocol):
    async def search(self, query: str) -> list[SpotifyTrack]:
        """Spotify's best tracks for a search, best first. Raises SpotifyBusy."""
        ...

    async def create_playlist(self, name: str, description: str) -> SpotifyPlaylist:
        """A new, empty, private playlist. Raises SpotifyBusy."""
        ...

    async def add_items(self, playlist_id: str, uris: list[str]) -> None:
        """Append up to 100 tracks to a playlist. Raises SpotifyBusy."""
        ...


def client_id(environ: Mapping[str, str] = os.environ) -> str:
    """The listener's Spotify app, from SPOTIFY_CLIENT_ID."""
    if value := environ.get(CLIENT_ID_SETTING, "").strip():
        return value
    raise SpotifyUnavailable(SETUP)


Authorise = Callable[[str, str], Awaitable[dict[str, str]]]
"""Takes the sign-in page's URL and the redirect URI, and returns the query parameters Spotify redirected back with."""


class _Token(BaseModel):
    access_token: str
    refresh_token: str
    expires_at: float
    """Seconds since the epoch."""


class SpotifyWebApi:
    """Spotify's Web API, signed in as the listener."""

    def __init__(
        self,
        client_id: str,
        token_path: Path,
        authorise: Authorise,
        transport: httpx2.AsyncBaseTransport | None = None,
        now: Callable[[], float] = time.time,
    ):
        self._client_id = client_id
        self._token_path = token_path
        self._authorise = authorise
        self._transport = transport
        self._now = now
        self._token: _Token | None = None
        self._client: httpx2.AsyncClient | None = None

    async def sign_in(self) -> None:
        """Make sure there is a working sign-in, opening the browser only when there is none."""
        await self._access_token()

    async def search(self, query: str) -> list[SpotifyTrack]:
        body = await self._call("GET", "/search", params={"q": query, "type": "track", "limit": SEARCH_LIMIT})
        return [_track(item) for item in (body.get("tracks") or {}).get("items") or [] if item]

    async def create_playlist(self, name: str, description: str) -> SpotifyPlaylist:
        body = await self._call(
            "POST", "/me/playlists", json={"name": name, "description": description, "public": False}
        )
        return SpotifyPlaylist(id=body["id"], url=body["external_urls"]["spotify"])

    async def add_items(self, playlist_id: str, uris: list[str]) -> None:
        await self._call("POST", f"/playlists/{playlist_id}/items", json={"uris": uris})

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _call(self, method: str, path: str, **options: Any) -> dict[str, Any]:
        for attempt in range(2):
            token = await self._access_token(refresh=attempt > 0)
            headers = {"Authorization": f"Bearer {token}"}
            response = await self._send(method, f"{API}{path}", headers=headers, **options)
            if response.status_code != 401:
                break
        status = response.status_code
        if status == 429:
            raise SpotifyBusy(f"Spotify answered HTTP 429 ({_message(response)})", _retry_after(response))
        if status >= 500:
            raise SpotifyBusy(f"Spotify answered HTTP {status}")
        if status == 403:
            raise SpotifyUnavailable(FORBIDDEN.format(message=f"HTTP 403: {_message(response)}"))
        if status >= 400:
            raise SpotifyUnavailable(f"Spotify refused the request (HTTP {status}: {_message(response)}).")
        return response.json() if response.content else {}

    async def _send(self, method: str, url: str, **options: Any) -> httpx2.Response:
        if self._client is None:
            self._client = httpx2.AsyncClient(transport=self._transport, timeout=30)
        try:
            return await self._client.request(method, url, **options)
        except httpx2.TransportError as error:
            raise SpotifyBusy(f"Spotify couldn't be reached ({type(error).__name__})") from error

    async def _access_token(self, refresh: bool = False) -> str:
        token = self._token or self._load()
        if token and not refresh and token.expires_at - REFRESH_EARLY > self._now():
            return token.access_token
        if token:
            token = await self._refreshed(token)
        if token is None:
            token = await self._signed_in()
        self._save(token)
        self._token = token
        return token.access_token

    async def _refreshed(self, token: _Token) -> _Token | None:
        """A new access token, or None when Spotify no longer accepts the sign-in (as after six months)."""
        form = {"grant_type": "refresh_token", "refresh_token": token.refresh_token, "client_id": self._client_id}
        response = await self._send("POST", f"{ACCOUNTS}/api/token", data=form)
        if response.status_code == 400 and _oauth_error(response) == "invalid_grant":
            return None
        if response.status_code != 200:
            raise _sign_in_failure(response)
        return self._token_from(response.json(), keep_refresh_token=token.refresh_token)

    async def _signed_in(self) -> _Token:
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)
        query = {
            "client_id": self._client_id,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPE,
            "state": state,
            "code_challenge_method": "S256",
            "code_challenge": challenge,
        }
        answer = await self._authorise(f"{ACCOUNTS}/authorize?{urlencode(query)}", REDIRECT_URI)
        if answer.get("state") != state:
            raise SpotifyUnavailable("Spotify's sign-in answer didn't match this sign-in. Run the command again.")
        if error := answer.get("error"):
            if error == "access_denied":
                raise SpotifyUnavailable("The Spotify sign-in was cancelled, so no playlist was made.")
            raise SpotifyUnavailable(f"The Spotify sign-in failed ({error}).")
        form = {
            "grant_type": "authorization_code",
            "code": answer.get("code", ""),
            "redirect_uri": REDIRECT_URI,
            "client_id": self._client_id,
            "code_verifier": verifier,
        }
        response = await self._send("POST", f"{ACCOUNTS}/api/token", data=form)
        if response.status_code != 200:
            raise _sign_in_failure(response)
        return self._token_from(response.json())

    def _token_from(self, body: dict[str, Any], keep_refresh_token: str = "") -> _Token:
        return _Token(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token") or keep_refresh_token,
            expires_at=self._now() + int(body.get("expires_in", 3600)),
        )

    def _load(self) -> _Token | None:
        try:
            return _Token.model_validate_json(self._token_path.read_text())
        except (OSError, ValidationError):
            return None

    def _save(self, token: _Token) -> None:
        """Saved readable only by the user: the sign-in can change their playlists."""
        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._token_path.with_name(self._token_path.name + ".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as file:
            file.write(token.model_dump_json())
        os.chmod(temporary, 0o600)
        temporary.replace(self._token_path)


def _track(item: dict[str, Any]) -> SpotifyTrack:
    url = (item.get("external_urls") or {}).get("spotify") or f"https://open.spotify.com/track/{item['id']}"
    artists = [artist["name"] for artist in item.get("artists") or [] if artist.get("name")]
    return SpotifyTrack(uri=item["uri"], url=url, name=item.get("name") or "", artists=artists)


def _message(response: httpx2.Response) -> str:
    try:
        error = response.json().get("error")
    except ValueError:
        return response.text[:200] or response.reason_phrase
    if isinstance(error, dict):
        return " ".join(str(part) for part in (error.get("message"), error.get("reason")) if part)
    return str(error or response.reason_phrase)


def _retry_after(response: httpx2.Response) -> float | None:
    try:
        return float(response.headers["Retry-After"])
    except (KeyError, ValueError):
        return None


def _oauth_error(response: httpx2.Response) -> str:
    try:
        return str(response.json().get("error", ""))
    except ValueError:
        return ""


def _sign_in_failure(response: httpx2.Response) -> SpotifyBusy | SpotifyUnavailable:
    if response.status_code == 429 or response.status_code >= 500:
        return SpotifyBusy(f"Spotify's sign-in answered HTTP {response.status_code}", _retry_after(response))
    try:
        body = response.json()
        reason = f"{body.get('error')}: {body.get('error_description', '')}".rstrip(": ")
    except ValueError:
        reason = f"HTTP {response.status_code}"
    return SpotifyUnavailable(
        f"Spotify refused the sign-in ({reason}). Check that {CLIENT_ID_SETTING} is your app's Client ID and that "
        f"the app's Redirect URIs include {REDIRECT_URI}."
    )


class BrowserSignIn:
    """Opens Spotify's sign-in page in the browser and waits for Spotify to redirect back to this machine."""

    def __init__(
        self,
        note: Callable[[str], None],
        open_browser: Callable[[str], object] = webbrowser.open,
        timeout: float = SIGN_IN_TIMEOUT,
    ):
        self._note = note
        self._open_browser = open_browser
        self._timeout = timeout

    async def __call__(self, url: str, redirect_uri: str) -> dict[str, str]:
        stop = threading.Event()
        try:
            return await asyncio.to_thread(self._wait, url, redirect_uri, stop)
        finally:
            stop.set()  # when cancelled (Ctrl-C), so the run doesn't wait for the sign-in to time out

    def _wait(self, url: str, redirect_uri: str, stop: threading.Event) -> dict[str, str]:
        address = urlparse(redirect_uri)
        answer: dict[str, str] = {}

        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                request = urlparse(self.path)
                if request.path != address.path:
                    self.send_error(404)
                    return
                answer.update({name: values[0] for name, values in parse_qs(request.query).items()})
                page = "Signed in to Spotify. You can close this tab." if "code" in answer else "Not signed in."
                body = f"<!doctype html><title>djset</title><p>{page}</p>".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        try:
            server = HTTPServer((address.hostname or "127.0.0.1", address.port or 80), Redirect)
        except OSError as error:
            raise SpotifyUnavailable(
                f"Can't wait for Spotify's sign-in on {address.netloc} ({error.strerror}). "
                "Close whatever is using that port and try again."
            ) from error
        with server:
            server.timeout = 1
            self._note(f"Opening Spotify in your browser to sign in. If it doesn't open, go to:\n{url}")
            self._open_browser(url)
            deadline = time.monotonic() + self._timeout
            while not answer and not stop.is_set():
                if time.monotonic() > deadline:
                    raise SpotifyUnavailable(
                        f"No Spotify sign-in arrived within {round(self._timeout / 60)} minutes. Run the command again."
                    )
                server.handle_request()
        return answer
