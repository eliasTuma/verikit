#!/usr/bin/env python3
"""verikit - machine-checked promises for any repository, beside your existing tests.

A repo lists its promises in verification/verification.toml. Each [[check]] has a
plain-English rule, ONE command that checks it (a TLA+ model, a property test in your
own test project, a code-level verifier - any tool, any language), the files it covers,
broken variants that MUST fail, and the commit where it last matched the code.

    python verikit.py doctor                 stacks, test frameworks and tools found; what fits
    python verikit.py init [--hook] [--ci github,azure]
                                             scaffold verification/ (+ skills, Stop hook, CI)
    python verikit.py list                   every check: rule, covers, bounds, sync state
    python verikit.py run [ID ...]           run checks; every must_fail variant has to FAIL
          [--tag T] [--changed [REF] | --ci] [--junit FILE] [--json FILE] [--seed N]
    python verikit.py stale [--strict]       checks whose covered code changed since their sync commit
    python verikit.py sync ID ...            re-run, then record HEAD as the sync commit
    python verikit.py tlc SPEC.tla [-c CFG] [--const K=V] [--inv NAME]
                                             TLA+ model checker (local Java, else Docker)
    python verikit.py explore MODEL.py       built-in breadth-first explorer (zero install)
    python verikit.py hook                   Claude Code Stop hook
    python verikit.py version

Exit codes: 0 = holds / all good, 1 = a property or check failed, 2 = tool or setup error.
Needs Python 3.11+ (stdlib only) and git. TLA+ checks also need Java 11+ or Docker.
"""
from __future__ import annotations

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
import sys
import tempfile
import time
import tomllib
import urllib.request
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path

VERSION = "0.2.1"
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
        c.setdefault("synced_commit", "")
        c.setdefault("synced_sources", {})
        c.setdefault("tags", [])
        c.setdefault("harness", [])
        if not isinstance(c["harness"], list):
            die(f"check {c['id']!r}: `harness` must be a list of globs (the check's own test/model files)")
        if not isinstance(c["synced_sources"], dict):
            die(f"check {c['id']!r}: `synced_sources` is written by `verikit sync`; restore it or delete the line")
        c["_sources"] = sources
        for pattern in (*c["covers"], *c["harness"]):
            m = SOURCE_PREFIX.match(pattern)
            if m and m.group(1) not in sources:
                die(f"check {c['id']!r}: {pattern!r} names a repository {m.group(1)!r}, but the manifest has no "
                    f"[sources.{m.group(1)}] table. Add one with `path = \"../{m.group(1)}\"`.")
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


def _source_changes(src: Path, globs: list[str], since: str | None) -> tuple[int, list[str]]:
    """Files under `src` matching `globs` that changed since commit `since` (None: only
    uncommitted edits and untracked files), relative to `src`."""
    specs = [f":(glob){g}" for g in globs]
    found: set[str] = set()
    code, out = 0, ""
    if since:
        code, out = git(src, "diff", "--name-only", "--relative", f"{since}..HEAD", "--", *specs)
        if code != 0:
            return code, [out]
        found.update(out.splitlines())
    _, dirty = git(src, "diff", "--name-only", "--relative", "HEAD", "--", *specs)
    _, untracked = git(src, "ls-files", "--others", "--exclude-standard", "--", *specs)
    found.update(dirty.splitlines())
    found.update(untracked.splitlines())
    return 0, sorted(f for f in found if f.strip())


def pathspecs(check: dict) -> list[str]:
    return [f":(glob){p}" for p in watched(check)]


def check_files(root: Path, check: dict) -> list[Path]:
    """Every file that decides this check's result: covered code, harness files, patches, and
    the verification/<area>/ folders its commands refer to. Not the manifest itself (the
    check's own entry is hashed separately) and not other checks' areas."""
    files: set[Path] = set()
    for pattern in watched(check):
        files.update(p for p in root.glob(pattern) if p.is_file())
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
        files.update(x for x in area.rglob("*") if x.is_file())
    return sorted(files)


def fingerprint(root: Path, check: dict) -> str:
    """Hash of the check's meaning + every file that decides its result, including the files
    it covers in other repositories."""
    meaning = {k: v for k, v in check.items()
               if k not in ("synced_commit", "synced_sources") and not k.startswith("_")}
    h = hashlib.sha256(json.dumps(meaning, sort_keys=True, default=str).encode())
    for p in check_files(root, check):
        h.update(p.relative_to(root).as_posix().encode())
        h.update(p.read_bytes())
    for name, globs in sorted(source_globs(check).items()):
        src = source_dir(root, check, name)
        if src is None:
            h.update(f"{name}:<no checkout>".encode())
            continue
        for p in sorted({f for g in globs for f in src.glob(g) if f.is_file()}):
            h.update(f"{name}:{p.relative_to(src).as_posix()}".encode())
            h.update(p.read_bytes())
    return h.hexdigest()


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


class AppliedPatch:
    """Context manager: apply a broken-variant patch, always revert it, and prove it changed code.
    The touched files are backed up byte for byte first, and the revert restores those bytes."""

    def __init__(self, root: Path, rel: str):
        self.root, self.rel, self.patch = root, rel, (root / rel)
        self.top, self.args = _patch_args(root, self.patch)
        self.files: list[Path] = []
        self.saved: list[dict] = []
        self.before: dict[str, str] = {}

    def __enter__(self):
        c, o = git(self.top, "apply", "--check", *self.args)
        if c != 0:
            raise VerikitError(f"patch {self.rel} does not apply: {o}", FAILED)
        self.files = _patched_files(self.root, self.patch)
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
            {"patch": str(self.patch.resolve()), "rel": self.rel, "at": time.time(), "files": self.saved}),
            encoding="utf-8")
        c, o = git(self.top, "apply", *self.args)
        if c != 0 or _digest(self.files) == self.before:
            self._revert()
            raise VerikitError(f"patch {self.rel} applied but changed no file; check its paths "
                               "(they must be relative to the folder that holds verification/)", FAILED)
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

def run_check(root: Path, check: dict, timeout: int, verbose: bool, seed: int | None) -> dict:
    timeout = int(check.get("timeout", timeout))
    result = {"id": check["id"], "rule": check["rule"], "ok": True, "steps": []}
    print(f"\n== {check['id']}  [{check.get('level', '?')} | {check.get('tool', '?')}]")
    print(f"   rule: {check['rule']}")

    try:
        main_cmd, src_env = with_sources(root, check, check["run"])
    except VerikitError as e:
        print(f"   [FAIL] holds on the current code: {e}")
        result["steps"].append({"what": "holds on the current code", "ok": False, "exit": None, "output": str(e)})
        result["ok"] = False
        return result
    code, out, secs = run_shell(main_cmd, root, timeout, seed, src_env)
    passed = code == 0
    print(f"   {'[ok]  ' if passed else '[FAIL]'} holds on the current code ({secs:.1f}s, exit {code})")
    if not passed or verbose:
        print(tail(out))
    result["steps"].append({"what": "holds on the current code", "ok": passed, "exit": code,
                            "seconds": round(secs, 2), "output": tail(out, 60)})
    result["ok"] &= passed

    for mf in check["must_fail"]:
        why = mf.get("why", "broken variant")
        try:
            cmd, _ = with_sources(root, check, mf.get("run", check["run"]))
            if mf.get("patch"):
                with AppliedPatch(root, mf["patch"]):
                    code, out, secs = run_shell(cmd, root, timeout, seed, src_env)
            else:
                code, out, secs = run_shell(cmd, root, timeout, seed, src_env)
        except VerikitError as e:
            if e.code == ERROR:
                raise
            print(f"   [FAIL] must fail: {why}\n          {e}")
            result["steps"].append({"what": f"must fail: {why}", "ok": False, "exit": None, "output": str(e)})
            result["ok"] = False
            continue
        matched = bool(re.search(mf["match"], out))
        caught = code not in (0, 124) and matched
        reason = "" if caught else (" - it PASSED, so this check cannot see that break" if code == 0
                                    else " - it timed out" if code == 124
                                    else " - it failed, but not with the expected message")
        print(f"   {'[ok]  ' if caught else '[FAIL]'} must fail: {why} ({secs:.1f}s, exit {code}){reason}")
        if not caught or verbose:
            print(tail(out))
        result["steps"].append({"what": f"must fail: {why}", "ok": caught, "exit": code,
                                "seconds": round(secs, 2), "output": tail(out, 60)})
        result["ok"] &= caught
    return result


def changed_checks(root: Path, checks: list[dict], base: str) -> list[dict]:
    """Checks whose covered files (or any verification file) changed. With `base`, against
    base...HEAD plus uncommitted edits (for PRs); without, against each check's sync commit."""
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
            # other repositories have no PR base here: compare them with their sync commit
            other = [f for s in source_globs(c) for f in _source_stale(root, c, s)[1] if not f.startswith(MISSING)]
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
    return FAILED if bad else OK


# --------------------------------------------------------------------------- stale / sync / list

RANK = {"synced": 0, "stale": 1, "never-synced": 2, "unknown": 3}


def _local_stale(root: Path, check: dict) -> tuple[str, list[str]]:
    specs = pathspecs(check)
    _, dirty = git(root, "status", "--porcelain", "--", *specs)
    dirty_files = [line[3:] for line in dirty.splitlines() if line.strip()]
    sync = check.get("synced_commit", "")
    if not sync:
        return "never-synced", dirty_files
    code, out = git(root, "diff", "--name-only", f"{sync}..HEAD", "--", *specs)
    if code != 0:
        return "unknown", [f"(git: {out})"]
    changed = sorted(set(out.splitlines()) | set(dirty_files))
    return ("stale" if changed else "synced"), changed


def _source_stale(root: Path, check: dict, name: str) -> tuple[str, list[str]]:
    """Drift in another repository, against the commit of it recorded at the last sync."""
    src = source_dir(root, check, name)
    if src is None:
        return "unknown", [f"{MISSING}{name}: no git checkout at {check['_sources'][name]['path']}; "
                           f"clone it there or set {source_env(name)})"]
    sha = check.get("synced_sources", {}).get(name, "")
    code, files = _source_changes(src, source_globs(check)[name], sha or None)
    if code != 0:
        return "unknown", [f"{name}: (git: {files[0]})"]
    files = [f"{name}:{f}" for f in files]
    if not sha:
        return "never-synced", files
    return ("stale" if files else "synced"), files


def stale_info(root: Path, check: dict) -> tuple[str, list[str]]:
    """(status, changed files). status: synced | stale | never-synced | unknown (worst wins).
    Files in other repositories are shown as `name:path`."""
    status, changed = _local_stale(root, check)
    for name in source_globs(check):
        s, files = _source_stale(root, check, name)
        status = s if RANK[s] > RANK[status] else status
        changed = [*changed, *files]
    return status, changed


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
        print(f"{status:13} {c['id']}  (sync {c.get('synced_commit') or '-'})")
        for f in files:
            print(f"              changed: {f}")
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
            print(f"    source:  {name} = {src or c['_sources'][name]['path'] + ' (NOT checked out)'}; "
                  f"sync {c['synced_sources'].get(name) or '-'}")
        print(f"    run:     {c['run']}")
        print(f"    variants:{len(c['must_fail']):>2} must_fail")
        print(f"    sync:    {c.get('synced_commit') or '-'} ({status});  last local pass: "
              f"{last.get('at', 'never')}{'' if fresh or not last else ' (code changed since)'}")
    return OK


def set_check_value(text: str, check_id: str, key: str, value: str, after: str = "id") -> str:
    """Rewrite one `key = value` line of one check (TOML value given as text), keeping every
    other byte and line ending as is. A missing line is inserted after the `after` line."""
    nl = "\r\n" if "\r\n" in text else "\n"
    blocks = re.split(r"(?m)^(?=\[\[check\]\]|\[sources\.)", text)
    for i, block in enumerate(blocks):
        if block.startswith("[[check]]") and re.search(rf'(?m)^id\s*=\s*"{re.escape(check_id)}"\s*$', block):
            line = f"{key} = {value}"
            if re.search(rf"(?m)^{key}\s*=", block):
                blocks[i] = re.sub(rf"(?m)^{key}\s*=[^\r\n]*", lambda _: line, block, count=1)
            else:
                anchor = rf'(?m)^({after}\s*=[^\r\n]*)'
                if not re.search(anchor, block):
                    anchor = rf'(?m)^(id\s*=\s*"{re.escape(check_id)}"[^\r\n]*)'
                blocks[i] = re.sub(anchor, lambda mm: f"{mm.group(1)}{nl}{line}", block, count=1)
            return "".join(blocks)
    die(f"check {check_id!r} not found in manifest")


def set_synced_commit(text: str, check_id: str, sha: str) -> str:
    return set_check_value(text, check_id, "synced_commit", f'"{sha}"')


def cmd_sync(args) -> int:
    root = find_root(args.root)
    recover_pending(root)
    m = load_manifest(root)
    code, sha = git(root, "rev-parse", "--short=12", "HEAD")
    if code != 0:
        die("sync needs a git repository with at least one commit")
    for c in select(m["check"], args.ids):
        _, dirty = git(root, "status", "--porcelain", "--", *pathspecs(c))
        if dirty.strip():
            die(f"{c['id']}: covered files have uncommitted changes; commit first so the sync "
                f"commit describes exactly what was checked:\n{dirty}")
        other_shas = {}
        for name, globs in source_globs(c).items():  # other repositories: clean, then record HEAD
            src = source_dir(root, c, name)
            if src is None:
                die(f"{c['id']}: it covers files in {name!r}, but there is no git checkout at "
                    f"{c['_sources'][name]['path']}. Clone it there or set {source_env(name)}.")
            _, edits = _source_changes(src, globs, None)
            if edits:
                die(f"{c['id']}: covered files in {name} ({src}) have uncommitted changes; commit them "
                    f"there first: {', '.join(edits)}")
            other_shas[name] = git(src, "rev-parse", "--short=12", "HEAD")[1]
        r = run_check(root, c, args.timeout, False, args.seed)
        if not r["ok"]:
            die(f"{c['id']} does not pass, so its sync commit stays at {c.get('synced_commit') or '-'}", FAILED)
        path = root / MANIFEST_REL
        text = set_synced_commit(read_keep(path), c["id"], sha)
        if other_shas:
            table = "{ " + ", ".join(f'{n} = "{s}"' for n, s in sorted(other_shas.items())) + " }"
            text = set_check_value(text, c["id"], "synced_sources", table, after="synced_commit")
        write_keep(path, text)
        state = read_state(root)  # this rerun passed: record it, so `list` and the hook see it
        state[c["id"]] = {"fingerprint": fingerprint(root, c), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        write_state(root, state)
        print(f"   synced {c['id']} -> {sha}. Commit {MANIFEST_REL.as_posix()} to record it.")
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


def cmd_hook(args) -> int:
    """Stop hook. Blocks finishing while covered code differs from its sync commit and no
    passing run has seen that exact content; warns once when a passing check is stale.
    Never blocks twice in a row (stop_hook_active)."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = {}
    if payload.get("stop_hook_active"):
        return OK
    project = Path(payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd())
    lines: list[str] = []
    for root in manifest_roots(project.resolve()):
        try:
            recover_pending(root)
            m = load_manifest(root)
        except VerikitError as e:
            lines.append(f"verikit ({root}): {e}")
            continue
        state = read_state(root)
        unchecked, unconfirmed = [], []
        for c in m["check"]:
            _, changed = stale_info(root, c)
            # a repository this developer has not checked out is not a reason to block them
            if not [f for f in changed if not f.startswith(MISSING)]:
                continue
            fp = fingerprint(root, c)
            entry = state.get(c["id"], {})
            if entry.get("fingerprint") != fp:
                unchecked.append(c)
            elif entry.get("stale_warned") != fp:
                unconfirmed.append(c)
                state[c["id"]] = {**entry, "stale_warned": fp}
        if unconfirmed:
            write_state(root, state)
        try:
            rel = root.relative_to(project.resolve()).as_posix()
            where = "" if rel == "." else f" (in {rel})"
        except ValueError:
            where = f" (in {root})"
        if unchecked:
            lines.append(f"You changed code that a formal check covers{where}, and no passing run has seen these edits:")
            for c in unchecked:
                lines.append(f"  - {c['id']}: \"{c['rule']}\"  ->  cd {root} && {self_cmd(root)} run {c['id']}")
            lines.append("Run the check(s). If one fails, report the counterexample in plain words; "
                         "do not weaken the rule or the model to make it pass.")
        if unconfirmed:
            lines.append(f"These checks pass{where}, but they describe the code at their sync commit "
                         "and the covered code has changed since:")
            for c in unconfirmed:
                lines.append(f"  - {c['id']} (sync {c.get('synced_commit') or 'never'}): \"{c['rule']}\"")
            lines.append("A model does not see code changes. Follow the verify-maintain skill "
                         "(.claude/skills/verify-maintain/SKILL.md): read the diff against the model, update it "
                         f"if the code changed shape, then `{self_cmd(root)} sync <id>`. If you do not, say in "
                         "your report that the model was not re-confirmed.")
    if not lines:
        return OK
    # Claude Code's documented JSON block: the agent receives `reason` and keeps working.
    # (The app shows its own "Stop hook" notice to the user whenever a Stop hook blocks.)
    print(json.dumps({"decision": "block", "reason": "\n".join(lines)}))
    return OK


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
# harness = ["tests/Payments.Tests/CheckoutVerificationTests.cs"]     # the check's own test files outside verification/
# bounds = "1 order, up to 3 attempts"
# assumes = ["The payment provider deduplicates by idempotency key"]
# synced_commit = ""          # set by `verikit sync <id>`, never by hand
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
                print(f"kept existing .claude/skills/{skill.name} (use --force to overwrite)")
                continue
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(skill, dest)
            print(f"installed skill .claude/skills/{skill.name}")
    if args.hook:
        settings = top / ".claude" / "settings.json"
        data = json.loads(_read(settings) or "{}") if settings.exists() else {}
        hook_cmd = 'python "${CLAUDE_PROJECT_DIR}/' + target.relative_to(top).as_posix() + '" hook'
        stop = data.setdefault("hooks", {}).setdefault("Stop", [])
        if not any("verikit.py" in h.get("command", "") for entry in stop for h in entry.get("hooks", [])):
            stop.append({"hooks": [{"type": "command", "command": hook_cmd, "timeout": 60}]})
            settings.parent.mkdir(exist_ok=True)
            write_keep(settings, json.dumps(data, indent=2) + "\n")
            print("added Stop hook to .claude/settings.json")
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
                        "or vs each check's sync commit when REF is omitted")
    p.add_argument("--ci", action="store_true",
                   help="in CI: on a pull request (GitHub Actions, Azure Pipelines, GitLab) run only the "
                        "checks whose files changed vs the target branch; otherwise run every check")
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--seed", type=int, help="seed exported as VERIKIT_SEED (default: random, printed)")
    p.add_argument("--verbose", "-v", action="store_true")
    p.add_argument("--json", help="write results as JSON")
    p.add_argument("--junit", help="write results as JUnit XML (for CI test reports)")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("stale", parents=[common], help="checks whose covered code changed since their sync commit")
    p.add_argument("ids", nargs="*")
    p.add_argument("--tag", action="append", default=[])
    p.add_argument("--strict", action="store_true", help="exit 1 when anything is stale (for CI)")
    p.set_defaults(fn=cmd_stale)

    p = sub.add_parser("sync", parents=[common], help="re-run checks, then record HEAD as their sync commit")
    p.add_argument("ids", nargs="+")
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--seed", type=int)
    p.set_defaults(fn=cmd_sync)

    p = sub.add_parser("list", parents=[common], help="show every check")
    p.add_argument("--tag", action="append", default=[])
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("doctor", parents=[common], help="detect projects, test frameworks and tools")
    p.add_argument("--depth", type=int, default=6)
    p.set_defaults(fn=cmd_doctor)

    sub.add_parser("hook", help="Claude Code Stop hook (reads hook JSON on stdin)").set_defaults(fn=cmd_hook)
    sub.add_parser("version", help="print the runner version").set_defaults(fn=lambda a: print(f"verikit {VERSION}") or OK)

    p = sub.add_parser("init", parents=[common], help="scaffold verification/ in this folder")
    p.add_argument("--hook", action="store_true", help="add the Claude Code Stop hook")
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
