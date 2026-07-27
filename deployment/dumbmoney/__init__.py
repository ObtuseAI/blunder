"""Dry-run-first deployment tooling for the DumbMoney Windows release.

This package deliberately contains no broker client and no service installation
primitive.  It prepares and validates immutable release evidence, migration
packages, readiness descriptors, and operator plans.
"""

from .common import ValidationIssue, ValidationReport

__all__ = ["ValidationIssue", "ValidationReport"]
