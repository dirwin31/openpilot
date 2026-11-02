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
entry from the archive without downloading weights. Upload its JSON report to
locate the actual archived source path; no assumed URL should count as verified.

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
