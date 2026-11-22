# dotnet-checkout

`src/Checkout` (netstandard2.0, usable from .NET Framework 4.7.2+ and .NET) retries a payment on
timeout. `tests/Checkout.Tests` targets **net8.0 and net48** and holds one verification test
marked `[Trait("Category", "Verification")]`. FsCheck generates which event happens next (the
timer rings, a request reaches the provider, a reply arrives) and drives the real `CheckoutSession`.

```sh
dotnet test --filter Category=Verification      # both frameworks; net48 needs Windows
python <kit>/verikit.py init --no-skills        # once, inside a git repo
python verification/verikit.py run
```

Keep the path short on Windows: .NET Framework test discovery fails silently near 260 characters.
