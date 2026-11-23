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
| `edit of X: the find text was not found` / `found N times` | The guard's text changed, or it is not unique | Update `find` to the guard's current text; add a little surrounding text until it matches exactly once |
| `changed: file#Name (anchor not found): renamed or moved?` | The function a check covers was renamed, moved or deleted | Find where the promise's code lives now, re-read it against the model, fix the cover, then `sync` |
| `[TRACE REJECTED]` | The real code reached a state the model does not allow | Read the listed observations against the code and the model. Either the code has a bug or the model is out of date; fix the one that is wrong |
| `the recording command failed ... so no trace was checked` | The recorder test itself failed | Fix the recorder; it should record, not assert the rule |
| `[isolation] link: X is not a folder` | `run --isolated` links git-ignored dependency folders into the copy, and this one is not installed | Install dependencies in the working tree first (`npm ci`), or remove it from `link` and give `[isolation] setup` |
| `left ...verikit-iso-... in place: it still contains a link` | A link in the throwaway copy could not be removed, so verikit refused to delete the tree | Remove that link by hand (`rmdir` on Windows, `unlink` elsewhere), then the folder |
| `reverted broken-variant patch ... left applied` | A previous run was interrupted | Nothing to do; verikit restored the files. Check `git diff` if unsure. |
| `[error] ... an action contradicts itself` | A TLA+ action sets a variable it also lists as UNCHANGED | Fix the action; the pass was vacuous |
| The Stop hook blocks | This session changed covered code and no passing run has seen it, or a passing check's sync record is older than the code | Run the named check, or re-confirm and `sync`. It never blocks twice in a row. |
| The hook never runs on macOS or Linux (0.3.0) | The 0.3.0 hook command called `python`, which many machines do not have | Re-run `init --hook` with 0.3.1+: the command tries `python3` first, then `python` |
| Hook error "verikit needs Python 3.11 or newer" | The machine's `python3` is older | Install Python 3.11+ and put it first on PATH |
| Hook fails to parse on Windows without Git Bash | Claude Code then runs hooks in PowerShell, and PowerShell cannot parse the `if ...; then ...; fi` form | Install Git for Windows (verikit needs git anyway; Claude Code then uses Git Bash), or shorten the command in `.claude/settings.json` to `python "${CLAUDE_PROJECT_DIR}/verification/verikit.py" hook` |
| `stale` shows `unknown` with "recorded by verikit 0.3 as commit ..." | A 0.3 manifest stores a commit id, and a squash merge, rebase or shallow clone made it unreachable | Run `verikit sync <id>` once with 0.3.1+: it records content in `verification/sync/<id>.json` instead. Delete the old `synced_commit` line |
| `stale` shows `unknown` with "missing source" | A check covers files in another repository that is not checked out here | Clone it at the manifest's `path`, or set `VERIKIT_SOURCE_<NAME>` |
| First TLA+ run is slow | Docker image pull and jar download | Cached afterwards in `~/.cache/verikit` (`VERIKIT_CACHE`) |
| `--ci: ... missing or shares no history with HEAD ... Running every check instead` | Shallow checkout, or the target branch was not fetched | Set `fetch-depth: 0` / `fetchDepth: 0`. verikit still runs every check, so nothing is skipped; it is only slower |
| Azure Repos: no build on pull requests | The YAML `pr:` trigger does not apply to Azure Repos | Add the pipeline as a *Build validation* branch policy on the target branch |
| Azure: TLC fails with an old Java | The image's default `java` is older than 11 | Keep the template's `JavaToolInstaller@0` step (`versionSpec: "21"`, `PreInstalled`) |
| Legacy .NET Framework: the check passes but ran no test | Wrong filter property (MSTest uses `TestCategory=`, xUnit/NUnit `Category=`), or `dotnet test` on a non-SDK project (runs nothing, exit 0) | Use `legacy_test.py` from `examples/dotnet-legacy` (MSBuild + vstest, fails when no test ran) |
| Legacy .NET Framework: new test file never runs | Not listed in the test `.csproj`'s `<Compile Include>` items | Add the `<Compile>` line |
| `dotnet restore`: "Nothing to do" on a legacy solution | packages.config is restored by MSBuild 16.5+ or nuget.exe, not `dotnet` | `msbuild X.sln -restore -p:RestorePackagesConfig=true` |
| `could not revert ... patch` (0.2.x) with `core.autocrlf=true` | `git apply` rewrote LF files as CRLF, so the bytes differed | Fixed in 0.3: verikit restores byte-exact backups. Upgrade by re-running `init` |
| Claude Code shows "Stop hook" / "hook error" while the agent keeps working | That is how the app shows any Stop hook that blocks | Expected. The agent received the reason and is running the named check |
