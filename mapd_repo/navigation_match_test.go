package main

import (
	"fmt"
	"math"
	"testing"
	"time"

	"capnproto.org/go/capnp/v3"

	"pfeifer.dev/mapd/cereal/custom"
	"pfeifer.dev/mapd/cereal/offline"
	"pfeifer.dev/mapd/maps"
	m "pfeifer.dev/mapd/math"
)

type namedWay struct {
	syntheticWay
	name string
}

func namedRoadTile(t *testing.T, specs []namedWay) maps.Offline {
	t.Helper()
	msg, seg, err := capnp.NewMessage(capnp.SingleSegment(nil))
	if err != nil {
		t.Fatal(err)
	}
	root, err := offline.NewRootOffline(seg)
	if err != nil {
		t.Fatal(err)
	}
	root.SetMinLat(35)
	root.SetMaxLat(35.25)
	root.SetMinLon(-98)
	root.SetMaxLon(-97.75)
	ways, err := root.NewWays(int32(len(specs)))
	if err != nil {
		t.Fatal(err)
	}
	for i, spec := range specs {
		way := ways.At(i)
		way.SetId(spec.id)
		way.SetMaxSpeed(spec.speed)
		way.SetOneWay(spec.oneWay)
		way.SetLanes(spec.lanes)
		way.SetMinLat(min(spec.lat1, spec.lat2) - 0.001)
		way.SetMaxLat(max(spec.lat1, spec.lat2) + 0.001)
		way.SetMinLon(min(spec.lon1, spec.lon2) - 0.001)
		way.SetMaxLon(max(spec.lon1, spec.lon2) + 0.001)
		if err := way.SetName(spec.name); err != nil {
			t.Fatal(err)
		}
		nodes, err := way.NewNodes(2)
		if err != nil {
			t.Fatal(err)
		}
		nodes.At(0).SetLatitude(spec.lat1)
		nodes.At(0).SetLongitude(spec.lon1)
		nodes.At(1).SetLatitude(spec.lat2)
		nodes.At(1).SetLongitude(spec.lon2)
	}
	data, err := msg.MarshalPacked()
	if err != nil {
		t.Fatal(err)
	}
	tile := maps.ReadOffline(data)
	if !tile.Loaded {
		t.Fatal("packed tile rejected")
	}
	return tile
}

// navigationFix feeds one new external fix at 35.15,-97.9 heading north.
func navigationFix(t *testing.T, s *State, tile maps.Offline, lat float64, speed float32, mono uint64) {
	t.Helper()
	s.bootNow = func() uint64 { return mono + 100_000_000 }
	sample := ambiguitySample(t, mono, s.ProducerSession != 0 && s.GpsMonoTime == 0)
	sample.NewFix = true
	sample.Location.SetLatitude(lat)
	sample.Location.SetSpeed(speed)
	s.ProcessGps(sample, true, func(m.Position) (maps.Offline, error) { return tile, nil }, time.Unix(0, 0))
}

func navigationOutput(t *testing.T, s *State) (custom.MapdOut, []float64) {
	t.Helper()
	event, out := decodedOutput(t, s)
	if event.Valid() || out.RoadStatus() != custom.MapdOut_SampleStatus_unknown || out.WayId() != 0 ||
		out.SpeedLimit() != 0 || s.RoadMatched() || s.CurrentWay.Way.Nodes.Len() != 0 {
		t.Fatalf("ambiguous fix leaked a road match: valid=%v status=%v way=%d", event.Valid(), out.RoadStatus(), out.WayId())
	}
	branches, err := out.NavigationBranches()
	if err != nil {
		t.Fatal(err)
	}
	angles := []float64{}
	for i := 0; i < branches.Len(); i++ {
		angles = append(angles, math.Round(float64(branches.At(i).AngleDegrees())))
	}
	return out, angles
}

func TestNavigationJunctionsFollowSameNamedRoadThroughWaySplit(t *testing.T) {
	for _, tc := range []struct {
		next     string
		branches int
	}{{"Main", 2}, {"Other", 1}} {
		tile := namedRoadTile(t, []namedWay{
			{syntheticWay{1, 20, 35.14, -97.9, 35.15, -97.9, false, 2}, "Main"},
			{syntheticWay{2, 20, 35.15, -97.9, 35.16, -97.9, false, 2}, tc.next},
			{syntheticWay{3, 20, 35.16, -97.9, 35.16, -97.91, false, 2}, "West"},
			{syntheticWay{4, 20, 35.16, -97.9, 35.16, -97.89, false, 2}, "East"},
		})
		s := &State{ShadowOnly: true}
		s.Init()
		navigationFix(t, s, tile, 35.14992, 20, 10_000_000_000)
		out, angles := navigationOutput(t, s)
		branches, _ := out.NavigationBranches()
		if len(angles) != tc.branches {
			t.Fatalf("next=%s angles=%v", tc.next, angles)
		}
		if tc.next == "Other" && (angles[0] != 0 || branches.At(0).WayId() != 2) {
			t.Fatalf("renamed continuation angles=%v", angles)
		}
		// Through the split (not itself a junction) the walk reaches the far junction, about 1.1 km ahead.
		if tc.next == "Main" && (angles[0] != -90 || angles[1] != 90 || math.Abs(float64(branches.At(0).DistanceMeters())-1121) > 5) {
			t.Fatalf("far junction angles=%v distance=%f", angles, branches.At(0).DistanceMeters())
		}
	}
}

func TestNavigationJunctionsIgnoreCrossingRoadByHeading(t *testing.T) {
	tile := namedRoadTile(t, []namedWay{
		{syntheticWay{1, 20, 35.14, -97.9, 35.16, -97.9, false, 2}, "Main"},
		{syntheticWay{2, 20, 35.15005, -97.91, 35.15005, -97.89, false, 2}, "Cross"},
		{syntheticWay{3, 20, 35.16, -97.9, 35.16, -97.89, false, 2}, "East"},
	})
	s := &State{ShadowOnly: true}
	s.Init()
	navigationFix(t, s, tile, 35.15, 20, 10_000_000_000)
	if _, angles := navigationOutput(t, s); len(angles) != 1 || angles[0] != 90 {
		t.Fatalf("angles=%v", angles)
	}
	// Nearly stopped, heading is noise: keep the road chosen while moving.
	navigationFix(t, s, tile, 35.15, 0.5, 10_200_000_000)
	if _, angles := navigationOutput(t, s); len(angles) != 1 {
		t.Fatalf("held angles=%v", angles)
	}
	// Without a prior choice a stopped fix publishes nothing.
	fresh := &State{ShadowOnly: true}
	fresh.Init()
	navigationFix(t, fresh, tile, 35.15, 0.5, 10_000_000_000)
	if _, angles := navigationOutput(t, fresh); len(angles) != 0 {
		t.Fatalf("stopped fix chose a road: %v", angles)
	}
}

func TestNavigationJunctionsStayEmptyForCloseParallelRoads(t *testing.T) {
	tile := namedRoadTile(t, []namedWay{
		{syntheticWay{1, 20, 35.14, -97.9, 35.16, -97.9, false, 2}, ""},
		{syntheticWay{2, 20, 35.14, -97.90005, 35.16, -97.90005, false, 2}, ""},
		{syntheticWay{3, 20, 35.16, -97.9, 35.16, -97.89, false, 2}, ""},
	})
	s := &State{ShadowOnly: true}
	s.Init()
	navigationFix(t, s, tile, 35.15, 20, 10_000_000_000)
	if _, angles := navigationOutput(t, s); len(angles) != 0 {
		t.Fatalf("parallel roads 4.5 m apart resolved: %v", angles)
	}
}

type polyWay struct {
	id     int64
	name   string
	oneWay bool
	nodes  [][2]float64
}

func polyRoadTile(t *testing.T, specs []polyWay) maps.Offline {
	t.Helper()
	msg, seg, err := capnp.NewMessage(capnp.SingleSegment(nil))
	if err != nil {
		t.Fatal(err)
	}
	root, err := offline.NewRootOffline(seg)
	if err != nil {
		t.Fatal(err)
	}
	root.SetMinLat(35)
	root.SetMaxLat(35.25)
	root.SetMinLon(-98)
	root.SetMaxLon(-97.75)
	ways, err := root.NewWays(int32(len(specs)))
	if err != nil {
		t.Fatal(err)
	}
	for i, spec := range specs {
		way := ways.At(i)
		way.SetId(spec.id)
		way.SetMaxSpeed(20)
		way.SetOneWay(spec.oneWay)
		way.SetLanes(2)
		if err := way.SetName(spec.name); err != nil {
			t.Fatal(err)
		}
		nodes, err := way.NewNodes(int32(len(spec.nodes)))
		if err != nil {
			t.Fatal(err)
		}
		minLat, minLon, maxLat, maxLon := 90.0, 180.0, -90.0, -180.0
		for n, p := range spec.nodes {
			nodes.At(n).SetLatitude(p[0])
			nodes.At(n).SetLongitude(p[1])
			minLat, maxLat = min(minLat, p[0]), max(maxLat, p[0])
			minLon, maxLon = min(minLon, p[1]), max(maxLon, p[1])
		}
		way.SetMinLat(minLat)
		way.SetMaxLat(maxLat)
		way.SetMinLon(minLon)
		way.SetMaxLon(maxLon)
	}
	data, err := msg.MarshalPacked()
	if err != nil {
		t.Fatal(err)
	}
	tile := maps.ReadOffline(data)
	if !tile.Loaded {
		t.Fatal("packed tile rejected")
	}
	return tile
}

func branchSummary(t *testing.T, out custom.MapdOut) []string {
	t.Helper()
	branches, err := out.NavigationBranches()
	if err != nil {
		t.Fatal(err)
	}
	summary := []string{}
	for i := 0; i < branches.Len(); i++ {
		name, _ := branches.At(i).RoadName()
		summary = append(summary, fmt.Sprintf("%s@%.0f/%.0f", name, math.Round(float64(branches.At(i).DistanceMeters())/10)*10, math.Round(float64(branches.At(i).AngleDegrees()))))
	}
	return summary
}

// OSM keeps a street as one way through its junctions; a junction at an
// interior node of the crossing street (or of the current road) still counts.
func TestNavigationJunctionsAtInteriorNodes(t *testing.T) {
	for _, tc := range []struct {
		name  string
		main  [][2]float64
		cross polyWay
		want  string
	}{
		{"T into through street", [][2]float64{{35.14, -97.9}, {35.15, -97.9}},
			polyWay{2, "Cross", false, [][2]float64{{35.15, -97.91}, {35.15, -97.9}, {35.15, -97.89}}}, "[Cross@560/90 Cross@560/-90]"},
		{"one-way through street", [][2]float64{{35.14, -97.9}, {35.15, -97.9}},
			polyWay{2, "Cross", true, [][2]float64{{35.15, -97.91}, {35.15, -97.9}, {35.15, -97.89}}}, "[Cross@560/90]"},
		{"crossroads of two through streets", [][2]float64{{35.14, -97.9}, {35.15, -97.9}, {35.16, -97.9}},
			polyWay{2, "Cross", false, [][2]float64{{35.15, -97.91}, {35.15, -97.9}, {35.15, -97.89}}}, "[Main@560/0 Cross@560/90 Cross@560/-90]"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			tile := polyRoadTile(t, []polyWay{{1, "Main", false, tc.main}, tc.cross})
			s := &State{ShadowOnly: true}
			s.Init()
			navigationFix(t, s, tile, 35.145, 20, 10_000_000_000)
			event, out := decodedOutput(t, s)
			if !event.Valid() || out.WayId() != 1 {
				t.Fatalf("sole road not matched: status=%v", out.RoadStatus())
			}
			if got := fmt.Sprint(branchSummary(t, out)); got != tc.want {
				t.Fatalf("branches %s, want %s", got, tc.want)
			}
		})
	}
}

// Driving out of a driveway or parking lot the tile does not map, the road
// straight ahead is offered both ways; the output stays a neutral noMatch.
func TestNavigationApproachFromUnmappedGround(t *testing.T) {
	tile := polyRoadTile(t, []polyWay{
		{1, "Grand", false, [][2]float64{{35.1505, -97.91}, {35.1505, -97.89}}},
		{2, "Behind", false, [][2]float64{{35.1495, -97.91}, {35.1495, -97.89}}},
		{3, "Alongside", false, [][2]float64{{35.149, -97.8995}, {35.152, -97.8995}}},
	})
	s := &State{ShadowOnly: true}
	s.Init()
	navigationFix(t, s, tile, 35.15, 6, 10_000_000_000)
	event, out := decodedOutput(t, s)
	if event.Valid() || out.RoadStatus() != custom.MapdOut_SampleStatus_noMatch || out.WayId() != 0 || out.SpeedLimit() != 0 || s.RoadMatched() {
		t.Fatalf("approach leaked a road match: valid=%v status=%v way=%d", event.Valid(), out.RoadStatus(), out.WayId())
	}
	if got := fmt.Sprint(branchSummary(t, out)); got != "[Grand@60/90 Grand@60/-90]" {
		t.Fatalf("approach branches %s", got)
	}
	// Nearly stopped, the heading cannot say which road lies ahead.
	stopped := &State{ShadowOnly: true}
	stopped.Init()
	navigationFix(t, stopped, tile, 35.15, 0.5, 10_000_000_000)
	if _, out := decodedOutput(t, stopped); len(branchSummary(t, out)) != 0 {
		t.Fatalf("stopped fix offered %v", branchSummary(t, out))
	}
}

// A held road keeps the navigation pick through a near tie with a parallel
// road; the other road takes over once it is closer by the margin.
func TestNavigationHoldsRoadThroughNearTie(t *testing.T) {
	tile := polyRoadTile(t, []polyWay{
		{1, "Main", false, [][2]float64{{35.14, -97.9}, {35.16, -97.9}}},
		{2, "Ramp", false, [][2]float64{{35.15, -97.9001}, {35.16, -97.9001}}},
		{3, "North", false, [][2]float64{{35.16, -97.9}, {35.17, -97.9}}},
		{4, "RampEnd", false, [][2]float64{{35.16, -97.9001}, {35.16, -97.91}}},
	})
	fix := func(s *State, lat, lon float64, mono uint64) string {
		s.bootNow = func() uint64 { return mono + 100_000_000 }
		sample := ambiguitySample(t, mono, s.GpsMonoTime == 0)
		sample.NewFix = true
		sample.Location.SetLatitude(lat)
		sample.Location.SetLongitude(lon)
		sample.Location.SetSpeed(20)
		s.ProcessGps(sample, true, func(m.Position) (maps.Offline, error) { return tile, nil }, time.Unix(0, 0))
		_, out := decodedOutput(t, s)
		if out.SpeedLimit() != 0 && out.RoadStatus() == custom.MapdOut_SampleStatus_unknown {
			t.Fatal("ambiguous fix published a limit")
		}
		return fmt.Sprint(out.RoadStatus(), branchSummary(t, out))
	}
	s := &State{ShadowOnly: true}
	s.Init()
	if got := fix(s, 35.148, -97.9, 10_000_000_000); got != "matchedLimit [North@1330/0]" {
		t.Fatalf("matched Main: %s", got)
	}
	if got := fix(s, 35.155, -97.90003, 10_200_000_000); got != "unknown [North@560/0]" {
		t.Fatalf("near tie should keep Main: %s", got)
	}
	if got := fix(s, 35.156, -97.9001, 10_400_000_000); got != "unknown [RampEnd@440/-90]" {
		t.Fatalf("on the ramp, closer by the margin: %s", got)
	}
	fresh := &State{ShadowOnly: true}
	fresh.Init()
	if got := fix(fresh, 35.155, -97.90003, 10_000_000_000); got != "unknown []" {
		t.Fatalf("near tie without a held road: %s", got)
	}
}
