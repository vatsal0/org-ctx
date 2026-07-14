"""OrgContext: git-native contract-change tracking + upstream-context injection.

This package implements the `orgctx` CLI described in plan.md. The design rests
on three primitives — *entities* (things with a contract others depend on), an
*interface graph* (edges between consumers and producers of those contracts), and
per-entity *change-event timelines*. Every subcommand is a thin operation over
those three primitives, persisted in a small SQLite index plus markdown files.

Read the individual module docstrings (and each directory's doc.md) for the
conceptual walkthrough; this file only marks the package root.
"""
