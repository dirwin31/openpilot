import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from openpilot.tools.full_frame.assemble import assemble
from openpilot.tools.full_frame.driving import dataset, training_distribution
from openpilot.tools.full_frame.trajectory import TIMES


class TestDatasetProtocol(unittest.TestCase):
  def segment(self, base, name, route):
    p=base/name; p.mkdir()
    manifest={'route_id':route,'samples':1,'motion_labels':'test-motion','canvas_size':[64,32],
              'camera_size':[64,32],'frame_skip':1,'frame_period':.2}
    (p/'manifest.json').write_text(json.dumps(manifest))
    np.savez(p/'000001.npz',target_path=np.stack((TIMES*10,np.zeros(33)),axis=1)[None].astype(np.float32),
             speed=np.array([[10.]],dtype=np.float32))
    return p

  def test_combined_manifest_retains_all_route_ids(self):
    with tempfile.TemporaryDirectory() as temp:
      base=Path(temp);a=self.segment(base,'a','drive-one');b=self.segment(base,'b','drive-two')
      assemble([a,b],base/'combined')
      manifest,samples=dataset(base/'combined')
      self.assertEqual(manifest['route_ids'],['drive-one','drive-two'])
      self.assertEqual(len(samples),2)

  def test_changed_child_geometry_is_rejected(self):
    with tempfile.TemporaryDirectory() as temp:
      base=Path(temp);a=self.segment(base,'a','drive-one');assemble([a],base/'combined')
      p=a/'manifest.json';m=json.loads(p.read_text());m['frame_period']=.05;p.write_text(json.dumps(m))
      with self.assertRaises(ValueError):dataset(base/'combined')

  def test_balanced_sampler_renormalizes_missing_categories(self):
    with tempfile.TemporaryDirectory() as temp:
      p=self.segment(Path(temp),'segment','drive-one')
      probabilities,metadata=training_distribution([p/'000001.npz'],'balanced_motion')
      np.testing.assert_allclose(probabilities,[1.])
      self.assertEqual(metadata['category_counts'],[0,0,0,1])
