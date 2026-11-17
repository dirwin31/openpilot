package main

// Metrics, reports and baseline/candidate comparison for the replay harness.
// Everything here reads frames files only, so any two runs can be compared.

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"os"
	"sort"
	"strings"
	"text/tabwriter"
)

type replayHeader struct {
	K       string       `json:"k"`
	Drive   string       `json:"drive"`
	Dump    string       `json:"dump_hash"`
	Truth   string       `json:"truth_hash"`
	Gate    replayGate   `json:"gate"`
	Quality truthQuality `json:"truth_quality"`
	// ProcessGps cost of each full replay; frames keep each fix's fastest run.
	CostRuns []perfStats `json:"cost_runs"`
}

type replayDrive struct {
	hdr    replayHeader
	frames []replayFrame
}

func writeFrames(path string, hdr replayHeader, frames []replayFrame) error {
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	w := bufio.NewWriter(f)
	enc := json.NewEncoder(w)
	if err := enc.Encode(hdr); err != nil {
		return err
	}
	for i := range frames {
		if err := enc.Encode(&frames[i]); err != nil {
			return err
		}
	}
	return w.Flush()
}

func readDrives(list string) ([]replayDrive, error) {
	out := []replayDrive{}
	for _, path := range strings.Split(list, ",") {
		f, err := os.Open(strings.TrimSpace(path))
		if err != nil {
			return nil, err
		}
		d := replayDrive{}
		sc := bufio.NewScanner(f)
		sc.Buffer(make([]byte, 1<<20), 1<<24)
		first := true
		for sc.Scan() {
			if first {
				first = false
				if err := json.Unmarshal(sc.Bytes(), &d.hdr); err != nil || d.hdr.K != "hdr" {
					f.Close()
					return nil, fmt.Errorf("%s: not a frames file written by TestReplayDrive", path)
				}
				continue
			}
			var fr replayFrame
			if err := json.Unmarshal(sc.Bytes(), &fr); err != nil {
				f.Close()
				return nil, err
			}
			d.frames = append(d.frames, fr)
		}
		f.Close()
		if err := sc.Err(); err != nil {
			return nil, err
		}
		out = append(out, d)
	}
	return out, nil
}

const limitTolerance = 0.1

func sameLimit(a, b float32) bool { return math.Abs(float64(a-b)) <= limitTolerance }

func (f *replayFrame) publishedLimit() bool { return f.Sim == "matchedLimit" && f.Lim > 0 }

// wrongLimit: a limit is published and differs from the sure truth way's limit.
func (f *replayFrame) wrongLimit() bool {
	return f.publishedLimit() && f.Tw > 0 && f.Tsl && !sameLimit(f.Lim, f.Tl)
}

func (f *replayFrame) publishedOffRoad() bool { return f.publishedLimit() && f.Tw == 0 && f.Tsl }

func (f *replayFrame) wrongWay() bool { return f.matched() && f.Tw > 0 && f.Tsw && f.Way != f.Tw }

func speedBand(v float32) string {
	switch {
	case v < 0.5:
		return "stopped"
	case v < 15:
		return "<15"
	case v < 25:
		return "15-25"
	default:
		return ">=25"
	}
}

func sourceName(gs int) string {
	switch gs {
	case 1:
		return "comma-internal"
	case 2:
		return "comma"
	case 3:
		return "car"
	case 4:
		return "phone"
	default:
		return "none"
	}
}

// replayCounts are frame counts for one group; frames without a fix are excluded.
type replayCounts struct {
	Frames            int `json:"frames_with_fix"`
	Matched           int `json:"matched"`
	Limit             int `json:"limit_published"`
	TruthRoad         int `json:"truth_on_road_sure"`
	MatchedRoad       int `json:"matched_where_truth_on_road"`
	TruthLimit        int `json:"truth_has_limit_sure"`
	CorrectLimit      int `json:"correct_limit"`
	LimitScored       int `json:"limit_published_scored"`
	WrongLimit        int `json:"wrong_limit"`
	WrongLimitLag     int `json:"wrong_limit_fix_lag"`
	WrongLimitNoTag   int `json:"wrong_limit_truth_has_no_limit"`
	WrongWay          int `json:"wrong_way"`
	WrongWaySameLimit int `json:"wrong_way_same_limit"`
	PubOffRoad        int `json:"limit_published_off_road"`
	PubUnsure         int `json:"limit_published_truth_unsure"`
	Unsure            int `json:"truth_unsure_or_missing"`
	FlipWay           int `json:"way_flip_flops"`
	FlipLimit         int `json:"limit_flip_flops"`
	Dropouts          int `json:"dropouts"`
}

func (c *replayCounts) add(f *replayFrame) {
	if !f.Fix {
		return
	}
	c.Frames++
	if f.matched() {
		c.Matched++
	}
	if f.publishedLimit() {
		c.Limit++
	}
	if f.Tw > 0 && f.Tsw {
		c.TruthRoad++
		if f.matched() {
			c.MatchedRoad++
		}
	}
	if f.Tw < 0 || !f.Tsl {
		c.Unsure++
	}
	if f.Tw > 0 && f.Tsl && f.Tl > 0 {
		c.TruthLimit++
		if f.publishedLimit() && sameLimit(f.Lim, f.Tl) {
			c.CorrectLimit++
		}
	}
	if f.publishedLimit() {
		switch {
		case f.Tw > 0 && f.Tsl:
			c.LimitScored++
		case f.Tw == 0 && f.Tsl:
			c.PubOffRoad++
		default:
			c.PubUnsure++
		}
	}
	if f.wrongLimit() {
		c.WrongLimit++
		if f.Tfw > 0 && sameLimit(f.Lim, f.Tfl) {
			c.WrongLimitLag++
		}
		if f.Tl == 0 {
			c.WrongLimitNoTag++
		}
	}
	if f.wrongWay() {
		c.WrongWay++
		if sameLimit(f.Lim, f.Tl) {
			c.WrongWaySameLimit++
		}
	}
}

func (c *replayCounts) merge(o replayCounts) {
	c.Frames += o.Frames
	c.Matched += o.Matched
	c.Limit += o.Limit
	c.TruthRoad += o.TruthRoad
	c.MatchedRoad += o.MatchedRoad
	c.TruthLimit += o.TruthLimit
	c.CorrectLimit += o.CorrectLimit
	c.LimitScored += o.LimitScored
	c.WrongLimit += o.WrongLimit
	c.WrongLimitLag += o.WrongLimitLag
	c.WrongLimitNoTag += o.WrongLimitNoTag
	c.WrongWay += o.WrongWay
	c.WrongWaySameLimit += o.WrongWaySameLimit
	c.PubOffRoad += o.PubOffRoad
	c.PubUnsure += o.PubUnsure
	c.Unsure += o.Unsure
	c.FlipWay += o.FlipWay
	c.FlipLimit += o.FlipLimit
	c.Dropouts += o.Dropouts
}

func ratio(a, b int) float64 {
	if b == 0 {
		return math.NaN()
	}
	return float64(a) / float64(b)
}

// ruleCounts score a counterfactual rule on unmatched frames against truth.
type ruleCounts struct {
	Applies       int `json:"applies"`
	Right         int `json:"right_limit"`
	NoLimit       int `json:"no_limit_published"`
	WrongTagged   int `json:"wrong_limit_vs_tagged_truth"`
	WrongUntagged int `json:"limit_where_truth_has_none"`
	Unscored      int `json:"truth_unsure"`
}

func (r *ruleCounts) score(f *replayFrame, limit float32) {
	r.Applies++
	switch {
	case f.Tw < 0 || !f.Tsl:
		r.Unscored++
	case limit <= 0 && f.Tw > 0 && f.Tl > 0:
		r.NoLimit++
	case limit <= 0 || (f.Tw > 0 && sameLimit(limit, f.Tl)):
		r.Right++
	case f.Tw > 0 && f.Tl > 0:
		r.WrongTagged++
	default:
		r.WrongUntagged++
	}
}

// ruleWrong: the rule would publish a limit that the sure truth contradicts.
func ruleWrong(f *replayFrame, limit float32) bool {
	return f.Tw >= 0 && f.Tsl && limit > 0 && !(f.Tw > 0 && sameLimit(limit, f.Tl))
}

const headingSine = 0.5735764363510461 // sin(35 deg), the navigation picker's gate

func accuracyGated(f *replayFrame) bool {
	ba, sa := float64(f.Ba), float64(f.Sa)
	return (f.Gs == 3 || f.Gs == 4) && (math.IsNaN(ba) || math.IsInf(ba, 0) || ba <= 0 || ba >= 180 || math.IsNaN(sa) || math.IsInf(sa, 0) || sa >= 100)
}

func allSame[T any](cands []replayCand, key func(replayCand) T, eq func(a, b T) bool) bool {
	for _, c := range cands[1:] {
		if !eq(key(c), key(cands[0])) {
			return false
		}
	}
	return true
}

func sameNamed(cands []replayCand) bool {
	return cands[0].Key != "\x00" && allSame(cands, func(c replayCand) string { return c.Key }, func(a, b string) bool { return a == b })
}

func sameLimited(cands []replayCand) bool {
	return allSame(cands, func(c replayCand) float32 { return c.Lim }, sameLimit)
}

// unmatchedCause says why a frame with a fix was not matched.
func unmatchedCause(f *replayFrame) string {
	switch {
	case f.Sim == "noCoverage":
		return "no-coverage"
	case f.Sim == "noGps":
		return "fix-expired"
	case f.Sim == "noMatch" && accuracyGated(f):
		return "accuracy-gate"
	case len(f.Cands) == 0:
		return "no-candidates"
	case len(f.Cands) == 1:
		return "sole-way-threshold"
	}
	name, limit := sameNamed(f.Cands), sameLimited(f.Cands)
	switch {
	case name && limit:
		return "multi:same-name+same-limit"
	case name:
		return "multi:same-name,limits-differ"
	case limit:
		return "multi:same-limit,names-differ"
	}
	if f.Spd < 3 || f.Ba >= 180 {
		return "multi:slow,differ"
	}
	aligned := alignedCands(f.Cands)
	switch {
	case len(aligned) == 0:
		return "multi:none-heading-aligned"
	case len(aligned) == 1:
		return "multi:heading-leaves-one"
	case sameNamed(aligned) || sameLimited(aligned):
		return "multi:aligned-share-name-or-limit"
	default:
		return "multi:parallel-aligned,differ"
	}
}

func alignedCands(cands []replayCand) []replayCand {
	out := []replayCand{}
	for _, c := range cands {
		if c.Sin <= headingSine {
			out = append(out, c)
		}
	}
	return out
}

func nearestCand(cands []replayCand) replayCand {
	best := cands[0]
	for _, c := range cands[1:] {
		if c.D < best.D {
			best = c
		}
	}
	return best
}

// counterfactual rules for stage 2, each returning the limit it would publish.
func rule1b(f *replayFrame) (float32, bool) {
	if len(f.Cands) > 1 && sameLimited(f.Cands) {
		return f.Cands[0].Lim, true
	}
	if len(f.Cands) > 1 && sameNamed(f.Cands) {
		return nearestCand(f.Cands).Lim, true
	}
	return 0, false
}

func rule2b(f *replayFrame) (float32, bool) {
	if len(f.Cands) < 2 || f.Spd < 3 || f.Ba >= 180 {
		return 0, false
	}
	a := alignedCands(f.Cands)
	switch {
	case len(a) == 1:
		return a[0].Lim, true
	case len(a) > 1 && sameLimited(a):
		return a[0].Lim, true
	case len(a) > 1 && sameNamed(a):
		return nearestCand(a).Lim, true
	}
	return 0, false
}

func rule3a(f *replayFrame) (float32, bool) { return f.R3, f.R3ok }

func chainRules(rules ...func(*replayFrame) (float32, bool)) func(*replayFrame) (float32, bool) {
	return func(f *replayFrame) (float32, bool) {
		for _, r := range rules {
			if lim, ok := r(f); ok {
				return lim, true
			}
		}
		return 0, false
	}
}

var replayRules = []struct {
	name string
	rule func(f *replayFrame) (float32, bool)
}{
	{"1a same-limit candidates", func(f *replayFrame) (float32, bool) {
		if len(f.Cands) > 1 && sameLimited(f.Cands) {
			return f.Cands[0].Lim, true
		}
		return 0, false
	}},
	{"1b same-name or same-limit (nearest piece)", rule1b},
	{"2a navigationPick as-is", func(f *replayFrame) (float32, bool) {
		return f.PickL, f.Pick != 0
	}},
	{"2b heading gate, then rule 1b", rule2b},
	{"3a heading-aligned and reachable from last match by wheel distance", rule3a},
	{"1b then 2b", chainRules(rule1b, rule2b)},
	{"1b then 2b then 3a", chainRules(rule1b, rule2b, rule3a)},
	{"3 oracle: every unmatched frame on a sure road", func(f *replayFrame) (float32, bool) {
		return f.Tl, f.Tw > 0 && f.Tsl
	}},
}

type firstMatchStats struct {
	Events        int     `json:"events"`
	SourceChanges int     `json:"of_which_source_changes"`
	Censored      int     `json:"interrupted_before_a_match"`
	P50           float64 `json:"p50_s"`
	P90           float64 `json:"p90_s"`
	Max           float64 `json:"max_s"`
}

type stretch struct {
	Drive string  `json:"drive"`
	Start float64 `json:"start_s"`
	Secs  float64 `json:"seconds"`
	Ctx   string  `json:"context_at_start"`
}

type perfStats struct {
	Fixes     int     `json:"fixes"`
	LoadFixes int     `json:"tile_load_fixes"`
	LoadMaxUs float64 `json:"tile_load_max_us"`
	MeanUs    float64 `json:"mean_us"`
	P50Us     float64 `json:"p50_us"`
	P99Us     float64 `json:"p99_us"`
	MaxUs     float64 `json:"max_us"`
}

type episode struct {
	Drive    string  `json:"drive"`
	Start    float64 `json:"start_s"`
	Secs     float64 `json:"seconds"`
	Frames   int     `json:"frames"`
	Lat      float64 `json:"lat"`
	Lon      float64 `json:"lon"`
	Source   string  `json:"source"`
	Context  string  `json:"context"`
	Way      int64   `json:"published_way"`
	Name     string  `json:"published_name"`
	Lim      float32 `json:"published_limit"`
	TruthWay int64   `json:"truth_way"`
	TruthNm  string  `json:"truth_name"`
	TruthLim float32 `json:"truth_limit"`
	Lag      bool    `json:"matches_truth_at_fix_time"`
	Base     string  `json:"baseline,omitempty"`
}

type metricsBlock struct {
	Overall    replayCounts               `json:"overall"`
	BySpeed    map[string]replayCounts    `json:"by_speed"`
	ByContext  map[string]replayCounts    `json:"by_context"`
	BySource   map[string]replayCounts    `json:"by_source"`
	CandCount  map[string]int             `json:"ambiguous_candidate_count"`
	Causes     map[string]int             `json:"unmatched_causes"`
	CausesSrc  map[string]map[string]int  `json:"unmatched_causes_by_source"`
	Rules      map[string]ruleCounts      `json:"counterfactual_rules"`
	FirstMatch map[string]firstMatchStats `json:"time_to_first_match"`
	Longest    []stretch                  `json:"longest_unmatched_on_road"`
	Perf       perfStats                  `json:"process_gps_cost"`
	WrongLimit []episode                  `json:"wrong_limit_episodes"`
	WrongWay   []episode                  `json:"wrong_way_episodes"`
	RuleWrong  map[string][]episode       `json:"rule_wrong_limit_episodes"`
}

func newMetricsBlock() metricsBlock {
	return metricsBlock{BySpeed: map[string]replayCounts{}, ByContext: map[string]replayCounts{}, BySource: map[string]replayCounts{},
		CandCount: map[string]int{}, Causes: map[string]int{}, CausesSrc: map[string]map[string]int{}, Rules: map[string]ruleCounts{}, FirstMatch: map[string]firstMatchStats{},
		RuleWrong: map[string][]episode{}}
}

func addTo(m map[string]replayCounts, k string, f *replayFrame) {
	c := m[k]
	c.add(f)
	m[k] = c
}

func bumpEvent(m map[string]replayCounts, k string, fn func(*replayCounts)) {
	c := m[k]
	fn(&c)
	m[k] = c
}

// episodes groups consecutive frames that satisfy bad into inspectable runs.
// ruleWrongEpisodes lists where a counterfactual rule would publish a wrong limit;
// the published fields show what the rule would publish.
func ruleWrongEpisodes(drive string, frames []replayFrame, rule func(*replayFrame) (float32, bool)) []episode {
	view := make([]replayFrame, len(frames))
	for i := range frames {
		f := frames[i]
		view[i] = replayFrame{T: f.T, Lat: f.Lat, Lon: f.Lon, Gs: f.Gs, Ctx: f.Ctx, Tw: f.Tw, Tn: f.Tn, Tl: f.Tl, Tfw: -1}
		if !f.Fix || f.matched() || (f.Sim != "unknown" && f.Sim != "noMatch") || f.Tw < 0 || !f.Tsl {
			continue
		}
		lim, ok := rule(&f)
		if !ok || !ruleWrong(&f, lim) {
			continue
		}
		names := []string{}
		for _, c := range f.Cands {
			names = append(names, fmt.Sprintf("%s %.1f", c.Name, c.Lim))
		}
		view[i].Way, view[i].Lim, view[i].Name, view[i].Sim = -1, lim, strings.Join(names, " | "), "wrong"
	}
	return episodes(drive, view, func(f *replayFrame) bool { return f.Sim == "wrong" })
}

func episodes(drive string, frames []replayFrame, bad func(*replayFrame) bool) []episode {
	out := []episode{}
	var cur *episode
	lastT := -1.0
	for i := range frames {
		f := &frames[i]
		if !bad(f) {
			continue
		}
		if cur != nil && f.T-lastT <= 1.0 && f.Way == cur.Way {
			cur.Secs, cur.Frames = f.T-cur.Start, cur.Frames+1
			cur.Lag = cur.Lag && f.Tfw > 0 && sameLimit(f.Lim, f.Tfl)
		} else {
			out = append(out, episode{Drive: drive, Start: f.T, Frames: 1, Lat: f.Lat, Lon: f.Lon, Source: sourceName(f.Gs), Context: f.Ctx,
				Way: f.Way, Name: f.Name, Lim: f.Lim, TruthWay: f.Tw, TruthNm: f.Tn, TruthLim: f.Tl, Lag: f.Tfw > 0 && sameLimit(f.Lim, f.Tfl)})
			cur = &out[len(out)-1]
		}
		lastT = f.T
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].Frames > out[j].Frames })
	return out
}

func computeMetrics(drives []replayDrive) metricsBlock {
	b := newMetricsBlock()
	firstMatch := map[string][]float64{}
	censored, srcChanges := map[string]int{}, map[string]int{}
	us, loads := []float64{}, []float64{}
	for _, d := range drives {
		fr := d.frames
		for i := range fr {
			f := &fr[i]
			b.Overall.add(f)
			addTo(b.BySpeed, speedBand(f.V), f)
			addTo(b.ByContext, f.Ctx, f)
			addTo(b.BySource, sourceName(f.Gs), f)
			if f.Us > 0 && f.Load {
				loads = append(loads, f.Us)
			} else if f.Us > 0 {
				us = append(us, f.Us)
			}
			if f.Fix && !f.matched() {
				cause := unmatchedCause(f)
				b.Causes[cause]++
				src := sourceName(f.Gs)
				if b.CausesSrc[src] == nil {
					b.CausesSrc[src] = map[string]int{}
				}
				b.CausesSrc[src][cause]++
				if f.Sim == "unknown" || f.Sim == "noMatch" {
					b.CandCount[fmt.Sprint(min(len(f.Cands), 5))]++
					for _, r := range replayRules {
						if lim, ok := r.rule(f); ok {
							rc := b.Rules[r.name]
							rc.score(f, lim)
							b.Rules[r.name] = rc
						}
					}
				}
			}
		}
		// Flip-flops: a published way (or limit) that changes and reverts within 5 s.
		type change struct {
			from, to float64
			t        float64
		}
		events := func(value func(*replayFrame) float64, count func(*replayCounts)) {
			var last, prev float64
			var lastChange *change
			for i := range fr {
				f := &fr[i]
				v := value(f)
				if v == 0 {
					continue
				}
				if last != 0 && v != last {
					c := change{last, v, f.T}
					if lastChange != nil && lastChange.from == v && lastChange.to == last && f.T-lastChange.t <= 5 {
						count(&b.Overall)
						bumpEvent(b.BySpeed, speedBand(f.V), count)
						bumpEvent(b.ByContext, f.Ctx, count)
						bumpEvent(b.BySource, sourceName(f.Gs), count)
					}
					lastChange = &c
				}
				prev, last = last, v
			}
			_ = prev
		}
		events(func(f *replayFrame) float64 {
			if f.matched() {
				return float64(f.Way)
			}
			return 0
		}, func(c *replayCounts) { c.FlipWay++ })
		events(func(f *replayFrame) float64 {
			if f.publishedLimit() {
				return float64(f.Lim)
			}
			return 0
		}, func(c *replayCounts) { c.FlipLimit++ })
		// Dropouts: the same way unmatched for at most 2 s, then matched again.
		lastWay, lastT, gap := int64(0), 0.0, false
		for i := range fr {
			f := &fr[i]
			if f.matched() {
				if gap && f.Way == lastWay && f.T-lastT <= 2 {
					b.Overall.Dropouts++
					bumpEvent(b.BySpeed, speedBand(f.V), func(c *replayCounts) { c.Dropouts++ })
					bumpEvent(b.ByContext, f.Ctx, func(c *replayCounts) { c.Dropouts++ })
					bumpEvent(b.BySource, sourceName(f.Gs), func(c *replayCounts) { c.Dropouts++ })
				}
				lastWay, lastT, gap = f.Way, f.T, false
			} else if f.Fix {
				gap = true
			}
		}
		// Time to first match after the fix source changes or a fix returns from a gap.
		open, openT, openSrc := false, 0.0, ""
		for i := range fr {
			f := &fr[i]
			start := f.Fix && (i == 0 || !fr[i-1].Fix || f.SrcCh)
			if start {
				if open {
					censored[openSrc]++
				}
				open, openT, openSrc = true, f.T, sourceName(f.Gs)
				if f.SrcCh {
					srcChanges[openSrc]++
				}
			}
			if open && f.matched() {
				firstMatch[openSrc] = append(firstMatch[openSrc], f.T-openT)
				open = false
			}
		}
		if open {
			censored[openSrc]++
		}
		// Longest unmatched stretches while the truth is on a road.
		var startT float64
		var startCtx string
		run := false
		flush := func(endT float64) {
			if run && endT-startT > 0 {
				b.Longest = append(b.Longest, stretch{d.hdr.Drive, startT, math.Round((endT-startT)*100) / 100, startCtx})
			}
			run = false
		}
		for i := range fr {
			f := &fr[i]
			bad := f.Fix && !f.matched() && f.Tw > 0
			if bad && !run {
				run, startT, startCtx = true, f.T, f.Ctx
			} else if !bad && run {
				flush(f.T)
			}
		}
		if len(fr) > 0 {
			flush(fr[len(fr)-1].T)
		}
		b.WrongLimit = append(b.WrongLimit, episodes(d.hdr.Drive, fr, func(f *replayFrame) bool { return f.wrongLimit() || f.publishedOffRoad() })...)
		b.WrongWay = append(b.WrongWay, episodes(d.hdr.Drive, fr, (*replayFrame).wrongWay)...)
		for _, r := range replayRules {
			b.RuleWrong[r.name] = append(b.RuleWrong[r.name], ruleWrongEpisodes(d.hdr.Drive, fr, r.rule)...)
		}
	}
	for src, v := range firstMatch {
		b.FirstMatch[src] = firstMatchStats{Events: len(v) + censored[src], SourceChanges: srcChanges[src], Censored: censored[src], P50: percentile(v, 0.5), P90: percentile(v, 0.9), Max: percentile(v, 1)}
	}
	for src, n := range censored {
		if _, ok := firstMatch[src]; !ok {
			b.FirstMatch[src] = firstMatchStats{Events: n, SourceChanges: srcChanges[src], Censored: n, P50: math.NaN(), P90: math.NaN(), Max: math.NaN()}
		}
	}
	sort.SliceStable(b.Longest, func(i, j int) bool { return b.Longest[i].Secs > b.Longest[j].Secs })
	if len(b.Longest) > 5 {
		b.Longest = b.Longest[:5]
	}
	sort.SliceStable(b.WrongLimit, func(i, j int) bool { return b.WrongLimit[i].Frames > b.WrongLimit[j].Frames })
	sort.SliceStable(b.WrongWay, func(i, j int) bool { return b.WrongWay[i].Frames > b.WrongWay[j].Frames })
	b.Perf = perfFrom(us, loads)
	return b
}

func perfFrom(us, loads []float64) perfStats {
	p := perfStats{Fixes: len(us), LoadFixes: len(loads), LoadMaxUs: percentile(loads, 1)}
	if len(us) == 0 {
		return p
	}
	total := 0.0
	for _, v := range us {
		total += v
	}
	p.MeanUs, p.P50Us, p.P99Us, p.MaxUs = total/float64(len(us)), percentile(us, 0.5), percentile(us, 0.99), percentile(us, 1)
	return p
}

func perfOf(frames []replayFrame) perfStats {
	us, loads := []float64{}, []float64{}
	for i := range frames {
		if f := &frames[i]; f.Us > 0 && f.Load {
			loads = append(loads, f.Us)
		} else if f.Us > 0 {
			us = append(us, f.Us)
		}
	}
	return perfFrom(us, loads)
}

type driveSummary struct {
	Drive    string       `json:"drive"`
	Dump     string       `json:"dump_hash"`
	Truth    string       `json:"truth_hash"`
	Gate     replayGate   `json:"gate"`
	Quality  truthQuality `json:"truth_quality"`
	CostRuns []perfStats  `json:"cost_runs"`
	Metrics  metricsBlock `json:"metrics"`
}

type replaySummary struct {
	Drives   []driveSummary `json:"drives"`
	Combined metricsBlock   `json:"combined"`
}

func summarize(drives []replayDrive) replaySummary {
	s := replaySummary{}
	for _, d := range drives {
		s.Drives = append(s.Drives, driveSummary{d.hdr.Drive, d.hdr.Dump, d.hdr.Truth, d.hdr.Gate, d.hdr.Quality, d.hdr.CostRuns, computeMetrics([]replayDrive{d})})
	}
	s.Combined = computeMetrics(drives)
	return s
}

func pct(a, b int) string {
	r := ratio(a, b)
	if math.IsNaN(r) {
		return "-"
	}
	return fmt.Sprintf("%.1f%%", 100*r)
}

func countsHeader(w io.Writer, first string) {
	fmt.Fprintf(w, "%s\tfix frames\tmatched\ton-road matched\tlimit\tlimit coverage\tWRONG LIMIT\t(fix lag)\tper 1k limit\toff-road limit\tlimit, truth unsure\twrong way\t(same limit)\tflip way/limit\tdropouts\ttruth unsure\n", first)
}

func countsRow(w io.Writer, name string, c replayCounts) {
	per := "-"
	if c.LimitScored > 0 {
		per = fmt.Sprintf("%.2f", 1000*ratio(c.WrongLimit, c.LimitScored))
	}
	fmt.Fprintf(w, "%s\t%d\t%s\t%s\t%s\t%s\t%d\t%d\t%s\t%d\t%d\t%d\t%d\t%d/%d\t%d\t%s\n", name, c.Frames, pct(c.Matched, c.Frames),
		pct(c.MatchedRoad, c.TruthRoad), pct(c.Limit, c.Frames), pct(c.CorrectLimit, c.TruthLimit), c.WrongLimit, c.WrongLimitLag, per,
		c.PubOffRoad, c.PubUnsure, c.WrongWay, c.WrongWaySameLimit, c.FlipWay, c.FlipLimit, c.Dropouts, pct(c.Unsure, c.Frames))
}

func sortedKeys[V any](m map[string]V) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

var speedOrder = []string{"stopped", "<15", "15-25", ">=25"}

func printSplits(w io.Writer, b metricsBlock) {
	tw := tabwriter.NewWriter(w, 0, 0, 2, ' ', 0)
	countsHeader(tw, "split")
	countsRow(tw, "ALL", b.Overall)
	for _, k := range speedOrder {
		if c, ok := b.BySpeed[k]; ok {
			countsRow(tw, "speed "+k, c)
		}
	}
	for _, k := range sortedKeys(b.ByContext) {
		countsRow(tw, "ctx "+k, b.ByContext[k])
	}
	for _, k := range sortedKeys(b.BySource) {
		countsRow(tw, "gps "+k, b.BySource[k])
	}
	tw.Flush()
}

func printEpisodes(w io.Writer, title string, eps []episode, n int) {
	fmt.Fprintf(w, "\n%s (%d episodes; longest first)\n", title, len(eps))
	tw := tabwriter.NewWriter(w, 0, 0, 2, ' ', 0)
	fmt.Fprintln(tw, "drive\tt (s)\tsecs\tframes\tposition\tgps\tcontext\tpublished\ttruth\tfix lag\tbaseline")
	for i, e := range eps {
		if i >= n {
			break
		}
		fmt.Fprintf(tw, "%s\t%.1f\t%.1f\t%d\t%.6f,%.6f\t%s\t%s\t%d %q %.2f\t%d %q %.2f\t%v\t%s\n", e.Drive, e.Start, e.Secs, e.Frames, e.Lat, e.Lon,
			e.Source, e.Context, e.Way, e.Name, e.Lim, e.TruthWay, e.TruthNm, e.TruthLim, e.Lag, e.Base)
	}
	tw.Flush()
}

func printReport(w io.Writer, s replaySummary, drives []replayDrive) {
	fmt.Fprintln(w, "\n=== REPRODUCTION GATE (replayed status, way id and limit vs what the car logged) ===")
	for _, d := range s.Drives {
		verdict := "PASS"
		if !d.Gate.Pass {
			verdict = "FAIL"
		}
		if !d.Gate.Enforced {
			verdict += " (not enforced: REPLAY_GATE=off)"
		}
		fmt.Fprintf(w, "%s: %d/%d frames agree (%.4f%%), minimum %.2f%% -> %s %s\n", d.Drive, d.Gate.Frames-d.Gate.Mismatches, d.Gate.Frames,
			100*d.Gate.Agree, 100*d.Gate.MinAgree, verdict, d.Gate.First)
	}
	fmt.Fprintln(w, "\n=== TRUTH QUALITY ===")
	for _, d := range s.Drives {
		q := d.Quality
		fmt.Fprintf(w, "%s: %d observations, %d breaks; frames with truth %d (off-road %d, unsure way %s, unsure limit %s, none %d)\n", d.Drive, q.Obs,
			q.Breaks, q.Frames, q.OffRoad, pct(q.UnsureWay, q.Frames), pct(q.UnsureLimit, q.Frames), q.NoTruth)
		fmt.Fprintf(w, "  way transitions %d: sharing a node %d, not sharing %d, u-turns %d\n", q.Transitions, q.ViaGraph, q.NotAdjacent, q.UTurns)
		fmt.Fprintf(w, "  even/odd fix subsets disagree with full truth on %.2f%% of %d sure frames (even %d, odd %d)\n", 100*q.Robustness.DisagreeShare,
			q.Robustness.Frames, q.Robustness.EvenDisagree, q.Robustness.OddDisagree)
		g := q.Guidance
		fmt.Fprintf(w, "  guidance road names: %d episodes, maneuver not reached/re-routed %d, agree %d, disagree %d, truth unnamed %d, no sure truth %d\n",
			g.Episodes, g.Rerouted, g.Agree, g.Disagree, g.Unnamed, g.NoTruth)
		for _, dis := range g.Disagrees {
			fmt.Fprintf(w, "    disagree: %s\n", dis)
		}
		o := q.Odometry
		fmt.Fprintf(w, "  wheel odometry vs truth path: %d pairs (mean %.0f m), route/wheel p10 %.3f p50 %.3f p90 %.3f, |residual| p50 %.1f m p90 %.1f m\n",
			o.Pairs, o.MeanWheelM, o.RatioP10, o.RatioP50, o.RatioP90, o.ResidualP50, o.ResidualP90)
		y := q.Yaw
		fmt.Fprintf(w, "  yaw vs GPS bearing rate (%d samples, carState.yawRate zero on %.1f%% of frames):", y.Samples, 100*y.CarYawZero)
		for _, k := range sortedKeys(y.Correlation) {
			fmt.Fprintf(w, " %s r=%.3f slope=%.3f;", k, y.Correlation[k], y.SlopeDegPerS[k])
		}
		fmt.Fprintln(w)
	}
	fmt.Fprintln(w, "\n=== RESULTS PER DRIVE ===")
	tw := tabwriter.NewWriter(w, 0, 0, 2, ' ', 0)
	countsHeader(tw, "drive")
	for _, d := range s.Drives {
		countsRow(tw, d.Drive, d.Metrics.Overall)
	}
	if len(s.Drives) > 1 {
		countsRow(tw, "COMBINED", s.Combined.Overall)
	}
	tw.Flush()
	fmt.Fprintln(w, "\n=== SPLITS (combined) ===")
	printSplits(w, s.Combined)
	fmt.Fprintln(w, "\n=== TIME TO FIRST MATCH after a source change or a fix gap ===")
	for _, k := range sortedKeys(s.Combined.FirstMatch) {
		v := s.Combined.FirstMatch[k]
		fmt.Fprintf(w, "%s: %d events (%d source changes, the rest fix gaps), interrupted before a match %d, p50 %.1f s, p90 %.1f s, max %.1f s\n",
			k, v.Events, v.SourceChanges, v.Censored, v.P50, v.P90, v.Max)
	}
	fmt.Fprintln(w, "longest unmatched stretches while the truth is on a road:")
	for _, l := range s.Combined.Longest {
		fmt.Fprintf(w, "  %s t=%.1f s for %.1f s (%s)\n", l.Drive, l.Start, l.Secs, l.Ctx)
	}
	fmt.Fprintln(w, "\n=== WHY UNMATCHED (frames with a fix and no match) ===")
	srcs := sortedKeys(s.Combined.CausesSrc)
	tw = tabwriter.NewWriter(w, 0, 0, 2, ' ', 0)
	fmt.Fprintf(tw, "cause\tall\t%s\n", strings.Join(srcs, "\t"))
	total := 0
	for _, k := range sortedKeys(s.Combined.Causes) {
		total += s.Combined.Causes[k]
		row := []string{}
		for _, src := range srcs {
			row = append(row, fmt.Sprint(s.Combined.CausesSrc[src][k]))
		}
		fmt.Fprintf(tw, "%s\t%d\t%s\n", k, s.Combined.Causes[k], strings.Join(row, "\t"))
	}
	fmt.Fprintf(tw, "TOTAL\t%d\n", total)
	tw.Flush()
	fmt.Fprintf(w, "ambiguous/noMatch frames by candidate count:")
	for _, k := range sortedKeys(s.Combined.CandCount) {
		label := k
		if k == "5" {
			label = "5+"
		}
		fmt.Fprintf(w, " %s:%d", label, s.Combined.CandCount[k])
	}
	fmt.Fprintln(w)
	fmt.Fprintln(w, "\n=== COUNTERFACTUAL RULES on ambiguous/noMatch frames, scored against truth (estimates for stage 2) ===")
	tw = tabwriter.NewWriter(w, 0, 0, 2, ' ', 0)
	fmt.Fprintln(tw, "rule\tapplies\tright\tno limit (truth has one)\tWRONG vs tagged truth\tlimit where truth has none\ttruth unsure\tmatch rate if adopted")
	for _, r := range replayRules {
		c := s.Combined.Rules[r.name]
		fmt.Fprintf(tw, "%s\t%d\t%d\t%d\t%d\t%d\t%d\t%s\n", r.name, c.Applies, c.Right, c.NoLimit, c.WrongTagged, c.WrongUntagged, c.Unscored,
			pct(s.Combined.Overall.Matched+c.Applies, s.Combined.Overall.Frames))
	}
	tw.Flush()
	p := s.Combined.Perf
	fmt.Fprintf(w, "\n=== ProcessGps COST per new fix, fastest of the repeated replays (Docker/laptop: compare runs relative to each other) ===\n")
	fmt.Fprintf(w, "%d fixes: mean %.0f us, p50 %.0f us, p99 %.0f us, max %.0f us; %d fixes loaded a tile (max %.0f us)\n",
		p.Fixes, p.MeanUs, p.P50Us, p.P99Us, p.MaxUs, p.LoadFixes, p.LoadMaxUs)
	for _, d := range s.Drives {
		fmt.Fprintf(w, "  %s single-run p99/max:", d.Drive)
		for _, r := range d.CostRuns {
			fmt.Fprintf(w, " %.0f/%.0f us", r.P99Us, r.MaxUs)
		}
		fmt.Fprintln(w)
	}
	printEpisodes(w, "WRONG LIMIT published (or a limit published where the truth is off the map)", s.Combined.WrongLimit, 15)
	printEpisodes(w, "WRONG WAY published (truth sanity check: the baseline only matches a sole candidate)", s.Combined.WrongWay, 15)
	for _, r := range replayRules {
		if eps := s.Combined.RuleWrong[r.name]; len(eps) > 0 {
			printEpisodes(w, "rule \""+r.name+"\" would publish a WRONG LIMIT (published = the rule's limit; name = candidates)", eps, 8)
		}
	}
}

type compareThresholds struct {
	MaxWrongLimitDelta int     `json:"max_wrong_limit_delta"`
	MaxOffRoadDelta    int     `json:"max_off_road_limit_delta"`
	MaxP99Ratio        float64 `json:"max_p99_cost_ratio"`
}

func compareThresholdsFromEnv() compareThresholds {
	return compareThresholds{int(envFloat("REPLAY_MAX_WRONG_LIMIT_DELTA", 0)), int(envFloat("REPLAY_MAX_OFFROAD_DELTA", 0)), envFloat("REPLAY_MAX_P99_RATIO", 0)}
}

type compareResult struct {
	Drives      []string          `json:"drives"`
	Thresholds  compareThresholds `json:"thresholds"`
	BaseGate    []replayGate      `json:"baseline_gate"`
	Base        metricsBlock      `json:"baseline"`
	Cand        metricsBlock      `json:"candidate"`
	Regressions []episode         `json:"new_wrong_limit_episodes"`
	NewWrongWay []episode         `json:"new_wrong_way_episodes"`
	Fixed       int               `json:"wrong_limit_frames_fixed"`
	Pass        bool              `json:"pass"`
	Failures    []string          `json:"failures,omitempty"`
}

func compareDrives(base, cand []replayDrive, th compareThresholds) (compareResult, error) {
	r := compareResult{Thresholds: th}
	if len(base) != len(cand) {
		return r, fmt.Errorf("baseline has %d drives, candidate %d", len(base), len(cand))
	}
	for i := range base {
		b, c := base[i], cand[i]
		if b.hdr.Dump != c.hdr.Dump || len(b.frames) != len(c.frames) {
			return r, fmt.Errorf("%s: baseline and candidate replay different dumps", b.hdr.Drive)
		}
		if b.hdr.Truth != c.hdr.Truth {
			return r, fmt.Errorf("%s: truth differs between runs (%s vs %s); the truth must not depend on the matcher", b.hdr.Drive, b.hdr.Truth, c.hdr.Truth)
		}
		r.Drives = append(r.Drives, b.hdr.Drive)
		r.BaseGate = append(r.BaseGate, b.hdr.Gate)
		badLimit := func(f *replayFrame) bool { return f.wrongLimit() || f.publishedOffRoad() }
		newLimit, newWay := map[*replayFrame]bool{}, map[*replayFrame]bool{}
		baseAt := map[float64]*replayFrame{}
		for j := range c.frames {
			cf, bf := &c.frames[j], &b.frames[j]
			if badLimit(bf) && !badLimit(cf) {
				r.Fixed++
			}
			newLimit[cf] = badLimit(cf) && !badLimit(bf)
			newWay[cf] = cf.wrongWay() && !bf.wrongWay()
			baseAt[cf.T] = bf
		}
		annotate := func(eps []episode) []episode {
			for k := range eps {
				if bf := baseAt[eps[k].Start]; bf != nil {
					eps[k].Base = fmt.Sprintf("%s %d %.2f", bf.Sim, bf.Way, bf.Lim)
				}
			}
			return eps
		}
		r.Regressions = append(r.Regressions, annotate(episodes(c.hdr.Drive, c.frames, func(f *replayFrame) bool { return newLimit[f] }))...)
		r.NewWrongWay = append(r.NewWrongWay, annotate(episodes(c.hdr.Drive, c.frames, func(f *replayFrame) bool { return newWay[f] }))...)
	}
	sort.SliceStable(r.Regressions, func(i, j int) bool { return r.Regressions[i].Frames > r.Regressions[j].Frames })
	sort.SliceStable(r.NewWrongWay, func(i, j int) bool { return r.NewWrongWay[i].Frames > r.NewWrongWay[j].Frames })
	r.Base, r.Cand = computeMetrics(base), computeMetrics(cand)
	bo, co := r.Base.Overall, r.Cand.Overall
	if co.WrongLimit > bo.WrongLimit+th.MaxWrongLimitDelta {
		r.Failures = append(r.Failures, fmt.Sprintf("wrong-limit frames %d > baseline %d + %d", co.WrongLimit, bo.WrongLimit, th.MaxWrongLimitDelta))
	}
	if co.PubOffRoad > bo.PubOffRoad+th.MaxOffRoadDelta {
		r.Failures = append(r.Failures, fmt.Sprintf("off-road limit frames %d > baseline %d + %d", co.PubOffRoad, bo.PubOffRoad, th.MaxOffRoadDelta))
	}
	if th.MaxP99Ratio > 0 && r.Base.Perf.P99Us > 0 && r.Cand.Perf.P99Us > th.MaxP99Ratio*r.Base.Perf.P99Us {
		r.Failures = append(r.Failures, fmt.Sprintf("p99 cost %.0f us > %.2f x baseline %.0f us", r.Cand.Perf.P99Us, th.MaxP99Ratio, r.Base.Perf.P99Us))
	}
	r.Pass = len(r.Failures) == 0
	return r, nil
}

func printComparison(w io.Writer, r compareResult) {
	fmt.Fprintf(w, "\n=== BASELINE vs CANDIDATE over %s ===\n", strings.Join(r.Drives, ", "))
	for i, g := range r.BaseGate {
		fmt.Fprintf(w, "baseline gate %s: %.4f%% agreement with the car (pass=%v)\n", r.Drives[i], 100*g.Agree, g.Pass)
	}
	b, c := r.Base.Overall, r.Cand.Overall
	tw := tabwriter.NewWriter(w, 0, 0, 2, ' ', 0)
	fmt.Fprintln(tw, "metric\tbaseline\tcandidate\tdelta")
	row := func(name string, x, y int) { fmt.Fprintf(tw, "%s\t%d\t%d\t%+d\n", name, x, y, y-x) }
	rate := func(name string, x, y float64) {
		fmt.Fprintf(tw, "%s\t%.1f%%\t%.1f%%\t%+.1f pts\n", name, 100*x, 100*y, 100*(y-x))
	}
	rate("match rate (fix frames)", ratio(b.Matched, b.Frames), ratio(c.Matched, c.Frames))
	rate("match rate where truth on road", ratio(b.MatchedRoad, b.TruthRoad), ratio(c.MatchedRoad, c.TruthRoad))
	rate("limit published", ratio(b.Limit, b.Frames), ratio(c.Limit, c.Frames))
	rate("limit coverage (correct / truth has limit)", ratio(b.CorrectLimit, b.TruthLimit), ratio(c.CorrectLimit, c.TruthLimit))
	row("WRONG LIMIT frames", b.WrongLimit, c.WrongLimit)
	row("  of which fix lag", b.WrongLimitLag, c.WrongLimitLag)
	row("  of which truth way has no limit", b.WrongLimitNoTag, c.WrongLimitNoTag)
	row("limit published off the map", b.PubOffRoad, c.PubOffRoad)
	row("limit published, truth unsure", b.PubUnsure, c.PubUnsure)
	row("wrong way frames", b.WrongWay, c.WrongWay)
	row("  of which same limit", b.WrongWaySameLimit, c.WrongWaySameLimit)
	row("way flip-flops", b.FlipWay, c.FlipWay)
	row("limit flip-flops", b.FlipLimit, c.FlipLimit)
	row("dropouts", b.Dropouts, c.Dropouts)
	fmt.Fprintf(tw, "ProcessGps mean / p99 / max us\t%.0f / %.0f / %.0f\t%.0f / %.0f / %.0f\t\n", r.Base.Perf.MeanUs, r.Base.Perf.P99Us, r.Base.Perf.MaxUs,
		r.Cand.Perf.MeanUs, r.Cand.Perf.P99Us, r.Cand.Perf.MaxUs)
	tw.Flush()
	fmt.Fprintln(w, "\nsplits, match rate and wrong-limit frames (baseline -> candidate):")
	tw = tabwriter.NewWriter(w, 0, 0, 2, ' ', 0)
	fmt.Fprintln(tw, "split\tfix frames\tmatched\twrong limit\twrong way")
	split := func(name string, x, y replayCounts) {
		fmt.Fprintf(tw, "%s\t%d\t%s -> %s\t%d -> %d\t%d -> %d\n", name, x.Frames, pct(x.Matched, x.Frames), pct(y.Matched, y.Frames), x.WrongLimit, y.WrongLimit, x.WrongWay, y.WrongWay)
	}
	for _, k := range speedOrder {
		split("speed "+k, r.Base.BySpeed[k], r.Cand.BySpeed[k])
	}
	for _, k := range sortedKeys(r.Base.ByContext) {
		split("ctx "+k, r.Base.ByContext[k], r.Cand.ByContext[k])
	}
	for _, k := range sortedKeys(r.Base.BySource) {
		split("gps "+k, r.Base.BySource[k], r.Cand.BySource[k])
	}
	tw.Flush()
	fmt.Fprintf(w, "\nwrong-limit frames fixed by the candidate: %d\n", r.Fixed)
	printEpisodes(w, "NEW WRONG LIMIT in the candidate (worst regressions)", r.Regressions, 20)
	printEpisodes(w, "NEW WRONG WAY in the candidate", r.NewWrongWay, 10)
	verdict := "PASS"
	if !r.Pass {
		verdict = "FAIL: " + strings.Join(r.Failures, "; ")
	}
	fmt.Fprintf(w, "\nverdict: %s\n", verdict)
}
