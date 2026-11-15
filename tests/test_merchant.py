"""Merchant keys: the per-transaction noise goes, the merchant stays."""
import pytest

from etl.merchant import direction, merchant_key


@pytest.mark.parametrize("description, key", [
    # ING: receipt, date and card digits change every time
    ("Visa Purchase - Receipt 100001 EXAMPLE CAFE EXAMPLETOWN Date 24/11/24 Card 1234",
     "EXAMPLE CAFE EXAMPLETOWN"),
    ("Visa Purchase - Receipt 200002 EXAMPLE CAFE EXAMPLETOWN Date 01/03/25 Card 1234",
     "EXAMPLE CAFE EXAMPLETOWN"),
    # How it was paid is not who was paid
    ("Direct Debit - Receipt 100002 EXAMPLE ENERGY 999999", "EXAMPLE ENERGY"),
    ("Osko Deposit - Receipt 100003 Example Person", "EXAMPLE PERSON"),
    ("Everyday round up - Receipt 100004 Oe Card xx1234 Transaction $10.00 Transfer From 100000001",
     "EVERYDAY ROUND UP OE TRANSACTION TRANSFER FROM"),
    # Fixed-width card statements: the first column is the merchant
    ("WOOLWORTHS      0101     EXAMPLETOWN  NS", "WOOLWORTHS"),
    ("PAYPAL *EXAMPLESHOP     10000000001  NL", "PAYPAL EXAMPLESHOP"),
    ("AIRBNB * HMAB12CD34     LONDON", "AIRBNB"),
    # Reference codes and foreign amounts
    ("Grab* A-1ABC2DEF3G EXAMPLE CITY (100000.00 VND)", "GRAB EXAMPLE CITY"),
    ("BOLT.EU/O/1234567890 Exampleville (20.00 EUR)", "BOLT EU O EXAMPLEVILLE"),
    # Trailing state and city words
    ("TRANSPORTFORNSW TAP SYDNEY", "TRANSPORTFORNSW TAP"),
    ("EXAMPLE LAKSA EXAMPLETOWN NSW", "EXAMPLE LAKSA EXAMPLETOWN"),
])
def test_noise_is_removed(description, key):
    assert merchant_key(description) == key


@pytest.mark.parametrize("description, key", [
    ("7-ELEVEN 1234 EXAMPLETOWN", "7 ELEVEN EXAMPLETOWN"),   # one digit is part of the name
    ("3M AUSTRALIA", "3M"),
    ("SYDNEY WATER", "SYDNEY WATER"),                        # a place word that is the merchant
    ("NETFLIX.COM", "NETFLIX COM"),
])
def test_merchant_names_survive(description, key):
    assert merchant_key(description) == key


def test_same_merchant_same_key_across_visits():
    a = merchant_key("COLES 0123   EXAMPLETOWN   NS")
    b = merchant_key("COLES 0456   OTHERTOWN     VI")
    assert a == b == "COLES"


def test_a_bare_type_word_is_kept():
    # Nothing follows the prefix, so it is the whole description.
    assert merchant_key("Deposit - Receipt 123456") == "DEPOSIT"


def test_direction():
    assert direction(-5.0) == "out" and direction(5.0) == "in"
