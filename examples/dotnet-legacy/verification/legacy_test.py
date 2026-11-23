"""Restore, build and test a legacy (non-SDK) .NET Framework solution with Visual Studio's own tools.

    python verification/legacy_test.py <solution.sln> <tests.dll> <vstest filter>

1. MSBuild restores packages.config (-restore -p:RestorePackagesConfig=true; no nuget.exe needed)
   and builds Release. It rebuilds on every run, so a broken-variant patch is always compiled in.
2. vstest.console.exe runs only the tests the filter selects, and fails if it selects none.

MSBuild.exe and vstest.console.exe come from Visual Studio or Build Tools for Visual Studio,
version 16.5 (2019) or newer: older MSBuild cannot restore packages.config. They are found
with vswhere. Set VERIKIT_MSBUILD / VERIKIT_VSTEST to use other copies.
"""
import os
import subprocess
import sys
from pathlib import Path

VSWHERE = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
               "Microsoft Visual Studio", "Installer", "vswhere.exe")


def fail(msg: str) -> None:
    print(f"[legacy_test] ERROR: {msg}", flush=True)
    sys.exit(2)


def locate(env: str, pattern: str, needs: str) -> str:
    if os.environ.get(env):
        return os.environ[env]
    if not VSWHERE.is_file():
        fail(f"{VSWHERE} not found. Install Visual Studio 2019 16.5+ or Build Tools for Visual Studio "
             f"(with {needs}), or set {env}.")
    out = subprocess.run([str(VSWHERE), "-latest", "-products", "*", "-version", "16.5", "-find", pattern],
                         capture_output=True, text=True).stdout.strip().splitlines()
    if not out:
        fail(f"vswhere found no {pattern} in Visual Studio 16.5+. Add {needs} in the Visual Studio "
             f"Installer, or set {env}.")
    return out[0]


def main() -> None:
    if len(sys.argv) != 4:
        fail("usage: python verification/legacy_test.py <solution.sln> <tests.dll> <vstest filter>")
    sln, dll, test_filter = sys.argv[1:]
    msbuild = locate("VERIKIT_MSBUILD", r"MSBuild\**\Bin\MSBuild.exe", "the .NET desktop build tools")
    vstest = locate("VERIKIT_VSTEST", r"**\TestPlatform\vstest.console.exe", "the testing tools")

    # -nr:false and no shared compiler: no MSBuild/VBCSCompiler process outlives the run.
    build = [msbuild, sln, "-restore", "-p:RestorePackagesConfig=true", "-p:Configuration=Release",
             "-nologo", "-v:minimal", "-nr:false", "-p:UseSharedCompilation=false"]
    print("[legacy_test] " + subprocess.list2cmdline(build), flush=True)
    if subprocess.call(build):
        fail("build failed (see above)")

    test = [vstest, dll, f"/TestCaseFilter:{test_filter}", "--", "RunConfiguration.TreatNoTestsAsError=true"]
    print("[legacy_test] " + subprocess.list2cmdline(test), flush=True)
    p = subprocess.run(test, capture_output=True, text=True, errors="replace")
    out = p.stdout + p.stderr
    print(out, flush=True)
    if "No test matches" in out or "No test is available" in out:
        fail(f"no test ran. Check the filter ({test_filter}; MSTest filters on TestCategory=, an xUnit "
             "trait on Category=), that the test file is listed in the test .csproj's <Compile> items, "
             "and that the path is well under 260 characters.")
    sys.exit(p.returncode)


if __name__ == "__main__":
    main()
