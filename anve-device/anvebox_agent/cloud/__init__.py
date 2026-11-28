"""The box's link to ANVE's servers, behind one interface so it can later talk
to a local hub instead (PLAN.md 4.5)."""

from .base import CloudLink

__all__ = ["CloudLink"]
