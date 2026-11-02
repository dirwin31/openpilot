"""NumPy reference for Dom's nominal camera warp, independent of ARM extensions.

Constants mirror common/transformations/{camera,model}.py at the pinned Dom ref.
This intentionally supports only the audited current camera configurations.
"""
import numpy as np

from openpilot.tools.full_frame.trajectory import rotation


def nominal_warp(calibration, device, sensor, wide=False):
  if device not in ('tici', 'tizi', 'mici'):
    raise ValueError(f'Unaudited device: {device}')
  if sensor == 'os04c10':
    width, height, focal = 1344, 760, 425.25 if wide else 1141.5
  elif sensor in ('ar0231', 'ox03c10'):
    width, height, focal = 1928, 1208, 567. if wide else 2648.
  else:
    raise ValueError(f'Unaudited camera sensor: {sensor}')
  camera = np.array([[focal, 0, width/2], [0, focal, height/2], [0, 0, 1.]])
  model = np.array([[455. if wide else 910., 0, 256.],
                    [0, 455. if wide else 910., 151.8 if wide else 47.6], [0, 0, 1.]])
  view_from_device = np.array([[0., 1, 0], [0, 0, 1], [1, 0, 0]])
  return camera @ view_from_device @ rotation(calibration)[0] @ np.linalg.inv(model @ view_from_device)
