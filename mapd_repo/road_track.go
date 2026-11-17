package main

import (
	"math"
	"slices"

	"pfeifer.dev/mapd/cereal/log"
	"pfeifer.dev/mapd/maps"
	m "pfeifer.dev/mapd/math"
	ms "pfeifer.dev/mapd/settings"
)

// Road tracking settles a fix that fits several roads from the car's heading,
// the road it was last matched to and what joins that road. It never guesses:
// when those do not single out one road the fix stays unmatched.
const (
	trackMinSpeed       = 3                  // m/s; slower headings are noise
	trackMaxHeadingSine = 0.5735764363510461 // sin(35 deg)
	trackMinMargin      = 8                  // metres another road must be closer by to take over
	trackMaxDriven      = 150                // metres driven without a match before the track is dropped
	trackMaxAgeNs       = 10_000_000_000
	trackLimitTolerance = 0.1         // m/s
	trackConfirmNs      = 700_000_000 // two fixes at 1 Hz, several at 5 Hz
)

type roadTrack struct {
	way         maps.Way
	forward     bool
	nodes       []m.Position // nodes of the way and of the ways ahead of it
	ahead       []int64
	fixMonoTime uint64
	driven      float32
	valid       bool
	limit       float64
	unsure      bool  // a road with another limit was about as close on the last fix
	pending     int64 // road waiting to be confirmed before it is published
	pendingAt   uint64
	held        bool // the last match was the slow-speed hold, not fresh evidence
}

func (t *roadTrack) clear() { *t = roadTrack{} }

func (t *roadTrack) set(way CurrentWay, next []maps.NextWayResult, fixMonoTime uint64) {
	t.limit, t.unsure, t.pending = way.EffectiveMaxSpeed(), false, 0
	if t.valid && t.way.Id() == way.Way.Id() && len(t.ahead) == len(next) {
		t.forward, t.fixMonoTime, t.driven = way.OnWay.IsForward, fixMonoTime, 0
		return
	}
	t.way, t.forward, t.fixMonoTime, t.driven, t.valid = way.Way, way.OnWay.IsForward, fixMonoTime, 0, true
	t.nodes, t.ahead = t.nodes[:0], t.ahead[:0]
	for i := range way.Way.Nodes.Len() {
		t.nodes = append(t.nodes, way.Way.Nodes.At(i))
	}
	for _, n := range next {
		t.ahead = append(t.ahead, n.Way.Id())
		for i := range n.Way.Nodes.Len() {
			t.nodes = append(t.nodes, n.Way.Nodes.At(i))
		}
	}
}

// confirmed is true once the same road has been chosen on every fix for trackConfirmNs.
func (t *roadTrack) confirmed(id int64, fixMonoTime uint64) bool {
	if t.pending != id || fixMonoTime < t.pendingAt {
		t.pending, t.pendingAt = id, fixMonoTime
	}
	return fixMonoTime-t.pendingAt >= trackConfirmNs
}

// touch keeps the track alive through a fix that is not published.
func (t *roadTrack) touch(fixMonoTime uint64) { t.fixMonoTime, t.driven = fixMonoTime, 0 }

func (t *roadTrack) usable(fixMonoTime uint64) bool {
	return t != nil && t.valid && t.driven <= trackMaxDriven && fixMonoTime >= t.fixMonoTime && fixMonoTime-t.fixMonoTime <= trackMaxAgeNs
}

// reaches is true for the tracked road, a road ahead of it, or one sharing a node with either.
func (t *roadTrack) reaches(w *maps.Way) bool {
	if w.Id() == t.way.Id() || slices.Contains(t.ahead, w.Id()) {
		return true
	}
	for i := range w.Nodes.Len() {
		node := w.Nodes.At(i)
		for j := range t.nodes {
			if t.nodes[j].Equals(node) {
				return true
			}
		}
	}
	return false
}

func trackMoving(location log.GpsLocationData) bool {
	return location.Speed() >= trackMinSpeed && location.BearingAccuracyDeg() < 180
}

// heldWhileSlow keeps the tracked road, as the loaded tile now has it, while the
// car is too slow for its heading to mean anything and is still beside that road.
func (t *roadTrack) heldWhileSlow(offlineMaps *maps.Offline, location log.GpsLocationData) (maps.Way, maps.OnWayResult, bool) {
	if location.Speed() >= trackMinSpeed || offlineMaps == nil || !offlineMaps.Loaded {
		return maps.Way{}, maps.OnWayResult{}, false
	}
	pos := m.NewPosition(location.Latitude(), location.Longitude())
	near, indexed := offlineMaps.WaysNear(pos)
	count := len(near)
	if !indexed {
		count = offlineMaps.Ways.Len()
	}
	for c := range count {
		i := c
		if indexed {
			i = int(near[c])
		}
		way := offlineMaps.Ways.At(i)
		if way.Id() != t.way.Id() || way.Nodes.Len() < 2 {
			continue
		}
		d, err := way.DistanceFrom(pos)
		if err != nil || d.Distance >= maps.MatchDistance(location, way.Width(), 2) {
			return maps.Way{}, maps.OnWayResult{}, false
		}
		return way, maps.OnWayResult{OnWay: true, Distance: d, IsForward: t.forward}, true
	}
	return maps.Way{}, maps.OnWayResult{}, false
}

type trackCandidate struct {
	way      maps.Way
	distance float32
	limit    float64
}

// decided is true when the nearest road is safe to take from a sorted list: it is the
// only one, or every road within the margin of it carries the same limit.
func decided(c []trackCandidate, margin float32) bool {
	for i := range c[1:] {
		if c[i+1].distance-c[0].distance < margin && math.Abs(c[i+1].limit-c[0].limit) > trackLimitTolerance {
			return false
		}
	}
	return true
}

// resolveWay picks one of several roads that fit the fix. keep reports that the
// tracked road is still plausible, so the track stays alive without a match.
func resolveWay(ways []maps.Way, location log.GpsLocationData, track *roadTrack, tracking bool) (way maps.Way, limit float64, ok, keep bool) {
	if !trackMoving(location) {
		return maps.Way{}, 0, false, false
	}
	aligned := make([]trackCandidate, 0, len(ways))
	for _, way := range ways {
		onWay, err := way.OnWay(location, 2)
		if err != nil || !onWay.OnWay {
			continue
		}
		axis := onWay.Distance.LineStart.VectorTo(onWay.Distance.LineEnd)
		if math.Abs(math.Sin(float64(location.BearingDeg())*ms.TO_RADIANS-axis.Bearing())) > trackMaxHeadingSine {
			continue
		}
		directed := CurrentWay{Way: way, OnWay: onWay}
		aligned = append(aligned, trackCandidate{way, onWay.Distance.Distance, directed.EffectiveMaxSpeed()})
	}
	if len(aligned) == 0 {
		return maps.Way{}, 0, false, false
	}
	slices.SortStableFunc(aligned, func(a, b trackCandidate) int {
		switch {
		case a.distance < b.distance:
			return -1
		case a.distance > b.distance:
			return 1
		}
		return 0
	})
	margin := max(float32(trackMinMargin), location.HorizontalAccuracy())
	if !tracking {
		// No history: the nearest road, and only when nothing near it disagrees on the limit.
		return aligned[0].way, aligned[0].limit, decided(aligned, margin), false
	}
	held := slices.IndexFunc(aligned, func(c trackCandidate) bool { return c.way.Id() == track.way.Id() })
	pool := aligned
	if held >= 0 {
		gap := aligned[held].distance - aligned[0].distance
		if gap < margin {
			// The tracked road stays, unless a road with another limit is about as close.
			rest := append([]trackCandidate{aligned[held]}, aligned[:held]...)
			rest = append(rest, aligned[held+1:]...)
			for i := range rest[1:] {
				near := rest[i+1].distance-aligned[held].distance < margin
				if near && math.Abs(rest[i+1].limit-aligned[held].limit) > trackLimitTolerance {
					return maps.Way{}, 0, false, true
				}
			}
			return aligned[held].way, aligned[held].limit, true, true
		}
		// Only a road clearly closer than the tracked one may replace it.
		pool = aligned[:slices.IndexFunc(aligned, func(c trackCandidate) bool { return aligned[held].distance-c.distance < margin })]
	}
	// The nearest road that joins the tracked one, unless a road with another
	// limit is nearer or about as near, whether or not it joins.
	for _, c := range pool {
		if !track.reaches(&c.way) {
			continue
		}
		for _, other := range aligned {
			if other.distance-c.distance < margin && math.Abs(other.limit-c.limit) > trackLimitTolerance {
				return maps.Way{}, 0, false, false
			}
		}
		return c.way, c.limit, true, false
	}
	return maps.Way{}, 0, false, false
}
