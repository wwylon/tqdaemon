"""F-259: unknown ``resources:`` keys are rejected (a silently ignored ``gpu: 1`` let two GPU jobs co-run)."""

from pathlib import Path

import pytest

from tqdaemon.taskspec import validate_task_spec


def _spec(resources: dict) -> dict:
    return {"task_id": "t", "command": "x.py", "resources": resources}


def test_gpu_key_is_rejected_with_hint() -> None:
    with pytest.raises(ValueError, match="gpu_vram_gib"):
        validate_task_spec(_spec({"gpu": 1}), Path("t.yaml"))


def test_known_keys_still_accepted() -> None:
    validate_task_spec(_spec({"cpu_cores": 2, "ram_gib": 4, "gpu_vram_gib": 5.5, "locks": ["ollama"]}), Path("t.yaml"))


def test_non_gpu_typos_warn_but_pass() -> None:
    with pytest.warns(UserWarning, match="ram_gb"):
        validate_task_spec(_spec({"cpu": 4, "ram_gb": 8}), Path("t.yaml"))
