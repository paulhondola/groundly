"""Pages whose VLM output stopped at the token cap, collected by the reader that read
them and drained by the worker after each call, so a truncated page is flagged, not
silently scored as a short one."""

_hit: set[int] = set()


def hit(page: int) -> None:
    _hit.add(page)


def drain() -> set[int]:
    pages = set(_hit)
    _hit.clear()
    return pages
