"""Finding a Track's Spotify Match among Spotify's search results, by fixed rules.

A Spotify Match is by the same artist and has the same title. A Track without version text needs a result without
any, so covers, remixes, live and sped-up versions are never chosen for an Original. Remaster, radio edit, single,
explicit and similar release labels don't count as version text. When a Track names an edit or remix that isn't on
Spotify, its Original stands in, marked as such.
"""

import re
import unicodedata
from dataclasses import dataclass

from djset.spotify import SpotifyMatch, SpotifyTrack
from djset.tracklist import Track

_BRACKETED = re.compile(r"\(([^()]*)\)|\[([^\[\]]*)\]")
_DASH = re.compile(r"\s+[-–—]\s+")
_FEATURING = re.compile(r"^\s*(?:feat\b|ft\b|featuring\b|with\b|&)", re.IGNORECASE)
_FEATURING_INLINE = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+.*$", re.IGNORECASE)
_ARTIST_SEPARATORS = re.compile(
    r"\s*(?:,|&|\bfeat\b\.?|\bft\b\.?|\bfeaturing\b)\s*|\s+(?:and|x|with|vs\.?)\s+", re.IGNORECASE
)
_RELEASE_LABEL = re.compile(
    r"(?:\d{4} )?(?:digital(?:ly)? )?remaster(?:ed)?(?: \d{4})?(?: version| edition)?"
    r"|(?:radio|single|album|extended|original|main|lp|clean|explicit|dirty|short) (?:edit|version|mix)"
    r"|edit|radio|original|explicit|clean|dirty|mono|stereo|(?:mono|stereo) version|bonus track"
    r"|(?:super )?deluxe(?: edition| version)?|from .+|\d{4}"
)
_VERSION_WORDS = {
    "remix", "rmx", "edit", "bootleg", "mix", "flip", "rework", "refix", "cut", "dub", "vip", "version", "mashup",
    "remake", "blend",
}  # fmt: skip
"""Words that make version text an edit or remix, whose Original can stand in for it."""


@dataclass(frozen=True)
class _Title:
    main: str
    """The title as written, without brackets, " - " parts or featured artists: what to search for."""
    core: str
    """`main`, normalised."""
    whole: str
    """The whole title normalised, without featured artists."""
    version: str
    """Normalised version text, such as "fnky remix"; empty for an Original."""
    version_text: str
    """Version text as written."""


def searches(track: Track) -> list[str]:
    """Searches for a Track, most specific first."""
    title = _title(track.title)
    main = title.main.replace('"', "")
    artist = re.sub(r"^the\s+", "", _first_artist(track.artist), flags=re.IGNORECASE).replace('"', "")
    version = track.version or title.version_text
    queries = [f"{main} {version} {artist}"] if version else []
    queries += [f'track:"{main}" artist:"{artist}"', f"{artist} {main}"]
    return list(dict.fromkeys(queries))


def spotify_match(track: Track, results: list[SpotifyTrack]) -> SpotifyMatch | None:
    """The first result that is the Track, or else, for a Track naming an edit or remix, its Original."""
    wanted = _title(f"{track.title} ({track.version})" if track.version else track.title)
    names = _artist_names(track.artist)
    by_artist = [(result, _title(result.name)) for result in results if names & {_artist(a) for a in result.artists}]
    for result, got in by_artist:
        if got.whole == wanted.whole or (got.core == wanted.core and _versions_agree(wanted.version, got.version)):
            return SpotifyMatch(**result.model_dump())
    if _VERSION_WORDS & set(wanted.version.split()):
        for result, got in by_artist:
            if got.core == wanted.core and not got.version:
                return SpotifyMatch(**result.model_dump(), original=True)
    return None


def _versions_agree(wanted: str, got: str) -> bool:
    """Both are Originals, or every word of the wanted version is in the result's (which may say more)."""
    if not wanted or not got:
        return wanted == got
    return set(wanted.split()) <= set(got.split())


def _title(text: str) -> _Title:
    extras = [_inside(bracket) for bracket in _BRACKETED.finditer(text)]
    main, *after_dashes = _DASH.split(_BRACKETED.sub(" ", text).strip())
    main = " ".join(_FEATURING_INLINE.sub("", main).split())
    versions = [
        part.strip()
        for part in [*extras, *after_dashes]
        if part.strip() and not _FEATURING.match(part) and not _RELEASE_LABEL.fullmatch(_normal(part))
    ]
    without_featuring = _BRACKETED.sub(lambda bracket: " " if _FEATURING.match(_inside(bracket)) else bracket[0], text)
    first, *rest = _DASH.split(without_featuring.strip())
    whole = " ".join([_FEATURING_INLINE.sub("", first), *(part for part in rest if not _FEATURING.match(part))])
    core = _normal(main)
    return _Title(
        main=main or text.strip(),
        core=core or _normal(whole),
        whole=_normal(whole),
        version=_normal(" ".join(versions)),
        version_text=" ".join(versions),
    )


def _inside(bracket: re.Match[str]) -> str:
    return bracket.group(1) or bracket.group(2) or ""


def _artist_names(text: str) -> set[str]:
    """The whole artist credit and each name in it, so "Simon & Garfunkel" and "Chris Brown Feat. Tyga" both match."""
    return {_artist(text)} | {_artist(name) for name in _ARTIST_SEPARATORS.split(text) if name.strip()}


def _first_artist(text: str) -> str:
    return next((name.strip() for name in _ARTIST_SEPARATORS.split(text) if name.strip()), text.strip())


def _artist(name: str) -> str:
    return _normal(name).removeprefix("the ")


def _normal(text: str) -> str:
    """Lowercase, without accents or punctuation, with "&" as "and"."""
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    text = re.sub(r"['’.]", "", text.casefold().replace("&", " and "))
    return " ".join(re.sub(r"[\W_]+", " ", text).split())
