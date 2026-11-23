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

    def test_check_id_must_be_a_safe_file_name(self):
        r = Repo(GOOD.replace('id = "guard"', 'id = "../guard"'))   # it names verification/sync/<id>.json
        try:
            code, out = vk("list", "--root", str(r.root))
            self.assertEqual(code, 2)
            self.assertIn("check id", out)
        finally:
            r.close()


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
            with verikit.AppliedVariant(self.r.root, {"patch": "verification/break.patch"}):
                self.assertIn(b"BROKEN", app.read_bytes())
                raise KeyboardInterrupt
        self.assertEqual(app.read_bytes(), original)    # __exit__ ran
        # a hard kill skips __exit__: simulate it, then the next command must restore
        p = verikit.AppliedVariant(self.r.root, {"patch": "verification/break.patch"}).__enter__()
        self.assertIn(b"BROKEN", app.read_bytes())
        del p
        code, out = vk("list", "--root", str(self.r.root))
        self.assertEqual(code, 0, out)
        self.assertIn("reverted broken-variant patch", out)
        self.assertEqual(app.read_bytes(), original)

    def use_edit(self, find: str, replace: str = "guard = BROKEN") -> None:
        manifest = self.r.root / "verification" / "verification.toml"
        manifest.write_text(manifest.read_text(encoding="utf-8").replace(
            'patch = "verification/break.patch"',
            f"edits = [{{ file = \"src/app.txt\", find = '{find}', replace = '{replace}' }}]"), encoding="utf-8")

    def test_find_and_replace_variant_is_caught_and_restored_exactly(self):
        app = self.r.root / "src" / "app.txt"
        app.write_bytes(b"# header\r\nguard = on\r\n# footer\r\n")          # CRLF file, as on Windows
        self.use_edit("guard = on")
        code, out = vk("run", "--root", str(self.r.root))
        self.assertEqual(code, 0, out)
        self.assertIn("[ok]   must fail: guard removed", out)
        self.assertEqual(app.read_bytes(), b"# header\r\nguard = on\r\n# footer\r\n")
        self.assertFalse((self.r.root / verikit.STATE_DIR / verikit.PENDING).exists())

    def test_find_that_matches_nothing_or_twice_fails_the_variant(self):
        app = self.r.root / "src" / "app.txt"
        self.use_edit("guard = enabled")                                     # the code moved on
        code, out = vk("run", "--root", str(self.r.root))
        self.assertEqual(code, 1, out)
        self.assertIn("`find` text was not found", out)
        manifest = self.r.root / "verification" / "verification.toml"
        manifest.write_text(manifest.read_text(encoding="utf-8").replace("guard = enabled", "guard = on"),
                            encoding="utf-8")
        app.write_text("guard = on\nguard = on\n", encoding="utf-8")          # ambiguous
        code, out = vk("run", "--root", str(self.r.root))
        self.assertEqual(code, 1, out)
        self.assertIn("found 2 times", out)
        self.assertEqual(app.read_text(encoding="utf-8"), "guard = on\nguard = on\n")

    def test_edit_without_find_is_rejected_when_the_manifest_loads(self):
        self.use_edit("")
        code, out = vk("list", "--root", str(self.r.root))
        self.assertEqual(code, 2)
        self.assertIn("each edit needs", out)

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
        self.assertIn("changed: src/notes.txt", out)
        self.assertRegex(out, r"review:  git log -p [0-9a-f]{7,}\.\.HEAD")   # a base that is always reachable

    def test_list_is_fresh_right_after_sync(self):
        code, out = vk("list", "--root", str(self.r.root))
        self.assertEqual(code, 0, out)
        self.assertIn("sync:    synced", out)
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
        self.assertIn("changed: check.py", out)
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
        self.assertIsNone(hook(self.r.top, "SessionStart"))           # nothing stale to mention
        self.assertIsNone(hook(self.r.top))                           # nothing changed
        (self.r.root / "src" / "notes.txt").write_text("x\n", encoding="utf-8")
        self.r.commit("edit")
        self.assertIn("(in svc)", hook(self.r.top))                   # unchecked edit: blocked
        self.assertIsNone(hook(self.r.top, stop_hook_active=True))
        self.assertEqual(vk("run", "--root", str(self.r.root))[0], 0)
        self.assertIn("sync record describes older content", hook(self.r.top))  # passes, model unconfirmed
        self.assertIsNone(hook(self.r.top))                           # reminded once only

    def test_editing_the_checks_own_model_files_is_drift(self):
        patch = self.r.root / "verification" / "break.patch"
        patch.write_bytes(patch.read_bytes() + b"\n")                 # stands for an edited model or mutant
        code, out = vk("stale", "--root", str(self.r.root), "--strict")
        self.assertEqual(code, 1, out)
        self.assertIn("changed: verification/break.patch", out)


def hook(cwd: Path, event: str = "Stop", session: str = "s1", **extra) -> str | None:
    """Run the Claude Code hook; return the block reason (Stop) or the context text
    (SessionStart), or None when it says nothing."""
    code, out = vk("hook", stdin=json.dumps({"cwd": str(cwd), "hook_event_name": event,
                                             "session_id": session, **extra}))
    assert code == 0, out
    out = "\n".join(line for line in out.splitlines() if not line.startswith("verikit: warning"))
    if not out.strip():
        return None
    if event == "Stop":
        decision = json.loads(out)
        assert decision["decision"] == "block", out
        return decision["reason"]
    return out


class SyncSurvivesHistoryRewrites(unittest.TestCase):
    """The recorded sync must not depend on a commit staying reachable: squash and rebase
    merges throw the branch's commits away, and CI often clones without history."""

    def setUp(self):
        self.r = Repo(GOOD)
        self.r.make_patch()

    def tearDown(self):
        self.r.close()

    def feature_synced_then_merged(self, how: str) -> None:
        git(self.r.top, "checkout", "-q", "-b", "feature")
        (self.r.root / "src" / "notes.txt").write_text("feature work\n", encoding="utf-8")
        self.r.commit("feature work")
        self.assertEqual(vk("sync", "guard", "--root", str(self.r.root))[0], 0)
        self.r.commit("sync")
        git(self.r.top, "checkout", "-q", "main")
        if how == "squash":
            git(self.r.top, "merge", "-q", "--squash", "feature")
            git(self.r.top, "commit", "-qm", "feature (squashed)")
        else:  # rebase: same content, new commit ids
            (self.r.top / "README.md").write_text("main moved on\n", encoding="utf-8")
            self.r.commit("unrelated change on main")
            git(self.r.top, "checkout", "-q", "feature")
            git(self.r.top, "rebase", "-q", "main")
            git(self.r.top, "checkout", "-q", "main")
            git(self.r.top, "merge", "-q", "--ff-only", "feature")
        git(self.r.top, "branch", "-q", "-D", "feature")

    def fresh_clone(self, *clone_args: str) -> Path:
        dest = self.r.tmp.parent / (self.r.tmp.name + "-clone")
        self.addCleanup(rmtree, dest)
        if dest.exists():
            rmtree(dest)
        git(self.r.tmp.parent, "clone", "-q", "--no-local", *clone_args, self.r.top.as_uri(), str(dest))
        return dest

    def assert_synced_and_quiet(self, clone: Path) -> None:
        code, out = vk("stale", "--root", str(clone / "svc"), "--strict")
        self.assertEqual(code, 0, out)
        self.assertIn("synced", out)
        self.assertNotIn("unknown", out)
        self.assertIsNone(hook(clone, "SessionStart"))        # nothing stale to mention
        self.assertIsNone(hook(clone))                        # and nothing to block

    def test_squash_merge_then_fresh_clone(self):
        self.feature_synced_then_merged("squash")
        self.assert_synced_and_quiet(self.fresh_clone())

    def test_rebase_merge_then_fresh_clone(self):
        self.feature_synced_then_merged("rebase")
        self.assert_synced_and_quiet(self.fresh_clone())

    def test_shallow_clone_needs_no_history(self):
        self.feature_synced_then_merged("squash")
        self.assert_synced_and_quiet(self.fresh_clone("--depth", "1"))

    def test_sync_does_not_rewrite_the_manifest_and_merges_cleanly(self):
        manifest = self.r.root / "verification" / "verification.toml"
        before = manifest.read_bytes()
        self.assertEqual(vk("sync", "guard", "--root", str(self.r.root))[0], 0)
        self.assertEqual(manifest.read_bytes(), before)
        record = self.r.root / "verification" / "sync" / "guard.json"
        self.assertTrue(record.is_file())                     # under verification/: CODEOWNERS covers it
        self.assertNotIn(b"\r\n", record.read_bytes())

    def test_crlf_and_lf_checkouts_record_the_same_content(self):
        notes = self.r.root / "src" / "notes.txt"
        notes.write_bytes(b"first line\nsecond line\n")
        self.assertEqual(vk("sync", "guard", "--root", str(self.r.root))[0], 0)
        notes.write_bytes(b"first line\r\nsecond line\r\n")    # the same commit checked out with autocrlf
        code, out = vk("stale", "--root", str(self.r.root), "--strict")
        self.assertEqual(code, 0, out)

    def test_wording_edits_in_the_manifest_do_not_make_a_check_stale(self):
        self.assertEqual(vk("sync", "guard", "--root", str(self.r.root))[0], 0)
        manifest = self.r.root / "verification" / "verification.toml"
        manifest.write_text(manifest.read_text(encoding="utf-8").replace("guard removed", "the guard is removed"),
                            encoding="utf-8")
        self.assertEqual(vk("stale", "--root", str(self.r.root), "--strict")[0], 0)

    def test_generated_ignored_files_do_not_count_as_covered(self):
        (self.r.top / ".gitignore").write_text("obj/\n", encoding="utf-8")
        self.r.commit("ignore obj")
        self.assertEqual(vk("sync", "guard", "--root", str(self.r.root))[0], 0)
        (self.r.root / "src" / "obj").mkdir()
        (self.r.root / "src" / "obj" / "generated.txt").write_text("build output\n", encoding="utf-8")
        self.assertEqual(vk("stale", "--root", str(self.r.root), "--strict")[0], 0)

    def test_record_from_0_3_is_still_read_and_never_assumed_synced(self):
        sha = git(self.r.top, "rev-parse", "--short=12", "HEAD").strip()
        manifest = self.r.root / "verification" / "verification.toml"
        text = manifest.read_text(encoding="utf-8")
        manifest.write_text(text.replace('synced_commit = ""', f'synced_commit = "{sha}"'), encoding="utf-8")
        self.r.commit("0.3-style sync")
        code, out = vk("stale", "--root", str(self.r.root), "--strict")
        self.assertEqual(code, 0, out)                        # reachable and unchanged: synced
        manifest.write_text(text.replace('synced_commit = ""', 'synced_commit = "0123456789ab"'), encoding="utf-8")
        code, out = vk("stale", "--root", str(self.r.root), "--strict")
        self.assertEqual(code, 1, out)                        # unreachable: unknown, with the way out
        self.assertIn("unknown", out)
        self.assertIn("sync guard", out)


SERVICE_JS = """\
import { log } from './log.js';

export function helper(x) {
  return x + 1;
}

export async function payForOrder(order, gateway) {
  const key = `order-${order.id}`;
  if (isPaid(order)) { return order.receipt; }
  return gateway.charge(order, key);
}

payForOrder({ id: 1 }, null);
"""

SERVICE_CS = """\
public class CheckoutService {
    [Authorize]
    public async Task<Receipt> PayForOrder(Order order) {
        var key = "order-" + order.Id;   // { not a brace
        return await _gateway.Charge(order, key);
    }

    public Task<Receipt> PayForOrder(Order order, int retries) => PayForOrder(order);

    public void Other() { PayForOrder(null); var s = "}"; }
}
"""


class AnchoredCovers(unittest.TestCase):
    """`covers = ["file#Function"]` watches one function, so unrelated edits in a big file are
    not drift. A wrong region must never read as "synced"."""

    def test_region_is_the_declaration_not_its_call_sites(self):
        region = verikit.extract_region(SERVICE_JS, "payForOrder", ".js")
        self.assertTrue(region.startswith("export async function payForOrder"))
        self.assertTrue(region.rstrip().endswith("}"))
        self.assertNotIn("payForOrder({ id: 1 }", region)
        self.assertNotIn("helper", region)

    def test_overloads_and_attributes_are_all_included(self):
        region = verikit.extract_region(SERVICE_CS, "PayForOrder", ".cs")
        self.assertIn("[Authorize]", region)                 # an attribute change is a behaviour change
        self.assertIn("int retries) => PayForOrder(order);", region)
        self.assertNotIn("Other()", region)

    def test_python_and_marker_comments(self):
        py = "import os\n\n@cached\ndef pay(order):\n    key = order.id\n\n    return key\n\ndef other():\n    pay(1)\n"
        self.assertEqual(verikit.extract_region(py, "pay", ".py"),
                         "@cached\ndef pay(order):\n    key = order.id\n\n    return key")
        sql = "SELECT 1;\n-- verikit:begin redeem\nUPDATE c SET x = 1;\n-- verikit:end redeem\nSELECT 2;\n"
        self.assertIn("UPDATE c SET x = 1;", verikit.extract_region(sql, "redeem", ".sql"))
        self.assertNotIn("SELECT 2", verikit.extract_region(sql, "redeem", ".sql"))

    def test_missing_name_is_none_never_empty(self):
        self.assertIsNone(verikit.extract_region(SERVICE_JS, "refundOrder", ".js"))
        self.assertIsNone(verikit.extract_region(SERVICE_JS, "isPaid", ".js"))   # only called, never declared

    def test_edit_outside_is_not_drift_edit_inside_is_and_a_rename_is_loud(self):
        r = Repo(GOOD.replace('covers = ["src/**/*.txt"]', 'covers = ["src/app.txt", "src/service.js#payForOrder"]'))
        try:
            r.make_patch()
            service = r.root / "src" / "service.js"
            service.write_text(SERVICE_JS, encoding="utf-8")
            self.assertEqual(vk("sync", "guard", "--root", str(r.root))[0], 0)
            service.write_text(SERVICE_JS.replace("return x + 1;", "return x + 2;"), encoding="utf-8")
            code, out = vk("stale", "--root", str(r.root), "--strict")
            self.assertEqual(code, 0, out)                                   # another function changed
            service.write_text(SERVICE_JS.replace("gateway.charge(order, key)", "gateway.charge(order)"),
                               encoding="utf-8")
            code, out = vk("stale", "--root", str(r.root), "--strict")
            self.assertEqual(code, 1, out)
            self.assertIn("changed: src/service.js#payForOrder", out)
            service.write_text(SERVICE_JS.replace("function payForOrder", "function chargeOrder"), encoding="utf-8")
            code, out = vk("stale", "--root", str(r.root), "--strict")
            self.assertEqual(code, 1, out)
            self.assertIn("anchor not found", out)
            code, out = vk("sync", "guard", "--root", str(r.root))           # and it cannot be synced away
            self.assertEqual(code, 2, out)
            self.assertIn("no declaration found", out)
        finally:
            r.close()

    def test_anchor_on_a_glob_is_rejected(self):
        r = Repo(GOOD.replace('covers = ["src/**/*.txt"]', 'covers = ["src/**/*.js#pay"]'))
        try:
            code, out = vk("list", "--root", str(r.root))
            self.assertEqual(code, 2)
            self.assertIn("one exact file", out)
        finally:
            r.close()


class HookBlamesOnlyItsSession(unittest.TestCase):
    def setUp(self):
        self.r = Repo(GOOD)
        self.r.make_patch()
        self.assertEqual(vk("sync", "guard", "--root", str(self.r.root))[0], 0)
        self.r.commit("sync")

    def tearDown(self):
        self.r.close()

    def teammate_drift(self) -> None:
        (self.r.root / "src" / "notes.txt").write_text("a teammate's merged change\n", encoding="utf-8")
        self.r.commit("teammate change, no sync")
        shutil.rmtree(self.r.root / ".verikit", ignore_errors=True)   # as in a fresh clone

    def test_drift_already_on_main_is_mentioned_once_not_blamed(self):
        self.teammate_drift()
        note = hook(self.r.top, "SessionStart")
        self.assertIn("guard", note)
        self.assertIn("before this session", note)
        self.assertIsNone(hook(self.r.top))                   # this session edited nothing
        self.assertIsNone(hook(self.r.top, "SessionStart", source="compact"))  # baseline kept, no repeat

    def test_session_that_edits_and_commits_covered_code_is_blocked(self):
        self.assertIsNone(hook(self.r.top, "SessionStart"))
        (self.r.root / "src" / "notes.txt").write_text("the agent's edit\n", encoding="utf-8")
        self.r.commit("agent commits its edit")               # committing must not hide it
        reason = hook(self.r.top)
        self.assertIn("This session changed", reason)
        self.assertIn("run guard", reason)
        self.assertIsNone(hook(self.r.top, stop_hook_active=True))
        self.assertEqual(vk("run", "--root", str(self.r.root))[0], 0)
        self.assertIn("sync guard", hook(self.r.top))         # passes; the record is not updated yet
        self.assertIsNone(hook(self.r.top))                   # said once
        self.assertEqual(vk("sync", "guard", "--root", str(self.r.root))[0], 0)
        self.assertIsNone(hook(self.r.top, session="s2"))

    def test_edit_on_top_of_old_drift_is_this_sessions(self):
        self.teammate_drift()
        hook(self.r.top, "SessionStart")
        (self.r.root / "src" / "app.txt").write_text("guard = on\n# touched\n", encoding="utf-8")
        self.assertIn("This session changed", hook(self.r.top))

    def test_without_a_session_baseline_drift_is_reported_not_let_through(self):
        self.teammate_drift()                                 # no SessionStart ran (hook installed mid-session)
        reason = hook(self.r.top)
        self.assertIn("guard", reason)
        self.assertNotIn("This session changed", reason)      # cannot know whose edit it is
        self.assertIn("earlier change", reason)

    def test_unsynced_check_edited_by_the_session_is_blocked(self):
        (self.r.root / "verification" / "sync" / "guard.json").unlink()
        self.r.commit("never synced")
        hook(self.r.top, "SessionStart")
        (self.r.root / "src" / "notes.txt").write_text("edit\n", encoding="utf-8")
        self.assertIn("This session changed", hook(self.r.top))


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


class ReportAndSummary(unittest.TestCase):
    def setUp(self):
        self.r = Repo(GOOD)
        self.r.make_patch()
        self.root = str(self.r.root)
        self.summary = self.r.tmp.parent / (self.r.tmp.name + "-summary.md")
        self.addCleanup(lambda: self.summary.unlink(missing_ok=True))

    def tearDown(self):
        self.r.close()

    def test_passing_but_unsynced_check_is_never_plain_green(self):
        code, out = vk("run", "--root", self.root, "--summary", str(self.summary))
        self.assertEqual(code, 0, out)
        text = self.summary.read_text(encoding="utf-8")
        self.assertIn("all 1 checked promises hold; 1 check(s) are not confirmed against the current code", text)
        self.assertIn("⚠️ holds, but see sync record", text)
        self.assertNotIn("✅ holds", text)
        self.assertIn("NEVER CONFIRMED", text)
        vk("sync", "guard", "--root", self.root)
        vk("run", "--root", self.root, "--summary", str(self.summary))
        text = self.summary.read_text(encoding="utf-8")
        self.assertIn("✅ holds; 1 broken variant(s) caught", text)
        self.assertNotIn("⚠️", text)

    def test_failure_shows_rule_output_and_replay_command(self):
        (self.r.root / "src" / "app.txt").write_text("guard = BROKEN\n", encoding="utf-8")
        code, out = vk("run", "--root", self.root, "--seed", "77", "--summary", str(self.summary))
        self.assertEqual(code, 1, out)
        text = self.summary.read_text(encoding="utf-8")
        self.assertIn("1 of 1 checked promises FAILED", text)
        self.assertIn("### ❌ `guard`: The guard is always on.", text)
        self.assertIn("RULE BROKEN: guard missing", text)
        self.assertIn("run guard --seed 77", text)

    def test_ci_publishes_to_github_and_azure(self):
        gh = self.r.tmp.parent / (self.r.tmp.name + "-gh-summary.md")
        self.addCleanup(lambda: gh.unlink(missing_ok=True))
        with ci_env(GITHUB_ACTIONS="true", GITHUB_EVENT_NAME="push", GITHUB_STEP_SUMMARY=str(gh)):
            self.assertEqual(vk("run", "--root", self.root, "--ci")[0], 0)
        self.assertIn("## verikit:", gh.read_text(encoding="utf-8"))
        with ci_env(TF_BUILD="True", BUILD_REASON="IndividualCI"):
            code, out = vk("run", "--root", self.root, "--ci")
        self.assertEqual(code, 0, out)
        line = next(x for x in out.splitlines() if x.startswith("##vso[task.uploadsummary]"))
        path = Path(line.split("]", 1)[1])
        self.assertTrue(path.is_absolute() and path.is_file(), line)       # Azure needs an absolute path
        self.assertIn("## verikit:", path.read_text(encoding="utf-8"))

    def test_report_lists_every_promise_and_flags_drift(self):
        vk("sync", "guard", "--root", self.root)
        code, out = vk("report", "--root", self.root)
        self.assertEqual(code, 0, out)
        self.assertIn("## The guard is always on.", out)
        self.assertIn("✅ up to date", out)
        (self.r.root / "src" / "notes.txt").write_text("drift\n", encoding="utf-8")
        page = self.r.tmp.parent / (self.r.tmp.name + "-report.html")
        self.addCleanup(lambda: page.unlink(missing_ok=True))
        code, out = vk("report", "--root", self.root, "--html", str(page))
        self.assertEqual(code, 0, out)
        html = page.read_text(encoding="utf-8")
        self.assertIn("OUT OF DATE", html)
        self.assertIn("1 check(s) need attention", html)
        self.assertNotIn("<td class=ok>", html)


class SavedSeeds(unittest.TestCase):
    """A random run that finds a bug prints its seed; `seed add` keeps it, and every later run
    replays it, so the same bug cannot come back unnoticed."""

    SEEDED_CHECK = textwrap.dedent("""
        import os, sys
        text = open("src/app.txt").read()
        if "BROKEN" in text:
            print("RULE BROKEN: guard missing"); sys.exit(1)
        if "rare bug" in text and os.environ.get("VERIKIT_SEED") == "13":
            print("RULE BROKEN: only this ordering finds it"); sys.exit(1)
        print("rule holds")
    """)

    def setUp(self):
        self.r = Repo(GOOD)
        self.r.make_patch()
        (self.r.root / "check.py").write_text(self.SEEDED_CHECK, encoding="utf-8")
        (self.r.root / "src" / "notes.txt").write_text("rare bug\n", encoding="utf-8")
        (self.r.root / "check.py").write_text(self.SEEDED_CHECK.replace("src/app.txt", "src/notes.txt")
                                              .replace('if "BROKEN" in text', 'if "BROKEN" in open("src/app.txt").read()'),
                                              encoding="utf-8")

    def tearDown(self):
        self.r.close()

    def test_failing_seed_is_kept_and_replayed_until_fixed(self):
        root = str(self.r.root)
        self.assertEqual(vk("run", "--root", root, "--seed", "5")[0], 0)        # most seeds miss the bug
        code, out = vk("run", "--root", root, "--seed", "13")
        self.assertEqual(code, 1, out)
        self.assertIn("seed add guard 13", out)                                  # tells how to keep the case
        code, out = vk("seed", "add", "guard", "13", "--root", root, "--note", "late reply")
        self.assertEqual(code, 0, out)
        self.assertIn("13  # late reply", (self.r.root / "verification" / "seeds" / "guard.txt").read_text(encoding="utf-8"))
        code, out = vk("run", "--root", root, "--seed", "5")
        self.assertEqual(code, 1, out)                                           # now every run finds it
        self.assertIn("[FAIL] holds with saved seed 13", out)
        (self.r.root / "src" / "notes.txt").write_text("fixed\n", encoding="utf-8")
        code, out = vk("run", "--root", root, "--seed", "5")
        self.assertEqual(code, 0, out)
        self.assertIn("[ok]   holds with saved seed 13", out)
        self.assertIn("guard: 13", vk("seed", "list", "--root", root)[1])
        self.assertIn("already saved", vk("seed", "add", "guard", "13", "--root", root)[1])
        self.assertEqual(vk("seed", "add", "nope", "1", "--root", root)[0], 2)


CROSS = GOOD.replace('covers = ["src/**/*.txt"]', 'covers = ["src/**/*.txt", "web:src/**/*.ts"]') + """
    [sources.web]
    path = "../../web"
"""


class CrossRepo(unittest.TestCase):
    """A check in repo A (svc/) whose covers name files in a separate repo B (web/)."""

    def setUp(self):
        self.r = Repo(CROSS)
        self.r.make_patch()
        self.web = self.r.tmp.parent / (self.r.tmp.name + "-web")
        # the manifest says ../../web relative to svc/: point it at our sibling folder by env
        self.env = ci_env(VERIKIT_SOURCE_WEB=str(self.web))
        self.env.start()
        self.addCleanup(self.env.stop)
        (self.web / "src").mkdir(parents=True)
        (self.web / "src" / "pay.ts").write_text("export const pay = 1;\n", encoding="utf-8")
        for a in (["init", "-q", "-b", "main"], ["config", "user.email", "t@example.invalid"],
                  ["config", "user.name", "t"], ["config", "core.autocrlf", "false"]):
            git(self.web, *a)
        self.web_commit("web init")
        self.addCleanup(rmtree, self.web)
        code, out = vk("sync", "guard", "--root", str(self.r.root))
        self.assertEqual(code, 0, out)
        self.r.commit("sync")

    def tearDown(self):
        self.r.close()

    def web_commit(self, msg: str) -> None:
        git(self.web, "add", "-A")
        git(self.web, "commit", "-qm", msg)

    def test_sync_records_the_other_repos_commit(self):
        record = json.loads((self.r.root / "verification" / "sync" / "guard.json").read_text(encoding="utf-8"))
        self.assertIn("web:src/pay.ts", record["files"])            # content, not a commit of that repo
        code, out = vk("stale", "--root", str(self.r.root), "--strict")
        self.assertEqual(code, 0, out)
        self.assertIn("web", vk("list", "--root", str(self.r.root))[1])

    def test_change_in_other_repo_is_drift(self):
        (self.web / "src" / "pay.ts").write_text("export const pay = 2;\n", encoding="utf-8")
        self.web_commit("change pay")
        code, out = vk("stale", "--root", str(self.r.root), "--strict")
        self.assertEqual(code, 1, out)
        self.assertIn("changed: web:src/pay.ts", out)
        self.assertIn("== guard", vk("run", "--root", str(self.r.root), "--changed")[1])
        base = git(self.r.top, "rev-parse", "HEAD").strip()     # a PR in repo A that touches nothing
        self.assertIn("== guard", vk("run", "--root", str(self.r.root), "--changed", base)[1])
        self.assertIn("guard", hook(self.r.top, session="no-baseline"))
        self.assertEqual(vk("sync", "guard", "--root", str(self.r.root))[0], 0)
        self.assertEqual(vk("stale", "--root", str(self.r.root), "--strict")[0], 0)

    def test_uncommitted_edit_in_other_repo_is_drift_too(self):
        (self.web / "src" / "pay.ts").write_text("export const pay = 3;\n", encoding="utf-8")
        code, out = vk("stale", "--root", str(self.r.root), "--strict")
        self.assertEqual(code, 1, out)
        self.assertIn("changed: web:src/pay.ts", out)
        (self.web / "src" / "new.ts").write_text("export const more = 1;\n", encoding="utf-8")   # untracked
        self.assertIn("changed: web:src/new.ts", vk("stale", "--root", str(self.r.root))[1])

    def test_missing_checkout_is_unknown_never_synced(self):
        moved = self.web.with_name(self.web.name + "-moved")
        self.web.rename(moved)
        self.addCleanup(lambda: moved.exists() and moved.rename(self.web))
        code, out = vk("stale", "--root", str(self.r.root), "--strict")
        self.assertEqual(code, 1, out)
        self.assertIn("unknown", out)
        self.assertIn("VERIKIT_SOURCE_WEB", out)
        # a developer without the other repo is not blocked by the hook for that alone
        self.assertIsNone(hook(self.r.top, session="no-baseline"))
        with mock.patch.dict(os.environ, {"VERIKIT_SOURCE_WEB": str(moved)}):
            self.assertEqual(vk("stale", "--root", str(self.r.root), "--strict")[0], 0)

    def test_unknown_source_prefix_is_rejected(self):
        manifest = self.r.root / "verification" / "verification.toml"
        manifest.write_text(manifest.read_text(encoding="utf-8").replace("web:src", "mobile:src"), encoding="utf-8")
        code, out = vk("list", "--root", str(self.r.root))
        self.assertEqual(code, 2)
        self.assertIn("[sources.mobile]", out)


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

    def test_init_upgrades_a_0_3_hook_without_duplicating(self):
        r = Repo(GOOD)
        try:
            settings = r.top / ".claude" / "settings.json"
            settings.parent.mkdir()
            old = 'python "${CLAUDE_PROJECT_DIR}/svc/verification/verikit.py" hook'
            settings.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
                {"type": "command", "command": old, "timeout": 60}]}]}}), encoding="utf-8")
            code, out = vk("init", "--root", str(r.root), "--hook", "--no-skills")
            self.assertEqual(code, 0, out)
            hooks = json.loads(settings.read_text(encoding="utf-8"))["hooks"]
            commands = [h["command"] for event in ("SessionStart", "Stop") for e in hooks[event] for h in e["hooks"]]
            self.assertEqual(len(commands), 2, hooks)             # one per event, the old entry replaced
            self.assertEqual(commands[0], commands[1])
            script = '"${CLAUDE_PROJECT_DIR}/svc/verification/verikit.py"'
            self.assertEqual(commands[0], f'if python3 -c "" >/dev/null 2>&1; then python3 {script} hook; '
                                          f'else python {script} hook; fi')
            before = settings.read_bytes()
            vk("init", "--root", str(r.root), "--hook", "--no-skills")
            self.assertEqual(settings.read_bytes(), before)       # idempotent
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
