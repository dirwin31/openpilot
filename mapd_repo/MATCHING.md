# Conservative loaded-tile road ambiguity

Before retaining a current road or taking a predicted road, mapd checks every way in the currently loaded tile against the current GPS fix. A candidate needs a positive OSM way ID, at least two nodes, and a successful existing `OnWay(fix, 2)` test. That test uses the projected distance, reported horizontal accuracy (with its existing 5 m floor), way width, and one-way/direction rule. Repeated tile records with the same way ID count once. Rank, road class, posted limit, name, and heading alignment do not resolve a tie.

No candidates produce `noMatch`. One candidate uses the current loaded tile's way geometry and still has to pass the existing narrower selection threshold to become a new match; the broad 2x ambiguity envelope alone never grants a match. A zero posted limit on a matched way remains `matchedNoLimit`. Two or more distinct IDs go through the resolution described below; when that does not settle on one road the output is a neutral invalid `unknown`. The state clears its current way, upcoming evidence, curve output, accepted/override limit, and numeric map limit before serialization. This applies even when the prior way would pass the sticky check or a predicted way would pass its shortcut. A way retained from another tile cannot be published when the loaded tile has no plausible way.

Adjacent OSM ways, junctions, and parallel roads may overlap this conservative uncertainty envelope. Availability will therefore decrease, including some ordinary transitions between adjoining segments. A sole candidate remains unqualified: a tile can omit a road, be stale or wrong, or provide inaccurate geometry/posted limits. This change does not activate the StarPilot SLC map consumer or grant map speed-limit authority.

The packed synthetic-tile tests cover parallel and crossing competitors, prior sticky and predicted ways, empty tile and new-fix recovery, duplicate IDs, a sole road admitted only by the broad envelope, genuinely unique direction-sensitive one-way roads, and no posted limit. A 64/256/1024-way benchmark checks enumeration cost; it does not establish worst-case device performance or justify a spatial index.

# Resolving a fix that fits several roads

When more than one road is inside the broad envelope the matcher no longer gives up at once. `road_track.go` settles the fix from three things, and stays `unknown` when they do not single out a road:

- **Heading.** While the car moves at 3 m/s or more, only roads within 35 degrees of its heading are considered.
- **Limit agreement.** A road is taken only if no road with a different directional limit is nearer or within the margin (the larger of 8 m and the reported accuracy). Roads that agree on the limit cannot publish a wrong one, so the nearest wins.
- **The tracked road.** The last matched road is remembered for 10 s or 150 m driven. It is kept through a near tie with same-limit roads. Another road replaces it only when it is closer by the margin and is the tracked road's continuation or shares a node with it or with the roads ahead of it.

Two further guards: a first match among several roads, a change of limit, or a match right after a near tie must be chosen on every fix for 0.7 s (two fixes at 1 Hz) before it is published; and a car below 3 m/s keeps the tracked road while it is still beside it, and gets no match when there is no tracked road. A hold keeps the track's age fresh but does not refill its 150 m budget.

The track is dropped on a GPS source change, a tile change and expired evidence. `road_track_test.go` holds a wrong-limit case for each rule (parallel roads with other limits, crossing road while stopped, unconnected nearer road, fork with different limits, stale track, hold at speed, fast fixes).

Known limits: between fixes the last match stands, so just after a turn the previous road's limit can show until the next fix arrives. The replay harness does not score `navigationBranches`. Replay new drives before trusting a change to these rules.

# Replaying a drive

The replay harness answers two questions about any recorded drive: how often mapd matches a road, and how often it would publish a wrong speed limit. It replays the drive through the full `State.ProcessGps` pipeline at the logged loop rate, with each fix reused the way the car reused it and carState fed in, on the real offline tiles. It scores every frame against an offline ground truth that does not use the matcher. Everything runs on a laptop; nothing runs on the comma.

## 1. Copy the logs and the tiles

Copy only the drive's `rlog.zst` files (about 8 MB a minute), and the device's offline tiles if you do not already have them. Keep both outside the repository: they hold your road names, destinations and home address.

```bash
mkdir -p ~/drives && cd ~/drives
ssh comma 'cd /data/media/0/realdata && nice -n 19 tar cf - <route>--*/rlog.zst' | tar xf -
ssh comma 'cd /data/media/0/osm && nice -n 19 tar cf - offline' | tar xf -   # tile root: ~/drives/offline
```

Replay with the same tiles the car had. A tile replaced since the drive will make the reproduction gate fail.

## 2. Dump

From the repository root:

```bash
.venv/bin/python -m openpilot.starpilot.navigation.replay.mapd_dump ~/drives ~/drives/<name>.jsonl --route <route>
```

`mapd_dump.py` writes one JSONL row per fix, mapdOut frame, carState sample (wheel speed, steering, yaw rate), deviceMotion sample and guidance update. Its module docstring lists the row kinds.

## 3. Replay, build the truth, report

The Go side runs in the pinned mapd builder image with the offline module cache, like `./build --mapd`. Set `COMMA_MAPD_MODULE_CACHE` if the cache is not in this checkout's `.cache/mapd-go-modules`.

```bash
mapd_repo/scripts/replay_drive.sh run ~/drives/<name>.jsonl <tile root>
```

This writes `<name>.frames.jsonl` (one record per frame: what the car published, what the replay published, diagnostics and the truth) and `<name>.summary.json` beside the dump, and prints the report. To combine several drives:

```bash
mapd_repo/scripts/replay_drive.sh report ~/drives/a.frames.jsonl,~/drives/b.frames.jsonl ~/drives/all.summary.json
```

The run first checks that the replay reproduces the car: replayed status, way id and limit must agree with the logged mapdOut on at least 99.9% of frames (`REPLAY_MIN_AGREE`). Otherwise the test fails with the first mismatch, and no other number from that run should be trusted. This gate only holds for the matcher the car was running. For a changed matcher use `candidate` instead of `run`; the agreement is then reported but not enforced.

## 4. Compare a change

Run the unchanged matcher with `run`, change the code, run the same dumps with `candidate` into other frames files, then:

```bash
mapd_repo/scripts/replay_drive.sh compare base/a.frames.jsonl,base/b.frames.jsonl cand/a.frames.jsonl,cand/b.frames.jsonl verdict.json
```

This prints a baseline-versus-candidate table, with splits, and lists the worst new wrong-limit episodes (time, position, what the candidate published and what the truth says). `verdict.json` holds the same numbers and a pass/fail. The compare fails if the candidate publishes more wrong-limit frames, or more limits off the map, than the baseline (`REPLAY_MAX_WRONG_LIMIT_DELTA`, `REPLAY_MAX_OFFROAD_DELTA`, default 0). It can also fail if the p99 cost grows past `REPLAY_MAX_P99_RATIO` times the baseline. It refuses to run when the two runs replayed different dumps or produced different truth.

## How the truth is built

`replay_truth_test.go` builds the truth offline over the whole drive, future fixes included. It is a hidden Markov model over the tile road graph:

- **Graph.** Ways join wherever they share a node, interior nodes included.
- **Inputs.** The comma's fixes, plus the phone's where the comma has none, thinned to about 1 Hz.
- **Emission.** The distance from the fix to each way, beyond half the road width, and the GPS heading against the travel direction. Driving against a one-way is a penalty, not an exclusion.
- **Transition.** The wheel distance driven (integrated `carState.vEgo`) against the road-graph distance between the two positions.
- **Off-road state.** An off-road state covers parking lots and unmapped ground.

Viterbi gives the path. For each fix, a frame is marked unsure when a path through a different way (way-unsure) or a different limit (limit-unsure) is less than e^5 times less likely than the best path. Unsure frames are kept out of the wrong-limit and wrong-way counts and are reported separately.

The truth never calls the matcher's code (`plausibleLoadedWays`, `GetCurrentWay`, `OnWay` and `navigationPick`), but it uses the same tiles. A road missing from the tile, or a wrong posted limit, is wrong in both. The report checks the truth in four ways:

- **Connectivity.** Consecutive truth ways must share a node.
- **Fix subsets.** The path is rebuilt from every other fix and compared with the full truth.
- **Guidance.** Road names in the phone's turn-by-turn instructions are compared with the truth path after each maneuver the car actually reached.
- **Odometry.** Wheel distance is compared with the truth path length.

## Reading the report

- **match / on-road match.** Frames with a matched road, over frames with a fix, or over frames where the truth is sure the car is on a road.
- **limit coverage.** Frames publishing the correct limit, over frames where the truth way has one.
- **WRONG LIMIT.** A limit was published and it differs from the sure truth way's limit in the travel direction. This includes publishing a limit on a way with none. `(fix lag)` counts the frames that match the truth at the time of the fix that was used, so the GPS was late rather than the road being wrong. `off-road limit` counts limits published where the truth is off the map.
- **wrong way.** A way id other than the truth's. `(same limit)` cannot change the car's speed. The baseline only matches a sole candidate, so its wrong-way episodes are mainly a check on the truth.
- **flip way/limit, dropouts.** A published way or limit that changes and reverts within 5 s. Dropouts are the same way lost for at most 2 s.
- **Splits.** By wheel speed, by GPS source, and by truth context. The contexts are another aligned road within 20 m (parallel), a junction node within 30 m, a node joining pieces of the same named road within 30 m (join), and mid-block. All split labels come from the truth, the map and the log, never from the matcher, so a baseline and a candidate share denominators.
- **WHY UNMATCHED.** For every unmatched frame, the candidates inside the matcher's own broad envelope, classified as follows:
  - none or one candidate;
  - all pieces of one named road;
  - all carrying one limit;
  - one left after a 35 degree heading gate;
  - several aligned roads with different names and limits;
  - too slow for a heading.
- **COUNTERFACTUAL RULES.** Each rule is a candidate change, scored on the ambiguous frames against the truth: how many frames it would match, how many would get the right limit, and how many a wrong one. The "oracle" row is the upper bound of any matcher, and the rule tables list where each rule would be wrong.
- **ProcessGps cost.** Timed per new fix, before any diagnostics run. On a laptop under Docker, compare runs with each other, not with the device.

`go test` without these variables skips the drive tests. The harness logic (truth, metrics, compare, gate) is covered on packed synthetic tiles in `replay_harness_logic_test.go`.
