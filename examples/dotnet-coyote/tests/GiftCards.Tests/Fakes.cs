using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;

namespace GiftCards.Tests
{
    /// <summary>What happened, in order, so a failure reads as a story. Thread-safe.</summary>
    public sealed class EventLog
    {
        // Which request the current async flow belongs to ("order-A"); set by the test.
        public static readonly AsyncLocal<string> Request = new AsyncLocal<string>();
        private readonly List<string> _events = new List<string>();

        public void Add(string what)
        {
            lock (_events) _events.Add((Request.Value ?? "?") + ": " + what);
        }

        public override string ToString()
        {
            lock (_events) return string.Join(" -> ", _events);
        }
    }

    /// <summary>
    /// The card table. Every call awaits first, like a database round trip, so another request
    /// can run in between. (Under Coyote, that await is a point where the scheduler may switch.)
    /// </summary>
    public sealed class InMemoryGiftCardStore : IGiftCardStore
    {
        private readonly Dictionary<string, GiftCard> _rows = new Dictionary<string, GiftCard>();
        private readonly EventLog _log;

        public InMemoryGiftCardStore(EventLog log, params GiftCard[] cards)
        {
            _log = log;
            foreach (var card in cards) _rows[card.Code] = card;
        }

        public async Task<GiftCard> FindAsync(string code)
        {
            await Task.Yield();
            GiftCard card;
            lock (_rows) _rows.TryGetValue(code, out card);
            _log.Add($"reads {code} ({(card == null ? "missing" : card.RedeemedByOrder == null ? "unused" : "used by " + card.RedeemedByOrder)})");
            return card;
        }

        public async Task SaveAsync(GiftCard card)
        {
            await Task.Yield();
            lock (_rows) _rows[card.Code] = card;
            _log.Add($"saves {card.Code} as used by {card.RedeemedByOrder}");
        }
    }

    /// <summary>Records every credit, so the test can count how often a card's money was paid out.</summary>
    public sealed class RecordingOrderLedger : IOrderLedger
    {
        private readonly List<(string OrderId, long AmountCents, string Reason)> _credits =
            new List<(string, long, string)>();
        private readonly EventLog _log;

        public RecordingOrderLedger(EventLog log) => _log = log;

        public async Task CreditAsync(string orderId, long amountCents, string reason)
        {
            await Task.Yield();
            lock (_credits) _credits.Add((orderId, amountCents, reason));
            _log.Add($"credits {orderId} with {amountCents} cents ({reason})");
        }

        public List<string> OrdersCreditedFor(string reason)
        {
            lock (_credits) return _credits.Where(c => c.Reason == reason).Select(c => c.OrderId).ToList();
        }
    }
}
