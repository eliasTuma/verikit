# Which tool, for which stack

The question picks the column; the stack picks the row. Detailed notes for the stacks the
kit has run: [.NET](stacks/dotnet.md) · [Node](stacks/node.md) · [React](stacks/react.md) ·
[Angular](stacks/angular.md). Everything else here is a pointer, not something the kit has run.

## Bad order of steps (any stack)

The model describes the design, so the code's language does not matter.

| Tool | Status in the kit | Notes |
|---|---|---|
| **TLA+ / TLC** (default) | ran | `verikit tlc Spec.tla [--const K=V] [--inv NAME]`. Java 11+ or Docker. |
| `verikit explore` | ran | Python model, zero install, small models only. Fallback and teaching aid. |
| Apalache | not run | Symbolic (SMT) checker for TLA+ and Quint; larger integer ranges, inductive invariants. |
| Quint | not run | TLA-based spec language with TypeScript-like syntax; `quint run spec.qnt --invariant=Inv`. |
| P | not run | Event-driven state machines from Microsoft/AWS; C# toolchain. |

## The real code: generated inputs and orderings, verifiers, proofs

| Stack | Property / concurrency tests | Bounded verifier on the code | Proofs |
|---|---|---|---|
| **C# / .NET and .NET Framework** | **FsCheck (ran: net8.0, net48, legacy non-SDK net48)**, CsCheck (.NET only); **Microsoft Coyote 1.7.11 for real async/Task interleavings (ran, net8.0)** | - | Dafny (compiles to C#) |
| **Node / TypeScript** | **fast-check (ran)**, `fc.scheduler()` for async races | - | - |
| **React** | **fast-check + Testing Library (ran)**; Playwright for full flows (not run) | - | - |
| **Angular** | **fast-check on RxJS services (ran)**; TestBed wiring (template, not run) | - | - |
| Python | Hypothesis (incl. stateful) | CrossHair | Nagini |
| Rust | proptest; loom for thread interleavings | Kani | Verus, Creusot, Prusti |
| Go | rapid, `go test -fuzz` | - | Gobra |
| Java / Kotlin | jqwik, kotest-property | JBMC | OpenJML, KeY |
| C / C++ | RapidCheck, libFuzzer | CBMC, ESBMC | Frama-C |

## Also useful beside verikit

- **Mutation testing** breaks your code in hundreds of small ways and reports which breaks
  no test noticed: Stryker.NET (C#), StrykerJS (JS/TS, React, Angular). It is the broad,
  automatic version of verikit's targeted `must_fail` variants.
- **Statecharts:** pstack's `/statechart` skill draws a TLA+ model as a diagram in the reader's
  words, useful for reviewing a rule with a product owner.

## Rules of thumb

- Model checking finds bad orderings in a design; it cannot see a bug the model leaves out.
- Property tests run the real code but sample: a pass is evidence, not proof.
- A bounded verifier is exhaustive up to its bound - write the bound in the manifest.
- A proof covers every size, but only the property written and only the code it is about.
