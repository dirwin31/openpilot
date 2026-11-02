"""Validate the uploaded Mountain Dew graph report and trace driving dependencies."""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import pickle

from openpilot.tools.full_frame.mdv3_adapter import SOURCE_SHA256


class SliceReader(pickle.Unpickler):
  def find_class(self, module, name):
    if (module, name) == ('builtins', 'slice'):
      return slice
    raise ValueError('Only slice metadata is supported')


def slices(encoded, output_size):
  if len(encoded) > 100000:
    raise ValueError('Unexpected metadata length')
  value = SliceReader(io.BytesIO(base64.b64decode(encoded))).load()
  if not isinstance(value, dict):
    raise ValueError('Expected a slice map')
  result = {}
  for name, section in value.items():
    if (not isinstance(name,str) or not isinstance(section,slice) or section.step not in (None,1) or
        type(section.start) is not int or type(section.stop) is not int or
        not 0 <= section.start <= section.stop <= output_size):
      raise ValueError('Invalid output slice')
    result[name] = [section.start,section.stop]
  return result


def analyze(report):
  if report.get('verified_sha256') != SOURCE_SHA256 or report.get('model_id') != 'mountaindewv3':
    raise ValueError('Report does not identify the verified Mountain Dew v3 source')
  inputs = {v['name']:v for v in report['inputs']}
  outputs = {v['name']:v for v in report['outputs']}
  if inputs['new_img']['shape'] != [2,6,128,256] or outputs['outputs']['shape'] != [1,18452]:
    raise ValueError('Unexpected audited model contract')
  sections = slices(report['metadata']['output_slices']['encoded_value'],18452)
  producers = {out:n for n in report['nodes'] for out in n['outputs']}
  def upstream(value):
    pending, visited = [value], set()
    while pending:
      name = pending.pop()
      if name in visited:
        continue
      visited.add(name)
      pending.extend(producers.get(name,{}).get('inputs',[]))
    return visited
  action = upstream('cat_4')
  memory = upstream('next_state_feat_q')
  state_bytes = {}
  import math
  for name,next_name in report['state_pairs_by_name'].items():
    spec = inputs[name]
    if spec['shape'] != outputs[next_name]['shape'] or spec['dtype'] != outputs[next_name]['dtype']:
      raise ValueError('State input/output contract mismatch')
    state_bytes[name] = math.prod(spec['shape'])*{'UINT8':1,'FLOAT':4}[spec['dtype']]
  return {'model_id':'mountaindewv3','source_sha256':SOURCE_SHA256,
          'checkpoint':report['metadata']['model_checkpoint']['value'],
          'camera_canvas':[512,256],'camera_packed_input':inputs['new_img'],
          'output_slices':sections,'recurrent_state_bytes':state_bytes,
          'recurrent_state_total_bytes':sum(state_bytes.values()),
          'initializer_elements':report['initializer_elements'],
          'feature_tap':'view_39','feature_reaches_action_head':'view_39' in action,
          'feature_reaches_next_memory':'view_39' in memory,
          'new_img_reaches_action_head':'new_img' in action,
          'past_features_reach_action_head':'state_feat_q' in action,
          'dom_v16_action':'Parsed action means: lateral acceleration / max(1,vEgo)^2 -> curvature; longitudinal acceleration -> acceleration, followed by runtime smoothing',
          'training_requirement':'Train and validate action output and recurrent rollout; plan-only loss is insufficient for the Dom v16 action contract',
          'proposed_full_view_branch':'Preserve calibrated new_img path; encode two uncropped letterboxed full_img views into a zero-initialized feature residual at view_39',
          'scope':'Static graph report analysis, not native execution or driving validation',
          'full_view_adapted_on_actual_weights':False,'modeld_ready':False}


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('report',type=Path)
  parser.add_argument('--output',type=Path,required=True)
  args = parser.parse_args()
  if args.output.exists():
    parser.error('Choose a new output path')
  result = analyze(json.loads(args.report.read_text()))
  result['uploaded_report_sha256'] = hashlib.sha256(args.report.read_bytes()).hexdigest()
  args.output.write_text(json.dumps(result,indent=2)+'\n')
  print(json.dumps(result,indent=2))


if __name__ == '__main__':
  main()
