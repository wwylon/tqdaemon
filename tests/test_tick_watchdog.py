from tqdaemon.tick_watchdog import TickWatchdog


def test_dumps_once_per_stall_and_rearms_on_beat() -> None:
    now = [0.0]
    dumps: list[float] = []
    wd = TickWatchdog(stall_sec=100, dump=dumps.append, clock=lambda: now[0])
    now[0] = 50
    assert wd.check() is False
    now[0] = 130
    assert wd.check() is True
    now[0] = 400
    assert wd.check() is False  # one-shot per stall
    assert dumps == [130.0]
    wd.beat()
    now[0] = 600
    assert wd.check() is True  # re-armed
    assert len(dumps) == 2
