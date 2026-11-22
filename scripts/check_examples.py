"""Run every example through the real verikit flow, each in its own fresh git repo.

    python scripts/check_examples.py                 # all examples
    python scripts/check_examples.py dotnet-checkout # one example

For each example: copy to a SHORT temp path (.NET Framework test discovery fails silently
near the 260-character path limit), git init + commit, install dependencies, `verikit init`,
then `verikit run`. Passes only if every check holds, every broken variant is caught, and the
working tree is clean afterwards. Prints a summary table; exit 1 if any example fails.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
EXAMPLES = KIT / "examples"
NEEDS = {  # example -> (setup command or None, executables it needs)
    "node-checkout": ("npm install --no-audit --no-fund --silent", ["node", "npm"]),
    "react-pay-button": ("npm install --no-audit --no-fund --silent", ["node", "npm"]),
    "angular-search-store": ("npm install --no-audit --no-fund --silent", ["node", "npm"]),
    "dotnet-checkout": (None, ["dotnet"]),
}


def rmtree(path) -> None:
    """shutil.rmtree that also removes git's read-only object files on Windows."""
    import stat

    def onerror(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    shutil.rmtree(path, onerror=onerror) if sys.version_info < (3, 12) else shutil.rmtree(path, onexc=onerror)


def sh(cmd, cwd: Path) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", shell=isinstance(cmd, str))
    return p.returncode, p.stdout + p.stderr


def short_tmp() -> Path:
    base = Path(os.environ.get("VERIKIT_SHORT_TMP") or tempfile.gettempdir())
    base.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="ex-", dir=base))
    if len(str(work)) > 120:
        print(f"warning: {work} is long; .NET Framework tests may not be discovered. Set VERIKIT_SHORT_TMP.")
    return work


def check(name: str) -> tuple[str, str]:
    setup, exes = NEEDS[name]
    missing = [e for e in exes if not shutil.which(e)]
    if missing:
        return "SKIPPED", f"missing {', '.join(missing)}"
    work = short_tmp()
    repo = work / name
    try:
        shutil.copytree(EXAMPLES / name, repo, ignore=shutil.ignore_patterns(
            "node_modules", ".verikit", "bin", "obj", "TestResults"))
        for args in (["init", "-q", "-b", "main"], ["config", "user.email", "ci@example.invalid"],
                     ["config", "user.name", "ci"], ["config", "core.autocrlf", "false"]):
            sh(["git", *args], repo)
        sh(["git", "add", "-A"], repo)
        sh(["git", "commit", "-qm", "example"], repo)
        if setup:
            code, out = sh(setup, repo)
            if code:
                return "FAILED", f"setup failed: {out[-400:]}"
        code, out = sh([sys.executable, str(KIT / "verikit.py"), "init", "--no-skills"], repo)
        if code:
            return "FAILED", f"init failed: {out[-400:]}"
        sh(["git", "add", "-A"], repo)
        sh(["git", "commit", "-qm", "verikit"], repo)
        t0 = time.time()
        code, out = sh([sys.executable, "verification/verikit.py", "run", "--seed", "1"], repo)
        summary = re.search(r"(\d+/\d+ checks OK.*)", out)
        variants = out.count("[ok]   must fail")
        if code != 0:
            print(out)
            return "FAILED", summary.group(1) if summary else "run failed"
        _, dirty = sh(["git", "status", "--porcelain"], repo)
        if dirty.strip():
            return "FAILED", f"working tree not clean after run: {dirty.strip()}"
        return "PASSED", f"{summary.group(1)}; {variants} broken variant(s) caught; {time.time() - t0:.0f}s"
    finally:
        rmtree(work)


def main() -> int:
    names = sys.argv[1:] or sorted(NEEDS)
    rows = [(n, *check(n)) for n in names]
    print("\n" + "\n".join(f"{status:8} {name:22} {detail}" for name, status, detail in rows))
    return 1 if any(status == "FAILED" for _, status, _ in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
