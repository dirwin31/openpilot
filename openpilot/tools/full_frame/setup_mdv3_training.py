#!/usr/bin/env python3
"""Prepare local archived route 4b and run an offline MDv3 action-loss check.

Downloads only research code, matching schemas and small dependencies. Model
weights and raw recordings come from the files already in Downloads. Nothing
is installed on a comma and no trained model is selected for driving.
"""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
from urllib.request import urlopen

CODE='https://raw.githubusercontent.com/natehuby76/StarPilotBT/research/full-frame-dom-current/openpilot/tools/full_frame'
SOURCE='https://raw.githubusercontent.com/StarPilotHQ/StarPilot/cbd6e1eb52e8a46f9837a750d675b877d5ac8762'
ROUTE='0000004b--855ea3ca69'
FILES=['local_log.py','capture.py','geometry.py','trajectory.py','calibration.py','action_targets.py',
       'mdv3_sequence.py','mdv3_training.py','mdv3_data.py','mdv3_fit.py','mdv3_adapter.py']
ARCHIVES={'comma-manual-rlogs.tar':(52111360,'a9a7bc4c96dbebc7e29d4a91fd556a46044c41e40756c21a85ddbf1a16ef4692'),
          'comma-manual-cameras.tar':(404357120,'71ef169240c05faa008f9987c7c357a7f7aa7f4736fa93809a10805d616555d3')}


def digest(path):
  h=hashlib.sha256()
  with Path(path).open('rb') as stream:
    for b in iter(lambda:stream.read(4*1024*1024),b''):h.update(b)
  return h.hexdigest()


def local_archive(downloads,work,name,size,sha):
  path=downloads/name
  if path.is_file() and path.stat().st_size==size and digest(path)==sha:return path
  parts=sorted(downloads.glob(name+'.part-*'))
  if not parts:raise RuntimeError(f'Missing valid {name} and split parts in Downloads')
  path=work/name
  with path.open('wb') as out:
    for part in parts:
      with part.open('rb') as src:shutil.copyfileobj(src,out,4*1024*1024)
  if path.stat().st_size!=size or digest(path)!=sha:raise ValueError(f'Archive checksum mismatch: {name}')
  return path


def extract(archive,segments):
  with tarfile.open(archive) as tar:
    for member in tar.getmembers():
      path=Path(member.name)
      if (not member.isfile() or len(path.parts)!=2 or path.parent.name not in {f'{ROUTE}--{i}'for i in range(6)} or
          path.name not in ('rlog.zst','fcamera.hevc','ecamera.hevc')):
        raise ValueError(f'Unexpected archive member: {member.name}')
      target=segments/path
      if not target.resolve().is_relative_to(segments.resolve()):raise ValueError('Unsafe extraction path')
      target.parent.mkdir(parents=True,exist_ok=True)
      with tar.extractfile(member) as src,target.open('wb') as out:shutil.copyfileobj(src,out,4*1024*1024)


def fetch(url,path):
  path.parent.mkdir(parents=True,exist_ok=True)
  with urlopen(url,timeout=40) as response:path.write_bytes(response.read())


def main():
  parser=argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--device',default='AUTO')
  parser.add_argument('--steps',type=int,default=20)
  parser.add_argument('--prepare-only',action='store_true',help='Validate archives and download code without model execution')
  args=parser.parse_args()
  downloads=Path.home()/'Downloads';python=downloads/'comma-mdv3-env/bin/python'
  if not python.is_file():parser.error('Run setup_mdv3_mac.py first to create the verified tinygrad environment')
  source=downloads/'mountaindewv3_driving_supercombo.onnx';graph=downloads/'mdv3-full-view-research.onnx'
  if not source.is_file() or not graph.is_file():parser.error('The original and verified research ONNX files must remain in Downloads')
  subprocess.check_call([str(python),'-m','pip','install','pycapnp==2.2.4','zstandard==0.25.0','imageio-ffmpeg==0.8.0'])
  check=('import hashlib,pathlib,tinygrad.nn.onnx as r; '
         'assert hashlib.sha256(pathlib.Path(r.__file__).read_bytes()).hexdigest()=='
         '"4ff1b2096d44c1dd3638193153664b5d0d8ac4fa1f8c9195e90c2f81ac61d7ca"')
  subprocess.check_call([str(python),'-c',check])
  work=downloads/'comma-mdv3-training-work';work.mkdir(exist_ok=True)
  segments=work/'segments';segments.mkdir(exist_ok=True)
  for name,(size,sha) in ARCHIVES.items():
    print(f'Checking and extracting {name}...',flush=True)
    extract(local_archive(downloads,work,name,size,sha),segments)
  code=work/'research-code';package=code/'openpilot/tools/full_frame'
  for path in (code/'openpilot',code/'openpilot/tools',package):path.mkdir(parents=True,exist_ok=True);(path/'__init__.py').write_text('')
  for name in FILES:fetch(CODE+'/'+name,package/name)
  schemas=work/'schemas'
  for name in ('log.capnp','custom.capnp','deprecated.capnp','include/c++.capnp'):
    fetch(SOURCE+'/openpilot/cereal/'+name,schemas/name)
  fetch(SOURCE+'/opendbc_repo/opendbc/car/car.capnp',schemas/'car.capnp')
  hashes={name:digest(package/name)for name in FILES}
  (work/'code-provenance.json').write_text(json.dumps({'code_url':CODE,'sha256':hashes,'schema_commit':'cbd6e1eb52e8a46f9837a750d675b877d5ac8762'},indent=2)+'\n')
  if args.prepare_only:
    print(f'Prepared local data and code at {work}; model execution was not requested',flush=True);return
  output=work/('run-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
  env=dict(os.environ,PYTHONPATH=str(code),OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')
  command=[str(python),'-u','-m','openpilot.tools.full_frame.mdv3_fit','--source',str(source),'--graph',str(graph),
           '--segments',str(segments),'--schema-root',str(schemas),'--output',str(output),'--device',args.device,'--steps',str(args.steps)]
  print('Starting the real-weight, one-route overfit check. This can take several minutes.',flush=True)
  log_path=output.with_suffix('.terminal.txt')
  with log_path.open('w') as log:
    process=subprocess.Popen(command,env=env,cwd=work,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
    for line in process.stdout:
      print(line,end='',flush=True);log.write(line);log.flush()
    returncode=process.wait()
  report=output/'training-report.json'
  if not report.is_file():
    output.mkdir(parents=True,exist_ok=True)
    report.write_text(json.dumps({'completed':False,'returncode':returncode,'modeld_ready':False,
                                 'error':'Training did not complete; inspect terminal_log_tail',
                                 'terminal_log_tail':log_path.read_text()[-8000:]},indent=2)+'\n')
  result=json.loads(report.read_text());result['process_returncode']=returncode
  result['completed']=returncode==0 and result.get('trained_adapter_checkpoint_saved') is True
  result['code_sha256']=hashes
  report.write_text(json.dumps(result,indent=2)+'\n')
  destination=downloads/'mdv3-manual-training-report.json';shutil.copyfile(report,destination)
  print(f'Upload {destination}',flush=True)
  if platform_is_mac():subprocess.run(['open','-R',str(destination)],check=False)
  if returncode:raise SystemExit(returncode)


def platform_is_mac():
  import sys
  return sys.platform=='darwin'


if __name__=='__main__':
  try:
    main()
  except Exception as error:
    destination=Path.home()/'Downloads/mdv3-manual-training-report.json'
    destination.write_text(json.dumps({'completed':False,'modeld_ready':False,
                                      'error':f'{type(error).__name__}: {error}'},indent=2)+'\n')
    print(f'Setup failed. Upload {destination}',flush=True)
    raise

