#!/usr/bin/env python3
"""verikit - machine-checked promises for any repository, beside your existing tests.

A repo lists its promises in verification/verification.toml. Each [[check]] has a
plain-English rule, ONE command that checks it (a TLA+ model, a property test in your
own test project, a code-level verifier - any tool, any language), the files it covers,
broken variants that MUST fail, and a record of the content it was last confirmed against.

    python verikit.py doctor                 stacks, test frameworks and tools found; what fits
    python verikit.py init [--hook] [--ci github,azure]
                                             scaffold verification/ (+ skills, hooks, CI)
    python verikit.py list                   every check: rule, covers, bounds, sync state
    python verikit.py report [--md F|--html F]   every promise and where it stands (runs nothing)
    python verikit.py run [ID ...]           run checks; every must_fail variant has to FAIL
          [--tag T] [--changed [REF] | --ci] [--isolated] [--jobs N] [--seed N]
          [--junit FILE] [--json FILE] [--summary FILE.md]
    python verikit.py stale [--strict]       checks whose covered content changed since their last sync
    python verikit.py sync [ID ...] [--tag T]
                                             re-run, then record that content in verification/sync/
    python verikit.py seed add ID SEED       keep a failing case; `run` replays saved seeds
    python verikit.py tlc SPEC.tla [-c CFG] [--const K=V] [--inv NAME]
                                             TLA+ model checker (local Java, else Docker)
    python verikit.py trace MODEL [TRACES] [--record CMD]
                                             recorded runs of the real code must be allowed by the model
    python verikit.py explore MODEL.py       built-in breadth-first explorer (zero install)
    python verikit.py hook                   Claude Code SessionStart + Stop hook
    python verikit.py version

Exit codes: 0 = holds / all good, 1 = a property or check failed, 2 = tool or setup error.
Needs Python 3.11+ (stdlib only) and git. TLA+ checks also need Java 11+ or Docker.
"""
from __future__ import annotations

import sys

if sys.version_info < (3, 11):  # before `import tomllib`, so an old interpreter gets a clear message
    sys.exit("verikit needs Python 3.11 or newer (this is %d.%d)" % sys.version_info[:2])

import argparse
import ast
import hashlib
import importlib.util
import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
import tomllib
import urllib.request
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path

VERSION = "0.4.1"
TLA_VERSION = "1.7.4"
TLA_URL = f"https://github.com/tlaplus/tlaplus/releases/download/v{TLA_VERSION}/tla2tools.jar"
TLA_SHA256 = "936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88"  # official v1.7.4 asset
JAVA_IMAGE = "eclipse-temurin:21-jre"
MANIFEST_REL = Path("verification") / "verification.toml"
STATE_DIR = ".verikit"
KIT_DIR = Path(__file__).resolve().parent
SKIP_DIRS = {".git", "node_modules", "bin", "obj", "dist", "build", "out", "packages", "vendor", ".venv",
             "venv", "__pycache__", ".angular", ".next", "coverage", "target", ".verikit", "TestResults"}

OK, FAILED, ERROR = 0, 1, 2

try:  # never crash on a Windows console that cannot print a character
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


class VerikitError(Exception):
    def __init__(self, msg: str, code: int = ERROR):
        super().__init__(msg)
        self.code = code


def die(msg: str, code: int = ERROR):
    raise VerikitError(msg, code)


def warn(msg: str) -> None:
    print(f"verikit: warning: {msg}", file=sys.stderr)


# --------------------------------------------------------------------------- git

def git(cwd: Path, *args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return 127, "git not found"
    return p.returncode, (p.stdout + p.stderr).rstrip()  # keep leading spaces: porcelain needs them


def git_toplevel(path: Path) -> Path | None:
    code, out = git(path, "rev-parse", "--show-toplevel")
    return Path(out).resolve() if code == 0 and out else None


def find_root(start: Path | None = None) -> Path:
    """Nearest folder at or above `start` that holds verification/verification.toml.
    Falls back to the git top level, then `start`. Monorepos keep one root per service."""
    start = (start or Path.cwd()).resolve()
    for d in [start, *start.parents]:
        if (d / MANIFEST_REL).is_file():
            return d
    return git_toplevel(start) or start


def git_prefix(root: Path) -> str:
    """The root's path inside its git repo ('' at the top level), POSIX style."""
    top = git_toplevel(root)
    if not top:
        return ""
    rel = root.resolve().relative_to(top).as_posix()
    return "" if rel == "." else rel


# --------------------------------------------------------------------------- manifest

def load_manifest(root: Path) -> dict:
    path = root / MANIFEST_REL
    if not path.exists():
        die(f"no {MANIFEST_REL.as_posix()} in {root}. Run `python verikit.py init` first.")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        die(f"{MANIFEST_REL.as_posix()}: {e}")
    checks = data.get("check", [])
    sources = data.get("sources", {})
    for name, spec in sources.items():
        if not re.fullmatch(r"[A-Za-z][\w-]+", name) or not isinstance(spec, dict) \
                or not isinstance(spec.get("path"), str):
            die(f"[sources.{name}] needs a name of 2+ letters/digits/-/_ and `path = \"../other-repo\"` "
                "(where that repository is checked out, relative to the folder holding verification/)")
    seen = set()
    for i, c in enumerate(checks):
        for key in ("id", "rule", "run", "covers"):
            if key not in c:
                die(f"[[check]] #{i + 1} is missing `{key}`")
        if c["id"] in seen:
            die(f"duplicate check id {c['id']!r}")
        seen.add(c["id"])
        if not isinstance(c["covers"], list) or not c["covers"]:
            die(f"check {c['id']!r}: `covers` must be a non-empty list of globs")
        c.setdefault("must_fail", [])
        if not re.fullmatch(r"[\w.-]+", str(c["id"])):
            die(f"check id {c['id']!r}: use letters, digits, '.', '_' and '-' only (it names verification/sync/<id>.json)")
        c.setdefault("synced_commit", "")   # 0.3 manifests; newer syncs live in verification/sync/
        c.setdefault("tags", [])
        c.setdefault("harness", [])
        if not isinstance(c["harness"], list):
            die(f"check {c['id']!r}: `harness` must be a list of globs (the check's own test/model files)")
        c["_sources"] = sources
        for pattern in (*c["covers"], *c["harness"]):
            m = SOURCE_PREFIX.match(pattern)
            if m and m.group(1) not in sources:
                die(f"check {c['id']!r}: {pattern!r} names a repository {m.group(1)!r}, but the manifest has no "
                    f"[sources.{m.group(1)}] table. Add one with `path = \"../{m.group(1)}\"`.")
            path, symbol = split_anchor(pattern)
            if symbol and re.search(r"[*?\[]", path):
                die(f"check {c['id']!r}: {pattern!r} - `#{symbol}` needs one exact file, not a glob")
        if not c["must_fail"]:
            warn(f"check {c['id']!r} has no [[check.must_fail]] - a check you have never seen fail proves nothing.")
        for j, mf in enumerate(c["must_fail"]):
            label = f"check {c['id']!r}, must_fail #{j + 1} ({mf.get('why', 'no why')})"
            if not mf.get("match"):
                die(f"{label} has no `match`. Give a regex naming the broken rule, so a crash or a "
                    "syntax error cannot count as 'it failed'.")
            try:
                re.compile(mf["match"])
            except re.error as e:
                die(f"{label}: `match` is not a valid regex: {e}")
            if mf.get("patch") and not (root / mf["patch"]).is_file():
                die(f"{label}: patch file {mf['patch']} not found")
            edits = variant_edits(mf)
            if edits and mf.get("patch"):
                die(f"{label}: give a `patch` or `edits`, not both")
            for e in edits:
                if not isinstance(e, dict) or not all(isinstance(e.get(k), str) for k in ("file", "find", "replace")) \
                        or not e["find"]:
                    die(f"{label}: each edit needs `file`, `find` and `replace` strings, e.g. "
                        "edits = [{ file = \"src/pay.js\", find = \"if (paid) return;\", replace = \"\" }]")
    data["check"] = checks
    return data


def select(checks: list[dict], ids: list[str], tags: list[str] | None = None) -> list[dict]:
    chosen = checks
    if ids:
        by_id = {c["id"]: c for c in checks}
        missing = [i for i in ids if i not in by_id]
        if missing:
            die(f"unknown check id(s): {', '.join(missing)}. Known: {', '.join(by_id)}")
        chosen = [by_id[i] for i in ids]
    if tags:
        chosen = [c for c in chosen if set(tags) & set(c["tags"])]
    return chosen


SOURCE_PREFIX = re.compile(r"^([A-Za-z][\w-]+):(?![\\/])(.+)$")  # web:src/**  (not C:\ or C:/)
MISSING = "(missing source "


def _source_of(pattern: str, check: dict) -> tuple[str | None, str]:
    m = SOURCE_PREFIX.match(pattern)
    if m and m.group(1) in check.get("_sources", {}):
        return m.group(1), m.group(2)
    return None, pattern


def watched(check: dict) -> list[str]:
    """Globs in THIS repository whose changes make a check stale: the product code it describes
    (`covers`) and the check's own test/harness files outside verification/ (`harness`)."""
    return [p for p in (*check["covers"], *check.get("harness", [])) if _source_of(p, check)[0] is None]


def source_globs(check: dict) -> dict[str, list[str]]:
    """Globs in OTHER repositories (`name:glob` in covers/harness), by source name."""
    out: dict[str, list[str]] = {}
    for p in (*check["covers"], *check.get("harness", [])):
        name, glob = _source_of(p, check)
        if name:
            out.setdefault(name, []).append(glob)
    return out


def source_env(name: str) -> str:
    return "VERIKIT_SOURCE_" + re.sub(r"\W", "_", name).upper()


def source_dir(root: Path, check: dict, name: str) -> Path | None:
    """Where source `name` is checked out (env VERIKIT_SOURCE_<NAME>, else its manifest path),
    or None when there is no git checkout there."""
    raw = os.environ.get(source_env(name)) or check["_sources"][name]["path"]
    path = (root / raw).resolve()
    return path if path.is_dir() and git_toplevel(path) else None


def pathspecs(check: dict) -> list[str]:
    return [f":(glob){split_anchor(p)[0]}" for p in watched(check)]


def content_digest(data: bytes) -> str:
    """Digest of a file's content with line endings normalised, so a CRLF checkout and an LF
    checkout of the same commit give the same value."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()[:16]


def _git_files(repo_dir: Path, globs: list[str]) -> list[str]:
    """Files under `repo_dir` matching `globs` that git tracks or would track (not ignored
    build output), relative to `repo_dir`. Outside a git repository: every match."""
    if not globs:
        return []
    try:
        p = subprocess.run(["git", "ls-files", "-z", "-co", "--exclude-standard", "--",
                            *[f":(glob){g}" for g in globs]], cwd=repo_dir, capture_output=True)
    except OSError:
        p = None
    if p is None or p.returncode != 0:
        return sorted({f.relative_to(repo_dir).as_posix() for g in globs for f in repo_dir.glob(g) if f.is_file()})
    return sorted({f for f in p.stdout.decode("utf-8", "replace").split("\0") if f})


def check_files(root: Path, check: dict) -> list[Path]:
    """The check's own verification files: every file its commands and patches name, plus the
    verification/<area>/ folders those live in (model, config, mutants). Not the manifest
    (wording edits must not look like drift) and not other checks' areas."""
    files: set[Path] = set()
    vdir = (root / "verification").resolve()
    areas: set[Path] = set()
    for text in [check["run"], *[mf.get("run", "") for mf in check["must_fail"]],
                 *[mf.get("patch", "") for mf in check["must_fail"]]]:
        for token in re.findall(r"[\w./\\:-]+", text):
            p = root / token
            if p.is_file():
                files.add(p)
                rp = p.resolve()
                if vdir in rp.parents and rp.parent != vdir:
                    areas.add(rp.parent)
    for area in areas:
        files.update(x for x in area.rglob("*") if x.is_file()
                     and not x.name.startswith((".verikit-", "VerikitTrace")))   # generated during a run
    files = {f for f in files if f.resolve() != Path(__file__).resolve()}
    # Only what git tracks or would track: a `run` line may name build output (a test dll),
    # which differs per machine and does not exist in a fresh clone.
    rels = {}
    for f in files:
        try:
            rels[f.resolve().relative_to(root.resolve()).as_posix()] = f
        except ValueError:
            pass                      # outside the verification root: not this check's file
    try:
        p = subprocess.run(["git", "ls-files", "-z", "-co", "--exclude-standard", "--",
                            *[f":(literal){r}" for r in rels]], cwd=root, capture_output=True)
    except OSError:
        p = None
    if rels and p is not None and p.returncode == 0:
        kept = {r for r in p.stdout.decode("utf-8", "replace").split("\0") if r}
        return sorted(f for r, f in rels.items() if r in kept)
    return sorted(rels.values())


def current_items(root: Path, check: dict) -> tuple[dict[str, str], list[str]]:
    """label -> content digest for everything that decides whether the check still describes
    the code: covered and harness files here, `name:path` files in other repositories, and the
    check's own verification files. Second value: notes for repositories with no checkout."""
    items: dict[str, str] = {}

    def add(folder: Path, entries: list[str], prefix: str) -> None:
        split = [split_anchor(e) for e in entries]
        for f in _git_files(folder, [path for path, symbol in split if symbol is None]):
            if (folder / f).is_file():
                items[prefix + f] = content_digest((folder / f).read_bytes())
        for path, symbol in split:
            if symbol is not None:      # only that function or class, not the whole file
                items[f"{prefix}{path}#{symbol}"] = anchor_digest(folder / path, symbol)

    add(root, watched(check), "")
    for p in check_files(root, check):
        items[p.resolve().relative_to(root.resolve()).as_posix()] = content_digest(p.read_bytes())
    notes = []
    for name, globs in sorted(source_globs(check).items()):
        src = source_dir(root, check, name)
        if src is None:
            notes.append(f"{MISSING}{name}: no git checkout at {check['_sources'][name]['path']}; "
                         f"clone it there or set {source_env(name)})")
            continue
        add(src, globs, f"{name}:")
    return items, notes


def items_hash(items: dict[str, str]) -> str:
    return hashlib.sha256("\n".join(f"{k}\t{v}" for k, v in sorted(items.items())).encode()).hexdigest()


def fingerprint(root: Path, check: dict) -> str:
    """Local identity of 'this check on this content': its meaning in the manifest plus every
    item. Used for the last-pass state and the hook; never committed."""
    meaning = {k: v for k, v in check.items()
               if k not in ("synced_commit", "synced_sources") and not k.startswith("_")}
    items, notes = current_items(root, check)
    return hashlib.sha256((json.dumps(meaning, sort_keys=True, default=str) + items_hash(items)
                           + "".join(notes)).encode()).hexdigest()


# --------------------------------------------------------------------------- anchors (path#Symbol)

ANCHOR = re.compile(r"^(?P<path>.+)#(?P<symbol>[A-Za-z_$][\w$]*)$")
NO_ANCHOR = "(anchor not found)"


def split_anchor(entry: str) -> tuple[str, str | None]:
    """`src/Checkout.cs#PayForOrder` -> (path, symbol). The check then watches only that
    function or class, not the whole file. A '#' followed by a path separator is a folder name."""
    m = ANCHOR.match(entry)
    return (m["path"], m["symbol"]) if m else (entry, None)


def _skip_literal(text: str, i: int) -> int:
    """If text[i] starts a comment or a string, the index just past it; else i."""
    two = text[i:i + 2]
    if two == "//":
        j = text.find("\n", i)
        return len(text) if j < 0 else j
    if two == "/*":
        j = text.find("*/", i + 2)
        return len(text) if j < 0 else j + 2
    ch = text[i]
    if ch in "\"'`":
        verbatim = ch == '"' and i > 0 and text[i - 1] in "@$" and "@" in text[max(0, i - 2):i]
        j = i + 1
        while j < len(text):
            if verbatim and text[j] == '"':
                if text[j + 1:j + 2] == '"':
                    j += 2
                    continue
                return j + 1
            if not verbatim and text[j] == "\\":
                j += 2
                continue
            if text[j] == ch or (text[j] == "\n" and ch != "`" and not verbatim):
                return j + 1
            j += 1
        return len(text)
    return i


def _body_end(text: str, i: int) -> tuple[int, str] | None:
    """From just after a declared name: (index past the body, kind). kind "block": the matching
    '}' of its first '{'. kind "expr": the ';' that ends an expression body. None if neither."""
    depth = 0          # () and [] nesting, so a ';' or '{' inside parameters does not count
    while i < len(text):
        j = _skip_literal(text, i)
        if j != i:
            i = j
            continue
        ch = text[i]
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == ";" and depth <= 0:
            return i + 1, "expr"
        elif ch == "}" and depth <= 0:
            return None                      # the enclosing block ended first: not a declaration
        elif ch == "{" and depth <= 0:
            # `Promise<{ ok: boolean }>` or `): { ok: boolean } {`: a brace right after < : | & ,
            # opens a type written inline, not the body. Taking it for the body would cut the
            # region short, and edits to the real body would then go unnoticed.
            is_type_literal = text[:i].rstrip()[-1:] in ("<", ":", "|", "&", ",")
            level = 0
            while is_type_literal and i < len(text):
                j = _skip_literal(text, i)
                if j != i:
                    i = j
                    continue
                level += (text[i] == "{") - (text[i] == "}")
                i += 1
                if level == 0:
                    break
            if is_type_literal:
                continue
            while i < len(text):
                j = _skip_literal(text, i)
                if j != i:
                    i = j
                    continue
                if text[i] == "{":
                    level += 1
                elif text[i] == "}":
                    level -= 1
                    if level == 0:
                        return i + 1, "block"
                i += 1
            return None
        i += 1
    return None


CALL_BEFORE = re.compile(r"(\.|\(|\)|\[|,|=|\breturn|\bnew|\bawait|\bthrow|\byield|\belse|\bdo|!|&&|\|\||\?|:|\+|-|\*|/|<)\s*$")
ASSIGNED_BEFORE = re.compile(r"\b(const|let|var|static|readonly|public|private|protected|export)\b[^=;(]*$")


def extract_region(text: str, symbol: str, suffix: str = "") -> str | None:
    """The source of every declaration named `symbol` in `text`, or None when there is none.
    1. Explicit markers win, in any language: a line with `verikit:begin NAME` ... `verikit:end NAME`.
    2. Python: `def NAME` / `class NAME` with its indented body.
    3. Brace languages (C#, Java, JS/TS, Go, ...): `NAME(...) {...}`, `class NAME {...}`,
       `NAME = (...) => ...`, with the attribute/decorator lines directly above.
    Overloads are all included, so adding or changing any of them counts as a change."""
    text = text.replace("\r\n", "\n")
    lines = text.split("\n")
    name = symbol
    begin = [n for n, ln in enumerate(lines) if re.search(rf"verikit:begin\s+{re.escape(symbol)}\b", ln)]
    if begin:
        end = [n for n, ln in enumerate(lines) if re.search(rf"verikit:end\s+{re.escape(symbol)}\b", ln)]
        if len(begin) != 1 or len(end) != 1 or end[0] < begin[0]:
            return None
        return "\n".join(ln.rstrip() for ln in lines[begin[0]:end[0] + 1])
    regions: list[str] = []
    if suffix == ".py":
        for n, ln in enumerate(lines):
            m = re.match(rf"^(\s*)(async\s+)?(def|class)\s+{re.escape(name)}\b", ln)
            if not m:
                continue
            start = n
            while start > 0 and lines[start - 1].strip().startswith("@"):
                start -= 1
            stop = n + 1
            while stop < len(lines) and (not lines[stop].strip() or len(lines[stop]) - len(lines[stop].lstrip()) > len(m.group(1))):
                stop += 1
            regions.append("\n".join(ln.rstrip() for ln in lines[start:stop]).rstrip())
        return "\n\n".join(regions) or None
    offsets, pos = [], 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln) + 1
    i, done_until = 0, -1
    pattern = re.compile(rf"(?<![\w$.]){re.escape(name)}(?![\w$])")
    while i < len(text):
        j = _skip_literal(text, i)
        if j != i:
            i = j
            continue
        m = pattern.match(text, i)
        if not m or i < done_until:
            i += 1
            continue
        line_no = text.count("\n", 0, i)
        before = text[offsets[line_no]:i]
        after = text[m.end():m.end() + 200].lstrip()
        is_type = re.search(r"\b(class|interface|struct|enum|record|namespace|module|trait|impl)\s+$", before)
        go_method = re.match(r"\s*func\s*\([^)]*\)\s*$", before)        # func (s *Service) Pay(...) {
        is_func = (after.startswith("(") or after.startswith("<")) and (go_method or not CALL_BEFORE.search(before))
        is_assigned = re.match(r"[:=]\s*(async\b\s*)?(function\b|\(|[\w$]+\s*=>)", after) and \
            (ASSIGNED_BEFORE.search(before) or not before.strip())
        if not (is_type or is_func or is_assigned):
            i = m.end()
            continue
        found = _body_end(text, m.end())
        # a body in braces, or `=> expression;`. `Name(args);` alone is a call, not a declaration.
        if found is None or (found[1] == "expr" and "=>" not in text[m.end():found[0]]):
            i = m.end()
            continue
        end = found[0]
        start = line_no
        while start > 0 and re.match(r"\s*(\[|@)", lines[start - 1]):
            start -= 1
        end_line = text.count("\n", 0, end)
        regions.append("\n".join(ln.rstrip() for ln in lines[start:end_line + 1]))
        done_until = end
        i = end
    return "\n\n".join(regions) or None


def anchor_digest(path: Path, symbol: str) -> str:
    try:
        region = extract_region(path.read_text(encoding="utf-8", errors="replace"), symbol, path.suffix)
    except OSError:
        region = None
    return content_digest(region.encode()) if region else NO_ANCHOR


# --------------------------------------------------------------------------- sync records

SYNC_DIR = Path("verification") / "sync"
MAX_SYNC_FILES = 3000


def sync_path(root: Path, check_id: str) -> Path:
    return root / SYNC_DIR / f"{check_id}.json"


def read_sync(root: Path, check_id: str) -> dict | None:
    try:
        rec = json.loads(sync_path(root, check_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) and rec.get("hash") else None


def write_sync(root: Path, check: dict, items: dict[str, str]) -> Path:
    """Record the content the check was just confirmed against. One small file per check, so
    two branches that sync different checks merge without conflict, and no commit id, so
    squash merges, rebases and shallow clones cannot invalidate it."""
    rec: dict = {"check": check["id"], "hash": items_hash(items)}
    if len(items) <= MAX_SYNC_FILES:
        rec["files"] = dict(sorted(items.items()))
    path = sync_path(root, check["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    write_keep(path, json.dumps(rec, indent=1) + "\n")
    return path


def sync_base(root: Path, check_id: str) -> str:
    """The last commit that changed the check's sync record: the point to review from.
    Always reachable, because it is found in this branch's own history."""
    rel = (SYNC_DIR / f"{check_id}.json").as_posix()
    code, out = git(root, "log", "-1", "--format=%h", "--", rel)
    return out.strip() if code == 0 else ""


def read_keep(path: Path) -> str:
    """Read text without translating line endings, so a rewrite keeps the file's style."""
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def write_keep(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def state_path(root: Path, name: str) -> Path:
    d = root / STATE_DIR
    d.mkdir(exist_ok=True)
    (d / ".gitignore").write_text("*\n", encoding="utf-8")
    return d / name


def read_state(root: Path) -> dict:
    try:
        return json.loads((root / STATE_DIR / "last-run.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_state(root: Path, state: dict) -> None:
    state_path(root, "last-run.json").write_text(json.dumps(state, indent=2), encoding="utf-8")


def expand(cmd: str) -> str:
    q = lambda s: f'"{s}"' if " " in s else s  # noqa: E731
    return (cmd.replace("{python}", q(sys.executable))
               .replace("{verikit}", f"{q(sys.executable)} {q(str(Path(__file__).resolve()))}"))


def with_sources(root: Path, check: dict, cmd: str) -> tuple[str, dict[str, str]]:
    """Expand {source:NAME} to that repository's checkout path and export VERIKIT_SOURCE_<NAME>
    for every source that is checked out. A command that needs a missing checkout fails."""
    env: dict[str, str] = {}
    for name in check.get("_sources", {}):
        d = source_dir(root, check, name)
        if d:
            env[source_env(name)] = str(d)
        if "{source:" + name + "}" in cmd:
            if not d:
                raise VerikitError(f"this command needs a checkout of source {name!r} at "
                                   f"{check['_sources'][name]['path']} (or set {source_env(name)})", FAILED)
            cmd = cmd.replace("{source:" + name + "}", f'"{d}"' if " " in str(d) else str(d))
    return cmd, env


def run_shell(cmd: str, cwd: Path, timeout: int, seed: int | None = None,
              extra_env: dict[str, str] | None = None) -> tuple[int, str, float]:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", FORCE_COLOR="0", NO_COLOR="1", **(extra_env or {}))
    if seed is not None:
        env["VERIKIT_SEED"] = str(seed)
    t0 = time.time()
    try:
        p = subprocess.run(expand(cmd), shell=True, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, env=env)
        return p.returncode, p.stdout + p.stderr, time.time() - t0
    except subprocess.TimeoutExpired as e:
        out = e.stdout if isinstance(e.stdout, str) else ""
        return 124, out + f"\n[verikit] timed out after {timeout}s", time.time() - t0


NOISE = re.compile(r"^\s*(at |async |buildError|asyncThrowIfFailed|stack: \|-|code: 'ERR_|location: |"
                   r"duration_ms|failureType|# (suites|cancelled|skipped|todo|duration_ms)|\.\.\.$|---$)"
                   r"|node_modules[\\/]|node:internal|^\s+at [\w.<>`]+\(.*\) in ")


def tail(text: str, n: int = 30) -> str:
    """Last n meaningful lines of a tool's output (stack frames and runner noise dropped)."""
    lines = [line for line in text.rstrip().splitlines() if not NOISE.search(line)]
    cut = lines[-n:]
    prefix = [f"      ... ({len(lines) - n} earlier lines)"] if len(lines) > n else []
    return "\n".join(prefix + [f"      | {line}" for line in cut])


# --------------------------------------------------------------------------- broken-variant patches

PENDING = "applied-patch.json"


def _patch_args(root: Path, patch: Path) -> tuple[Path, list[str]]:
    """Patches are written relative to the verification root. Apply them from the git top
    level with --directory, because `git apply` run inside a subfolder silently skips paths."""
    top = git_toplevel(root) or root
    prefix = git_prefix(root)
    args = [f"--directory={prefix}"] if prefix else []
    return top, [*args, str(patch.resolve())]


def _patched_files(root: Path, patch: Path) -> list[Path]:
    top, args = _patch_args(root, patch)
    code, out = git(top, "apply", "--numstat", *args)
    if code != 0:
        return []
    return [top / line.split("\t")[-1] for line in out.splitlines() if "\t" in line]


def _digest(files: list[Path]) -> dict[str, str]:
    return {str(f): hashlib.sha256(f.read_bytes()).hexdigest() if f.exists() else "-" for f in files}


BACKUP_DIR = "patch-backup"


def _restore(root: Path, files: list[dict]) -> list[str]:
    """Put back the exact bytes saved before a patch was applied (a file the patch created is
    removed). Byte-exact, so line-ending conversion (core.autocrlf) cannot leave a diff behind.
    Returns the files that could not be restored."""
    bad = []
    for f in files:
        path = Path(f["path"])
        try:
            if f["backup"]:
                data = (root / STATE_DIR / BACKUP_DIR / f["backup"]).read_bytes()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            elif path.exists():
                path.unlink()
        except OSError as e:
            bad.append(f"{path} ({e})")
    return bad


def _clear_pending(root: Path) -> None:
    (root / STATE_DIR / PENDING).unlink(missing_ok=True)
    shutil.rmtree(root / STATE_DIR / BACKUP_DIR, ignore_errors=True)


def recover_pending(root: Path) -> None:
    """Revert a broken-variant patch that an interrupted run left applied."""
    rec_path = root / STATE_DIR / PENDING
    if not rec_path.exists():
        return
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    if "files" in rec:  # 0.3+: exact backups of every file the patch touches
        bad = _restore(root, rec["files"])
        if bad:
            die(f"an interrupted run left {rec['rel']} applied and these files could not be restored: "
                f"{', '.join(bad)}. Backups are in {root / STATE_DIR / BACKUP_DIR}; restore them by hand, "
                f"then delete {rec_path}.")
    else:  # record written by an older runner: reverse the patch
        top, args = _patch_args(root, Path(rec["patch"]))
        c, o = git(top, "apply", "-R", "--check", *args)
        if c != 0:
            die(f"an interrupted run left {rec['rel']} applied and it no longer reverts cleanly:\n{o}\n"
                f"Restore the files it touches by hand (git diff), then delete {rec_path}.")
        git(top, "apply", "-R", *args)
    _clear_pending(root)
    warn(f"reverted broken-variant patch {rec['rel']} left applied by an interrupted run")


def variant_edits(mf: dict) -> list[dict]:
    """The find-and-replace edits of a broken variant (`edit = {...}` or `edits = [...]`)."""
    edits = mf.get("edits") or ([mf["edit"]] if mf.get("edit") else [])
    return edits if isinstance(edits, list) else [edits]


def _apply_edit(path: Path, find: str, replace: str) -> None:
    """Replace the one occurrence of `find` in `path`. Line endings in the file are kept:
    the text is matched with LF endings and written back in the file's own style."""
    raw = read_keep(path)
    crlf = "\r\n" in raw
    text = raw.replace("\r\n", "\n")
    find, replace = find.replace("\r\n", "\n"), replace.replace("\r\n", "\n")
    count = text.count(find)
    if count != 1:
        where = "not found" if count == 0 else f"found {count} times"
        raise VerikitError(f"edit of {path.name}: the `find` text was {where}; it must match exactly once. "
                           "The code moved: update `find` to the current text of the guard.", FAILED)
    text = text.replace(find, replace)
    write_keep(path, text.replace("\n", "\r\n") if crlf else text)


class AppliedVariant:
    """Context manager: apply a broken variant (a patch file, or find-and-replace edits), prove
    it changed code, and always put the files back. The touched files are backed up byte for
    byte first; the revert restores those bytes, also after an interrupted run."""

    def __init__(self, root: Path, mf: dict):
        self.root = root
        self.rel = mf.get("patch") or "edits to " + ", ".join(sorted({e["file"] for e in variant_edits(mf)}))
        self.patch = (root / mf["patch"]) if mf.get("patch") else None
        self.edits = variant_edits(mf)
        self.top, self.args = _patch_args(root, self.patch) if self.patch else (root, [])
        self.files: list[Path] = []
        self.saved: list[dict] = []
        self.before: dict[str, str] = {}

    def __enter__(self):
        if self.patch:
            c, o = git(self.top, "apply", "--check", *self.args)
            if c != 0:
                raise VerikitError(f"patch {self.rel} does not apply: {o}", FAILED)
            self.files = _patched_files(self.root, self.patch)
        else:
            self.files = sorted({self.root / e["file"] for e in self.edits})
            missing = [f.name for f in self.files if not f.is_file()]
            if missing:
                raise VerikitError(f"edit target not found: {', '.join(missing)}", FAILED)
        self.before = _digest(self.files)
        backups = state_path(self.root, BACKUP_DIR)
        shutil.rmtree(backups, ignore_errors=True)
        backups.mkdir()
        self.saved = []
        for i, f in enumerate(self.files):
            name = None
            if f.exists():
                name = f"{i}.orig"
                (backups / name).write_bytes(f.read_bytes())
            self.saved.append({"path": str(f), "backup": name})
        state_path(self.root, PENDING).write_text(json.dumps(
            {"rel": self.rel, "at": time.time(), "files": self.saved}), encoding="utf-8")
        try:
            if self.patch:
                c, o = git(self.top, "apply", *self.args)
                if c != 0:
                    raise VerikitError(f"patch {self.rel} does not apply: {o}", FAILED)
            else:
                for e in self.edits:
                    _apply_edit(self.root / e["file"], e["find"], e["replace"])
        except VerikitError:
            self._revert()
            raise
        if _digest(self.files) == self.before:
            self._revert()
            raise VerikitError(f"{self.rel} changed no file" + ("; check the patch's paths (they must be "
                               "relative to the folder that holds verification/)" if self.patch else
                               "; `find` and `replace` are the same text"), FAILED)
        return self

    def _revert(self) -> None:
        if _digest(self.files) != self.before:
            bad = _restore(self.root, self.saved)
            if bad or _digest(self.files) != self.before:
                die(f"could not revert {self.rel}: {', '.join(bad) or 'files differ after restore'}. "
                    f"Backups are in {self.root / STATE_DIR / BACKUP_DIR}; restore the files by hand!")
        _clear_pending(self.root)

    def __exit__(self, *exc):
        self._revert()
        return False


# --------------------------------------------------------------------------- run

# --------------------------------------------------------------------------- isolated copies

def _is_link(path: Path) -> bool:
    """A symlink, or on Windows any reparse point (a junction is not a symlink to Python 3.11)."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return os.path.islink(path) or bool(getattr(st, "st_file_attributes", 0) & 0x400)


def _link_dir(target: Path, link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))      # needs no admin rights, unlike symlinks
    else:
        os.symlink(target, link, target_is_directory=True)


def _unlink_dir(link: Path) -> None:
    """Remove the link itself. Never follows it: the real folder stays as it is."""
    if os.name == "nt":
        os.rmdir(link)
    else:
        os.unlink(link)


class IsolatedCopy:
    """A throwaway copy of the repository in which broken variants are applied and run, so the
    developer's (or agent's) working files are never edited, not even for a moment.

    It holds every file git tracks or would track, as it is on disk now. Dependency folders
    that git ignores (node_modules, ...) are not copied: the manifest's `[isolation] link`
    names the ones to link in, and `setup` can install into the copy instead."""

    def __init__(self, root: Path, manifest: dict):
        cfg = manifest.get("isolation", {})
        top = git_toplevel(root) or root
        self.tmp = Path(tempfile.mkdtemp(prefix="verikit-iso-"))
        self.top = self.tmp / "repo"
        self.root = self.top / root.resolve().relative_to(top)
        self.links: list[Path] = []
        try:
            p = subprocess.run(["git", "ls-files", "-z", "-co", "--exclude-standard", "--", *cfg.get("include", [])],
                               cwd=top, capture_output=True)
            if p.returncode != 0:
                die(f"--isolated needs a git repository: {p.stderr.decode('utf-8', 'replace').strip()}")
            for rel in p.stdout.decode("utf-8", "replace").split("\0"):
                src = top / rel
                if rel and src.is_file():
                    dest = self.top / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
            self.root.mkdir(parents=True, exist_ok=True)
            for rel in cfg.get("link", []):
                target = (root / rel).resolve()
                if not target.is_dir():
                    die(f"[isolation] link: {rel} is not a folder in {root} (install dependencies first)")
                _link_dir(target, self.root / rel)
                self.links.append(self.root / rel)
            if cfg.get("setup"):
                code, out, _ = run_shell(cfg["setup"], self.root, int(cfg.get("setup_timeout", 1800)))
                if code != 0:
                    die(f"[isolation] setup failed in the copy (exit {code}):\n{tail(out)}")
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """Delete the copy. Links are removed first, one by one, and the tree is deleted only
        once no link is left in it, so deleting the copy can never reach a real folder."""
        for link in self.links:
            try:
                if _is_link(link):
                    _unlink_dir(link)
            except OSError as e:
                warn(f"could not remove link {link}: {e}")
        left = [Path(d) / n for d, dirs, files in os.walk(self.tmp) for n in (*dirs, *files) if _is_link(Path(d) / n)]
        if left:
            warn(f"left {self.tmp} in place: it still contains a link ({left[0]}); remove it by hand")
            return

        def writable(func, path, _exc):
            try:
                os.chmod(path, 0o700)
                func(path)
            except OSError:
                pass
        if sys.version_info >= (3, 12):
            shutil.rmtree(self.tmp, onexc=writable)
        else:
            shutil.rmtree(self.tmp, onerror=writable)


SEEDS_DIR = Path("verification") / "seeds"


def saved_seeds(root: Path, check_id: str) -> list[int]:
    """Seeds of cases that broke the rule once (verification/seeds/<id>.txt, one per line,
    `#` comments). `run` replays each of them every time, so a found bug cannot come back."""
    try:
        lines = (root / SEEDS_DIR / f"{check_id}.txt").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    seeds = []
    for line in lines:
        token = line.split("#")[0].strip()
        if token.lstrip("-").isdigit() and int(token) not in seeds:
            seeds.append(int(token))
    return seeds


def cmd_seed(args) -> int:
    root = find_root(args.root)
    m = load_manifest(root)
    if args.action == "list":
        for c in select(m["check"], [args.id] if args.id else []):
            print(f"{c['id']}: {', '.join(map(str, saved_seeds(root, c['id']))) or 'no saved seeds'}")
        return OK
    if not args.id or args.seed is None:
        die("usage: verikit seed add ID SEED [--note TEXT]")
    select(m["check"], [args.id])
    if args.seed in saved_seeds(root, args.id):
        print(f"seed {args.seed} is already saved for {args.id}")
        return OK
    path = root / SEEDS_DIR / f"{args.id}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = read_keep(path) if path.exists() else \
        f"# Seeds of cases that once broke check {args.id}. `verikit run` replays every one, every time.\n"
    note = f"  # {args.note}" if args.note else ""
    write_keep(path, existing + ("" if existing.endswith("\n") else "\n") + f"{args.seed}{note}\n")
    print(f"saved seed {args.seed} in {path.relative_to(root).as_posix()}; commit it")
    return OK


def run_check(root: Path, check: dict, timeout: int, verbose: bool, seed: int | None,
              variant_root: Path | None = None, emit=print, main_lock=None) -> dict:
    """Run one check: it must hold on the current code (and for every saved seed), and every
    broken variant must fail with its message. With `variant_root` (an isolated copy) the
    variants are applied and run there, so the real working files are never edited.
    `main_lock` serialises the runs in the real tree when several checks run in parallel."""
    timeout = int(check.get("timeout", timeout))
    result = {"id": check["id"], "rule": check["rule"], "ok": True, "steps": []}
    emit(f"\n== {check['id']}  [{check.get('level', '?')} | {check.get('tool', '?')}]")
    emit(f"   rule: {check['rule']}")

    def holds() -> dict[str, str] | None:
        try:
            main_cmd, src_env = with_sources(root, check, check["run"])
        except VerikitError as e:
            emit(f"   [FAIL] holds on the current code: {e}")
            result["steps"].append({"what": "holds on the current code", "ok": False, "exit": None, "output": str(e)})
            result["ok"] = False
            return None
        code, out, secs = run_shell(main_cmd, root, timeout, seed, src_env)
        passed = code == 0
        emit(f"   {'[ok]  ' if passed else '[FAIL]'} holds on the current code ({secs:.1f}s, exit {code})")
        if not passed or verbose:
            emit(tail(out))
        if not passed and seed is not None:
            emit(f"          to replay this case on every future run (if the test reads VERIKIT_SEED): "
                  f"{self_cmd(root)} seed add {check['id']} {seed}")
        result["steps"].append({"what": "holds on the current code", "ok": passed, "exit": code,
                                "seconds": round(secs, 2), "output": tail(out, 60)})
        result["ok"] &= passed

        for saved in saved_seeds(root, check["id"]):   # cases that failed once: replayed forever
            if saved == seed:
                continue
            code, out, secs = run_shell(main_cmd, root, timeout, saved, src_env)
            ok = code == 0
            emit(f"   {'[ok]  ' if ok else '[FAIL]'} holds with saved seed {saved} ({secs:.1f}s, exit {code})")
            if not ok or verbose:
                emit(tail(out))
            result["steps"].append({"what": f"holds with saved seed {saved}", "ok": ok, "exit": code,
                                    "seconds": round(secs, 2), "output": tail(out, 60)})
            result["ok"] &= ok
        return src_env

    if main_lock:
        with main_lock:
            src_env = holds()
    else:
        src_env = holds()
    if src_env is None:
        return result
    vroot = variant_root or root   # where broken variants are applied and run

    for mf in check["must_fail"]:
        why = mf.get("why", "broken variant")
        try:
            cmd, _ = with_sources(root, check, mf.get("run", check["run"]))
            if mf.get("patch") or variant_edits(mf):
                with AppliedVariant(vroot, mf):
                    code, out, secs = run_shell(cmd, vroot, timeout, seed, src_env)
            else:
                code, out, secs = run_shell(cmd, vroot, timeout, seed, src_env)
        except VerikitError as e:
            if e.code == ERROR:
                raise
            emit(f"   [FAIL] must fail: {why}\n          {e}")
            result["steps"].append({"what": f"must fail: {why}", "ok": False, "exit": None, "output": str(e)})
            result["ok"] = False
            continue
        matched = bool(re.search(mf["match"], out))
        caught = code not in (0, 124) and matched
        reason = "" if caught else (" - it PASSED, so this check cannot see that break" if code == 0
                                    else " - it timed out" if code == 124
                                    else " - it failed, but not with the expected message")
        emit(f"   {'[ok]  ' if caught else '[FAIL]'} must fail: {why} ({secs:.1f}s, exit {code}){reason}")
        if not caught or verbose:
            emit(tail(out))
        result["steps"].append({"what": f"must fail: {why}", "ok": caught, "exit": code,
                                "seconds": round(secs, 2), "output": tail(out, 60)})
        result["ok"] &= caught
    return result


def run_isolated(root: Path, manifest: dict, checks: list[dict], timeout: int, verbose: bool,
                 seed: int, jobs: int) -> list[dict]:
    """Run checks with their broken variants in throwaway copies of the repository. "Holds on
    the current code" still runs in the real tree, one check at a time; variants of different
    checks run side by side, one copy per job. Each check's output is printed when it is done."""
    import queue
    import threading
    from concurrent.futures import ThreadPoolExecutor
    jobs = max(1, min(jobs, len(checks) or 1))
    copies: list[IsolatedCopy] = []
    free: queue.SimpleQueue = queue.SimpleQueue()
    make_lock, main_lock, print_lock = threading.Lock(), threading.Lock(), threading.Lock()

    def a_copy() -> IsolatedCopy:
        try:
            return free.get_nowait()
        except queue.Empty:
            with make_lock:
                copies.append(IsolatedCopy(root, manifest))
                return copies[-1]

    def work(check: dict) -> dict:
        lines: list[str] = []
        iso = a_copy() if check["must_fail"] else None
        try:
            return run_check(root, check, timeout, verbose, seed, variant_root=iso.root if iso else None,
                             emit=lines.append, main_lock=main_lock)
        finally:
            if iso:
                free.put(iso)
            with print_lock:
                print("\n".join(lines), flush=True)

    print(f"[verikit] isolated: broken variants run in a copy of the repository, not in your working "
          f"files ({jobs} job{'s' if jobs > 1 else ''})")
    try:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            return list(pool.map(work, checks))
    finally:
        for c in copies:
            c.close()


def changed_checks(root: Path, checks: list[dict], base: str) -> list[dict]:
    """Checks whose covered files (or any verification file) changed. With `base`, against
    base...HEAD plus uncommitted edits (for PRs); without, against each check's sync record."""
    _, dirty_v = git(root, "status", "--porcelain", "--", "verification")
    v_changed = bool(dirty_v.strip())
    if base and not v_changed:
        code, out = git(root, "diff", "--name-only", f"{base}...HEAD", "--", "verification")
        if code != 0:
            die(f"git diff against {base!r} failed: {out}")
        v_changed = bool(out.strip())
    picked = []
    for c in checks:
        if v_changed:
            picked.append(c)
            continue
        if base:
            _, out = git(root, "diff", "--name-only", f"{base}...HEAD", "--", *pathspecs(c))
            _, dirty = git(root, "status", "--porcelain", "--", *pathspecs(c))
            # other repositories have no PR base here: compare them with their sync record
            other = [f for f in stale_info(root, c)[1] if _source_of(f, c)[0]] if source_globs(c) else []
            if out.strip() or dirty.strip() or other:
                picked.append(c)
        else:
            status, files = stale_info(root, c)
            if status != "synced" and not (files and all(f.startswith(MISSING) for f in files)):
                picked.append(c)
    return picked


def write_junit(path: str, results: list[dict], total_secs: float) -> None:
    cases = [(r, s) for r in results for s in r["steps"]]
    failures = sum(1 for _, s in cases if not s["ok"])
    suites = ET.Element("testsuites", name="verikit", tests=str(len(cases)), failures=str(failures),
                        time=f"{total_secs:.2f}")
    suite = ET.SubElement(suites, "testsuite", name="verikit", tests=str(len(cases)),
                          failures=str(failures), time=f"{total_secs:.2f}")
    for r, s in cases:
        case = ET.SubElement(suite, "testcase", classname=f"verikit.{r['id']}",
                             name=f"{s['what']} | {r['rule']}", time=str(s.get("seconds", 0)))
        if not s["ok"]:
            fail = ET.SubElement(case, "failure", message=f"{r['id']}: {s['what']} (exit {s['exit']})")
            fail.text = s.get("output", "")
    Path(path).write_text(ET.tostring(suites, encoding="unicode"), encoding="utf-8")


def ci_target_branch(env=None) -> tuple[str | None, str]:
    """(target branch of the pull request, CI name), or (None, why) when this build is not a
    pull request. Reads the variables GitHub Actions, Azure Pipelines and GitLab CI set."""
    env = os.environ if env is None else env
    if env.get("GITHUB_ACTIONS"):
        if env.get("GITHUB_EVENT_NAME", "").startswith("pull_request") and env.get("GITHUB_BASE_REF"):
            return env["GITHUB_BASE_REF"], "GitHub Actions pull request"
        return None, "GitHub Actions, not a pull request"
    if env.get("TF_BUILD") or env.get("BUILD_REASON"):
        # Azure Repos gives refs/heads/main, GitHub-hosted repos give main
        target = env.get("SYSTEM_PULLREQUEST_TARGETBRANCHNAME") or env.get("SYSTEM_PULLREQUEST_TARGETBRANCH", "")
        if env.get("BUILD_REASON") == "PullRequest" and target:
            return target.removeprefix("refs/heads/"), "Azure Pipelines pull request"
        return None, "Azure Pipelines, not a pull request"
    if env.get("CI_MERGE_REQUEST_TARGET_BRANCH_NAME"):
        return env["CI_MERGE_REQUEST_TARGET_BRANCH_NAME"], "GitLab merge request"
    return None, "no pull request detected"


def ci_base(root: Path, env=None) -> str | None:
    """The ref a PR build should diff against, or None to run every check. Never returns a
    ref that cannot be diffed: a missing or shallow base means 'run everything', not 'run nothing'."""
    branch, where = ci_target_branch(env)
    if not branch:
        print(f"[verikit] --ci: {where}; running every check")
        return None
    base = f"origin/{branch}"
    if git(root, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}")[0] != 0:
        try:  # never prompt for credentials in CI; give up quickly instead
            subprocess.run(["git", "fetch", "--no-tags", "--quiet", "origin",
                            f"+refs/heads/{branch}:refs/remotes/{base}"], cwd=root, capture_output=True,
                           timeout=120, env=dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never"))
        except (OSError, subprocess.TimeoutExpired):
            pass
    if git(root, "merge-base", base, "HEAD")[0] != 0:
        warn(f"--ci: {where} into {branch}, but {base} is missing or shares no history with HEAD "
             "(shallow checkout? use fetch-depth 0 / fetchDepth: 0). Running every check instead.")
        return None
    print(f"[verikit] --ci: {where} into {branch}; running the checks whose files changed vs {base}")
    return base


DRIFT_WORDS = {"synced": "up to date", "stale": "OUT OF DATE: covered code changed since the last sync",
               "never-synced": "NEVER CONFIRMED: no sync record", "unknown": "UNKNOWN: cannot be compared here"}


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def summary_markdown(root: Path, all_checks: list[dict], results: list[dict], seed: int) -> str:
    """What a reviewer needs from one run, as Markdown for the pull request or build page.
    A check that passes while its sync record is out of date is never shown as plain green."""
    by_id = {r["id"]: r for r in results}
    drift = {c["id"]: stale_info(root, c) for c in all_checks}
    failed = [r for r in results if not r["ok"]]
    out_of_date = [c for c in all_checks if drift[c["id"]][0] != "synced"]
    if failed:
        headline = f"{len(failed)} of {len(results)} checked promises FAILED"
    elif not results:
        headline = "no check covers the changed files; nothing was run"
    else:
        headline = f"all {len(results)} checked promises hold"
    if out_of_date:
        headline += f"; {len(out_of_date)} check(s) are not confirmed against the current code"
    lines = [f"## verikit: {headline}", "",
             "| Promise | Check | This run | Sync record |", "|---|---|---|---|"]
    for c in all_checks:
        r = by_id.get(c["id"])
        status, _ = drift[c["id"]]
        if r is None:
            ran = "not run (its files did not change)"
        elif r["ok"]:
            caught = sum(1 for s in r["steps"] if s["what"].startswith("must fail"))
            ran = f"✅ holds; {caught} broken variant(s) caught" if status == "synced" else \
                f"⚠️ holds, but see sync record; {caught} broken variant(s) caught"
        else:
            ran = "❌ FAILED: " + "; ".join(s["what"] for s in r["steps"] if not s["ok"])
        lines.append(f"| {_cell(c['rule'])} | `{c['id']}` ({_cell(c.get('level', '?'))}, {_cell(c.get('tool', '?'))}) "
                     f"| {_cell(ran)} | {'✅' if status == 'synced' else '⚠️'} {DRIFT_WORDS[status]} |")
    for r in failed:
        c = next(x for x in all_checks if x["id"] == r["id"])
        lines += ["", f"### ❌ `{r['id']}`: {c['rule']}"]
        if c.get("harm"):
            lines.append(f"Harm if this ships: {c['harm']}")
        for s in r["steps"]:
            if not s["ok"]:
                lines += ["", f"**{s['what']}** (exit {s.get('exit')})", "", "```",
                          *[ln.replace("      | ", "", 1) for ln in (s.get("output") or "").splitlines()[-40:]], "```"]
        lines += ["", f"Replay: `{self_cmd(root)} run {r['id']} --seed {seed}`"]
    if out_of_date:
        lines += ["", "### ⚠️ Not confirmed against the current code", ""]
        for c in out_of_date:
            status, files = drift[c["id"]]
            shown = ", ".join(f"`{f}`" for f in files[:6]) + (" ..." if len(files) > 6 else "")
            lines.append(f"- `{c['id']}`: {DRIFT_WORDS[status]}" + (f" ({shown})" if files else ""))
        lines += ["", "A check can pass while describing code that no longer exists. Re-read the changes against "
                      f"the model (verify-maintain), then `{self_cmd(root)} sync <id>`."]
    lines += ["", f"<sub>verikit {VERSION}, seed {seed}</sub>", ""]
    return "\n".join(lines)


def publish_summary(root: Path, text: str, path: str | None, ci: bool) -> None:
    """Write the run summary where people will see it: a file, GitHub's job summary, or the
    Extensions tab of an Azure Pipelines run."""
    if path:
        Path(path).write_text(text, encoding="utf-8")
    if not ci:
        return
    if os.environ.get("GITHUB_ACTIONS") and os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")
    elif os.environ.get("TF_BUILD"):
        target = Path(path).resolve() if path else (root / "verify-summary.md").resolve()
        if not path:
            target.write_text(text, encoding="utf-8")
        print(f"##vso[task.uploadsummary]{target}")   # Azure logging command: needs an absolute path


def cmd_report(args) -> int:
    """Every promise this repo verifies and where each one stands, without running anything."""
    root = find_root(args.root)
    m = load_manifest(root)
    state = read_state(root)
    rows = []
    for c in select(m["check"], [], args.tag):
        status, files = stale_info(root, c)
        last = state.get(c["id"], {})
        fresh = last.get("fingerprint") == fingerprint(root, c)
        rows.append({"rule": c["rule"], "harm": c.get("harm", ""), "id": c["id"], "level": c.get("level", "?"),
                     "tool": c.get("tool", "?"), "bounds": c.get("bounds", ""), "variants": len(c["must_fail"]),
                     "status": status, "changed": files,
                     "last": f"passed {last['at']}" if fresh and last else "not run on the current content"})
    rules = list(dict.fromkeys(r["rule"] for r in rows))
    title = f"Verified promises: {root.name}"
    attention = sum(1 for r in rows if r["status"] != "synced")
    intro = (f"{len(rules)} promise(s), {len(rows)} check(s); {attention} check(s) need attention."
             if attention else f"{len(rules)} promise(s), {len(rows)} check(s); every check is confirmed "
                               "against the current code.")
    if args.html:
        import html
        esc = html.escape
        body = [f"<h1>{esc(title)}</h1><p>{esc(intro)}</p>"]
        for rule in rules:
            mine = [r for r in rows if r["rule"] == rule]
            body.append(f"<h2>{esc(rule)}</h2>" + (f"<p class=harm>If it breaks: {esc(mine[0]['harm'])}</p>" if mine[0]["harm"] else ""))
            body.append("<table><tr><th>Check</th><th>Level and tool</th><th>Scope checked</th>"
                        "<th>Broken variants</th><th>Sync record</th><th>Last run on this machine</th></tr>")
            for r in mine:
                cls = "ok" if r["status"] == "synced" else "warn"
                body.append(f"<tr><td><code>{esc(r['id'])}</code></td><td>{esc(r['level'])}: {esc(r['tool'])}</td>"
                            f"<td>{esc(r['bounds'])}</td><td>{r['variants']}</td>"
                            f"<td class={cls}>{esc(DRIFT_WORDS[r['status']])}</td><td>{esc(r['last'])}</td></tr>")
            body.append("</table>")
        page = ("<!doctype html><html lang=en><meta charset=utf-8><title>" + esc(title) + "</title><style>"
                "body{font:15px/1.5 system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#1b1f24}"
                "table{border-collapse:collapse;width:100%;margin:.5rem 0 1.5rem}th,td{border:1px solid #d0d7de;"
                "padding:.4rem .6rem;text-align:left;vertical-align:top}th{background:#f6f8fa}"
                ".ok{color:#1a7f37}.warn{color:#9a6700;font-weight:600}.harm{color:#57606a}"
                "</style>" + "".join(body) + f"<p><small>verikit {VERSION}</small></p></html>")
        Path(args.html).write_text(page, encoding="utf-8")
        print(f"wrote {args.html}")
    lines = [f"# {title}", "", intro, ""]
    for rule in rules:
        mine = [r for r in rows if r["rule"] == rule]
        lines += [f"## {rule}", ""] + ([f"If it breaks: {mine[0]['harm']}", ""] if mine[0]["harm"] else [])
        lines += ["| Check | Level and tool | Scope checked | Broken variants | Sync record | Last run on this machine |",
                  "|---|---|---|---|---|---|"]
        for r in mine:
            mark = "✅" if r["status"] == "synced" else "⚠️"
            lines.append(f"| `{r['id']}` | {_cell(r['level'])}: {_cell(r['tool'])} | {_cell(r['bounds'])} | "
                         f"{r['variants']} | {mark} {DRIFT_WORDS[r['status']]} | {r['last']} |")
        lines.append("")
    text = "\n".join(lines)
    if args.md:
        Path(args.md).write_text(text, encoding="utf-8")
        print(f"wrote {args.md}")
    if not args.md and not args.html:
        print(text)
    return OK


def cmd_run(args) -> int:
    root = find_root(args.root)
    recover_pending(root)
    m = load_manifest(root)
    checks = select(m["check"], args.ids, args.tag)
    if args.ci:
        if args.changed is not None:
            die("use --ci or --changed, not both")
        base = ci_base(root)
        args.changed = base  # None = not a PR (or no usable base): run everything
    if args.changed is not None:
        checks = changed_checks(root, checks, args.changed)
        if not checks:
            print("no check covers a changed file - nothing to run")
    elif not checks:
        die("no checks match. Use the verify-create skill to add one.")
    seed = args.seed if args.seed is not None else random.randrange(1, 2**31)
    print(f"verikit {VERSION} | root {root} | seed {seed} (rerun with --seed {seed}; "
          "property tests can read it from VERIKIT_SEED)")
    state = read_state(root)
    t0 = time.time()
    if args.isolated or args.jobs > 1:
        results = run_isolated(root, m, checks, args.timeout, args.verbose, seed, args.jobs)
    else:
        results = [run_check(root, c, args.timeout, args.verbose, seed) for c in checks]
    for c, r in zip(checks, results):
        if r["ok"]:
            state[c["id"]] = {"fingerprint": fingerprint(root, c), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        else:
            state.pop(c["id"], None)
    write_state(root, state)
    bad = [r["id"] for r in results if not r["ok"]]
    print("\n" + "-" * 72)
    print(f"{len(results) - len(bad)}/{len(results)} checks OK" + (f"; failing: {', '.join(bad)}" if bad else ""))
    if args.json:
        Path(args.json).write_text(json.dumps({"seed": seed, "results": results}, indent=2), encoding="utf-8")
    if args.junit:
        write_junit(args.junit, results, time.time() - t0)
    if args.summary or args.ci:
        publish_summary(root, summary_markdown(root, m["check"], results, seed), args.summary, args.ci)
    return FAILED if bad else OK


# --------------------------------------------------------------------------- stale / sync / list

def _legacy_stale(root: Path, check: dict) -> tuple[str, list[str]]:
    """A 0.3 manifest: `synced_commit` names a commit. It only works while that commit is
    reachable, which a squash merge, a rebase or a shallow clone breaks."""
    if source_globs(check):
        return "never-synced", []
    specs = pathspecs(check)
    sync = check["synced_commit"]
    hint = f"run `{self_cmd(root)} sync {check['id']}` to record content instead of a commit"
    code, out = git(root, "diff", "--name-only", sync, "--", *specs)
    if code != 0:
        return "unknown", [f"(recorded by verikit 0.3 as commit {sync}, which is not in this history; {hint})"]
    _, untracked = git(root, "ls-files", "--others", "--exclude-standard", "--", *specs)
    changed = sorted({f for f in (out + "\n" + untracked).splitlines() if f.strip()})
    return ("stale" if changed else "synced"), changed


def stale_info(root: Path, check: dict) -> tuple[str, list[str]]:
    """(status, what differs). status: synced | stale | never-synced | unknown.
    Compares today's content with the content recorded by the last `sync`; it needs no git
    history. Files in other repositories are shown as `name:path`."""
    rec = read_sync(root, check["id"])
    if rec is None:
        return _legacy_stale(root, check) if check.get("synced_commit") else ("never-synced", [])
    items, notes = current_items(root, check)
    absent = [name for name in source_globs(check) if source_dir(root, check, name) is None]
    if "files" in rec:
        old = {k: v for k, v in rec["files"].items() if not any(k.startswith(n + ":") for n in absent)}
        changed = sorted(f"{k} {NO_ANCHOR}: renamed or moved?" if items.get(k) == NO_ANCHOR else k
                         for k in old.keys() | items.keys() if old.get(k) != items.get(k))
    elif absent:
        changed = []
    else:
        changed = [] if rec["hash"] == items_hash(items) else ["(covered files differ from the recorded content)"]
    status = "unknown" if notes else ("stale" if changed else "synced")
    return status, [*changed, *notes]


def self_cmd(root: Path) -> str:
    me = Path(__file__).resolve()
    try:
        return f"python {me.relative_to(root).as_posix()}"
    except ValueError:
        return f"python {me}"


def cmd_stale(args) -> int:
    root = find_root(args.root)
    recover_pending(root)
    m = load_manifest(root)
    any_stale = False
    for c in select(m["check"], args.ids, args.tag):
        status, files = stale_info(root, c)
        any_stale |= status != "synced"
        print(f"{status:13} {c['id']}")
        for f in files:
            print(f"              changed: {f}")
        base = sync_base(root, c["id"]) if status == "stale" else ""
        if base:
            print(f"              review:  git log -p {base}..HEAD -- <the files above>   "
                  f"({base} last recorded this check)")
    if any_stale:
        print("\nA stale check may still pass while describing code that no longer exists.\n"
              "Re-read the changes (verify-maintain skill), update the model if needed, then "
              f"`{self_cmd(root)} sync <id>`.")
    return FAILED if (any_stale and args.strict) else OK


def cmd_list(args) -> int:
    root = find_root(args.root)
    recover_pending(root)
    m = load_manifest(root)
    state = read_state(root)
    for c in select(m["check"], [], args.tag):
        status, _ = stale_info(root, c)
        last = state.get(c["id"], {})
        fresh = last.get("fingerprint") == fingerprint(root, c)
        print(f"- {c['id']}  [{c.get('level', '?')} | {c.get('tool', '?')}]" + (f"  tags: {', '.join(c['tags'])}" if c["tags"] else ""))
        print(f"    rule:    {c['rule']}")
        for key in ("harm", "bounds"):
            if c.get(key):
                print(f"    {key + ':':<8} {c[key]}")
        print(f"    covers:  {', '.join(c['covers'])}")
        if c["harness"]:
            print(f"    harness: {', '.join(c['harness'])}")
        for name in source_globs(c):
            src = source_dir(root, c, name)
            print(f"    source:  {name} = {src or c['_sources'][name]['path'] + ' (NOT checked out)'}")
        print(f"    run:     {c['run']}")
        print(f"    variants:{len(c['must_fail']):>2} must_fail")
        print(f"    sync:    {status};  last local pass: "
              f"{last.get('at', 'never')}{'' if fresh or not last else ' (code changed since)'}")
    return OK


def cmd_sync(args) -> int:
    """Re-run each check on the content that is on disk now and record that content as
    confirmed (verification/sync/<id>.json). Commit the record with the change it confirms."""
    root = find_root(args.root)
    recover_pending(root)
    m = load_manifest(root)
    chosen = select(m["check"], args.ids, args.tag)
    if args.tag and not chosen:
        die(f"no check has the tag {', '.join(args.tag)}, so nothing was synced")
    for c in chosen:
        for name in source_globs(c):
            if source_dir(root, c, name) is None:
                die(f"{c['id']}: it covers files in {name!r}, but there is no git checkout at "
                    f"{c['_sources'][name]['path']}. Clone it there or set {source_env(name)}.")
        before, _ = current_items(root, c)
        lost = [k for k, v in before.items() if v == NO_ANCHOR]
        if lost:
            die(f"{c['id']}: no declaration found for {', '.join(lost)}. Fix the name in covers/harness "
                "(or mark the region with `verikit:begin NAME` / `verikit:end NAME` comments) before syncing.")
        r = run_check(root, c, args.timeout, False, args.seed)
        if not r["ok"]:
            die(f"{c['id']} does not pass, so its sync record is unchanged", FAILED)
        items, _ = current_items(root, c)
        if items != before:
            die(f"{c['id']}: covered files changed while the check was running; run sync again")
        path = write_sync(root, c, items)
        state = read_state(root)  # this rerun passed: record it, so `list` and the hook see it
        state[c["id"]] = {"fingerprint": fingerprint(root, c), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        write_state(root, state)
        print(f"   synced {c['id']}. Commit {path.relative_to(root).as_posix()} together with the change it confirms.")
    return OK


# --------------------------------------------------------------------------- Claude Code Stop hook

def manifest_roots(project: Path, depth: int = 5) -> list[Path]:
    """Verification roots for a project: the nearest one above, plus any below (monorepos)."""
    roots = []
    up = find_root(project)
    if (up / MANIFEST_REL).is_file():
        roots.append(up)
    base_depth = len(project.parts)
    for dirpath, dirnames, filenames in os.walk(project):
        d = Path(dirpath)
        dirnames[:] = [n for n in dirnames if n not in SKIP_DIRS and not n.startswith(".")]
        if len(d.parts) - base_depth >= depth:
            dirnames[:] = []
        if d.name == "verification" and "verification.toml" in filenames and d.parent not in roots:
            roots.append(d.parent)
    return roots


SESSIONS = "sessions"


def _where(root: Path, project: Path) -> str:
    try:
        rel = root.relative_to(project).as_posix()
        return "" if rel == "." else f" (in {rel})"
    except ValueError:
        return f" (in {root})"


def _drifted(status: str, changed: list[str]) -> bool:
    """Out of date in a way someone can act on. A repository that is simply not checked out
    on this machine does not count."""
    if status == "unknown":
        return any(not f.startswith(MISSING) for f in changed)
    return status != "synced"


def _hook_session_start(project: Path, session: str) -> int:
    """Remember what every check's content looked like when the session began, so the Stop
    hook can tell this session's edits from drift that was already there. Tell the agent once
    about checks that were out of date before it started (stdout becomes session context)."""
    lines: list[str] = []
    for root in manifest_roots(project):
        try:
            recover_pending(root)
            m = load_manifest(root)
        except VerikitError:
            continue
        if not m["check"] or not session:
            continue
        folder = state_path(root, SESSIONS)
        folder.mkdir(exist_ok=True)
        path = folder / f"{session}.json"
        if path.exists():       # resume or compact: keep the first baseline, say nothing twice
            continue
        for old in folder.glob("*.json"):
            if time.time() - old.stat().st_mtime > 14 * 86400:
                old.unlink(missing_ok=True)
        path.write_text(json.dumps({c["id"]: fingerprint(root, c) for c in m["check"]}), encoding="utf-8")
        stale = [(c, status) for c in m["check"] for status, changed in [stale_info(root, c)]
                 if _drifted(status, changed)]
        if stale:
            lines.append(f"verikit{_where(root, project)}: these machine-checked promises were already out "
                         "of date before this session started (not your edits):")
            lines += [f"  - {c['id']} ({status}): \"{c['rule']}\"" for c, status in stale]
            lines.append("If your task touches their code, follow the verify-maintain skill "
                         f"(`{self_cmd(root)} stale` lists what changed). Otherwise leave them and "
                         "mention them in your report.")
    if lines:
        print("\n".join(lines))
    return OK


def _hook_stop(project: Path, session: str) -> int:
    lines: list[str] = []
    for root in manifest_roots(project):
        try:
            recover_pending(root)
            m = load_manifest(root)
        except VerikitError as e:
            lines.append(f"verikit ({root}): {e}")
            continue
        state = read_state(root)
        try:
            baseline = json.loads((root / STATE_DIR / SESSIONS / f"{session}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            baseline = None
        unchecked, unconfirmed = [], []
        for c in m["check"]:
            fp = fingerprint(root, c)
            status, changed = stale_info(root, c)
            if baseline is not None:
                touched = baseline.get(c["id"]) != fp      # this session changed what the check reads
            else:
                touched = status != "never-synced" and _drifted(status, changed)   # cannot tell whose edit
            if not touched or status == "synced":
                continue
            entry = state.get(c["id"], {})
            if entry.get("fingerprint") != fp:
                unchecked.append(c)
            elif entry.get("stale_warned") != fp:
                unconfirmed.append(c)
                state[c["id"]] = {**entry, "stale_warned": fp}
        if unconfirmed:
            write_state(root, state)
        where = _where(root, project)
        if unchecked:
            if baseline is not None:
                lines.append(f"This session changed code that a formal check covers{where}, and no passing "
                             "run has seen these edits:")
            else:
                lines.append(f"Covered code{where} differs from what these checks last confirmed, and no "
                             "passing run on this machine has seen it (this session's edits or an earlier change):")
            for c in unchecked:
                lines.append(f"  - {c['id']}: \"{c['rule']}\"  ->  cd {root} && {self_cmd(root)} run {c['id']}")
            lines.append("Run the check(s). If one fails, report the counterexample in plain words; "
                         "do not weaken the rule or the model to make it pass.")
        if unconfirmed:
            lines.append(f"These checks pass{where}, but their sync record describes older content:")
            for c in unconfirmed:
                lines.append(f"  - {c['id']}: \"{c['rule']}\"  ->  {self_cmd(root)} sync {c['id']}")
            lines.append("A model does not see code changes. Follow the verify-maintain skill "
                         "(.claude/skills/verify-maintain/SKILL.md): read the diff against the model, update it "
                         "if the code changed shape, then sync and commit verification/sync/<id>.json with "
                         "your change. If you do not, say in your report that the model was not re-confirmed.")
    if not lines:
        return OK
    # Claude Code's documented JSON block: the agent receives `reason` and keeps working.
    # (The app shows its own "Stop hook" notice to the user whenever a Stop hook blocks.)
    print(json.dumps({"decision": "block", "reason": "\n".join(lines)}))
    return OK


def cmd_hook(args) -> int:
    """Claude Code hook for two events. SessionStart: record a baseline per session. Stop: block
    while this session's edits to covered code are unchecked; remind once when a passing check's
    sync record is older than the code. Never blocks twice in a row (stop_hook_active)."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = {}
    event = payload.get("hook_event_name") or "Stop"
    session = re.sub(r"[^\w.-]", "_", str(payload.get("session_id") or ""))[:80]
    project = Path(payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd()).resolve()
    if event == "SessionStart":
        return _hook_session_start(project, session)
    if event != "Stop" or payload.get("stop_hook_active"):
        return OK
    return _hook_stop(project, session)


# --------------------------------------------------------------------------- TLA+ (TLC)

CFG_KEYWORDS = {"SPECIFICATION", "INIT", "NEXT", "CONSTANT", "CONSTANTS", "INVARIANT", "INVARIANTS",
                "PROPERTY", "PROPERTIES", "CHECK_DEADLOCK", "SYMMETRY", "VIEW", "CONSTRAINT",
                "CONSTRAINTS", "ACTION_CONSTRAINT", "ACTION_CONSTRAINTS", "ALIAS", "POSTCONDITION"}


def transform_cfg(text: str, consts: dict[str, str], invs: list[str] | None) -> str:
    """Override CONSTANT values and/or replace the INVARIANT list of a TLC .cfg file,
    so a broken variant or witness needs no hand-copied config file."""
    out, section, seen = [], None, set()

    def const_line(body: str, indent: str) -> str | None:
        m = re.match(r"^([A-Za-z_]\w*)\s*(=|<-)\s*(.+)$", body)
        if m and m.group(1) in consts:
            seen.add(m.group(1))
            return f"{indent}{m.group(1)} = {consts[m.group(1)]}"
        return None

    for line in text.splitlines():
        body = line.split("\\*")[0].strip()
        first = body.split()[0] if body else ""
        if first in CFG_KEYWORDS:
            section = first
            if section.startswith("INVARIANT") and invs is not None:
                section = "DROPPED"
                continue
            rest = body[len(first):].strip()
            if section.startswith("CONSTANT") and rest:
                replaced = const_line(rest, "")
                if replaced:
                    out.append(f"{first} {replaced}")
                    continue
            out.append(line)
            continue
        if section == "DROPPED":
            continue
        if section and section.startswith("CONSTANT") and body:
            replaced = const_line(body, "    ")
            if replaced:
                out.append(replaced)
                continue
        out.append(line)
    missing = [k for k in consts if k not in seen]
    if missing:
        out += ["CONSTANTS", *[f"    {k} = {consts[k]}" for k in missing]]
    if invs is not None:
        out += ["INVARIANTS", *[f"    {i}" for i in invs]]
    return "\n".join(out) + "\n"


def _cache_dir() -> Path:
    d = Path(os.environ.get("VERIKIT_CACHE", Path.home() / ".cache" / "verikit"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ensure_tla_jar() -> Path:
    jar = _cache_dir() / f"tla2tools-{TLA_VERSION}.jar"
    if not jar.exists():
        print(f"[verikit] downloading TLA+ tools {TLA_VERSION} from {TLA_URL}", file=sys.stderr)
        tmp = jar.with_suffix(".part")
        with urllib.request.urlopen(TLA_URL, timeout=120) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        tmp.replace(jar)
    digest = hashlib.sha256(jar.read_bytes()).hexdigest()
    if digest != TLA_SHA256:
        jar.unlink()
        die(f"tla2tools.jar checksum mismatch ({digest}); deleted it. Expected {TLA_SHA256}.")
    return jar


def _docker_ok() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


STATE_RE = re.compile(r"^State (\d+): <([^>]*)>")


def parse_tlc_trace(out: str) -> list[tuple[str, dict[str, str]]]:
    trace: list[tuple[str, dict[str, str]]] = []
    current: dict[str, str] | None = None
    last_var = None
    for raw in out.replace("\r\n", "\n").splitlines():
        m = STATE_RE.match(raw)
        if m:
            label = m.group(2)
            action = "Initial state" if label.startswith("Initial") else label.split(" line ")[0]
            current = {}
            trace.append((action, current))
            last_var = None
            continue
        if current is None:
            continue
        mv = re.match(r"^(?:/\\ )?([A-Za-z_][A-Za-z0-9_]*) = (.*)$", raw)
        if mv:
            last_var = mv.group(1)
            current[last_var] = mv.group(2).strip()
        elif raw.strip() and last_var and raw.startswith(" "):
            current[last_var] += " " + raw.strip()
        elif not raw.strip():
            last_var = None
    return trace


def tlc_warnings(out: str) -> list[str]:
    """TLC's 'Warning:' messages, each joined into one line, de-duplicated, in order."""
    found: list[str] = []
    current: list[str] = []

    def close() -> None:
        msg = " ".join(current)
        if msg and msg not in found:
            found.append(msg)
        current.clear()

    for line in out.splitlines():
        if line.startswith("Warning:"):
            close()
            current.append(line[len("Warning:"):].strip())
        elif current and line.strip() and not line.startswith("(Use the -nowarning") \
                and not re.match(r"^(Model checking|Finished|Computing|Starting|Error:|\d+ states)", line):
            current.append(line.strip())
        else:
            close()
    close()
    return found


def print_trace(trace: list[tuple[str, dict]], heading: str) -> None:
    print(heading)
    prev: dict = {}
    for i, (action, state) in enumerate(trace, 1):
        if i == 1:
            print(f"  {i:>2}. {action}: " + ", ".join(f"{k}={v}" for k, v in state.items()))
        else:
            changes = [f"{k}: {prev.get(k)} -> {v}" for k, v in state.items() if prev.get(k) != v]
            print(f"  {i:>2}. {action:<18} " + ("; ".join(changes) or "(no change)"))
        prev = state


def cmd_tlc(args) -> int:
    spec = Path(args.spec).resolve()
    if not spec.exists():
        die(f"{spec} not found")
    cfg = Path(args.config).resolve() if args.config else spec.with_suffix(".cfg")
    if not cfg.exists():
        die(f"config {cfg} not found")
    try:
        cfg.relative_to(spec.parent)
    except ValueError:
        die("the .cfg file must live in the spec's folder (or below it)")
    tmp_cfg = None
    if args.const or args.inv:
        consts = {}
        for kv in args.const:
            k, sep, v = kv.partition("=")
            if not sep or not k.strip():
                die(f"--const expects NAME=VALUE, got {kv!r}")
            consts[k.strip()] = v.strip()
        tmp_cfg = spec.parent / f".verikit-{os.getpid()}-{random.randrange(10**6)}.cfg"
        tmp_cfg.write_text(transform_cfg(cfg.read_text(encoding="utf-8"), consts, args.inv or None), encoding="utf-8")
        cfg = tmp_cfg
    cfg_rel = cfg.relative_to(spec.parent).as_posix()
    try:
        return _run_tlc(args, spec, cfg_rel)
    finally:
        if tmp_cfg:
            tmp_cfg.unlink(missing_ok=True)


def _run_tlc(args, spec: Path, cfg_rel: str) -> int:
    jar = _ensure_tla_jar()
    tlc_args = ["tlc2.TLC", "-workers", str(args.workers), "-config", cfg_rel, spec.name]
    java = os.environ.get("VERIKIT_JAVA") or shutil.which("java")
    tmp_meta = None
    if java and not args.docker:
        tmp_meta = tempfile.mkdtemp(prefix="tlc-")
        cmd = [java, "-XX:+UseParallelGC", "-cp", str(jar), tlc_args[0], "-metadir", tmp_meta, *tlc_args[1:]]
        where = "local Java"
    elif _docker_ok():
        cmd = ["docker", "run", "--rm",
               "--mount", f"type=bind,source={spec.parent},target=/spec",
               "--mount", f"type=bind,source={jar.parent},target=/tools,readonly",
               "-w", "/spec", JAVA_IMAGE, "java", "-XX:+UseParallelGC",
               "-cp", f"/tools/{jar.name}", tlc_args[0], "-metadir", "/tmp/tlc", *tlc_args[1:]]
        where = f"Docker ({JAVA_IMAGE})"
    else:
        die("TLC needs Java 11+ on PATH (or VERIKIT_JAVA), or a running Docker daemon. "
            "Install a JDK (e.g. Temurin) or start Docker, or use `verikit explore` with a Python model.")
    shown = cfg_rel if not cfg_rel.startswith(".verikit-") else "generated config (" + \
        ", ".join([f"{c}" for c in args.const] + [f"INVARIANT {i}" for i in args.inv]) + ")"
    print(f"[verikit] TLC {TLA_VERSION} via {where}: {spec.name} with {shown}")
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=spec.parent, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=args.timeout)
        out = (p.stdout + p.stderr).replace("\r\n", "\n")
    except subprocess.TimeoutExpired:
        die(f"TLC timed out after {args.timeout}s - shrink the model's bounds")
    finally:
        if tmp_meta:
            shutil.rmtree(tmp_meta, ignore_errors=True)
    secs = time.time() - t0
    if args.raw:
        print(out)
    stats = re.search(r"(\d+) states generated, (\d+) distinct states found", out)
    depth = re.search(r"depth of the complete state graph search is (\d+)", out)
    stat_txt = f"{stats.group(2)} distinct states, depth {depth.group(1) if depth else '?'}" if stats else ""
    warnings = tlc_warnings(out)
    for w in warnings:
        print(f"[warning] {w}")
    contradiction = [w for w in warnings if "specified as UNCHANGED" in w]
    violated = re.search(r"Error: (Invariant (\w+) is violated|Deadlock reached|"
                         r"Temporal properties were violated|The following behavior)", out)
    if "Model checking completed. No error has been found." in out:
        if contradiction:
            print(f"[error] TLC found no violation, but an action contradicts itself and can never "
                  f"happen, so the pass may be vacuous. Fix the action above. ({stat_txt}, {secs:.1f}s)")
            return ERROR
        print(f"[ok] no violation - every reachable state checked ({stat_txt}, {secs:.1f}s)")
        return OK
    if violated:
        what = violated.group(2) or violated.group(1)
        trace = parse_tlc_trace(out)
        print(f"[VIOLATION] {what}  ({stat_txt or 'search stopped at first error'}, {secs:.1f}s)")
        if trace:
            kind = "Shortest counterexample" if str(args.workers) == "1" else "Counterexample (may not be shortest with several workers)"
            print_trace(trace, f"{kind} ({len(trace)} states):")
        return FAILED
    if not args.raw:
        print(out)
    print(f"[error] TLC did not finish a check (exit {p.returncode}); see output above.")
    return ERROR


# --------------------------------------------------------------------------- built-in explorer

def _freeze(state: dict):
    try:
        key = tuple(sorted(state.items()))
        hash(key)
        return key
    except TypeError:
        die("explorer states must be dicts of hashable values (int, str, bool, tuple, frozenset)")


def _fmt(v) -> str:
    if isinstance(v, frozenset):
        return "{" + ", ".join(sorted(map(_fmt, v))) + "}"
    if isinstance(v, str):
        return f'"{v}"'
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    return str(v)


def cmd_explore(args) -> int:
    path = Path(args.model).resolve()
    spec = importlib.util.spec_from_file_location("verikit_model", path)
    if not spec or not spec.loader:
        die(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    consts = getattr(mod, "CONSTANTS", {})
    for kv in args.const:
        k, _, v = kv.partition("=")
        if k not in consts:
            die(f"unknown constant {k!r}; the model defines {', '.join(consts) or 'none'}")
        try:
            consts[k] = ast.literal_eval(v)
        except (ValueError, SyntaxError):
            consts[k] = v
    invs = getattr(mod, "INVARIANTS", {})
    if args.inv:  # may also name WITNESSES: invariants that are expected to fail
        pool = {**invs, **getattr(mod, "WITNESSES", {})}
        missing = [i for i in args.inv if i not in pool]
        if missing:
            die(f"unknown invariant(s) {missing}; the model defines {', '.join(pool)}")
        invs = {k: pool[k] for k in args.inv}
    terminal = getattr(mod, "terminal", lambda s: False)
    check_deadlock = getattr(mod, "CHECK_DEADLOCK", True)
    print(f"[verikit] exploring {path.name} with {consts}; invariants: {', '.join(invs) or 'none'}")

    t0 = time.time()
    states: dict = {}
    parent: dict = {}
    queue: deque = deque()

    def violation(key, what: str) -> int:
        chain = []
        k = key
        while k is not None:
            prev, action = parent[k]
            chain.append((action, {n: _fmt(v) for n, v in states[k].items()}))
            k = prev
        chain.reverse()
        print(f"[VIOLATION] {what}  ({len(states)} distinct states seen, {time.time() - t0:.1f}s)")
        print_trace(chain, f"Shortest counterexample ({len(chain)} states):")
        return FAILED

    def visit(s: dict, prev, action: str):
        key = _freeze(s)
        if key in states:
            return None
        states[key] = s
        parent[key] = (prev, action)
        for name, fn in invs.items():
            if not fn(s):
                return violation(key, f"Invariant {name}")
        queue.append(key)
        return None

    for s in mod.init():
        if (r := visit(s, None, "Initial state")) is not None:
            return r
    depth_of = {k: 1 for k in queue}
    while queue:
        key = queue.popleft()
        succ = list(mod.next(states[key]))
        if not succ and check_deadlock and not terminal(states[key]):
            return violation(key, "Deadlock (no step possible and not a terminal state)")
        for action, s2 in succ:
            before = len(states)
            if (r := visit(s2, key, action)) is not None:
                return r
            if len(states) > before:
                depth_of[_freeze(s2)] = depth_of[key] + 1
        if len(states) > args.max_states:
            print(f"[error] stopped after {args.max_states} states without finishing; the result "
                  "is incomplete. Shrink the constants.")
            return ERROR
    print(f"[ok] no violation - {len(states)} distinct states, depth {max(depth_of.values(), default=0)}, "
          f"{time.time() - t0:.2f}s")
    return OK


# --------------------------------------------------------------------------- trace checking

def load_traces(path: Path, limit: int) -> tuple[list[tuple[str, list[dict]]], int]:
    """Recorded traces from a JSON Lines file: one trace per line, either a list of
    observations or {"label": ..., "trace": [...]}. An observation is an object that maps
    model variable names to the values the real code had at that moment. Returns the distinct
    traces (label, observations) and how many were recorded."""
    try:
        raw = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        die(f"no trace file at {path}: the recording command did not write VERIKIT_TRACE_FILE")
    seen: dict[str, tuple[str, list[dict]]] = {}
    for n, line in enumerate(raw, 1):
        try:
            obj = json.loads(line)
        except ValueError as e:
            die(f"{path.name} line {n}: not JSON ({e})")
        label, events = (str(obj.get("label", "")), obj.get("trace")) if isinstance(obj, dict) else ("", obj)
        if not isinstance(events, list) or not events:
            die(f"{path.name} line {n}: a trace must be a non-empty list of observations")
        clean: list[dict] = []
        for ev in events:
            if not isinstance(ev, dict):
                die(f"{path.name} line {n}: each observation must be an object of variable -> value")
            ev = {k: v for k, v in ev.items() if v is not None}
            if not ev:
                die(f"{path.name} line {n}: an observation names no model variable, so it checks nothing")
            if not clean or clean[-1] != ev:        # the same state logged twice is one observation
                clean.append(ev)
        seen.setdefault(json.dumps(clean, sort_keys=True), (label, clean))
    if not seen:
        die(f"{path.name} holds no trace: nothing was recorded, so there is nothing to check")
    if len(seen) > limit:
        die(f"{len(seen)} distinct traces were recorded, more than --max-traces {limit}. Raise the limit or "
            "record fewer runs: skipping traces could hide the one the model rejects.")
    return list(seen.values()), len(raw)


def tla_value(v) -> str:
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, list):
        return "<<" + ", ".join(tla_value(x) for x in v) + ">>"
    if isinstance(v, dict) and set(v) == {"set"} and isinstance(v["set"], list):
        return "{" + ", ".join(tla_value(x) for x in v["set"]) + "}"
    if isinstance(v, dict) and v:
        return "[" + ", ".join(f"{k} |-> {tla_value(x)}" for k, x in v.items()) + "]"
    die(f"trace value {v!r} cannot be written in TLA+; use numbers, strings, booleans, lists or {{\"set\": [...]}}")


def tla_variables(tla_text: str) -> list[str]:
    names: list[str] = []
    for block in re.findall(r"(?ms)^\s*VARIABLES?\b(.*?)(?=^\s*$|^\s*[A-Za-z_]\w*(?:\([^)]*\))?\s*==|^\s*(?:CONSTANTS?|ASSUME|EXTENDS)\b)", tla_text):
        block = re.sub(r"\\\*[^\n]*", "", re.sub(r"(?s)\(\*.*?\*\)", "", block))
        names += [n for n in re.split(r"[\s,]+", block) if re.fullmatch(r"[A-Za-z_]\w*", n)]
    return names


def trace_module(name: str, spec_module: str, traces: list[tuple[str, list[dict]]], init: str, nxt: str,
                 exact: bool) -> str:
    """A TLA+ module that follows the model while consuming one recorded trace: vk_t is the
    trace, vk_i the next observation to match. An observation is consumed by a model step whose
    new state has the observed values; with hidden steps allowed, the model may also take steps
    the recording did not see. A trace is accepted when TLC reaches vk_i = length + 1."""
    def obs(ev: dict, prime: str) -> str:
        return "(" + " /\\ ".join(f"{k}{prime} = {tla_value(v)}" for k, v in sorted(ev.items())) + ")"

    def cases(prime: str) -> str:
        rows = [f"t = {t} /\\ k = {k} -> {obs(ev, prime)}"
                for t, (_, events) in enumerate(traces, 1) for k, ev in enumerate(events, 1)]
        return "\n      [] ".join(rows)

    lens = " [] ".join(f"t = {t} -> {len(events)}" for t, (_, events) in enumerate(traces, 1))
    plain, primed, count = cases(""), cases("'"), len(traces)
    hidden = "" if exact else f"\n    \\/ ({nxt} /\\ UNCHANGED <<vk_t, vk_i>>)"
    return f"""---- MODULE {name} ----
\\* Generated by `verikit trace`; deleted after the run.
EXTENDS {spec_module}, Naturals, TLC
VARIABLES vk_t, vk_i

VkLen(t) == CASE {lens} [] OTHER -> 0
VkObs(t, k) ==
    CASE {plain}
      [] OTHER -> FALSE
VkObsNext(t, k) ==
    CASE {primed}
      [] OTHER -> FALSE
VkInit ==
    /\\ {init}
    /\\ vk_t \\in 1..{count}
    /\\ (vk_i = 1 \\/ (vk_i = 2 /\\ VkObs(vk_t, 1)))
VkNext ==
    \\/ (vk_i <= VkLen(vk_t) /\\ {nxt} /\\ VkObsNext(vk_t, vk_i) /\\ vk_t' = vk_t /\\ vk_i' = vk_i + 1){hidden}
VkSeen == PrintT(<<"vk-seen", vk_t, vk_i>>)
====
"""


def _tlc_exec(spec: Path, cfg_rel: str, timeout: int, docker: bool = False) -> tuple[str, str, float]:
    """Run TLC (one worker) on `spec` with `cfg_rel`; returns (output, where it ran, seconds)."""
    jar = _ensure_tla_jar()
    tlc_args = ["-workers", "1", "-config", cfg_rel, spec.name]
    java = os.environ.get("VERIKIT_JAVA") or shutil.which("java")
    tmp_meta = None
    if java and not docker:
        tmp_meta = tempfile.mkdtemp(prefix="tlc-")
        cmd = [java, "-XX:+UseParallelGC", "-cp", str(jar), "tlc2.TLC", "-metadir", tmp_meta, *tlc_args]
        where = "local Java"
    elif _docker_ok():
        cmd = ["docker", "run", "--rm",
               "--mount", f"type=bind,source={spec.parent},target=/spec",
               "--mount", f"type=bind,source={jar.parent},target=/tools,readonly",
               "-w", "/spec", JAVA_IMAGE, "java", "-XX:+UseParallelGC",
               "-cp", f"/tools/{jar.name}", "tlc2.TLC", "-metadir", "/tmp/tlc", *tlc_args]
        where = f"Docker ({JAVA_IMAGE})"
    else:
        die("TLC needs Java 11+ on PATH (or VERIKIT_JAVA), or a running Docker daemon.")
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=spec.parent, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        die(f"TLC timed out after {timeout}s - record fewer or shorter traces, or shrink the model")
    finally:
        if tmp_meta:
            shutil.rmtree(tmp_meta, ignore_errors=True)
    return (p.stdout + p.stderr).replace("\r\n", "\n"), where, time.time() - t0


def _trace_tla(args, spec: Path, traces: list[tuple[str, list[dict]]]) -> dict[int, int]:
    """For each trace (1-based), how many observations the TLA+ model could follow."""
    text = spec.read_text(encoding="utf-8")
    cfg = Path(args.config).resolve() if args.config else spec.with_suffix(".cfg")
    if not cfg.exists():
        die(f"config {cfg} not found")
    known = tla_variables(text)
    used = sorted({k for _, events in traces for ev in events for k in ev})
    unknown = [k for k in used if k not in known]
    if unknown:
        die(f"the traces observe {', '.join(unknown)}, which {spec.name} does not declare. "
            f"Its variables are: {', '.join(known) or '(none found)'}")
    consts = {}
    for kv in args.const:
        k, sep, v = kv.partition("=")
        if not sep or not k.strip():
            die(f"--const expects NAME=VALUE, got {kv!r}")
        consts[k.strip()] = v.strip()
    cfg_text = transform_cfg(cfg.read_text(encoding="utf-8"), consts, None)
    init, nxt = args.init, args.next
    kept, section = [], None
    for line in cfg_text.splitlines():
        body = line.split("\\*")[0].strip()
        first = body.split()[0] if body else ""
        if first in CFG_KEYWORDS:
            section = first
            rest = body[len(first):].strip()
            if first == "INIT" and rest and not init:
                init = rest
            if first == "NEXT" and rest and not nxt:
                nxt = rest
            if first == "SPECIFICATION" and rest and not (init and nxt):
                m = re.search(rf"(?m)^{re.escape(rest)}\s*==\s*(\w+)\s*/\\\s*\[\]\[\s*(\w+)\s*\]_", text)
                if m:
                    init, nxt = init or m.group(1), nxt or m.group(2)
        if section and section.startswith("CONSTANT"):
            kept.append(line)
    init, nxt = init or "Init", nxt or "Next"
    name = f"VerikitTrace{os.getpid()}{random.randrange(10**6)}"
    mod, mod_cfg = spec.parent / f"{name}.tla", spec.parent / f"{name}.cfg"
    spec_module = re.search(r"MODULE\s+(\w+)", text).group(1)
    try:
        mod.write_text(trace_module(name, spec_module, traces, init, nxt, args.exact), encoding="utf-8")
        mod_cfg.write_text("\n".join([*kept, "INIT VkInit", "NEXT VkNext", "INVARIANT VkSeen",
                                      "CHECK_DEADLOCK FALSE", ""]), encoding="utf-8")
        out, where, secs = _tlc_exec(mod, mod_cfg.name, args.timeout, args.docker)
    finally:
        mod.unlink(missing_ok=True)
        mod_cfg.unlink(missing_ok=True)
    if args.raw:
        print(out)
    if "Model checking completed. No error has been found." not in out:
        print(out if not args.raw else "")
        die(f"TLC could not replay the traces against {spec.name} (see its output above)")
    print(f"[verikit] replayed with TLC {TLA_VERSION} via {where} ({secs:.1f}s)")
    reached: dict[int, int] = {}
    for t, i in re.findall(r'<<"vk-seen", (\d+), (\d+)>>', out):
        reached[int(t)] = max(reached.get(int(t), 0), int(i) - 1)
    return reached


def _trace_python(args, path: Path, traces: list[tuple[str, list[dict]]]) -> dict[int, int]:
    """The same replay for a Python explorer model (init() / next(state))."""
    spec = importlib.util.spec_from_file_location("verikit_model", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    consts = getattr(mod, "CONSTANTS", {})
    for kv in args.const:
        k, _, v = kv.partition("=")
        if k not in consts:
            die(f"unknown constant {k!r}; the model defines {', '.join(consts) or 'none'}")
        try:
            consts[k] = ast.literal_eval(v)
        except (ValueError, SyntaxError):
            consts[k] = v

    def norm(v):
        if isinstance(v, list):
            return tuple(norm(x) for x in v)
        if isinstance(v, dict) and set(v) == {"set"}:
            return frozenset(norm(x) for x in v["set"])
        return v

    def closure(states: dict) -> dict:      # everything reachable in zero or more steps
        todo, found = list(states.values()), dict(states)
        while todo:
            for _, s2 in mod.next(todo.pop()):
                key = _freeze(s2)
                if key not in found:
                    found[key] = s2
                    todo.append(s2)
                    if len(found) > args.max_states:
                        die(f"more than {args.max_states} model states while replaying; shrink the constants")
        return found

    def step(states: dict) -> dict:
        return {_freeze(s2): s2 for s in states.values() for _, s2 in mod.next(s)}

    start = {_freeze(s): s for s in mod.init()}
    variables = set(next(iter(start.values())))
    reached: dict[int, int] = {}
    for t, (_, events) in enumerate(traces, 1):
        unknown = sorted({k for ev in events for k in ev} - variables)
        if unknown:
            die(f"the traces observe {', '.join(unknown)}, which the model's states do not have. "
                f"Its variables are: {', '.join(sorted(variables))}")
        current, reached[t] = start, 0
        for k, ev in enumerate(events):
            if args.exact:
                pool = {**(current if k == 0 else {}), **step(current)}
            else:
                pool = closure(current if k == 0 else step(current))
            current = {key: s for key, s in pool.items() if all(s[n] == norm(v) for n, v in ev.items())}
            if not current:
                break
            reached[t] = k + 1
    return reached


def cmd_trace(args) -> int:
    """Check that what the real code did (recorded in tests) is something the model allows."""
    model = Path(args.model).resolve()
    if not model.exists():
        die(f"{model} not found")
    tmp_dir = None
    if args.record:
        tmp_dir = Path(tempfile.mkdtemp(prefix="verikit-trace-"))
        traces_path = tmp_dir / "traces.jsonl"
        code, out, secs = run_shell(args.record, Path.cwd(), args.timeout, None,
                                    {"VERIKIT_TRACE_FILE": str(traces_path),
                                     **({"VERIKIT_SEED": os.environ["VERIKIT_SEED"]} if os.environ.get("VERIKIT_SEED") else {})})
        if code != 0:
            print(tail(out))
            shutil.rmtree(tmp_dir, ignore_errors=True)
            die(f"the recording command failed (exit {code}), so no trace was checked: {args.record}")
    elif args.traces:
        traces_path = Path(args.traces).resolve()
    else:
        die("give a trace file, or --record COMMAND (it must write traces to $VERIKIT_TRACE_FILE)")
    try:
        traces, recorded = load_traces(traces_path, args.max_traces)
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    sizes = sorted(len(events) for _, events in traces)
    print(f"[verikit] trace: {len(traces)} distinct trace(s) of {recorded} recorded, {sizes[0]}-{sizes[-1]} "
          f"observations each, against {model.name} "
          f"({'every step must be observed' if args.exact else 'unobserved model steps allowed'})")
    reached = _trace_python(args, model, traces) if model.suffix == ".py" else _trace_tla(args, model, traces)
    rejected = [(t, label, events) for t, (label, events) in enumerate(traces, 1) if reached.get(t, 0) < len(events)]
    if not rejected:
        print(f"[ok] every recorded trace is a behaviour the model allows ({len(traces)} distinct)")
        return OK
    print(f"[TRACE REJECTED] {len(rejected)} of {len(traces)} distinct trace(s) are not behaviours of the model: "
          "the real code reached a state the model says is impossible")
    for t, label, events in rejected[:5]:
        k = reached.get(t, 0)
        print(f"  trace {t}{' (' + label + ')' if label else ''}: the model follows observations 1-{k}, then has "
              f"no step to observation {k + 1}:" if k else
              f"  trace {t}{' (' + label + ')' if label else ''}: the model cannot produce observation 1:")
        for n, ev in enumerate(events[:k + 1], 1):
            mark = "  <- not allowed by the model" if n == k + 1 else ""
            print(f"    {n:>2}. " + ", ".join(f"{name}={json.dumps(v)}" for name, v in sorted(ev.items())) + mark)
    if len(rejected) > 5:
        print(f"  ... and {len(rejected) - 5} more")
    print("Either the code has a bug, or the model no longer describes the code. Replay the trace against "
          "both, and fix the one that is wrong.")
    return FAILED


# --------------------------------------------------------------------------- doctor

def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def classify_csproj(p: Path) -> tuple[str, list[str]]:
    text = _read(p)
    tfms = re.findall(r"<TargetFrameworks?>([^<]+)</TargetFrameworks?>", text)
    tfms += re.findall(r"<TargetFrameworkVersion>v?([^<]+)</TargetFrameworkVersion>", text)
    tf = sorted({t.strip() for group in tfms for t in group.split(";") if t.strip()})
    framework = [t for t in tf if re.match(r"^(net4|4\.|v4|net3|net2)", t)]
    modern = [t for t in tf if t not in framework]
    sdk = "Sdk=" in text
    kind = []
    if framework or not sdk:
        kind.append(".NET Framework" + ("" if sdk else " (legacy project format)"))
    if modern:
        kind.append(".NET")
    pkgs = _read(p.parent / "packages.config").lower()  # legacy projects list packages there
    tests = [n for n, pat in (("xUnit", "xunit"), ("NUnit", "nunit"), ("MSTest", "mstest"),
                              ("FsCheck", "fscheck"), ("CsCheck", "cscheck"), ("Coyote", "coyote"))
             if pat in text.lower() or pat in pkgs]
    if pkgs:
        tests.append("packages.config")
    return " + ".join(kind) or ".NET", [*tf, *tests]


def classify_package_json(p: Path) -> tuple[str, list[str]]:
    try:
        data = json.loads(_read(p) or "{}")
    except ValueError:
        return "JavaScript", []
    deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
    kinds = []
    if "@angular/core" in deps:
        kinds.append("Angular")
    if "react" in deps:
        kinds.append("React")
    if any(d in deps for d in ("express", "fastify", "@nestjs/core", "koa", "@hapi/hapi", "hono")):
        kinds.append("Node backend")
    if not kinds:
        kinds.append("TypeScript" if "typescript" in deps else "JavaScript/Node")
    tests = [d for d in ("jest", "vitest", "karma", "jasmine-core", "mocha", "@playwright/test",
                         "@testing-library/react", "@testing-library/angular", "fast-check") if d in deps]
    return " + ".join(kinds), tests


ROUTES = {
    ".NET": ("FsCheck / CsCheck property tests; Microsoft Coyote for async/concurrency",
             "dotnet test --filter Category=Verification"),
    ".NET Framework": ("FsCheck property tests in an SDK-style test project (TargetFrameworks net8.0;net48)",
                       "dotnet test --filter Category=Verification (Windows for net4x)"),
    ".NET Framework (legacy)": (
        "FsCheck 2.x + FSharp.Core via packages.config, in the test framework the project already uses; "
        "list new test files in the .csproj's <Compile> items",
        "msbuild -restore -p:RestorePackagesConfig=true, then vstest.console.exe <tests.dll> "
        "/TestCaseFilter:TestCategory=Verification (MSTest; an xUnit trait uses Category=) -- "
        "RunConfiguration.TreatNoTestsAsError=true. Copy examples/dotnet-legacy/verification/legacy_test.py. "
        "NOT `dotnet test` on the project: it runs no tests and exits 0"),
    "React": ("fast-check + Testing Library (fc.scheduler for click/response races)",
              "npx vitest run -t verification  /  npx jest -t verification"),
    "Angular": ("fast-check on services/stores (RxJS), Jasmine/Jest; component races via TestBed",
                "npx jest -t verification  /  ng test --watch=false"),
    "Node backend": ("fast-check (fc.scheduler for retries/races) on the real handlers",
                     "node --test  /  npx vitest run -t verification"),
}
ROUTES["JavaScript/Node"] = ROUTES["TypeScript"] = ROUTES["Node backend"]


def cmd_doctor(args) -> int:
    root = find_root(args.root)
    print(f"verikit {VERSION} | root {root}")
    projects: list[tuple[str, str, list[str]]] = []
    base = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        dirnames[:] = [n for n in dirnames if n not in SKIP_DIRS and not n.startswith(".")]
        if len(d.parts) - base >= args.depth:
            dirnames[:] = []
        for f in filenames:
            p = d / f
            rel = p.relative_to(root).as_posix()
            if f.endswith((".csproj", ".fsproj", ".vbproj")):
                kind, extra = classify_csproj(p)
                projects.append((rel, kind, extra))
            elif f == "package.json":
                kind, extra = classify_package_json(p)
                projects.append((rel, kind, extra))
            elif f in ("pyproject.toml", "setup.py"):
                projects.append((rel, "Python", []))
            elif f == "Cargo.toml":
                projects.append((rel, "Rust", []))
            elif f == "go.mod":
                projects.append((rel, "Go", []))
            elif f in ("pom.xml", "build.gradle", "build.gradle.kts"):
                projects.append((rel, "Java/Kotlin", []))
    if not projects:
        print("\nno project files found (searched to depth", args.depth, ")")
    else:
        print(f"\nprojects found ({len(projects)}, depth <= {args.depth}):")
        for rel, kind, extra in projects[:200]:
            print(f"  {kind:<34} {rel}" + (f"   [{', '.join(extra)}]" if extra else ""))
        if len(projects) > 200:
            print(f"  ... {len(projects) - 200} more")
    kinds = {k.strip().replace(" (legacy project format)", " (legacy)") for _, kind, _ in projects
             for k in kind.split("+")}
    print("\nwhat fits:")
    print("  bad order of steps, any stack (retries, races, queues, sync, state machines)\n"
          "      -> TLA+ model + `verikit tlc` (needs Java 11+ or Docker)")
    for k in sorted(kinds):
        if k in ROUTES:
            tools, cmd = ROUTES[k]
            print(f"  {k} code\n      -> {tools}\n      run as: {cmd}")
    other = sorted(k for k in kinds if k not in ROUTES)
    if other:
        print(f"  {', '.join(other)}: see .claude/skills/verify-create/routing.md (kit: plugin/skills/...)")
    if kinds & set(ROUTES):
        print("  stack notes: .claude/skills/verify-create/stacks/ (dotnet.md, node.md, react.md, angular.md)")
    print("\ntools on this machine:")
    for exe, use in (("git", "required"), ("python", "verikit itself"), ("java", "TLA+ TLC"),
                     ("docker", "TLA+ TLC in a container"), ("dotnet", ".NET tests"), ("node", "JS/TS tests")):
        path = shutil.which(exe)
        extra = (" (daemon up)" if _docker_ok() else " (daemon NOT running)") if exe == "docker" and path else ""
        print(f"  {'[x]' if path else '[ ]'} {exe:<8} {use}{extra}")
    if ".NET Framework (legacy)" in kinds:
        vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                       "Microsoft Visual Studio", "Installer", "vswhere.exe")
        print(f"  {'[x]' if vswhere.is_file() else '[ ]'} vswhere  finds MSBuild 16.5+ and vstest.console for "
              "legacy projects (Visual Studio or Build Tools)")
        legacy_msbuild = shutil.which("msbuild")
        if legacy_msbuild and "Microsoft.NET" in legacy_msbuild:
            print(f"      note: `msbuild` on PATH is {legacy_msbuild} (MSBuild 4, cannot restore packages.config)")
    if len(str(root)) > 110 and any(".NET Framework" in k for k in kinds):
        print(f"\nwarning: this root's path is {len(str(root))} characters. .NET Framework test discovery "
              "fails silently near 260, and packages/ adds about 140. Clone to a short path (C:\\src\\...).")
    vendored = root / "verification" / "verikit.py"
    if (root / MANIFEST_REL).exists():
        v = re.search(r'^VERSION = "([^"]+)"', _read(vendored), re.M) if vendored.exists() else None
        print(f"\nmanifest: {MANIFEST_REL.as_posix()} found; vendored runner {v.group(1) if v else 'missing'}"
              f" (this kit: {VERSION})")
    else:
        print(f"\nmanifest: {MANIFEST_REL.as_posix()} missing - run `python verikit.py init` here")
    return OK


# --------------------------------------------------------------------------- init

MANIFEST_TEMPLATE = '''# Promises this repo verifies. Read by verikit: python verification/verikit.py run
# One [[check]] per promise. Add checks with the verify-create skill; see the verikit README.
version = 1

# [[check]]
# id = "checkout-design"
# rule = "A customer is never charged twice for one purchase."      # one plain sentence
# harm = "The customer pays twice; refunds, chargebacks, lost trust."
# level = "design"            # design = model of the design | code = runs the real code
# tool = "TLA+ / TLC"
# tags = ["payments"]
# run = "{verikit} tlc verification/checkout/Checkout.tla"            # ONE command, exit 0 = holds
# covers = ["src/Payments/**/*.cs", "web/src/app/checkout/**/*.ts"]   # product code this check describes
#          # or one function, so other edits in a big file are not drift: "src/Payments/Checkout.cs#PayForOrder"
# harness = ["tests/Payments.Tests/CheckoutVerificationTests.cs"]     # the check's own test files outside verification/
# bounds = "1 order, up to 3 attempts"
# assumes = ["The payment provider deduplicates by idempotency key"]
# (`verikit sync <id>` records what the check was confirmed against in verification/sync/<id>.json)
#
#   [[check.must_fail]]       # a check you have never seen fail proves nothing
#   why = "Broken variant: a fresh idempotency key on every retry"
#   run = "{verikit} tlc verification/checkout/Checkout.tla --const ReuseKey=FALSE"
#   match = 'VIOLATION\\] NeverChargedTwice'     # regex naming the rule; '...' = TOML literal string
#
#   [[check.must_fail]]
#   why = "Reachability witness: a charge can happen at all"
#   run = "{verikit} tlc verification/checkout/Checkout.tla --inv NeverCharged"
#   match = 'VIOLATION\\] NeverCharged\\b'
#
#   [[check.must_fail]]       # in a code check: break the guard itself, by find-and-replace ...
#   why = "Broken variant: the paid check is gone"
#   edits = [{ file = "src/Payments/Checkout.cs", find = 'if (order.IsPaid) return;', replace = '' }]
#   match = 'customer charged [2-9] times'
#   # ... or with a patch file: patch = "verification/checkout/mutants/no-paid-check.patch"

# [isolation]                 # for `run --isolated`: variants run in a throwaway copy of the repo
# link = ["node_modules"]     # git-ignored dependency folders to link into the copy
'''


CI_TEMPLATES = {"github": ("github-actions.yml", ".github/workflows/verify.yml"),
                "azure": ("azure-pipelines.yml", "azure-pipelines.verify.yml")}
ROOTS_LINE = re.compile(r"(?m)^(?P<pre>[^\r\n#]*?)\[(?P<items>[^\]\r\n]*)\](?P<post>[ \t]*# verikit:roots[^\r\n]*)")


def set_ci_roots(text: str, roots: list[str]) -> str:
    m = ROOTS_LINE.search(text)
    if not m:
        die("CI template has no `# verikit:roots` line")
    line = m["pre"] + "[" + ", ".join(json.dumps(r) for r in roots) + "]" + m["post"]
    return text[:m.start()] + line + text[m.end():]


def add_ci_root(path: Path, root_rel: str) -> str:
    """Add a verification root to an existing workflow's `# verikit:roots` list."""
    text = read_keep(path)
    m = ROOTS_LINE.search(text)
    if not m:
        return "no-marker"
    roots = [r.strip().strip("'\"") for r in m["items"].split(",") if r.strip()]
    if root_rel in roots:
        return "present"
    write_keep(path, set_ci_roots(text, [*roots, root_rel]))
    return "added"


def ci_list(value: str) -> list[str]:
    names = ["github", "azure"] if value == "both" else [v.strip() for v in value.split(",") if v.strip()]
    bad = [n for n in names if n not in CI_TEMPLATES]
    if bad:
        raise argparse.ArgumentTypeError(f"unknown CI {', '.join(bad)}; use github, azure, github,azure or both")
    return names


def hook_command(script: str) -> str:
    """One command line for every developer's machine: macOS and many Linux systems have only
    `python3`, Windows usually only `python`. Claude Code runs it with sh, or Git Bash on Windows."""
    # The probe is silent on purpose: Windows ships a placeholder `python3` that prints a hint.
    return f'if python3 -c "" >/dev/null 2>&1; then python3 {script} hook; else python {script} hook; fi'


def cmd_init(args) -> int:
    root = (args.root or Path.cwd()).resolve()
    top = git_toplevel(root)
    if not top:
        die(f"{root} is not inside a git repository; verikit needs git (git init first)")
    vdir = root / "verification"
    vdir.mkdir(exist_ok=True)
    man = root / MANIFEST_REL
    if not man.exists():
        write_keep(man, MANIFEST_TEMPLATE)  # LF on every OS; sync keeps whatever style it finds
        print(f"created {man.relative_to(top).as_posix()}")
    target = vdir / "verikit.py"
    if Path(__file__).resolve() != target.resolve():
        shutil.copy2(__file__, target)
        print(f"wrote {target.relative_to(top).as_posix()} (runner {VERSION}; commit it; re-run init to upgrade)")
    gi = root / ".gitignore"
    if ".verikit/" not in _read(gi):
        existing = read_keep(gi) if gi.exists() else ""
        nl = "\r\n" if "\r\n" in existing else "\n"
        sep = nl if existing and not existing.endswith("\n") else ""
        write_keep(gi, existing + sep + ".verikit/" + nl)   # same line endings as the file had
        print("added .verikit/ to .gitignore")
    skills_src = KIT_DIR / "plugin" / "skills"
    if args.skills and skills_src.exists():
        for skill in sorted(skills_src.iterdir()):
            dest = top / ".claude" / "skills" / skill.name
            if dest.exists() and not args.force:
                def digest(folder: Path) -> str:
                    return hashlib.sha256(b"".join(
                        f.relative_to(folder).as_posix().encode() + content_digest(f.read_bytes()).encode()
                        for f in sorted(folder.rglob("*")) if f.is_file())).hexdigest()
                same = digest(dest) == digest(skill)
                print(f"kept existing .claude/skills/{skill.name}" + ("" if same else
                      " - it DIFFERS from this kit's version; run init again with --force to update it "
                      "(agents follow these files, and older ones describe commands that have changed)"))
                continue
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(skill, dest)
            print(f"installed skill .claude/skills/{skill.name}")
    if args.hook:
        settings = top / ".claude" / "settings.json"
        data = json.loads(_read(settings) or "{}") if settings.exists() else {}
        hooks = data.setdefault("hooks", {})
        existing = [h for event in ("SessionStart", "Stop") for entry in hooks.get(event, [])
                    for h in entry.get("hooks", []) if "verikit.py" in h.get("command", "")]
        # one hook serves every verification root in the repo: keep the runner an earlier init chose
        found = re.search(r'"(\$\{CLAUDE_PROJECT_DIR\}/[^"]*verikit\.py)"', existing[0]["command"]) if existing else None
        script = f'"{found.group(1)}"' if found else '"${CLAUDE_PROJECT_DIR}/' + target.relative_to(top).as_posix() + '"'
        hook_cmd = hook_command(script)
        done = []
        for event in ("SessionStart", "Stop"):   # SessionStart records the baseline that Stop compares with
            entries = hooks.setdefault(event, [])
            mine = [h for entry in entries for h in entry.get("hooks", []) if "verikit.py" in h.get("command", "")]
            if not mine:
                entries.append({"hooks": [{"type": "command", "command": hook_cmd, "timeout": 60}]})
                done.append(f"added the {event} hook")
            for h in mine:
                if h["command"] != hook_cmd:
                    h["command"] = hook_cmd
                    done.append(f"updated the {event} hook")
        if done:
            settings.parent.mkdir(exist_ok=True)
            write_keep(settings, json.dumps(data, indent=2) + "\n")
            print(f"{', '.join(done)} in .claude/settings.json")
    for ci in args.ci:
        src, dest = CI_TEMPLATES[ci]
        dest = top / dest
        rel = dest.relative_to(top).as_posix()
        root_rel = git_prefix(root) or "."
        if dest.exists():
            result = add_ci_root(dest, root_rel)
            print({"added": f"added root {root_rel} to {rel}",
                   "present": f"{rel} already runs root {root_rel}",
                   "no-marker": f"kept existing {rel}; it has no `# verikit:roots` list, so add a step "
                                f"for root {root_rel} by hand"}[result])
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            write_keep(dest, set_ci_roots(read_keep(KIT_DIR / "templates" / "ci" / src), [root_rel]))
            print(f"added {rel} (root {root_rel}) - edit the setup steps for your stacks")
    print("\nnext: in Claude Code run /verify-survey to rank the promises worth verifying, "
          "then /verify-create on the top one.")
    return OK


# --------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="verikit", description=__doc__.split("\n\n")[0])
    ap.add_argument("--version", action="version", version=f"verikit {VERSION}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", type=Path, help="folder that holds verification/ (default: nearest above cwd)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", parents=[common], help="run checks and their must_fail variants")
    p.add_argument("ids", nargs="*")
    p.add_argument("--tag", action="append", default=[], help="only checks with this tag (repeatable)")
    p.add_argument("--changed", nargs="?", const="", default=None, metavar="REF",
                   help="only checks whose covered files changed: vs REF...HEAD (e.g. origin/main), "
                        "or vs each check's sync record when REF is omitted")
    p.add_argument("--ci", action="store_true",
                   help="in CI: on a pull request (GitHub Actions, Azure Pipelines, GitLab) run only the "
                        "checks whose files changed vs the target branch; otherwise run every check")
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--seed", type=int, help="seed exported as VERIKIT_SEED (default: random, printed)")
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--json", help="write results as JSON")
    p.add_argument("--junit", help="write results as JUnit XML (for CI test reports)")
    p.add_argument("--isolated", action="store_true",
                   help="apply and run broken variants in a throwaway copy of the repository, so your "
                        "working files are never edited (see `[isolation]` in the manifest)")
    p.add_argument("--jobs", type=int, default=1, metavar="N",
                   help="run the variants of up to N checks side by side, one copy each (implies --isolated)")
    p.add_argument("--summary", metavar="FILE.md",
                   help="write a Markdown summary of this run (with --ci it also goes to the GitHub job "
                        "summary or the Azure run's Extensions tab)")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("stale", parents=[common], help="checks whose covered content changed since their last sync")
    p.add_argument("ids", nargs="*")
    p.add_argument("--tag", action="append", default=[])
    p.add_argument("--strict", action="store_true", help="exit 1 when anything is stale (for CI)")
    p.set_defaults(fn=cmd_stale)

    p = sub.add_parser("sync", parents=[common], help="re-run checks, then record the content they confirmed (verification/sync/)")
    p.add_argument("ids", nargs="*", help="check ids (default: every check)")
    p.add_argument("--tag", action="append", default=[],
                   help="only checks with this tag (repeatable), e.g. every rule of one flow")
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--seed", type=int)
    p.set_defaults(fn=cmd_sync)

    p = sub.add_parser("report", parents=[common], help="every promise and where it stands, without running anything")
    p.add_argument("--tag", action="append", default=[])
    p.add_argument("--md", metavar="FILE", help="write Markdown (default: print it)")
    p.add_argument("--html", metavar="FILE", help="write a self-contained HTML page")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("seed", parents=[common], help="keep the seed of a failing case; `run` replays saved seeds")
    p.add_argument("action", choices=["add", "list"])
    p.add_argument("id", nargs="?")
    p.add_argument("seed", nargs="?", type=int)
    p.add_argument("--note", help="what this case is, e.g. 'late reply after retry'")
    p.set_defaults(fn=cmd_seed)

    p = sub.add_parser("list", parents=[common], help="show every check")
    p.add_argument("--tag", action="append", default=[])
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("doctor", parents=[common], help="detect projects, test frameworks and tools")
    p.add_argument("--depth", type=int, default=6)
    p.set_defaults(fn=cmd_doctor)

    sub.add_parser("hook", help="Claude Code SessionStart and Stop hook (reads hook JSON on stdin)").set_defaults(fn=cmd_hook)
    sub.add_parser("version", help="print the runner version").set_defaults(fn=lambda a: print(f"verikit {VERSION}") or OK)

    p = sub.add_parser("init", parents=[common], help="scaffold verification/ in this folder")
    p.add_argument("--hook", action="store_true", help="add the Claude Code hooks (SessionStart baseline + Stop check)")
    p.add_argument("--ci", type=ci_list, default=[], metavar="github,azure",
                   help="add CI workflow(s): github, azure, or both (comma-separated); run again in each "
                        "monorepo root to add that root to the workflow")
    p.add_argument("--no-skills", dest="skills", action="store_false", help="do not copy the skills")
    p.add_argument("--force", action="store_true", help="overwrite existing skills with the kit's version")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("tlc", help="model-check a TLA+ spec with TLC")
    p.add_argument("spec")
    p.add_argument("--config", "-c")
    p.add_argument("--const", action="append", default=[], metavar="NAME=VALUE",
                   help="override a CONSTANT for this run (repeatable)")
    p.add_argument("--inv", action="append", default=[], metavar="INVARIANT",
                   help="check only these invariants (repeatable); for witnesses")
    p.add_argument("--workers", default="1",
                   help="1 (default) guarantees the SHORTEST counterexample; 'auto' is faster on big models")
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--docker", action="store_true", help="use Docker even if Java is installed")
    p.add_argument("--raw", action="store_true", help="also print TLC's raw output")
    p.set_defaults(fn=cmd_tlc)

    p = sub.add_parser("trace", help="check that traces recorded from the real code are behaviours of the model")
    p.add_argument("model", help="SPEC.tla (checked with TLC) or MODEL.py (built-in explorer)")
    p.add_argument("traces", nargs="?", help="JSON Lines file: one trace per line, each a list of observations")
    p.add_argument("--record", metavar="COMMAND",
                   help="run COMMAND first; it must append traces to the file named by $VERIKIT_TRACE_FILE")
    p.add_argument("--config", "-c")
    p.add_argument("--const", action="append", default=[], metavar="NAME=VALUE")
    p.add_argument("--exact", action="store_true",
                   help="every model step must appear in the trace (default: the model may take unobserved steps)")
    p.add_argument("--max-traces", type=int, default=500,
                   help="fail if more distinct traces than this were recorded (default 500); none is ever skipped")
    p.add_argument("--init", help="name of the model's initial predicate (default: from the .cfg, else Init)")
    p.add_argument("--next", help="name of the model's next-state action (default: from the .cfg, else Next)")
    p.add_argument("--max-states", type=int, default=2_000_000)
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--docker", action="store_true", help="use Docker even if Java is installed")
    p.add_argument("--raw", action="store_true", help="also print TLC's raw output")
    p.set_defaults(fn=cmd_trace)

    p = sub.add_parser("explore", help="exhaustively explore a Python state-machine model")
    p.add_argument("model")
    p.add_argument("--const", action="append", default=[], metavar="NAME=VALUE")
    p.add_argument("--inv", action="append", default=[], metavar="INVARIANT")
    p.add_argument("--max-states", type=int, default=2_000_000)
    p.set_defaults(fn=cmd_explore)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except VerikitError as e:
        print(f"verikit: {e}", file=sys.stderr)
        return e.code
    except KeyboardInterrupt:
        print("verikit: interrupted; any broken-variant patch is reverted on the next run", file=sys.stderr)
        return ERROR


if __name__ == "__main__":
    sys.exit(main())
