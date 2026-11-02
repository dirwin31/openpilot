"""Scenario metrics with explicit limits on what recorded motion can establish."""
import numpy as np

from openpilot.tools.full_frame.trajectory import TIMES


def inward_deviation(predicted, reference):
  """Maximum inward deviation from a curved reference path, not curb clearance.

  Project predicted points onto the reference polyline to reduce speed/timing
  confounding. Return None for straight paths or insufficient interior matches.
  No road boundary or vehicle footprint is inferred from a demonstration.
  """
  reference, predicted = np.asarray(reference), np.asarray(predicted)
  edges = np.diff(reference, axis=0)
  lengths = np.linalg.norm(edges, axis=1)
  usable = lengths > .05
  if usable.sum() < 3:
    return None
  heading = np.unwrap(np.arctan2(edges[usable, 1], edges[usable, 0]))
  turning = heading[-1]-heading[0]
  if abs(turning) < np.deg2rad(15):
    return None
  starts, edges, lengths = reference[:-1][usable], edges[usable], lengths[usable]
  delta = predicted[:, None, :]-starts[None]
  fraction = (delta*edges[None]).sum(axis=-1)/(lengths[None]**2)
  projection = starts[None]+fraction.clip(0, 1)[..., None]*edges[None]
  difference = predicted[:, None, :]-projection
  nearest = np.argmin((difference**2).sum(axis=-1), axis=1)
  valid = (fraction[np.arange(len(predicted)), nearest] > 0) & (fraction[np.arange(len(predicted)), nearest] < 1)
  edge = edges[nearest]/lengths[nearest, None]
  offset = difference[np.arange(len(predicted)), nearest]
  signed = (edge[:, 0]*offset[:, 1]-edge[:, 1]*offset[:, 0])*np.sign(turning)
  if not valid.any():
    return None
  return float(max(0, signed[valid].max()))


def scenario_metrics(predicted, reference, speeds):
  inward = [inward_deviation(p, r) for p, r in zip(predicted, reference)]
  inward = [x for x in inward if x is not None]
  velocity = np.diff(reference, axis=1)/np.diff(TIMES)[None, :, None]
  predicted_velocity = np.diff(predicted, axis=1)/np.diff(TIMES)[None, :, None]
  stops = (np.linalg.norm(velocity, axis=-1) < .5) & (np.asarray(speeds)[:, None] > 2)
  terminal_speed, position_error = [], []
  for i, row in enumerate(stops):
    indices = np.flatnonzero(row)
    if len(indices):
      k = indices[0]
      terminal_speed.append(float(np.linalg.norm(predicted_velocity[i, k])))
      position_error.append(float(np.linalg.norm(predicted[i, k+1]-reference[i, k+1])))
  return {
    'curved_reference_paths': len(inward),
    'mean_max_inward_deviation_from_recorded_path_m': float(np.mean(inward)) if inward else None,
    'recorded_stop_approaches': len(terminal_speed),
    'mean_predicted_speed_at_recorded_stop_mps': float(np.mean(terminal_speed)) if terminal_speed else None,
    'mean_position_error_at_recorded_stop_m': float(np.mean(position_error)) if position_error else None,
    'limits': 'Inward deviation is relative to a demonstration, not a curb or lane. Recorded stops are not stop-sign labels.',
    'corner_boundary_clearance': 'not evaluated: road-boundary and ego-footprint labels required',
    'parked_car_clearance': 'not evaluated: parked-vehicle geometry and ego-footprint labels required',
    'stop_sign_detection': 'not evaluated: sign identity/location/visibility labels required',
    'stop_line_compliance': 'not evaluated: applicable stop-line geometry and event labels required',
  }
