# dotnet-legacy

The promise **"a customer is never charged twice for one purchase"**, checked in a legacy
.NET Framework 4.8 solution of the kind older enterprise codebases have.

`Billing/CheckoutSession.cs` retries a payment on timeout and sends the same idempotency key
with every retry, so the gateway charges at most once. `Billing.Tests` holds the team's ordinary
MSTest tests and one verification test, `CheckoutVerificationTests`, marked
`[TestCategory("Verification")]`. FsCheck 2.16.6 generates which event happens next (the timer
rings, a request reaches the gateway, a reply arrives) and drives the real `CheckoutSession`
through 500 schedules. The seed comes from `VERIKIT_SEED`.

## What is legacy about it

- **Non-SDK project files.** `ToolsVersion="15.0"`, explicit Debug/Release property groups,
  `<TargetFrameworkVersion>v4.8</TargetFrameworkVersion>`, and `Properties\AssemblyInfo.cs`.
  Every source file is listed in `<Compile Include="...">`. A new test file that is not listed
  there is silently not compiled.
- **`packages.config`** with a `packages\` folder next to the solution. Each package assembly is
  referenced by `<HintPath>..\packages\...`. Dependencies are not resolved, so FsCheck's
  dependency FSharp.Core is listed by hand. It is pinned to 4.2.3, the version FsCheck 2.16.6 was
  built against, so no binding redirect is needed. The test adapter's `.props`/`.targets` are
  imported by hand.
- **MSTest v2** (MSTest.TestFramework / MSTest.TestAdapter 2.2.10) is what Visual Studio's
  "Unit Test Project (.NET Framework)" template writes. That makes it the most common shape of
  legacy test project. Its filter property is `TestCategory`, not `Category`.
- **Built by MSBuild.exe and run by vstest.console.exe** from Visual Studio or Build Tools.
  `dotnet restore` does not restore `packages.config`, and `dotnet test` on a non-SDK test
  project runs nothing and still exits 0.

## Run it

By hand, in a Visual Studio Developer prompt:

```bat
msbuild Legacy.sln -restore -p:RestorePackagesConfig=true -p:Configuration=Release
vstest.console.exe Billing.Tests\bin\Release\Billing.Tests.dll /TestCaseFilter:TestCategory=Verification
```

With verikit, run inside a git repo. The manifest's `run` is one command,
`{python} verification/legacy_test.py Legacy.sln Billing.Tests/bin/Release/Billing.Tests.dll TestCategory=Verification`.
That script finds MSBuild and vstest with `vswhere`, restores, rebuilds, and runs only the
verification test. It fails when no test ran.

```sh
python <kit>/verikit.py init --no-skills
python verification/verikit.py run
```

## The broken variant

`verification/billing/mutants/key-per-attempt.patch` builds the idempotency key per attempt
(`"order-" + _orderId + "-attempt-" + _attempt`). A retry then looks like a new purchase to the
gateway. The check must then fail, and its manifest `match` (`'customer charged [2-9] times'`)
names the rule. With `--seed 1` the message is `customer charged 2 times for one purchase;
events: customer pays -> timer rings -> request 1 reaches the gateway (late) -> request 2
reaches the gateway (late)`.

Keep the path short on Windows. .NET Framework test discovery fails silently near 260 characters.
