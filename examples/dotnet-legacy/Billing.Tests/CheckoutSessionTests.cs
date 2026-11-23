using System.Collections.Generic;
using Billing;
using Microsoft.VisualStudio.TestTools.UnitTesting;

namespace Billing.Tests
{
    /// <summary>The team's ordinary example tests. The verification filter leaves these out.</summary>
    [TestClass]
    public class CheckoutSessionTests
    {
        private sealed class RecordingGateway : IPaymentGateway
        {
            public readonly List<ChargeRequest> Sent = new List<ChargeRequest>();
            public void Send(ChargeRequest request) { Sent.Add(request); }
        }

        [TestMethod]
        public void Pay_sends_one_charge_request()
        {
            var gateway = new RecordingGateway();
            var session = new CheckoutSession("A-1001", 4999, 2, gateway);

            session.Pay();
            session.Pay();

            Assert.AreEqual(1, gateway.Sent.Count);
            Assert.AreEqual(CheckoutStatus.Waiting, session.Status);
        }

        [TestMethod]
        public void Reply_to_the_current_attempt_marks_the_order_paid()
        {
            var gateway = new RecordingGateway();
            var session = new CheckoutSession("A-1001", 4999, 2, gateway);

            session.Pay();
            session.OnReply(1, "ch_1");

            Assert.AreEqual(CheckoutStatus.Paid, session.Status);
            Assert.AreEqual("ch_1", session.ReceiptId);
        }
    }
}
