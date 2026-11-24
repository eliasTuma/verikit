# Methodology

## The problem

A test walks one path: the one someone thought of. Real traffic walks every path it can. With
three things that can happen at each step, 10 steps give 3^10 = 59,049 paths and 20 steps give
about 3.5 billion; ten events that each happen once can arrive in 10! = 3,628,800 orders. The
classic bug: an agent adds "retry when the payment call times out". Tests are green. The first
call was slow, not dead; it lands, the retry lands, and the customer is charged twice.

## The method

1. **A promise is one sentence** a product owner agrees with, plus the harm when it breaks and
   the neighbouring promises it does *not* cover. Nothing can prove the sentence is the right
   one; a person reads it. Everything below checks the code against it.
2. **The question picks the tool.**
   - *Can steps happen in a bad order?* Write a small **model** of the design (TLA+): the state,
     and each indivisible step as an action. A **model checker** (TLC) visits every reachable
     state within the bounds (many paths reach the same state, and each state is checked once)
     and returns the shortest path that breaks the rule.
   - *Does the real code hold for every input or ordering?* Where the language has a verifier
     on the code (Kani for Rust, CBMC for C), use it. Where it does not (C#, TypeScript), run
     **property tests on the real code** that generate inputs and event orderings (FsCheck,
     fast-check). That is sampling, not proof, and is labelled as such.
   - *Must it hold at every size?* A **proof** (Lean, Dafny): the rule holds initially and every
     step preserves it, so it holds on every path. Rarely the first step.
3. **A check you have never seen fail proves nothing.** For every guard the code relies on, a
   broken variant (a patch removing it, or a model constant disabling it) must make the check
   fail. Every rule checked on a model also needs a **witness**: an invariant claiming the
   situation the rule talks about never happens, which must fail. Without it, a model where
   nothing can happen passes every rule. A model shared by several rules needs one per rule.
4. **Findings stay findings.** When the current code breaks the rule, report the counterexample
   in the code's words. Do not weaken the rule or the model until it passes.
5. **Keep the check in step with the code.** Each check records the files it describes and the
   content it was last confirmed against (`verification/sync/<id>.json`; content, not a commit
   id, so squash merges and shallow clones cannot invalidate it). A model does not see code changes:
   a model check keeps passing after someone breaks the code. So pair design checks with code
   checks, flag drift (`stale`), and re-confirm models after changes (`/verify-maintain`).
6. **Few promises, chosen well.** 3-10 per product, where a bug costs the most and tests reach
   the least (`/verify-survey`). Keep your tests for everything else.

## Three kinds of check, and why

| | Design check (TLA+ model) | Code check (property test) | Conformance check (`verikit trace`) |
|---|---|---|---|
| Covers | every ordering within the bounds | the real code, including what the model left out | whether the real code stays inside the model |
| Blind to | code that no longer matches the model | orderings it did not sample | behaviour the model allows but the code never shows |
| Breaks when | the design is wrong | the code is wrong | the code and the model disagree |

In the kit's end-to-end test (`scripts/e2e.py`), a commit reintroduces the double charge in
code. The design check stays green, because the model never saw the change. The code check
fails; the conformance check fails too, showing the exact observation (a second charge) that
the model cannot reach; `stale` and the Stop hook flag the change.

## Where this comes from

The method follows the verification skills in Lauren Tan's pstack (`cursor/plugins`, see
NOTICE). At Amazon Web Services, TLA+ found design bugs whose shortest traces were 35 steps
long (Newcombe et al., "How Amazon Web Services Uses Formal Methods", CACM 2015).
To learn TLA+: https://learntla.com.
