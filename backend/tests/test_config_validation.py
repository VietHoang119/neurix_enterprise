"""validate_production_config: fails fast (and names exactly what's missing)
when MODE=production and a required setting is unset or empty; is a no-op
otherwise, so local/dev runs against LOCAL_DATABASE_URL never need these."""

import pytest

from core.config import REQUIRED_IN_PRODUCTION, ConfigError, Settings, validate_production_config

_ALL_SET = {name: "x" for name in REQUIRED_IN_PRODUCTION}

# FRONTEND_URL is a plain `str` with a default (not `str | None` like the
# others), so Pydantic rejects None for it outright — "" is how "missing"
# is represented for that one field; every other field is `str | None`.
_UNSET = {name: ("" if name == "FRONTEND_URL" else None) for name in REQUIRED_IN_PRODUCTION}


def _settings(**overrides) -> Settings:
    return Settings(MODE="production", **{**_ALL_SET, **overrides})


def test_passes_when_every_required_setting_is_present():
    validate_production_config(_settings())  # must not raise


@pytest.mark.parametrize("name", REQUIRED_IN_PRODUCTION)
def test_fails_naming_exactly_the_missing_setting(name):
    with pytest.raises(ConfigError) as exc_info:
        validate_production_config(_settings(**{name: _UNSET[name]}))
    assert name in str(exc_info.value)
    # Only the one missing setting is named, not every required setting.
    for other in REQUIRED_IN_PRODUCTION:
        if other != name:
            assert other not in str(exc_info.value)


@pytest.mark.parametrize("name", REQUIRED_IN_PRODUCTION)
def test_fails_when_a_setting_is_empty_not_just_unset(name):
    with pytest.raises(ConfigError) as exc_info:
        validate_production_config(_settings(**{name: ""}))
    assert name in str(exc_info.value)


def test_names_all_missing_settings_at_once():
    with pytest.raises(ConfigError) as exc_info:
        validate_production_config(_settings(DB_NAME=None, FIREBASE_PROJECT_ID=None))
    message = str(exc_info.value)
    assert "DB_NAME" in message
    assert "FIREBASE_PROJECT_ID" in message


def test_no_op_outside_production_even_with_nothing_set():
    validate_production_config(Settings(MODE="dev"))  # must not raise
    validate_production_config(Settings())  # default MODE ("dev"), must not raise
