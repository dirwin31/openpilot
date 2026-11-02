"""Short, time-correct MDv3 research clips from local logs and camera files.

No camera-clock inference from a decoder FPS estimate. Encoded presentation
indexes select frames; timestamps and separate action delays come from logs.
"""
from pathlib import Path

import numpy as np

from openpilot.tools.full_frame.local_log import read_events,log_schema
from openpilot.tools.full_frame.capture import event_contract
from openpilot.tools.full_frame.calibration import nominal_warp
from openpilot.tools.full_frame.trajectory import rotation
from openpilot.tools.full_frame.action_targets import aligned_future,action_proxy
from openpilot.tools.full_frame.geometry import unpack_nv12
from openpilot.tools.full_frame.mdv3_sequence import camera_inputs,traffic_convention

RECORDING_COMMIT = 'cbd6e1eb52e8a46f9837a750d675b877d5ac8762'


def asof(rows,query,max_age):
  rows = np.asarray(rows,dtype=np.float64)
  query = np.asarray(query,dtype=np.float64)
  if not len(rows):
    raise ValueError('Required signal stream is absent')
  ids = np.searchsorted(rows[:,0],query,side='right')-1
  safe = np.maximum(ids,0)
  return rows[safe,1:],(ids>=0)&(query-rows[safe,0]<=max_age)


def build_clip(root,*,schema_root=None,start=45.,end=78.):
  root = Path(root)
  if not 0<=start<end:
    raise ValueError('Invalid clip interval')
  names = event_contract(log_schema(schema_root).Event.schema.fields)
  if names['pose']!='deviceMotion':
    raise ValueError('This MDv3 clip builder currently requires the current sensor-timestamp schema')
  frames=[];wide={};car=[];control=[];calib=[];pose=[];lateral=[];rhd=[]
  source=set();routes=set();device=sensor=None;long_delay=None;brand=None
  for path in sorted(root.glob('*/rlog.zst'),key=lambda p:int(p.parent.name.rsplit('--',1)[1])):
    segment = path.parent.name
    routes.add(segment.rsplit('--',1)[0])
    for msg in read_events(path,schema_root):
      kind = msg.which();t=msg.logMonoTime/1e9
      if kind=='initData':source.add(str(msg.initData.gitCommit))
      elif kind=='deviceState':device=str(msg.deviceState.deviceType)
      elif kind==names['road_state']:sensor=str(getattr(msg,kind).sensor)
      elif kind=='carParams':
        brand=str(msg.carParams.brand);long_delay=float(msg.carParams.longitudinalActuatorDelay)+.3+.075
      elif kind=='carState':
        x=msg.carState
        car.append((t,float(x.vEgo),bool(msg.valid and x.canValid and not x.canTimeout),
                    str(x.gearShifter)=='drive',bool(x.cruiseState.enabled)))
      elif kind=='carControl':
        x=msg.carControl;control.append((t,bool(msg.valid),bool(x.latActive),bool(x.longActive)))
      elif kind=='driverMonitoringState':rhd.append((t,bool(msg.driverMonitoringState.isRHD)))
      elif kind=='lateralDelay':lateral.append((t,float(msg.lateralDelay.lateralDelay)+.075))
      elif kind==names['calibration']:
        x=getattr(msg,kind)
        if len(x.rpyCalib)==3:
          calib.append((t,*x.rpyCalib,bool(msg.valid and str(x.calStatus)=='calibrated')))
      elif kind==names['pose']:
        x=getattr(msg,kind)
        if not x.timestamp:continue
        measurements=(x.velocityDevice,x.angularVelocityDevice,x.accelerationDevice)
        values=[v for measurement in measurements for v in (measurement.x,measurement.y,measurement.z)]
        valid=bool(msg.valid and x.inputsOK and x.sensorsOK and x.posenetOK and all(m.valid for m in measurements)
                   and np.isfinite(values).all())
        pose.append((x.timestamp/1e9,*np.nan_to_num(values,nan=0.,posinf=0.,neginf=0.),valid))
      elif kind in (names['road_index'],'wideRoadEncodeIdx') and msg.valid:
        x=getattr(msg,kind)
        if not x.timestampSof:continue
        record=(segment,int(x.frameId),int(x.segmentId),x.timestampSof/1e9)
        if kind==names['road_index']:frames.append(record)
        else:
          key=(segment,int(x.frameId))
          if key in wide:raise ValueError('Duplicate wide camera frame index')
          wide[key]=record
  if source!={RECORDING_COMMIT} or len(routes)!=1 or brand!='toyota' or long_delay is None:
    raise ValueError('Clip context currently audited for this Dom revision and Toyota manual-route timing only')
  frames.sort(key=lambda x:x[3]);origin=frames[0][3]
  frames=[f for f in frames if start<=f[3]-origin<end]
  if not frames:raise ValueError('Empty clip')
  for rows in (car,control,calib,lateral,rhd):rows.sort(key=lambda row:row[0])
  car=np.asarray(car);control=np.asarray(control);calib=np.asarray(calib);lateral=np.asarray(lateral);rhd=np.asarray(rhd)
  pose=np.array(sorted({row[0]:row for row in pose}.values()),dtype=np.float64)
  pt=pose[:,0];pc,pok=asof(calib,pt,1.);cs,cok=asof(car,pt,.1);cc,ook=asof(control,pt,.1)
  eligible=pose[:,-1].astype(bool)&pok&cok&ook&pc[:,3].astype(bool)&cs[:,1].astype(bool)&cs[:,2].astype(bool)
  eligible &= ~cs[:,3].astype(bool)&cc[:,0].astype(bool)&~cc[:,1].astype(bool)&~cc[:,2].astype(bool)
  matrix=rotation(pc[:,:3]);vectors=pose[:,1:10].reshape(-1,3,3)
  calibrated=np.einsum('nji,nkj->nki',matrix,vectors)
  candidates=[]
  for segment,fid,video_index,t in frames:
    other=wide.get((segment,fid))
    if other is None or abs(t-other[3])>.01:raise ValueError('Missing synchronized camera pair')
    c,cv=asof(car,[t],.1);a,av=asof(control,[t],.1);k,kv=asof(calib,[t],1.)
    delay,dv=asof(lateral,[t],1.);traffic,tv=asof(rhd,[t],1.)
    if not (kv[0] and k[0,3] and cv[0] and av[0] and dv[0] and tv[0]):continue
    if not np.isfinite(delay).all() or not 0<delay[0,0]<2 or not 0<long_delay<2:raise ValueError('Invalid action delays')
    action_t=np.array([[delay[0,0],long_delay]],np.float32)
    current_ok=bool(cv[0] and av[0] and dv[0] and tv[0] and c[0,1] and c[0,2] and not c[0,3]
                    and a[0,0] and not a[0,1] and not a[0,2])
    lat=aligned_future(pt,calibrated,pose[:,-1].astype(bool),eligible,t,float(action_t[0,0]))
    lon=aligned_future(pt,calibrated,pose[:,-1].astype(bool),eligible,t,float(action_t[0,1]))
    target=np.zeros((1,2),np.float32);mask=np.zeros((1,2),bool)
    if current_ok and lat is not None and lon is not None:
      target,mask=action_proxy(max(0.,float(c[0,0])),lat[0],lat[1],float(lon[2,0]))
    candidates.append({'segment':segment,'frame_id':fid,'road_index':video_index,'wide_index':other[2],
                       'timestamp':t,'route_seconds':t-origin,'rpy':k[0,:3],
                       'road_transform':nominal_warp(k[0,:3],device,sensor),
                       'wide_transform':nominal_warp(k[0,:3],device,sensor,True),
                       'action_t':action_t,'traffic_convention':traffic_convention(bool(traffic[0,0])),
                       'target':target,'mask':mask})
  return {'route_id':next(iter(routes)),'recorded_commit':next(iter(source)),
          'camera_size':(1344,760),'frames':candidates,'scope':'Kinematic imitation proxy; nominal baseline camera reference; one development route, no independent evaluation'}


def decoded_frames(root,clip):
  """Yield native 20 Hz input pairs with bounded memory, preserving decoder indexes."""
  import imageio_ffmpeg
  readers={};positions={};meta={}
  try:
    for record in clip['frames']:
      raw=[]
      for camera,index in (('fcamera.hevc',record['road_index']),('ecamera.hevc',record['wide_index'])):
        key=(record['segment'],camera)
        if key not in readers:
          # Close the prior minute's streams to bound ffmpeg processes/memory.
          for old in list(readers):
            if old[0]!=record['segment']:readers.pop(old).close();positions.pop(old);meta.pop(old)
          reader=imageio_ffmpeg.read_frames(str(Path(root)/record['segment']/camera),pix_fmt='nv12',bits_per_pixel=12,
                                            input_params=['-r','20','-threads','1'],output_params=['-fps_mode','passthrough'])
          information=next(reader)
          if tuple(information['size'])!=clip['camera_size']:raise ValueError('Video dimensions differ from audited camera contract')
          readers[key]=reader;positions[key]=-1;meta[key]=information
        if index<=positions[key]:raise ValueError('Video presentation indexes must increase')
        while positions[key]<index:
          frame=next(readers[key]);positions[key]+=1
        raw.append(unpack_nv12(np.frombuffer(frame,np.uint8),*clip['camera_size']))
      inputs=camera_inputs(*raw,record['road_transform'],record['wide_transform'])
      yield record,inputs
  finally:
    for reader in readers.values():reader.close()
