package main

import (
	"math"
	"strings"
	"testing"

	"pfeifer.dev/mapd/maps"
	m "pfeifer.dev/mapd/math"
	ms "pfeifer.dev/mapd/settings"
)

const metresPerDegLat = 111195.0

// syntheticDrive drives path at speed with 1 Hz comma fixes (deterministic
// lateral noise) and 20 Hz carState, as a dump would carry them.
func syntheticDrive(path [][2]float64, speed float32, noise float64) *replayDump {
	d := &replayDump{start: 1_000_000_000}
	kx := metresPerDegLat * math.Cos(35*math.Pi/180)
	type leg struct{ a, b [2]float64 }
	legs := []leg{}
	for i := 1; i < len(path); i++ {
		legs = append(legs, leg{path[i-1], path[i]})
	}
	t := d.start
	for _, l := range legs {
		dy, dx := (l.b[0]-l.a[0])*metresPerDegLat, (l.b[1]-l.a[1])*kx
		length := math.Hypot(dx, dy)
		bearing := math.Mod(math.Atan2(dx, dy)*180/math.Pi+360, 360)
		for s := 0.0; s < length; s += float64(speed) {
			k := float64(len(d.ext))
			off := noise * math.Sin(k*1.7)
			x, y := s*dx/length+off*dy/length, s*dy/length-off*dx/length
			d.ext = append(d.ext, replayRow{K: "ext", Lm: t, Fix: true, Valid: true, Lat: l.a[0] + y/metresPerDegLat, Lon: l.a[1] + x/kx,
				V: speed, B: float32(bearing), Ha: 3, Ba: 2, Sa: 0.5})
			for c := uint64(0); c < 20; c++ {
				d.cs = append(d.cs, replayRow{K: "cs", Lm: t + c*50_000_000, V: speed})
			}
			t += 1_000_000_000
		}
	}
	return d
}

func harnessTile(t *testing.T) maps.Offline {
	return namedRoadTile(t, []namedWay{
		{syntheticWay{1, 20, 35.100, -97.9000, 35.110, -97.9000, false, 2}, "Main St"},
		{syntheticWay{2, 20, 35.110, -97.9000, 35.115, -97.9000, false, 2}, "Main St"},
		{syntheticWay{3, 22, 35.115, -97.9000, 35.120, -97.9000, false, 2}, "Main St"},
		{syntheticWay{5, 15, 35.115, -97.9100, 35.115, -97.9000, false, 2}, "Cross Ave"},
		{syntheticWay{6, 15, 35.115, -97.9000, 35.115, -97.8900, false, 2}, "Cross Ave"},
		{syntheticWay{7, 25, 35.100, -97.8999, 35.110, -97.8999, true, 1}, "Ramp"},
	})
}

func truthAtLat(t *testing.T, tr *truthResult, d *replayDump, lat float64, lonAbove float64) truthPoint {
	t.Helper()
	for _, r := range d.ext {
		if r.Lat >= lat && r.Lon >= lonAbove {
			return tr.at(r.Lm)
		}
	}
	t.Fatalf("no fix past %f", lat)
	return truthPoint{}
}

func TestTruthFollowsGraphPastParallelRampAndTurn(t *testing.T) {
	ms.Settings.Default()
	tile := harnessTile(t)
	tm := truthMapFromTile(&tile, 35.11, -97.9)
	d := syntheticDrive([][2]float64{{35.1010, -97.9000}, {35.1150, -97.9000}, {35.1150, -97.8930}}, 10, 1.5)
	tr := buildTruth(d, tm, truthObservations(d, -1))
	for _, tc := range []struct {
		lat, lon float64
		way      int64
		limit    float64
		ctx      string
	}{
		{35.1050, -98, 1, 20, "parallel"},
		{35.1101, -98, 2, 20, "join"},
		{35.1125, -98, 2, 20, "midblock"},
		{35.1149, -98, 2, 20, "junction"},
		{35.1150, -97.8960, 6, 15, "midblock"},
	} {
		p := truthAtLat(t, tr, d, tc.lat, tc.lon)
		if !p.ok || p.way < 0 || tm.ways[p.way].id != tc.way || tm.ways[p.way].limit(p.fwd) != tc.limit {
			t.Fatalf("at %f,%f truth %+v, want way %d limit %.0f", tc.lat, tc.lon, p, tc.way, tc.limit)
		}
		if ctx, _ := tm.context(p); ctx != tc.ctx {
			t.Fatalf("at %f,%f context %s, want %s", tc.lat, tc.lon, ctx, tc.ctx)
		}
		if tc.ctx == "midblock" && (!p.sureWay || !p.sureLimit) {
			t.Fatalf("at %f,%f mid-block truth should be sure: %+v", tc.lat, tc.lon, p)
		}
	}
	for k := range tr.path {
		if tr.path[k].way >= 0 && tm.ways[tr.path[k].way].id == 7 {
			t.Fatal("truth jumped onto the unconnected parallel ramp")
		}
	}
	if q := checkOdometry(tr); q.Pairs == 0 || math.Abs(q.RatioP50-1) > 0.02 {
		t.Fatalf("route length should match wheel distance: %+v", q)
	}
}

func TestTruthIsUnsureBetweenIndistinguishableRoads(t *testing.T) {
	ms.Settings.Default()
	d := syntheticDrive([][2]float64{{35.1010, -97.9}, {35.1090, -97.9}}, 10, 1)
	for _, tc := range []struct {
		otherLimit float64
		limitSure  bool
	}{{25, false}, {20, true}} {
		tile := namedRoadTile(t, []namedWay{
			{syntheticWay{1, 20, 35.100, -97.9, 35.110, -97.9, false, 2}, "Main St"},
			{syntheticWay{2, tc.otherLimit, 35.100, -97.9, 35.110, -97.9, false, 2}, "Other St"},
		})
		tm := truthMapFromTile(&tile, 35.105, -97.9)
		tr := buildTruth(d, tm, truthObservations(d, -1))
		p := tr.at(d.ext[len(d.ext)/2].Lm)
		if !p.ok || p.sureWay || p.sureLimit != tc.limitSure {
			t.Fatalf("other limit %.0f: truth %+v, want way unsure and limit sure=%v", tc.otherLimit, p, tc.limitSure)
		}
	}
}

func TestTruthLeavesTheMapInAParkingLot(t *testing.T) {
	ms.Settings.Default()
	tile := namedRoadTile(t, []namedWay{{syntheticWay{1, 20, 35.100, -97.9, 35.120, -97.9, false, 2}, "Main St"}})
	tm := truthMapFromTile(&tile, 35.11, -97.9)
	d := syntheticDrive([][2]float64{{35.1010, -97.9}, {35.1050, -97.9}, {35.1050, -97.8985}, {35.1065, -97.8985},
		{35.1065, -97.9}, {35.1150, -97.9}}, 5, 1)
	tr := buildTruth(d, tm, truthObservations(d, -1))
	if p := truthAtLat(t, tr, d, 35.1058, -97.8986); !p.ok || p.way != -1 {
		t.Fatalf("parking lot truth %+v, want off the map", p)
	}
	if p := truthAtLat(t, tr, d, 35.1100, -98); !p.ok || p.way < 0 || tm.ways[p.way].id != 1 || !p.sureWay {
		t.Fatalf("back on the road truth %+v", p)
	}
}

func frameWith(edit func(f *replayFrame)) replayFrame {
	f := replayFrame{Fix: true, Sim: "matchedLimit", Way: 1, Lim: 20, Tw: 1, Tl: 20, Tsw: true, Tsl: true, Tfw: 1, Tfl: 20, Ctx: "midblock", Gs: 2, V: 20}
	edit(&f)
	return f
}

func TestReplayCountsSeparateWrongLimitFromHarmlessWrongWay(t *testing.T) {
	frames := []replayFrame{
		frameWith(func(f *replayFrame) {}),
		frameWith(func(f *replayFrame) { f.Way = 2 }),
		frameWith(func(f *replayFrame) { f.Way, f.Lim, f.Tfl = 3, 25, 25 }),
		frameWith(func(f *replayFrame) { f.Way, f.Lim = 3, 25 }),
		frameWith(func(f *replayFrame) { f.Way, f.Lim, f.Tsw, f.Tsl = 3, 25, false, false }),
		frameWith(func(f *replayFrame) { f.Tw, f.Tl = 0, 0 }),
		frameWith(func(f *replayFrame) { f.Sim, f.Way, f.Lim = "unknown", 0, 0 }),
		frameWith(func(f *replayFrame) { f.Fix, f.Sim, f.Way, f.Lim = false, "noGps", 0, 0 }),
	}
	c := computeMetrics([]replayDrive{{replayHeader{Drive: "x"}, frames}}).Overall
	want := replayCounts{Frames: 7, Matched: 6, Limit: 6, TruthRoad: 5, MatchedRoad: 4, TruthLimit: 5, CorrectLimit: 2, LimitScored: 4,
		WrongLimit: 2, WrongLimitLag: 1, WrongWay: 3, WrongWaySameLimit: 1, PubOffRoad: 1, PubUnsure: 1, Unsure: 1}
	c.FlipWay, c.FlipLimit, c.Dropouts = 0, 0, 0
	if c != want {
		t.Fatalf("counts\n got %+v\nwant %+v", c, want)
	}
}

func timeline(spec string) []replayFrame {
	// One frame per 0.1 s; per character: '.' no fix, 'u' unmatched, 'A'/'B' matched way 1/2 (limits 20/25).
	frames := []replayFrame{}
	for i, ch := range spec {
		f := frameWith(func(f *replayFrame) {})
		f.T = float64(i) / 10
		switch ch {
		case '.':
			f.Fix, f.Sim, f.Way, f.Lim = false, "noGps", 0, 0
		case 'u':
			f.Sim, f.Way, f.Lim = "unknown", 0, 0
		case 'B':
			f.Way, f.Lim = 2, 25
		}
		frames = append(frames, f)
	}
	return frames
}

func TestReplayEventsFlipFlopsDropoutsAndFirstMatch(t *testing.T) {
	spec := "..." + strings.Repeat("u", 15) + strings.Repeat("A", 20) + strings.Repeat("B", 10) + strings.Repeat("A", 10) +
		strings.Repeat("u", 10) + strings.Repeat("A", 5) + strings.Repeat("u", 40)
	b := computeMetrics([]replayDrive{{replayHeader{Drive: "x"}, timeline(spec)}})
	if b.Overall.FlipWay != 1 || b.Overall.FlipLimit != 1 || b.Overall.Dropouts != 1 {
		t.Fatalf("events %+v", b.Overall)
	}
	fm := b.FirstMatch["comma"]
	if fm.Events != 1 || fm.Censored != 0 || math.Abs(fm.P50-1.5) > 1e-9 {
		t.Fatalf("first match %+v", fm)
	}
	if len(b.Longest) == 0 || math.Abs(b.Longest[0].Secs-3.9) > 1e-9 {
		t.Fatalf("longest %+v", b.Longest)
	}
}

func TestUnmatchedCausesAndCounterfactualRules(t *testing.T) {
	cand := func(id int64, lim float32, key string, sin float32, d float32) replayCand {
		return replayCand{Id: id, Lim: lim, Key: key, Sin: sin, D: d}
	}
	unknown := func(cands ...replayCand) replayFrame {
		return frameWith(func(f *replayFrame) { f.Sim, f.Way, f.Lim, f.Spd, f.Ba, f.Cands = "unknown", 0, 0, 10, 2, cands })
	}
	for _, tc := range []struct {
		f     replayFrame
		cause string
	}{
		{unknown(), "no-candidates"},
		{unknown(cand(1, 20, "a", 0, 1)), "sole-way-threshold"},
		{unknown(cand(1, 20, "a", 0, 1), cand(2, 20, "a", 0, 3)), "multi:same-name+same-limit"},
		{unknown(cand(1, 20, "a", 0, 1), cand(2, 25, "a", 0, 3)), "multi:same-name,limits-differ"},
		{unknown(cand(1, 20, "a", 0, 1), cand(2, 20, "b", 1, 3)), "multi:same-limit,names-differ"},
		{unknown(cand(1, 20, "a", 0.1, 1), cand(2, 25, "b", 0.99, 3)), "multi:heading-leaves-one"},
		{unknown(cand(1, 20, "a", 0.1, 4), cand(2, 25, "b", 0.1, 3)), "multi:parallel-aligned,differ"},
	} {
		if got := unmatchedCause(&tc.f); got != tc.cause {
			t.Fatalf("cause %q, want %q (%+v)", got, tc.cause, tc.f.Cands)
		}
	}
	// The nearest same-name piece carries the wrong limit: rule 1b is wrong, rule 1a abstains.
	f := unknown(cand(1, 25, "a", 0, 1), cand(2, 20, "a", 0, 3))
	f.Tw, f.Tl = 2, 20
	b := computeMetrics([]replayDrive{{replayHeader{Drive: "x"}, []replayFrame{f}}})
	if r := b.Rules["1b same-name or same-limit (nearest piece)"]; r.Applies != 1 || r.WrongTagged != 1 {
		t.Fatalf("rule 1b %+v", r)
	}
	if r := b.Rules["1a same-limit candidates"]; r.Applies != 0 {
		t.Fatalf("rule 1a %+v", r)
	}
	if eps := b.RuleWrong["1b same-name or same-limit (nearest piece)"]; len(eps) != 1 || eps[0].Lim != 25 {
		t.Fatalf("rule 1b wrong episodes %+v", eps)
	}
}

func TestReplayCompareFailsOnNewWrongLimit(t *testing.T) {
	base := timeline(strings.Repeat("A", 30))
	cand := timeline(strings.Repeat("A", 30))
	for i := 10; i < 15; i++ {
		cand[i].Way, cand[i].Lim = 9, 30
	}
	hdr := replayHeader{Drive: "x", Dump: "d", Truth: "t"}
	r, err := compareDrives([]replayDrive{{hdr, base}}, []replayDrive{{hdr, cand}}, compareThresholds{})
	if err != nil {
		t.Fatal(err)
	}
	if r.Pass || len(r.Regressions) != 1 || r.Regressions[0].Frames != 5 || r.Regressions[0].Lim != 30 || r.Cand.Overall.WrongLimit != 5 {
		t.Fatalf("compare %+v", r)
	}
	if r, _ := compareDrives([]replayDrive{{hdr, base}}, []replayDrive{{hdr, base}}, compareThresholds{}); !r.Pass {
		t.Fatalf("identical runs must pass: %v", r.Failures)
	}
	other := hdr
	other.Truth = "changed"
	if _, err := compareDrives([]replayDrive{{hdr, base}}, []replayDrive{{other, base}}, compareThresholds{}); err == nil {
		t.Fatal("a changed truth must refuse to compare")
	}
}

func TestReplayPipelineReproducesItsOwnOutputThroughTheGate(t *testing.T) {
	ms.Settings.Default()
	tile := namedRoadTile(t, []namedWay{{syntheticWay{1, 20, 35.100, -97.9, 35.120, -97.9, false, 2}, "Main St"}})
	load := func(m.Position) (maps.Offline, error) { return tile, nil }
	d := syntheticDrive([][2]float64{{35.1010, -97.9}, {35.1100, -97.9}}, 10, 1)
	for _, r := range d.ext {
		for k := uint64(0); k < 20; k++ {
			d.frames = append(d.frames, replayRow{K: "map", Lm: r.Lm + k*50_000_000 + 10_000_000, Cm: r.Lm + k*50_000_000 + 10_000_000, Gs: 2, Gm: r.Lm})
		}
	}
	first, err := runReplay(d, load, true)
	if err != nil {
		t.Fatal(err)
	}
	for i, f := range first {
		d.frames[i].St, d.frames[i].Way, d.frames[i].Lim = f.Sim, f.Way, f.Lim
	}
	again, err := runReplay(d, load, true)
	if err != nil {
		t.Fatal(err)
	}
	g := computeGate(again, 1, true)
	mid := again[20*(len(d.ext)/2)]
	if !g.Pass || mid.Sim != "matchedLimit" || mid.Way != 1 {
		t.Fatalf("gate %+v, mid frame %+v", g, mid)
	}
	news := 0
	for _, f := range again {
		if f.Us > 0 {
			news++
		}
	}
	if news < len(d.ext)-2 {
		t.Fatalf("timed %d new fixes of %d", news, len(d.ext))
	}
	d.frames[5].Lim = 30
	if g := computeGate(func() []replayFrame { f, _ := runReplay(d, load, true); return f }(), 1, true); g.Pass || g.Mismatches != 1 {
		t.Fatalf("an altered logged limit must fail the gate: %+v", g)
	}
	d.frames[0].Gs = 3
	if _, err := runReplay(d, load, true); err == nil {
		t.Fatal("car GPS frames must be refused, not replayed as missing fixes")
	}
}

func TestGuidanceRoadNames(t *testing.T) {
	for _, tc := range []struct {
		text, road string
		current    bool
	}{
		{"Stay on I-99", "I-99", true},
		{"Turn left onto Elm Blvd", "Elm Blvd", false},
		{"Exit 12B Oak Canyon Dr", "Oak Canyon Dr", false},
		{"Take the Pine Ave exit", "Pine Ave", false},
		{"Take exit 7 toward Birch St", "Birch St", false},
		{"Arrive at your destination", "", false},
		{"123 Maple Ct", "", false},
	} {
		road, current := guidanceRoad(tc.text)
		if road != tc.road || current != tc.current {
			t.Fatalf("%q -> %q %v", tc.text, road, current)
		}
	}
	w := &truthWay{name: "South Oak Canyon Drive"}
	if !sameRoadName("S Oak Canyon Dr", w) || sameRoadName("Oak Ave", w) {
		t.Fatal("road name normalisation")
	}
	if !sameRoadName("I-99 N / Somewhere", &truthWay{ref: "I 99;US 1"}) {
		t.Fatal("ref match")
	}
}
