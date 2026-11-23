# Roadmap

Ordered by value for large, mixed-stack projects. Each item says how to know it is done.

Done in 0.3.0: hook in a live Claude Code session ([evidence](evidence/live-stop-hook.md)),
Coyote example, Angular TestBed/Karma example, legacy (non-SDK) .NET Framework example,
cross-repo drift tracking, the plugin install path from GitHub, `run --ci` and CI templates
for GitHub Actions and Azure Pipelines with monorepo roots.

1. **Azure Pipelines template run in a real Azure DevOps project.** Today it is schema-valid and
   its commands run in the e2e test with Azure's PR variables. Done when a run is green on both
   an Azure Repos PR (branch policy) and a GitHub-hosted repo, with JUnit in the Tests tab.
2. **Parallel checks** (`run --jobs N`): main runs in parallel; patch variants serialized, or run
   in a temporary worktree so they never touch the working tree.
3. **Split manifests** (`include = ["checks/*.toml"]`) for areas owned by different teams.
4. **More .NET wiring:** NUnit (filter property, adapter), Coyote on `net48` wired into MSBuild
   (rewritten by hand once with `Microsoft.Coyote.Tool`), a pinned Coyote build for net10 once
   one is released.
5. **More Angular wiring through `verikit run`:** the older `@angular-devkit/build-angular:karma`
   builder, Jest and the Angular 21+ Vitest runner (both tried by hand only), NgRx effects.
6. **Cross-repo broken variants:** apply a `must_fail` patch in a source repository, with the
   same byte-exact backup and recovery as local patches. Today cross-repo is drift tracking only.
7. **Statechart output** for TLA+ models (Mermaid), or a documented hand-off to pstack's
   `/statechart`.
8. **Packaging**: an installable runner with pinned versions (pipx / dotnet tool / npm wrapper).
9. **Metrics**: counterexamples found, checks per promise, time per check, days stale, as JSON
   for a dashboard.
10. **Mutation testing beside verikit:** a documented Stryker.NET / StrykerJS setup on one
    example, so teams see breadth (Stryker) and targeted promises (verikit) side by side.
