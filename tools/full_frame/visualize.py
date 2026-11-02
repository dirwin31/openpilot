"""Render camera footprints and trajectory comparisons from a prepared dataset."""
import argparse
from pathlib import Path

import numpy as np


def rgb(packed):
  _, h, w = packed.shape
  y = np.empty((h*2, w*2), dtype=np.float32)
  for channel, (row, col) in enumerate([(0, 0), (1, 0), (0, 1), (1, 1)]):
    y[row::2, col::2] = packed[channel]
  u, v = [np.repeat(np.repeat(packed[i].astype(np.float32)-128, 2, axis=0), 2, axis=1) for i in [4, 5]]
  y = 1.164*(y-16)
  return np.stack((y+1.596*v, y-.392*u-.813*v, y+2.017*u), axis=-1).clip(0, 255).astype(np.uint8)


def main():
  import matplotlib
  matplotlib.use('Agg')
  import matplotlib.pyplot as plt
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('dataset', type=Path)
  parser.add_argument('--results', type=Path, required=True)
  parser.add_argument('--output', type=Path, required=True)
  parser.add_argument('--title', default='Full-frame trajectory research')
  args = parser.parse_args()
  samples = sorted(args.dataset.glob('[0-9]*.npz'))
  with np.load(samples[len(samples)//2]) as d:
    full, masks = d['full'][0], d['legacy_masks']
  with np.load(args.results) as d:
    pred, target, baseline = d['predictions'], d['target_paths'], d['constant_velocity']
  fig, axes = plt.subplots(2, 2, figsize=(12, 8), layout='constrained')
  for ax, start, frame, name in [(axes[0, 0], 6, 1, 'Road camera'), (axes[0, 1], 18, 3, 'Wide road camera')]:
    ax.imshow(rgb(full[start:start+6]))
    mask = np.repeat(np.repeat(masks[frame], 2, axis=0), 2, axis=1)
    ax.contour(mask, levels=[.5], colors=['#ffcb47'], linewidths=1.5)
    ax.set_title(name+' — full student input')
    ax.axis('off')
  idx = len(pred)//2
  for path, label in [(target[idx], 'Recorded motion'), (pred[idx], 'Full-view model'), (baseline[idx], 'Constant speed')]:
    axes[1, 0].plot(path[:, 1], path[:, 0], label=label)
  axes[1, 0].set(xlabel='Right displacement (m)', ylabel='Forward displacement (m)', title='One held-out example')
  axes[1, 0].legend()
  times = 10*(np.arange(33)/32)**2
  for value, label in [(pred, 'Full-view model'), (baseline, 'Constant speed')]:
    axes[1, 1].plot(times, np.linalg.norm(value-target, axis=-1).mean(axis=0), label=label)
  axes[1, 1].set(xlabel='Prediction horizon (s)', ylabel='Mean displacement error (m)',
                 title=f'Evaluation errors over {len(pred)} overlapping examples')
  axes[1, 1].legend()
  fig.suptitle(args.title+' — yellow outline: nominal bundled-model footprint', fontsize=13)
  fig.savefig(args.output, dpi=150)
  plt.close(fig)


if __name__ == '__main__':
  main()
