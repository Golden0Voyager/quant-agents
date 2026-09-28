import json
from pathlib import Path

_PROFILES_DIR = Path(__file__).parent.parent / "profiles"


def _ensure_dir() -> Path:
    _PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    return _PROFILES_DIR


def _profile_path(name: str) -> Path:
    """Resolve a profile name to a path inside the profiles directory.

    Profile names are free-form user input (CLI prompts / --profile flags), so
    they must never escape the profiles directory: path separators, ``..``
    traversal, and absolute/tilde paths are rejected. A single trailing
    ``.json`` is normalized away so ``foo`` and ``foo.json`` address the same
    profile (previously ``foo.json`` produced ``foo.json.json`` while
    ``list_profiles()`` reported it as ``foo.json``).
    """
    directory = _ensure_dir().resolve()
    base = name.strip()
    if base.lower().endswith(".json"):
        base = base[:-5]
    if not base:
        raise ValueError("Profile name must not be empty")
    if "/" in base or "\\" in base or ".." in base or base.startswith(("~", ".")):
        raise ValueError(
            f"Invalid profile name {name!r}: separators, traversal and hidden names are not allowed"
        )
    path = (directory / f"{base}.json").resolve()
    if path.parent != directory:
        # Defense in depth — should be unreachable after the checks above.
        raise ValueError(f"Profile name {name!r} resolves outside the profiles directory")
    return path


def save_profile(name: str, config: dict) -> Path:
    """Save a profile to disk. Returns the file path."""
    path = _profile_path(name)
    payload = {
        "name": name,
        "created_at": __import__("datetime").datetime.now().isoformat(),
        "config": config,
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return path


def load_profile(name: str) -> dict:
    """Load a profile by name.

    Raises:
        FileNotFoundError: if the profile does not exist.
        ValueError: if the profile file is unreadable or not valid JSON.
    """
    path = _profile_path(name)
    if not path.exists():
        raise FileNotFoundError(f"Profile '{name}' not found at {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Profile '{name}' is unreadable or corrupted: {exc}") from exc


def list_profiles() -> list[str]:
    """Return a list of saved profile names."""
    directory = _ensure_dir()
    return sorted([p.stem for p in directory.glob("*.json") if p.is_file()])


def delete_profile(name: str) -> bool:
    """Delete a profile by name. Returns True if a file was removed."""
    path = _profile_path(name)
    if path.exists():
        path.unlink()
        return True
    return False
