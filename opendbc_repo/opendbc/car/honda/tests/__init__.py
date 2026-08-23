class FakePacker:
  """Hands back the signal values instead of CAN bytes so tests can assert on them."""

  @staticmethod
  def make_can_msg(name, bus, values):
    return name, bus, values
