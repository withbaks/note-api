"""HLC receive semantics used by sync pull."""

from note_core.hlc import HLC, HLCClock


def test_receive_advances_past_remote():
    clock = HLCClock("device-b")
    remote = HLC.from_string("1700000000000:5:device-a")
    merged = clock.receive(remote)
    assert merged.wall_time >= remote.wall_time


def test_local_after_receive_beats_stale_remote():
    clock = HLCClock("device-b")
    remote = HLC.from_string("1700000000000:5:device-a")
    clock.receive(remote)
    later = clock.now()
    assert later > remote
