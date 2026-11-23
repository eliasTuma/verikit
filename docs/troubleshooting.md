# Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `dotnet test` on `net48`: "No test is available in ...Tests.dll" | The path is near the 260-character Windows limit; .NET Framework test discovery fails silently | Clone to a short path (`C:\src\repo`) or enable long paths. `net8.0` is unaffected. |
| `net48` build: reference assemblies not found | No .NET Framework 4.8 targeting pack | Add `Microsoft.NETFramework.ReferenceAssemblies` with `PrivateAssets="all"` |
| `TLC needs Java 11+ ... or a running Docker daemon` | Neither is installed, or Docker is stopped | Install a JDK (Temurin) or start Docker, or set `VERIKIT_JAVA` to the java executable |
| TOML error: `Unescaped '\' in a string` | A regex in a double-quoted TOML string | Use single quotes: `match = 'VIOLATION\] Rule'` |
| `must fail: ... it PASSED` | The check cannot see that break | Fix the check (model too coarse, test too narrow), never the variant |
| `must fail: ... not with the expected message` | The variant crashed, or the failure message changed | Make the failure message state the rule; check `match` |
| `patch ... does not apply` | The code moved since the patch was written | Regenerate it with `git diff --relative` from the folder holding `verification/` |
| `patch ... applied but changed no file` | Patch paths are relative to the git top level, not the verification root | Regenerate it from the verification root |
| `reverted broken-variant patch ... left applied` | A previous run was interrupted | Nothing to do; verikit restored the files. Check `git diff` if unsure. |
| `[error] ... an action contradicts itself` | A TLA+ action sets a variable it also lists as UNCHANGED | Fix the action; the pass was vacuous |
| The Stop hook blocks | Covered code changed and no passing run has seen it, or a passing check is stale | Run the named check, or re-confirm and `sync`. It never blocks twice in a row. |
| Hook does nothing on Windows without Git Bash | Hooks then run in PowerShell | The `${CLAUDE_PROJECT_DIR}` form that `init` writes works in both shells; re-run `init --hook` if it was edited |
| `stale` shows `unknown` | The sync commit is not in local history (shallow clone) | Use `fetch-depth: 0` (GitHub) / `fetchDepth: 0` (Azure) in CI |
| First TLA+ run is slow | Docker image pull and jar download | Cached afterwards in `~/.cache/verikit` (`VERIKIT_CACHE`) |
| `--ci: ... missing or shares no history with HEAD ... Running every check instead` | Shallow checkout, or the target branch was not fetched | Set `fetch-depth: 0` / `fetchDepth: 0`. verikit still runs every check, so nothing is skipped; it is only slower |
| Azure Repos: no build on pull requests | The YAML `pr:` trigger does not apply to Azure Repos | Add the pipeline as a *Build validation* branch policy on the target branch |
| Azure: TLC fails with an old Java | The image's default `java` is older than 11 | Keep the template's `JavaToolInstaller@0` step (`versionSpec: "21"`, `PreInstalled`) |
| Legacy .NET Framework: the check passes but ran no test | Wrong filter property (MSTest uses `TestCategory=`, xUnit/NUnit `Category=`), or `dotnet test` on a non-SDK project (runs nothing, exit 0) | Use `legacy_test.py` from `examples/dotnet-legacy` (MSBuild + vstest, fails when no test ran) |
| Legacy .NET Framework: new test file never runs | Not listed in the test `.csproj`'s `<Compile Include>` items | Add the `<Compile>` line |
| `dotnet restore`: "Nothing to do" on a legacy solution | packages.config is restored by MSBuild 16.5+ or nuget.exe, not `dotnet` | `msbuild X.sln -restore -p:RestorePackagesConfig=true` |
| `could not revert ... patch` (0.2.x) with `core.autocrlf=true` | `git apply` rewrote LF files as CRLF, so the bytes differed | Fixed in 0.3: verikit restores byte-exact backups. Upgrade by re-running `init` |
| Claude Code shows "Stop hook" / "hook error" while the agent keeps working | That is how the app shows any Stop hook that blocks | Expected. The agent received the reason and is running the named check |
