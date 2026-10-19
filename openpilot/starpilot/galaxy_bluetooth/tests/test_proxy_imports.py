"""Keep UI package imports independent of the standalone bridge search path."""
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[4]
BRIDGE = ROOT / 'openpilot/starpilot/galaxy_bluetooth/bridge'


class ProxyImportTest(unittest.TestCase):
  def run_import(self, directory, source):
    # Other bridge tests add this directory to sys.path, which hides package-import failures.
    environment = dict(os.environ)
    environment.pop('PYTHONPATH', None)
    result = subprocess.run([sys.executable, '-c', source], cwd=directory, env=environment,
                            capture_output=True, text=True, timeout=15)
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

  def test_pairing_screen_package_import(self):
    self.run_import(ROOT, "from openpilot.starpilot.galaxy_bluetooth.bridge.proxy import GalaxyProxy; GalaxyProxy()")

  def test_standalone_bridge_import(self):
    self.run_import(BRIDGE, "from proxy import GalaxyProxy; GalaxyProxy()")


if __name__ == '__main__':
  unittest.main()
