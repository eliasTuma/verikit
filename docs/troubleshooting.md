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
| `stale` shows `unknown` | The sync commit is not in local history (shallow clone) | Use `fetch-depth: 0` in CI |
| First TLA+ run is slow | Docker image pull and jar download | Cached afterwards in `~/.cache/verikit` |
