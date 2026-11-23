-- Tables of the coupon service. The tests apply this file to an empty schema.

CREATE TABLE coupons (
  code         text PRIMARY KEY,
  amount_cents integer NOT NULL CHECK (amount_cents > 0),
  redeemed_by  text,          -- order that used the coupon; NULL = still unused
  redeemed_at  timestamptz
);

-- One row per discount given to an order. This is the money.
-- There is no UNIQUE (coupon_code) here on purpose: the guard of this example is the
-- conditional UPDATE in src/coupons.js. A unique constraint would be a second guard, and
-- every guard needs its own broken variant (see README, "Limits").
CREATE TABLE order_discounts (
  order_id     text PRIMARY KEY,
  coupon_code  text NOT NULL REFERENCES coupons (code),
  amount_cents integer NOT NULL
);
