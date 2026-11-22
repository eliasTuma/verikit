# react-pay-button

A Pay button that must start at most one payment however it is clicked. The test generates
bursts of same-tick clicks interleaved with server replies (fast-check scheduler + Testing
Library + Vitest). The broken variant replaces the `useRef` guard with a check on React state.
That looks right and still pays twice when two clicks land before React re-renders.

```sh
npm install
npx vitest run -t verification
python <kit>/verikit.py init --no-skills && python verification/verikit.py run
```
