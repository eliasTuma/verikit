# .NET and .NET Framework

Status: **ran** with FsCheck 2.16.6 + xUnit 2.9 on `net8.0` and `net48` (Windows), see the
kit's `examples/dotnet-checkout`; in a **legacy (non-SDK) .NET Framework 4.8** solution with
MSTest v2 2.2.10 + packages.config (MSBuild 17.11 + vstest 17.11), see `examples/dotnet-legacy`;
and **Microsoft Coyote 1.7.11** on `net8.0` for real async interleavings, see
`examples/dotnet-coyote`. NUnit is **not exercised** by the kit.

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
run = "{python} verification/legacy_test.py Legacy.sln Legacy.Tests/bin/Release/Legacy.Tests.dll TestCategory=Verification"  # legacy (non-SDK) projects: see below
```

The filter property depends on the framework: an xUnit trait uses `Category=`, MSTest uses
`TestCategory=` (for NUnit, check which property your adapter version filters on). A wrong
property matches nothing, and the runner **exits 0** - a silent pass. Add
`-- RunConfiguration.TreatNoTestsAsError=true` (vstest and `dotnet test` both accept it), and
always watch the check fail with its broken variant before trusting a pass.

## Which tool

| Question | Tool | Notes |
|---|---|---|
| Bad order of steps across services, queues, retries | TLA+ model + `verikit tlc` | Same in every stack. |
| Generated inputs / event orderings on real code | **FsCheck** (`FsCheck` package, netstandard2.0, works on .NET Framework 4.7.2+ and .NET) | Use from C# with `Prop.ForAll(...).Check(config)`. CsCheck is a good alternative on modern .NET only. |
| Real `async`/`Task` interleavings inside one process (check-then-act across an `await`, a missing or too-narrow lock) | **Microsoft Coyote** 1.7.11 systematic testing | **Ran** on net8.0 (`examples/dotnet-coyote`). Requires binary rewriting after build; see [Coyote](#coyote-async-races) below. net48: rewritten by hand once, not wired. |
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

## Legacy (non-SDK) projects

Status: **ran** - `examples/dotnet-legacy` (MSTest v2 2.2.10 + FsCheck 2.16.6, packages.config,
net48; VS 2022 MSBuild 17.11, vstest 17.11.1). xUnit and NUnit inside legacy projects: not exercised.

- **Recognise it:** `<Project ToolsVersion=...>` without `Sdk=`, `<TargetFrameworkVersion>v4.x`,
  a `packages.config`, and source files listed in `<Compile Include>`. `doctor` labels these
  "legacy project format".
- **Toolchain:** `msbuild X.sln -restore -p:RestorePackagesConfig=true -p:Configuration=Release`,
  then `vstest.console.exe <tests.dll> /TestCaseFilter:TestCategory=Verification -- RunConfiguration.TreatNoTestsAsError=true`.
  packages.config restore through MSBuild needs MSBuild 16.5+ (VS 2019 16.5); older installs need
  `nuget.exe restore`. Restore the `.sln`: `packages\` lives next to it.
- **Do not use `dotnet` to restore or test the project.** `dotnet restore` says "Nothing to do"
  for packages.config, and `dotnet test X.sln` / `X.Tests.csproj` on a non-SDK project runs **no
  tests and exits 0**. (`dotnet test <tests.dll> --filter ...` does work on a built net48 dll.)
- **Find the tools with vswhere, not PATH.** Outside a Developer prompt, `msbuild` can resolve to
  `C:\Windows\Microsoft.NET\Framework\v4.0.30319\MSBuild.exe` (MSBuild 4, cannot restore).
- **One-command manifest:** copy `examples/dotnet-legacy/verification/legacy_test.py` (it finds
  MSBuild/vstest with vswhere, rebuilds, runs only the filtered tests, and fails if none ran) and set
  `run = "{python} verification/legacy_test.py <sln> <tests.dll> TestCategory=Verification"`.
- **Wiring a verification test into an existing legacy test project:**
  1. Add the `.cs` file **and** `<Compile Include="CheckoutVerificationTests.cs" />` to the test
     `.csproj`. A file that is not listed is silently not compiled; Visual Studio adds it for you,
     an agent writing files must edit the csproj.
  2. packages.config does not resolve dependencies: add `FsCheck 2.16.6` **and** `FSharp.Core
     4.2.3`, each with a `<Reference>` + `<HintPath>` (`..\packages\FsCheck.2.16.6\lib\net452\FsCheck.dll`,
     `..\packages\FSharp.Core.4.2.3\lib\net45\FSharp.Core.dll`). If the project already uses a newer
     FSharp.Core, add a `bindingRedirect` in `app.config`. Match the HintPath depth to the folder.
  3. The test adapter must reach `bin\` (MSTest.TestAdapter's build imports, or
     xunit.runner.visualstudio); without it vstest finds no tests.
  4. Write C# 7.3 (the .NET Framework default): no records, switch expressions, `using var` or
     nullable annotations, unless the project sets `LangVersion`.
  5. Mark the test `[TestMethod, TestCategory("Verification")]` and read `VERIKIT_SEED`.
- **Rebuild on every run** so a `must_fail` patch is compiled in; pass `-nr:false
  -p:UseSharedCompilation=false` so no MSBuild node or VBCSCompiler process outlives the run.
- **Covers and harness:** in these projects the Compile list and package versions change what is
  tested, so consider the product `.csproj` in `covers` and the test `.csproj` + `packages.config`
  in `harness`.
- **Path length:** `packages\` holds the longest paths (about 140 characters below the solution
  root). Keep the solution root under about 120 characters.

## Coyote (async races)

Status: **ran** - `examples/dotnet-coyote` (Microsoft.Coyote.Test + Microsoft.Coyote.CLI 1.7.11,
xUnit 2, net8.0, Windows). Linux: runs in the kit's CI. net48: rewritten by hand once, not wired.
1.7.11 (March 2024) is the latest release; the repository is active again but has not published
a newer one. net10 test assemblies are not supported by 1.7.11.

- **Pick the tool:**
  - **Coyote** when the promise breaks only if real `async` methods interleave inside one process:
    check-then-act across an `await`, a missing or too-narrow `SemaphoreSlim`/lock, a retry that
    overlaps the first attempt. It runs the unmodified method and chooses which continuation runs
    at each `await`, `Task.Run`, `SemaphoreSlim` call, and lock or collection access.
  - **FsCheck** when the code is (or can be wrapped as) event handlers and the question is which
    event or input comes next. FsCheck cannot pause a real `async` method at an `await`.
  - **A TLA+ model** when the promise spans processes or instances (two service instances, queue
    redelivery, database isolation, webhooks). An in-process guard that Coyote verifies does
    nothing for a second instance: model the instance count, and put
    `assumes = ["single instance"]` on the Coyote check.
- **Wiring:**
  1. At the verification root: `dotnet new tool-manifest`, then
     `dotnet tool install --local Microsoft.Coyote.CLI --version 1.7.11` (keep `rollForward: false`;
     the CLI needs the .NET 8 runtime with ASP.NET Core, which the .NET 8 SDK includes).
  2. Test project: `<PackageReference Include="Microsoft.Coyote.Test" Version="1.7.11" />` (same
     version as the tool).
  3. Next to the csproj, copied to the output: `rewrite.coyote.json` =
     `{"AssembliesPath": ".", "Assemblies": ["Product.dll", "Product.Tests.dll"]}` - only dlls you own.
  4. A target `AfterTargets="Build"` with `Condition="'$(TargetFrameworkIdentifier)' == '.NETCoreApp'"`
     that runs `dotnet tool restore` and `dotnet tool run coyote rewrite "$(TargetDir)rewrite.coyote.json"`
     with `EnvironmentVariables="COYOTE_CLI_TELEMETRY_OPTOUT=1"`. It rewrites `bin/` only, never sources.
  5. The test: `Configuration.Create().WithTestingIterations(N).WithRandomStrategy()
     .WithRandomGeneratorSeed(<uint from VERIKIT_SEED>).WithPartiallyControlledConcurrencyAllowed(false)
     .WithTelemetryEnabled(false)`, then `TestingEngine.Create(config, async () => ...)` and `Run()`.
     Fail with the bug report, seed and trace paths when `TestReport.NumOfFoundBugs > 0`; also
     assert that N schedules ran and that internal errors and uncontrolled invocations are empty.
  6. In the scenario: `Specification.Assert(cond, "{0}", message)` with a message that names the
     rule (`gift card GC-100 redeemed 2 times ...`), so the manifest's `match` finds it.
  7. Manifest: `run = "dotnet test tests/X.Tests --filter Category=Verification"` - the build does the rewrite.
- **Gotchas:**
  - Without rewriting, a pass means nothing: assert both dlls are rewritten before `Run()`.
  - Telemetry is on by default in the CLI and the TestingEngine: turn it off in both.
  - `Specification.Assert(bool, string, params object[])` formats its string; pass the message as
    an argument, or a `{` in it throws.
  - `TryEmitReports` does not create its folder: create it under `AppContext.BaseDirectory` (`bin/`)
    so `verikit run` leaves the tree clean.
  - Fakes should `await Task.Yield()` (or `Task.Delay`, which Coyote does not actually wait out) to
    stand in for I/O.
  - Set the strategy explicitly (the default is a portfolio). This is sampling of schedules: choose
    N by measuring the first failing schedule over many seeds on the broken variant (in the
    example: always within 8 of 1000).
  - Same seed and code, same schedules: `Configuration.WithReproducibleTrace(File.ReadAllText(".trace"))`
    replays a failure under a debugger.
  - The whole test project runs on the rewritten dlls; ordinary tests are unaffected, so the check
    can live in the team's existing test project.
