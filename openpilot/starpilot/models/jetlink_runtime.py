"""modeld scheduling and engagement inputs for the pinned Jetlink interface."""
import os

from openpilot.starpilot.system.jetlink.lifecycle import selected


def chestnut_available(params, *, present: bool, cable) -> bool:
  # A Jetlink iPad also asserts the Type-C orientation bit. Only an identified
  # Chestnut may select local AMD inference when external inference is requested.
  return present or (not selected(params) and cable())


def configure_gpu(params, *, device_type: str, chestnut=False, recovery=False) -> bool:
  """Run before the first QCOM context opens; smaller KGSL priorities run first."""
  if device_type not in ('tizi', 'mici') or chestnut or recovery or not selected(params):
    return False
  os.environ['QCOM_PRIORITY'] = '11'
  return True


def in_control(sm) -> bool:
  # Unknown or stale controls must never permit a model swap. Read modeld's
  # existing frame snapshot rather than starting another subscription thread.
  return (not sm.all_checks(['selfdriveState', 'carControl']) or
          sm['selfdriveState'].enabled or sm['selfdriveState'].active or
          sm['carControl'].latActive or sm['carControl'].longActive)
