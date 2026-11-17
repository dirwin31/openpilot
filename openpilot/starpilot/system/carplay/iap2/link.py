"""iAP2 link layer: marker, SYN negotiation, sequenced packets with ACK and retransmit.

Pure state machine (no I/O), ported from DiPlay's Iap2LinkEngine. Feed received bytes with
feed(), take bytes to write with take_output(), and read events with poll_event().
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import struct

CONTROL_SESSION_ID = 10
EA_SESSION_ID = 11
FILE_TRANSFER_SESSION_ID = 12
MARKER = bytes([0xFF, 0x55, 0x02, 0x00, 0xEE, 0x10])
HEADER = 9
CHECKSUM = 1
MAX_WIRE = 0xFFFF
MAX_PAYLOAD = MAX_WIRE - HEADER - CHECKSUM
SYN_FIXED = 10
SYN_VERSION = 1
START = b"\xff\x5a"
SYN, ACK, EAK, RST = 0x80, 0x40, 0x20, 0x10
INITIAL_SEQUENCE = 99
MARKER_RESEND_MS = 1000
SYN_RESEND_MS = 500
RETRANSMIT_MS = 4000
MAX_RETRANSMIT = 4
MAX_ACKS = 3
ACK_SLACK = 10


@dataclass(frozen=True)
class LinkConfig:
  max_outgoing: int = 4
  ack_timeout_ms: int = 500
  zero_acks: bool = False
  control_session_version: int = 2
  max_queued: int = 64
  max_out_of_order: int = 64
  max_pending_output: int = 1 << 20


WIRELESS_CONFIG = LinkConfig()
TUNNEL_CONFIG = LinkConfig(zero_acks=True)


@dataclass(frozen=True)
class Sync:
  max_outgoing: int
  max_length: int
  retransmit_ms: int
  ack_ms: int
  max_retransmit: int
  max_acks: int
  sessions: tuple = ()

  def encode(self) -> bytes:
    out = struct.pack(">BBHHHBB", SYN_VERSION, self.max_outgoing, self.max_length, self.retransmit_ms,
                      self.ack_ms, self.max_retransmit, self.max_acks)
    return out + b"".join(bytes(s) for s in self.sessions)

  @classmethod
  def decode(cls, data: bytes) -> Sync | None:
    if len(data) < SYN_FIXED or data[0] != SYN_VERSION:
      return None
    _, mo, ml, rt, at, mr, ma = struct.unpack_from(">BBHHHBB", data)
    sessions = tuple(tuple(data[i:i + 3]) for i in range(SYN_FIXED, len(data) - 2, 3))
    return cls(mo, ml, rt, at, mr, ma, sessions)


@dataclass
class Packet:
  sequence: int
  session_id: int
  payload: bytes
  retransmissions: int = 0
  deadline: int = 0


def checksum(data: bytes) -> int:
  return (-sum(data)) & 0xFF


def checksum_valid(data: bytes) -> bool:
  return sum(data) & 0xFF == 0


def distance(sequence: int, previous: int | None) -> int:
  return 0 if previous is None else (sequence - previous) & 0xFF


@dataclass
class Event:
  kind: str  # control, session, writable, dead
  session_id: int = CONTROL_SESSION_ID
  data: bytes = b""
  value: bool = False
  reason: str | None = None


@dataclass
class LinkEngine:
  config: LinkConfig = field(default_factory=LinkConfig)

  def __post_init__(self):
    self.state = "idle"
    self.peer = self._local_sync()
    self.peer_received = False
    self.peer_sync_sequence: int | None = None
    self.sent_sequence = INITIAL_SEQUENCE
    self.last_sent_acked: int | None = None
    self.last_received = 0
    self.last_acked_received: int | None = None
    self.accumulated_acks = 0
    self.writable = False
    self.unacked: list[Packet] = []
    self.queued: deque[Packet] = deque()
    self.out_of_order: list[Packet] = []
    self.events: deque[Event] = deque()
    self.output = bytearray()
    self.receive = bytearray()
    self.awaiting: tuple | None = None
    self.marker_deadline = self.sync_deadline = self.send_ack_deadline = self.recv_ack_deadline = None

  def take_output(self) -> bytes:
    data = bytes(self.output)
    self.output.clear()
    return data

  def poll_event(self) -> Event | None:
    return self.events.popleft() if self.events else None

  def next_deadline(self) -> int | None:
    deadlines = [d for d in (self.marker_deadline, self.sync_deadline, self.send_ack_deadline, self.recv_ack_deadline)
                 if d is not None]
    return min(deadlines) if deadlines else None

  def start(self, initiator: bool, now: int) -> None:
    if self.state != "idle":
      return
    self.state = "detecting"
    self._append(MARKER)
    self.marker_deadline = now + MARKER_RESEND_MS
    if initiator and self.state != "dead":
      self._enter_negotiating(now)

  def feed(self, data: bytes, now: int) -> None:
    if self.state == "dead" or not data:
      return
    self.receive += data
    if len(self.receive) > 2 * MAX_WIRE:
      self._die("iAP2 receive buffer overflow")
      return
    while self.state != "dead" and self._parse(now):
      pass

  def feed_eof(self) -> None:
    self._die(None)

  def advance(self, now: int) -> None:
    while self.state != "dead":
      deadline = self.next_deadline()
      if deadline is None or deadline > now:
        return
      if self.marker_deadline is not None and self.marker_deadline <= now:
        self.marker_deadline = None
        if self.state == "detecting":
          self._append(MARKER)
          self.marker_deadline = now + MARKER_RESEND_MS
      elif self.sync_deadline is not None and self.sync_deadline <= now:
        self.sync_deadline = None
        if self.state == "negotiating":
          self._send_sync()
          self.sync_deadline = now + SYN_RESEND_MS
      elif self.send_ack_deadline is not None and self.send_ack_deadline <= now:
        self.send_ack_deadline = None
        if self.state == "normal":
          self.last_acked_received = self.last_received
          self._send_ack()
      elif self.recv_ack_deadline is not None and self.recv_ack_deadline <= now:
        self.recv_ack_deadline = None
        self._retransmit_due(now)

  def peer_max_payload(self) -> int:
    return self.peer.max_length - HEADER - CHECKSUM

  def send(self, session_id: int, data: bytes, now: int) -> None:
    if len(data) > MAX_PAYLOAD:
      raise ValueError("iAP2 payload too large")
    if (self.peer_received or self.state == "normal") and not self._acceptable(data):
      raise ValueError(f"iAP2 payload exceeds peer maxLength {self.peer.max_length}")
    self._send_packet(Packet(0, session_id, bytes(data)), now)

  def _parse(self, now: int) -> bool:
    if self.state == "detecting":
      if len(self.receive) < len(MARKER):
        return False
      if self.receive[:len(MARKER)] != MARKER:
        self._die("iAP2 marker was not received")
        return False
      del self.receive[:len(MARKER)]
      self._enter_negotiating(now)
      return True
    if self.awaiting is not None:
      need = self.awaiting[0] - HEADER
      if len(self.receive) < need:
        return False
      body = bytes(self.receive[:need])
      del self.receive[:need]
      header, self.awaiting = self.awaiting, None
      if checksum_valid(body):
        self._process(header, body[:-CHECKSUM], now)
      return True
    discarded = False
    while len(self.receive) >= 2 and self.receive[:2] != START:
      del self.receive[0]
      discarded = True
    if len(self.receive) < HEADER:
      return discarded
    raw = bytes(self.receive[:HEADER])
    del self.receive[:HEADER]
    if not checksum_valid(raw):
      return True
    length, control, sequence, ack, session = struct.unpack(">HBBBB", raw[2:8])
    if length < HEADER:
      return True
    header = (length, control, sequence, ack, session)
    if length == HEADER:
      self._process(header, None, now)
    else:
      self.awaiting = header
    return True

  def _enter_negotiating(self, now: int) -> None:
    self.state = "negotiating"
    self.marker_deadline = None
    self._send_sync()
    if self.state != "dead":
      self.sync_deadline = now + SYN_RESEND_MS

  def _process(self, header: tuple, payload: bytes | None, now: int) -> None:
    _length, control, sequence, ack, session = header
    if control & RST:
      self._die("peer sent iAP2 reset")
      return
    if control & SYN:
      sync = Sync.decode(payload or b"")
      if sync is not None:
        self._handle_sync(sync, sequence, bool(control & ACK))
    if control & ACK:
      self.accumulated_acks += 1
      self._handle_ack(ack, now)
    if control & EAK and payload is not None:
      self._handle_eak(payload)
    if (control & ~ACK) == 0 and payload is not None:
      self._handle_data(Packet(sequence, session, payload), now)
    if self.peer.max_acks > 0 and self.accumulated_acks >= self.peer.max_acks:
      self.accumulated_acks = 0
      self.last_acked_received = self.last_received
      self._send_ack()

  def _handle_sync(self, sync: Sync, sequence: int, acknowledges: bool) -> None:
    if self.state == "normal":
      if acknowledges and sequence == self.peer_sync_sequence and sync == self.peer:
        self._send_ack()
      return
    if self.state != "negotiating":
      return
    self.peer = sync
    self.peer_received = True
    self.peer_sync_sequence = sequence
    self.last_received = sequence
    self.last_acked_received = sequence
    self._send_ack()

  def _handle_ack(self, ack: int, now: int) -> None:
    if self.state == "negotiating":
      if not self.peer_received or not self._limits_usable() or any(not self._acceptable(p.payload) for p in self.queued):
        self._die("peer iAP2 maxLength cannot carry pending packets")
        return
      self.state = "normal"
      self.sync_deadline = None
      self._set_writable(True)
    self.last_sent_acked = ack
    rearmed = False
    while self.unacked:
      first = self.unacked[0]
      d = distance(first.sequence, self.last_sent_acked)
      if 0 < d <= self.peer.max_acks + ACK_SLACK:
        self.recv_ack_deadline = first.deadline
        rearmed = True
        break
      self.unacked.pop(0)
    if not rearmed:
      self.recv_ack_deadline = None
    while distance(self.sent_sequence, self.last_sent_acked) < self.peer.max_outgoing and self.queued:
      self._send_packet(self.queued.popleft(), now)
      self._set_writable(True)

  def _handle_eak(self, missing: bytes) -> None:
    if self.state != "normal":
      return
    for packet in self.unacked:
      if packet.sequence not in missing:
        continue
      packet.retransmissions += 1
      if packet.retransmissions == self.peer.max_retransmit:
        self._die(f"iAP2 packet {packet.sequence} was not acknowledged")
        return
      self._send_data(packet)
      self.send_ack_deadline = None
      self.recv_ack_deadline = packet.deadline

  def _handle_data(self, packet: Packet, now: int) -> None:
    d = distance(packet.sequence, self.last_received)
    if d == 0 or d > self.peer.max_outgoing + ACK_SLACK:
      self._send_ack()
      return
    if any(p.sequence == packet.sequence for p in self.out_of_order):
      return
    if len(self.out_of_order) >= self.config.max_out_of_order:
      self._die("iAP2 out-of-order packet limit exceeded")
      return
    self.out_of_order.append(packet)
    if d > 1:
      if d >= self.peer.max_outgoing:
        missing, sequence = [], self.last_received
        while distance(packet.sequence, sequence) > 1:
          sequence = (sequence + 1) & 0xFF
          missing.append(sequence)
        self.send_ack_deadline = None
        self._write(bytes(missing), self.sent_sequence, EAK, 0)
      return
    self.out_of_order.sort(key=lambda p: distance(p.sequence, self.last_received))
    while self.out_of_order and distance(self.out_of_order[0].sequence, self.last_received) == 1:
      in_order = self.out_of_order.pop(0)
      self.last_received = in_order.sequence
      self.peer_sync_sequence = None
      kind = "control" if in_order.session_id == CONTROL_SESSION_ID else "session"
      self._event(Event(kind, in_order.session_id, in_order.payload))
    if self.peer.max_acks == 0:
      return
    window = max(1, self.peer.max_outgoing)
    if distance(self.last_received, self.last_acked_received) >= window:
      self.send_ack_deadline = None
      self.last_acked_received = self.last_received
      self._send_ack()
    else:
      self.send_ack_deadline = now + self.peer.ack_ms

  def _send_packet(self, packet: Packet, now: int) -> None:
    if self.state != "normal" or distance(self.sent_sequence, self.last_sent_acked) > self.peer.max_outgoing:
      if len(self.queued) >= self.config.max_queued:
        self._die("iAP2 outbound queue limit exceeded")
      else:
        self.queued.append(packet)
        self._set_writable(False)
      return
    self.sent_sequence = (self.sent_sequence + 1) & 0xFF
    packet.sequence = self.sent_sequence
    packet.retransmissions = 0
    packet.deadline = now + self.peer.retransmit_ms
    self.send_ack_deadline = None
    self._send_data(packet)
    self.last_acked_received = self.last_received
    if self.peer.max_retransmit > 0:
      self.unacked.append(packet)
      self.recv_ack_deadline = packet.deadline
    else:
      self.last_sent_acked = self.sent_sequence

  def _retransmit_due(self, now: int) -> None:
    if self.state != "normal" or not self.unacked:
      return
    nxt = min(self.unacked, key=lambda p: p.deadline)
    others = [p.deadline for p in self.unacked if p is not nxt]
    nxt.deadline = now + self.peer.retransmit_ms
    nxt.retransmissions += 1
    if nxt.retransmissions == self.peer.max_retransmit:
      self._die(f"iAP2 packet {nxt.sequence} was not acknowledged")
      return
    self._send_data(nxt)
    self.recv_ack_deadline = min(others) if others else nxt.deadline

  def _send_sync(self) -> None:
    self._write(self._local_sync().encode(), self.sent_sequence, SYN, 0)

  def _send_ack(self) -> None:
    self._write(None, self.sent_sequence, ACK, 0)

  def _send_data(self, packet: Packet) -> None:
    self._write(packet.payload, packet.sequence, ACK, packet.session_id)

  def _write(self, payload: bytes | None, sequence: int, control: int, session_id: int) -> None:
    if self.state == "dead":
      return
    self.accumulated_acks = 0
    length = HEADER + (len(payload) + CHECKSUM if payload is not None else 0)
    header = START + struct.pack(">HBBBB", length, control, sequence, self.last_received & 0xFF, session_id)
    frame = header + bytes([checksum(header)])
    if payload is not None:
      frame += payload + bytes([checksum(payload)])
    self._append(frame)

  def _set_writable(self, value: bool) -> None:
    if self.writable != value:
      self.writable = value
      self._event(Event("writable", value=value))

  def _die(self, reason: str | None) -> None:
    if self.state == "dead":
      return
    self.marker_deadline = self.sync_deadline = self.send_ack_deadline = self.recv_ack_deadline = None
    self.state = "dead"
    self.writable = False
    self.output.clear()
    self.events.clear()
    self.events.append(Event("dead", reason=reason))

  def _append(self, data: bytes) -> None:
    if self.state == "dead":
      return
    if len(self.output) + len(data) > self.config.max_pending_output:
      self._die("iAP2 pending output limit exceeded")
      return
    self.output += data

  def _event(self, event: Event) -> None:
    if self.state != "dead":
      self.events.append(event)

  def _limits_usable(self) -> bool:
    return self.peer.max_length > HEADER + CHECKSUM

  def _acceptable(self, payload: bytes) -> bool:
    return self._limits_usable() and len(payload) <= self.peer.max_length - HEADER - CHECKSUM

  def _local_sync(self) -> Sync:
    zero = self.config.zero_acks
    return Sync(self.config.max_outgoing, MAX_WIRE, 0 if zero else RETRANSMIT_MS, 0 if zero else self.config.ack_timeout_ms,
                0 if zero else MAX_RETRANSMIT, 0 if zero else MAX_ACKS,
                ((CONTROL_SESSION_ID, 0, self.config.control_session_version), (EA_SESSION_ID, 2, 1),
                 (FILE_TRANSFER_SESSION_ID, 1, 2)))
