import pytest

from tvheadend_mcp.config import Settings


@pytest.mark.parametrize(
    "url",
    [
        "",
        "ftp://tvh.test",
        "http://user:secret@tvh.test",
        "http://tvh.test/api",
        "http://tvh.test?password=secret",
        "http://tvh.test/#fragment",
        "http://tvh.test:invalid",
    ],
)
def test_invalid_or_credential_bearing_urls_are_rejected(url):
    with pytest.raises(ValueError):
        Settings(url)


def test_settings_repr_hides_credentials():
    settings = Settings("http://tvh.test", "PRIVATE_USER", "PRIVATE_PASSWORD")
    assert "PRIVATE_USER" not in repr(settings)
    assert "PRIVATE_PASSWORD" not in repr(settings)


def test_environment_is_parsed_and_validated(monkeypatch):
    monkeypatch.setenv("TVH_URL", "http://tvh.test:9981/tvh/")
    monkeypatch.setenv("TVH_AUTH", "BASIC")
    monkeypatch.setenv("TVH_READ_ONLY", "yes")
    monkeypatch.setenv("TVH_VERIFY_SSL", "false")
    settings = Settings.from_env()
    assert settings.url == "http://tvh.test:9981/tvh"
    assert settings.auth == "basic"
    assert settings.read_only is True
    assert settings.verify_ssl is False


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_timeouts_are_rejected(timeout):
    with pytest.raises(ValueError):
        Settings("http://tvh.test", timeout=timeout)
