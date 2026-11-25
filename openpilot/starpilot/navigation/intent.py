"""Route advice admitted through existing lateral and longitudinal owners."""

import math

from openpilot.cereal import log
from openpilot.common.constants import CV

from openpilot.starpilot.navigation.wire import PHONE_CONTROL_AGE_NS, PHONE_PASSED_SLACK_M, is_phone_navigation, navigation_state
from openpilot.starpilot.navigation.slowdown import DEFAULT_SLOWDOWN_PERCENT, valid_slowdown_percent
from openpilot.starpilot.lateral.signal_turn import SignalTurnEvidence

SERVICE = 'starpilotNavigation'
MAX_AGE_NS = 2_500_000_000
NAVIGATION_BRAKE_MAGNITUDE = 1.0
TARGET_MPH = {'uturn': 5., 'sharpLeft': 10., 'sharpRight': 10., 'left': 14., 'right': 14.}
LEFT, RIGHT = ('left', 'sharpLeft'), ('right', 'sharpRight')
STOP_LEFT_MPS = 3.  # faster than this after a stop, a new stop request is a new approach


def current_instruction(sm, now_ns: int):
  try:
    nav, device = navigation_state(sm[SERVICE]), sm['deviceState']
    stamp = sm.logMonoTime[SERVICE]
    device_stamp = sm.logMonoTime['deviceState']
    if (not sm.valid[SERVICE] or not sm.alive[SERVICE] or not nav.enabled or not nav.controlValid or
        nav.status != 'guiding' or not nav.sessionId or not nav.revision or
        not 0 < nav.frameMonoTime <= stamp <= now_ns <= nav.frameMonoTime + MAX_AGE_NS or
        not sm.valid['deviceState'] or not sm.alive['deviceState'] or not device.started or
        not 0 < device.startedMonoTime == nav.startedMonoTime <= device_stamp <= now_ns <= device_stamp + 2_000_000_000 or
        not nav.startedMonoTime <= nav.locationMonoTime <= now_ns <= nav.locationMonoTime +
            (PHONE_CONTROL_AGE_NS if is_phone_navigation(nav) else MAX_AGE_NS)):
      return None
    for service in ('carState', 'carControl'):
      source = sm.logMonoTime[service]
      if not sm.valid[service] or not sm.alive[service] or not nav.startedMonoTime <= source <= now_ns <= source + 100_000_000:
        return None
    cs = sm['carState']
    if not cs.canValid or cs.canTimeout or str(cs.gearShifter) != 'drive':
      return None
    return nav
  except (KeyError, AttributeError, TypeError):
    return None


def instruction_distance(nav, instruction, v_ego: float, now_ns: int, *, advance: bool = False) -> float:
  # Phone guidance advances from its fix; asked to, routed guidance advances from its publish (about 1 Hz).
  distance = instruction.distanceMeters
  phone = is_phone_navigation(nav)
  if (phone or advance) and math.isfinite(distance) and math.isfinite(v_ego):
    driven = max(0., v_ego) * max(0., now_ns - (nav.locationMonoTime if phone else nav.frameMonoTime)) * 1e-9
    # Well past the reported turn with no newer notification: the guidance no longer applies.
    return math.inf if phone and driven > distance + PHONE_PASSED_SLACK_M else max(0., distance - driven)
  return distance


def within_turn_window(speed, distance):
  return math.isfinite(speed) and speed >= 0 and math.isfinite(distance) and 0 <= distance <= min(90., max(35., speed * 6.))


def turn_side(nav, instruction, cs) -> str:
  """The side the driver is signalling, when it is the instructed one. An arrow-only phone turn is confirmed
  at its junction but carries no side, so there the signal alone chooses it."""
  if instruction.maneuverType != 'turn' or cs.leftBlinker == cs.rightBlinker:
    return ''
  side = 'left' if cs.leftBlinker else 'right'
  modifier = instruction.maneuverModifier
  return side if modifier in (LEFT if cs.leftBlinker else RIGHT) or (not modifier and is_phone_navigation(nav)) else ''


def turn_desire(sm, now_ns: int, current_desire, *, supported: bool):
  if not supported or current_desire != log.Desire.none:
    return current_desire
  nav = current_instruction(sm, now_ns)
  if nav is None:
    return current_desire
  cs, cc, instruction = sm['carState'], sm['carControl'], nav.instruction
  distance = instruction_distance(nav, instruction, cs.vEgo, now_ns, advance=True)
  if (not cc.latActive or cs.standstill or cs.vEgo >= 14 or not within_turn_window(cs.vEgo, distance) or
      instruction.maneuverType != 'turn'):
    return current_desire
  side = turn_side(nav, instruction, cs)
  if side == 'left' and not cs.leftBlindspot:
    return log.Desire.turnLeft
  if side == 'right' and not cs.rightBlindspot:
    return log.Desire.turnRight
  return current_desire


def turn_target(maneuver, min_steer_speed: float) -> float | None:
  kind, modifier = maneuver.maneuverType, maneuver.maneuverModifier
  target = TARGET_MPH.get(modifier or 'left') if kind == 'turn' else None
  if modifier == 'uturn' or 'uturn' in kind or 'u-turn' in kind:
    target = 5.
  elif 'roundabout' in kind or 'rotary' in kind:
    target = 12.
  if target is None:
    return None
  return max(target * CV.MPH_TO_MS, min_steer_speed)


def approach_profile(maneuver) -> tuple[float, float]:
  ordinary = maneuver.maneuverType == 'turn' and maneuver.maneuverModifier in ('left', 'right')
  return (.75, 20.) if ordinary else (.45, 8.)


def turn_speed(maneuver, min_steer_speed: float, slowdown_percent: int = DEFAULT_SLOWDOWN_PERCENT, *,
               traveled: float = 0., distance: float | None = None) -> float | None:
  target = turn_target(maneuver, min_steer_speed)
  distance = maneuver.distanceMeters if distance is None else distance
  if target is None or not math.isfinite(distance) or distance < 0:
    return None
  approach_decel, settling_distance = approach_profile(maneuver)
  if valid_slowdown_percent(slowdown_percent) and slowdown_percent != DEFAULT_SLOWDOWN_PERCENT:
    # Distance scale is .25 at 0%, 1 at 50%, and 2 at 100%. Keep the
    # existing braking envelope and preserve the turn-speed settling buffer.
    scale = 1.0 + (slowdown_percent - DEFAULT_SLOWDOWN_PERCENT) * (.015 if slowdown_percent < DEFAULT_SLOWDOWN_PERCENT else .02)
    approach_decel = min(approach_decel / scale, NAVIGATION_BRAKE_MAGNITUDE)
  return math.sqrt(target * target + 2 * approach_decel * max(0., distance - traveled - settling_distance))


def cruise_ceiling(sm, CP, now_ns: int, cruise_mps: float, *, slowdown_percent: int = DEFAULT_SLOWDOWN_PERCENT) -> float | None:
  nav = current_instruction(sm, now_ns)
  if nav is None:
    return None
  cs, cc = sm['carState'], sm['carControl']
  if (not CP.openpilotLongitudinalControl or CP.passive or CP.dashcamOnly or not cc.longActive or
      cs.gasPressed or cs.brakePressed or not math.isfinite(cruise_mps) or cruise_mps <= 0):
    return None
  minimum = float(CP.minSteerSpeed)
  if not math.isfinite(minimum) or not math.isfinite(cs.vEgo) or cs.vEgo < 0:
    return None
  phone = is_phone_navigation(nav)
  # Route distance is measured at the last location fix; phone distances advance (and expire) in instruction_distance.
  traveled = 0. if phone else cs.vEgo * ((now_ns - nav.locationMonoTime) / 1e9)
  ceiling = None
  for instruction in (nav.instruction, nav.nextManeuver):
    distance = instruction_distance(nav, instruction, cs.vEgo, now_ns) if phone else None
    target = turn_speed(instruction, max(minimum, 0.), slowdown_percent, traveled=traveled, distance=distance)
    if target is not None and target + .25 < cruise_mps and (ceiling is None or target < ceiling):
      ceiling = target
  return ceiling


def matching_turn_signal(sm, now_ns: int, *, supported: bool) -> bool:
  nav = current_instruction(sm, now_ns) if supported else None
  if nav is None:
    return False
  cs, instruction = sm['carState'], nav.instruction
  distance = instruction_distance(nav, instruction, cs.vEgo, now_ns, advance=True)
  return bool(turn_side(nav, instruction, cs) and within_turn_window(cs.vEgo, distance))


def in_signal_range(distance: float, v_ego: float) -> bool:
  return bool(math.isfinite(v_ego) and math.isfinite(distance) and
              0 <= distance <= min(250., max(30., 15. + max(0., v_ego) * 12.)))


def turn_cue(sm, CP, now_ns: int) -> str:
  """Display only: what route advice is doing or waiting for. Model turn support and stop holds are not visible here."""
  nav = current_instruction(sm, now_ns)
  if nav is None:
    return ''
  try:
    cs, cc, instruction = sm['carState'], sm['carControl'], nav.instruction
    distance = instruction_distance(nav, instruction, cs.vEgo, now_ns, advance=True)
    if instruction.maneuverType == 'turn' and cc.latActive and not cs.standstill and in_signal_range(distance, cs.vEgo):
      side = turn_side(nav, instruction, cs)
      if side:
        if cs.leftBlindspot if side == 'left' else cs.rightBlindspot:
          return 'blindspot'
        return 'assist' if turn_desire(sm, now_ns, log.Desire.none, supported=True) != log.Desire.none else 'ready'
      modifier = instruction.maneuverModifier
      if cs.leftBlinker == cs.rightBlinker and (modifier in LEFT + RIGHT or (not modifier and is_phone_navigation(nav))):
        return 'signalLeft' if modifier in LEFT else 'signalRight' if modifier in RIGHT else 'signal'
    # The planner applies the ceiling only while its own longitudinal control is engaged.
    if (sm['selfdriveState'].enabled and str(sm['controlsState'].longControlState) != 'off' and math.isfinite(cs.vEgo) and
        cruise_ceiling(sm, CP, now_ns, max(0., cs.vEgo) + .25) is not None):
      return 'slowing'
  except (KeyError, AttributeError, TypeError, ValueError):
    pass
  return ''


class TurnIntent:
  def __init__(self, params=None, cp=None):
    from openpilot.starpilot.lateral.auto_lane_change import steering_capable
    from openpilot.starpilot.lateral.turn_initiation_preferences import TurnInitiationPreference
    self.guidance = TurnInitiationPreference(params) if params is not None else None
    self.steering_capable = steering_capable(cp)
    self.signal_model = SignalTurnEvidence()
    self.automatic_turn_enabled = False
    self.signal_stop_hold = False
    self.signal_stop_completed = False
    self.key = None
    self.stop_hold = False
    self.stop_completed = False

  def select(self, sm, now_ns: int, current_desire, *, supported: bool, model_stop: bool, waiting_for_lane: bool = False):
    try:
      cs = sm['carState']
      signaling = bool(cs.leftBlinker or cs.rightBlinker)
      preferred = self.guidance.sample(now_ns, signaling=signaling,
                                       maneuver_active=current_desire != log.Desire.none) if self.guidance is not None else False
    except (AttributeError, KeyError, TypeError):
      preferred = False
    self.automatic_turn_enabled = bool(preferred and supported and self.steering_capable)
    if not self.automatic_turn_enabled:
      self.key = None
      self.stop_hold = self.signal_stop_hold = self.signal_stop_completed = False
      return current_desire
    nav = current_instruction(sm, now_ns)
    key = (nav.sessionId, nav.startedMonoTime) if nav is not None else None
    if key != self.key:
      self.key, self.stop_hold, self.stop_completed = key, False, False
    if nav is None:
      return self.signal_desire(sm, now_ns, current_desire, supported=supported, model_stop=model_stop)
    cs = sm['carState']
    try:
      stamp, plan = sm.logMonoTime['longitudinalPlan'], sm['longitudinalPlan']
      plan_valid = (sm.valid['longitudinalPlan'] and sm.alive['longitudinalPlan'] and
                    nav.startedMonoTime <= stamp <= now_ns <= stamp + 150_000_000 and
                    nav.startedMonoTime <= plan.modelMonoTime <= stamp)
    except (KeyError, AttributeError, TypeError):
      plan_valid = False
    if cs.standstill:
      self.stop_hold, self.stop_completed = False, True
    elif cs.leftBlinker == cs.rightBlinker:
      self.stop_hold = False
    elif model_stop or (plan_valid and plan.shouldStop):
      self.stop_hold = True
    elif self.stop_completed:
      # Pulling away, the stop request lags standstill and flickers; it holds only until it clears.
      self.stop_hold = False
    if self.stop_completed and not self.stop_hold and not cs.standstill and cs.vEgo > STOP_LEFT_MPS:
      self.stop_completed = False
    if self.stop_hold or not plan_valid:
      return current_desire
    if waiting_for_lane and matching_turn_signal(sm, now_ns, supported=supported):
      return current_desire
    selected = turn_desire(sm, now_ns, current_desire, supported=supported)
    if selected != log.Desire.none:
      return selected
    return self.signal_desire(sm, now_ns, current_desire, supported=supported, model_stop=model_stop)

  def signal_desire(self, sm, now_ns, current_desire, *, supported, model_stop):
    if self.guidance is None:
      return current_desire
    try:
      cs, cc, device, plan = (sm[name] for name in ('carState', 'carControl', 'deviceState', 'longitudinalPlan'))
      signaling = bool(cs.leftBlinker or cs.rightBlinker)
      if (not supported or not self.steering_capable or not self.automatic_turn_enabled or
          current_desire != log.Desire.none or not cc.latActive or
          not cs.canValid or cs.canTimeout or str(cs.gearShifter) != 'drive' or
          not math.isfinite(cs.vEgo) or (cs.vEgo >= 20 * CV.MPH_TO_MS and
            (cs.steeringPressed or not self.signal_model.current(sm, now_ns))) or
          not device.started):
        return current_desire
      for service, age in (('carState', 100_000_000), ('carControl', 100_000_000),
                           ('longitudinalPlan', 150_000_000), ('deviceState', 2_000_000_000)):
        stamp = sm.logMonoTime[service]
        if (not sm.valid[service] or not sm.alive[service] or
            not 0 < device.startedMonoTime <= stamp <= now_ns <= stamp + age):
          return current_desire
      if not device.startedMonoTime <= plan.modelMonoTime <= sm.logMonoTime['longitudinalPlan']:
        return current_desire
      if not signaling:
        self.signal_stop_hold = self.signal_stop_completed = False
      elif cs.standstill:
        self.signal_stop_hold = False
        self.signal_stop_completed = True
      elif model_stop or plan.shouldStop:
        self.signal_stop_hold = True
      elif self.signal_stop_completed:
        # A completed stop may release after its final planner sample clears.
        self.signal_stop_hold = self.signal_stop_completed = False
      if self.signal_stop_hold or cs.standstill or cs.vEgo <= 0.1 or cs.leftBlinker == cs.rightBlinker:
        return current_desire
      if cs.leftBlinker and not cs.leftBlindspot:
        return log.Desire.turnLeft
      if cs.rightBlinker and not cs.rightBlindspot:
        return log.Desire.turnRight
    except (KeyError, AttributeError, TypeError, ValueError):
      pass
    return current_desire
