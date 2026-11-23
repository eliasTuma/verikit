"""Unit tests for verikit.py. Run: python -m unittest discover -s tests -v   (needs git only)"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
import xml.etree.ElementTree as ET
from unittest import mock
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT))
import verikit  # noqa: E402

CHECK_PY = textwrap.dedent("""
    import sys
    text = open("src/app.txt").read()
    if "BROKEN" in text:
        print("RULE BROKEN: guard missing")
        sys.exit(1)
    print("rule holds")
""")


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


def vk(*args: str, stdin: str = "") -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    old_stdin = sys.stdin
    sys.stdin = io.StringIO(stdin)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = verikit.main(list(args))
    finally:
        sys.stdin = old_stdin
    return code, out.getvalue() + err.getvalue()


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


class Repo:
    """A temp git repo with a service at <repo>/svc whose verification root is svc/."""

    def __init__(self, manifest: str, nested: bool = True):
        self.tmp = Path(tempfile.mkdtemp(prefix="verikit-test-"))
        self.top = self.tmp
        self.root = self.tmp / "svc" if nested else self.tmp
        (self.root / "src").mkdir(parents=True)
        (self.root / "verification").mkdir()
        (self.root / "src" / "app.txt").write_text("guard = on\n", encoding="utf-8")
        (self.root / "check.py").write_text(CHECK_PY, encoding="utf-8")
        (self.root / "verification" / "verification.toml").write_text(textwrap.dedent(manifest), encoding="utf-8")
        for a in (["init", "-q", "-b", "main"], ["config", "user.email", "t@example.invalid"],
                  ["config", "user.name", "t"], ["config", "core.autocrlf", "false"]):
            git(self.top, *a)
        self.commit("init")

    def commit(self, msg: str) -> None:
        git(self.top, "add", "-A")
        git(self.top, "commit", "-qm", msg, "--allow-empty")

    def make_patch(self) -> None:
        """Broken variant: a patch (relative to the verification root) that breaks the guard."""
        app = self.root / "src" / "app.txt"
        app.write_text("guard = BROKEN\n", encoding="utf-8")
        diff = git(self.root, "diff", "--relative", "--", "src/app.txt")
        git(self.root, "checkout", "--", "src/app.txt")
        (self.root / "verification" / "break.patch").write_text(diff, encoding="utf-8")
        self.commit("patch")

    def close(self) -> None:
        rmtree(self.tmp)


GOOD = """
    version = 1
    [[check]]
    id = "guard"
    rule = "The guard is always on."
    run = "{python} check.py"
    covers = ["src/**/*.txt"]
    tags = ["core"]
    synced_commit = ""
      [[check.must_fail]]
      why = "guard removed"
      patch = "verification/break.patch"
      match = 'RULE BROKEN'
"""


class TransformCfg(unittest.TestCase):
    CFG = "\\* comment\nSPECIFICATION Spec\nCONSTANTS\n    N = 3\n    Fix = TRUE\nINVARIANTS\n    TypeOK\n    Safe\n"

    def test_overrides_constant_in_section(self):
        out = verikit.transform_cfg(self.CFG, {"Fix": "FALSE"}, None)
        self.assertIn("Fix = FALSE", out)
        self.assertIn("N = 3", out)
        self.assertIn("Safe", out)

    def test_inline_constant_and_missing_constant(self):
        out = verikit.transform_cfg("SPECIFICATION Spec\nCONSTANT N = 3\n", {"N": "5", "M": "2"}, None)
        self.assertIn("CONSTANT N = 5", out)
        self.assertIn("M = 2", out)

    def test_replaces_invariants(self):
        out = verikit.transform_cfg(self.CFG, {}, ["Witness"])
        self.assertNotIn("Safe", out)
        self.assertNotIn("TypeOK", out)
        self.assertTrue(out.rstrip().endswith("INVARIANTS\n    Witness"))
        self.assertIn("N = 3", out)


class TlcParsing(unittest.TestCase):
    OUT = textwrap.dedent("""\
        Warning: The variable paid was changed while it is specified as UNCHANGED at
        line 9, col 62 to line 9, col 65 of module Vac
        (Use the -nowarning option to disable this warning.)
        Error: Invariant NeverChargedTwice is violated.
        Error: The behavior up to this point is:
        State 1: <Initial predicate>
        /\\ charges = 0
        /\\ client = "sending"

        State 2: <SendCharge line 38, col 5 to line 43, col 46 of module Checkout>
        /\\ charges = 1
        /\\ client = "waiting"
        """)

    def test_trace(self):
        trace = verikit.parse_tlc_trace(self.OUT)
        self.assertEqual([a for a, _ in trace], ["Initial state", "SendCharge"])
        self.assertEqual(trace[1][1]["charges"], "1")

    def test_warnings_joined_once(self):
        w = verikit.tlc_warnings(self.OUT + self.OUT)
        self.assertEqual(len(w), 1)
        self.assertIn("specified as UNCHANGED at line 9", w[0])


class Manifest(unittest.TestCase):
    def test_must_fail_requires_match(self):
        r = Repo(GOOD.replace("match = 'RULE BROKEN'", ""))
        try:
            code, out = vk("run", "--root", str(r.root))
            self.assertEqual(code, 2)
            self.assertIn("has no `match`", out)
        finally:
            r.close()

    def test_set_synced_commit(self):
        text = '[[check]]\nid = "a"\nsynced_commit = ""\n[[check]]\nid = "b"\n'
        out = verikit.set_synced_commit(text, "b", "abc123")
        self.assertIn('id = "b"\nsynced_commit = "abc123"', out)
        self.assertIn('id = "a"\nsynced_commit = ""', out)


class RunAndPatches(unittest.TestCase):
    def setUp(self):
        self.r = Repo(GOOD)
        self.r.make_patch()

    def tearDown(self):
        self.r.close()

    def test_patch_variant_in_nested_root_is_caught_and_reverted(self):
        junit = self.r.tmp.parent / f"{self.r.tmp.name}-junit.xml"   # outside the repo
        self.addCleanup(lambda: junit.unlink(missing_ok=True))
        code, out = vk("run", "--root", str(self.r.root), "--junit", str(junit), "--seed", "3")
        self.assertEqual(code, 0, out)
        self.assertIn("[ok]   must fail: guard removed", out)
        self.assertIn("seed 3", out)
        self.assertEqual(git(self.r.top, "status", "--porcelain").strip(), "")
        suite = ET.parse(junit).getroot()
        self.assertEqual(suite.get("failures"), "0")
        self.assertEqual(suite.get("tests"), "2")

    def test_variant_that_passes_is_a_failure(self):
        (self.r.root / "check.py").write_text("print('always fine')\n", encoding="utf-8")
        code, out = vk("run", "--root", str(self.r.root))
        self.assertEqual(code, 1)
        self.assertIn("it PASSED, so this check cannot see that break", out)

    def test_crash_does_not_count_as_failing(self):
        (self.r.root / "check.py").write_text(
            "import sys\nt=open('src/app.txt').read()\nsys.exit(3 if 'BROKEN' in t else 0)\n", encoding="utf-8")
        code, out = vk("run", "--root", str(self.r.root))
        self.assertEqual(code, 1)
        self.assertIn("not with the expected message", out)

    def test_patch_that_changes_nothing_is_reported(self):
        # an empty-effect patch: paths relative to the git top level instead of the root
        bad = (self.r.root / "verification" / "break.patch").read_text(encoding="utf-8").replace(
            "a/src/app.txt", "a/svc/src/app.txt").replace("b/src/app.txt", "b/svc/src/app.txt")
        (self.r.root / "verification" / "break.patch").write_text(bad, encoding="utf-8")
        code, out = vk("run", "--root", str(self.r.root))
        self.assertEqual(code, 1)
        self.assertIn("does not apply", out)
        self.assertEqual((self.r.root / "src" / "app.txt").read_text(encoding="utf-8"), "guard = on\n")

    def test_recovers_patch_left_by_interrupted_run(self):
        patch = self.r.root / "verification" / "break.patch"
        git(self.r.top, "apply", "--directory=svc", str(patch))
        verikit.state_path(self.r.root, verikit.PENDING).write_text(
            json.dumps({"patch": str(patch), "rel": "verification/break.patch", "at": 0}), encoding="utf-8")
        code, out = vk("list", "--root", str(self.r.root))
        self.assertEqual(code, 0, out)
        self.assertIn("reverted broken-variant patch", out)
        self.assertEqual((self.r.root / "src" / "app.txt").read_text(encoding="utf-8"), "guard = on\n")

    def test_revert_restores_exact_bytes_under_autocrlf(self):
        # LF file on disk + core.autocrlf=true: git apply writes CRLF, so `apply -R` alone
        # cannot give the original bytes back. verikit must restore them exactly.
        app = self.r.root / "src" / "app.txt"
        app.write_bytes(b"guard = on\nline two\n")
        self.r.commit("lf file")
        patch = self.r.root / "verification" / "break.patch"
        patch.write_bytes(b"--- a/src/app.txt\n+++ b/src/app.txt\n@@ -1,2 +1,2 @@\n-guard = on\n"
                          b"+guard = BROKEN\n line two\n")
        self.r.commit("lf patch")
        git(self.r.top, "config", "core.autocrlf", "true")
        code, out = vk("run", "--root", str(self.r.root))
        self.assertEqual(code, 0, out)
        self.assertEqual(app.read_bytes(), b"guard = on\nline two\n")
        self.assertFalse((self.r.root / verikit.STATE_DIR / verikit.PENDING).exists())

    def test_recovery_restores_exact_bytes_from_backup(self):
        app = self.r.root / "src" / "app.txt"
        original = app.read_bytes()
        with self.assertRaises(KeyboardInterrupt):      # killed while the variant is applied
            with verikit.AppliedPatch(self.r.root, "verification/break.patch"):
                self.assertIn(b"BROKEN", app.read_bytes())
                raise KeyboardInterrupt
        self.assertEqual(app.read_bytes(), original)    # __exit__ ran
        # a hard kill skips __exit__: simulate it, then the next command must restore
        p = verikit.AppliedPatch(self.r.root, "verification/break.patch").__enter__()
        self.assertIn(b"BROKEN", app.read_bytes())
        del p
        code, out = vk("list", "--root", str(self.r.root))
        self.assertEqual(code, 0, out)
        self.assertIn("reverted broken-variant patch", out)
        self.assertEqual(app.read_bytes(), original)

    def test_tag_filter(self):
        code, out = vk("run", "--root", str(self.r.root), "--tag", "nope")
        self.assertEqual(code, 2)
        code, out = vk("run", "--root", str(self.r.root), "--tag", "core")
        self.assertEqual(code, 0, out)


class StaleChangedHook(unittest.TestCase):
    def setUp(self):
        self.r = Repo(GOOD)
        self.r.make_patch()
        code, out = vk("sync", "guard", "--root", str(self.r.root))
        self.assertEqual(code, 0, out)
        self.r.commit("sync")

    def tearDown(self):
        self.r.close()

    def test_synced_then_stale_after_committed_edit(self):
        code, out = vk("stale", "--root", str(self.r.root), "--strict")
        self.assertEqual(code, 0, out)
        (self.r.root / "src" / "notes.txt").write_text("note\n", encoding="utf-8")
        self.r.commit("edit")
        code, out = vk("stale", "--root", str(self.r.root), "--strict")
        self.assertEqual(code, 1)
        self.assertIn("changed: svc/src/notes.txt", out)

    def test_list_is_fresh_right_after_sync(self):
        code, out = vk("list", "--root", str(self.r.root))
        self.assertEqual(code, 0, out)
        self.assertIn("(synced)", out)
        self.assertNotIn("code changed since", out)
        self.assertNotIn("last local pass: never", out)

    def test_harness_edit_makes_check_stale_and_selected(self):
        manifest = self.r.root / "verification" / "verification.toml"
        manifest.write_text(manifest.read_text(encoding="utf-8").replace(
            'covers = ["src/**/*.txt"]', 'covers = ["src/**/*.txt"]\nharness = ["check.py"]'), encoding="utf-8")
        self.r.commit("declare harness")
        vk("sync", "guard", "--root", str(self.r.root))
        self.r.commit("sync")
        self.assertIn("nothing to run", vk("run", "--root", str(self.r.root), "--changed")[1])
        # someone weakens the verification test itself
        (self.r.root / "check.py").write_text(CHECK_PY + "\n# weakened\n", encoding="utf-8")
        self.r.commit("edit harness")
        code, out = vk("stale", "--root", str(self.r.root), "--strict")
        self.assertEqual(code, 1)
        self.assertIn("changed: svc/check.py", out)
        self.assertIn("== guard", vk("run", "--root", str(self.r.root), "--changed")[1])

    def test_sync_keeps_crlf_line_endings(self):
        manifest = self.r.root / "verification" / "verification.toml"
        raw = manifest.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        manifest.write_bytes(raw)
        self.r.commit("crlf manifest")
        code, out = vk("sync", "guard", "--root", str(self.r.root))
        self.assertEqual(code, 0, out)
        after = manifest.read_bytes()
        self.assertEqual(after.count(b"\r\n"), raw.count(b"\r\n"))
        self.assertNotIn(b"\r\r", after)
        self.assertEqual(after.replace(b"\r\n", b"").count(b"\n"), 0)

    def test_changed_against_base_ref(self):
        base = git(self.r.top, "rev-parse", "HEAD").strip()
        code, out = vk("run", "--root", str(self.r.root), "--changed", base)
        self.assertIn("nothing to run", out)
        (self.r.root / "src" / "notes.txt").write_text("more\n", encoding="utf-8")
        self.r.commit("edit")
        code, out = vk("run", "--root", str(self.r.root), "--changed", base)
        self.assertEqual(code, 0, out)
        self.assertIn("== guard", out)

    def test_hook_blocks_then_warns_once_then_allows(self):
        payload = json.dumps({"cwd": str(self.r.top)})

        def hook(stdin: str) -> str | None:
            """The block reason the hook hands Claude Code, or None when it lets the agent stop."""
            code, out = vk("hook", stdin=stdin)
            self.assertEqual(code, 0, out)
            if not out.strip():
                return None
            decision = json.loads(out)
            self.assertEqual(decision["decision"], "block")
            return decision["reason"]

        self.assertIsNone(hook(payload))                              # nothing changed
        (self.r.root / "src" / "notes.txt").write_text("x\n", encoding="utf-8")
        self.r.commit("edit")
        self.assertIn("(in svc)", hook(payload))                      # unchecked edit: blocked
        self.assertIsNone(hook(json.dumps({"cwd": str(self.r.top), "stop_hook_active": True})))
        self.assertEqual(vk("run", "--root", str(self.r.root))[0], 0)
        self.assertIn("describe the code at their sync commit", hook(payload))  # passes, model unconfirmed
        self.assertIsNone(hook(payload))                              # warned once only


CI_VARS = ("GITHUB_ACTIONS", "GITHUB_EVENT_NAME", "GITHUB_BASE_REF", "TF_BUILD", "BUILD_REASON",
           "SYSTEM_PULLREQUEST_TARGETBRANCH", "SYSTEM_PULLREQUEST_TARGETBRANCHNAME",
           "CI_MERGE_REQUEST_TARGET_BRANCH_NAME")


def ci_env(**values: str):
    """os.environ without any real CI variables (the kit's own CI sets them), plus `values`."""
    env = {k: v for k, v in os.environ.items() if k not in CI_VARS}
    return mock.patch.dict(os.environ, {**env, **values}, clear=True)


class CiMode(unittest.TestCase):
    def test_target_branch_for_each_ci(self):
        tb = verikit.ci_target_branch
        self.assertEqual(tb({"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "pull_request",
                             "GITHUB_BASE_REF": "main"})[0], "main")
        self.assertIsNone(tb({"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "push"})[0])
        # Azure Repos spells the target refs/heads/<name>; GitHub-hosted repos spell it <name>
        self.assertEqual(tb({"TF_BUILD": "True", "BUILD_REASON": "PullRequest",
                             "SYSTEM_PULLREQUEST_TARGETBRANCH": "refs/heads/release/2.1"})[0], "release/2.1")
        self.assertEqual(tb({"TF_BUILD": "True", "BUILD_REASON": "PullRequest",
                             "SYSTEM_PULLREQUEST_TARGETBRANCH": "main"})[0], "main")
        self.assertIsNone(tb({"TF_BUILD": "True", "BUILD_REASON": "IndividualCI"})[0])
        self.assertEqual(tb({"CI_MERGE_REQUEST_TARGET_BRANCH_NAME": "develop"})[0], "develop")
        self.assertIsNone(tb({})[0])

    def setUp(self):
        self.r = Repo(GOOD)
        self.r.make_patch()
        vk("sync", "guard", "--root", str(self.r.root))
        self.r.commit("sync")

    def tearDown(self):
        self.r.close()

    def add_origin(self) -> None:
        bare = self.r.tmp.parent / (self.r.tmp.name + "-origin.git")
        git(self.r.tmp.parent, "clone", "-q", "--bare", str(self.r.top), str(bare))
        self.addCleanup(rmtree, bare)
        git(self.r.top, "remote", "add", "origin", str(bare))
        git(self.r.top, "fetch", "-q", "origin")
        git(self.r.top, "checkout", "-q", "-b", "feature")

    def test_pull_request_runs_only_changed_checks(self):
        self.add_origin()
        pr = dict(TF_BUILD="True", BUILD_REASON="PullRequest", SYSTEM_PULLREQUEST_TARGETBRANCH="refs/heads/main")
        (self.r.top / "README.md").write_text("docs only\n", encoding="utf-8")
        self.r.commit("docs")
        with ci_env(**pr):
            code, out = vk("run", "--root", str(self.r.root), "--ci")
        self.assertEqual(code, 0, out)
        self.assertIn("nothing to run", out)
        (self.r.root / "src" / "notes.txt").write_text("covered\n", encoding="utf-8")
        self.r.commit("covered edit")
        with ci_env(**pr):
            code, out = vk("run", "--root", str(self.r.root), "--ci")
        self.assertEqual(code, 0, out)
        self.assertIn("vs origin/main", out)
        self.assertIn("== guard", out)

    def test_missing_base_runs_everything_never_nothing(self):
        with ci_env(GITHUB_ACTIONS="true", GITHUB_EVENT_NAME="pull_request", GITHUB_BASE_REF="main"):
            code, out = vk("run", "--root", str(self.r.root), "--ci")   # no origin remote at all
        self.assertEqual(code, 0, out)
        self.assertIn("Running every check instead", out)
        self.assertIn("== guard", out)

    def test_not_a_pull_request_runs_everything(self):
        with ci_env(GITHUB_ACTIONS="true", GITHUB_EVENT_NAME="push"):
            code, out = vk("run", "--root", str(self.r.root), "--ci")
        self.assertEqual(code, 0, out)
        self.assertIn("== guard", out)
        self.assertEqual(vk("run", "--root", str(self.r.root), "--ci", "--changed")[0], 2)


class Explorer(unittest.TestCase):
    MODEL = str(KIT / "examples" / "explorer" / "checkout_model.py")

    def test_holds_and_breaks(self):
        self.assertEqual(vk("explore", self.MODEL)[0], 0)
        code, out = vk("explore", self.MODEL, "--const", "ReuseKey=False")
        self.assertEqual(code, 1)
        self.assertIn("Shortest counterexample (6 states)", out)
        self.assertEqual(vk("explore", self.MODEL, "--inv", "NeverCharged")[0], 1)


class Doctor(unittest.TestCase):
    def test_classifies_dotnet_and_js(self):
        tmp = Path(tempfile.mkdtemp(prefix="verikit-doc-"))
        try:
            (tmp / "Api").mkdir()
            (tmp / "Api" / "Api.csproj").write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>'
                                                     '<TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>')
            (tmp / "Legacy").mkdir()
            (tmp / "Legacy" / "Legacy.csproj").write_text('<Project ToolsVersion="15.0"><PropertyGroup>'
                                                           '<TargetFrameworkVersion>v4.7.2</TargetFrameworkVersion>'
                                                           '</PropertyGroup></Project>')
            (tmp / "web").mkdir()
            (tmp / "web" / "package.json").write_text(json.dumps({"dependencies": {"@angular/core": "17"},
                                                                  "devDependencies": {"jest": "29"}}))
            (tmp / "spa").mkdir()
            (tmp / "spa" / "package.json").write_text(json.dumps({"dependencies": {"react": "18", "express": "4"}}))
            code, out = vk("doctor", "--root", str(tmp))
            self.assertEqual(code, 0)
            self.assertRegex(out, r"\.NET\s+Api/Api\.csproj")
            self.assertIn(".NET Framework (legacy project format)", out)
            self.assertIn("Angular", out)
            self.assertIn("React + Node backend", out)
            self.assertIn("FsCheck", out)
        finally:
            rmtree(tmp)


class Init(unittest.TestCase):
    def test_init_scaffolds_nested_root(self):
        r = Repo(GOOD)
        try:
            (r.root / "verification" / "verification.toml").unlink()
            code, out = vk("init", "--root", str(r.root), "--hook", "--ci", "github")
            self.assertEqual(code, 0, out)
            self.assertTrue((r.root / "verification" / "verikit.py").exists())
            self.assertTrue((r.top / ".claude" / "skills" / "verify-survey" / "SKILL.md").exists())
            settings = json.loads((r.top / ".claude" / "settings.json").read_text(encoding="utf-8"))
            self.assertIn("svc/verification/verikit.py", settings["hooks"]["Stop"][0]["hooks"][0]["command"])
            self.assertIn(".verikit/", (r.root / ".gitignore").read_text(encoding="utf-8"))
            self.assertNotIn(b"\r\n", (r.root / "verification" / "verification.toml").read_bytes())
            self.assertNotIn(b"\r\n", (r.top / ".claude" / "settings.json").read_bytes())
            self.assertTrue((r.top / ".github" / "workflows" / "verify.yml").exists())
            code, out = vk("init", "--root", str(r.root), "--hook")             # idempotent
            self.assertEqual(code, 0)
            settings = json.loads((r.top / ".claude" / "settings.json").read_text(encoding="utf-8"))
            self.assertEqual(len(settings["hooks"]["Stop"]), 1)
        finally:
            r.close()

    def test_init_ci_both_lists_every_root(self):
        r = Repo(GOOD)
        try:
            (r.root / "verification" / "verification.toml").unlink()
            other = r.top / "web"
            other.mkdir()
            self.assertEqual(vk("init", "--root", str(r.root), "--no-skills", "--ci", "both")[0], 0)
            code, out = vk("init", "--root", str(other), "--no-skills", "--ci", "github,azure")
            self.assertEqual(code, 0, out)
            self.assertIn("added root web", out)
            for f in (".github/workflows/verify.yml", "azure-pipelines.verify.yml"):
                line = [x for x in (r.top / f).read_text(encoding="utf-8").splitlines()
                        if x.rstrip().endswith("# verikit:roots")]
                self.assertEqual(len(line), 1, f)
                self.assertIn('["svc", "web"]', line[0], f)
            code, out = vk("init", "--root", str(other), "--no-skills", "--ci", "azure")
            self.assertIn("already runs root web", out)
            with self.assertRaises(SystemExit):                                # argparse rejects it
                vk("init", "--root", str(other), "--ci", "jenkins")
        finally:
            r.close()


if __name__ == "__main__":
    unittest.main()
