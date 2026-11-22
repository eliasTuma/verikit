# Roadmap

Ordered by value for large, mixed-stack projects. Each item says how to know it is done.

1. **Hook in a live Claude Code session.** Done when a recorded session shows the hook blocking
   and the agent running the named check.
2. **Azure Pipelines template exercised** on a real project. Done when a run is green with the
   JUnit results in the Tests tab.
3. **Microsoft Coyote example** for async/Task races in .NET, runnable in `check_examples.py`.
4. **Angular TestBed/Karma example** runnable. Today the RxJS logic runs; the wiring is a template.
5. **Legacy .NET Framework (non-SDK project) example** using msbuild + vstest.console.
6. **Parallel checks** (`run --jobs N`): main runs in parallel; patch variants serialized, or
   run in a temporary worktree so they never touch the working tree.
7. **Split manifests** (`include = ["checks/*.toml"]`) for areas owned by different teams.
8. **Cross-repo drift**: a check in repo A whose `covers` names files in repo B (a separate
   frontend repo, for example), resolved through a pinned commit of B.
9. **Statechart output** for TLA+ models (Mermaid), or a documented hand-off to pstack's
   `/statechart`.
10. **Packaging**: an installable runner with pinned versions, and the Claude Code plugin
    install path tested (`/plugin marketplace add eliasTuma/verikit`).
11. **Metrics**: counterexamples found, checks per promise, time per check, days stale, as JSON
    for a dashboard.
