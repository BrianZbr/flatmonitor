"""
Unit tests for scheduler.py
Tests job scheduling, queue management
"""

import pytest
import time
from queue import Queue
from unittest.mock import Mock

from app.scheduler import Scheduler
from app.models import DomainConfig


class TestScheduler:
    """Tests for Scheduler class."""

    @pytest.fixture
    def sample_domains(self):
        return [
            DomainConfig(id="test.site1", url="https://example1.com"),
            DomainConfig(id="test.site2", url="https://example2.com"),
        ]

    @pytest.fixture
    def scheduler(self, sample_domains):
        return Scheduler(sample_domains)

    @pytest.fixture
    def job_queue(self):
        return Queue()

    def test_initialization(self, scheduler, sample_domains):
        assert len(scheduler.domains) == 2
        assert len(scheduler.next_run) == 2

    def test_tick_adds_jobs_immediately(self, scheduler, job_queue, sample_domains):
        # First tick should add all domains (initialized to run immediately)
        jobs_added = scheduler.tick(job_queue)

        assert jobs_added == 2
        assert job_queue.qsize() == 2

        # Verify correct domains were added
        added_domains = set()
        while not job_queue.empty():
            domain = job_queue.get()
            added_domains.add(domain.id)

        assert added_domains == {"test.site1", "test.site2"}

    def test_tick_respects_interval(self, scheduler, job_queue):
        # First tick adds all jobs
        scheduler.tick(job_queue)

        # Clear the queue
        while not job_queue.empty():
            job_queue.get()

        # Immediate second tick should not add jobs (interval not elapsed)
        jobs_added = scheduler.tick(job_queue)

        assert jobs_added == 0
        assert job_queue.qsize() == 0

    def test_tick_after_interval(self, scheduler, job_queue):
        # First tick - update next_run times to past
        scheduler.tick(job_queue)
        while not job_queue.empty():
            job_queue.get()
        
        # Manually set next_run to past to simulate interval elapsed
        past_time = time.time() - 15
        for domain_id in scheduler.next_run:
            scheduler.next_run[domain_id] = past_time

        # Now tick should add jobs again
        jobs_added = scheduler.tick(job_queue)

        assert jobs_added == 2

    def test_get_next_wait_time(self, scheduler, job_queue):
        # After initial tick, next wait should be around interval time
        scheduler.tick(job_queue)

        wait_time = scheduler.get_next_wait_time()

        # Should be positive and less than or equal to max interval (60s fixed)
        assert wait_time > 0
        assert wait_time <= 60

    def test_get_next_wait_time_no_domains(self):
        empty_scheduler = Scheduler([])
        wait_time = empty_scheduler.get_next_wait_time()

        assert wait_time == 1.0  # Default when no domains

    def test_add_domain(self, scheduler):
        new_domain = DomainConfig(
            id="new.site",
            url="https://new.com"
        )

        scheduler.add_domain(new_domain)

        assert len(scheduler.domains) == 3
        assert new_domain.id in scheduler.next_run

    def test_remove_domain(self, scheduler, sample_domains):
        scheduler.remove_domain("test.site1")

        assert len(scheduler.domains) == 1
        assert scheduler.domains[0].id == "test.site2"
        assert "test.site1" not in scheduler.next_run

    def test_remove_nonexistent_domain(self, scheduler, sample_domains):
        # Should not raise error
        scheduler.remove_domain("nonexistent")

        assert len(scheduler.domains) == 2

    def test_tick_updates_next_run(self, scheduler, job_queue):
        original_next_run = dict(scheduler.next_run)

        scheduler.tick(job_queue)

        # Next run times should have been updated
        for domain_id in scheduler.next_run:
            assert scheduler.next_run[domain_id] > original_next_run[domain_id]
