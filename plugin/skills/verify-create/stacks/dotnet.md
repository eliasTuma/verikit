# .NET and .NET Framework

Status: **ran** with FsCheck 2.16.6 + xUnit 2.9 on `net8.0` and `net48` (Windows), see the
kit's `examples/dotnet-checkout`. Coyote and NUnit/MSTest variants below are **not exercised**
by the kit yet.

## Where the checks go

In the team's existing test project for the code that makes the promise, not in a new
solution. Mark each verification test so it can run alone and still runs with everything else:

```csharp
[Fact, Trait("Category", "Verification")]        // xUnit
[Test, Category("Verification")]                  // NUnit
[TestMethod, TestCategory("Verification")]        // MSTest
```

Manifest `run` (pick the one the solution uses):

```toml
run = "dotnet test tests/Payments.Tests --filter Category=Verification"            # SDK-style projects
run = "dotnet test tests/Payments.Tests -f net48 --filter Category=Verification"   # one framework only
run = "vstest.console.exe tests/Legacy.Tests/bin/Release/Legacy.Tests.dll /TestCaseFilter:Category=Verification"  # legacy (non-SDK) projects, after msbuild
```

## Which tool

| Question | Tool | Notes |
|---|---|---|
| Bad order of steps across services, queues, retries | TLA+ model + `verikit tlc` | Same in every stack. |
| Generated inputs / event orderings on real code | **FsCheck** (`FsCheck` package, netstandard2.0, works on .NET Framework 4.7.2+ and .NET) | Use from C# with `Prop.ForAll(...).Check(config)`. CsCheck is a good alternative on modern .NET only. |
| Real `async`/`Task` interleavings, thread races | **Microsoft Coyote** systematic testing | Explores task interleavings. Requires binary rewriting (`coyote rewrite`); check which .NET versions the current release supports before using it on Framework projects. Not exercised by the kit. |
| SQL / EF concurrency (two requests, one row) | Integration test with two real connections | Model the isolation level in TLA+ if the promise depends on it. |

## Pattern: drive the real class with generated event orderings

When the code is (or can be wrapped as) a state machine that reacts to events - requests,
timeouts, replies, messages - let FsCheck pick which enabled event happens next:

```csharp
var config = Configuration.QuickThrowOnFailure;
config.MaxNbOfTest = 500;
if (int.TryParse(Environment.GetEnvironmentVariable("VERIKIT_SEED"), out var s))
    config.Replay = FsCheck.Random.StdGen.NewStdGen(s, 0);       // replay with: verikit run --seed N

Prop.ForAll(Arb.From(Gen.Choose(0, 1000).ArrayOf()), Arb.From(Gen.Choose(0, 3)),
        (schedule, maxRetries) => RunOnce(schedule, maxRetries))  // pick enabled[choice % count]
    .Check(config);
```

Throw an exception whose message states the rule (`customer charged 2 times for one purchase;
events: ...`) so the manifest's `match` finds it and the failure reads as a story.

## Pitfalls (seen while building the example)

- **Path length.** .NET Framework test discovery fails silently ("No test is available")
  when the output path nears 260 characters. Keep clones short (`C:\src\repo`) or enable
  Windows long paths. `dotnet test` on `net8.0` is not affected.
- **Building `net48` without the targeting pack:** add
  `<PackageReference Include="Microsoft.NETFramework.ReferenceAssemblies" Version="1.0.3" PrivateAssets="all" />`.
  Running `net48` tests still needs Windows with .NET Framework 4.8.
- **CI:** `net48` tests need a Windows runner. Linux runners can run the `net8.0` target only.
- **Broken-variant patches** for C# files: write them relative to the folder that holds
  `verification/` (`git diff --relative`), one guard per patch.
