# Getting the full-view research onto comma

Target: Nathan's comma four, stock Chestnut, StarPilot Dom in
natehuby76/StarPilotBT; preferred external-GPU model mountaindewv3.

## What hardware access helps with

Chestnut attached in its normal supported comma configuration lets us measure
AMD execution, memory, camera transfers, preprocessing and sustained runtime.
It does not supply training code or fix the current weak driving predictions.
Connecting it to a Mac over USB does not by itself expose it as a native Mac
GPU. We have not verified a supported standalone Mac-to-Chestnut arrangement.
No hardware rearrangement is needed for the next model-identification step.

Current Dom also vendors Jetlink with an Apple silicon Mac host option. That
could be useful for later offline inference or transport experiments, but its
existing comma-side warp still emits the small model's calibrated crop. It is
not a full-frame solution without a new, trained and verified input contract.

## Next information from the comma

With the comma connected to the Mac through the already-working ADB setup,
run this repository's standard-library-only collector on the Mac:

```sh
python3 openpilot/tools/full_frame/device_inventory.py --output comma-model-inventory.json
```

It reads saved small/big selections and matching cached v26 catalog rows,
including the Mountain Dew v3 source digest when present. It lists installed
model file sizes without copying or loading model weights. It changes no
settings and needs no Chestnut. Missing entries are reported as missing; a
saved model ID alone cannot identify weights, geometry or the training ABI.
The cloud workspace cannot access the Mac's USB connection automatically.

The uploaded catalog now identifies Mountain Dew v3 as
`mountaindewv3_driving_supercombo.onnx`, SHA-256
`1cfd2c45353d876b5b257b24d77537de58ab90a44ae595f15b1f64b6ea3fd770`.
Its separate compiled Chestnut artifact is 804,589,195 bytes with SHA-256
`b93bcff2c7c012ff711967535112dd16f19747b1e51fe0eaee5a810d4b858096`.
See `validation/mountain-dew-v3-identity.json`. These identify the intended
weights but do not reveal input shapes or establish trainability.

The GitLab LFS endpoint did not return this source object. The cloud network
cannot reach the Hugging Face source archive. `source_index.py` runs with only
Python's standard library on a Mac and collects every paginated ONNX object
entry from the archive without downloading weights. The uploaded complete three-page index now confirms the source at
`onnx/mountaindewv3/mountaindewv3_driving_supercombo.onnx`, 792,542,056 bytes.
Its Xet hash is a storage identity, not the ONNX SHA-256; verify the latter.

On a Mac with a Python environment containing ONNX, the standalone
`inspect_source.py` supports:

```sh
python inspect_source.py /path/to/mountaindewv3_driving_supercombo.onnx \
  --download --output /path/to/mountain-dew-v3-report.json
```

It pins the identity verified from Nathan's catalog when `--catalog` is omitted.
`--download` fetches the identified public source (792.5 MB), prints progress,
and verifies size and SHA-256 before publishing the file. Existing sources are
preserved and must pass the same digest check. The report includes tensor
contracts, initializer shapes and graph connectivity, with no learned weights.
This lets the Mac send a small report for investigation rather than upload the
entire model. Raw metadata is reported as encoded text; it is not unpickled.

After obtaining the source, `inspect_source` accepts `--catalog`, `--model` and
`--output`, verifies the source hash before parsing the graph, and records graph
inputs/outputs, temporal state names and operator counts. It does not execute
the graph, unpickle metadata or load external tensor paths.

Next inspect the actual source graph: camera
input sizes/format, temporal state, outputs and metadata. A compiled inference
pickle alone is insufficient evidence that fine-tuning the intended model is
possible. The earlier split-model proof used a different historical small
model and must not stand in for this verification.

## Compute benchmark prepared for hardware access

Use a fresh output path, the included historical tinygrad runtime, a saved
research checkpoint and one numbered prepared example:

```sh
export PYTHONPATH="$PWD/tinygrad_model_runtime/legacy9d/tinygrad_repo:$PWD/opendbc_repo${PYTHONPATH:+:$PYTHONPATH}"
python -m openpilot.tools.full_frame.benchmark /path/to/checkpoint /path/to/000000.npz \
  --device AMD --iterations 100 --output /tmp/full-view-compute.json
```

Only run AMD where that GPU/backend is available. CPU and METAL are alternative
explicit backends, not substitutes for an actual Chestnut measurement. This
benchmark runs the current small trajectory research network on a recorded
sample. It reports synchronized warmed JIT latency and tinygrad's tracked
allocation count, not total GPU memory. It does not measure Mountain Dew v3,
frame decoding, camera transfers, production warps or total control-loop time.
A low compute latency does not establish runtime compatibility or driving quality.
No vehicle-control service is contacted.

## Remaining implementation before a driving model

1. Obtain and verify the actual Mountain Dew v3 source/contract, or explicitly
   choose another trainable base with the user if that source is unavailable.
2. Train full-view image inputs through driving losses; retain or retrain every
   production output/head needed by Dom, plus temporal state and calibration.
3. Validate held-out turns, parked-car clearance and sign/stop-line behavior.
   The present motion imitation failed the constant-speed baseline. The one
   visible stop-sign example lacks valid motion labels and is not a correct-stop
   training target. Object detection or output sensitivity alone cannot pass.
4. Verify exported and native predictions match, then implement the production
   full-view warp/input metadata and the actual modeld output/state contract.
5. Run logged replay on the comma/Chestnut and bench shadow inference, with no
   research outputs fed to vehicle controls, to check end-to-end timing and state.

No experimental model has been installed or selected on Nathan's device.

## Verified Mountain Dew v3 graph and proposed full-view feature branch

The Mac report verifies the source SHA and identifies `new_img` as
`(2,6,128,256)` uint8: two 512x256 packed-YUV views. It has three recurrent
state pairs, including a `(128,1,16384)` float feature history. State buffers
alone total 10,358,912 bytes, excluding weights, activations and workspaces.
The graph has 395,302,147 initializer elements and 18,452 output values.

The action slice is `[2062:2066]`, separate from the plan `[917:1907]`.
Dom's v16 runner parses the action means and uses lateral acceleration divided
by `max(1,vEgo)^2` for curvature, and longitudinal acceleration for acceleration,
followed by runtime smoothing. Training only trajectory positions does not
establish correct v16 driving actions. Static dependency tracing confirms that
`view_39` feeds both this action head and the next feature-memory state.
See `validation/mountain-dew-v3-contract.json`.

`mdv3_adapter.py` constructs an offline research graph retaining the original
calibrated camera path and adding a second input `full_img`, the complete
letterboxed road/wide views at 512x256. A 58,160-parameter image branch produces
a `(1,32,512)` residual at `view_39`. Its projection starts at zero to preserve
the baseline; gradients first update that projection, then reach the new image
encoder. The branch feeds the actual action/policy path and recurrent memory.
It does not use independent object-detection boxes or a hard-coded sign rule.

The graph retains all original output/state names and adds one image input.
It still requires a production dual-view warp and loader integration; the
existing compiler/runner must not be assumed to provide `full_img`. Changed
features can also affect auxiliary heads, so those require validation too.

Tests verify output and recurrent-state preservation, action-loss gradients to
the image encoder, and sensitivity to peripheral image content on a small
synthetic fixture. These tests are not execution of Mountain Dew's full weights
or evidence of improved driving.

The standalone script can construct and verify the graph on the Mac, where the
actual source already exists. Its `--verify-device AUTO` selects METAL on Apple
silicon Mac, otherwise CPU, and prints the backend. Install tinygrad revision
`9d0446a4ba8a532c8b674fb6ad795af015cd9dcf` into the existing audit virtualenv;
its ONNX runner source matches Dom's vendored historical runner byte-for-byte.
Three synthetic frames compare every output and recurrent state against the
original at `atol=rtol=1e-4`. This is zero-branch numerical parity, not a warmed
real-camera replay, training run, hardware timing check or driving validation.
It writes `.contract.json` and `.parity.json` beside the untrained ONNX. Keep
the original source; allow another approximately 793 MB for the research graph.
Never select this graph in modeld. It remains explicitly `modeld_ready=false`.


### First Mac native attempt: dependency failure

The uploaded contract reports graph construction completed with the original
source hash and 58,160 new branch parameters. The uploaded parity report says
`ModuleNotFoundError: No module named 'tinygrad'`. No native inference or output
comparison ran. It is not evidence of a numerical mismatch.

The pinned upstream tinygrad revision requires Python >=3.11. The failed Mac
attempt did not report its Python version, so an older interpreter is a possible
cause, not an established diagnosis. `setup_mdv3_mac.py` checks for a supported
interpreter, creates a separate `Downloads/comma-mdv3-env`, installs ONNX and the
pinned tinygrad, and verifies the ONNX runner source hash before retrying. If
no Python >=3.11 is installed, it uses Homebrew to install Python 3.12. It prints
installation failures directly. It downloads the updated adapter script and runs
`--verify-only` on the existing research graph. No weights are downloaded, no
graph is rebuilt, and no SSH/device services are accessed. The user can upload
the refreshed parity report. An explicit comparison_executed field now separates
startup failures from completed comparisons that fail numerical tolerances.


### Second native attempt: Metal numerical mismatch

The uploaded result confirms three-frame native METAL inference completed.
The original tolerance (`atol=rtol=1e-4`) failed: max absolute output difference
0.1875 and feature-state difference 0.0078125; image/desire states matched.
No conclusion about the action means follows from an aggregate output maximum.
No cause has been established and no tolerance has been loosened.

`setup_mdv3_mac.py --diagnose` runs `diagnose_mdv3.py` on the existing files and
refreshes the same parity JSON with frozen-initializer comparisons, original
repeatability, per-frame/per-output-section errors, and observed feature tensors.
All output buffers are captured before inspecting intermediate tensors. The
report explicitly notes this observation order; taps cannot be assumed to expose
exactly the same internal materialization as an already fused output kernel.

The runtime controls compare the original added branch, an identity bypass,
an explicitly buffered addition, and an FP32 addition cast back to FP16. They
change no files or weights. A bypass passing does not qualify a full-view model:
the report's top-level pass remains tied to the original residual branch,
original repeatability and preserved original initializers. The buffering and
precision controls are not exported models. They help separate a nonzero branch,
changed frozen constants, unstable native execution and fusion/rounding effects.
The small CPU fixture verifies that a deliberately nonzero branch fails while
its identity control passes, without incorrectly marking the model as qualified.
Native diagnostic results on the actual Mac weights remain pending.

### Metal diagnostic result and saved-graph repair candidate

The uploaded diagnostics establish: all 653 original initializer tensors match;
the original repeats with zero error; all observed encoder/base-feature values
match; and the full-view residual is exactly zero. The unbuffered addition
changes the resulting features by up to 0.0078125. Its action-mean difference is
0.00048828125 on the second synthetic frame. An FP32 addition alone reproduces
the error. The eager buffered-add control matches every output and recurrent
state exactly on all three Metal frames. These are synthetic numerical results,
not driving performance evidence. See `validation/mdv3-native-diagnostics.json`.

New exports put three `org.tinygrad::Contiguous` nodes around the FP16 addition.
Unlike eager realization, these are lazy graph barriers, so the exact runtime
control result does not automatically establish parity for the saved export.
Action-loss gradients through these graph buffers pass on a small CPU fixture.
Training must differentiate a fresh lazy forward before realizing inference
buffers; the pinned tinygrad realization discards already materialized graphs.

`setup_mdv3_mac.py --repair` downloads the updated standalone repair utility.
It verifies the original source digest and requires the research graph to remain
untrained with a zero projection. It needs about 0.9 GB temporary disk space,
adds graph buffers to a candidate, checks/saves it, and compares the saved file
against the original with the unchanged tolerances. Only a completed successful
comparison replaces the existing derived research ONNX. A failure preserves it
and removes the temporary candidate. The original source remains unchanged.
The old parity report is backed up before the familiar parity JSON is updated.
CPU tests cover both candidate publication paths and the graph-buffer gradient.
The actual saved-graph repair result on Metal remains pending.

### Saved buffered export: Mac contract records successful native parity

The uploaded updated contract records feature-fusion revision 2 and
`native_output_parity_verified=true`. The repair script writes this sidecar only
after a successful saved-candidate replay and publication. The detailed updated
parity report is still uninspected; no exact maximum difference is claimed for
this saved export. The model remains untrained and `modeld_ready=false`.

The action-signal audit now covers all ten segments from routes 48 and 49.
Across 11,436 road-camera frames, 9,719 are moving above 2 m/s; 1,784 moving
frames have neither openpilot axis active, and only 259 of those have matched
valid pose angular-velocity and acceleration signals (12.95 seconds at 20 Hz).
This is a candidate count before calibration, future-delay, gear and human
quality checks. It is not a count of verified expert labels, and openpilot
inactivity does not rule out the vehicle's stock cruise controller.

CAN yawRate is exactly zero in all audited moving samples, including turns.
Do not build lateral-action targets from that field. Valid pose yaw-rate
estimates exist after initialization; rotate their full vectors using the
logged calibration before any action-target construction. Most manually driven
startup footage precedes valid poses. The visual stop-sign approach still lacks
a valid pose at that time and is not a verified complete-stop demonstration.
Assisted footage can supply context or warm-up, but imitating its existing
errors is not evidence of learning the requested corrections. More curated
manual demonstrations are needed for a useful training/evaluation set.

### Selecting the next existing recordings

Prioritize complete routes with openpilot disengaged and the driver performing
the desired behavior: full stops at relevant stop signs/lines, the preferred
corner path, and comfortable parked-car clearance. Keep all consecutive segments
with matching road/wide HEVC and rlog, including the initial context. Several
minutes per route allow pose initialization and later examples; short startup
clips can be visually useful while having no valid lateral-motion target.
Confirm assistance flags, calibration, gear, valid poses and stock-cruise status
from logs instead of assuming a remembered manual drive proves every label.
Human review must establish the relevant sign/stop line and intended clearance.
First acquire one or two suitable full routes and audit their signal coverage
before requesting a larger corpus. Maintain whole-route training/evaluation
separation. Additional footage alone is not proof of improved driving.

## Training preparation while collecting manual routes

`mdv3_sequence.py` builds both camera inputs from unsigned-byte YUV planes and
explicit caller-supplied baseline transforms. The full_img reference retains
both complete fields of view with an aspect-preserving 512x256 fit. This is not
production-warp parity; exact transform provenance/custom offsets remain a
separate requirement. Input order is road, then wide, for both tensors.

The actual model must consume contiguous 20 Hz frames. The former small-driver
5 Hz examples cannot be fed to the internal five-image/128-feature/132-desire
queues as if they had the same timing. SequenceGuard resets history at route
changes, missing frames, restarts and timing gaps; by default losses start only
after 132 contiguous frames. Sparse loss sampling may later be used only while
still replaying every intermediate frame. Desire pulses match the runtime's
rising-edge behavior, with slot zero suppressed. The actual RHD flag selects
traffic_convention[is_rhd]; right-hand traffic/left-hand-drive is [1,0]. Earlier
synthetic parity smoke inputs used [0,1] for both models and therefore remain
parity tests, not geographically accurate route replays.

`action_targets.py` rotates complete velocity/angular-velocity/acceleration
vectors by device_from_calib.T, and rejects nonfinite or malformed inputs.
Delayed interpolation rejects invalid/gapped data and eligibility transitions;
lateral and longitudinal horizons must be supplied separately. The kinematic
proxy computes future measured curvature yaw_rate/forward_speed, encoded with
the anchor speed squared to match v16's runtime curvature decoding. This avoids
mistaking future-speed lateral acceleration for current-speed encoded curvature
when braking. It is an explicit imitation proxy, not comma's original loss or
certified expert target. Low-speed lateral targets are masked while stationary
longitudinal labels remain possible. Gear, stock cruise, calibration and human
scenario quality still require checks by the route assembler.

`mdv3_training.py` provides one-frame truncated action supervision, not a complete
route trainer. The optimizer must contain exactly the eight 58,160-parameter
full-view branch tensors; frozen model parameters are rejected. It computes
masked robust action loss and, by default, requires baseline auxiliary-head
references for regularization of the pose/transform outputs that the branch can
affect. Loss/gradients and pre-update states are realized before optimizer
mutation; recurrent state is detached before the next step. A small synthetic
fixture verifies projection-first learning, encoder learning after projection
changes, preservation of frozen model weights, and rejection of an optimizer
containing frozen parameters. No actual Mountain Dew training run has occurred.
Auxiliary loss alone does not qualify the resulting auxiliary heads or vehicle
behavior; they must be evaluated on held-out replay.

## New manual route 4b: log audit

Three uploaded 24 MiB-or-smaller parts reconstruct a 52,111,360-byte tar with six
complete rlog files, for route `0000004b--855ea3ca69`. Raw logs stay outside Git.
The logs identify Dom source `cbd6e1eb52e8a46f9837a750d675b877d5ac8762`, so the
current schema applies, including renamed narrow-camera/calibration events and
`deviceMotion.timestamp`. No openpilot assistance or stock-cruise activation was
observed in the matched candidate frames. This is a promising demonstration
route; visual corner/clearance/sign correctness has not been reviewed yet.

There are 6,464 matched road/wide frame indexes over 323.14 seconds, with no
20 Hz timing gaps in the measured camera intervals. Worst inter-camera skew is
0.127 ms. After initialization at about 40 seconds, the per-segment audit finds
5,315 moving frames (265.75 s at 20 Hz) meeting valid pose, calibration, drive
gear, no-openpilot and no-stock-cruise criteria. Whole-route context can recover
a few boundary frames that a per-segment as-of lookup excludes. CAN yawRate is
still unusably zero; use calibrated motion instead.

The current motion sensor timestamp precedes publication by typically 24 ms.
Capture now explicitly maps old/current event names from the chosen schema,
uses current sensor timestamps for valid poses, masks missing timestamps or
nonfinite vectors, and retains the last snapshot at a repeated sample time.
Historical livePose retains its publication-time provenance. Output metadata
records the pose event/time source and recorded source commit. A smoke capture
on real current rlogs from segments 1 and 2 passed with eight paired frame
indexes and 2,400 monotonically ordered motion rows; image decoding was stubbed,
so this is not a real-video or model inference check.

The cameras for all six segments are needed next, preserving both views and
history across the minute boundaries. A kinematic stationary interval near
61–62 seconds is only an unreviewed event candidate; logs alone cannot label it
as a stop sign, traffic signal, parking maneuver or verified correct stop line.
