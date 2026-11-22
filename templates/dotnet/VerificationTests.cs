// Template: a code-level verification test in an EXISTING xUnit test project (works for
// .NET Framework 4.7.2+ and .NET via FsCheck's netstandard2.0 build). Worked, runnable version:
// examples/dotnet-checkout/tests/Checkout.Tests/CheckoutVerificationTests.cs
// Packages: FsCheck 2.16.x (+ xunit, Microsoft.NET.Test.Sdk). NUnit: [Test, Category("Verification")].
using System;
using System.Collections.Generic;
using FsCheck;
using Xunit;

public class TODO_PromiseVerificationTests
{
    [Fact]
    [Trait("Category", "Verification")]            // manifest run: dotnet test <proj> --filter Category=Verification
    public void TODO_rule_in_plain_words()
    {
        var config = Configuration.QuickThrowOnFailure;
        config.MaxNbOfTest = 500;
        if (int.TryParse(Environment.GetEnvironmentVariable("VERIKIT_SEED"), out var seed))
            config.Replay = FsCheck.Random.StdGen.NewStdGen(seed, 0);   // verikit run --seed N replays

        Prop.ForAll(Arb.From(Gen.Choose(0, 1000).ArrayOf()), schedule => RunOnce(schedule)).Check(config);
    }

    private static bool RunOnce(int[] schedule)
    {
        // TODO: build the REAL class under test with fakes only for boundaries you do not own.
        var log = new List<string>();
        foreach (var choice in schedule)
        {
            var enabled = new List<(string Label, Action Act)>();   // TODO: every event that can happen now
            if (enabled.Count == 0) break;
            var (label, act) = enabled[choice % enabled.Count];
            log.Add(label);
            act();
        }
        // TODO: let in-flight work finish, then check the rule. State the rule in the message:
        var broken = false;
        if (broken)
            throw new InvalidOperationException($"TODO rule broken; events: {string.Join(" -> ", log)}");
        return true;
    }
}
