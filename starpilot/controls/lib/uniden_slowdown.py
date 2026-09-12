"""Optional cruise speed ceiling from fresh Uniden alerts; never commands actuators."""
import math
import time

from openpilot.starpilot.system.bluetooth.uniden import STATE_KEY, SLOWDOWN_KEY, load_config

MAX_ALERT_AGE = 3.0


class UnidenSlowdown:
  def __init__(self, params, memory):
    self.params = params
    self.memory = memory
    self.gas_override = False
    self._last_feedback = None
    self._last_feedback_at = 0.0

  def update(self, *, enabled, longitudinal, gas_pressed, brake_pressed, cruise, posted_limit, source, now=None):
    now = time.monotonic() if now is None else now
    config = load_config(self.params)
    try:
      state = self.memory.get(STATE_KEY) or {}
    except (ValueError, TypeError):
      state = {}
    target, reason = 0.0, "Disabled"
    threat = False
    try:
      age = now - float(state.get("observed_at", float("nan")))
      fresh = (state.get("connected") is True and state.get("address") == config["address"] and
               math.isfinite(age) and 0 <= age <= MAX_ALERT_AGE)
      if config["enabled"] and config["auto_slowdown"]:
        reason = "Waiting for fresh detector data"
        if fresh:
          threat = any(alert["band"] in config["bands"] and
                       config["min_strength"] <= alert["strength"] <= 8 and
                       (not config["ignore_muted"] or alert["muted"] is False) for alert in state.get("alerts", []))
          reason = "No qualifying alert"
    except (AttributeError, TypeError, ValueError, KeyError):
      threat = False
      reason = "Invalid detector data"
    if not threat:
      self.gas_override = False
    elif gas_pressed:
      self.gas_override = True
    if threat:
      if not enabled or not longitudinal or brake_pressed:
        reason = "Longitudinal control inactive"
      elif self.gas_override:
        reason = "Gas override until alert clears"
      elif (source in (None, "", "None") or not math.isfinite(posted_limit) or not 5 / 3.6 <= posted_limit <= 160 / 3.6):
        reason = "No valid posted speed limit"
      elif not math.isfinite(cruise) or cruise <= 0:
        reason = "Cruise speed unavailable"
      else:
        target = min(cruise, posted_limit)
        reason = "Slowing to posted limit" if target < cruise else "Already below posted limit"
    feedback = {"active": target > 0 and target < cruise, "target_mps": target, "reason": reason, "gas_override": self.gas_override}
    if feedback != self._last_feedback or now - self._last_feedback_at >= 1:
      self.memory.put_nonblocking(SLOWDOWN_KEY, {**feedback, "observed_at": now})
      self._last_feedback = feedback
      self._last_feedback_at = now
    return target
