import time

import pytest

from tqdaemon import jobs


def _wait(job_id: str) -> dict:
    for _ in range(200):
        j = jobs.get(job_id)
        assert j is not None
        if j["state"] != "running":
            return j
        time.sleep(0.01)
    pytest.fail("job did not finish")


def test_job_result_and_error_and_unknown() -> None:
    ok = _wait(jobs.submit("t", lambda: {"archived": 3}))
    assert ok["state"] == "done" and ok["result"] == {"archived": 3}

    def boom() -> dict:
        raise RuntimeError("nope")

    bad = _wait(jobs.submit("t", boom))
    assert bad["state"] == "error" and "nope" in bad["error"]
    assert jobs.get("missing") is None


def test_registry_is_bounded() -> None:
    ids = [jobs.submit("t", lambda: {}) for _ in range(jobs.MAX_JOBS + 5)]
    assert jobs.get(ids[0]) is None
    assert jobs.get(ids[-1]) is not None
