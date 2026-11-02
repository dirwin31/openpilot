#!/usr/bin/env python3
"""Collect model identities over ADB without changing comma settings or copying drives.

Runs with Python's standard library on the host. No Chestnut is required.
"""
import argparse
import json
from pathlib import Path
import shlex
import subprocess


def collect(models_root='/data/models/v26'):
  root = Path(models_root)
  report = {'format': 1, 'scope': 'Read-only model inventory; no weights or driving logs', 'errors': {}}
  for name, filename in [('preferences', 'preferences.json'), ('catalog', 'catalog.json')]:
    try:
      value = json.loads((root / filename).read_text())
      if name == 'preferences':
        report[name] = {k: value.get(k) for k in ('small', 'big')}
      else:
        rows = value.get('models', [])
        if not isinstance(rows, list):
          raise ValueError('Expected models list')
        wanted = {'mountaindewv3', *[v for v in report.get('preferences', {}).values() if isinstance(v, str)]}
        report['selected_catalog_rows'] = [row for row in rows if isinstance(row, dict) and row.get('id') in wanted]
        report['mountain_dew_v3_found'] = any(row.get('id') == 'mountaindewv3' for row in report['selected_catalog_rows'])
    except (OSError, ValueError, TypeError) as exc:
      report['errors'][name] = str(exc)
  report['installed_files'] = {}
  for mid in ('mountaindewv3', 'rdf43'):
    directory = root / mid
    if directory.is_dir():
      report['installed_files'][mid] = [{'name': p.name, 'bytes': p.stat().st_size}
                                         for p in sorted(directory.iterdir()) if p.is_file() and not p.is_symlink()]
  return report


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--output', type=Path, default=Path('comma-model-inventory.json'))
  parser.add_argument('--serial', help='ADB device to use if more than one is connected')
  args = parser.parse_args()
  if args.output.exists():
    parser.error('Choose a new output filename')
  # Ship only the collector, not this host entrypoint, to the device interpreter.
  import inspect
  source = 'import json\nfrom pathlib import Path\n' + inspect.getsource(collect) + '\nprint(json.dumps(collect()))\n'
  command = ['adb'] + (['-s', args.serial] if args.serial else [])
  result = subprocess.run(command + ['shell', 'python3', '-c', shlex.quote(source)], capture_output=True, text=True, timeout=45)
  if result.returncode:
    parser.exit(1, 'ADB inventory failed: ' + result.stderr.strip() + '\n')
  try:
    report = json.loads(result.stdout)
  except ValueError:
    parser.exit(1, 'Device did not return JSON; check that adb shell python3 works.\n')
  args.output.write_text(json.dumps(report, indent=2) + '\n')
  print(f'Saved {args.output}; Mountain Dew v3 catalog entry found: {report.get("mountain_dew_v3_found", False)}')


if __name__ == '__main__':
  main()
