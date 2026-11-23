# dotnet-coyote

A .NET async race, checked with [Microsoft Coyote](https://github.com/microsoft/coyote) inside a
normal xUnit test project.

`src/GiftCards` (netstandard2.0) redeems gift cards. `GiftCardService.RedeemAsync` reads the card,
checks that it is unused, marks it used and credits the order, with an `await` between every step.
A `SemaphoreSlim` keeps two redemptions in this process from interleaving.

**Promise:** a gift card is redeemed at most once, even when two requests for it arrive at the
same time (two browser tabs, a double submit, a client retry).
**Harm:** the card's balance is paid out twice.
**Not covered:** a second service instance (the semaphore is per process; that needs a database
guard and a design model), partial failures (credit fails after the card is marked used), and
refunds or reversals.

## How it is wired

| File | Role |
|---|---|
| `tests/GiftCards.Tests/GiftCardRaceVerificationTests.cs` | The check: `[Fact, Trait("Category","Verification")]`. Runs two concurrent `RedeemAsync` calls under Coyote's `TestingEngine` for 1000 schedules, seeded from `VERIKIT_SEED`. |
| `tests/GiftCards.Tests/Fakes.cs` | In-memory card store and ledger. Every call awaits first, like a database round trip, and logs what happened. |
| `tests/GiftCards.Tests/GiftCardServiceTests.cs` | The team's ordinary example tests, in the same project. |
| `tests/GiftCards.Tests/GiftCards.Tests.csproj` | After every build, a target runs `dotnet tool restore` and then `coyote rewrite` on the product and test dlls in `bin/`. |
| `tests/GiftCards.Tests/rewrite.coyote.json` | The dlls to rewrite (only the code you own, not xUnit or other packages). |
| `.config/dotnet-tools.json` | Pins the `coyote` CLI (`Microsoft.Coyote.CLI` 1.7.11, the same version as the `Microsoft.Coyote.Test` package). |

Coyote can only control `Task`/`async` code that has been rewritten. The rewrite changes the dlls in
`bin/` only, never the sources. The test refuses to run (it fails) if either dll is not rewritten,
so a skipped rewrite cannot give a hollow pass. It also fails if Coyote reports uncontrolled
concurrency or explored fewer schedules than asked. Telemetry is off, both in the test
(`WithTelemetryEnabled(false)`) and for the CLI (`COYOTE_CLI_TELEMETRY_OPTOUT=1`).

## Run it

```sh
dotnet test                                     # all tests; restores the tool and rewrites on build
dotnet test --filter Category=Verification      # only the check
python <kit>/verikit.py init --no-skills        # once, inside a git repo
python verification/verikit.py run              # the check, plus both broken variants
python verification/verikit.py run --seed 12345 # replay a printed seed
```

You need the .NET 8 SDK, with its ASP.NET Core runtime, which the `coyote` CLI uses. Nothing else
has to be installed: the build restores the tool. In CI, `actions/setup-dotnet` with `8.0.x` is
enough. The tool manifest pins `rollForward: false`, so a runner that has only a newer .NET needs
the 8.0 runtime added. This example has run on Windows only so far.

## What a failure looks like

From the broken variant without the semaphore:

```
gift card GC-100 redeemed 2 times (order-B, order-A); rule: a gift card is redeemed at most once.
events: order-A: reads GC-100 (unused) -> order-B: reads GC-100 (unused) -> order-B: saves GC-100 as used by order-B
-> order-B: credits order-B with 5000 cents (gift card GC-100) -> order-A: saves GC-100 as used by order-A
-> order-A: credits order-A with 5000 cents (gift card GC-100)
found on schedule 1 of 1000 (seed 1). Replay the same schedule: verikit run <id> --seed 1, ...
trace files: .../bin/Debug/net8.0/coyote-traces/giftcard-race-seed1.txt, ...seed1.trace, ...seed1.trace.dgml
```

The `.trace` file replays that exact schedule under a debugger with
`Configuration.Create().WithReproducibleTrace(File.ReadAllText(path))`. Running again with the same
seed explores the same schedules.

## Broken variants (`verification/giftcards/mutants/`)

| Patch | What it removes | Caught |
|---|---|---|
| `no-redeem-gate.patch` | the `SemaphoreSlim` wait and release, so this becomes plain check-then-act: read, `await`, write | yes, `redeemed 2 times` |
| `no-already-redeemed-check.patch` | the "already used" check | yes; the ordinary example test catches it too |

## What Coyote adds over FsCheck, and over a stress test

- **FsCheck** (see `examples/dotnet-checkout`) chooses which *event* happens next, so the code must
  be written as event handlers, or wrapped as them. It cannot pause a real `async` method at an
  `await` and run another request's continuation in between. Coyote can: it takes over the task
  scheduler and picks which pending continuation runs at every `await`, `Task.Run`,
  `SemaphoreSlim` call, and lock or collection access. The code under test is the unmodified
  production method.
- **A stress test** also hits this race, but only by timing luck, and a pass tells you nothing.
  The stress test here was a `Task.WhenAll` loop on the real thread pool, outside the
  `TestingEngine`, using the same rewritten dlls. Outside a test, Coyote's hooks pass straight
  through. Measured on this machine against the semaphore-free variant:
  - **With these fakes:** the stress loop redeemed twice in 2484 of 5000 runs. Coyote found the bug
    on schedule 1-4 in each of 200 seeds.
  - **With synchronous fakes** (no `await Task.Yield()`): the stress loop redeemed twice in only
    305 of 5000 runs. Coyote found the bug on schedule 1-3 in each of 10 seeds. It also switches
    at lock and collection accesses.

  A Coyote failure replays from its seed or `.trace` file, and a pass means 1000 controlled
  schedules ran.

Measured on Windows 11 with the .NET 8.0.403 SDK:

| | |
|---|---|
| Broken variant (no semaphore), `random` strategy | found in 200/200 seeds; first failing schedule median 1, max 4 |
| Same, `prioritization` (PCT) strategy | 200/200 seeds; median 3, max 8 |
| Passing run on the correct code | 1000 schedules in about 2-4 s |
| `dotnet test` of the check | 5-17 s: MSBuild and the test host, about 0.5 s of rewriting, then the schedules |
| `verikit run` (holds + 2 variants) | 23-32 s |

1000 schedules is far more than this bug needs. The extra schedules cover the passing run and
cost little.

## Limits

- **Sampling, not proof.** Coyote explores schedules at its scheduling points, seeded and at
  random. It is not an exhaustive search of every interleaving. Its report says "1000 unique"
  paths, but that counts sequences of scheduling decisions, not distinct behaviours. Coyote's
  exhaustive DFS strategy is internal in 1.7.11.
- **One process.** Two service instances do not share the semaphore. Check that with a TLA+ model
  with an instance count, or with a database-level guard (conditional update, unique constraint)
  and an integration test.
- **.NET versions.** Coyote 1.7.11 (March 2024) ships `net8.0`, `net6.0`, `netcoreapp3.1` and
  `netstandard2.0` libraries, and a `coyote` dotnet tool for .NET Core / .NET 8. This example
  targets `net8.0` only.
  - **`net48`, tried once by hand on Windows, not part of this example:** the steps were to
    multi-target the test project `net8.0;net48`, build `net48` with the rewrite target skipped,
    rewrite `bin/Debug/net48` with the net462 `coyote.exe` from the `Microsoft.Coyote.Tool` 1.7.11
    package (fetched with a `<PackageDownload>`), then run `dotnet test -f net48 --no-build`. The
    check passed, and the semaphore-free variant failed with the same message. If the dotnet tool
    rewrites the `net48` dlls instead, test discovery breaks for the whole `net48` project ("No
    test is available"). That is why the rewrite target here only runs for .NET (Core) target
    frameworks.
  - **`net10.0`:** the CLI is a .NET 8 process. The Coyote repository's own compatibility
    assessment says `net10.0` test assemblies need a workaround and are not fully supported in
    1.7.11.
