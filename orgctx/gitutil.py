"""Thin subprocess wrappers around git.

We shell out to the `git` binary rather than depend on a library (like gitpython)
for two reasons: it keeps the dependency set tiny (git is assumed on PATH), and
the exact commands are transparent — you can copy any command here and run it in a
terminal to see what we see. Everything is read-only; `orgctx` never writes to the
target repo's history.

All functions take `repo` (a path to a git working tree) and run `git -C <repo>
...`, so the caller never has to chdir. This matters because the demo's dummy-org
is a *nested* repo: we always target it explicitly by path.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Commit:
    """The metadata we attach to change events, pulled from `git log`."""

    sha: str
    author: str
    date: str      # ISO-8601 (strict) author date
    subject: str


def _git(repo: str | Path, *args: str) -> str:
    """Run a git command in `repo` and return stripped stdout.

    We raise on non-zero exit (check=True) because every call here is a query we
    expect to succeed; a failure means a bad ref or a non-repo path, which is a
    programming/setup error we want surfaced loudly, not swallowed.
    """
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.rstrip("\n")


def rev_parse(repo: str | Path, ref: str) -> str:
    """Resolve a ref (e.g. "HEAD", "origin/main") to a full commit sha."""
    return _git(repo, "rev-parse", ref)


def list_commits(repo: str | Path, commit_range: str, *, paths: list[str] | None = None) -> list[Commit]:
    """List commits in `commit_range`, oldest first.

    `commit_range` is anything git log accepts: "A..B", a single sha, or "HEAD".
    We request oldest-first (`--reverse`) so that when `ingest` walks the list it
    processes history in chronological order — essential for origin-vs-latest
    attribution (the first `added` we see is the origin).

    We use an ASCII Unit Separator (\\x1f) between fields and a Record Separator
    (\\x1e) between records so subjects containing spaces/pipes never break
    parsing.
    """
    fmt = "%H\x1f%an\x1f%aI\x1f%s\x1e"
    args = ["log", "--reverse", f"--pretty=format:{fmt}"]
    # A single sha like "HEAD" would otherwise list *all* ancestors; when the
    # caller means "just this commit" they pass e.g. "HEAD~1..HEAD". We pass the
    # range through verbatim and let git interpret it.
    args.append(commit_range)
    if paths:
        args.append("--")
        args.extend(paths)
    raw = _git(repo, *args)
    commits: list[Commit] = []
    for record in raw.split("\x1e"):
        record = record.strip()
        if not record:
            continue
        sha, author, date, subject = record.split("\x1f")
        commits.append(Commit(sha=sha, author=author, date=date, subject=subject))
    return commits


def changed_files(repo: str | Path, commit_sha: str) -> list[str]:
    """Files touched by a single commit, relative to the repo root.

    We diff the commit against its first parent. A ROOT commit has no parent — and
    `diff-tree <sha>^!` yields empty output there rather than erroring — so we
    detect that case up front and list the whole introduced tree instead.
    """
    if parent_sha(repo, commit_sha) is None:
        out = _git(repo, "ls-tree", "--name-only", "-r", commit_sha)
    else:
        out = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", f"{commit_sha}^!")
    return [line for line in out.splitlines() if line]


def file_at_rev(repo: str | Path, commit_sha: str, path: str) -> str | None:
    """Return the contents of `path` as of `commit_sha`, or None if it did not
    exist at that revision.

    This is the workhorse behind the "extract entities at both revisions" ingest
    strategy: we read the file before and after a commit and diff the extracted
    entity tables, sidestepping fragile line-drift hunk mapping.
    """
    try:
        return _git(repo, "show", f"{commit_sha}:{path}")
    except subprocess.CalledProcessError:
        return None  # path absent at that revision (added or deleted)


def parent_sha(repo: str | Path, commit_sha: str) -> str | None:
    """The first parent of a commit, or None for a root commit."""
    try:
        return _git(repo, "rev-parse", f"{commit_sha}^")
    except subprocess.CalledProcessError:
        return None


def list_tree_files(repo: str | Path, rev: str, suffix: str = "") -> list[str]:
    """List files tracked at `rev`, optionally filtered by suffix (e.g. ".py").

    Used to build the schema-model index at a given revision so a route's embedded
    response-model fields resolve consistently whether or not the model's file was
    part of the commit being ingested.
    """
    out = _git(repo, "ls-tree", "-r", "--name-only", rev)
    return [line for line in out.splitlines() if line and (not suffix or line.endswith(suffix))]


def changed_line_ranges(repo: str | Path, commit_sha: str, path: str) -> list[tuple[int, int]]:
    """New-file line ranges touched by `commit_sha` in `path`.

    We parse unified-diff hunk headers ("@@ -a,b +c,d @@") and return the NEW-side
    ranges (c .. c+d-1). This drives *body-touch detection* in ingest: an entity
    whose signature is unchanged but whose definition span overlaps a changed range
    gets an INTERNAL change event (implementation churn). Without this, pure-body
    edits would be invisible and the compression story (churn that must be dropped)
    would have nothing to drop.
    """
    try:
        out = _git(repo, "diff", "--unified=0", f"{commit_sha}^", commit_sha, "--", path)
    except subprocess.CalledProcessError:
        # Root commit: treat the whole file as changed (every entity is "added"
        # anyway, so ranges are not consulted for it).
        return []
    ranges: list[tuple[int, int]] = []
    for line in out.splitlines():
        if not line.startswith("@@"):
            continue
        # Header form: @@ -old_start,old_len +new_start,new_len @@
        try:
            plus = line.split("+", 1)[1]
            spec = plus.split(" ", 1)[0]
            if "," in spec:
                start_s, len_s = spec.split(",")
                start, length = int(start_s), int(len_s)
            else:
                start, length = int(spec), 1
        except (IndexError, ValueError):
            continue
        if length == 0:
            # A pure deletion hunk has new_len 0; anchor a zero-width range at the
            # line so an entity straddling the deletion still counts as touched.
            ranges.append((start, start))
        else:
            ranges.append((start, start + length - 1))
    return ranges
