"""Exact entitlement arithmetic and strict settlement guard. NOT a market backtest.
No use of adjusted prices as executable fractions; no inferred cash payment dates.
Only engine's plain integer forward split is currently integration-ready.
"""
from decimal import Decimal,ROUND_FLOOR
from strategies.impl.riseConfirmScale import DataError

def entitlements(old_whole_shares,ratios):
    if type(old_whole_shares) is not int or old_whole_shares<0:raise DataError('Whole nonnegative entitled shares required')
    result={}
    for leg,ratio in ratios.items():
        v=Decimal(old_whole_shares)*Decimal(str(ratio));whole=int(v.to_integral_value(rounding=ROUND_FLOOR))
        result[leg]={'whole_shares':whole,'fraction':str(v-Decimal(whole))}
    return result

def verified_fraction_cash(fraction,terms,observed_prices,asof):
    """Cash becomes usable only on verified payment date, after valuation is observable.
    Taxes/rounding cannot be guessed. No receivable market valuation inferred here.
    """
    for k in ['valuation_date','payment_date','rounding','net_retention','source_ref','terms_verified']:
        if k not in terms or terms[k] is None:raise DataError('Incomplete fraction settlement terms: '+k)
    if terms['terms_verified'] is not True:raise DataError('Settlement terms not source verified')
    if not terms['valuation_date']<=terms['payment_date']<=asof:raise DataError('Cannot use future valuation or undelivered cash')
    if terms['rounding']!='floor_krw':raise DataError('Only explicit documented floor-KRW convention supported')
    if terms['valuation_date'] not in observed_prices:raise DataError('Official valuation-date close absent')
    v=Decimal(str(fraction))*Decimal(str(observed_prices[terms['valuation_date']]))*Decimal(str(terms['net_retention']))
    return int(v.to_integral_value(rounding=ROUND_FLOOR))
