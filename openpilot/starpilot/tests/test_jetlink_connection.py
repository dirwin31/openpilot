"""Connection retries must stay bounded and release failed setup sockets."""
from unittest.mock import Mock, patch

import pytest

from jetlink.comma import lending
from jetlink.openpilot import link
from jetlink.transport.tcp import TcpTransport


def test_connection_retries_share_one_deadline():
  now = [100.0]
  deadlines = []
  connection = Mock()

  def opening(deadline):
    deadlines.append(deadline)
    now[0] += min(8.0, deadline - now[0])
    raise TimeoutError('no phone')

  connection.open.side_effect = opening
  with patch.object(link.time, 'monotonic', side_effect=lambda: now[0]), \
       patch.object(link.time, 'sleep', side_effect=lambda seconds: now.__setitem__(0, now[0] + seconds)):
    with pytest.raises(TimeoutError, match='no phone'):
      link.connect_patiently(connection)
  assert now[0] == 145.0
  assert len(deadlines) > 1
  assert set(deadlines) == {145.0}


def test_borrow_time_is_deducted_from_phone_accept_budget():
  connection = link.Link(Mock())
  loan = Mock(closed=False, cable=True)
  client = Mock()
  with patch.object(lending, 'borrow', return_value=loan) as borrow, \
       patch.object(link.time, 'monotonic', side_effect=[100.0, 103.0]), \
       patch.object(link, 'connect', return_value=client) as connect:
    assert connection.open(105.0) is client
  borrow.assert_called_once_with('modeld', timeout=5.0)
  assert connect.call_args.kwargs['wait'] == 2.0


@pytest.mark.parametrize('operation', ['setsockopt', 'bind', 'listen'])
def test_failed_listener_setup_closes_socket(operation):
  sock = Mock()
  getattr(sock, operation).side_effect = OSError('unavailable')
  with patch('jetlink.transport.tcp.socket.socket', return_value=sock):
    with pytest.raises(OSError, match='unavailable'):
      TcpTransport.listen('127.0.0.1', 0)
  sock.close.assert_called_once()


@pytest.mark.parametrize('operation', ['setsockopt', 'connect'])
def test_failed_datagram_setup_closes_socket_and_keeps_tcp(operation):
  transport = TcpTransport(Mock())
  transport.sock.getpeername.return_value = ('192.168.60.2', 5599)
  udp = Mock()
  getattr(udp, operation).side_effect = OSError('unavailable')
  with patch.object(transport, 'on_the_cable', return_value=True), \
       patch('jetlink.transport.tcp.socket.socket', return_value=udp):
    assert not transport.use_datagrams(5599, 123)
  assert not transport.datagrams
  udp.close.assert_called_once()


def test_unavailable_owner_closes_borrow_socket():
  sock = Mock()
  sock.connect.side_effect = ConnectionRefusedError()
  with patch.object(lending.socket, 'socket', return_value=sock):
    assert lending.borrow() is None
  sock.close.assert_called_once()


@pytest.fixture
def cable_broker():
  import tempfile
  from pathlib import Path
  from jetlink.comma import gadget
  cable = lending.CableListener()
  with tempfile.TemporaryDirectory(prefix='jl-', dir='/tmp') as tmp, \
       patch.object(gadget, 'CABLE_ADDR', ('127.0.0.1', 0)), patch.object(gadget, 'bound_udc', return_value='test'):
    assert cable.open()
    lender = lending.Lender(lambda: True, lambda: True, path=Path(tmp) / 'lend.sock',
                            cable=lambda: True, vacate=cable.vacate, take_cable=cable.take)
    assert lender.start()
    try:
      yield cable, lender
    finally:
      lender.stop()
      cable.close()


def test_idle_phone_handoff_preserves_connection_and_listener(cable_broker):
  import socket
  cable, lender = cable_broker
  with socket.create_connection(cable.bound) as phone:
    phone.settimeout(1)
    cable.poll()
    loan = lending.borrow(path=lender.path)
    try:
      assert loan._brokered
      with loan.accept(1) as accepted:
        assert cable.listening
        assert not cable.held
        phone.sendall(b'hello')
        accepted.settimeout(1)
        assert accepted.recv(5) == b'hello'
        accepted.sendall(b'ready')
        assert phone.recv(5) == b'ready'
    finally:
      loan.close()


def test_phone_can_arrive_late_and_reconnect_on_same_loan(cable_broker):
  import socket
  cable, lender = cable_broker
  loan = lending.borrow(path=lender.path)
  try:
    with pytest.raises(TimeoutError):
      loan.accept(.02)
    assert not loan.closed
    for _ in range(3):
      with socket.create_connection(cable.bound) as phone, loan.accept(1) as accepted:
        phone.sendall(b'x')
        accepted.settimeout(1)
        assert accepted.recv(1) == b'x'
    assert cable.listening
  finally:
    loan.close()


def test_newest_phone_connection_wins(cable_broker):
  import socket
  cable, lender = cable_broker
  with socket.create_connection(cable.bound) as old:
    cable.poll()
    with socket.create_connection(cable.bound) as new:
      loan = lending.borrow(path=lender.path)
      try:
        with loan.accept(1) as accepted:
          new.sendall(b'new')
          accepted.settimeout(1)
          assert accepted.recv(3) == b'new'
          old.settimeout(1)
          assert old.recv(1) == b''
      finally:
        loan.close()


def test_legacy_borrower_keeps_legacy_handoff(cable_broker):
  import socket
  cable, lender = cable_broker
  with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as borrower:
    borrower.settimeout(1)
    borrower.connect(str(lender.path))
    lending._send(borrower, {'op': 'borrow', 'name': 'old-runtime'})
    import time
    reply = lending._recv_line(borrower, bytearray(), time.monotonic() + 1)
    assert reply['ok']
    assert not reply['socket_handoff']
    assert not cable.listening


def test_borrower_crash_releases_connection_and_next_borrower_can_join(cable_broker):
  import os
  import socket
  import subprocess
  import sys
  cable, lender = cable_broker
  script = '''
import os, sys
from pathlib import Path
from jetlink.comma import lending
loan = lending.borrow(path=Path(sys.argv[1]))
sock = loan.accept(2)
sock.sendall(b'ready')
os._exit(0)
'''
  with socket.create_connection(cable.bound) as phone:
    phone.settimeout(3)
    child = subprocess.Popen([sys.executable, '-c', script, str(lender.path)], env=os.environ.copy())
    try:
      assert phone.recv(5) == b'ready'
      assert child.wait(timeout=3) == 0
      assert phone.recv(1) == b''
    finally:
      if child.poll() is None:
        child.kill()
        child.wait()
  with socket.create_connection(cable.bound) as phone:
    loan = lending.borrow(path=lender.path)
    try:
      with loan.accept(1) as accepted:
        accepted.sendall(b'ok')
        phone.settimeout(1)
        assert phone.recv(2) == b'ok'
    finally:
      loan.close()


def test_provision_cleanup_releases_loan_even_when_leave_fails():
  from jetlink.openpilot.provision import ProvisioningRun
  run = ProvisioningRun(Mock())
  client, loan = Mock(), Mock()
  client.leave.side_effect = OSError('phone disconnected')
  run.client, run.loan = client, loan
  run.close_link()
  client.close.assert_called_once()
  loan.close.assert_called_once()
  assert run.client is None and run.loan is None


@pytest.mark.parametrize('mode,live,expected', [
  ('off', None, 'Choose your device'),
  ('ios', None, 'connection service'),
  ('ios', {'present': False}, 'USB 3 data cable'),
  ('usb', {'present': True, 'udc': 'addressed'}, 'finish connecting'),
  ('ios', {'present': True, 'udc': 'configured'}, 'Local Network'),
  ('usb', {'present': True, 'udc': 'configured'}, 'model readiness'),
  ('ios', {'present': True, 'udc': 'configured', 'peer': '192.168.60.2'}, 'model readiness'),
])
def test_connection_guidance(mode, live, expected):
  from jetlink.openpilot.status import connection_detail
  assert expected in connection_detail(mode, live)


def test_invalid_descriptor_reply_closes_lease():
  import socket
  owner, client = socket.socketpair()
  loan = lending.Loan(client, bytearray(), '', '')
  loan.cable = loan._brokered = True
  owner.sendall(b'1')  # Claimed socket without an SCM_RIGHTS descriptor.
  try:
    with pytest.raises(ValueError, match='handoff'):
      loan.accept(1)
    assert loan.closed
  finally:
    loan.close()
    owner.close()


def test_new_borrower_falls_back_with_legacy_owner(cable_broker):
  cable, lender = cable_broker
  lender._take_cable = None
  loan = lending.borrow(path=lender.path)
  try:
    assert loan.cable
    assert not loan._brokered
    assert not cable.listening
  finally:
    loan.close()


def test_owner_loss_interrupts_phone_wait(cable_broker):
  cable, lender = cable_broker
  loan = lending.borrow(path=lender.path)
  lender.stop()
  try:
    with pytest.raises((OSError, ValueError)):
      loan.accept(30)
    assert loan.closed
  finally:
    loan.close()


def test_slow_owner_accept_reply_keeps_lease():
  import socket
  import threading
  owner, client = socket.socketpair()
  client.settimeout(lending.POLL)
  loan = lending.Loan(client, bytearray(), '', '')
  loan.cable = loan._brokered = True

  def answer_late():
    owner.recv(64)
    threading.Event().wait(lending.POLL * 2)
    owner.sendall(b'0')

  thread = threading.Thread(target=answer_late)
  thread.start()
  try:
    with pytest.raises(TimeoutError, match='app to connect'):
      loan.accept(.01)
    assert not loan.closed
  finally:
    thread.join()
    loan.close()
    owner.close()


def test_failed_cable_take_answers_empty_and_keeps_lease(cable_broker):
  import socket
  cable, lender = cable_broker
  lender._take_cable = Mock(side_effect=[OSError('reset'), None])
  loan = lending.borrow(path=lender.path)
  try:
    with pytest.raises(TimeoutError):
      loan.accept(.3)
    assert not loan.closed
    lender._take_cable = cable.take
    with socket.create_connection(cable.bound) as phone, loan.accept(1) as accepted:
      phone.sendall(b'x')
      accepted.settimeout(1)
      assert accepted.recv(1) == b'x'
  finally:
    loan.close()


def test_dial_reset_before_peer_lookup_is_dropped():
  conn = Mock()
  conn.getpeername.side_effect = OSError('not connected')
  cable = lending.CableListener()
  cable._srv = Mock()
  with patch.object(lending, 'newest_dial', return_value=conn):
    assert cable.poll() is None
  conn.close.assert_called_once()
  assert not cable.held and cable.peer is None


def _owner_with(take):
  from jetlink.comma.owner import Owner
  owner = Owner.__new__(Owner)
  owner.cable = Mock(take=Mock(return_value=take))
  owner.built_ios = True
  owner._peer = None
  return owner


def test_owner_publishes_brokered_peer_and_clears_it_on_empty_accept():
  from jetlink.comma import gadget
  sock = Mock()
  sock.getpeername.return_value = ('192.168.60.2', 4000)
  owner = _owner_with(sock)
  with patch.object(gadget, 'note_link') as note:
    assert owner.take_cable() is sock
    assert owner._peer == '192.168.60.2'
    owner.cable.take.return_value = None
    assert owner.take_cable() is None
    assert owner._peer is None
  assert [c.args for c in note.call_args_list] == [('cable', '192.168.60.2'), ('cable', None)]


def test_owner_drops_dial_reset_before_handoff():
  from jetlink.comma import gadget
  sock = Mock()
  sock.getpeername.side_effect = OSError('not connected')
  owner = _owner_with(sock)
  owner._peer = '192.168.60.2'
  with patch.object(gadget, 'note_link'):
    assert owner.take_cable() is None
  sock.close.assert_called_once()
  assert owner._peer is None


def test_dormant_guidance():
  from jetlink.openpilot.status import connection_detail
  assert 'asleep' in connection_detail('usb', {'present': True, 'dormant': True, 'udc': 'not attached'})
