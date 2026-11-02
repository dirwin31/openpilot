#!/usr/bin/env python3
"""Set up an isolated Mac runtime and retry the existing offline MDv3 graph.

No model download, graph rebuild, SSH connection or vehicle configuration.
Uses an installed Python >=3.11, or Homebrew Python 3.12 if needed.
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.request import urlopen

REVISION = '9d0446a4ba8a532c8b674fb6ad795af015cd9dcf'
RUNNER_SHA256 = '4ff1b2096d44c1dd3638193153664b5d0d8ac4fa1f8c9195e90c2f81ac61d7ca'
SCRIPT_URL = ('https://raw.githubusercontent.com/natehuby76/StarPilotBT/'
              'research/full-frame-dom-current/openpilot/tools/full_frame/mdv3_adapter.py')


def supported_python(candidate):
  if not candidate or not Path(candidate).is_file():
    return False
  result = subprocess.run([str(candidate),'-c','import sys; raise SystemExit(sys.version_info < (3,11))'],
                          stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  return result.returncode == 0


def choose_python():
  candidates = [shutil.which('python3.12'), '/opt/homebrew/opt/python@3.12/bin/python3.12',
                '/usr/local/opt/python@3.12/bin/python3.12', sys.executable,
                shutil.which('python3.13'),shutil.which('python3.11')]
  for candidate in candidates:
    if supported_python(candidate):
      return str(candidate)
  brew = shutil.which('brew')
  if not brew:
    for candidate in ('/opt/homebrew/bin/brew','/usr/local/bin/brew'):
      if Path(candidate).is_file():
        brew = candidate
        break
  if not brew:
    raise RuntimeError('Need Python 3.11+; no supported interpreter or Homebrew was found')
  print('Installing Python 3.12 with Homebrew for this runtime...',flush=True)
  subprocess.check_call([brew,'install','python@3.12'])
  prefix = subprocess.check_output([brew,'--prefix','python@3.12'],text=True).strip()
  candidate = str(Path(prefix)/'bin/python3.12')
  if not supported_python(candidate):
    raise RuntimeError('Python 3.12 was not available after installation')
  return candidate


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  downloads = Path.home()/'Downloads'
  parser.add_argument('--source',type=Path,default=downloads/'mountaindewv3_driving_supercombo.onnx')
  parser.add_argument('--graph',type=Path,default=downloads/'mdv3-full-view-research.onnx')
  parser.add_argument('--device',default='AUTO')
  args = parser.parse_args()
  for path in (args.source,args.graph,args.graph.with_suffix('.contract.json')):
    if not path.is_file():
      parser.error(f'Required existing file missing: {path}')
  env = downloads/'comma-mdv3-env'
  python = env/'bin/python'
  if not python.is_file():
    host = choose_python()
    print(f'Creating isolated runtime at {env} using {host}',flush=True)
    subprocess.check_call([host,'-m','venv',str(env)])
  if not supported_python(python):
    parser.error(f'{env} uses an older Python; choose a fresh environment before proceeding')
  print('Checking isolated Python runtime...',flush=True)
  subprocess.check_call([str(python),'-V'])
  check = ('import hashlib, pathlib; import tinygrad.nn.onnx as runner; import onnx, numpy; '
           f'assert hashlib.sha256(pathlib.Path(runner.__file__).read_bytes()).hexdigest() == {RUNNER_SHA256!r}; '
           'print("Pinned tinygrad ONNX runner verified")')
  ready = subprocess.run([str(python),'-c',check],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode == 0
  if not ready:
    subprocess.check_call([str(python),'-m','pip','install','--upgrade','pip'])
    subprocess.check_call([str(python),'-m','pip','install','onnx',
                           f'https://github.com/tinygrad/tinygrad/archive/{REVISION}.zip'])
  subprocess.check_call([str(python),'-c',check])
  adapter = downloads/'mdv3_adapter.py'
  with urlopen(SCRIPT_URL,timeout=40) as response:
    code = response.read()
  adapter.write_bytes(code)
  print('Retrying native comparison on the existing graph...',flush=True)
  subprocess.check_call([str(python),str(adapter),str(args.source),'--output',str(args.graph),
                          '--verify-only','--verify-device',args.device])
  print(f'Upload {args.graph.with_suffix(".parity.json")}',flush=True)


if __name__ == '__main__':
  main()
