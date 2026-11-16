// Drive replay harness, skipped unless REPLAY or REPLAY_WAYS is set. Input is
// a JSONL dump of rlog fixes and mapdOut frames; tiles come from REPLAY_TILES.
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"sort"
	"strconv"
	"strings"
	"testing"
	"time"

	"capnproto.org/go/capnp/v3"
	"pfeifer.dev/mapd/cereal"
	"pfeifer.dev/mapd/cereal/log"
	"pfeifer.dev/mapd/maps"
	m "pfeifer.dev/mapd/math"
	ms "pfeifer.dev/mapd/settings"
)

type replayRow struct {
	K     string  `json:"k"`
	Lm    uint64  `json:"lm"`
	Src   uint64  `json:"src"`
	Valid bool    `json:"valid"`
	Fix   bool    `json:"fix"`
	Lat   float64 `json:"lat"`
	Lon   float64 `json:"lon"`
	V     float32 `json:"v"`
	B     float32 `json:"b"`
	Ha    float32 `json:"ha"`
	Ba    float32 `json:"ba"`
	Sa    float32 `json:"sa"`
	Alt   float64 `json:"alt"`
	St    string  `json:"st"`
	Gs    int     `json:"gs"`
	Gm    uint64  `json:"gm"`
	Cm    uint64  `json:"cm"`
	Way   int64   `json:"way"`
	Road  string  `json:"road"`
	Lim   float32 `json:"lim"`
	Br    []struct {
		Id int64   `json:"id"`
		N  string  `json:"n"`
		D  float32 `json:"d"`
		A  float32 `json:"a"`
	} `json:"br"`
}

func replayLocation(t *testing.T, r replayRow) log.GpsLocationData {
	_, seg, err := capnp.NewMessage(capnp.SingleSegment(nil))
	if err != nil {
		t.Fatal(err)
	}
	l, err := log.NewRootGpsLocationData(seg)
	if err != nil {
		t.Fatal(err)
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

func replayNavDiag(o *maps.Offline, loc log.GpsLocationData, held navigationHold) string {
	_, r := navigationPick(o, loc, held)
	return [...]string{"nav:ok", "nav:no-candidates", "nav:heading-gate", "nav:slow-not-held", "nav:margin", "nav:narrow-failed"}[r]
}

func replayNearest(o *maps.Offline, loc log.GpsLocationData) string {
	pos := m.NewPosition(loc.Latitude(), loc.Longitude())
	type c struct {
		w    maps.Way
		d    float32
		fw   bool
		sine float32
	}
	cs := []c{}
	idx, ok := o.WaysNear(pos)
	n := len(idx)
	if !ok {
		n = o.Ways.Len()
	}
	for k := range n {
		i := k
		if ok {
			i = int(idx[k])
		}
		w := o.Ways.At(i)
		if w.Nodes.Len() < 2 {
			continue
		}
		d, err := w.DistanceFrom(pos)
		if err != nil || d.Distance > 60 {
			continue
		}
		s, _ := w.BearingAlignment(loc)
		cs = append(cs, c{w, d.Distance, maps.IsForward(d.LineStart, d.LineEnd, float64(loc.BearingDeg())), s})
	}
	sort.Slice(cs, func(i, j int) bool { return cs[i].d < cs[j].d })
	parts := []string{}
	for i, x := range cs {
		if i >= 4 {
			break
		}
		lim := maps.MatchDistance(loc, x.w.Width(), 2)
		parts = append(parts, fmt.Sprintf("%d:%q/%s d=%.1f env2=%.1f nar=%.1f ow=%v fw=%v sin=%.2f", x.w.Id(), x.w.Name(), x.w.HighwayClass(), x.d, lim, maps.MatchDistance(loc, x.w.Width(), x.w.DistanceMultiplier()), x.w.OneWay(), x.fw, x.sine))
	}
	return strings.Join(parts, " | ")
}

func replayCandidates(o *maps.Offline, loc log.GpsLocationData) string {
	parts := []string{}
	for _, w := range plausibleLoadedWays(o, loc) {
		ow, _ := w.OnWay(loc, 2)
		nar, _ := w.OnWay(loc, w.DistanceMultiplier())
		s, _ := w.BearingAlignment(loc)
		parts = append(parts, fmt.Sprintf("%d:%q/%s d=%.1f nar=%v sin=%.2f", w.Id(), w.Name(), w.HighwayClass(), ow.Distance.Distance, nar.OnWay, s))
	}
	return strings.Join(parts, " | ")
}

// TestReplayDrive replays a dumped drive through
// State.ProcessGps on the real tiles and writes one JSON line per mapdOut frame.
func TestReplayDrive(t *testing.T) {
	path := os.Getenv("REPLAY")
	if path == "" {
		t.Skip("REPLAY not set")
	}
	ms.Settings.Default()
	f, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	var ext, ph, frames, cs []replayRow
	var start uint64
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 1<<20), 1<<24)
	for sc.Scan() {
		var r replayRow
		if err := json.Unmarshal(sc.Bytes(), &r); err != nil {
			t.Fatal(err)
		}
		switch r.K {
		case "ext":
			ext = append(ext, r)
		case "ph":
			if len(ph) == 0 || ph[len(ph)-1].Src != r.Src {
				ph = append(ph, r)
			}
		case "map":
			frames = append(frames, r)
		case "cs":
			cs = append(cs, r)
		case "start":
			start = r.Lm
		}
	}
	sort.Slice(ph, func(i, j int) bool { return ph[i].Src < ph[j].Src })
	out, err := os.Create(os.Getenv("REPLAY_OUT"))
	if err != nil {
		t.Fatal(err)
	}
	defer out.Close()
	w := bufio.NewWriter(out)
	defer w.Flush()

	s := &State{}
	s.Init()
	tileDir := os.Getenv("REPLAY_TILES")
	load := func(p m.Position) (maps.Offline, error) { return maps.FindWaysAroundPositionIn(p, tileDir) }
	prevGs, prevGm, gen := -1, uint64(0), uint64(0)
	ci := 0
	navSrc, navName := "", ""
	durations := []time.Duration{}
	defer func() {
		sort.Slice(durations, func(i, j int) bool { return durations[i] < durations[j] })
		if n := len(durations); n > 0 {
			total := time.Duration(0)
			for _, d := range durations {
				total += d
			}
			fmt.Fprintf(os.Stderr, "PERF fixes=%d mean=%v p99=%v max=%v\n", n, total/time.Duration(n), durations[n*99/100], durations[n-1])
		}
	}()
	for _, fr := range frames {
		for ci+1 < len(cs) && cs[ci+1].Lm <= fr.Lm {
			ci++
		}
		vEgo := float32(0)
		if len(cs) > 0 {
			vEgo = cs[ci].V
		}
		tsec := float64(fr.Lm-start) / 1e9
		cm := fr.Cm
		s.bootNow = func() uint64 { return cm }
		now := time.Unix(0, int64(fr.Lm))
		var fix replayRow
		found := false
		switch fr.Gs {
		case 2:
			fix, found = nearestFix(ext, func(r replayRow) uint64 { return r.Lm }, fr.Gm)
		case 4:
			fix, found = nearestFix(ph, func(r replayRow) uint64 { return r.Src }, fr.Gm)
		}
		changed := fr.Gs != prevGs
		if changed {
			gen++
		}
		newFix := fr.Gm != prevGm || changed
		reason, cands, nav, near := "", "", "", ""
		if !found {
			s.ProcessGps(cereal.GpsSample{Source: cereal.GpsSource(fr.Gs), SourceChanged: changed, SourceGeneration: gen}, false, load, now)
		} else {
			loc := replayLocation(t, fix)
			if newFix && s.Data.Loaded {
				held := s.navigationHold
				_, gerr := GetCurrentWay(s.CurrentWay, s.NextWays, &s.Data, loc)
				if gerr != nil {
					reason = gerr.Error()
					cands = replayCandidates(&s.Data, loc)
					nav = replayNavDiag(&s.Data, loc, held)
					near = replayNearest(&s.Data, loc)
				}
			}
			began := time.Now()
			s.ProcessGps(cereal.GpsSample{Location: loc, Source: cereal.GpsSource(fr.Gs), FixMonoTime: fr.Gm,
				SourceGeneration: gen, SourceChanged: changed, NewFix: newFix}, true, load, now)
			if newFix && !changed {
				durations = append(durations, time.Since(began))
			}
			if s.RoadMatched() {
				s.UpdateRoadDependentCar()
			}
			if newFix {
				switch {
				case s.RoadMatched():
					navSrc, navName = "match", s.CurrentWay.Way.WayName()+"/"+s.CurrentWay.Way.WayRef()
				case nav == "nav:ok":
					navSrc, navName = "pick", s.navigationHold.name+"/"+s.navigationHold.ref
				case len(s.NavigationBranches) > 0:
					navSrc, navName = "approach", ""
				default:
					navSrc, navName = "", ""
				}
			}
		}
		prevGs, prevGm = fr.Gs, fr.Gm
		s.expireGpsEvidenceAt(cm)
		brs := []string{}
		for _, b := range s.NavigationBranches {
			brs = append(brs, fmt.Sprintf("%s@%.0f/%.0f", b.way.Name(), b.distance, b.angle))
		}
		dbrs := []string{}
		for _, b := range fr.Br {
			dbrs = append(dbrs, fmt.Sprintf("%s@%.0f/%.0f", b.N, b.D, b.A))
		}
		way := int64(0)
		lim := float32(0)
		if s.RoadMatched() {
			way, lim = s.CurrentWay.Way.Id(), s.MatchedLimitMps
		}
		rec := map[string]any{"t": math.Round(tsec*100) / 100, "v": vEgo, "gs": fr.Gs, "new": newFix && found,
			"dev": fr.St, "dway": fr.Way, "dlim": fr.Lim, "dbr": len(fr.Br),
			"sim": s.RoadStatus.String(), "way": way, "lim": lim, "navway": s.navigationHold.id, "br": len(s.NavigationBranches), "src": navSrc, "name": navName}
		if newFix && found {
			rec["lat"], rec["lon"], rec["spd"], rec["brg"], rec["ha"], rec["ba"] = fix.Lat, fix.Lon, fix.V, fix.B, fix.Ha, fix.Ba
			rec["brs"] = strings.Join(brs, ",")
			rec["dbrs"] = strings.Join(dbrs, ",")
		}
		if reason != "" {
			rec["why"], rec["cands"], rec["nav"], rec["near"] = reason, cands, nav, near
		}
		b, _ := json.Marshal(rec)
		w.Write(b)
		w.WriteString("\n")
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
