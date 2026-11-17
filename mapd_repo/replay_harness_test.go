// Drive replay harness (see MATCHING.md, "Replaying a drive"). The drive tests
// skip unless their environment variables are set; the logic they use is
// covered by replay_harness_logic_test.go on synthetic tiles.
package main

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"slices"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"

	"capnproto.org/go/capnp/v3"
	"pfeifer.dev/mapd/cereal"
	"pfeifer.dev/mapd/cereal/car"
	"pfeifer.dev/mapd/cereal/log"
	"pfeifer.dev/mapd/maps"
	m "pfeifer.dev/mapd/math"
	ms "pfeifer.dev/mapd/settings"
)

type replayRow struct {
	K      string  `json:"k"`
	Lm     uint64  `json:"lm"`
	Src    uint64  `json:"src"`
	Valid  bool    `json:"valid"`
	Fix    bool    `json:"fix"`
	Lat    float64 `json:"lat"`
	Lon    float64 `json:"lon"`
	V      float32 `json:"v"`
	B      float32 `json:"b"`
	Ha     float32 `json:"ha"`
	Ba     float32 `json:"ba"`
	Sa     float32 `json:"sa"`
	Alt    float64 `json:"alt"`
	St     string  `json:"st"`
	Gs     int     `json:"gs"`
	Gm     uint64  `json:"gm"`
	Cm     uint64  `json:"cm"`
	Way    int64   `json:"way"`
	Road   string  `json:"road"`
	Lim    float32 `json:"lim"`
	A      float32 `json:"a"`
	Yr     float32 `json:"yr"`
	Ss     bool    `json:"ss"`
	Wx     float32 `json:"wx"`
	Wy     float32 `json:"wy"`
	Wz     float32 `json:"wz"`
	Status string  `json:"status"`
	Text   string  `json:"text"`
	Dist   float32 `json:"dist"`
	Br     []struct {
		Id int64   `json:"id"`
		N  string  `json:"n"`
		D  float32 `json:"d"`
		A  float32 `json:"a"`
	} `json:"br"`
}

// replayDump is one drive's dump, split by row kind and sorted by time.
type replayDump struct {
	start                                  uint64
	ext, internal, ph, frames, cs, dm, nav []replayRow
	hash                                   string
}

func loadReplayDump(path string) (*replayDump, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	d := &replayDump{}
	h := sha256.New()
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<24)
	for sc.Scan() {
		h.Write(sc.Bytes())
		var r replayRow
		if err := json.Unmarshal(sc.Bytes(), &r); err != nil {
			return nil, err
		}
		d.add(r)
	}
	if err := sc.Err(); err != nil {
		return nil, err
	}
	d.finish()
	d.hash = hex.EncodeToString(h.Sum(nil))[:16]
	return d, nil
}

func (d *replayDump) add(r replayRow) {
	switch r.K {
	case "ext":
		d.ext = append(d.ext, r)
	case "int":
		d.internal = append(d.internal, r)
	case "ph":
		d.ph = append(d.ph, r)
	case "map":
		d.frames = append(d.frames, r)
	case "cs":
		d.cs = append(d.cs, r)
	case "dm":
		d.dm = append(d.dm, r)
	case "nav":
		d.nav = append(d.nav, r)
	case "start":
		d.start = r.Lm
	}
}

func (d *replayDump) finish() {
	for _, rows := range [][]replayRow{d.ext, d.internal, d.frames, d.cs, d.dm, d.nav} {
		sort.SliceStable(rows, func(i, j int) bool { return rows[i].Lm < rows[j].Lm })
	}
	// Phone fixes repeat in every navigation message; keep one per source time.
	sort.SliceStable(d.ph, func(i, j int) bool { return d.ph[i].Src < d.ph[j].Src })
	ph := d.ph[:0]
	for _, r := range d.ph {
		if len(ph) == 0 || ph[len(ph)-1].Src != r.Src {
			ph = append(ph, r)
		}
	}
	d.ph = ph
	if d.start == 0 && len(d.frames) > 0 {
		d.start = d.frames[0].Lm
	}
}

func replayLocation(r replayRow) log.GpsLocationData {
	_, seg, err := capnp.NewMessage(capnp.SingleSegment(nil))
	if err != nil {
		panic(err)
	}
	l, err := log.NewRootGpsLocationData(seg)
	if err != nil {
		panic(err)
	}
	l.SetHasFix(r.Fix)
	l.SetLatitude(r.Lat)
	l.SetLongitude(r.Lon)
	l.SetSpeed(r.V)
	l.SetBearingDeg(r.B)
	l.SetHorizontalAccuracy(r.Ha)
	l.SetBearingAccuracyDeg(r.Ba)
	l.SetSpeedAccuracy(r.Sa)
	l.SetAltitude(r.Alt)
	return l
}

func replayCarState(r replayRow) car.CarState {
	_, seg, err := capnp.NewMessage(capnp.SingleSegment(nil))
	if err != nil {
		panic(err)
	}
	c, err := car.NewRootCarState(seg)
	if err != nil {
		panic(err)
	}
	c.SetVEgo(r.V)
	c.SetAEgo(r.A)
	c.SetStandstill(r.Ss)
	return c
}

func nearestFix(rows []replayRow, key func(replayRow) uint64, t uint64) (replayRow, bool) {
	i := sort.Search(len(rows), func(i int) bool { return key(rows[i]) >= t })
	best, bestD := replayRow{}, uint64(math.MaxUint64)
	for _, j := range []int{i - 1, i} {
		if j < 0 || j >= len(rows) {
			continue
		}
		d := key(rows[j]) - t
		if key(rows[j]) < t {
			d = t - key(rows[j])
		}
		if d < bestD {
			best, bestD = rows[j], d
		}
	}
	return best, bestD < 2_000_000
}

// replayCand is one way inside the matcher's own broad envelope for a fix.
type replayCand struct {
	Id   int64   `json:"id"`
	Lim  float32 `json:"lim"`
	Key  string  `json:"key"`
	Sin  float32 `json:"sin"`
	D    float32 `json:"d"`
	Nar  bool    `json:"nar"`
	Name string  `json:"n"`
}

// replayFrame is one replayed mapdOut frame: the car's output, the replayed
// output, diagnostics, and (after truth.apply) the offline truth.
type replayFrame struct {
	T     float64      `json:"t"`
	Lm    uint64       `json:"lm"`
	Gs    int          `json:"gs"`
	Gm    uint64       `json:"gm"`
	Fix   bool         `json:"fix"`
	New   bool         `json:"new"`
	SrcCh bool         `json:"srcch,omitempty"`
	V     float32      `json:"v"`
	Lat   float64      `json:"lat,omitempty"`
	Lon   float64      `json:"lon,omitempty"`
	Spd   float32      `json:"spd,omitempty"`
	Brg   float32      `json:"brg,omitempty"`
	Ha    float32      `json:"ha,omitempty"`
	Ba    float32      `json:"ba,omitempty"`
	Sa    float32      `json:"sa,omitempty"`
	Dev   string       `json:"dev"`
	Dway  int64        `json:"dway"`
	Dlim  float32      `json:"dlim"`
	Sim   string       `json:"sim"`
	Way   int64        `json:"way"`
	Lim   float32      `json:"lim"`
	Name  string       `json:"name,omitempty"`
	Us    float64      `json:"us,omitempty"`
	Load  bool         `json:"load,omitempty"`
	Why   string       `json:"why,omitempty"`
	Cands []replayCand `json:"cands,omitempty"`
	Pick  int64        `json:"pick,omitempty"`
	PickL float32      `json:"pickl,omitempty"`
	Nav   string       `json:"nav,omitempty"`
	R3    float32      `json:"r3,omitempty"`
	R3ok  bool         `json:"r3ok,omitempty"`
	Br    int          `json:"br"`
	Dbr   int          `json:"dbr"`
	// Offline truth at the frame time; Tw is the way (0 off the map, -1 none).
	// Tfw/Tfl are the truth at the time of the fix the frame used.
	Tw  int64   `json:"tw"`
	Tl  float32 `json:"tl"`
	Tn  string  `json:"tn,omitempty"`
	Tsw bool    `json:"tsw"`
	Tsl bool    `json:"tsl"`
	Tfw int64   `json:"tfw"`
	Tfl float32 `json:"tfl"`
	Ctx string  `json:"ctx,omitempty"`
	Jd  float32 `json:"jd,omitempty"`
}

func (f *replayFrame) matched() bool {
	return f.Sim == "matchedLimit" || f.Sim == "matchedNoLimit"
}

func replayCandidates(o *maps.Offline, loc log.GpsLocationData) []replayCand {
	cands := []replayCand{}
	for _, w := range plausibleLoadedWays(o, loc) {
		ow, _ := w.OnWay(loc, 2)
		nar, _ := w.OnWay(loc, w.DistanceMultiplier())
		s, _ := w.BearingAlignment(loc)
		cw := CurrentWay{Way: w, OnWay: ow}
		cands = append(cands, replayCand{Id: w.Id(), Lim: float32(cw.MaxSpeed()), Key: w.WayName() + "\x00" + w.WayRef(),
			Sin: s, D: ow.Distance.Distance, Nar: nar.OnWay, Name: w.Name()})
	}
	return cands
}

var replayNavReasons = [...]string{"ok", "no-candidates", "heading-gate", "slow-not-held", "margin", "narrow-failed"}

func replayTileLoader(tileDir string) MapLoader {
	return func(p m.Position) (maps.Offline, error) { return maps.FindWaysAroundPositionIn(p, tileDir) }
}

// runReplay feeds the dump through State.ProcessGps frame by frame at the
// logged loop times, reusing each fix and feeding carState like the car did.
func runReplay(d *replayDump, load MapLoader, diag bool) ([]replayFrame, error) {
	if ms.Settings.ConditionalSpeedLimitControlEnabled {
		return nil, fmt.Errorf("conditional speed limits read the wall clock; replay needs them off")
	}
	unsupported := 0
	for _, fr := range d.frames {
		if fr.Gs == int(cereal.GpsSourceCar) || fr.Gs > int(cereal.GpsSourcePhone) {
			unsupported++
		}
	}
	if unsupported > 0 {
		return nil, fmt.Errorf("%d frames use a GPS source the dump does not carry (car GPS)", unsupported)
	}
	s := &State{}
	s.Init()
	prevGs, prevGm, gen := -1, uint64(0), uint64(0)
	ci, lastCar := 0, -1
	var last replayFrame
	out := make([]replayFrame, 0, len(d.frames))
	for _, fr := range d.frames {
		for ci+1 < len(d.cs) && d.cs[ci+1].Lm <= fr.Lm {
			ci++
		}
		vEgo := float32(0)
		if len(d.cs) > 0 && d.cs[ci].Lm <= fr.Lm {
			vEgo = d.cs[ci].V
			if ci != lastCar {
				s.UpdateCarState(replayCarState(d.cs[ci]))
				lastCar = ci
			}
		}
		cm := fr.Cm
		s.bootNow = func() uint64 { return cm }
		now := time.Unix(0, int64(fr.Lm))
		var fix replayRow
		found := false
		switch fr.Gs {
		case int(cereal.GpsSourceExternal):
			fix, found = nearestFix(d.ext, func(r replayRow) uint64 { return r.Lm }, fr.Gm)
		case int(cereal.GpsSourceInternal):
			fix, found = nearestFix(d.internal, func(r replayRow) uint64 { return r.Lm }, fr.Gm)
		case int(cereal.GpsSourcePhone):
			fix, found = nearestFix(d.ph, func(r replayRow) uint64 { return r.Src }, fr.Gm)
		}
		changed := fr.Gs != prevGs
		if changed {
			gen++
		}
		newFix := fr.Gm != prevGm || changed
		rec := replayFrame{T: math.Round(float64(fr.Lm-d.start)/1e7) / 100, Lm: fr.Lm, Gs: fr.Gs, Gm: fr.Gm, Fix: found,
			New: newFix && found, SrcCh: changed, V: vEgo, Dev: fr.St, Dway: fr.Way, Dlim: fr.Lim, Dbr: len(fr.Br), Tw: -1, Tfw: -1}
		if !found {
			s.ProcessGps(cereal.GpsSample{Source: cereal.GpsSource(fr.Gs), SourceChanged: changed, SourceGeneration: gen}, false, load, now)
		} else {
			loc := replayLocation(fix)
			priorWay, priorNext, held, loadedAt := s.CurrentWay, s.NextWays, s.navigationHold, s.lastMapLoadAttempt
			priorTrack := s.track
			priorTrack.nodes, priorTrack.ahead = slices.Clone(s.track.nodes), slices.Clone(s.track.ahead)
			began := time.Now()
			s.ProcessGps(cereal.GpsSample{Location: loc, Source: cereal.GpsSource(fr.Gs), FixMonoTime: fr.Gm,
				SourceGeneration: gen, SourceChanged: changed, NewFix: newFix}, true, load, now)
			elapsed := time.Since(began)
			if s.RoadMatched() {
				s.UpdateRoadDependentCar()
			}
			rec.Lat, rec.Lon, rec.Spd, rec.Brg, rec.Ha, rec.Ba, rec.Sa = fix.Lat, fix.Lon, fix.V, fix.B, fix.Ha, fix.Ba, fix.Sa
			if newFix {
				if !changed {
					rec.Us = float64(elapsed.Nanoseconds()) / 1e3
					rec.Load = s.lastMapLoadAttempt != loadedAt
				}
				if diag && !s.RoadMatched() && s.Data.Loaded {
					// Diagnostics run after the timed call, from the state it started with.
					if _, gerr := GetCurrentWay(priorWay, priorNext, &s.Data, loc, &priorTrack, fr.Gm); gerr != nil {
						rec.Why = gerr.Error()
					}
					rec.Cands = replayCandidates(&s.Data, loc)
					pick, reason := navigationPick(&s.Data, loc, held)
					rec.Nav = replayNavReasons[reason]
					if reason == navigationPicked {
						rec.Pick, rec.PickL = pick.Way.Id(), float32(pick.MaxSpeed())
					}
				}
			} else if diag && !s.RoadMatched() && last.Gm == fr.Gm {
				rec.Why, rec.Cands, rec.Nav, rec.Pick, rec.PickL = last.Why, last.Cands, last.Nav, last.Pick, last.PickL
			}
		}
		prevGs, prevGm = fr.Gs, fr.Gm
		s.expireGpsEvidenceAt(cm)
		rec.Sim = s.RoadStatus.String()
		rec.Br = len(s.NavigationBranches)
		if s.RoadMatched() {
			rec.Way, rec.Lim, rec.Name = s.CurrentWay.Way.Id(), s.MatchedLimitMps, s.CurrentWay.Way.Name()
		}
		out = append(out, rec)
		last = rec
	}
	return out, nil
}

// replayCost reruns the replay and keeps each fix's fastest ProcessGps time.
func replayCost(d *replayDump, load MapLoader, frames []replayFrame, repeats int) ([]perfStats, error) {
	runs := []perfStats{perfOf(frames)}
	for range repeats - 1 {
		again, err := runReplay(d, load, false)
		if err != nil {
			return nil, err
		}
		runs = append(runs, perfOf(again))
		for i := range frames {
			if again[i].Us > 0 && again[i].Us < frames[i].Us {
				frames[i].Us = again[i].Us
			}
		}
	}
	return runs, nil
}

// replayGate compares the replayed output with what the car published.
type replayGate struct {
	Frames     int     `json:"frames"`
	Mismatches int     `json:"mismatches"`
	Agree      float64 `json:"agree"`
	MinAgree   float64 `json:"min_agree"`
	Enforced   bool    `json:"enforced"`
	Pass       bool    `json:"pass"`
	First      string  `json:"first_mismatch,omitempty"`
}

func computeGate(frames []replayFrame, minAgree float64, enforced bool) replayGate {
	g := replayGate{Frames: len(frames), MinAgree: minAgree, Enforced: enforced}
	for _, f := range frames {
		if f.Sim != f.Dev || f.Way != f.Dway || math.Abs(float64(f.Lim-f.Dlim)) > 0.01 {
			g.Mismatches++
			if g.First == "" {
				g.First = fmt.Sprintf("t=%.2f car=%s/%d/%.2f replay=%s/%d/%.2f", f.T, f.Dev, f.Dway, f.Dlim, f.Sim, f.Way, f.Lim)
			}
		}
	}
	if g.Frames > 0 {
		g.Agree = 1 - float64(g.Mismatches)/float64(g.Frames)
	}
	g.Pass = g.Frames > 0 && g.Agree >= minAgree
	return g
}

func envFloat(name string, def float64) float64 {
	if v, err := strconv.ParseFloat(os.Getenv(name), 64); err == nil {
		return v
	}
	return def
}

func writeJSON(path string, v any) error {
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, append(b, '\n'), 0o644)
}

// TestReplayDrive replays one dumped drive on real tiles, builds the offline
// truth, and writes a frames file, a JSON summary and a printed report.
//
//	REPLAY        dump written by openpilot/starpilot/navigation/replay/mapd_dump.py
//	REPLAY_TILES  offline tile root
//	REPLAY_OUT    frames file (default: REPLAY with .frames.jsonl)
//	REPLAY_GATE   "off" for a changed matcher: agreement with the car is reported, not enforced
func TestReplayDrive(t *testing.T) {
	path := os.Getenv("REPLAY")
	if path == "" {
		t.Skip("REPLAY not set")
	}
	ms.Settings.Default()
	d, err := loadReplayDump(path)
	if err != nil {
		t.Fatal(err)
	}
	tileDir := os.Getenv("REPLAY_TILES")
	frames, err := runReplay(d, replayTileLoader(tileDir), true)
	if err != nil {
		t.Fatal(err)
	}
	costRuns, err := replayCost(d, replayTileLoader(tileDir), frames, int(envFloat("REPLAY_COST_REPEATS", 3)))
	if err != nil {
		t.Fatal(err)
	}
	enforced := os.Getenv("REPLAY_GATE") != "off"
	gate := computeGate(frames, envFloat("REPLAY_MIN_AGREE", 1), enforced)

	tm, err := loadTruthMap(d, tileDir)
	if err != nil {
		t.Fatal(err)
	}
	truth := buildTruth(d, tm, truthObservations(d, -1))
	quality := truth.apply(frames)
	reachabilityEstimate(tm, newOdometer(d.cs), frames)
	quality.Robustness = truthRobustness(d, tm, frames)
	quality.Guidance = checkGuidance(d, truth)
	quality.Odometry = checkOdometry(truth)
	quality.Yaw = checkYaw(d)

	drive := strings.TrimSuffix(filepath.Base(path), ".jsonl")
	hdr := replayHeader{K: "hdr", Drive: drive, Dump: d.hash, Truth: truthHash(frames), Gate: gate, Quality: quality, CostRuns: costRuns}
	out := os.Getenv("REPLAY_OUT")
	if out == "" {
		out = strings.TrimSuffix(path, ".jsonl") + ".frames.jsonl"
	}
	if err := writeFrames(out, hdr, frames); err != nil {
		t.Fatal(err)
	}
	drives := []replayDrive{{hdr, frames}}
	summary := summarize(drives)
	if err := writeJSON(strings.TrimSuffix(out, ".frames.jsonl")+".summary.json", summary); err != nil {
		t.Fatal(err)
	}
	report := &strings.Builder{}
	printReport(report, summary, drives)
	fmt.Fprint(os.Stderr, report.String())
	if enforced && !gate.Pass {
		t.Fatalf("REPRODUCTION GATE FAILED: replay agrees with the car on %.4f of frames (%d mismatches, minimum %.4f); first %s",
			gate.Agree, gate.Mismatches, gate.MinAgree, gate.First)
	}
}

// TestReplayReport prints the combined report for frames files written by
// TestReplayDrive: REPLAY_FRAMES=a.frames.jsonl,b.frames.jsonl.
func TestReplayReport(t *testing.T) {
	list := os.Getenv("REPLAY_FRAMES")
	if list == "" {
		t.Skip("REPLAY_FRAMES not set")
	}
	drives, err := readDrives(list)
	if err != nil {
		t.Fatal(err)
	}
	summary := summarize(drives)
	if out := os.Getenv("REPLAY_SUMMARY"); out != "" {
		if err := writeJSON(out, summary); err != nil {
			t.Fatal(err)
		}
	}
	report := &strings.Builder{}
	printReport(report, summary, drives)
	fmt.Fprint(os.Stderr, report.String())
}

// TestReplayCompare compares a candidate matcher with a baseline over the
// same drives: REPLAY_BASE and REPLAY_CAND are comma-separated frames files
// in the same drive order. It fails when a compareThresholds limit is
// crossed; REPLAY_COMPARE_OUT receives the machine-readable verdict.
func TestReplayCompare(t *testing.T) {
	baseList, candList := os.Getenv("REPLAY_BASE"), os.Getenv("REPLAY_CAND")
	if baseList == "" || candList == "" {
		t.Skip("REPLAY_BASE and REPLAY_CAND not set")
	}
	base, err := readDrives(baseList)
	if err != nil {
		t.Fatal(err)
	}
	cand, err := readDrives(candList)
	if err != nil {
		t.Fatal(err)
	}
	result, err := compareDrives(base, cand, compareThresholdsFromEnv())
	if err != nil {
		t.Fatal(err)
	}
	report := &strings.Builder{}
	printComparison(report, result)
	fmt.Fprint(os.Stderr, report.String())
	if out := os.Getenv("REPLAY_COMPARE_OUT"); out != "" {
		if err := writeJSON(out, result); err != nil {
			t.Fatal(err)
		}
	}
	if !result.Pass {
		t.Fatalf("candidate fails: %s", strings.Join(result.Failures, "; "))
	}
}

// TestReplayWayNodes lists, for each node of the given ways, the other ways
// ending or passing through it: REPLAY_WAYS=lat,lon,wayID[,wayID...].
func TestReplayWayNodes(t *testing.T) {
	spec := os.Getenv("REPLAY_WAYS")
	if spec == "" {
		t.Skip()
	}
	ms.Settings.Default()
	parts := strings.Split(spec, ",")
	lat, _ := strconv.ParseFloat(parts[0], 64)
	lon, _ := strconv.ParseFloat(parts[1], 64)
	o, err := maps.FindWaysAroundPositionIn(m.NewPosition(lat, lon), os.Getenv("REPLAY_TILES"))
	if err != nil {
		t.Fatal(err)
	}
	for _, ids := range parts[2:] {
		id, _ := strconv.ParseInt(ids, 10, 64)
		for i := 0; i < o.Ways.Len(); i++ {
			w := o.Ways.At(i)
			if w.Id() != id {
				continue
			}
			fmt.Printf("way %d %q %s oneway=%v nodes=%d\n", id, w.Name(), w.HighwayClass(), w.OneWay(), w.Nodes.Len())
			for n := 0; n < w.Nodes.Len(); n++ {
				node := w.Nodes.At(n)
				ends := []string{}
				interior := []string{}
				for j := 0; j < o.Ways.Len(); j++ {
					x := o.Ways.At(j)
					if x.Id() == id || !func() bool { b := x.Box(); return b.PosInside(node) }() {
						continue
					}
					for k := 0; k < x.Nodes.Len(); k++ {
						if p := x.Nodes.At(k); p.Equals(node) {
							s := fmt.Sprintf("%d:%q", x.Id(), x.Name())
							if k == 0 || k == x.Nodes.Len()-1 {
								ends = append(ends, s)
							} else {
								interior = append(interior, s)
							}
						}
					}
				}
				fmt.Printf("  node %d %.6f,%.6f ends=%v interior=%v\n", n, node.Lat(), node.Lon(), ends, interior)
			}
		}
	}
}
