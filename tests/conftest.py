"""Tests never ask the person's own Claude to draw: storyboard drawings use the local
painter (usually faked) unless a test chooses otherwise."""
import pytest


@pytest.fixture(autouse=True)
def _drawings_by_the_local_painter(monkeypatch):
    monkeypatch.setenv("ZUCKER_DRAWINGS", "painter")
