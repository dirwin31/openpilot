package main

// Offline ground truth for the replay harness. It is independent of the
// matcher under test: it never calls plausibleLoadedWays, GetCurrentWay,
// navigationPick or OnWay, and it sees the whole drive (future fixes too).
// It is an HMM over the tile road graph: emission from the distance to each
// way and the GPS heading; transition from the wheel distance driven versus
// the graph distance between the two road positions; an off-road state covers
// parking lots and unmapped ground. Viterbi gives the path, max-marginals say
// how much worse the best path through another way (or limit) is.

import (
	"container/heap"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"math"
	"regexp"
	"sort"
	"strings"

	"pfeifer.dev/mapd/maps"
	m "pfeifer.dev/mapd/math"
	ms "pfeifer.dev/mapd/settings"
)

const (
	truthRadius        = 45.0 // metres searched around a fix for candidate ways
	truthMaxStates     = 16
	truthOffroadEmit   = -6.0
	truthOffroadSwitch = -8.0
	truthBreakPenalty  = -40.0
	truthSureMargin    = 5.0 // log-likelihood gap to the best path through another way
	truthMaxGapNs      = 10_000_000_000
	truthCorridor      = 200.0 // ways kept within this many metres of a fix
	guidanceReachedM   = 150.0
	guidanceAfterM     = 1500.0 // freeway connectors can be over a kilometre long
	contextJunctionM   = 30.0
	contextParallelM   = 20.0
	contextParallelSin = 0.342 // sin(20 deg)
)

type truthWay struct {
	id         int64
	name, ref  string
	oneWay     bool
	width      float64
	limF, limB float64
	nodes      []int32
	cum        []float64
}

func (w *truthWay) key() string { return w.name + "\x00" + w.ref }

// limit is the posted limit in the travel direction, as CurrentWay.MaxSpeed reads it.
func (w *truthWay) limit(fwd bool) float64 {
	if fwd {
		return w.limF
	}
	return w.limB
}

func (w *truthWay) label() string {
	if w.name != "" {
		return w.name
	}
	return w.ref
}

func (w *truthWay) length() float64 { return w.cum[len(w.cum)-1] }

type truthEdge struct {
	to       int32
	way, seg int32
	fwd      bool
	length   float64
}

type truthSegRef struct{ way, seg int32 }

type truthMap struct {
	lat0, lon0, kx, ky float64
	ways               []truthWay
	byID               map[int64]int32
	xy                 [][2]float64
	nodeID             map[[2]float64]int32
	nodeWays           [][]int32
	adj                [][]truthEdge
	cell               float64
	grid               map[[2]int32][]truthSegRef
}

func newTruthMap(lat0, lon0 float64) *truthMap {
	return &truthMap{lat0: lat0, lon0: lon0, kx: math.Cos(lat0*ms.TO_RADIANS) * ms.R * ms.TO_RADIANS, ky: ms.R * ms.TO_RADIANS,
		byID: map[int64]int32{}, nodeID: map[[2]float64]int32{}, cell: 50, grid: map[[2]int32][]truthSegRef{}}
}

func (tm *truthMap) project(lat, lon float64) (float64, float64) {
	return (lon - tm.lon0) * tm.kx, (lat - tm.lat0) * tm.ky
}

func (tm *truthMap) unproject(x, y float64) (float64, float64) {
	return tm.lat0 + y/tm.ky, tm.lon0 + x/tm.kx
}

func (tm *truthMap) node(lat, lon float64) int32 {
	k := [2]float64{lat, lon}
	if id, ok := tm.nodeID[k]; ok {
		return id
	}
	id := int32(len(tm.xy))
	x, y := tm.project(lat, lon)
	tm.xy = append(tm.xy, [2]float64{x, y})
	tm.nodeID[k] = id
	tm.nodeWays = append(tm.nodeWays, nil)
	tm.adj = append(tm.adj, nil)
	return id
}

// addWay copies one tile way; repeated ids (tile overlaps) are kept once.
func (tm *truthMap) addWay(w maps.Way) {
	if w.Id() <= 0 || w.Nodes.Len() < 2 {
		return
	}
	if _, ok := tm.byID[w.Id()]; ok {
		return
	}
	limF, limB := w.MaxSpeed(), w.MaxSpeed()
	if w.MaxSpeedForward() > 0 {
		limF = w.MaxSpeedForward()
	}
	if w.MaxSpeedBackward() > 0 {
		limB = w.MaxSpeedBackward()
	}
	tw := truthWay{id: w.Id(), name: w.WayName(), ref: w.WayRef(), oneWay: w.OneWay(), width: float64(w.Width()), limF: limF, limB: limB}
	for i := 0; i < w.Nodes.Len(); i++ {
		p := w.Nodes.At(i)
		n := tm.node(p.Lat(), p.Lon())
		if len(tw.nodes) > 0 && tw.nodes[len(tw.nodes)-1] == n {
			continue
		}
		tw.nodes = append(tw.nodes, n)
	}
	if len(tw.nodes) < 2 {
		return
	}
	tw.cum = make([]float64, len(tw.nodes))
	for i := 1; i < len(tw.nodes); i++ {
		a, b := tm.xy[tw.nodes[i-1]], tm.xy[tw.nodes[i]]
		tw.cum[i] = tw.cum[i-1] + math.Hypot(b[0]-a[0], b[1]-a[1])
	}
	idx := int32(len(tm.ways))
	tm.byID[tw.id] = idx
	tm.ways = append(tm.ways, tw)
	for i, n := range tw.nodes {
		if len(tm.nodeWays[n]) == 0 || tm.nodeWays[n][len(tm.nodeWays[n])-1] != idx {
			tm.nodeWays[n] = append(tm.nodeWays[n], idx)
		}
		if i == 0 {
			continue
		}
		p := tw.nodes[i-1]
		l := tw.cum[i] - tw.cum[i-1]
		tm.adj[p] = append(tm.adj[p], truthEdge{n, idx, int32(i - 1), true, l})
		if !tw.oneWay {
			tm.adj[n] = append(tm.adj[n], truthEdge{p, idx, int32(i - 1), false, l})
		}
		a, b := tm.xy[p], tm.xy[n]
		c0 := [2]int32{int32(math.Floor(min(a[0], b[0]) / tm.cell)), int32(math.Floor(min(a[1], b[1]) / tm.cell))}
		c1 := [2]int32{int32(math.Floor(max(a[0], b[0]) / tm.cell)), int32(math.Floor(max(a[1], b[1]) / tm.cell))}
		for cx := c0[0]; cx <= c1[0]; cx++ {
			for cy := c0[1]; cy <= c1[1]; cy++ {
				tm.grid[[2]int32{cx, cy}] = append(tm.grid[[2]int32{cx, cy}], truthSegRef{idx, int32(i - 1)})
			}
		}
	}
}

// truthProj is the nearest point of one way to a position.
type truthProj struct {
	way, seg int32
	t, s, d  float64
	bearing  float64 // degrees, along the node order
}

func (tm *truthMap) projectSeg(w *truthWay, seg int32, x, y float64) truthProj {
	a, b := tm.xy[w.nodes[seg]], tm.xy[w.nodes[seg+1]]
	dx, dy := b[0]-a[0], b[1]-a[1]
	l2 := dx*dx + dy*dy
	t := 0.0
	if l2 > 0 {
		t = math.Max(0, math.Min(1, ((x-a[0])*dx+(y-a[1])*dy)/l2))
	}
	px, py := a[0]+t*dx, a[1]+t*dy
	return truthProj{seg: seg, t: t, s: w.cum[seg] + t*(w.cum[seg+1]-w.cum[seg]), d: math.Hypot(x-px, y-py),
		bearing: math.Mod(math.Atan2(dx, dy)*180/math.Pi+360, 360)}
}

// near returns, per way, the nearest point within radius of (x, y).
func (tm *truthMap) near(x, y, radius float64) []truthProj {
	best := map[int32]truthProj{}
	c0x, c0y := int32(math.Floor((x-radius)/tm.cell)), int32(math.Floor((y-radius)/tm.cell))
	c1x, c1y := int32(math.Floor((x+radius)/tm.cell)), int32(math.Floor((y+radius)/tm.cell))
	for cx := c0x; cx <= c1x; cx++ {
		for cy := c0y; cy <= c1y; cy++ {
			for _, r := range tm.grid[[2]int32{cx, cy}] {
				p := tm.projectSeg(&tm.ways[r.way], r.seg, x, y)
				p.way = r.way
				if p.d > radius+tm.ways[r.way].width/2 {
					continue
				}
				if old, ok := best[r.way]; !ok || p.d < old.d {
					best[r.way] = p
				}
			}
		}
	}
	out := make([]truthProj, 0, len(best))
	for _, p := range best {
		out = append(out, p)
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].d != out[j].d {
			return out[i].d < out[j].d
		}
		return tm.ways[out[i].way].id < tm.ways[out[j].way].id
	})
	return out
}

func (tm *truthMap) pointAt(w int32, s float64) (float64, float64) {
	way := &tm.ways[w]
	i := sort.SearchFloat64s(way.cum, s)
	i = max(1, min(i, len(way.cum)-1))
	a, b := tm.xy[way.nodes[i-1]], tm.xy[way.nodes[i]]
	l := way.cum[i] - way.cum[i-1]
	t := 0.0
	if l > 0 {
		t = math.Max(0, math.Min(1, (s-way.cum[i-1])/l))
	}
	return a[0] + t*(b[0]-a[0]), a[1] + t*(b[1]-a[1])
}

func (tm *truthMap) axisAt(w int32, s float64) float64 {
	way := &tm.ways[w]
	i := sort.SearchFloat64s(way.cum, s)
	i = max(1, min(i, len(way.cum)-1))
	a, b := tm.xy[way.nodes[i-1]], tm.xy[way.nodes[i]]
	return math.Atan2(b[0]-a[0], b[1]-a[1]) * 180 / math.Pi
}

// loadTruthMap keeps every tile way within truthCorridor of any fix of the drive.
func loadTruthMap(d *replayDump, tileDir string) (*truthMap, error) {
	obs := allFixes(d)
	if len(obs) == 0 {
		return newTruthMap(0, 0), nil
	}
	tm := newTruthMap(obs[len(obs)/2].Lat, obs[len(obs)/2].Lon)
	tiles := []*maps.Offline{}
	lastX, lastY := math.Inf(1), math.Inf(1)
	for _, r := range obs {
		x, y := tm.project(r.Lat, r.Lon)
		if math.Hypot(x-lastX, y-lastY) < truthCorridor/4 {
			continue
		}
		lastX, lastY = x, y
		pos := m.NewPosition(r.Lat, r.Lon)
		var tile *maps.Offline
		for _, t := range tiles {
			if b := t.Box(); b.PosInside(pos) {
				tile = t
				break
			}
		}
		if tile == nil {
			o, err := maps.FindWaysAroundPositionIn(pos, tileDir)
			if err != nil || !o.Loaded {
				continue
			}
			tile = &o
			tiles = append(tiles, tile)
		}
		idx, ok := tile.WaysNear(pos)
		n := len(idx)
		if !ok {
			n = tile.Ways.Len()
		}
		for k := range n {
			i := k
			if ok {
				i = int(idx[k])
			}
			w := tile.Ways.At(i)
			if maps.MayBeWithin(w.Box(), pos, truthCorridor) {
				tm.addWay(w)
			}
		}
	}
	return tm, nil
}

// truthMapFromTile copies every way of one tile; used by the synthetic tests.
func truthMapFromTile(o *maps.Offline, lat0, lon0 float64) *truthMap {
	tm := newTruthMap(lat0, lon0)
	for i := 0; i < o.Ways.Len(); i++ {
		tm.addWay(o.Ways.At(i))
	}
	return tm
}

func validFix(r replayRow, maxHa float32) bool {
	return r.Fix && r.Ha > 0 && r.Ha < maxHa && !math.IsNaN(r.Lat) && !math.IsNaN(r.Lon) && (r.Lat != 0 || r.Lon != 0)
}

func allFixes(d *replayDump) []replayRow {
	out := []replayRow{}
	for _, rows := range [][]replayRow{d.ext, d.internal, d.ph} {
		for _, r := range rows {
			if validFix(r, 50) {
				out = append(out, r)
			}
		}
	}
	return out
}

type truthObs struct {
	t                      uint64
	x, y                   float64
	speed, bearing, ha, ba float64
	odo                    float64
}

// odometer integrates carState wheel speed over log time.
type odometer struct {
	t []uint64
	d []float64
}

func newOdometer(cs []replayRow) odometer {
	o := odometer{}
	total := 0.0
	for i, r := range cs {
		if i > 0 {
			dt := float64(r.Lm-cs[i-1].Lm) / 1e9
			if dt < 1 {
				total += dt * float64(r.V+cs[i-1].V) / 2
			}
		}
		o.t = append(o.t, r.Lm)
		o.d = append(o.d, total)
	}
	return o
}

func (o odometer) at(t uint64) float64 {
	if len(o.t) == 0 {
		return math.NaN()
	}
	i := sort.Search(len(o.t), func(i int) bool { return o.t[i] >= t })
	if i == 0 {
		return o.d[0]
	}
	if i == len(o.t) {
		return o.d[len(o.d)-1]
	}
	f := float64(t-o.t[i-1]) / float64(o.t[i]-o.t[i-1])
	return o.d[i-1] + f*(o.d[i]-o.d[i-1])
}

// truthObservations merges the comma's fixes with the phone's (phone only
// where the comma has none), thins to about 1 Hz and to one fix per 10 s
// while stopped. parity 0/1 keeps every other thinned fix, -1 keeps all.
func truthObservations(d *replayDump, parity int) []replayRow {
	comma := []replayRow{}
	for _, rows := range [][]replayRow{d.ext, d.internal} {
		for _, r := range rows {
			if validFix(r, 30) {
				r.Src = r.Lm
				comma = append(comma, r)
			}
		}
	}
	sort.Slice(comma, func(i, j int) bool { return comma[i].Src < comma[j].Src })
	merged := append([]replayRow{}, comma...)
	for _, r := range d.ph {
		if !validFix(r, 50) {
			continue
		}
		i := sort.Search(len(comma), func(i int) bool { return comma[i].Src >= r.Src })
		close := (i < len(comma) && comma[i].Src-r.Src < 1_500_000_000) || (i > 0 && r.Src-comma[i-1].Src < 1_500_000_000)
		if !close {
			merged = append(merged, r)
		}
	}
	sort.Slice(merged, func(i, j int) bool { return merged[i].Src < merged[j].Src })
	thinned := []replayRow{}
	for _, r := range merged {
		if n := len(thinned); n > 0 {
			prev := thinned[n-1]
			dt := r.Src - prev.Src
			if dt < 900_000_000 || (r.V < 0.5 && prev.V < 0.5 && dt < 10_000_000_000) {
				continue
			}
		}
		thinned = append(thinned, r)
	}
	if parity < 0 {
		return thinned
	}
	out := []replayRow{}
	for i, r := range thinned {
		if i%2 == parity {
			out = append(out, r)
		}
	}
	return out
}

type truthState struct {
	way  int32 // -1 off the map
	seg  int32
	s, d float64
	fwd  bool
	emit float64
}

func angleDiff(a, b float64) float64 {
	return math.Abs(math.Mod(a-b+540, 360) - 180)
}

func (tm *truthMap) states(o truthObs) []truthState {
	sig := math.Max(o.ha, 4)
	out := []truthState{}
	for _, p := range tm.near(o.x, o.y, truthRadius) {
		w := &tm.ways[p.way]
		dd := math.Max(0, p.d-w.width/2)
		base := -0.5 * (dd / sig) * (dd / sig)
		for _, fwd := range []bool{true, false} {
			e := base
			if !fwd && w.oneWay {
				e -= 10
			}
			if o.speed >= 3 && (o.ba <= 0 || o.ba < 90) {
				heading := p.bearing
				if !fwd {
					heading = math.Mod(heading+180, 360)
				}
				sd := 20 + math.Min(math.Max(o.ba, 0), 40)
				dh := angleDiff(o.bearing, heading) / sd
				e -= math.Min(12, 0.5*dh*dh)
			}
			out = append(out, truthState{way: p.way, seg: p.seg, s: p.s, d: p.d, fwd: fwd, emit: e})
		}
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].emit > out[j].emit })
	if len(out) > truthMaxStates {
		out = out[:truthMaxStates]
	}
	return append(out, truthState{way: -1, emit: truthOffroadEmit})
}

type dijkstraItem struct {
	node int32
	dist float64
}
type dijkstraHeap []dijkstraItem

func (h dijkstraHeap) Len() int           { return len(h) }
func (h dijkstraHeap) Less(i, j int) bool { return h[i].dist < h[j].dist }
func (h dijkstraHeap) Swap(i, j int)      { h[i], h[j] = h[j], h[i] }
func (h *dijkstraHeap) Push(x any)        { *h = append(*h, x.(dijkstraItem)) }
func (h *dijkstraHeap) Pop() any {
	old := *h
	x := old[len(old)-1]
	*h = old[:len(old)-1]
	return x
}

type truthSearch struct {
	dist map[int32]float64
	pred map[int32]truthEdge // edge used to reach the node; .to is the node it came from
}

// search runs a bounded Dijkstra from the position a, leaving it in its direction.
func (tm *truthMap) search(a truthState, bound float64) truthSearch {
	w := &tm.ways[a.way]
	sr := truthSearch{dist: map[int32]float64{}, pred: map[int32]truthEdge{}}
	h := &dijkstraHeap{}
	seed := func(n int32, d float64) {
		if old, ok := sr.dist[n]; !ok || d < old {
			sr.dist[n] = d
			heap.Push(h, dijkstraItem{n, d})
		}
	}
	if a.fwd {
		seed(w.nodes[a.seg+1], w.cum[a.seg+1]-a.s)
	} else {
		seed(w.nodes[a.seg], a.s-w.cum[a.seg])
	}
	for h.Len() > 0 {
		it := heap.Pop(h).(dijkstraItem)
		if it.dist > sr.dist[it.node] || it.dist > bound {
			continue
		}
		for _, e := range tm.adj[it.node] {
			nd := it.dist + e.length
			if old, ok := sr.dist[e.to]; nd <= bound && (!ok || nd < old) {
				sr.dist[e.to] = nd
				sr.pred[e.to] = truthEdge{to: it.node, way: e.way, seg: e.seg, fwd: e.fwd, length: e.length}
				heap.Push(h, dijkstraItem{e.to, nd})
			}
		}
	}
	return sr
}

// route is the driving distance from a to b (direct along one way, or via
// the graph), or -1 when b is not reachable within the search.
func (tm *truthMap) route(a, b truthState, sr truthSearch, slack float64) (float64, bool) {
	best, direct := -1.0, false
	if a.way == b.way && a.fwd == b.fwd {
		r := b.s - a.s
		if !a.fwd {
			r = a.s - b.s
		}
		if r >= -slack {
			best, direct = math.Abs(r), true
		}
	}
	w := &tm.ways[b.way]
	entry, extra := w.nodes[b.seg], b.s-w.cum[b.seg]
	if !b.fwd {
		entry, extra = w.nodes[b.seg+1], w.cum[b.seg+1]-b.s
	}
	if d, ok := sr.dist[entry]; ok && (best < 0 || d+extra < best) {
		best, direct = d+extra, false
	}
	return best, direct
}

type truthPiece struct {
	way    int32
	fwd    bool
	s0, s1 float64
}

func (p truthPiece) length() float64 { return math.Abs(p.s1 - p.s0) }

// pieces rebuilds the driven way pieces from a to b.
func (tm *truthMap) pieces(a, b truthState, sr truthSearch, direct bool) []truthPiece {
	if direct {
		return []truthPiece{{a.way, a.fwd, a.s, b.s}}
	}
	w := &tm.ways[b.way]
	entry := w.nodes[b.seg]
	if !b.fwd {
		entry = w.nodes[b.seg+1]
	}
	edges := []truthEdge{}
	for n := entry; ; {
		e, ok := sr.pred[n]
		if !ok {
			break
		}
		edges = append(edges, truthEdge{to: n, way: e.way, seg: e.seg, fwd: e.fwd})
		n = e.to
		if len(edges) > 100000 {
			break
		}
	}
	aw := &tm.ways[a.way]
	exit := aw.cum[a.seg+1]
	if !a.fwd {
		exit = aw.cum[a.seg]
	}
	out := []truthPiece{{a.way, a.fwd, a.s, exit}}
	for i := len(edges) - 1; i >= 0; i-- {
		e := edges[i]
		ew := &tm.ways[e.way]
		s0, s1 := ew.cum[e.seg], ew.cum[e.seg+1]
		if !e.fwd {
			s0, s1 = s1, s0
		}
		last := &out[len(out)-1]
		if last.way == e.way && last.fwd == e.fwd && math.Abs(last.s1-s0) < 1e-6 {
			last.s1 = s1
		} else {
			out = append(out, truthPiece{e.way, e.fwd, s0, s1})
		}
	}
	bs := w.cum[b.seg]
	if !b.fwd {
		bs = w.cum[b.seg+1]
	}
	last := &out[len(out)-1]
	if last.way == b.way && last.fwd == b.fwd && math.Abs(last.s1-bs) < 1e-6 {
		last.s1 = b.s
	} else {
		out = append(out, truthPiece{b.way, b.fwd, bs, b.s})
	}
	return out
}

// truthResult is the decoded path with per-observation confidence.
type truthResult struct {
	tm        *truthMap
	obs       []truthObs
	path      []truthState
	routes    [][]truthPiece // route from obs k to k+1 (empty off the map)
	broken    []bool         // transition k -> k+1 had no feasible road path
	wayMargin []float64
	limMargin []float64
	odo       odometer
}

func limitKey(tm *truthMap, s truthState) float64 {
	if s.way < 0 {
		return -1
	}
	return tm.ways[s.way].limit(s.fwd)
}

func wayKey(tm *truthMap, s truthState) int64 {
	if s.way < 0 {
		return 0
	}
	return tm.ways[s.way].id
}

func buildTruth(d *replayDump, tm *truthMap, rows []replayRow) *truthResult {
	odo := newOdometer(d.cs)
	res := &truthResult{tm: tm, odo: odo}
	for _, r := range rows {
		x, y := tm.project(r.Lat, r.Lon)
		res.obs = append(res.obs, truthObs{t: r.Src, x: x, y: y, speed: float64(r.V), bearing: float64(r.B), ha: float64(r.Ha), ba: float64(r.Ba), odo: odo.at(r.Src)})
	}
	n := len(res.obs)
	if n == 0 {
		return res
	}
	states := make([][]truthState, n)
	for k, o := range res.obs {
		states[k] = tm.states(o)
	}
	trans := make([][][]float64, n) // trans[k][i][j]: k-1 state i -> k state j
	directs := make([][][]bool, n)
	fwdScore := make([][]float64, n)
	back := make([][]int, n)
	res.broken = make([]bool, n)
	fwdScore[0] = make([]float64, len(states[0]))
	for j, s := range states[0] {
		fwdScore[0][j] = s.emit
	}
	for k := 1; k < n; k++ {
		prev, cur := states[k-1], states[k]
		driven := res.obs[k].odo - res.obs[k-1].odo
		if math.IsNaN(driven) {
			driven = math.Hypot(res.obs[k].x-res.obs[k-1].x, res.obs[k].y-res.obs[k-1].y)
		}
		sig := math.Max(math.Max(res.obs[k].ha, res.obs[k-1].ha), 4)
		bound := 1.5*driven + 3*sig + 30
		beta := 4 + 0.1*driven
		t := make([][]float64, len(prev))
		dr := make([][]bool, len(prev))
		feasible := false
		for i, a := range prev {
			t[i] = make([]float64, len(cur))
			dr[i] = make([]bool, len(cur))
			var sr truthSearch
			if a.way >= 0 {
				sr = tm.search(a, bound)
			}
			for j, b := range cur {
				switch {
				case a.way < 0 && b.way < 0:
					t[i][j] = -0.5
				case a.way < 0 || b.way < 0:
					t[i][j] = truthOffroadSwitch
				default:
					r, direct := tm.route(a, b, sr, 2*sig+5)
					if r < 0 {
						t[i][j] = math.Inf(-1)
						continue
					}
					t[i][j] = -math.Abs(r-driven) / beta
					dr[i][j] = direct
					feasible = true
				}
			}
		}
		// The off-road state always connects; a break is when no road-to-road
		// path exists and the best road path would have to jump.
		if !feasible {
			hasRoadPrev, hasRoadCur := false, false
			for _, a := range prev {
				hasRoadPrev = hasRoadPrev || a.way >= 0
			}
			for _, b := range cur {
				hasRoadCur = hasRoadCur || b.way >= 0
			}
			if hasRoadPrev && hasRoadCur {
				res.broken[k-1] = true
				for i := range t {
					for j := range t[i] {
						if prev[i].way >= 0 && cur[j].way >= 0 {
							t[i][j] = truthBreakPenalty
						}
					}
				}
			}
		}
		trans[k], directs[k] = t, dr
		fwdScore[k] = make([]float64, len(cur))
		back[k] = make([]int, len(cur))
		for j, b := range cur {
			best, arg := math.Inf(-1), 0
			for i := range prev {
				if v := fwdScore[k-1][i] + t[i][j]; v > best {
					best, arg = v, i
				}
			}
			fwdScore[k][j], back[k][j] = best+b.emit, arg
		}
	}
	bwd := make([][]float64, n)
	bwd[n-1] = make([]float64, len(states[n-1]))
	for k := n - 2; k >= 0; k-- {
		bwd[k] = make([]float64, len(states[k]))
		for i := range states[k] {
			best := math.Inf(-1)
			for j, b := range states[k+1] {
				if v := trans[k+1][i][j] + b.emit + bwd[k+1][j]; v > best {
					best = v
				}
			}
			bwd[k][i] = best
		}
	}
	idx := make([]int, n)
	best := math.Inf(-1)
	for j, v := range fwdScore[n-1] {
		if v > best {
			best, idx[n-1] = v, j
		}
	}
	for k := n - 1; k > 0; k-- {
		idx[k-1] = back[k][idx[k]]
	}
	res.path = make([]truthState, n)
	res.wayMargin = make([]float64, n)
	res.limMargin = make([]float64, n)
	res.routes = make([][]truthPiece, n)
	for k := range n {
		chosen := states[k][idx[k]]
		res.path[k] = chosen
		top := fwdScore[k][idx[k]] + bwd[k][idx[k]]
		altWay, altLim := math.Inf(-1), math.Inf(-1)
		for j, s := range states[k] {
			v := fwdScore[k][j] + bwd[k][j]
			if wayKey(tm, s) != wayKey(tm, chosen) {
				altWay = math.Max(altWay, v)
			}
			if math.Abs(limitKey(tm, s)-limitKey(tm, chosen)) > 0.05 {
				altLim = math.Max(altLim, v)
			}
		}
		res.wayMargin[k], res.limMargin[k] = top-altWay, top-altLim
	}
	for k := 0; k+1 < n; k++ {
		a, b := res.path[k], res.path[k+1]
		if a.way < 0 || b.way < 0 || res.broken[k] {
			continue
		}
		driven := res.obs[k+1].odo - res.obs[k].odo
		if math.IsNaN(driven) {
			driven = math.Hypot(res.obs[k+1].x-res.obs[k].x, res.obs[k+1].y-res.obs[k].y)
		}
		sig := math.Max(math.Max(res.obs[k].ha, res.obs[k+1].ha), 4)
		sr := tm.search(a, 1.5*driven+3*sig+30)
		_, direct := tm.route(a, b, sr, 2*sig+5)
		res.routes[k] = tm.pieces(a, b, sr, direct)
	}
	return res
}

// truthPoint is the truth at one moment.
type truthPoint struct {
	ok          bool
	way         int32 // -1 off the map
	fwd         bool
	s           float64
	sureWay     bool
	sureLimit   bool
	nearestObsK int
}

func (tr *truthResult) sure(k int) (bool, bool) {
	broken := tr.broken[k] || (k > 0 && tr.broken[k-1])
	return !broken && tr.wayMargin[k] >= truthSureMargin, !broken && tr.limMargin[k] >= truthSureMargin
}

func (tr *truthResult) at(t uint64) truthPoint {
	n := len(tr.obs)
	if n == 0 {
		return truthPoint{}
	}
	k := sort.Search(n, func(i int) bool { return tr.obs[i].t > t }) - 1
	if k < 0 {
		if tr.obs[0].t-t > 2_000_000_000 {
			return truthPoint{}
		}
		k = 0
	}
	if k == n-1 {
		if t-tr.obs[n-1].t > 2_000_000_000 {
			return truthPoint{}
		}
		p := tr.path[n-1]
		w, l := tr.sure(n - 1)
		return truthPoint{ok: true, way: p.way, fwd: p.fwd, s: p.s, sureWay: w, sureLimit: l, nearestObsK: n - 1}
	}
	a, b := tr.path[k], tr.path[k+1]
	wa, la := tr.sure(k)
	wb, lb := tr.sure(k + 1)
	gap := tr.obs[k+1].t - tr.obs[k].t
	sureW, sureL := wa && wb && gap <= truthMaxGapNs, la && lb && gap <= truthMaxGapNs
	f := float64(t-tr.obs[k].t) / float64(gap)
	if od := tr.obs[k+1].odo - tr.obs[k].odo; od > 0.5 && !math.IsNaN(od) {
		f = math.Max(0, math.Min(1, (tr.odo.at(t)-tr.obs[k].odo)/od))
	}
	near := k
	if f >= 0.5 {
		near = k + 1
	}
	route := tr.routes[k]
	if len(route) == 0 {
		p := tr.path[near]
		return truthPoint{ok: true, way: p.way, fwd: p.fwd, s: p.s, sureWay: sureW && a.way == b.way, sureLimit: sureL && a.way == b.way, nearestObsK: near}
	}
	total := 0.0
	for _, p := range route {
		total += p.length()
	}
	pos := f * total
	for i, p := range route {
		if pos <= p.length() || i == len(route)-1 {
			s := p.s0 + pos
			if p.s1 < p.s0 {
				s = p.s0 - pos
			}
			return truthPoint{ok: true, way: p.way, fwd: p.fwd, s: s, sureWay: sureW, sureLimit: sureL, nearestObsK: near}
		}
		pos -= p.length()
	}
	last := route[len(route)-1]
	return truthPoint{ok: true, way: last.way, fwd: last.fwd, s: last.s1, sureWay: sureW, sureLimit: sureL, nearestObsK: near}
}

// context labels the truth position: parallel road, junction, a join between
// pieces of the same named road, or ordinary mid-block. It reads only truth and map.
func (tm *truthMap) context(p truthPoint) (string, float32) {
	if !p.ok {
		return "none", 0
	}
	if p.way < 0 {
		return "offroad", 0
	}
	w := &tm.ways[p.way]
	x, y := tm.pointAt(p.way, p.s)
	axis := tm.axisAt(p.way, p.s)
	for _, q := range tm.near(x, y, contextParallelM) {
		o := &tm.ways[q.way]
		if q.way == p.way || (o.key() == w.key() && w.key() != "\x00") || q.d < 2 {
			continue
		}
		if (q.seg == 0 && q.t <= 0) || (int(q.seg) == len(o.nodes)-2 && q.t >= 1) {
			continue
		}
		if math.Abs(math.Sin((q.bearing-axis)*math.Pi/180)) < contextParallelSin {
			return "parallel", 0
		}
	}
	junction, join := math.Inf(1), math.Inf(1)
	for i, n := range w.nodes {
		dist := math.Abs(w.cum[i] - p.s)
		if dist > contextJunctionM {
			continue
		}
		others, same := 0, true
		for _, ow := range tm.nodeWays[n] {
			if ow == p.way {
				continue
			}
			others++
			if tm.ways[ow].key() != w.key() || w.key() == "\x00" {
				same = false
			}
		}
		if others == 0 {
			continue
		}
		if same {
			join = math.Min(join, dist)
		} else {
			junction = math.Min(junction, dist)
		}
	}
	if !math.IsInf(junction, 1) {
		return "junction", float32(junction)
	}
	if !math.IsInf(join, 1) {
		return "join", float32(join)
	}
	return "midblock", 0
}

// truthQuality says how far the truth itself can be trusted.
type truthQuality struct {
	Obs         int              `json:"observations"`
	Breaks      int              `json:"breaks"`
	Transitions int              `json:"way_transitions"`
	ViaGraph    int              `json:"transitions_via_graph"`
	NotAdjacent int              `json:"transitions_not_sharing_a_node"`
	UTurns      int              `json:"u_turns"`
	Frames      int              `json:"frames_with_truth"`
	OffRoad     int              `json:"frames_off_road"`
	UnsureWay   int              `json:"frames_unsure_way"`
	UnsureLimit int              `json:"frames_unsure_limit"`
	NoTruth     int              `json:"frames_without_truth"`
	Robustness  truthRobustCheck `json:"robustness"`
	Guidance    guidanceCheck    `json:"guidance"`
	Odometry    odometryCheck    `json:"odometry"`
	Yaw         yawCheck         `json:"yaw"`
}

// apply writes the truth into each frame and returns the quality counts.
func (tr *truthResult) apply(frames []replayFrame) truthQuality {
	q := truthQuality{Obs: len(tr.obs)}
	for _, b := range tr.broken {
		if b {
			q.Breaks++
		}
	}
	prevWay, prevFwd := int32(-2), false
	for k, route := range tr.routes {
		if tr.broken[k] {
			prevWay = -2
			continue
		}
		for _, p := range route {
			if prevWay >= 0 && p.way != prevWay {
				q.Transitions++
				shared := false
				for _, n := range tr.tm.ways[prevWay].nodes {
					for _, ow := range tr.tm.nodeWays[n] {
						shared = shared || ow == p.way
					}
				}
				if shared {
					q.ViaGraph++
				} else {
					q.NotAdjacent++
				}
			} else if prevWay == p.way && prevFwd != p.fwd {
				q.UTurns++
			}
			prevWay, prevFwd = p.way, p.fwd
		}
	}
	for i := range frames {
		f := &frames[i]
		p := tr.at(f.Lm)
		if !p.ok {
			q.NoTruth++
			f.Tw, f.Tfw, f.Ctx = -1, -1, "none"
			continue
		}
		q.Frames++
		f.Tw, f.Tl, f.Tn = 0, 0, ""
		if p.way >= 0 {
			w := &tr.tm.ways[p.way]
			f.Tw, f.Tl, f.Tn = w.id, float32(w.limit(p.fwd)), w.label()
		} else {
			q.OffRoad++
		}
		f.Tsw, f.Tsl = p.sureWay, p.sureLimit
		if !p.sureWay {
			q.UnsureWay++
		}
		if !p.sureLimit {
			q.UnsureLimit++
		}
		f.Ctx, f.Jd = tr.tm.context(p)
		f.Tfw, f.Tfl = -1, 0
		if f.Fix {
			if fp := tr.at(f.Gm); fp.ok {
				f.Tfw = 0
				if fp.way >= 0 {
					w := &tr.tm.ways[fp.way]
					f.Tfw, f.Tfl = w.id, float32(w.limit(fp.fwd))
				}
			}
		}
	}
	return q
}

func truthHash(frames []replayFrame) string {
	h := sha256.New()
	var b [8]byte
	for _, f := range frames {
		for _, v := range []uint64{f.Lm, uint64(f.Tw), uint64(math.Float32bits(f.Tl))} {
			binary.LittleEndian.PutUint64(b[:], v)
			h.Write(b[:])
		}
		flags := byte(0)
		if f.Tsw {
			flags |= 1
		}
		if f.Tsl {
			flags |= 2
		}
		h.Write([]byte{flags})
	}
	return hex.EncodeToString(h.Sum(nil))[:16]
}

// truthRobustCheck compares truth built from every other fix (even, odd)
// with the full truth, on frames where the full truth is sure of the way.
type truthRobustCheck struct {
	Frames        int     `json:"frames_compared"`
	EvenDisagree  int     `json:"even_disagree"`
	OddDisagree   int     `json:"odd_disagree"`
	DisagreeShare float64 `json:"disagree_share"`
}

func truthRobustness(d *replayDump, tm *truthMap, frames []replayFrame) truthRobustCheck {
	even := buildTruth(d, tm, truthObservations(d, 0))
	odd := buildTruth(d, tm, truthObservations(d, 1))
	c := truthRobustCheck{}
	id := func(tr *truthResult, t uint64) int64 {
		p := tr.at(t)
		if !p.ok {
			return -1
		}
		if p.way < 0 {
			return 0
		}
		return tm.ways[p.way].id
	}
	for i := 0; i < len(frames); i += 5 {
		f := &frames[i]
		if f.Tw < 0 || !f.Tsw {
			continue
		}
		c.Frames++
		if id(even, f.Lm) != f.Tw {
			c.EvenDisagree++
		}
		if id(odd, f.Lm) != f.Tw {
			c.OddDisagree++
		}
	}
	if c.Frames > 0 {
		c.DisagreeShare = float64(c.EvenDisagree+c.OddDisagree) / float64(2*c.Frames)
	}
	return c
}

// guidanceCheck compares the road names in the phone's turn-by-turn
// instructions with the truth path near where each instruction applies.
type guidanceCheck struct {
	Episodes  int      `json:"episodes_with_a_road_name"`
	Rerouted  int      `json:"not_reached_or_rerouted"`
	Agree     int      `json:"agree"`
	Disagree  int      `json:"disagree"`
	NoTruth   int      `json:"no_truth"`
	Unnamed   int      `json:"truth_ways_unnamed"`
	Disagrees []string `json:"disagreements,omitempty"`
}

var guidancePrefix = regexp.MustCompile(`(?i)^(stay on|continue onto|continue on|turn (left|right) onto|slight (left|right) onto|keep (left|right) onto|merge onto|take the|take exit \S+ toward|take exit \S+ for|exit \S+)\s+`)

func guidanceRoad(text string) (string, bool) {
	text = strings.TrimSpace(strings.SplitN(text, "\n", 2)[0])
	current := strings.HasPrefix(strings.ToLower(text), "stay on")
	for {
		stripped := guidancePrefix.ReplaceAllString(text, "")
		if stripped == text {
			break
		}
		text = stripped
	}
	text = regexp.MustCompile(`(?i)\s+(exit|ramp.*)$`).ReplaceAllString(text, "")
	if text == "" || regexp.MustCompile(`^\d`).MatchString(text) {
		return "", current
	}
	low := strings.ToLower(text)
	for _, skip := range []string{"head ", "arrive", "destination", "slight right", "slight left", "turn left", "turn right"} {
		if strings.HasPrefix(low, skip) {
			return "", current
		}
	}
	return text, current
}

var roadWordMap = map[string]string{"n": "north", "s": "south", "e": "east", "w": "west", "dr": "drive", "ave": "avenue",
	"blvd": "boulevard", "st": "street", "ln": "lane", "rd": "road", "fwy": "freeway", "pkwy": "parkway", "hwy": "highway",
	"ct": "court", "jr": "junior", "pl": "place", "cir": "circle"}

func roadTokens(s string) map[string]bool {
	s = strings.ToLower(s)
	s = regexp.MustCompile(`[^a-z0-9]+`).ReplaceAllString(s, " ")
	out := map[string]bool{}
	for _, t := range strings.Fields(s) {
		if v, ok := roadWordMap[t]; ok {
			t = v
		}
		switch t {
		case "north", "south", "east", "west", "us", "i", "sr", "nv":
			continue
		}
		out[t] = true
	}
	return out
}

// sameRoadName is true when every significant word of one name is in the other.
func sameRoadName(guidance string, way *truthWay) bool {
	for _, alt := range strings.Split(guidance, "/") {
		g := roadTokens(alt)
		if len(g) == 0 {
			continue
		}
		for _, n := range append([]string{way.name}, strings.Split(way.ref, ";")...) {
			w := roadTokens(n)
			if len(w) == 0 {
				continue
			}
			sub := func(a, b map[string]bool) bool {
				for t := range a {
					if !b[t] {
						return false
					}
				}
				return true
			}
			if sub(g, w) || sub(w, g) {
				return true
			}
		}
	}
	return false
}

func checkGuidance(d *replayDump, tr *truthResult) guidanceCheck {
	c := guidanceCheck{}
	type episode struct {
		text        string
		first, last replayRow
	}
	eps := []episode{}
	for _, r := range d.nav {
		if r.Status != "guiding" || r.Text == "" {
			continue
		}
		if n := len(eps); n > 0 && eps[n-1].text == r.Text && r.Lm-eps[n-1].last.Lm < 30_000_000_000 {
			eps[n-1].last = r
			continue
		}
		eps = append(eps, episode{r.Text, r, r})
	}
	for _, e := range eps {
		road, current := guidanceRoad(e.text)
		if road == "" {
			continue
		}
		c.Episodes++
		if !current && e.last.Dist > guidanceReachedM {
			// The instruction changed before its maneuver: a re-route or a new phrasing.
			c.Rerouted++
			continue
		}
		var from, to float64
		base := tr.odo.at(e.last.Lm)
		if current {
			from, to = tr.odo.at(e.first.Lm), base+float64(e.last.Dist)
		} else {
			from, to = base+float64(e.last.Dist)-50, base+float64(e.last.Dist)+guidanceAfterM
		}
		seen, unnamed, agree := 0, 0, false
		names := map[string]bool{}
		for k, o := range tr.obs {
			if o.odo < from || o.odo > to || tr.path[k].way < 0 {
				continue
			}
			if sw, _ := tr.sure(k); !sw {
				continue
			}
			w := &tr.tm.ways[tr.path[k].way]
			seen++
			if w.key() == "\x00" {
				unnamed++
				continue
			}
			names[w.label()] = true
			agree = agree || sameRoadName(road, w)
		}
		switch {
		case seen == 0:
			c.NoTruth++
		case agree:
			c.Agree++
		case unnamed == seen:
			c.Unnamed++
		default:
			c.Disagree++
			list := []string{}
			for n := range names {
				list = append(list, n)
			}
			sort.Strings(list)
			c.Disagrees = append(c.Disagrees, e.text+" -> truth "+strings.Join(list, ", "))
		}
	}
	return c
}

// odometryCheck compares wheel distance with the truth path length between
// consecutive sure fixes; dead reckoning along a way needs only this scale.
type odometryCheck struct {
	Pairs       int     `json:"pairs"`
	RatioP10    float64 `json:"route_over_wheel_p10"`
	RatioP50    float64 `json:"route_over_wheel_p50"`
	RatioP90    float64 `json:"route_over_wheel_p90"`
	ResidualP50 float64 `json:"abs_residual_m_p50"`
	ResidualP90 float64 `json:"abs_residual_m_p90"`
	PerKmDriftM float64 `json:"median_drift_m_per_km"`
	MeanWheelM  float64 `json:"mean_wheel_m_per_pair"`
}

func percentile(v []float64, p float64) float64 {
	if len(v) == 0 {
		return math.NaN()
	}
	s := append([]float64{}, v...)
	sort.Float64s(s)
	return s[min(len(s)-1, int(p*float64(len(s))))]
}

func checkOdometry(tr *truthResult) odometryCheck {
	c := odometryCheck{}
	ratios, res := []float64{}, []float64{}
	wheel := 0.0
	for k, route := range tr.routes {
		if len(route) == 0 {
			continue
		}
		wa, _ := tr.sure(k)
		wb, _ := tr.sure(k + 1)
		driven := tr.obs[k+1].odo - tr.obs[k].odo
		if !wa || !wb || driven < 8 || math.IsNaN(driven) {
			continue
		}
		l := 0.0
		for _, p := range route {
			l += p.length()
		}
		ratios = append(ratios, l/driven)
		res = append(res, math.Abs(l-driven))
		wheel += driven
	}
	c.Pairs = len(ratios)
	if c.Pairs == 0 {
		return c
	}
	c.RatioP10, c.RatioP50, c.RatioP90 = percentile(ratios, 0.1), percentile(ratios, 0.5), percentile(ratios, 0.9)
	c.ResidualP50, c.ResidualP90 = percentile(res, 0.5), percentile(res, 0.9)
	c.PerKmDriftM = math.Abs(c.RatioP50-1) * 1000
	c.MeanWheelM = wheel / float64(c.Pairs)
	return c
}

// yawCheck correlates candidate yaw-rate signals with the comma GPS bearing rate.
type yawCheck struct {
	Samples      int                `json:"samples"`
	CarYawZero   float64            `json:"carstate_yawrate_zero_share"`
	Correlation  map[string]float64 `json:"correlation"`
	SlopeDegPerS map[string]float64 `json:"slope"`
}

func checkYaw(d *replayDump) yawCheck {
	c := yawCheck{Correlation: map[string]float64{}, SlopeDegPerS: map[string]float64{}}
	zero := 0
	for _, r := range d.cs {
		if r.Yr == 0 {
			zero++
		}
	}
	if len(d.cs) > 0 {
		c.CarYawZero = float64(zero) / float64(len(d.cs))
	}
	near := func(rows []replayRow, t uint64) (replayRow, bool) {
		i := sort.Search(len(rows), func(i int) bool { return rows[i].Lm >= t })
		if i >= len(rows) {
			return replayRow{}, false
		}
		dt := int64(rows[i].Lm) - int64(t)
		return rows[i], dt < 150_000_000 && dt > -150_000_000
	}
	signals := map[string][][2]float64{}
	for i := 1; i < len(d.ext); i++ {
		a, b := d.ext[i-1], d.ext[i]
		dt := float64(b.Lm-a.Lm) / 1e9
		if !validFix(a, 10) || !validFix(b, 10) || a.V < 5 || b.V < 5 || dt <= 0 || dt > 0.5 {
			continue
		}
		rate := (math.Mod(float64(b.B-a.B)+540, 360) - 180) / dt
		mid := (a.Lm + b.Lm) / 2
		if cs, ok := near(d.cs, mid); ok {
			signals["carstate.yawRate"] = append(signals["carstate.yawRate"], [2]float64{rate, float64(cs.Yr) * 180 / math.Pi})
			signals["speed*steeringAngle"] = append(signals["speed*steeringAngle"], [2]float64{rate, float64(cs.V * cs.Sa)})
		}
		if dm, ok := near(d.dm, mid); ok {
			signals["deviceMotion.x"] = append(signals["deviceMotion.x"], [2]float64{rate, float64(dm.Wx) * 180 / math.Pi})
			signals["deviceMotion.y"] = append(signals["deviceMotion.y"], [2]float64{rate, float64(dm.Wy) * 180 / math.Pi})
			signals["deviceMotion.z"] = append(signals["deviceMotion.z"], [2]float64{rate, float64(dm.Wz) * 180 / math.Pi})
		}
	}
	for name, pairs := range signals {
		c.Samples = max(c.Samples, len(pairs))
		var sx, sy, sxx, syy, sxy float64
		for _, p := range pairs {
			sx, sy, sxx, syy, sxy = sx+p[0], sy+p[1], sxx+p[0]*p[0], syy+p[1]*p[1], sxy+p[0]*p[1]
		}
		n := float64(len(pairs))
		den := math.Sqrt((n*sxx - sx*sx) * (n*syy - sy*sy))
		if den > 0 {
			c.Correlation[name] = math.Round((n*sxy-sx*sy)/den*1000) / 1000
		}
		if sxx > 0 {
			c.SlopeDegPerS[name] = math.Round(sxy/sxx*1000) / 1000
		}
	}
	return c
}

const (
	reachMaxAgeS   = 30.0
	reachMaxDriven = 600.0
)

// reachabilityEstimate approximates stage-2 change (3) on the frames the
// matcher left ambiguous: starting from the last matched way, a candidate
// counts only if it is heading-aligned and the road graph reaches it within
// the wheel distance driven since. One reachable road (or one shared limit) is published and becomes
// the next anchor; anything else fails closed. It uses the tile graph and the
// fix, never the truth path.
func reachabilityEstimate(tm *truthMap, odo odometer, frames []replayFrame) {
	type anchor struct {
		ok   bool
		way  int64
		x, y float64
		fwd  bool
		odo  float64
		t    float64
	}
	var a anchor
	var lastGm uint64
	var lastLim float32
	var lastOk bool
	for i := range frames {
		f := &frames[i]
		if !f.Fix {
			a.ok = false
			continue
		}
		if f.SrcCh {
			a.ok = false
		}
		x, y := tm.project(f.Lat, f.Lon)
		now := odo.at(f.Gm)
		if f.matched() {
			if w, ok := tm.byID[f.Way]; ok {
				p := tm.nearestOn(w, x, y)
				fwd := true
				if f.Spd >= 3 {
					fwd = angleDiff(float64(f.Brg), p.bearing) < 90
				} else if a.ok && a.way == f.Way {
					fwd = a.fwd
				}
				a = anchor{true, f.Way, x, y, fwd, now, f.T}
			}
			continue
		}
		if f.Sim != "unknown" && f.Sim != "noMatch" || len(f.Cands) == 0 {
			continue
		}
		if !f.New && f.Gm == lastGm {
			f.R3, f.R3ok = lastLim, lastOk
			continue
		}
		lastGm, lastLim, lastOk = f.Gm, 0, false
		driven := now - a.odo
		if !a.ok || f.T-a.t > reachMaxAgeS || math.IsNaN(driven) || driven > reachMaxDriven {
			continue
		}
		aw, ok := tm.byID[a.way]
		if !ok {
			continue
		}
		ap := tm.nearestOn(aw, a.x, a.y)
		from := truthState{way: aw, seg: ap.seg, s: ap.s, fwd: a.fwd}
		slack := 10 + 0.1*driven + math.Max(float64(f.Ha), 5)
		sr := tm.search(from, driven+slack+20)
		reach := []replayCand{}
		for _, c := range f.Cands {
			cw, ok := tm.byID[c.Id]
			if !ok || (f.Spd >= 3 && c.Sin > headingSine) {
				continue
			}
			cp := tm.nearestOn(cw, x, y)
			for _, fwd := range []bool{true, false} {
				if !fwd && tm.ways[cw].oneWay {
					continue
				}
				to := truthState{way: cw, seg: cp.seg, s: cp.s, fwd: fwd}
				if r, _ := tm.route(from, to, sr, slack); r >= 0 && math.Abs(r-driven) <= slack {
					reach = append(reach, c)
					break
				}
			}
		}
		if len(reach) == 0 || !sameLimited(reach) {
			continue
		}
		lastLim, lastOk = reach[0].Lim, true
		f.R3, f.R3ok = lastLim, true
		ids := map[int64]bool{}
		for _, c := range reach {
			ids[c.Id] = true
		}
		if len(ids) == 1 {
			cw := tm.byID[reach[0].Id]
			cp := tm.nearestOn(cw, x, y)
			fwd := a.fwd
			if f.Spd >= 3 {
				fwd = angleDiff(float64(f.Brg), cp.bearing) < 90
			}
			a = anchor{true, reach[0].Id, x, y, fwd, now, f.T}
		}
	}
}

func (tm *truthMap) nearestOn(w int32, x, y float64) truthProj {
	way := &tm.ways[w]
	best := truthProj{d: math.Inf(1)}
	for seg := int32(0); int(seg) < len(way.nodes)-1; seg++ {
		if p := tm.projectSeg(way, seg, x, y); p.d < best.d {
			best = p
		}
	}
	best.way = w
	return best
}
