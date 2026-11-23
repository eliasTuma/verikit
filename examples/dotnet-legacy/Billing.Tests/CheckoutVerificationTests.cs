using System;
using System.Collections.Generic;
using System.Linq;
using Billing;
using FsCheck;
using Microsoft.VisualStudio.TestTools.UnitTesting;

namespace Billing.Tests
{
    /// <summary>
    /// Code-level check of the promise "a customer is never charged twice for one purchase".
    /// FsCheck generates a schedule: at every step it picks one of the events that can happen
    /// next (the timer rings, a request reaches the gateway, a reply reaches the client), so
    /// each run drives the REAL CheckoutSession down a different path. It samples; it is not a proof.
    /// Selected alone with: vstest.console.exe Billing.Tests.dll /TestCaseFilter:TestCategory=Verification
    /// </summary>
    [TestClass]
    public class CheckoutVerificationTests
    {
        [TestMethod]
        [TestCategory("Verification")]
        public void Customer_is_never_charged_twice_for_one_purchase()
        {
            var config = Configuration.QuickThrowOnFailure;
            config.MaxNbOfTest = 500;
            int seed;
            if (int.TryParse(Environment.GetEnvironmentVariable("VERIKIT_SEED"), out seed))
                config.Replay = FsCheck.Random.StdGen.NewStdGen(seed, 0);   // replay: verikit run --seed N

            Prop.ForAll(
                    Arb.From(Gen.Choose(0, 1000).ArrayOf()),
                    Arb.From(Gen.Choose(0, 3)),
                    (schedule, maxRetries) => RunOnce(schedule, maxRetries))
                .Check(config);
        }

        private static bool RunOnce(int[] schedule, int maxRetries)
        {
            var gateway = new FakeGateway();
            var session = new CheckoutSession("A-1001", 4999, maxRetries, gateway);
            var log = new List<string> { "customer pays" };
            session.Pay();

            foreach (var choice in schedule)
            {
                var next = Enabled(session, gateway);
                if (next.Count == 0) break;
                var picked = next[choice % next.Count];
                log.Add(picked.Item1);
                picked.Item2();
            }
            while (gateway.InFlight.Count > 0) // requests still travelling land late
            {
                log.Add("request " + gateway.InFlight[0].Attempt + " reaches the gateway (late)");
                gateway.Deliver(gateway.InFlight[0]);
            }

            if (gateway.Charges > 1)
                throw new InvalidOperationException(
                    "customer charged " + gateway.Charges + " times for one purchase; events: " + string.Join(" -> ", log));
            if (session.Status == CheckoutStatus.Paid && gateway.Charges != 1)
                throw new InvalidOperationException("order marked paid without a charge");
            return true;
        }

        private static List<Tuple<string, Action>> Enabled(CheckoutSession session, FakeGateway gateway)
        {
            var next = new List<Tuple<string, Action>>();
            if (session.Status == CheckoutStatus.Waiting)
                next.Add(Tuple.Create("timer rings", (Action)session.OnTimeout));
            foreach (var request in gateway.InFlight.ToList())
                next.Add(Tuple.Create("request " + request.Attempt + " reaches the gateway", (Action)(() => gateway.Deliver(request))));
            foreach (var reply in gateway.Replies.ToList())
                next.Add(Tuple.Create("reply " + reply.Attempt + " reaches the client", (Action)(() =>
                {
                    gateway.Replies.Remove(reply);
                    session.OnReply(reply.Attempt, reply.ReceiptId);
                })));
            return next;
        }

        private sealed class Reply
        {
            public int Attempt;
            public string ReceiptId;
        }

        /// <summary>A payment gateway that, like real ones, charges once per idempotency key.</summary>
        private sealed class FakeGateway : IPaymentGateway
        {
            private readonly Dictionary<string, string> _receiptsByKey = new Dictionary<string, string>();
            public readonly List<ChargeRequest> InFlight = new List<ChargeRequest>();
            public readonly List<Reply> Replies = new List<Reply>();
            public int Charges;

            public void Send(ChargeRequest request)
            {
                InFlight.Add(request);
            }

            public void Deliver(ChargeRequest request)
            {
                InFlight.Remove(request);
                string receipt;
                if (request.IdempotencyKey == null || !_receiptsByKey.TryGetValue(request.IdempotencyKey, out receipt))
                {
                    Charges++;
                    receipt = "ch_" + Charges;
                    if (request.IdempotencyKey != null) _receiptsByKey[request.IdempotencyKey] = receipt;
                }
                Replies.Add(new Reply { Attempt = request.Attempt, ReceiptId = receipt });
            }
        }
    }
}
