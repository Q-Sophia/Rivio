from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def canonical_source_url(value: str) -> str:
    """Return a stable source identity without changing content parameters."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw.casefold().rstrip("/")

    scheme = parsed.scheme.casefold()
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if not scheme or not hostname:
        return raw.casefold().rstrip("/")

    try:
        port = parsed.port
    except ValueError:
        return raw.casefold().rstrip("/")
    if port and not (
        (scheme == "http" and port == 80)
        or (scheme == "https" and port == 443)
    ):
        netloc = f"{hostname}:{port}"
    else:
        netloc = hostname

    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(
        sorted(
            (key, item)
            for key, item in parse_qsl(
                parsed.query,
                keep_blank_values=True,
            )
            if not key.casefold().startswith("utm_")
        ),
        doseq=True,
    )
    return urlunsplit((scheme, netloc, path, query, ""))
