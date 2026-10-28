"""Explicit vehicle contracts for the private steady-state replay fixture."""


def checkpoint_kind(cp):
  from openpilot.starpilot.longitudinal.ioniq6_start import eligible

  if eligible(cp):
    return 'ioniq6'
  # The user's radarless Civic has no diagnostic ECU handoff in CI.init.
  # Keep this narrow until other Honda configurations have replay evidence.
  if (cp.brand == 'honda' and cp.carFingerprint == 'HONDA_CIVIC_2022'
      and cp.openpilotLongitudinalControl and not cp.pcmCruise
      and not cp.passive and not cp.dashcamOnly and not cp.notCar
      and int(cp.flags) == 2060 and int(cp.alternativeExperience) == 32
      and len(cp.safetyConfigs) == 1
      and str(cp.safetyConfigs[0].safetyModel) == 'hondaBosch'
      and int(cp.safetyConfigs[0].safetyParam) == 10):
    return 'honda-civic-2022'
  raise ValueError('Unsupported steady-state CarParams: expected qualified Ioniq 6 or radarless Civic 2022 active-long configuration')


def restored_fields(kind):
  if kind == 'ioniq6':
    return {'ci_initialized': True, 'ioniq6_long_selected': True, 'ioniq6_long_prearmed': True, 'ioniq6_long_pending': False}
  if kind == 'honda-civic-2022':
    return {'ci_initialized': True}
  raise ValueError('Unknown vehicle checkpoint')
