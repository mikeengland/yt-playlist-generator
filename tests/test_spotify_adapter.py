"""The real Spotify client, against a stand-in for Spotify's accounts service and Web API (no network)."""

import asyncio
import base64
import hashlib
import json
import socket
import stat
import threading
import urllib.request
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx2
import pytest

from djset.spotify import (
    REDIRECT_URI,
    BrowserSignIn,
    SpotifyBusy,
    SpotifyTrack,
    SpotifyUnavailable,
    SpotifyWebApi,
    client_id,
)

NOW = 1_800_000_000.0
SIGNED_IN = {"access_token": "A1", "token_type": "Bearer", "expires_in": 3600, "refresh_token": "R1"}
SEARCH_ANSWER = {
    "tracks": {
        "items": [
            {
                "id": "6Mq5OAzrlrTbxEmMkzuGRU",
                "uri": "spotify:track:6Mq5OAzrlrTbxEmMkzuGRU",
                "name": "Love Me JeJe - FNKY Remix",
                "artists": [{"name": "Tems"}, {"name": "FNKY"}],
                "external_urls": {"spotify": "https://open.spotify.com/track/6Mq5OAzrlrTbxEmMkzuGRU"},
            },
            None,
        ]
    }
}


class FakeServer:
    """Answers each (method, path) from a queue of answers, recording every request."""

    def __init__(self, answers: dict[tuple[str, str], list[httpx2.Response]]):
        self.answers = answers
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.answers[(request.method, request.url.path)].pop(0)

    def sent(self, path: str) -> list[httpx2.Request]:
        return [request for request in self.requests if request.url.path == path]


class FakeBrowser:
    """Signs in at once, answering with the request's state unless told otherwise."""

    def __init__(self, **answer: str):
        self.answer = answer
        self.urls: list[str] = []

    async def __call__(self, url: str, redirect_uri: str) -> dict[str, str]:
        self.urls.append(url)
        state = parse_qs(urlparse(url).query)["state"][0]
        return {"code": "the-code", "state": state, **self.answer}


def answer(status: int = 200, body: object = None, headers: dict[str, str] | None = None) -> httpx2.Response:
    return httpx2.Response(status, json=body if body is not None else {}, headers=headers)


def client(tmp_path: Path, server: FakeServer, browser: FakeBrowser | None = None, now: float = NOW) -> SpotifyWebApi:
    return SpotifyWebApi(
        "the-client-id",
        tmp_path / "spotify-token.json",
        browser or FakeBrowser(),
        transport=httpx2.MockTransport(server),
        now=lambda: now,
    )


def run(api: SpotifyWebApi, call: Callable[[SpotifyWebApi], object]):
    async def go():
        try:
            return await call(api)
        finally:
            await api.aclose()

    return asyncio.run(go())


def form(request: httpx2.Request) -> dict[str, str]:
    return {name: values[0] for name, values in parse_qs(request.content.decode()).items()}


def save_token(tmp_path: Path, expires_at: float) -> None:
    token = {"access_token": "A0", "refresh_token": "R0", "expires_at": expires_at}
    (tmp_path / "spotify-token.json").write_text(json.dumps(token))


def test_the_first_search_signs_in_with_pkce_and_the_sign_in_is_kept_for_later_runs(tmp_path):
    server = FakeServer(
        {("POST", "/api/token"): [answer(body=SIGNED_IN)], ("GET", "/v1/search"): [answer(body=SEARCH_ANSWER)] * 2}
    )
    browser = FakeBrowser()

    tracks = run(client(tmp_path, server, browser), lambda api: api.search("Love Me JeJe FNKY Remix Tems"))

    assert tracks == [
        SpotifyTrack(
            uri="spotify:track:6Mq5OAzrlrTbxEmMkzuGRU",
            url="https://open.spotify.com/track/6Mq5OAzrlrTbxEmMkzuGRU",
            name="Love Me JeJe - FNKY Remix",
            artists=["Tems", "FNKY"],
        )
    ]
    (url,) = browser.urls
    assert url.startswith("https://accounts.spotify.com/authorize?")
    asked = {name: values[0] for name, values in parse_qs(urlparse(url).query).items()}
    (token_request,) = server.sent("/api/token")
    sent = form(token_request)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(sent["code_verifier"].encode()).digest()).rstrip(b"=")
    assert asked["code_challenge"] == challenge.decode()
    assert 43 <= len(sent["code_verifier"]) <= 128
    assert {k: asked[k] for k in ("client_id", "response_type", "redirect_uri", "scope", "code_challenge_method")} == {
        "client_id": "the-client-id",
        "response_type": "code",
        "redirect_uri": "http://127.0.0.1:8888/callback",
        "scope": "playlist-modify-private",
        "code_challenge_method": "S256",
    }
    assert {k: v for k, v in sent.items() if k != "code_verifier"} == {
        "grant_type": "authorization_code",
        "code": "the-code",
        "redirect_uri": REDIRECT_URI,
        "client_id": "the-client-id",
    }
    (search,) = server.sent("/v1/search")
    assert search.headers["Authorization"] == "Bearer A1"
    assert dict(search.url.params) == {"q": "Love Me JeJe FNKY Remix Tems", "type": "track", "limit": "10"}
    saved = tmp_path / "spotify-token.json"
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    later = FakeBrowser()
    run(client(tmp_path, server, later, now=NOW + 600), lambda api: api.search("Wannabe"))
    assert later.urls == []
    assert server.sent("/v1/search")[1].headers["Authorization"] == "Bearer A1"


def test_an_expired_access_token_is_refreshed_keeping_the_refresh_token_unless_a_new_one_comes(tmp_path):
    save_token(tmp_path, expires_at=NOW - 1)
    server = FakeServer(
        {
            ("POST", "/api/token"): [answer(body={"access_token": "A2", "expires_in": 3600})],
            ("GET", "/v1/search"): [answer(body=SEARCH_ANSWER)],
        }
    )
    browser = FakeBrowser()

    run(client(tmp_path, server, browser), lambda api: api.search("Wannabe"))

    assert browser.urls == []
    assert form(server.sent("/api/token")[0]) == {
        "grant_type": "refresh_token",
        "refresh_token": "R0",
        "client_id": "the-client-id",
    }
    assert server.sent("/v1/search")[0].headers["Authorization"] == "Bearer A2"
    saved = json.loads((tmp_path / "spotify-token.json").read_text())
    assert (saved["access_token"], saved["refresh_token"], saved["expires_at"]) == ("A2", "R0", NOW + 3600)


def test_a_sign_in_spotify_no_longer_accepts_means_signing_in_again(tmp_path):
    save_token(tmp_path, expires_at=NOW - 1)
    server = FakeServer(
        {
            ("POST", "/api/token"): [answer(400, {"error": "invalid_grant"}), answer(body=SIGNED_IN)],
            ("GET", "/v1/search"): [answer(body=SEARCH_ANSWER)],
        }
    )
    browser = FakeBrowser()

    run(client(tmp_path, server, browser), lambda api: api.search("Wannabe"))

    assert len(browser.urls) == 1
    assert json.loads((tmp_path / "spotify-token.json").read_text())["refresh_token"] == "R1"


def test_an_access_token_spotify_rejects_is_refreshed_once(tmp_path):
    save_token(tmp_path, expires_at=NOW + 3000)
    server = FakeServer(
        {
            ("POST", "/api/token"): [answer(body={"access_token": "A2", "expires_in": 3600})],
            ("GET", "/v1/search"): [answer(401, {"error": {"status": 401, "message": "expired"}}), answer(body={})],
        }
    )

    assert run(client(tmp_path, server), lambda api: api.search("Wannabe")) == []
    assert [r.headers["Authorization"] for r in server.sent("/v1/search")] == ["Bearer A0", "Bearer A2"]


def test_a_private_playlist_is_created_and_items_are_added_with_a_uris_body(tmp_path):
    save_token(tmp_path, expires_at=NOW + 3000)
    created = {"id": "3cEYpjA9oz9GiPac4AsH4n", "external_urls": {"spotify": "https://open.spotify.com/playlist/3cE"}}
    server = FakeServer(
        {
            ("POST", "/v1/me/playlists"): [answer(201, created)],
            ("POST", "/v1/playlists/3cEYpjA9oz9GiPac4AsH4n/items"): [answer(201, {"snapshot_id": "abc"})],
        }
    )

    async def make(api):
        playlist = await api.create_playlist("Siangyoo 011", "The Tracks from the Set at https://youtu.be/x")
        await api.add_items(playlist.id, ["spotify:track:1", "spotify:track:2"])
        return playlist

    playlist = run(client(tmp_path, server), make)

    assert (playlist.id, playlist.url) == ("3cEYpjA9oz9GiPac4AsH4n", "https://open.spotify.com/playlist/3cE")
    create, add = server.requests
    assert json.loads(create.content) == {
        "name": "Siangyoo 011",
        "description": "The Tracks from the Set at https://youtu.be/x",
        "public": False,
    }
    assert json.loads(add.content) == {"uris": ["spotify:track:1", "spotify:track:2"]}


@pytest.mark.parametrize(
    "response, retry_after, reason",
    [
        (
            answer(429, {"error": {"status": 429, "message": "Too many requests"}}, {"Retry-After": "7"}),
            7.0,
            "HTTP 429 (Too many requests)",
        ),
        (
            answer(429, {"error": {"status": 429, "message": "Too many requests", "reason": "QUOTA_EXCEEDED"}}),
            None,
            "HTTP 429 (Too many requests QUOTA_EXCEEDED)",
        ),
        (answer(503, {"error": {"status": 503, "message": "Service unavailable"}}), None, "HTTP 503"),
    ],
)
def test_rate_limits_quotas_and_outages_are_busy(tmp_path, response, retry_after, reason):
    save_token(tmp_path, expires_at=NOW + 3000)
    server = FakeServer({("GET", "/v1/search"): [response]})

    with pytest.raises(SpotifyBusy) as busy:
        run(client(tmp_path, server), lambda api: api.search("Wannabe"))

    assert busy.value.retry_after == retry_after
    assert reason in str(busy.value)


def test_an_unreachable_spotify_is_busy(tmp_path):
    save_token(tmp_path, expires_at=NOW + 3000)

    def unreachable(request):
        raise httpx2.ConnectError("no route", request=request)

    api = SpotifyWebApi("id", tmp_path / "spotify-token.json", FakeBrowser(), httpx2.MockTransport(unreachable))
    with pytest.raises(SpotifyBusy, match="couldn't be reached"):
        run(api, lambda api: api.search("Wannabe"))


def test_a_refused_app_explains_development_mode(tmp_path):
    save_token(tmp_path, expires_at=NOW + 3000)
    refused = answer(403, {"error": {"status": 403, "message": "User not registered in the Developer Dashboard"}})
    server = FakeServer({("POST", "/v1/me/playlists"): [refused]})

    with pytest.raises(SpotifyUnavailable, match="owner has Spotify Premium.*User Management"):
        run(client(tmp_path, server), lambda api: api.create_playlist("Set", ""))


@pytest.mark.parametrize(
    "browser, message",
    [
        (FakeBrowser(state="someone else's"), "didn't match"),
        (FakeBrowser(error="access_denied"), "cancelled"),
    ],
)
def test_sign_in_answers_are_checked(tmp_path, browser, message):
    with pytest.raises(SpotifyUnavailable, match=message):
        run(client(tmp_path, FakeServer({}), browser), lambda api: api.sign_in())


def test_a_misconfigured_app_says_what_to_check(tmp_path):
    refused = answer(400, {"error": "invalid_client", "error_description": "Invalid redirect URI"})
    server = FakeServer({("POST", "/api/token"): [refused]})

    with pytest.raises(SpotifyUnavailable, match="invalid_client: Invalid redirect URI.*127.0.0.1:8888/callback"):
        run(client(tmp_path, server), lambda api: api.sign_in())


def test_a_missing_client_id_explains_how_to_set_up_a_spotify_app():
    with pytest.raises(SpotifyUnavailable, match="(?s)developer.spotify.com/dashboard.*export SPOTIFY_CLIENT_ID"):
        client_id({})
    assert client_id({"SPOTIFY_CLIENT_ID": " abc123 "}) == "abc123"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_the_browser_sign_in_waits_for_spotifys_redirect(tmp_path):
    redirect_uri = f"http://127.0.0.1:{free_port()}/callback"
    pages: list[str] = []
    notes: list[str] = []

    def spotify_redirects(url: str) -> None:
        def visit():
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(f"{redirect_uri}?code=the-code&state=s1", timeout=5) as page:
                pages.append(page.read().decode())

        threading.Thread(target=visit).start()

    sign_in = BrowserSignIn(notes.append, open_browser=spotify_redirects, timeout=10)

    answer = asyncio.run(sign_in("https://accounts.spotify.com/authorize?client_id=x", redirect_uri))

    assert answer == {"code": "the-code", "state": "s1"}
    assert "https://accounts.spotify.com/authorize?client_id=x" in notes[0]
    for _ in range(50):
        if pages:
            break
        threading.Event().wait(0.1)
    assert "Signed in to Spotify" in pages[0]
