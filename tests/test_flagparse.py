import pytest

from tqdaemon.flagparse import FlagError, parse_flags


def test_bool_value_repeat_alias_and_positionals() -> None:
    opts, pos = parse_flags(
        ["p/t", "-d", "--force", "--set", "--a=1", "--set=--b", "--status", "failed"],
        bools=["downstream", "force"],
        values=["status"],
        repeat=["set"],
        aliases={"-d": "--downstream"},
    )
    assert pos == ["p/t"]
    assert opts == {"downstream": True, "force": True, "status": "failed", "set": ["--a=1", "--b"]}


def test_unknown_flag_stays_positional_and_missing_value_errors() -> None:
    _, pos = parse_flags(["--wat", "x"], bools=["force"])
    assert pos == ["--wat", "x"]
    with pytest.raises(FlagError, match="--set requires a value"):
        parse_flags(["--set"], repeat=["set"])
