import json
from pathlib import Path

_PROFILES_DIR = Path(__file__).parent.parent / "profiles"


def _ensure_dir() -> Path:
    _PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    return _PROFILES_DIR


def save_profile(name: str, config: dict) -> Path:
    """Save a profile to disk. Returns the file path."""
    directory = _ensure_dir()
    path = directory / f"{name}.json"
    payload = {
        "name": name,
        "created_at": __import__("datetime").datetime.now().isoformat(),
        "config": config,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_profile(name: str) -> dict:
    """Load a profile by name. Raises FileNotFoundError if missing."""
    path = _ensure_dir() / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"Profile '{name}' not found at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def list_profiles() -> list[str]:
    """Return a list of saved profile names."""
    directory = _ensure_dir()
    return sorted([p.stem for p in directory.glob("*.json")])


def delete_profile(name: str) -> None:
    """Delete a profile by name."""
    path = _ensure_dir() / f"{name}.json"
    if path.exists():
        path.unlink()
