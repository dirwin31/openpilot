#!/usr/bin/env python3
"""Repair an untrained research graph's feature buffers and gate publication on replay.

The original source stays unchanged. The existing research graph is replaced
only after its saved candidate passes the original native parity tolerances.
"""
import argparse
import gc
import hashlib
import json
from pathlib import Path
import shutil


def publish_candidate(candidate, graph, verification):
  if (verification.get('comparison_executed') is True and
      verification.get('all_outputs_and_states_match') is True):
    Path(candidate).replace(graph)
    return True
  Path(candidate).unlink(missing_ok=True)
  return False


def main():
  import onnx
  import numpy as np
  from onnx import helper,numpy_helper
  from mdv3_adapter import SOURCE_SHA256,buffer_feature_fusion,verify_native
  parser = argparse.ArgumentParser(description=__doc__)
  downloads = Path.home()/'Downloads'
  parser.add_argument('--source',type=Path,default=downloads/'mountaindewv3_driving_supercombo.onnx')
  parser.add_argument('--graph',type=Path,default=downloads/'mdv3-full-view-research.onnx')
  parser.add_argument('--device',default='AUTO')
  args = parser.parse_args()
  contract_path = args.graph.with_suffix('.contract.json')
  contract = json.loads(contract_path.read_text())
  if (contract.get('original_source_sha256')!=SOURCE_SHA256 or contract.get('trained') is not False or
      contract.get('modeld_ready') is not False):
    parser.error('Repair supports only the identified untrained research graph')
  print('Verifying original source hash...',flush=True)
  digest = hashlib.sha256()
  with args.source.open('rb') as stream:
    for block in iter(lambda:stream.read(4*1024*1024),b''):digest.update(block)
  if digest.hexdigest()!=SOURCE_SHA256:
    parser.error('Source hash differs from the audited Mountain Dew v3')
  if shutil.disk_usage(args.graph.parent).free < args.graph.stat().st_size+64*1024*1024:
    parser.error('Need about 0.9 GB free for the temporary candidate')
  candidate = args.graph.with_name(args.graph.stem+'.buffered-candidate.onnx')
  if candidate.exists():
    parser.error(f'Temporary candidate already exists: {candidate}')
  print('Adding explicit FP16 feature buffers to the research graph...',flush=True)
  model = onnx.load(args.graph,load_external_data=False)
  if any(t.data_location==onnx.TensorProto.EXTERNAL for t in model.graph.initializer):
    parser.error('External tensor graphs are not supported')
  tensors = {t.name:t for t in model.graph.initializer}
  for name in ('ff_projection_weight','ff_projection_bias'):
    if name not in tensors or np.any(numpy_helper.to_array(tensors[name]) != 0):
      parser.error('Projection has changed; this repair supports zero-initialized research graphs only')
  buffer_feature_fusion(model)
  contract.update({'feature_fusion_revision':2,
                   'feature_fusion':'three explicit tinygrad Contiguous buffers around FP16 addition',
                   'native_output_parity_verified':False})
  props = {p.key:p.value for p in model.metadata_props}
  props['full_view_research_contract'] = json.dumps(contract)
  helper.set_model_props(model,props)
  onnx.checker.check_model(model)
  print('Writing and replaying the saved candidate...',flush=True)
  onnx.save(model,candidate)
  del model,tensors
  gc.collect()
  try:
    verification = verify_native(args.source,candidate,args.device)
  except Exception as exc:
    import traceback
    verification = {'comparison_executed':False,'all_outputs_and_states_match':False,
                    'error':f'{type(exc).__name__}: {exc}','traceback':traceback.format_exc(),'modeld_ready':False}
  accepted = publish_candidate(candidate,args.graph,verification)
  verification.update({'buffer_repair_accepted':accepted,'candidate_feature_fusion_revision':2,
                       'trained':False,'modeld_ready':False})
  parity = args.graph.with_suffix('.parity.json')
  backup = args.graph.with_suffix('.parity.before-buffer-repair.json')
  if parity.exists() and not backup.exists():
    shutil.copyfile(parity,backup)
  parity.write_text(json.dumps(verification,indent=2)+'\n')
  if accepted:
    contract['native_output_parity_verified'] = True
    contract_path.write_text(json.dumps(contract,indent=2)+'\n')
    print('Saved candidate passed parity; research graph updated. It remains untrained.',flush=True)
  else:
    print('Candidate failed parity; existing graph preserved and temporary candidate removed.',flush=True)
  print(f'Upload {parity}',flush=True)


if __name__ == '__main__':
  main()
