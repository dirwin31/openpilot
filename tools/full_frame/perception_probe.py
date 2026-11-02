"""Offline pretrained object-detector probe, separate from the driving network.

Uses the bundled tinygrad YOLOv8 example architecture with explicit external
safetensor weights. Predictions are proposals, not verified training labels.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np

CLASSES = {2: 'car', 5: 'bus', 7: 'truck', 9: 'traffic light', 11: 'stop sign'}


def nms(boxes, scores, threshold=.45):
  order, keep = np.argsort(scores)[::-1], []
  while len(order):
    index = int(order[0]); keep.append(index)
    rest = order[1:]
    if not len(rest):
      break
    left = np.maximum(boxes[index, :2], boxes[rest, :2])
    right = np.minimum(boxes[index, 2:], boxes[rest, 2:])
    intersection = np.prod(np.maximum(0, right-left), axis=1)
    area = np.prod(np.maximum(0, boxes[index, 2:]-boxes[index, :2]))
    other_area = np.prod(np.maximum(0, boxes[rest, 2:]-boxes[rest, :2]), axis=1)
    iou = intersection/np.maximum(1e-9, area+other_area-intersection)
    order = rest[iou <= threshold]
  return keep


def preprocess(rgb, size=640):
  from PIL import Image
  height, width = rgb.shape[:2]
  scale = min(size/width, size/height)
  resized_w, resized_h = round(width*scale), round(height*scale)
  left, top = (size-resized_w)//2, (size-resized_h)//2
  canvas = np.full((size, size, 3), 114, dtype=np.uint8)
  canvas[top:top+resized_h, left:left+resized_w] = np.array(Image.fromarray(rgb).resize((resized_w, resized_h)))
  return canvas.transpose(2, 0, 1)[None].astype(np.float32)/255, (resized_w/width, resized_h/height, left, top)


def proposals(raw, transform, width, height, confidence=.25):
  rows = raw[0].T
  xywh = rows[:, :4]
  boxes = np.concatenate((xywh[:, :2]-xywh[:, 2:]/2, xywh[:, :2]+xywh[:, 2:]/2), axis=1)
  sx, sy, left, top = transform
  boxes = (boxes-np.array([left, top, left, top]))/np.array([sx, sy, sx, sy])
  boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, width)
  boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, height)
  result = []
  for category, name in CLASSES.items():
    scores = rows[:, 4+category]
    indices = np.flatnonzero(scores >= confidence)
    if not len(indices):
      continue
    for k in nms(boxes[indices], scores[indices]):
      idx = int(indices[k])
      result.append({'class': name, 'class_id': category, 'confidence': float(scores[idx]),
                     'box_xyxy_pixels': boxes[idx].tolist(), 'status': 'unverified detector proposal'})
  return result


def load_detector(weights):
  from tinygrad.nn.state import load_state_dict, safe_load
  root = Path(__file__).resolve().parents[2]
  spec = importlib.util.spec_from_file_location('full_frame_yolo_example', root/'tinygrad_repo/examples/yolov8.py')
  module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
  depth, width, ratio = module.get_variant_multiples('n')
  model = module.YOLOv8(w=width, r=ratio, d=depth, num_classes=80)
  load_state_dict(model, safe_load(str(weights)))
  return model


def main():
  from PIL import Image, ImageDraw
  from tinygrad import Tensor
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--images', type=Path, nargs='+', required=True)
  parser.add_argument('--weights', type=Path, required=True, help='FP32 safetensor weights compatible with the bundled YOLOv8n example')
  parser.add_argument('--output', type=Path, required=True)
  args = parser.parse_args()
  args.output.mkdir(parents=True, exist_ok=False)
  model = load_detector(args.weights)
  output = {'weights_sha256': hashlib.sha256(args.weights.read_bytes()).hexdigest(),
    'scope': 'pretrained perception probe only; not the trained driving model or a parked-car classifier', 'images': []}
  for path in args.images:
    rgb = np.array(Image.open(path).convert('RGB'))
    inputs, transform = preprocess(rgb)
    raw = model.head(model.fpn(*model.net(Tensor(inputs)))).numpy()
    objects = proposals(raw, transform, rgb.shape[1], rgb.shape[0])
    image = Image.fromarray(rgb); draw = ImageDraw.Draw(image)
    for obj in objects:
      color = '#ff4747' if obj['class'] == 'stop sign' else '#26d7ef'
      box = obj['box_xyxy_pixels']
      draw.rectangle(box, outline=color, width=3)
      draw.text((box[0], max(0, box[1]-15)), f"{obj['class']} {obj['confidence']:.2f}", fill=color)
    image.save(args.output/f'{path.stem}-detections.png')
    output['images'].append({'image': str(path), 'objects': objects,
      'max_stop_sign_score_before_threshold': float(raw[0, 15].max())})
    print(json.dumps(output['images'][-1]), flush=True)
  (args.output/'proposals.json').write_text(json.dumps(output, indent=2)+'\n')


if __name__ == '__main__':
  main()
