"""Tiny declarative flag parser for the ``tq`` command wrappers (audit 2026-11b, D2).

Replaces hand-written ``while i < len(rest)`` loops that each re-implemented ``--flag value`` /
``--flag=value`` / usage errors (the ``tq retry --force`` bug came from one of them).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping


class FlagError(ValueError):
    """A flag was malformed (missing value); the message is user-facing."""


def parse_flags(
    rest: Iterable[str],
    *,
    bools: Iterable[str] = (),
    values: Iterable[str] = (),
    repeat: Iterable[str] = (),
    aliases: Mapping[str, str] | None = None,
) -> tuple[dict[str, object], list[str]]:
    """Split ``rest`` into ``(opts, positionals)``.

    ``bools`` are ``--name`` switches (value True, default False); ``values`` take ``--name V`` or
    ``--name=V`` (last wins, default None); ``repeat`` values accumulate into a list. ``aliases`` maps ``-d`` -> ``--name``.
    Unknown ``--x`` tokens stay positional so callers keep their own "bad key" errors.
    Raises FlagError when a value flag has no value.
    """
    bool_set = {b.lstrip("-") for b in bools}
    val_set = {v.lstrip("-") for v in values}
    rep_set = {r.lstrip("-") for r in repeat}
    alias = {k: v.lstrip("-") for k, v in (aliases or {}).items()}
    opts: dict[str, object] = {name: False for name in bool_set}
    for name in val_set:
        opts[name] = None  # absent value flag -> None
    for name in rep_set:
        opts[name] = []
    positionals: list[str] = []
    items = list(rest)
    i = 0
    while i < len(items):
        tok = items[i]
        name = alias.get(tok) or (tok[2:] if tok.startswith("--") else "")
        inline: str | None = None
        if not alias.get(tok) and "=" in name:
            name, inline = name.split("=", 1)
        if name in bool_set and inline is None:
            opts[name] = True
        elif name in val_set or name in rep_set:
            if inline is None:
                if i + 1 >= len(items):
                    raise FlagError(f"--{name} requires a value")
                inline = items[i + 1]
                i += 1
            if name in rep_set:
                lst = opts[name]
                assert isinstance(lst, list)
                lst.append(inline)
            else:
                opts[name] = inline
        else:
            positionals.append(tok)
        i += 1
    return opts, positionals
