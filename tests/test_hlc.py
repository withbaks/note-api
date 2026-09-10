"""Sync conflict resolution tests."""

from note_core.hlc import HLC, HLCClock


def test_hlc_ordering():
    a = HLC(wall_time=1000, logical=0, node_id="device-a")
    b = HLC(wall_time=1000, logical=1, node_id="device-a")
    c = HLC(wall_time=1001, logical=0, node_id="device-b")
    assert a < b < c


def test_hlc_clock_increments():
    clock = HLCClock("device-a")
    h1 = clock.now()
    h2 = clock.now()
    assert h2 >= h1


    def test_hlc_receive():
        clock = HLCClock("device-a")
        remote = HLC(wall_time=5000, logical=3, node_id="device-b")
        received = clock.receive(remote)
        assert received.wall_time >= 5000
        assert received.logical >= 3


def test_hlc_from_string():
    h = HLC.from_string("1000:2:device-a")
    assert h.wall_time == 1000
    assert h.logical == 2
    assert h.node_id == "device-a"
