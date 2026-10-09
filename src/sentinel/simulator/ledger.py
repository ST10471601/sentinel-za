"""The bank's ledger: running balances, daily limits and approve/decline decisions."""

from collections.abc import Sequence
from datetime import date

from sentinel.core.datetimes import to_sast
from sentinel.domain.accounts import Account, AccountType, Card
from sentinel.domain.transactions import Channel, DeclineReason, Direction, TransactionStatus
from sentinel.simulator.drafts import Settlement, TransactionDraft


class Ledger:
    """Balances and today's limit usage for every account and card.

    For credit-card accounts the balance is the amount owed (as in ``Account``), so a
    purchase raises it and a repayment lowers it.
    """

    def __init__(self, accounts: Sequence[Account], cards: Sequence[Card]) -> None:
        self._accounts = {account.account_id: account for account in accounts}
        self._cards = {card.card_id: card for card in cards}
        self._balances = {account.account_id: account.opening_balance_cents for account in accounts}
        # Limit key -> (SA calendar day, cents used that day). One entry per key.
        self._usage: dict[str, tuple[date, int]] = {}

    def balance_cents(self, account_id: str) -> int:
        """Current balance, or amount owed for a credit-card account."""
        return self._balances[account_id]

    def available_cents(self, account_id: str) -> int:
        """How much can be spent right now. Negative when overdrawn."""
        account = self._accounts[account_id]
        if account.credit_limit_cents is not None:
            return account.credit_limit_cents - self._balances[account_id]
        return self._balances[account_id]

    def settle(self, draft: TransactionDraft) -> Settlement:
        """Approve or decline a draft, and post it to the balance when approved."""
        if draft.direction is Direction.DEBIT:
            reason = self._decline_reason(draft)
            if reason is not None:
                balance = self._balances[draft.account_id]
                return Settlement(TransactionStatus.DECLINED, reason, balance)
            self._use_limit(draft)

        balance = self._post(draft)
        return Settlement(TransactionStatus.APPROVED, None, balance)

    def _decline_reason(self, draft: TransactionDraft) -> DeclineReason | None:
        if draft.amount_cents > self.available_cents(draft.account_id):
            return DeclineReason.INSUFFICIENT_FUNDS

        limit = self._daily_limit(draft)
        if limit is None:
            return None
        key, limit_cents = limit
        if self._used_today(key, draft) + draft.amount_cents > limit_cents:
            return DeclineReason.LIMIT_EXCEEDED
        return None

    def _daily_limit(self, draft: TransactionDraft) -> tuple[str, int] | None:
        """The limit a debit counts towards, as (key, cents). Debit orders have none."""
        account = self._accounts[draft.account_id]
        match draft.channel:
            case Channel.ATM:
                card = self._card(draft)
                return f"{card.card_id}:atm", card.daily_atm_limit_cents
            case Channel.CARD_PRESENT | Channel.CARD_NOT_PRESENT:
                card = self._card(draft)
                return f"{card.card_id}:pos", card.daily_pos_limit_cents
            case Channel.PAYSHAP:
                return f"{account.account_id}:payshap", account.payshap_daily_limit_cents
            case Channel.EFT | Channel.INTERNAL_TRANSFER:
                return f"{account.account_id}:transfer", account.daily_transfer_limit_cents
            case _:
                return None

    def _card(self, draft: TransactionDraft) -> Card:
        if draft.card_id is None:
            raise ValueError(f"{draft.channel} draft has no card_id")
        return self._cards[draft.card_id]

    def _used_today(self, key: str, draft: TransactionDraft) -> int:
        day, used = self._usage.get(key, (None, 0))
        return used if day == _sa_day(draft) else 0

    def _use_limit(self, draft: TransactionDraft) -> None:
        limit = self._daily_limit(draft)
        if limit is not None:
            key = limit[0]
            self._usage[key] = (_sa_day(draft), self._used_today(key, draft) + draft.amount_cents)

    def _post(self, draft: TransactionDraft) -> int:
        """Apply an approved draft to the balance and return the new balance."""
        change = draft.amount_cents
        if draft.direction is Direction.DEBIT:
            change = -change
        if self._accounts[draft.account_id].account_type is AccountType.CREDIT_CARD:
            change = -change  # the balance is the amount owed

        self._balances[draft.account_id] += change
        return self._balances[draft.account_id]


def _sa_day(draft: TransactionDraft) -> date:
    """Daily limits reset at midnight SA time, not UTC."""
    return to_sast(draft.event_time).date()
