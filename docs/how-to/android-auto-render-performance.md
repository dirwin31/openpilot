# Android Auto render optimizations

These changes reduce per-frame work without reducing the configured FPS or
changing driver-monitoring validity/frequency checks.

## Profile findings

The September 25 capture contained 30 profile windows and 163 render-stat
intervals, mostly onroad. Median interval values were 25.8 FPS, 38.35 ms frame
time and 27.19 ms renderer CPU time. The corner decoration and repeated Params
reads were visible hot paths. Native calls that release the GIL can absorb
sampling attribution: the raylib share is not a measurement of wrapper overhead.

## Changes

- Bypass the entire radial favorites component in the dedicated Android Auto
  car view. `OnroadControls.route()` sends no touches to the normal driving
  layout, so its radial menu cannot be opened there. Avoid constructing the
  component, processing gestures, loading favorite slots/layouts each frame,
  drawing its hint or creating its cached textures. The separate AA quick menu
  and navigation destination favorites remain available.
- Keep the favorite-menu corner cache for the comma's own large UI (C3X), based on the useful part of
  `origin/AAOptimizev1` (`ef767cff9`). Size and pressed state invalidate it;
  translation reuses it. Deferred rendering captures its inputs, and compositing
  preserves premultiplied alpha. Only this small cached decoration is supersampled.
  C4's native `mici` UI uses a separate favorites overlay and is unchanged.
  Mirror mode still shows whatever is on the device's own display.
- Use the existing shared UI settings cache for default border/lead helpers,
  instead of opening Params and reading the filesystem on each draw. Explicit
  Params arguments retain their behavior; control-state parameters are unchanged.
- Skip unchanged polygon shader uniform uploads. Value snapshots detect in-place
  color, stop and rectangle changes, and shader teardown clears the cache.
- Avoid invalidating model-renderer transforms when an equivalent transform is
  submitted. Use scalar two-point interpolation for normal increasing endpoints,
  retaining NumPy's behavior for duplicate/reversed endpoints. The parent already
  caches transforms, so this is not a claim of eliminating an every-frame update.
- Avoid polling map data twice when drawing an already-prepared Android Auto map.
- Attribute sampled Params waits to key names in the existing profiler. Unlike
  the branch's global Params wrappers, this adds no hook to every parameter read.
  The report contains sampled shares, not exact read counts, and never values.

Earlier changes removed full-frame car-layout MSAA, fused padding/vertical flip
and alpha composition into NV12 conversion, and made diagnostic `glFinish`
opt-in. The map's separate render target and camera upload path remain unchanged.

## Device measurements

Offroad microbenchmarks on the comma's Adreno 630 used a temporary source overlay;
the installed checkout and original logs were not modified. Median wall times:

| Operation | Before | After | Iterations per variant |
| --- | ---: | ---: | ---: |
| Corner decoration, including GPU completion | 3.099 ms | 0.639 ms | 200 |
| Border-setting lookup | 0.115 ms | 0.038 ms | 1,000 |
| Unchanged gradient configuration | 0.187 ms | 0.058 ms | 300 |

Corner CPU time fell from 2.937 ms to 0.509 ms. Both corner variants include
render-target setup, clearing, and a GPU completion wait. Their 2.460 ms wall-time
difference demonstrates the cache benefit, but the remaining 0.639 ms is not
the hint's standalone cost. The subsequent AA component bypass has not been
benchmarked; it also skips menu layout and parameter reads absent from this
microbenchmark. These measurements are not additive whole-frame savings or a
full-drive FPS result.

The initial optimizations passed 232 relevant tests on the device, including actual GPU NV12 composition
and byte-for-byte cached-versus-uncached gradient rendering checks. Tests cover
cache invalidation, map polling, interpolation, navigation, render layers,
settings changes and profiling privacy.

The subsequent AA favorites bypass passed 91 device tests covering the optional
component's lifecycle, C3X radial favorites, C4 favorites and AA navigation/touch
routing. These ran through the temporary source overlay, without deploying to
the installed UI or restarting projection.

## Remaining validation

After deployment, compare matched onroad runs at the same configured FPS and
resolution. Check produced and sent FPS, frame age, driverStateV2 and
driverMonitoringState frequency/validity, and communication alerts. The target is
20 Hz driver monitoring with no loss of delivered UI FPS; isolated benchmarks
cannot prove that outcome. Do not weaken safety checks to suppress the warning.
