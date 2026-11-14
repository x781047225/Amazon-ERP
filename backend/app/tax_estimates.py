"""Local estimates only; never replace actual Amazon tax or settlement amounts."""
from decimal import Decimal, ROUND_HALF_UP

VAT_RATE = Decimal("0.20")

def included_vat(gross, rate=VAT_RATE):
    """Extract VAT from an inclusive amount using the configured estimate rate."""
    return (Decimal(str(gross)) * rate / (Decimal(1) + rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
