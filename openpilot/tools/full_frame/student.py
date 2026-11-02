"""Small spatial encoder for full-view feature distillation, using bundled tinygrad."""
from tinygrad import Tensor, nn


class FullFrameEncoder:
  def __init__(self, height=288, width=512, output_dim=512, channels=16):
    if height % 32 or width % 32:
      raise ValueError("Canvas must be divisible by 32")
    # Four frames: previous/current road, previous/current wide; six YUV channels each.
    self.convs = [nn.Conv2d(a, b, 3, stride=2, padding=1) for a, b in
                  [(24, channels), (channels, channels*2), (channels*2, channels*4), (channels*4, channels*4)]]
    # Retain spatial layout; global averaging alone discards useful road geometry.
    self.head = nn.Linear(channels * 4 * (height // 32) * (width // 32), output_dim)

  def __call__(self, images):
    x = images.cast('float32') / 127.5 - 1
    for conv in self.convs:
      x = conv(x).relu()
    feature = self.head(x.flatten(1))
    # The audited teacher normalizes this vector before handing it to the policy.
    return feature / feature.square().sum(axis=1, keepdim=True).sqrt().maximum(1e-6)


class FullFrameDriver:
  """Full camera history -> 33 future XY positions; trained end to end.

  This research head has no modeld ABI adapter. It predicts recorded motion,
  not steering commands. The entire encoder receives the trajectory gradient.
  """
  def __init__(self, height=288, width=512, channels=16, head='waypoints'):
    if head not in ('waypoints', 'smooth_accel'):
      raise ValueError(f'Unknown trajectory head {head}')
    self.head_type = head
    self.encoder = FullFrameEncoder(height, width, 512, channels)
    self.hidden = nn.Linear(513, 256)
    self.path = nn.Linear(256, 12 if head == 'smooth_accel' else 66)

  def __call__(self, images, speed):
    features = self.encoder(images)
    context = features.cat(speed / 30, dim=1)
    output = self.path(self.hidden(context).relu())
    if self.head_type == 'smooth_accel':
      from openpilot.tools.full_frame.smooth import acceleration_basis
      # Continuous piecewise-linear acceleration -> C2 position, rather than
      # independent waypoints. Reference limits are research hyperparameters.
      acceleration = output.reshape(-1, 6, 2).tanh() * Tensor([3., 2.])
      residual = Tensor(acceleration_basis()).unsqueeze(0).matmul(acceleration)
    else:
      residual = output.reshape(-1, 33, 2)
      residual = (residual - residual[:, 0:1, :]) * 10
    # Constant-speed reference plus a learned correction, all expressed in metres.
    from openpilot.tools.full_frame.trajectory import TIMES
    baseline = speed.reshape(-1, 1, 1) * Tensor(TIMES.astype('float32')).reshape(1, 33, 1)
    return baseline.cat(baseline.zeros_like(), dim=2) + residual
