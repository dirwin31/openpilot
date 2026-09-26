from types import SimpleNamespace

import numpy as np
import pytest

from openpilot.selfdrive.ui.onroad.model_renderer import ModelRenderer


@pytest.fixture
def renderer():
  result = object.__new__(ModelRenderer)
  result._car_space_transform = np.eye(3, dtype=np.float32)
  result._transform_dirty = False
  result._radar_transform_generation = 7
  result._clip_region = SimpleNamespace(x=-500, y=-500, width=2000, height=2000)
  return result


def test_transform_invalidates_only_when_float32_projection_changes(renderer):
  original = renderer._car_space_transform
  renderer.set_transform(original.astype(np.float64))
  assert renderer._car_space_transform is original
  assert not renderer._transform_dirty and renderer._radar_transform_generation == 7
  changed = original.copy()
  changed[0, 0] = 2
  renderer.set_transform(changed)
  assert renderer._transform_dirty and renderer._radar_transform_generation == 8
  renderer.set_transform(changed)
  assert renderer._transform_dirty and renderer._radar_transform_generation == 8
  changed[0, 0] = 3
  assert renderer._car_space_transform[0, 0] == 2


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
@pytest.mark.parametrize("endpoints", [(10, 20), (10, 10), (20, 10)])
@pytest.mark.parametrize("distance", [5, 10, 15, 20, 25])
def test_scalar_endpoint_interpolation_matches_numpy(renderer, dtype, endpoints, distance):
  line = np.array([[0, 0, 2], [endpoints[0], 2, 3], [endpoints[1], 6, 5], [30, 8, 7]], dtype=dtype)
  endpoint = [distance, np.interp(distance, line[1:3, 0], line[1:3, 1]), np.interp(distance, line[1:3, 0], line[1:3, 2])]
  expanded = np.concatenate((line[:2], np.array([endpoint], dtype=dtype)))
  expected = renderer._map_line_to_polygon(expanded, 0.9, 1.2, 2, distance)
  actual = renderer._map_line_to_polygon(line, 0.9, 1.2, 1, distance)
  np.testing.assert_allclose(actual, expected, rtol=1e-6, atol=1e-6)
