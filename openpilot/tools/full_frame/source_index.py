#!/usr/bin/env python3
"""List published StarPilot ONNX source objects; download no weights or device data."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

BUCKET = 'StarPilot-Driving/StarPilot-Resources'


def inventory():
  url = f'https://huggingface.co/api/buckets/{BUCKET}/tree?recursive=true&limit=1000'
  found, visited = [], set()
  while url:
    if url in visited or urlparse(url).hostname != 'huggingface.co' or urlparse(url).scheme != 'https':
      raise ValueError('Unexpected pagination link')
    visited.add(url)
    with urlopen(url, timeout=40) as response:
      rows = json.load(response)
      if not isinstance(rows, list):
        raise ValueError('Expected a bucket object list')
      found.extend(row for row in rows if row.get('type') == 'file' and row.get('path', '').endswith('.onnx'))
      links = response.headers.get('Link', '')
    url = next((part.split('>')[0].strip('< ') for part in links.split(',') if 'rel="next"' in part), '')
  return {'bucket': BUCKET, 'pages': len(visited), 'complete': True, 'onnx_objects': found}


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--output', type=Path, default=Path('comma-source-index.json'))
  args = parser.parse_args()
  if args.output.exists():
    parser.error('Choose a new output file')
  result = inventory()
  args.output.write_text(json.dumps(result, indent=2)+'\n')
  print(f'Saved {len(result["onnx_objects"])} source entries to {args.output}. No model weights were downloaded.')


if __name__ == '__main__':
  main()
