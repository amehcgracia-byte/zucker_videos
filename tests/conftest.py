"""Tests never ask the person's own Claude to draw: storyboard drawings use the local
painter (usually faked) unless a test chooses otherwise. Nor do they look through the
person's real projects for library pictures: a test lists its own."""
import pytest


@pytest.fixture(autouse=True)
def _drawings_by_the_local_painter(monkeypatch):
    monkeypatch.setenv("ZUCKER_DRAWINGS", "painter")


@pytest.fixture(autouse=True)
def _no_real_projects_in_the_library(monkeypatch):
    from core import show_library
    monkeypatch.setattr(show_library, "shelf_projects", lambda: [])
