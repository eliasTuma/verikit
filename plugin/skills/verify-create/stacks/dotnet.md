# .NET and .NET Framework

Status: **ran** with FsCheck 2.16.6 + xUnit 2.9 on `net8.0` and `net48` (Windows), see the
kit's `examples/dotnet-checkout`; and in a **legacy (non-SDK) .NET Framework 4.8** solution with
MSTest v2 2.2.10 + packages.config (MSBuild 17.11 + vstest 17.11), see `examples/dotnet-legacy`.
NUnit is **not exercised** by the kit.

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

The filter property depends on the framework: xUnit traits and NUnit categories use
`Category=`, MSTest uses `TestCategory=`. A wrong property matches nothing, and the runner
**exits 0** - a silent pass. With vstest, always add `-- RunConfiguration.TreatNoTestsAsError=true`.

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
