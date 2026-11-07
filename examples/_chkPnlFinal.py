# -*- coding: utf-8 -*-
"""Final numbers for user."""
avg = 5810
qty = 172
exit_px = 5700
buy_amt = avg * qty
sell_amt = exit_px * qty
pnl = sell_amt - buy_amt
pct = pnl / buy_amt * 100
print(f"한화 buy {avg}x{qty}={buy_amt} sell {exit_px}x{qty}={sell_amt} pnl={pnl} pct={pct:.4f}%")
print(f"today corrected: {-10520 + pnl}")
print(f"month corrected from DB 922702: {922702 - 980400 + pnl}")
print(f"month from live rounds + 한화: {-57698 + pnl}")
