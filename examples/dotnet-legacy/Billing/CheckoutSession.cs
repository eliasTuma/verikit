namespace Billing
{
    public enum CheckoutStatus { NotStarted, Waiting, Paid, GaveUp }

    public sealed class ChargeRequest
    {
        public ChargeRequest(int attempt, string idempotencyKey, string orderId, long amountCents)
        {
            Attempt = attempt;
            IdempotencyKey = idempotencyKey;
            OrderId = orderId;
            AmountCents = amountCents;
        }

        public int Attempt { get; private set; }
        public string IdempotencyKey { get; private set; }
        public string OrderId { get; private set; }
        public long AmountCents { get; private set; }
    }

    /// <summary>Sends a charge request; the reply arrives later through CheckoutSession.OnReply.</summary>
    public interface IPaymentGateway
    {
        void Send(ChargeRequest request);
    }

    /// <summary>
    /// Pays for one order. If the gateway is slow, the caller's timer calls OnTimeout and the
    /// session retries, up to maxRetries times.
    /// Promise: a customer is never charged twice for one purchase.
    /// </summary>
    public sealed class CheckoutSession
    {
        private readonly string _orderId;
        private readonly long _amountCents;
        private readonly int _maxRetries;
        private readonly IPaymentGateway _gateway;
        private int _attempt;

        public CheckoutSession(string orderId, long amountCents, int maxRetries, IPaymentGateway gateway)
        {
            _orderId = orderId;
            _amountCents = amountCents;
            _maxRetries = maxRetries;
            _gateway = gateway;
            Status = CheckoutStatus.NotStarted;
        }

        public CheckoutStatus Status { get; private set; }
        public string ReceiptId { get; private set; }
        public int Attempts { get { return _attempt; } }

        public void Pay()
        {
            if (Status != CheckoutStatus.NotStarted) return;
            SendAttempt();
        }

        public void OnTimeout()
        {
            if (Status != CheckoutStatus.Waiting) return;
            if (_attempt <= _maxRetries) SendAttempt();
            else Status = CheckoutStatus.GaveUp;
        }

        public void OnReply(int attempt, string receiptId)
        {
            // Only the current attempt's reply counts; a late reply to an earlier attempt is ignored.
            if (Status != CheckoutStatus.Waiting || attempt != _attempt) return;
            Status = CheckoutStatus.Paid;
            ReceiptId = receiptId;
        }

        private void SendAttempt()
        {
            _attempt++;
            Status = CheckoutStatus.Waiting;
            _gateway.Send(new ChargeRequest(_attempt, IdempotencyKey(), _orderId, _amountCents));
        }

        // One key per purchase, reused by every retry, so the gateway charges at most once.
        private string IdempotencyKey()
        {
            return "order-" + _orderId;
        }
    }
}
