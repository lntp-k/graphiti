"""Tests for the `server.idle_timeout_seconds` config field and its CLI override."""

import argparse

import pytest
from pydantic import ValidationError

from config.schema import GraphitiConfig, ServerConfig


def test_idle_timeout_seconds_defaults_to_disabled():
    assert ServerConfig().idle_timeout_seconds is None


def test_idle_timeout_seconds_rejects_non_positive_values_at_construction():
    with pytest.raises(ValidationError):
        ServerConfig(idle_timeout_seconds=0)
    with pytest.raises(ValidationError):
        ServerConfig(idle_timeout_seconds=-5)


def test_apply_cli_overrides_sets_idle_timeout_seconds():
    config = GraphitiConfig()
    args = argparse.Namespace(idle_timeout_seconds=900.0)

    config.apply_cli_overrides(args)

    assert config.server.idle_timeout_seconds == 900.0


def test_apply_cli_overrides_leaves_idle_timeout_seconds_untouched_when_absent():
    config = GraphitiConfig()
    config.server.idle_timeout_seconds = 300.0
    args = argparse.Namespace()

    config.apply_cli_overrides(args)

    assert config.server.idle_timeout_seconds == 300.0
