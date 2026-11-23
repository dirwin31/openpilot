import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bridge'))
import map_token
from proxy import GalaxyProxy


class MapTokenTest(unittest.TestCase):
  def setUp(self):
    directory = tempfile.TemporaryDirectory()
    self.addCleanup(directory.cleanup)
    self.path = Path(directory.name) / 'offer'
    self.token = 'pk.synthetic.test'

  def test_explicit_one_time_offer(self):
    self.assertIsNone(map_token.take(self.path))
    self.assertTrue(map_token.offer(self.token, self.path))
    with ThreadPoolExecutor(max_workers=4) as workers:
      values = list(workers.map(map_token.take, [self.path] * 4))
    self.assertCountEqual(values, [self.token, None, None, None])

  def test_expiry_and_secret_values(self):
    for token in ('sk.synthetic', 'pk.', 'pk.invalid space', '', None, 'pk.' + 'x' * 2046):
      self.assertFalse(map_token.offer(token, self.path))
    with patch('map_token.time.monotonic', return_value=0):
      map_token.offer(self.token, self.path)
    with patch('map_token.time.monotonic', return_value=120):
      self.assertIsNone(map_token.take(self.path))
    self.assertFalse(self.path.exists())

  def test_only_bridge_get_consumes_offer(self):
    request = {'id': 'test', 'path': '/_bridge/mapbox-public-token', 'method': 'GET'}
    proxy = GalaxyProxy()
    take = map_token.take
    with patch.object(map_token, 'take', side_effect=lambda: take(self.path)):
      self.assertEqual(proxy.handle(request)['status'], 404)
      map_token.offer(self.token, self.path)
      self.assertEqual(proxy.handle(dict(request, method='POST'))['status'], 400)
      reply = proxy.handle(request)
      self.assertEqual(reply['status'], 200)
      self.assertEqual(reply['headers']['cache-control'], 'no-store')
      self.assertEqual(json.loads(base64.b64decode(reply['body'])), {'publicToken': self.token})
      self.assertEqual(proxy.handle(request)['status'], 404)
