# Roadmap

Ordered by value for large, mixed-stack projects. Each item says how to know it is done.

Done in 0.3.x: hook in live Claude Code sessions ([evidence](evidence/live-stop-hook.md)),
Coyote, Angular TestBed and legacy .NET Framework examples, cross-repo drift tracking, the
plugin install path, `run --ci` with templates for GitHub Actions and Azure Pipelines, sync
records that survive squash merges, a hook that blames only its own session.

Done in 0.4.0: function-level covers, find-and-replace variants, saved seeds and nightly
random runs, the status report and run summary, isolated and parallel variants, trace
checking against the model, a database-backed example on PostgreSQL.

1. **A pilot on a real codebase.** Everything so far runs on small programs written for the
   kit. Done when one promise is verified in a production repository whose code uses a real
   database and has no ready-made seams, and the kit's gaps found there are fixed or listed.
2. **Azure Pipelines template run in a real Azure DevOps project.** Today it is schema-valid and
   its commands run in the e2e test with Azure's PR variables. Done when a run is green on both
   an Azure Repos PR (branch policy) and a GitHub-hosted repo, with JUnit in the Tests tab and
   the run summary on the Extensions tab.
3. **Database races on SQL Server from .NET.** The two-connection pattern ran on PostgreSQL
   from Node. Done when a `DbConnection` wrapper + FsCheck example runs against SQL Server
   (LocalDB or a container) in `check_examples.py`.
4. **Trace checking beyond the Node example:** a recorder in a .NET test project, traces that
   include which action happened (not only state), and a liveness-style check that every
   model action was seen in some trace (the code can do what the model says it can).
5. **Maintenance as a scheduled agent:** a CI job that runs `/verify-maintain` on stale checks
   and opens a pull request with its triage, so drift is handled without a person starting it.
6. **Dozens of rules.** The layout is documented (one model and one test file per flow, one
   entry per rule) and its mechanics are unit-tested, but nothing has run with more than a few
   rules. Open parts: **one run shared by several rules** (today a database container or a
   legacy build is paid once per rule and variant), and **split manifests**
   (`include = ["checks/*.toml"]`) for flows owned by different teams. Done when a repository
   with 25 rules has measured run times for a pull request and for the nightly run.
7. **More .NET wiring:** NUnit (filter property, adapter), Coyote on `net48` wired into MSBuild
   (rewritten by hand once), a pinned Coyote build for net10 once one is released.
8. **More Angular wiring through `verikit run`:** the older `@angular-devkit/build-angular:karma`
   builder, Jest and the Angular 21+ Vitest runner (both tried by hand only), NgRx effects.
9. **Isolated runs on huge repositories:** copy only what a check needs without listing folders
   by hand (`[isolation] include` exists; derive it from `covers` and project references), and
   reuse build output between variants.
10. **Cross-repo broken variants:** apply a `must_fail` edit in a source repository. Today
    cross-repo is drift tracking only.
11. **Function-level covers with a real parser** where the heuristic is not enough (nested
    generics, macros, languages without braces or indentation rules); marker comments work
    everywhere today.
12. **Other agents:** the SessionStart/Stop hook is Claude Code only. Done when Cursor and
    Codex have an equivalent, or a git pre-push hook covers everyone.
13. **Statechart output** for TLA+ models (Mermaid), **packaging** (pipx / dotnet tool / npm
    wrapper), **metrics** as JSON for a dashboard, and a documented **mutation testing** setup
    (Stryker.NET / StrykerJS) beside verikit.
