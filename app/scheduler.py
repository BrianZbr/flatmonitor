"""
FlatMonitor - Scheduler

Job producer that tracks timing and pushes DomainConfig objects to the job queue.
"""

import time
from queue import Queue
from datetime import datetime, timedelta
from typing import Dict, List

from app.models import DomainConfig


class Scheduler:
    """Schedules checks for domains based on their intervals."""

    def __init__(self, domains: List[DomainConfig]):
        self.domains = domains
        # Track next run time for each domain
        self.next_run: Dict[str, float] = {}
        self._initialize_schedule()

    def _initialize_schedule(self) -> None:
        """Initialize the schedule with all domains ready to run immediately."""
        now = time.time()
        for domain in self.domains:
            # Stagger initial checks slightly to avoid thundering herd
            self.next_run[domain.id] = now

    def tick(self, job_queue: Queue) -> int:
        """
        Check for domains that are due and add them to the job queue.
        Returns the number of jobs added.
        """
        now = time.time()
        jobs_added = 0

        for domain in self.domains:
            if now >= self.next_run[domain.id]:
                # Add to queue
                job_queue.put(domain)
                jobs_added += 1

                # Schedule next run
                self.next_run[domain.id] = now + domain.interval_seconds

        return jobs_added

    def get_next_wait_time(self) -> float:
        """Get the time until the next domain is due (for optimized sleeping)."""
        if not self.next_run:
            return 1.0

        now = time.time()
        min_wait = min(next_time - now for next_time in self.next_run.values())

        # Return max of min_wait and 0.1s (don't wait negative time)
        return max(min_wait, 0.1)

    def add_domain(self, domain: DomainConfig) -> None:
        """Add a new domain to the scheduler."""
        self.domains.append(domain)
        self.next_run[domain.id] = time.time()

    def remove_domain(self, domain_id: str) -> None:
        """Remove a domain from the scheduler."""
        self.domains = [d for d in self.domains if d.id != domain_id]
        if domain_id in self.next_run:
            del self.next_run[domain_id]
