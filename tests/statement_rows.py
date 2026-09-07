"""Builders for synthetic statement pages.

Real statements are full of account numbers, addresses and payee names, so
none can be committed as a fixture. These builders instead reproduce the part
that actually matters — where each word sits horizontally — using the column
positions measured from the genuine PDFs.
"""
from etl.parsers.pdf_layout import Row, Word, make_row

CHAR_W = 6.0


def text_words(text: str, x0: float) -> list[Word]:
    """Lay out left-aligned words, as a description column does."""
    words, x = [], x0
    for token in text.split():
        width = len(token) * CHAR_W
        words.append(Word(token, x, x + width))
        x += width + 3
    return words


def money(text: str, x1: float) -> Word:
    """Lay out a right-aligned amount ending at a column's right edge."""
    return Word(text, x1 - len(text) * CHAR_W, x1)


def line(*parts, page: int = 1) -> Row:
    """Build a row from Words and lists of Words."""
    words: list[Word] = []
    for part in parts:
        if part is None:
            continue
        words.extend(part if isinstance(part, list) else [part])
    return make_row(words, page)


def _header(labels: list[tuple[str, float, float]], page: int) -> Row:
    return make_row([Word(t, x0, x1) for t, x0, x1 in labels], page)


class SmartAccess:
    """CBA transaction-account geometry."""
    DATE_X0, DESC_X0 = 58.0, 89.0
    DEBIT_X1, CREDIT_X1, BALANCE_X1 = 389.0, 442.0, 540.0

    @classmethod
    def header(cls, page: int = 1) -> Row:
        return _header([
            ("Date", 58, 81), ("Transaction", 89, 149),
            ("Debit", 362, 389), ("Credit", 411, 442), ("Balance", 499, 540),
        ], page)

    @classmethod
    def balance(cls, text: str) -> list[Word]:
        """CBA right-aligns the number and its CR marker together.

        They are emitted as separate words, as the real extraction does at the
        tolerance this statement needs, so the rejoining logic is exercised.
        """
        if not text.upper().endswith(("CR", "DR")):
            return [money(text, cls.BALANCE_X1)]
        number, marker = text[:-2], text[-2:]
        split = cls.BALANCE_X1 - len(marker) * CHAR_W
        return [
            Word(number, split - len(number) * CHAR_W, split),
            Word(marker, split, cls.BALANCE_X1),
        ]

    @classmethod
    def row(cls, date=None, desc="", debit=None, credit=None, balance=None, page=1) -> Row:
        return line(
            text_words(date, cls.DATE_X0) if date else None,
            text_words(desc, cls.DESC_X0) if desc else None,
            money(debit, cls.DEBIT_X1) if debit else None,
            money(credit, cls.CREDIT_X1) if credit else None,
            cls.balance(balance) if balance else None,
            page=page,
        )


class Bankwest:
    """Bankwest loan/offset geometry."""
    DATE_X0, PART_X0 = 65.0, 116.0
    DEBIT_X1, CREDIT_X1, BALANCE_X1 = 380.0, 462.0, 546.0
    # "DR" is printed past the column's right edge rather than inside it.
    DR_OVERHANG = 12.6

    @classmethod
    def header(cls, page: int = 1) -> Row:
        return _header([
            ("Date", 65, 87), ("Particulars", 116, 167),
            ("Debit", 355, 380), ("Credit", 433, 462), ("Balance", 508, 546),
        ], page)

    @classmethod
    def balance(cls, text: str) -> Word:
        """One atomic token, with any DR/CR marker overhanging the column."""
        overhang = cls.DR_OVERHANG if text.upper().endswith(("CR", "DR")) else 0.0
        return money(text, cls.BALANCE_X1 + overhang)

    @classmethod
    def row(cls, date=None, desc="", debit=None, credit=None, balance=None, page=1) -> Row:
        return line(
            text_words(date, cls.DATE_X0) if date else None,
            text_words(desc, cls.PART_X0) if desc else None,
            money(debit, cls.DEBIT_X1) if debit else None,
            money(credit, cls.CREDIT_X1) if credit else None,
            cls.balance(balance) if balance else None,
            page=page,
        )


class CreditCard:
    """CBA credit card geometry — a single amount column."""
    DATE_X0, DESC_X0 = 52.0, 92.0
    AMOUNT_X1 = 563.0

    @classmethod
    def header(cls, page: int = 1) -> Row:
        return _header([
            ("Date", 52, 72), ("Transaction", 92, 143), ("details", 145, 174),
            ("Amount", 510, 544), ("(A$)", 546, 563),
        ], page)

    @classmethod
    def row(cls, date=None, desc="", amount=None, page=1) -> Row:
        return line(
            text_words(date, cls.DATE_X0) if date else None,
            text_words(desc, cls.DESC_X0) if desc else None,
            money(amount, cls.AMOUNT_X1) if amount else None,
            page=page,
        )


class HSBCAccount:
    """HSBC everyday-account geometry, measured from a real statement.

    The later layout, whose columns are headed "Debits/Withdrawals" and
    "Credits/Deposits".
    """
    DATE_X0, DESC_X0 = 46.1, 98.5
    DEBIT_X1, CREDIT_X1, BALANCE_X1 = 379.1, 472.7, 550.6
    HEADER = [
        ("Date", 46.1, 63.1), ("Transaction", 98.5, 142.0), ("Details", 144.0, 170.0),
        ("Debits/Withdrawals", 305.6, 379.1), ("Credits/Deposits", 412.2, 472.7),
        ("Balance", 520.1, 550.6),
    ]
    # The account's own heading, above the column header. Its "Balance" figure
    # sits well left of the balance column, so it is read as text, not as a
    # column amount.
    HEADING_BALANCE_X1 = 523.4

    @classmethod
    def header(cls, page: int = 1) -> Row:
        return _header(cls.HEADER, page)

    @classmethod
    def heading(cls, name="DAY TO DAY ACCOUNT", number="519410412",
                balance="0.00", page: int = 1) -> Row:
        return line(
            text_words(f"{name} BSB No. 342094 Account {number} Currency AUD Balance",
                       46.1),
            money(balance, cls.HEADING_BALANCE_X1),
            page=page,
        )

    @classmethod
    def row(cls, date=None, desc="", debit=None, credit=None, balance=None, page=1) -> Row:
        return line(
            text_words(date, cls.DATE_X0) if date else None,
            text_words(desc, cls.DESC_X0) if desc else None,
            money(debit, cls.DEBIT_X1) if debit else None,
            money(credit, cls.CREDIT_X1) if credit else None,
            money(balance, cls.BALANCE_X1) if balance else None,
            page=page,
        )

    @classmethod
    def totals(cls, debits="0.00", credits="0.00", page: int = 1) -> Row:
        return line(
            text_words("Transaction Total", cls.DESC_X0),
            money(debits, cls.DEBIT_X1),
            money(credits, cls.CREDIT_X1),
            page=page,
        )


class HSBCFinancial(HSBCAccount):
    """The older "Financial Statement" layout: "Debit" and "Credit", further left."""
    DATE_X0, DESC_X0 = 57.0, 116.5
    DEBIT_X1, CREDIT_X1, BALANCE_X1 = 375.9, 460.6, 541.4
    HEADER = [
        ("Date", 57.0, 74.0), ("Transaction", 116.5, 160.0), ("Details", 162.0, 188.0),
        ("Debit", 356.4, 375.9), ("Credit", 438.6, 460.6), ("Balance", 510.9, 541.4),
    ]
    HEADING_BALANCE_X1 = 508.1

    @classmethod
    def heading(cls, name="AUD DAY TO DAY ACCOUNT", number="519410412",
                balance="0.00", page: int = 1) -> Row:
        return line(
            text_words(f"{name} Branch DIRECT BNKING BSB 342094 "
                       f"Account No {number} Balance", 41.2),
            money(balance, cls.HEADING_BALANCE_X1),
            page=page,
        )
