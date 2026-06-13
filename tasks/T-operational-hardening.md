# T-operational-hardening — margin forecast, kill switch, broker reconciliation, alert delivery

> **Source:** TARGET §5.9 + §6 + §7.9 + the 2026-06-08 autonomy audit. Umbrella for the
> operational slice a desk expects; mostly post-week, but S2 needs margin forecasting first.

## The gaps (four sub-lanes, can split into their own tasks when prioritised)
1. **Margin / assignment-capacity forecasting** — S2's line-capacity rule **is** a margin number
   (the course's InvWC); size it up front. Blocks S2 from running safely.
2. **Kill switch** — a book-level switch that flattens a strategy on a drawdown / vol-regime
   trigger (S2's kill condition; §6 requires it).
3. **Broker reconciliation** — broker **cash / position / fill** reconciliation, distinct from the
   internal `risk/reconciliation.py` (which reconciles greeks/positions internally).
4. **Alert delivery** — route the already-detected gateway-disconnect ALARM (keepalive + loud
   capture failure) to Telegram/email + a pre-close check ([[deferred-disconnect-alert]] memory;
   today detected-but-unrouted).

## Depends on
Margin forecast gates S2 live; reconciliation + kill switch gate any non-paper booking
([[T-fills-position-store]], 3B). Alert delivery is independent and cheap.

## Done criteria
Each sub-lane: margin forecast sizes S2 capacity; kill switch flattens on trigger; broker recon
flags cash/position/fill drift; disconnect ALARM reaches Telegram/email + pre-close check; gate green.
