import pytest

from cli.profiles import delete_profile, list_profiles, load_profile, save_profile


@pytest.fixture(autouse=True)
def _mock_profiles_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("cli.profiles._PROFILES_DIR", tmp_path / "profiles")


def test_save_and_load_profile():
    config = {
        "analysts": ["market", "news"],
        "research_depth": 3,
        "llm_provider": "openai",
        "output_language": "Chinese",
    }
    path = save_profile("my_profile", config)
    assert path.exists()
    loaded = load_profile("my_profile")
    assert loaded["name"] == "my_profile"
    assert loaded["config"] == config


def test_load_profile_not_found():
    with pytest.raises(FileNotFoundError):
        load_profile("nonexistent")


def test_list_profiles():
    save_profile("alpha", {"llm_provider": "openai"})
    save_profile("beta", {"llm_provider": "anthropic"})
    names = list_profiles()
    assert sorted(names) == ["alpha", "beta"]


def test_delete_profile():
    save_profile("to_delete", {"llm_provider": "openai"})
    delete_profile("to_delete")
    assert "to_delete" not in list_profiles()
