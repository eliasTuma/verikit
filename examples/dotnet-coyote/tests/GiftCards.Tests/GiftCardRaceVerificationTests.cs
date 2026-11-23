using System;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using Microsoft.Coyote;
using Microsoft.Coyote.Rewriting;
using Microsoft.Coyote.Specifications;
using Microsoft.Coyote.SystematicTesting;
using Xunit;
using Xunit.Abstractions;

namespace GiftCards.Tests
{
    /// <summary>
    /// Code-level check of the promise "a gift card is redeemed at most once, even when two requests
    /// for it arrive at the same time". Coyote runs the REAL GiftCardService with two concurrent
    /// RedeemAsync calls, and on every iteration its scheduler picks a different interleaving of their
    /// awaits (the semaphore, each store read and write, the ledger credit). It explores schedules
    /// one at a time from a seed and stops at the first bug; it samples, it is not a proof.
    /// </summary>
    public class GiftCardRaceVerificationTests
    {
        private const int Iterations = 1000;
        private const string Code = "GC-100";
        private readonly ITestOutputHelper _output;

        public GiftCardRaceVerificationTests(ITestOutputHelper output) => _output = output;

        [Fact]
        [Trait("Category", "Verification")]
        public void Gift_card_is_redeemed_at_most_once_when_two_requests_race()
        {
            // The same seed replays the same schedules: verikit run <id> --seed N (exports VERIKIT_SEED).
            var seed = uint.TryParse(Environment.GetEnvironmentVariable("VERIKIT_SEED"), out var s)
                ? s : (uint)Random.Shared.Next();
            var config = Configuration.Create()
                .WithTestingIterations(Iterations)
                .WithRandomStrategy()
                .WithRandomGeneratorSeed(seed)
                .WithPartiallyControlledConcurrencyAllowed(false) // uncontrolled concurrency = error, not a silent pass
                .WithTelemetryEnabled(false);

            using var engine = TestingEngine.Create(config, TwoRequestsRedeemTheSameCard);
            // Without rewriting, Coyote cannot see the awaits, and a pass would mean nothing.
            Assert.True(engine.IsTestRewritten() && RewritingEngine.IsAssemblyRewritten(typeof(GiftCardService).Assembly),
                "GiftCards.dll / GiftCards.Tests.dll are not rewritten by Coyote: run `dotnet tool restore` and rebuild");

            engine.Run();
            var report = engine.TestReport;
            var explored = report.NumOfExploredFairPaths + report.NumOfExploredUnfairPaths;
            _output.WriteLine($"seed {seed}: explored {explored} schedules, {report.NumOfFoundBugs} bug(s)");
            _output.WriteLine(report.GetText(config));

            if (report.NumOfFoundBugs > 0)
            {
                var dir = Path.Combine(AppContext.BaseDirectory, "coyote-traces"); // under bin/, not the source tree
                Directory.CreateDirectory(dir);
                var saved = engine.TryEmitReports(dir, $"giftcard-race-seed{seed}", out var paths)
                    ? string.Join(", ", paths) : "(no trace written)";
                Assert.Fail($"{string.Join("; ", report.BugReports)}\n" +
                            $"found on schedule {explored} of {Iterations} (seed {seed}). Replay the same schedule: " +
                            $"verikit run <id> --seed {seed}, or VERIKIT_SEED={seed} dotnet test --filter Category=Verification, " +
                            $"or Configuration.WithReproducibleTrace(<the .trace file>)\ntrace files: {saved}");
            }
            Assert.True(explored == Iterations, $"Coyote explored {explored} of {Iterations} schedules");
            Assert.True(report.InternalErrors.Count == 0, "Coyote internal errors: " + string.Join("; ", report.InternalErrors));
            Assert.True(report.UncontrolledInvocations.Count == 0,
                "uncontrolled calls, Coyote could not explore them: " + string.Join("; ", report.UncontrolledInvocations));
        }

        /// <summary>One iteration: a fresh service and one unused card; two orders redeem it at once.</summary>
        private static async Task TwoRequestsRedeemTheSameCard()
        {
            var log = new EventLog();
            var ledger = new RecordingOrderLedger(log);
            var service = new GiftCardService(new InMemoryGiftCardStore(log, new GiftCard(Code, 5000)), ledger);

            var results = await Task.WhenAll(Redeem(service, "order-A"), Redeem(service, "order-B"));

            var credited = ledger.OrdersCreditedFor("gift card " + Code);
            Specification.Assert(credited.Count <= 1, "{0}",
                $"gift card {Code} redeemed {credited.Count} times ({string.Join(", ", credited)}); " +
                $"rule: a gift card is redeemed at most once. events: {log}");
            Specification.Assert(credited.Count == 1, "{0}",
                $"gift card {Code} redeemed 0 times: one of the two requests should have succeeded. events: {log}");
            Specification.Assert(results.Count(r => r == RedeemResult.Redeemed) == 1, "{0}",
                $"service answered [{string.Join(", ", results)}] but the ledger credited [{string.Join(", ", credited)}]. events: {log}");
        }

        private static Task<RedeemResult> Redeem(GiftCardService service, string orderId) =>
            Task.Run(async () =>
            {
                EventLog.Request.Value = orderId;
                return await service.RedeemAsync(Code, orderId);
            });
    }
}
