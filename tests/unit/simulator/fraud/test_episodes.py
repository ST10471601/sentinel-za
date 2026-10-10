from datetime import UTC, date, datetime

from sentinel.domain.labels import FraudType
from sentinel.domain.transactions import Channel, Direction, TransactionAuthMethod
from sentinel.simulator.drafts import TransactionDraft
from sentinel.simulator.fraud.episodes import ScriptedEpisode


class NoBalances:
    def available_cents(self, account_id: str) -> int:
        raise AssertionError("a scripted episode never looks at balances")


def draft_at(moment: datetime) -> TransactionDraft:
    return TransactionDraft(
        account_id="ACC-0000001",
        direction=Direction.DEBIT,
        amount_cents=5_000,
        event_time=moment,
        channel=Channel.CARD_NOT_PRESENT,
        auth_method=TransactionAuthMethod.THREE_DS,
        country_code="US",
        card_id="CRD-0000001",
        merchant_id="MER-0000001",
    )


def test_scripted_episode_acts_on_sa_calendar_days() -> None:
    # 21:50 UTC is 23:50 in SA; 22:10 UTC is already the next SA day.
    before_midnight = draft_at(datetime(2026, 1, 14, 21, 50, tzinfo=UTC))
    after_midnight = draft_at(datetime(2026, 1, 14, 22, 10, tzinfo=UTC))
    episode = ScriptedEpisode(
        scenario_id="SCN-0000001",
        fraud_type=FraudType.CARD_NOT_PRESENT,
        victim_customer_id="CUS-0000001",
        params={},
        drafts=(before_midnight, after_midnight),
    )

    assert episode.days() == {date(2026, 1, 14), date(2026, 1, 15)}
    assert episode.act(date(2026, 1, 14), NoBalances()).attempts == [before_midnight]
    assert episode.act(date(2026, 1, 15), NoBalances()).attempts == [after_midnight]
    assert episode.act(date(2026, 1, 16), NoBalances()).attempts == []
