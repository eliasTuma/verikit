using System.Threading;
using System.Threading.Tasks;

namespace GiftCards
{
    public sealed class GiftCard
    {
        public GiftCard(string code, long balanceCents, string redeemedByOrder = null)
        {
            Code = code;
            BalanceCents = balanceCents;
            RedeemedByOrder = redeemedByOrder;
        }

        public string Code { get; }
        public long BalanceCents { get; }
        /// <summary>The order the card was used on, or null while it is unused.</summary>
        public string RedeemedByOrder { get; }

        public GiftCard RedeemedBy(string orderId) => new GiftCard(Code, BalanceCents, orderId);
    }

    /// <summary>The gift card table (a database in production).</summary>
    public interface IGiftCardStore
    {
        Task<GiftCard> FindAsync(string code);
        Task SaveAsync(GiftCard card);
    }

    /// <summary>Applies money to an order (the order / payments service in production).</summary>
    public interface IOrderLedger
    {
        Task CreditAsync(string orderId, long amountCents, string reason);
    }

    public enum RedeemResult { Redeemed, AlreadyRedeemed, NotFound }

    /// <summary>
    /// Redeems a gift card against an order: the whole balance is credited to that order.
    /// Promise: a gift card is redeemed at most once, even when two requests for the same card
    /// arrive at the same time (two browser tabs, a double submit, a client retry).
    /// </summary>
    public sealed class GiftCardService
    {
        private readonly IGiftCardStore _cards;
        private readonly IOrderLedger _ledger;

        // One redemption at a time in this process, so "read the card, check it is unused, mark it
        // used" cannot interleave with another request. It does NOT protect a second instance.
        private readonly SemaphoreSlim _redeemGate = new SemaphoreSlim(1, 1);

        public GiftCardService(IGiftCardStore cards, IOrderLedger ledger)
        {
            _cards = cards;
            _ledger = ledger;
        }

        public async Task<RedeemResult> RedeemAsync(string code, string orderId)
        {
            await _redeemGate.WaitAsync().ConfigureAwait(false);
            try
            {
                var card = await _cards.FindAsync(code).ConfigureAwait(false);
                if (card == null)
                    return RedeemResult.NotFound;
                if (card.RedeemedByOrder != null)
                    return RedeemResult.AlreadyRedeemed;

                await _cards.SaveAsync(card.RedeemedBy(orderId)).ConfigureAwait(false);
                await _ledger.CreditAsync(orderId, card.BalanceCents, "gift card " + code).ConfigureAwait(false);
                return RedeemResult.Redeemed;
            }
            finally
            {
                _redeemGate.Release();
            }
        }
    }
}
