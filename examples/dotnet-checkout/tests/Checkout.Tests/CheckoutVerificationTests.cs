using System;
using System.Collections.Generic;
using System.Linq;
using Checkout;
using FsCheck;
using Xunit;

namespace Checkout.Tests
{
    /// <summary>
    /// Code-level check of the promise "a customer is never charged twice for one purchase".
    /// FsCheck generates a schedule: at every step it picks one of the events that can happen
    /// next (the timer rings, a request reaches the provider, a reply reaches the client), so
    /// each run drives the REAL CheckoutSession down a different path. It samples; it is not a proof.
    /// </summary>
    public class CheckoutVerificationTests
    {
        [Fact]
        [Trait("Category", "Verification")]
        public void Customer_is_never_charged_twice_for_one_purchase()
        {
            var config = Configuration.QuickThrowOnFailure;
            config.MaxNbOfTest = 500;
            var seed = Environment.GetEnvironmentVariable("VERIKIT_SEED");
            if (int.TryParse(seed, out var s))
                config.Replay = FsCheck.Random.StdGen.NewStdGen(s, 0);

            Prop.ForAll(
                    Arb.From(Gen.Choose(0, 1000).ArrayOf()),
                    Arb.From(Gen.Choose(0, 3)),
                    (schedule, maxRetries) => RunOnce(schedule, maxRetries))
                .Check(config);
        }

        private static bool RunOnce(int[] schedule, int maxRetries)
        {
            var provider = new FakeProvider();
            var session = new CheckoutSession("A-1001", 4999, maxRetries, provider);
            var log = new List<string> { "customer pays" };
            session.Pay();

            foreach (var choice in schedule)
            {
                var next = Enabled(session, provider);
                if (next.Count == 0) break;
                var (label, act) = next[choice % next.Count];
                log.Add(label);
                act();
            }
            while (provider.InFlight.Count > 0) // requests still travelling land late
            {
                log.Add($"request {provider.InFlight[0].Attempt} reaches the provider (late)");
                provider.Deliver(provider.InFlight[0]);
            }

            if (provider.Charges > 1)
                throw new InvalidOperationException(
                    $"customer charged {provider.Charges} times for one purchase; events: {string.Join(" -> ", log)}");
            if (session.Status == CheckoutStatus.Paid && provider.Charges != 1)
                throw new InvalidOperationException("order marked paid without a charge");
            return true;
        }

        private static List<(string, Action)> Enabled(CheckoutSession session, FakeProvider provider)
        {
            var next = new List<(string, Action)>();
            if (session.Status == CheckoutStatus.Waiting)
                next.Add(("timer rings", session.OnTimeout));
            foreach (var request in provider.InFlight.ToList())
                next.Add(($"request {request.Attempt} reaches the provider", () => provider.Deliver(request)));
            foreach (var reply in provider.Replies.ToList())
                next.Add(($"reply {reply.Attempt} reaches the client", () =>
                {
                    provider.Replies.Remove(reply);
                    session.OnReply(reply.Attempt, reply.ReceiptId);
                }));
            return next;
        }

        /// <summary>A payment provider that, like real ones, charges once per idempotency key.</summary>
        private sealed class FakeProvider : IPaymentProvider
        {
            private readonly Dictionary<string, string> _receiptsByKey = new Dictionary<string, string>();
            public List<ChargeRequest> InFlight { get; } = new List<ChargeRequest>();
            public List<(int Attempt, string ReceiptId)> Replies { get; } = new List<(int, string)>();
            public int Charges { get; private set; }

            public void Send(ChargeRequest request) => InFlight.Add(request);

            public void Deliver(ChargeRequest request)
            {
                InFlight.Remove(request);
                if (request.IdempotencyKey == null || !_receiptsByKey.TryGetValue(request.IdempotencyKey, out var receipt))
                {
                    Charges++;
                    receipt = "ch_" + Charges;
                    if (request.IdempotencyKey != null) _receiptsByKey[request.IdempotencyKey] = receipt;
                }
                Replies.Add((request.Attempt, receipt));
            }
        }
    }
}
