"""End-to-end test of verikit on the Node checkout example, set up the way a monorepo is.

    python scripts/e2e.py            # needs git, node/npm, and Java 11+ or Docker

It copies examples/node-checkout into <tmp>/repo/services/checkout (so the verification
root is NOT the git top level), commits it, runs `init`, then asserts the full story:
holds + every broken variant caught -> sync -> a committed regression is caught by the
code check, flagged by `stale`, and blocked by the Stop hook -> working tree restored.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
EXAMPLE = KIT / "examples" / "node-checkout"
PY = sys.executable


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


def sh(cmd: list[str] | str, cwd: Path, expect: int | None = 0, stdin: str | None = None) -> str:
    p = subprocess.run(cmd, cwd=cwd, input=stdin, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", shell=isinstance(cmd, str))
    out = p.stdout + p.stderr
    shown = cmd if isinstance(cmd, str) else " ".join(cmd)
    print(f"$ {shown}  -> exit {p.returncode}")
    if expect is not None and p.returncode != expect:
        print(out)
        raise SystemExit(f"E2E FAILED: expected exit {expect}, got {p.returncode} for: {shown}")
    return out


def must(cond: bool, what: str, out: str = "") -> None:
    if not cond:
        print(out)
        raise SystemExit(f"E2E FAILED: {what}")
    print(f"   ok: {what}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="verikit-e2e-"))
    repo = tmp / "repo"
    svc = repo / "services" / "checkout"
    try:
        shutil.copytree(EXAMPLE, svc, ignore=shutil.ignore_patterns("node_modules", ".verikit"))
        (repo / "README.md").write_text("monorepo fixture\n", encoding="utf-8")
        for args in (["init", "-q", "-b", "main"], ["config", "user.email", "e2e@example.invalid"],
                     ["config", "user.name", "e2e"], ["config", "core.autocrlf", "false"]):
            sh(["git", *args], repo)
        sh(["git", "add", "-A"], repo)
        sh(["git", "commit", "-qm", "fixture"], repo)
        sh("npm install --no-audit --no-fund --silent", svc)

        out = sh([PY, str(KIT / "verikit.py"), "init", "--hook"], svc)
        must((svc / "verification" / "verikit.py").exists(), "init vendored the runner", out)
        settings = json.loads((repo / ".claude" / "settings.json").read_text(encoding="utf-8"))
        cmd = settings["hooks"]["Stop"][0]["hooks"][0]["command"]
        must("services/checkout/verification/verikit.py" in cmd, "hook points at the nested runner", cmd)
        must((repo / ".claude" / "skills" / "verify-create" / "SKILL.md").exists(), "skills installed at the git top level")
        sh(["git", "add", "-A"], repo)
        sh(["git", "commit", "-qm", "add verikit"], repo)

        vk = [PY, "verification/verikit.py"]
        junit = tmp / "junit.xml"
        out = sh([*vk, "run", "--seed", "7", "--junit", str(junit)], svc)
        must("2/2 checks OK" in out, "both checks hold and every broken variant is caught", out)
        must(out.count("[ok]   must fail") == 3, "three broken variants went red", out)
        must("failures=\"0\"" in junit.read_text(encoding="utf-8"), "JUnit report written with 0 failures")
        must(sh(["git", "status", "--porcelain"], repo).strip() == "", "patch variant reverted; tree clean")

        out = sh([*vk, "sync", "checkout-design", "checkout-code", "--seed", "7"], svc)
        sh(["git", "commit", "-qam", "sync verification"], repo)
        out = sh([*vk, "stale"], svc)
        must(out.count("synced ") == 2, "both checks synced", out)
        must("no check covers a changed file" in sh([*vk, "run", "--changed"], svc), "--changed runs nothing when nothing changed")

        # A refactor reintroduces the bug, committed. The model cannot see it; the rest must.
        patch = svc / "verification" / "checkout" / "mutants" / "fresh-key-per-attempt.patch"
        sh(["git", "apply", "--directory=services/checkout", str(patch)], repo)
        sh(["git", "commit", "-qam", "refactor: key per attempt"], repo)
        out = sh([*vk, "run", "--changed", "--seed", "7"], svc, expect=1)
        must("== checkout-design" in out and "== checkout-code" in out, "--changed picks both checks", out)
        must("1/2 checks OK; failing: checkout-code" in out, "design check stays green (blind), code check goes red", out)
        out = sh([*vk, "stale", "--strict"], svc, expect=1)
        must("stale" in out, "stale flags the drift", out)
        out = sh([PY, str(svc / "verification" / "verikit.py"), "hook"], repo, expect=2,
                 stdin=json.dumps({"cwd": str(repo)}))
        must("checkout-code" in out and "services/checkout" in out, "Stop hook blocks and names the nested root", out)
        sh([PY, str(svc / "verification" / "verikit.py"), "hook"], repo, expect=0,
           stdin=json.dumps({"cwd": str(repo), "stop_hook_active": True}))
        print("   ok: hook never blocks twice in a row")

        print("\nE2E PASSED")
        return 0
    finally:
        rmtree(tmp)


if __name__ == "__main__":
    sys.exit(main())
