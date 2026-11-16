package main

import (
	"cmp"
	"math"
	"slices"

	"pfeifer.dev/mapd/cereal/log"
	"pfeifer.dev/mapd/maps"
	m "pfeifer.dev/mapd/math"
	ms "pfeifer.dev/mapd/settings"
)

// Navigation-only road choice for fixes the conservative matcher does not
// match. It selects the road whose junctions corroborate phone guidance;
// it never becomes CurrentWay, a speed limit, curve output or a valid event.
const (
	navigationMinSpeed        = 3                  // m/s; slower headings are noise
	navigationMaxHeadingSine  = 0.5735764363510461 // sin(35 deg)
	navigationMinMargin       = 8                  // metres between nearest and next road
	navigationApproachMeters  = 120                // look-ahead for a road crossed from unmapped ground
	navigationApproachSpan    = 30                 // further crossings within this count as one junction
	navigationApproachMinSine = 0.5                // sin(30 deg); shallower crossings are not junctions
)

type navigationCandidate struct {
	way      maps.Way
	distance float32
}

// navigationHold is the road the last navigation junctions came from.
type navigationHold struct {
	id        int64
	name, ref string
}

func holdOf(w *maps.Way) navigationHold {
	return navigationHold{w.Id(), w.WayName(), w.WayRef()}
}

// continues is true for the held way or another piece of the same named road.
func (h navigationHold) continues(w *maps.Way) bool {
	return h.id != 0 && (w.Id() == h.id || (h.name != "" && w.WayName() == h.name) || (h.ref != "" && w.WayRef() == h.ref))
}

type navigationReason int

const (
	navigationPicked navigationReason = iota
	navigationNoCandidates
	navigationNoAligned
	navigationSlowNotHeld
	navigationTooClose
	navigationOutsideNarrow
)

func navigationMoving(location log.GpsLocationData) bool {
	return location.Speed() >= navigationMinSpeed && location.BearingAccuracyDeg() < 180
}

// navigationPick returns the way the junction list should be built from, or
// why there is none. held keeps the previous road through near ties and stops.
func navigationPick(offlineMaps *maps.Offline, location log.GpsLocationData, held navigationHold) (CurrentWay, navigationReason) {
	ways := plausibleLoadedWays(offlineMaps, location)
	if len(ways) == 0 {
		return CurrentWay{}, navigationNoCandidates
	}
	moving := navigationMoving(location)
	candidates := make([]navigationCandidate, 0, len(ways))
	for _, way := range ways {
		if moving {
			sine, err := way.BearingAlignment(location)
			if err != nil || sine > navigationMaxHeadingSine {
				continue
			}
		} else if !held.continues(&way) {
			continue
		}
		onWay, err := way.OnWay(location, 2)
		if err != nil || !onWay.OnWay {
			continue
		}
		candidates = append(candidates, navigationCandidate{way, onWay.Distance.Distance})
	}
	if len(candidates) == 0 {
		if moving {
			return CurrentWay{}, navigationNoAligned
		}
		return CurrentWay{}, navigationSlowNotHeld
	}
	slices.SortStableFunc(candidates, func(a, b navigationCandidate) int { return cmp.Compare(a.distance, b.distance) })
	best := candidates[0].way
	if len(candidates) > 1 && !sameNamedRoad(candidates) {
		margin := max(float32(navigationMinMargin), location.HorizontalAccuracy())
		heldAt := slices.IndexFunc(candidates, func(c navigationCandidate) bool { return held.continues(&c.way) })
		if heldAt > 0 && candidates[heldAt].distance-candidates[0].distance < margin {
			// Hysteresis: another road must be closer by the margin to take over.
			best = candidates[heldAt].way
		} else if heldAt != 0 && candidates[1].distance-candidates[0].distance < margin {
			return CurrentWay{}, navigationTooClose
		}
	}
	// The chosen road still needs the matcher's own narrow selection envelope.
	onWay, err := best.OnWay(location, best.DistanceMultiplier())
	if err != nil || !onWay.OnWay {
		return CurrentWay{}, navigationOutsideNarrow
	}
	return CurrentWay{Way: best, Distance: onWay.Distance, OnWay: onWay}, navigationPicked
}

// sameNamedRoad is true when every candidate is a piece of one named road,
// such as consecutive OSM ways of a freeway split at each bridge.
func sameNamedRoad(candidates []navigationCandidate) bool {
	key := func(w *maps.Way) string { return w.WayName() + "\x00" + w.WayRef() }
	first := key(&candidates[0].way)
	if first == "\x00" {
		return false
	}
	for i := range candidates[1:] {
		if key(&candidates[i+1].way) != first {
			return false
		}
	}
	return true
}

type approachCrossing struct {
	way    maps.Way
	along  float64
	x, y   float64
	dx, dy float64
}

// navigationApproach lists the roads crossed straight ahead when the car moves
// on ground the tile does not map (driveways, parking lots, service roads).
// Each crossed road is offered in both directions it can be driven.
func navigationApproach(offlineMaps *maps.Offline, location log.GpsLocationData) []navigationBranch {
	if offlineMaps == nil || !offlineMaps.Loaded || !navigationMoving(location) {
		return nil
	}
	lat0, lon0 := location.Latitude(), location.Longitude()
	kx := math.Cos(lat0*ms.TO_RADIANS) * ms.R * ms.TO_RADIANS
	ky := ms.R * ms.TO_RADIANS
	heading := float64(location.BearingDeg()) * ms.TO_RADIANS
	hx, hy := math.Sin(heading), math.Cos(heading)
	ex, ey := hx*navigationApproachMeters, hy*navigationApproachMeters
	ray := m.Box{
		MinPos: m.NewPosition(lat0+min(0, ey)/ky, lon0+min(0, ex)/kx),
		MaxPos: m.NewPosition(lat0+max(0, ey)/ky, lon0+max(0, ex)/kx),
	}
	candidates, indexed := offlineMaps.WaysNear(m.NewPosition(lat0, lon0))
	count := len(candidates)
	if !indexed {
		count = offlineMaps.Ways.Len()
	}
	crossings := []approachCrossing{}
	for c := range count {
		i := c
		if indexed {
			i = int(candidates[c])
		}
		if !offlineMaps.WayBoxOverlaps(i, ray) {
			continue
		}
		way := offlineMaps.Ways.At(i)
		if way.Id() <= 0 || way.Nodes.Len() < 2 {
			continue
		}
		first := way.Nodes.At(0)
		px, py := (first.Lon()-lon0)*kx, (first.Lat()-lat0)*ky
		for n := 1; n < way.Nodes.Len(); n++ {
			node := way.Nodes.At(n)
			qx, qy := (node.Lon()-lon0)*kx, (node.Lat()-lat0)*ky
			sx, sy := qx-px, qy-py
			length := math.Hypot(sx, sy)
			denom := hx*sy - hy*sx // heading x segment
			if length > 0 && math.Abs(denom) >= navigationApproachMinSine*length {
				along := (px*sy - py*sx) / denom
				u := (px*hy - py*hx) / denom
				if along > 0 && along <= navigationApproachMeters && u >= 0 && u <= 1 {
					crossings = append(crossings, approachCrossing{way, along, px + u*sx, py + u*sy, sx / length, sy / length})
				}
			}
			px, py = qx, qy
		}
	}
	if len(crossings) == 0 {
		return nil
	}
	slices.SortStableFunc(crossings, func(a, b approachCrossing) int { return cmp.Compare(a.along, b.along) })
	result := []navigationBranch{}
	seen := map[int64]bool{}
	for _, c := range crossings {
		if c.along > crossings[0].along+navigationApproachSpan || len(result) >= maxNavigationBranches-1 {
			break
		}
		if seen[c.way.Id()] {
			continue
		}
		seen[c.way.Id()] = true
		junction := m.NewPosition(lat0+c.y/ky, lon0+c.x/kx)
		for _, sign := range []float64{1, -1} {
			if sign < 0 && c.way.OneWay() {
				continue
			}
			bearing := math.Atan2(sign*c.dx, sign*c.dy)
			angle := math.Mod((bearing-heading)*180/math.Pi+540, 360) - 180
			result = append(result, navigationBranch{c.way, junction, float32(c.along), float32(angle)})
		}
	}
	return result
}
