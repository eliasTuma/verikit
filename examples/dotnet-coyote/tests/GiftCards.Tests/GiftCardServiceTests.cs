using System.Threading.Tasks;
using Xunit;

namespace GiftCards.Tests
{
    /// <summary>The team's ordinary example tests. They run on the same (rewritten) dlls as the Coyote check.</summary>
    public class GiftCardServiceTests
    {
        [Fact]
        public async Task Redeeming_an_unused_card_credits_the_order()
        {
            var log = new EventLog();
            var ledger = new RecordingOrderLedger(log);
            var service = new GiftCardService(new InMemoryGiftCardStore(log, new GiftCard("GC-1", 2500)), ledger);

            Assert.Equal(RedeemResult.Redeemed, await service.RedeemAsync("GC-1", "order-1"));
            Assert.Equal(new[] { "order-1" }, ledger.OrdersCreditedFor("gift card GC-1"));
        }

        [Fact]
        public async Task A_card_cannot_be_redeemed_twice_in_a_row()
        {
            var log = new EventLog();
            var ledger = new RecordingOrderLedger(log);
            var service = new GiftCardService(new InMemoryGiftCardStore(log, new GiftCard("GC-1", 2500)), ledger);

            await service.RedeemAsync("GC-1", "order-1");
            Assert.Equal(RedeemResult.AlreadyRedeemed, await service.RedeemAsync("GC-1", "order-2"));
            Assert.Single(ledger.OrdersCreditedFor("gift card GC-1"));
        }

        [Fact]
        public async Task An_unknown_card_is_refused()
        {
            var log = new EventLog();
            var service = new GiftCardService(new InMemoryGiftCardStore(log), new RecordingOrderLedger(log));
            Assert.Equal(RedeemResult.NotFound, await service.RedeemAsync("nope", "order-1"));
        }
    }
}
