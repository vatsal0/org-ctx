"""Generic `--flag value` -> Arguments-dataclass parser.

This is the "no middleman" argument machinery CLAUDE.md asks for: one reflector
that turns raw argv tokens into a typed dataclass instance, so subcommands declare
their surface purely as dataclass fields (see args.py) and never write argparse
plumbing. It supports:

  - positional args (consumed in declared order, per POSITIONAL_FIELDS),
  - `--flag value` for typed fields,
  - `--flag` as a bare switch for bool fields (presence => True),
  - `--field-name` with hyphens mapping to `field_name` with underscores,
  - type coercion driven by the dataclass field annotations (int/bool/str/optional).

It deliberately does NOT support abbreviations or `-x` short flags — explicitness
over cleverness for a research tool. Unknown flags raise, so typos fail loudly
rather than being silently ignored.
"""

from __future__ import annotations

import dataclasses
import typing
from dataclasses import MISSING


def _field_types(cls: type) -> dict[str, type]:
    """Resolve each dataclass field to a concrete 'base' type for coercion.

    We unwrap Optional[T] (i.e. Union[T, None]) down to T, since our flags are
    either present-with-value or absent (None). Anything we don't recognize falls
    back to str.
    """
    resolved = typing.get_type_hints(cls)
    out: dict[str, type] = {}
    for f in dataclasses.fields(cls):
        ann = resolved.get(f.name, str)
        origin = typing.get_origin(ann)
        if origin is typing.Union:
            # Optional[T] / T | None -> pick the first non-None arg.
            non_none = [a for a in typing.get_args(ann) if a is not type(None)]
            ann = non_none[0] if non_none else str
        out[f.name] = ann
    return out


def _coerce(value: str, target: type):
    """Coerce a raw string token to the field's declared type."""
    if target is bool:
        # Only reached when a bool flag is given an explicit value; accept the
        # common truthy/falsey spellings.
        return value.lower() in {"1", "true", "yes", "on"}
    if target is int:
        return int(value)
    return value  # str and anything else


def parse_into(cls: type, positional_names: list[str], argv: list[str]):
    """Parse `argv` into an instance of dataclass `cls`.

    `positional_names` lists the fields (in order) that may be supplied
    positionally. Remaining `--flags` fill keyword fields. Fields left unset keep
    their dataclass defaults; a required field (no default) left unset raises.
    """
    types = _field_types(cls)
    valid = set(types)
    values: dict[str, object] = {}

    # First pass: split tokens into positionals (leading, non---) and flags.
    positionals: list[str] = []
    i = 0
    flag_tokens: list[str] = []
    seen_flag = False
    while i < len(argv):
        tok = argv[i]
        if tok.startswith("--"):
            seen_flag = True
            flag_tokens.append(tok)
        elif not seen_flag:
            positionals.append(tok)
        else:
            flag_tokens.append(tok)
        i += 1

    # Assign positionals in declared order.
    for name, val in zip(positional_names, positionals):
        values[name] = _coerce(val, types[name])
    if len(positionals) > len(positional_names):
        extra = positionals[len(positional_names):]
        raise SystemExit(f"unexpected positional argument(s): {extra}")

    # Assign flags.
    i = 0
    while i < len(flag_tokens):
        tok = flag_tokens[i]
        if not tok.startswith("--"):
            raise SystemExit(f"expected a --flag but got: {tok!r}")
        name = tok[2:].replace("-", "_")
        if name not in valid:
            raise SystemExit(f"unknown flag: --{tok[2:]}")
        target = types[name]
        if target is bool:
            # Bare switch: `--verbose` => True, unless the next token looks like a
            # value (rare; we support `--verbose false` too).
            if i + 1 < len(flag_tokens) and not flag_tokens[i + 1].startswith("--"):
                values[name] = _coerce(flag_tokens[i + 1], bool)
                i += 2
            else:
                values[name] = True
                i += 1
        else:
            if i + 1 >= len(flag_tokens):
                raise SystemExit(f"flag --{tok[2:]} expects a value")
            values[name] = _coerce(flag_tokens[i + 1], target)
            i += 2

    # Verify all required fields (those without a default) are satisfied.
    for f in dataclasses.fields(cls):
        if f.name in values:
            continue
        if f.default is MISSING and f.default_factory is MISSING:  # type: ignore[attr-defined]
            raise SystemExit(f"missing required argument: {f.name}")

    return cls(**values)  # type: ignore[arg-type]
