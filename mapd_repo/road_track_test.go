package main

import (
	"fmt"
	"testing"
	"time"

	"pfeifer.dev/mapd/maps"
	m "pfeifer.dev/mapd/math"
)

type trackDrive struct {
	t    *testing.T
	s    *State
	tile maps.Offline
	mono uint64
}

func newTrackDrive(t *testing.T, ways []syntheticWay) *trackDrive {
	s := &State{}
	s.Init()
	return &trackDrive{t: t, s: s, tile: packedRoadTile(t, ways), mono: 10_000_000_000}
}

// fix feeds one new fix heading north and returns "way/limit", or "none".
func (d *trackDrive) fix(lat, lon float64, speed float32) string {
	d.t.Helper()
	mono := d.mono
	d.mono += 1_000_000_000
	d.s.bootNow = func() uint64 { return mono + 100_000_000 }
	sample := ambiguitySample(d.t, mono, d.s.GpsMonoTime == 0)
	sample.Location.SetLatitude(lat)
	sample.Location.SetLongitude(lon)
	sample.Location.SetSpeed(speed)
	d.s.ProcessGps(sample, true, func(m.Position) (maps.Offline, error) { return d.tile, nil }, time.Unix(0, 0))
	event, out := decodedOutput(d.t, d.s)
	if !event.Valid() || !d.s.RoadMatched() {
		if out.WayId() != 0 || out.SpeedLimit() != 0 {
			d.t.Fatalf("unmatched fix published way=%d limit=%v", out.WayId(), out.SpeedLimit())
		}
		return "none"
	}
	return fmt.Sprintf("%d/%.0f", out.WayId(), out.SpeedLimit())
}

func (d *trackDrive) expect(what, want, got string) {
	d.t.Helper()
	if got != want {
		d.t.Fatalf("%s: got %s, want %s", what, got, want)
	}
}

func TestTrackParallelRoadsMatchOnlyWhenLimitsAgree(t *testing.T) {
	main := syntheticWay{1, 20, 35.1, -97.9, 35.2, -97.9, false, 2}
	same := newTrackDrive(t, []syntheticWay{main, {2, 20, 35.1, -97.90005, 35.2, -97.90005, false, 2}})
	same.expect("first fix among several roads waits", "none", same.fix(35.15, -97.9, 20))
	same.expect("second fix confirms", "1/20", same.fix(35.1502, -97.9, 20))

	differ := newTrackDrive(t, []syntheticWay{main, {2, 30, 35.1, -97.90005, 35.2, -97.90005, false, 2}})
	for i := range 4 {
		differ.expect("near tie with another limit", "none", differ.fix(35.15+float64(i)*0.0002, -97.9, 20))
	}
}

func TestTrackCrossingRoadIgnoredByHeadingOnlyWhileMoving(t *testing.T) {
	ways := []syntheticWay{{1, 8, 35.1, -97.9, 35.2, -97.9, false, 2}, {2, 30, 35.15, -97.95, 35.15, -97.85, false, 6}}
	d := newTrackDrive(t, ways)
	d.expect("first fix waits", "none", d.fix(35.15, -97.9, 20))
	d.expect("heading picks the road", "1/8", d.fix(35.15003, -97.9, 20))
	d.expect("stopped keeps the tracked road", "1/8", d.fix(35.15003, -97.9, 0))

	fresh := newTrackDrive(t, ways)
	fresh.expect("stopped with no history", "none", fresh.fix(35.15, -97.9, 0))
	fresh.expect("still stopped", "none", fresh.fix(35.15, -97.9, 0))
}

func TestTrackDoesNotJumpToUnconnectedRoadWithAnotherLimit(t *testing.T) {
	d := newTrackDrive(t, []syntheticWay{{1, 20, 35.1, -97.9, 35.2, -97.9, false, 2}, {2, 30, 35.1, -97.90015, 35.2, -97.90015, false, 2}})
	d.expect("first fix waits", "none", d.fix(35.15, -97.89995, 20))
	d.expect("clearly nearest", "1/20", d.fix(35.1502, -97.89995, 20))
	for i := range 4 {
		d.expect("beside the other road", "none", d.fix(35.151+float64(i)*0.0002, -97.90014, 20))
	}
}

func TestTrackForkWithDifferentLimitsWaitsUntilClear(t *testing.T) {
	d := newTrackDrive(t, []syntheticWay{
		{1, 20, 35.14, -97.9, 35.15, -97.9, false, 2},
		{2, 30, 35.15, -97.9, 35.16, -97.9, false, 2},
		{3, 13, 35.15, -97.9, 35.16, -97.899, false, 2},
	})
	d.expect("before the fork", "1/20", d.fix(35.149, -97.9, 20))
	d.expect("branches still together", "none", d.fix(35.1503, -97.89998, 20))
	d.expect("first clear fix waits", "none", d.fix(35.152, -97.8998, 20))
	d.expect("second clear fix takes the branch", "3/13", d.fix(35.1522, -97.89978, 20))
}

func TestTrackHoldIsBoundedAndOnlyWhenSlow(t *testing.T) {
	ways := []syntheticWay{{1, 8, 35.1, -97.9, 35.2, -97.9, false, 2}, {2, 30, 35.1, -97.90005, 35.2, -97.90005, false, 2}}
	d := newTrackDrive(t, ways[:1])
	d.expect("tracked", "1/8", d.fix(35.15, -97.9, 20))
	d.tile = packedRoadTile(t, ways)
	d.s.Data = d.tile
	for range 20 {
		d.expect("held while stopped", "1/8", d.fix(35.15, -97.9, 0))
	}
	d.s.track.driven = trackMaxDriven - 1
	d.expect("hold does not refill the distance budget", "1/8", d.fix(35.15, -97.9, 1))
	if d.s.track.driven < trackMaxDriven-1 {
		t.Fatalf("hold reset driven distance to %v", d.s.track.driven)
	}
	d.s.track.driven = trackMaxDriven + 1
	d.expect("crept past the budget", "none", d.fix(35.15, -97.9, 1))

	fast := newTrackDrive(t, ways[:1])
	fast.expect("tracked", "1/8", fast.fix(35.15, -97.9, 20))
	fast.tile = packedRoadTile(t, ways)
	fast.s.Data = fast.tile
	mono := fast.mono
	fast.s.bootNow = func() uint64 { return mono + 100_000_000 }
	sample := ambiguitySample(t, mono, false)
	sample.Location.SetSpeed(20)
	sample.Location.SetBearingAccuracyDeg(180)
	fast.s.ProcessGps(sample, true, nil, time.Unix(0, 0))
	if fast.s.RoadMatched() {
		t.Fatal("held at speed on a fix with no usable heading")
	}
}

func TestTrackClearedWithRoadEvidence(t *testing.T) {
	d := newTrackDrive(t, []syntheticWay{{1, 8, 35.1, -97.9, 35.2, -97.9, false, 2}, {2, 30, 35.15, -97.95, 35.15, -97.85, false, 6}})
	d.fix(35.15, -97.9, 20)
	d.expect("tracked", "1/8", d.fix(35.15003, -97.9, 20))
	d.s.ResetRoadEvidence()
	d.expect("no hold after a reset", "none", d.fix(35.15003, -97.9, 0))
}

func TestTrackConfirmationTakesTimeNotFixCount(t *testing.T) {
	d := newTrackDrive(t, []syntheticWay{{1, 20, 35.1, -97.9, 35.2, -97.9, false, 2}, {2, 20, 35.1, -97.90005, 35.2, -97.90005, false, 2}})
	for i := range 3 {
		d.mono = 10_000_000_000 + uint64(i)*200_000_000
		d.expect("fast fixes do not confirm early", "none", d.fix(35.15, -97.9, 20))
	}
	d.mono = 10_800_000_000
	d.expect("confirmed after the wait", "1/20", d.fix(35.15, -97.9, 20))
}

func TestTrackExpiresWithDistanceAndTime(t *testing.T) {
	ways := []syntheticWay{{1, 8, 35.1, -97.9, 35.2, -97.9, false, 2}, {2, 30, 35.15, -97.95, 35.15, -97.85, false, 6}}
	for _, expire := range []func(*trackDrive){
		func(d *trackDrive) { d.s.track.driven = trackMaxDriven + 1 },
		func(d *trackDrive) { d.mono += trackMaxAgeNs },
	} {
		d := newTrackDrive(t, ways)
		d.fix(35.15, -97.9, 20)
		d.expect("tracked", "1/8", d.fix(35.15003, -97.9, 20))
		expire(d)
		d.expect("stale track is not held", "none", d.fix(35.15003, -97.9, 0))
	}
}
