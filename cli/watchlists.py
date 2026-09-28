from pathlib import Path

_WATCHLISTS_DIR = Path(__file__).parent.parent / "watchlists"


def _ensure_dir() -> Path:
    _WATCHLISTS_DIR.mkdir(parents=True, exist_ok=True)
    return _WATCHLISTS_DIR


def parse_watchlist_content(content: str) -> list[str]:
    """Parse watchlist text: one ticker per line, ignore comments and blanks."""
    return [code for code, _ in parse_watchlist_entries(content)]


def parse_watchlist_entries(content: str) -> list[tuple[str, str]]:
    """Parse watchlist text into (code, display_name) pairs.

    Inline comments carry the stock name (``002241  # 歌尔股份``); lines
    without a comment fall back to the bare code as the display name.
    Blank lines and full-line comments are skipped.
    """
    entries: list[tuple[str, str]] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        code, _, comment = stripped.partition("#")
        code = code.strip()
        if not code:
            continue
        display = comment.strip() or code
        entries.append((code, display))
    return entries


def _watchlist_path(name: str) -> Path:
    """Resolve a watchlist name to a path inside the watchlists directory.

    Watchlist names come from CLI flags / interactive prompts, so they must
    not be able to escape the directory via separators or ``..`` traversal.
    """
    directory = _ensure_dir().resolve()
    base = name.strip()
    if base.lower().endswith(".txt"):
        base = base[:-4]
    if not base:
        raise ValueError("Watchlist name must not be empty")
    if "/" in base or "\\" in base or ".." in base or base.startswith(("~", ".")):
        raise ValueError(
            f"Invalid watchlist name {name!r}: separators, traversal and hidden names are not allowed"
        )
    path = (directory / f"{base}.txt").resolve()
    if path.parent != directory:
        raise ValueError(f"Watchlist name {name!r} resolves outside the watchlists directory")
    return path


def save_watchlist(name: str, tickers: list[str]) -> Path:
    """Save a watchlist to disk. Returns the file path."""
    path = _watchlist_path(name)
    lines = "\n".join(tickers) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(lines, encoding="utf-8")
    tmp.replace(path)
    return path


def load_watchlist(name: str) -> list[str]:
    """Load a watchlist by name. Raises FileNotFoundError if missing."""
    return [code for code, _ in load_watchlist_entries(name)]


def load_watchlist_entries(name: str) -> list[tuple[str, str]]:
    """Load a watchlist as (code, display_name) pairs.

    Raises FileNotFoundError if the watchlist file is missing.
    """
    path = _watchlist_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Watchlist '{name}' not found at {path}")
    return parse_watchlist_entries(path.read_text(encoding="utf-8"))


def list_watchlists() -> list[str]:
    """Return a list of saved watchlist names, with 'my' prioritized first."""
    directory = _ensure_dir()
    names = sorted([p.stem for p in directory.glob("*.txt") if p.is_file()])
    if "my" in names:
        names.remove("my")
        names.insert(0, "my")
    return names
