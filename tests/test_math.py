"""Тесты чистых функций: разбор энкодеров и отображение значений."""

from __future__ import annotations

import pytest

from photocraftmidi.main import decode_relative, inverse_map, map_absolute


@pytest.mark.parametrize(
    ("value", "expected"),
    [(65, 1), (63, -1), (67, 5), (69, 5), (61, -5), (59, -5), (64, 0), (0, -5), (127, 5)],
)
def test_decode_relative2(value: int, expected: int) -> None:
    assert decode_relative(value, "relative2") == expected


@pytest.mark.parametrize("scheme", ["relative1", "relative3"])
def test_decode_relative_other_schemes(scheme: str) -> None:
    assert decode_relative(1, scheme) == 1
    assert decode_relative(64, scheme) == 0


def test_decode_relative_unknown_scheme() -> None:
    assert decode_relative(65, "nope") == 0


def test_map_absolute_linear() -> None:
    assert map_absolute(0, -180, 180, "linear") == pytest.approx(-180)
    assert map_absolute(127, -180, 180, "linear") == pytest.approx(180)
    assert map_absolute(64, 0, 1, "linear") == pytest.approx(64 / 127)


def test_map_absolute_log() -> None:
    assert map_absolute(0, 0.1, 16, "log") == pytest.approx(0.1)
    assert map_absolute(127, 0.1, 16, "log") == pytest.approx(16)


def test_map_absolute_log_requires_positive_min() -> None:
    with pytest.raises(ValueError, match="log"):
        map_absolute(64, 0.0, 16, "log")


@pytest.mark.parametrize("raw", [0, 1, 32, 64, 96, 126, 127])
def test_inverse_map_roundtrip_log(raw: int) -> None:
    value = map_absolute(raw, 0.1, 16, "log")
    assert round(inverse_map(value, 0.1, 16, "log")) == raw


def test_inverse_map_clamps() -> None:
    assert inverse_map(-999, 0, 1, "linear") == 0
    assert inverse_map(999, 0, 1, "linear") == 127
