"""Build a route-aware dataset manifest over prepared segments without copying images."""
import argparse
import json
from pathlib import Path


def assemble(sources, output):
  manifests = [json.loads((path/'manifest.json').read_text()) for path in sources]
  if not manifests:
    raise ValueError('At least one prepared segment is required')
  reference = manifests[0]
  for manifest in manifests:
    for key in ('canvas_size', 'camera_size', 'frame_skip', 'motion_labels'):
      if manifest[key] != reference[key]:
        raise ValueError(f'Segment {key} differs')
    if manifest.get('frame_period', .05) != reference.get('frame_period', .05):
      raise ValueError('Segment frame periods differ')
  if output.exists():
    raise ValueError('Use a new dataset directory')
  output.mkdir(parents=True)
  merged = {**reference, 'route_id': output.name, 'route_ids': sorted({m['route_id'] for m in manifests}),
            'samples': sum(m['samples'] for m in manifests), 'sources': [str(p.resolve()) for p in sources]}
  merged.pop('capture_sha256', None)
  (output/'manifest.json').write_text(json.dumps(merged, indent=2)+'\n')
  print(json.dumps({'route_ids': merged['route_ids'], 'samples': merged['samples']}, indent=2))


if __name__ == '__main__':
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--sources', type=Path, nargs='+', required=True)
  parser.add_argument('--output', type=Path, required=True)
  args = parser.parse_args()
  assemble(args.sources, args.output)
