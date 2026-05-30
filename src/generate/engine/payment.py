"""Layer 6 — Payment (D18).

Per-transaction payment fields. Tender/network/wallet enrollment
are locked at Layer 3 (D16.3 — one card per customer). Layer 6
sets the three per-transaction fields D16 deferred:

* Entry mode (D18.1) — conditioned on customer (wallet flag + age)
  × merchant (segment, terminal capability) × daypart.
* Wallet-at-tap (D18.2) — gated by the D16 wallet-enrollment flag;
  enrolled → ~55-70% phone-tap; population-wide ~16-20%.
* Connectivity type (D18.3) — set by store terminal form factor
  (countertop vs counter+drive-thru), not consumer behavior.

Stage 4.6 implements; Stage 3 ships a stub.
"""

# Stage 4.6 will implement.
