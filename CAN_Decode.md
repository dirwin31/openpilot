# 2022 Honda Civic CAN Decode Notes

## Blind-Spot Monitoring Investigation

### Capture

- Vehicle: 2022 Honda Civic (`HONDA_CIVIC_2022`)
- Route: private comma Connect capture (device and route identifiers omitted)
- Primary rlogs: segments 11, 12, and 13
- Control rlogs: segments 0, 3, 7, 14, 15, 16, 17, and 22
- Raw byte numbers in this document are zero-based.

The bookmarks were intended to mark blind-spot warning activity while the turn
signals were active. The recorded `carState` timeline shows that the first event
was on the right and the second event was on the left.

| Segment | Event |
| --- | --- |
| 11 | Right blinker starts at `45.479 s`; bookmark at `58.443 s` |
| 12 | Right blinker remains active; bookmark at `10.829 s`; blinker ends at `14.717 s` |
| 13 | Bookmark at `14.864 s`; left blinker starts at `15.611 s`; second bookmark at `35.926 s`; blinker ends at `37.406 s` |

## Confirmed Turn-Signal Signals

### `0x326` — `SCM_FEEDBACK`

| Raw location | Meaning | Behavior |
| --- | --- | --- |
| Byte 3, mask `0x04` | Left blinker request | High for the complete decoded left-blinker interval |
| Byte 3, mask `0x08` | Right blinker request | High for the complete decoded right-blinker interval |

These are the existing turn-signal request states used by `carState`.

### `0x372` — turn-lamp/body-controller output

`0x372` is not currently defined in `honda_bosch_radarless_generated.dbc`.
Ignoring its final checksum/counter byte, its inactive payload is:

```text
00 10 00 00 00 00 00
```

| Raw location | Meaning | Behavior |
| --- | --- | --- |
| Byte 2, mask `0x04` | Right-turn activation pulse | High for approximately one second after every right-blinker activation |
| Byte 2, mask `0x08` | Left-turn activation pulse | High for approximately one second after every left-blinker activation |
| Byte 3, mask `0x01` | Right flasher output | Pulses approximately `0.3 s` on / `0.4 s` off while the right blinker is active |
| Byte 3, mask `0x02` | Left flasher output | Pulses approximately `0.3 s` on / `0.4 s` off while the left blinker is active |

Representative right-blinker payloads:

```text
00 10 04 01 00 00 00  # activation pulse + flasher on
00 10 04 00 00 00 00  # activation pulse + flasher off
00 10 00 01 00 00 00  # flasher on after the activation pulse
00 10 00 00 00 00 00  # flasher off
```

Representative left-blinker payloads:

```text
00 10 08 02 00 00 00  # activation pulse + flasher on
00 10 08 00 00 00 00  # activation pulse + flasher off
00 10 00 02 00 00 00  # flasher on after the activation pulse
00 10 00 00 00 00 00  # flasher off
```

The exact `0x372` startup and flashing patterns repeat during every ordinary,
unbookmarked turn-signal event examined in this route. They are therefore
turn-lamp/body-controller signals, not evidence of blind-spot occupancy.

## BSM Result

No reliable factory BSM occupancy signal was found on the CAN channels recorded
by these rlogs.

Evidence:

- The existing Honda B-CAN BSM frames were absent:
  - Left: `0x12F8BE9F`
  - Right: `0x12F8BFA7`
- No CAN message ID appeared exclusively during either bookmarked BSM event.
- A bit-level comparison against eight ordinary turn-signal events found no
  stable left/right occupancy candidate beyond the confirmed turn-signal
  signals above.
- Other statistical candidates followed vehicle speed, route position,
  odometer, cruise, steering, or camera state instead of BSM activity.
- The route's `CarParams` reported:

  ```text
  carFingerprint: HONDA_CIVIC_2022
  enableBsm: false
  radarUnavailable: true
  ```

The current Honda integration only enables the known BSM decoder for the
fifth-generation CR-V; see
[`opendbc/car/honda/interface.py`](opendbc/car/honda/interface.py). The existing
Honda implementation also notes that BSM messages are on B-CAN and require a
panda to forward B-CAN messages; see
[`opendbc/car/honda/carstate.py`](opendbc/car/honda/carstate.py). The known older
Honda signal definitions are in
[`opendbc/dbc/honda_crv_ex_2017_body_generated.dbc`](opendbc/dbc/honda_crv_ex_2017_body_generated.dbc).

The 2022 Civic currently has only the powertrain DBC configured and no body DBC;
see [`opendbc/car/honda/values.py`](opendbc/car/honda/values.py).

## What May Still Be Decodable

### BSM On, Off, and Fault

The instrument cluster displays BSM On, Off, and Failure as part of the Safety
Support status. Those states may be bridged onto one of the currently visible
F-CAN/cluster messages even if left/right radar detections remain confined to
B-CAN. The current capture never changed the BSM setting or entered a fault
state, so constant status bits cannot be identified from it.

A useful follow-up capture with the current harness would be:

1. Record BSM On for at least ten seconds and create a bookmark.
2. Switch BSM Off, wait at least ten seconds, and create a bookmark.
3. Switch BSM On again, wait at least ten seconds, and create a bookmark.
4. Do not combine these state changes with other controls or menu operations if
   avoidable.

### Left and Right Occupancy

Capturing actual left/right occupancy will most likely require a validated
Civic B-CAN harness or tap connected to a panda CAN channel. Do not guess the
vehicle connector pins.

With B-CAN visible, record the following clearly separated states:

1. Clear on both sides.
2. Left vehicle detected without a turn signal.
3. Clear again.
4. Right vehicle detected without a turn signal.
5. Clear again.
6. Left vehicle detected with the left turn signal.
7. Right vehicle detected with the right turn signal.
8. Include ordinary left and right turn-signal controls with no detected vehicle.

Wait several seconds in each state and bookmark both the start and end of each
detection interval. Honda states that the 2022 Civic BSM system activates at
approximately `20 mph` (`32 km/h`) or above, so detection captures must be
performed safely under suitable driving conditions.

### Rapid Approach Versus Occupancy

Honda's driver documentation describes a single mirror alert based on vehicle
position and relative-speed conditions. It does not describe a separate
driver-visible “rapid approach” state. The rear radar may retain richer tracking
information internally, but a distinct exported CAN state is not guaranteed.
A safely controlled two-vehicle capture would be needed to test for it.

Official behavior reference:
[2022 Civic Blind Spot Information System](https://techinfo.honda.com/rjanisis/pubs/OM/AH/AT202222IOM/enu/details/131229047-297348.html).

## Implementation Status

No production decoder was changed from this investigation. The capture supports
adding names for the `0x372` turn-lamp output if useful, but it does not support
setting `CarState.leftBlindspot` or `CarState.rightBlindspot` from that frame.
