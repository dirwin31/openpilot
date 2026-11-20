import pytest

from openpilot.starpilot.galaxy import phone_projection
from openpilot.starpilot.system.phone_projection import settings


class Reader:
  def __init__(self, status):
    self.status = status

  def read(self):
    return self.status


def test_snapshot_off_by_default(tmp_path):
  value = phone_projection.snapshot(Reader({}), root=tmp_path)
  assert value["enabled"] is False and value["state"] == "off" and value["side"] == "right"


def test_save_turns_on_and_picks_side_and_audio(tmp_path):
  value = phone_projection.save({"enabled": True, "side": "left", "audio": "music"}, root=tmp_path)
  assert (value["enabled"], value["side"], value["audio"]) == (True, "left", "music")
  assert settings.enabled(tmp_path)
  phone_projection.save({"enabled": False}, root=tmp_path)
  assert not settings.enabled(tmp_path) and settings.load(tmp_path).side == "left"


@pytest.mark.parametrize("payload", [{}, {"side": "top"}, {"audio": "car"}, {"enabled": "yes"}, []])
def test_save_rejects_bad_values(tmp_path, payload):
  with pytest.raises(ValueError):
    phone_projection.save(payload, root=tmp_path)


def test_snapshot_reports_the_active_phone(tmp_path):
  phone_projection.save({"enabled": True}, root=tmp_path)
  value = phone_projection.snapshot(Reader({"state": "active", "kind": "carplay", "phone": "iPhone"}), root=tmp_path)
  assert (value["state"], value["kind"], value["phone"]) == ("active", "carplay", "iPhone")


def test_snapshot_passes_each_phone_types_setup_state(tmp_path):
  phone_projection.save({"enabled": True}, root=tmp_path)
  status = {"state": "waiting", "carplay": {"state": "unavailable", "message": "Add the CarPlay key."},
            "android": {"state": "bogus", "message": "x"}}
  value = phone_projection.snapshot(Reader(status), root=tmp_path)
  assert value["carplay"] == {"state": "unavailable", "message": "Add the CarPlay key."}
  assert value["android"] == {"state": "", "message": ""}
  off = phone_projection.snapshot(Reader(status), root=tmp_path / "off")
  assert off["carplay"] == {"state": "", "message": ""}
