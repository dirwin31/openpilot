#!/usr/bin/env python3
"""Verify an ONNX against a catalog digest and inspect its graph without executing it."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

MOUNTAIN_DEW_V3 = {
  'id': 'mountaindewv3',
  'source_sha256': {'mountaindewv3_driving_supercombo.onnx':
                   '1cfd2c45353d876b5b257b24d77537de58ab90a44ae595f15b1f64b6ea3fd770'},
}
SOURCE_URL = ('https://huggingface.co/buckets/StarPilot-Driving/StarPilot-Resources/resolve/'
              'onnx/mountaindewv3/mountaindewv3_driving_supercombo.onnx')
SOURCE_BYTES = 792542056


def file_digest(path):
  digest = hashlib.sha256()
  with Path(path).open('rb') as stream:
    for block in iter(lambda: stream.read(4*1024*1024), b''):
      digest.update(block)
  return digest.hexdigest()


def download_source(path):
  path = Path(path)
  if path.exists():
    print('Source already exists; will verify its digest before inspection.', flush=True)
    return
  part = path.with_name(path.name+'.part')
  expected = next(iter(MOUNTAIN_DEW_V3['source_sha256'].values()))
  count, last, digest = 0, 0, hashlib.sha256()
  print('Downloading Mountain Dew v3 ONNX (792.5 MB)...', flush=True)
  with urlopen(SOURCE_URL, timeout=60) as response, part.open('wb') as output:
    while block := response.read(4*1024*1024):
      output.write(block)
      digest.update(block)
      count += len(block)
      if count-last >= 64*1024*1024:
        print(f'{count/1e6:.0f} / {SOURCE_BYTES/1e6:.0f} MB', flush=True)
        last = count
  if count != SOURCE_BYTES or digest.hexdigest() != expected:
    raise ValueError('Downloaded source size or SHA-256 does not match; partial file was not published')
  part.replace(path)
  print('Source SHA-256 verified.', flush=True)


def inspect(path, row):
  import onnx
  path = Path(path)
  expected = row.get('source_sha256', {})
  if not isinstance(expected, dict) or len(expected) != 1:
    raise ValueError('Need one unambiguous catalog source digest')
  name, digest = next(iter(expected.items()))
  actual = file_digest(path)
  if actual != digest:
    raise ValueError(f'Source SHA-256 mismatch: expected {digest}, got {actual}')
  # Do not execute the graph, unpickle metadata, or resolve external tensor paths.
  model = onnx.load(path, load_external_data=False)
  external = [t.name for t in model.graph.initializer if t.data_location == onnx.TensorProto.EXTERNAL]
  def contract(value):
    tensor = value.type.tensor_type
    return {'name': value.name, 'dtype': onnx.TensorProto.DataType.Name(tensor.elem_type),
            'shape': [d.dim_param if d.dim_param else d.dim_value for d in tensor.shape.dim]}
  initialized = {t.name for t in model.graph.initializer}
  metadata = {p.key: p.value for p in model.metadata_props}
  inputs = [contract(v) for v in model.graph.input if v.name not in initialized]
  outputs = [contract(v) for v in model.graph.output]
  names = {v['name'] for v in outputs}
  result = {'model_id': row['id'], 'source_filename_in_catalog': name, 'verified_sha256': actual,
            'bytes': path.stat().st_size, 'inputs': inputs, 'outputs': outputs,
            'state_pairs_by_name': {v['name']: 'next_'+v['name'] for v in inputs if 'next_'+v['name'] in names},
            'opsets': {v.domain: v.version for v in model.opset_import},
            'operators': dict(Counter((n.domain+'::' if n.domain else '')+n.op_type for n in model.graph.node)),
            'initializer_elements': sum(__import__('math').prod(t.dims) for t in model.graph.initializer),
            'initializers': [{'name': t.name, 'shape': list(t.dims),
                              'dtype': onnx.TensorProto.DataType.Name(t.data_type)} for t in model.graph.initializer],
            'nodes': [{'name': n.name, 'op': n.op_type, 'domain': n.domain,
                       'inputs': list(n.input), 'outputs': list(n.output)} for n in model.graph.node],
            'external_tensors': external,
            'metadata': {k: {'characters': len(v), 'value': v if k in ('model_checkpoint', 'model_version') else None,
                             'encoded_value': v if len(v) <= 100000 else None}
                         for k,v in metadata.items()},
            'scope': 'Graph inventory only; source availability and shape do not establish differentiability or driving quality',
            'full_view_adapted': False, 'production_ready': False}
  return result


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('source', type=Path)
  parser.add_argument('--catalog', type=Path, help='Optional catalog; otherwise use the verified Mountain Dew v3 identity')
  parser.add_argument('--model', default='mountaindewv3')
  parser.add_argument('--download', action='store_true', help='Download the pinned Mountain Dew v3 source if absent (792.5 MB)')
  parser.add_argument('--output', type=Path, required=True)
  args = parser.parse_args()
  if args.output.exists():
    parser.error('Choose a new report path')
  rows = json.loads(args.catalog.read_text())['models'] if args.catalog else [MOUNTAIN_DEW_V3]
  matches = [r for r in rows if r.get('id') == args.model]
  if len(matches) != 1:
    parser.error('Catalog must identify the requested model exactly once')
  if args.download:
    if matches[0].get('source_sha256') != MOUNTAIN_DEW_V3['source_sha256']:
      parser.error('Automatic download supports only the verified Mountain Dew v3 identity')
    download_source(args.source)
  result = inspect(args.source, matches[0])
  args.output.write_text(json.dumps(result, indent=2)+'\n')
  print(f'Saved graph report to {args.output}; inputs: {result["inputs"]}; outputs: {result["outputs"]}')


if __name__ == '__main__':
  main()
