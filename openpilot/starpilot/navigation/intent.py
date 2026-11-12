"""Route advice admitted through existing lateral and longitudinal owners."""

import math

from openpilot.cereal import log
from openpilot.common.constants import CV

from openpilot.starpilot.navigation.wire import PHONE_CONTROL_AGE_NS, PHONE_PASSED_SLACK_M, is_phone_navigation, navigation_state
from openpilot.starpilot.lateral.signal_turn import SignalTurnEvidence

SERVICE = 'starpilotNavigation'
MAX_AGE_NS = 2_500_000_000
NAVIGATION_BRAKE_MAGNITUDE = 1.0
SMOOTH_CEILING_CAR = 'HONDA_CIVIC_2022'
TARGET_MPH = {'uturn': 5., 'sharpLeft': 10., 'sharpRight': 10., 'left': 14., 'right': 14.}
LEFT, RIGHT = ('left', 'sharpLeft'), ('right', 'sharpRight')


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


def instruction_distance(nav, instruction, v_ego: float, now_ns: int) -> float:
  # Phone distances are only as fresh as the last notification, so advance them by the distance driven since.
  distance = instruction.distanceMeters
  if is_phone_navigation(nav) and math.isfinite(distance) and math.isfinite(v_ego):
    driven = max(0., v_ego) * max(0., now_ns - nav.locationMonoTime) * 1e-9
    # Well past the reported turn with no newer notification: the guidance no longer applies.
    return math.inf if driven > distance + PHONE_PASSED_SLACK_M else max(0., distance - driven)
  return distance


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
  distance = instruction_distance(nav, instruction, cs.vEgo, now_ns)
  if (not cc.latActive or cs.standstill or not math.isfinite(cs.vEgo) or not 0 <= cs.vEgo < 14 or
      instruction.maneuverType != 'turn' or not math.isfinite(distance) or
      not 0 <= distance <= min(90., max(35., cs.vEgo * 6.))):
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


def turn_speed(maneuver, min_steer_speed: float, distance: float | None = None) -> float | None:
  target = turn_target(maneuver, min_steer_speed)
  distance = maneuver.distanceMeters if distance is None else distance
  if target is None or not math.isfinite(distance) or distance < 0:
    return None
  approach_decel, settling_distance = approach_profile(maneuver)
  return math.sqrt(target * target + 2 * approach_decel * max(0., distance - settling_distance))


def smooth_turn_ceiling(CP) -> bool:
  # Route distance only refreshes about once a second, which stepped the cap and pulsed the brakes.
  return getattr(CP, 'brand', None) == 'honda' and getattr(CP, 'carFingerprint', None) == SMOOTH_CEILING_CAR


def smoothed_turn_speed(maneuver, min_steer_speed: float, nav, v_ego: float, now_ns: int) -> float | None:
  # Advance the last routed distance by the distance driven since that location fix.
  distance = maneuver.distanceMeters
  if not math.isfinite(distance) or distance < 0 or not math.isfinite(v_ego):
    return None
  if is_phone_navigation(nav) and not math.isfinite(instruction_distance(nav, maneuver, v_ego, now_ns)):
    return None
  distance = max(0., distance - max(0., v_ego) * max(0., now_ns - nav.locationMonoTime) * 1e-9)
  profile = turn_speed(maneuver, min_steer_speed, distance)
  target = turn_target(maneuver, min_steer_speed)
  settling_distance = approach_profile(maneuver)[1]
  if profile is None or target is None or v_ego <= profile or distance - settling_distance <= 1.:
    return profile
  # Above the profile (e.g. after a gas override), brake only as hard as reaching the turn speed requires.
  return max(profile, v_ego - (v_ego * v_ego - target * target) / (2 * (distance - settling_distance)))


def cruise_ceiling(sm, CP, now_ns: int, cruise_mps: float) -> float | None:
  nav = current_instruction(sm, now_ns)
  if nav is None:
    return None
  cs, cc = sm['carState'], sm['carControl']
  if (not CP.openpilotLongitudinalControl or CP.passive or CP.dashcamOnly or not cc.longActive or
      cs.gasPressed or cs.brakePressed or not math.isfinite(cruise_mps) or cruise_mps <= 0):
    return None
  minimum = float(CP.minSteerSpeed)
  if not math.isfinite(minimum):
    return None
  smooth = smooth_turn_ceiling(CP)
  for instruction in (nav.instruction, nav.nextManeuver):
    if smooth:
      target = smoothed_turn_speed(instruction, max(minimum, 0.), nav, cs.vEgo, now_ns)
    else:
      target = turn_speed(instruction, max(minimum, 0.), instruction_distance(nav, instruction, cs.vEgo, now_ns))
    if target is not None and target + .25 < cruise_mps:
      return target
  return None


def matching_turn_signal(sm, now_ns: int, *, supported: bool) -> bool:
  nav = current_instruction(sm, now_ns) if supported else None
  if nav is None:
    return False
  cs, instruction = sm['carState'], nav.instruction
  distance = instruction_distance(nav, instruction, cs.vEgo, now_ns)
  return bool(turn_side(nav, instruction, cs) and math.isfinite(cs.vEgo) and
              math.isfinite(distance) and
              0 <= distance <= min(250., max(30., 15. + max(0., cs.vEgo) * 12.)))


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

  def select(self, sm, now_ns: int, current_desire, *, supported: bool, model_stop: bool):
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
      self.key, self.stop_hold = key, False
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
    if cs.standstill or cs.leftBlinker == cs.rightBlinker:
      self.stop_hold = False
    elif model_stop or (plan_valid and plan.shouldStop):
      self.stop_hold = True
    if self.stop_hold or not plan_valid:
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
