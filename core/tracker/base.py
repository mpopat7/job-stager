"""Base tracker interface for application logging."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional
from core.scrapers.base import JobPosting


class BaseTracker(ABC):
    """Abstract tracker interface."""

    @abstractmethod
    def log_application(
        self,
        job: JobPosting,
        grad_year: int,
        stage: str = "Applied",
        notes: str = "",
    ) -> bool:
        """Log a staged or submitted application to the tracker."""
        pass
