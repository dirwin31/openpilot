#!/usr/bin/env python3
"""Verify an ONNX against a catalog digest and inspect its graph without executing it."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


def inspect(path, row):
  import onnx
  path = Path(path)
  expected = row.get('source_sha256', {})
  if not isinstance(expected, dict) or len(expected) != 1:
    raise ValueError('Need one unambiguous catalog source digest')
  name, digest = next(iter(expected.items()))
  with path.open('rb') as stream:
    actual = hashlib.file_digest(stream, 'sha256').hexdigest()
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
            'external_tensors': external,
            'metadata': {k: {'characters': len(v), 'value': v if k in ('model_checkpoint', 'model_version') else None}
                         for k,v in metadata.items()},
            'scope': 'Graph inventory only; source availability and shape do not establish differentiability or driving quality',
            'full_view_adapted': False, 'production_ready': False}
  return result


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('source', type=Path)
  parser.add_argument('--catalog', type=Path, required=True)
  parser.add_argument('--model', default='mountaindewv3')
  parser.add_argument('--output', type=Path, required=True)
  args = parser.parse_args()
  if args.output.exists():
    parser.error('Choose a new report path')
  matches = [r for r in json.loads(args.catalog.read_text())['models'] if r.get('id') == args.model]
  if len(matches) != 1:
    parser.error('Catalog must identify the requested model exactly once')
  result = inspect(args.source, matches[0])
  args.output.write_text(json.dumps(result, indent=2)+'\n')
  print(json.dumps(result, indent=2))


if __name__ == '__main__':
  main()
