"""Staging adapters for supported ATS platforms."""

from typing import Optional
from core.adapters.ashby import AshbyAdapter
from core.adapters.base import BaseStagingAdapter, StagingResult
from core.adapters.greenhouse import GreenhouseAdapter
from core.adapters.lever import LeverAdapter
from core.adapters.workday import WorkdayAdapter
from core.scrapers.base import ATSProvider

ADAPTER_MAP = {
    ATSProvider.GREENHOUSE: GreenhouseAdapter(),
    ATSProvider.LEVER: LeverAdapter(),
    ATSProvider.ASHBY: AshbyAdapter(),
    ATSProvider.WORKDAY: WorkdayAdapter(),
}


def get_adapter(provider: ATSProvider) -> Optional[BaseStagingAdapter]:
    """Retrieve the staging adapter for a given ATS provider."""
    return ADAPTER_MAP.get(provider)


__all__ = [
    "BaseStagingAdapter",
    "StagingResult",
    "GreenhouseAdapter",
    "LeverAdapter",
    "AshbyAdapter",
    "WorkdayAdapter",
    "get_adapter",
]
