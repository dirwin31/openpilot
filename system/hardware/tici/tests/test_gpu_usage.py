from io import StringIO

import pytest

from openpilot.system.hardware.tici.hardware import Tici


@pytest.mark.parametrize(("sample", "expected"), [
  ("25 100", 25.0),
  ("0 100", 0.0),
  ("0 0", -1),
  ("invalid", -1),
])
def test_gpu_usage_percent_samples(monkeypatch, sample, expected):
  monkeypatch.setattr("builtins.open", lambda *_args, **_kwargs: StringIO(sample))
  assert Tici().get_gpu_usage_percent() == expected


def test_gpu_usage_percent_missing(monkeypatch):
  def missing(*_args, **_kwargs):
    raise FileNotFoundError

  monkeypatch.setattr("builtins.open", missing)
  assert Tici().get_gpu_usage_percent() == -1
