"""Ground truth: which fraud happened and which accounts are mules.

Only the simulator writes these, and only training and evaluation read them.
The scoring service must never see them.
"""

import json
from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, model_validator

from sentinel.core.datetimes import UtcDatetime
from sentinel.domain.accounts import AccountId
from sentinel.domain.base import Cents, DomainModel
from sentinel.domain.customers import CustomerId
from sentinel.domain.transactions import TransactionId

ScenarioId = Annotated[str, Field(pattern=r"^SCN-\d{7}$")]


class FraudType(StrEnum):
    """Kinds of fraud the simulator injects (see the SABRIC research in SZ-3)."""

    SIM_SWAP_TAKEOVER = "sim_swap_takeover"
    CARD_NOT_PRESENT = "card_not_present"
    CARD_PRESENT_LOST_STOLEN = "card_present_lost_stolen"
    CARD_PRESENT_COUNTERFEIT = "card_present_counterfeit"
    APP_VISHING = "app_vishing"
    APP_REMOTE_ACCESS = "app_remote_access"
    SUPPLIER_MANDATE = "supplier_mandate"
    PAYSHAP_DRAIN = "payshap_drain"
    MULE_LAYERING = "mule_layering"


class ScenarioInstance(DomainModel):
    """One injected fraud episode, from first to last fraudulent transaction."""

    scenario_id: ScenarioId
    fraud_type: FraudType
    victim_customer_id: CustomerId | None
    started_at: UtcDatetime
    ended_at: UtcDatetime
    total_amount_cents: Cents  # approved amounts only; zero if every attempt was declined
    params: str  # JSON object of the parameters used, for reproducibility

    @model_validator(mode="after")
    def check_times(self) -> Self:
        """An episode can't end before it starts."""
        if self.ended_at < self.started_at:
            raise ValueError("ended_at cannot be before started_at")
        return self

    @model_validator(mode="after")
    def check_victim(self) -> Self:
        """Every type has a victim except mule layering, which only moves stolen money."""
        is_layering = self.fraud_type is FraudType.MULE_LAYERING
        if is_layering and self.victim_customer_id is not None:
            raise ValueError("mule_layering has no victim_customer_id")
        if not is_layering and self.victim_customer_id is None:
            raise ValueError(f"{self.fraud_type} needs a victim_customer_id")
        return self

    @model_validator(mode="after")
    def check_params(self) -> Self:
        """Params must be a JSON object so they can be queried later."""
        try:
            parsed = json.loads(self.params)
        except json.JSONDecodeError as error:
            raise ValueError("params must be valid JSON") from error
        if not isinstance(parsed, dict):
            raise ValueError("params must be a JSON object")
        return self


class TransactionLabel(DomainModel):
    """Whether a transaction was fraud, and when the bank found out.

    Every transaction gets a label. reported_at is empty for normal transactions and
    for the few frauds the bank never hears about.
    """

    transaction_id: TransactionId
    is_fraud: bool
    fraud_type: FraudType | None
    scenario_id: ScenarioId | None
    reported_at: UtcDatetime | None

    @model_validator(mode="after")
    def check_fraud_fields(self) -> Self:
        """Fraud needs a type and scenario; normal transactions have no fraud fields."""
        has_fraud_fields = self.fraud_type is not None and self.scenario_id is not None
        has_any = self.fraud_type is not None or self.scenario_id is not None
        if self.is_fraud and not has_fraud_fields:
            raise ValueError("fraud labels need fraud_type and scenario_id")
        if not self.is_fraud and (has_any or self.reported_at is not None):
            raise ValueError("normal transactions have no fraud_type, scenario_id or reported_at")
        return self


class AccountLabel(DomainModel):
    """Whether an account is a money mule. Every account gets a label."""

    account_id: AccountId
    is_mule: bool
    mule_since: UtcDatetime | None

    @model_validator(mode="after")
    def check_mule_since(self) -> Self:
        """mule_since is set exactly when the account is a mule."""
        if self.is_mule != (self.mule_since is not None):
            raise ValueError("mule_since must be set for mules and empty otherwise")
        return self
