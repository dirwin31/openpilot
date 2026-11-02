# Dom full-frame driving research

The target is a driving model whose predictions use the full field of view of
both road cameras. The primary experiment here trains full-view images directly
against recorded future motion. Teacher feature distillation is optional warm-up.

This is an offline research implementation, not a model ready to select in Dom.
There are no recorded drives in this workspace, no demonstrated driving gains,
and no vehicle deployment. Synthetic checkpoints only verify the machinery.

## What is implemented

- Paired road/wide input, preserving the whole sensor view at reduced resolution.
  The default canvas is 512x288 per camera for comma four's 1344x760 images.
  Aspect-preserving fitting uses even YUV dimensions/offsets and neutral padding.
- Nominal calibrated old-model views for comparison, plus masks locating those
  old views inside the full-view canvas.
- Camera synchronization and continuity checks. The audited model samples two
  frames 200 ms apart, rather than simply taking adjacent 20 Hz frames.
- A local segment exporter using encoded frame IDs/indexes, logged calibration,
  both camera files, and valid livePose data.
- Recorded-motion labels: integrate NED velocity, transform into the current
  calibrated frame, and sample 33 XY points over the same 0–10 second time grid
  used by Dom. Reject invalid poses, gaps, and insufficient future coverage.
- A roughly 4.93M parameter full-view driving network. Four packed YUV frames
  enter a spatial CNN. Its features and current speed feed a trajectory head.
  Trajectory loss backpropagates through the entire image encoder. A constant
  velocity reference plus learned trajectory residual gives outputs in metres.
- Whole-drive holdouts, constant-velocity comparison, old-visible-region-only
  ablation, displacement errors, lateral errors, and timing reports.
- ONNX export of the trained trajectory network, checker validation, and
  numerical comparison against its native tinygrad implementation.

The driver consumes no teacher features at inference. It reads the full images.
The gradient check also verified that trajectory loss reaches pixels outside a
central reference crop. This establishes a computation path, not useful learned
behaviour; that requires real training and evaluation.

## Verified base and model details

Branch: `research/full-frame-offline`, based on Dom commit
`934dadbcdbaaaac78dd6badd09a1ebadd298bae8` (fetched from the user's fork).

The bundled `driving_vision.onnx` takes `img` and `big_img`, each
`(1,12,128,256)` uint8. This represents two 512x256 YUV420 images per camera.
Its `(1,1576)` output includes the normalized 512-element hidden feature at
`1064:1576`. The bundled policy takes 25 such features and 25 desire pulses;
its 1000-element output contains 990 plan values and eight desire values.
See `validation/model-audit.json` for hashes, checkpoint IDs, weights, and ops.

Both `big_driving_*.onnx` paths in this Dom snapshot are symlinks to the smaller
bundled ONNX files. Downloaded models can differ. The audit command accepts
explicit model paths; do not infer the installed model from these filenames.

The packed channel order follows the executable compiler:
`Y[0::2,0::2], Y[1::2,0::2], Y[0::2,1::2], Y[1::2,1::2], U, V`.
The old models README lists the middle two Y channels differently.

## Run on recorded drives

Run from the repository root in a normal openpilot development environment.
The capture exporter needs the repository's cereal schemas, pycapnp, zstandard,
NumPy, ffmpeg, and ffprobe. A local-only log reader and a pinned NumPy camera-warp
reference let it run without the device's ARM transformation/IPC extensions.
It has now been exercised on two uploaded real rlog/video segments; see
`validation/real-data-run.json` and the first real-run report linked below.
Its nominal teacher transforms assume zero custom camera offset and road camera
as the main view; runs with custom offsets/main-wide settings need exact runtime
transform export before using it for teacher parity claims.

Use matching files from ONE segment, and a stable route ID for the WHOLE drive:

```sh
python -m openpilot.tools.full_frame.capture \
  --rlog /data/train-drive/0/rlog \
  --road /data/train-drive/0/fcamera.hevc \
  --wide /data/train-drive/0/ecamera.hevc \
  --route-id train-drive --max-frames 200 --output /tmp/train-capture.npz

python -m openpilot.tools.full_frame.lab prepare /tmp/train-capture.npz \
  --output /tmp/train-full-view
```

Repeat for a DIFFERENT drive, producing `/tmp/validation-full-view` with a
different whole-drive route ID. Do not split adjacent frames or segments of the
same drive between training and validation. Use more than these short initial
captures for any learning-quality conclusion. The 200-frame default is for
checking export and synchronization first, not a sufficient training dataset.

The driving experiment does not require the teacher-labeling stage:

```sh
DEV=CPU XDG_CACHE_HOME=/tmp/full-frame-cache \
python -m openpilot.tools.full_frame.driving train \
  --train /tmp/train-full-view --validation /tmp/validation-full-view \
  --output /tmp/full-frame-driver --steps 1000

DEV=CPU XDG_CACHE_HOME=/tmp/full-frame-cache \
python -m openpilot.tools.full_frame.driving evaluate /tmp/validation-full-view \
  --checkpoint /tmp/full-frame-driver --output /tmp/driver-validation.json

DEV=CPU XDG_CACHE_HOME=/tmp/full-frame-cache \
python -m openpilot.tools.full_frame.export /tmp/full-frame-driver \
  --output /tmp/full-frame-driver.onnx
```

Use your configured tinygrad GPU backend for GPU work; CPU examples above are
the path tested here. Export needs `onnx`. No torch conversion is required.
Stock Chestnut's RX 9060 has 8 GB. This small model is a plausible first memory
target, but its training time, memory use, USB behaviour, and thermals on that
hardware remain unmeasured. CPU timing here is not a Chestnut benchmark.

The NPZ capture contract is also supported directly: tightly packed uint8
`road_nv12`/`wide_nv12` arrays `(N,H*3/2,W)`, monotonic paired `road_times` and
`wide_times` in seconds, `road_tfm`/`wide_tfm` `(N,3,3)` destination-to-source
teacher matrices, scalar string `route_id`, `calibration` `(N,3)`, `pose_times`
`(P,)`, `orientation_ned`/`velocity_device` `(P,3)`, and boolean `pose_valid`.
Raw hardware buffers must be depadded first. A 1928x1208 capture needs
`--camera-width 1928 --camera-height 1208 --height 320` during preparation.

## How to establish that the new view matters

The driving evaluator records full-view trajectory error and the error after
removing sensor regions outside the old model views. It also records prediction
change from that intervention. Changed outputs alone are insufficient: useful
new context should improve held-out prediction error, especially on curated
cross-traffic, side-entry, turn, merge, and near-side obstacle cases.

Masking introduces a distribution shift. A stronger follow-up is to train a
matched old-view-only model with the same architecture, training budget, and
holdouts, then compare full and cropped training, including scenario-level
results. Record actual obstacle outcomes or annotations where possible.
The trainer supports `--view legacy` for this matched comparison; use the
`legacy_regions_only` evaluation field for that model because it was trained
on masked inputs. The first real run includes this comparison.
Imitating recorded motion can learn bad demonstrations and shortcuts; livePose
is an estimated trajectory, not independent obstacle ground truth. Separate
manual driving, assisted driving, disengagements, and interventions in the data.

This first network sees 200 ms of image history and speed, without the original
five-second temporal state, desire conditioning, traffic convention, or a
learned uncertainty model. Those are substantive next architecture tasks.

## What remains before it can drive through Dom

1. Run real-data export, inspect synchronized images/paths, and train on varied
   whole-drive splits. Measure full-view gains against constant and crop baselines.
2. Add route/desire conditioning, temporal context, and calibrated output
   uncertainty; extend labels/head outputs beyond this XY-only prototype.
3. Implement an explicit artifact input-geometry contract. Dom currently calls
   `get_warp_matrix` for its fixed trained views, even though it transfers full
   camera buffers. New artifacts must select the full-view resize consistently
   in training, compilation, and runtime, preserving NV12 layout/history.
4. Satisfy the complete modeld output contract. Dom's parser expects 33x15 plan
   quantities and uncertainty; publishing also uses perception/meta/pose fields.
   The current XY network cannot be dropped into that interface. Do not invent
   confidence or missing safety outputs merely to make the parser accept it.
5. Replay and shadow-run through the real parser/planners, evaluate closed-loop
   recovery and interventions, and benchmark sustained 20 Hz on stock Chestnut.

The exported ONNX deliberately has a separate `full,speed -> trajectory` ABI
and no driving artifact metadata. The current compiler will not treat it as a
deployable Dom model. Deployment work should follow evidence from real training.

## Optional teacher warm-up and audit

```sh
DEV=NPY python -m openpilot.tools.full_frame.lab audit --output /tmp/model-audit.json
DEV=CPU python -m openpilot.tools.full_frame.lab label /tmp/train-full-view
DEV=CPU python -m openpilot.tools.full_frame.lab label /tmp/validation-full-view
DEV=CPU python -m openpilot.tools.full_frame.lab train \
  --train /tmp/train-full-view --validation /tmp/validation-full-view \
  --output /tmp/warmup-encoder --steps 1000
```

Pass `--encoder /tmp/warmup-encoder` to driving training to initialize the image
encoder. The driving loss still trains the encoder. Feature matching alone
cannot teach decisions about hazards the cropped teacher never observes.

ONNX is not inherently untrainable. We verified a finite, nonzero gradient
through `summarizer._mu.weight` in the bundled vision ONNX using tinygrad.
What is unavailable in this fork is comma's full original training/data/world
model system; that is a different limitation from access to weight tensors.

## Validation and sources

`python -m unittest openpilot.tools.full_frame.test_geometry openpilot.tools.full_frame.test_trajectory`
runs 17 tests for packing, padded NV12, full-view geometry, synchronization,
temporal gaps, pose frames, integration, target validity, and crop ablation.
With a working tinygrad backend, add `openpilot.tools.full_frame.test_student`
to run the eighteenth test: trajectory gradients must reach the image encoder
and camera pixels outside a central reference crop, with the path anchored at zero.
`validation/` contains the audit and synthetic smoke evidence. Synthetic runs
verify execution only and are explicitly not driving performance measurements.
The capture-support tests add four checks for camera references and local raw,
bz2, and zstd event reading. Run all tests with a working tinygrad backend and
pycapnp installed: `python -m unittest discover -s tools/full_frame -p 'test_*.py'`.

## First real-data run

Two uploaded segments each contain 1200 paired road/wide frames at 1344x760,
with valid livePose throughout and sub-0.04 ms road/wide timestamp differences.
Both were fully assisted driving, from installed Dom commit
`d9b60fddc18fb5970e89a128bf642728cb9ea0bd`, newer than the pinned fork base.

The initial experiment uses 200 frames (about ten seconds) from each segment:
196 overlapping examples for training, and 196 from the other drive for
validation. Matched full-view and nominal-crop models each train 200 steps,
with eight base channels, the same seed, samples, and optimizer settings.
Held-out mean displacement errors are 3.041 m (full), 3.318 m (crop-trained),
and 2.827 m (constant speed). The full model reacts to the extra regions, but
does not beat the simple baseline overall. This demonstrates a functioning real
training pipeline, not a useful improvement in driving. Overlapping examples
are not independent trials. Only one seed and two short image windows were used.

Detailed artifacts live outside Git under `/workspace/full-frame-data/`:
`FIRST-REAL-RUN.txt`, `first-real-run.png`, prepared datasets, both checkpoints,
and the verified full-view research ONNX. Raw user recordings stay outside the
repository. No model was registered with modeld or deployed to a vehicle.

## Focused follow-up: corners, parked cars, and stop signs

New training defaults to `--head smooth_accel`: six continuous acceleration
knots are integrated twice to produce a smooth path with the correct origin and
initial reference velocity. Old waypoint checkpoints load using their saved
head configuration (or the legacy default when that field is absent). ONNX
export supports both heads. This removes independent-waypoint jitter; it does
not guarantee accurate or physically admissible driving.

`capture --frame-step 4 --max-frames 400` samples the full minute at 5 Hz.
Prepare it with `--frame-skip 1` to retain a 200 ms previous/current image gap.
Frame period is now recorded and checked in training/evaluation manifests.
This yielded 248 examples per uploaded route spanning about 49.4 seconds;
remaining frames lack a complete ten-second future within the segment.

The expanded development comparison failed to improve accuracy: smooth/full
mean displacement error was 9.791 m, smooth/nominal-crop 5.417 m,
waypoint/full 7.968 m, and constant speed 4.343 m. These use a different
evaluation window than the first report. Do not compare their values directly.
Only the highway drive is in training, with zero stops; the nighttime urban
development drive contains the red-light stop. It has been reused for research
and is not an untouched final test set.

Scenario diagnostics now include motion-based stop approaches, reference bends,
spatial inward deviation relative to a recorded curved path, and predicted
speed/position at a recorded stop. These are explicitly distinguished from true
curb/road-boundary clearance, parked-car clearance, stop-sign recognition, and
stop-line compliance. The latter require verified geometry/object/event labels.
No stop-sign detector has been trained in this prototype.

The full scenario audit, measured failures, label requirements, next-data copy
script, and checkpoints are described in
`/workspace/full-frame-data/FOCUSED-RESEARCH.txt`. The newer routes identified by
the user are 48/49; the current clips came from 46/47. A separate Mac script
copies those newer routes' road-camera files and full logs through USB ADB.

- [comma 0.11.2 release](https://blog.comma.ai/0112release/#full-frame-copy):
  full road-camera copies precede GPU transformation; future full-frame models
  are a separate change.
- [Chestnut hardware](https://blog.comma.ai/chestnut/): stock RX 9060, 8 GB.
- [Official driving ONNX repository](https://huggingface.co/commaai/openpilot_driving_models).
- [Community training pipeline](https://github.com/mbalesni/openpilot-pipeline):
  useful precedent for ONNX fine-tuning/distillation, but targets old 0.8.11
  supercombo and is not a current Dom training solution.
- [comma2k19](https://github.com/commaai/comma2k19): older single-road-camera
  data, useful for pipeline research but insufficient for paired current-camera
  full-view claims.
