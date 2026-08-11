"""Deterministic, read-only GR/IR ageing analysis."""

from .analyzer import GrIrAnalyzer, GrIrAnalysisError
from .evidence import EvidenceSnapshot, EvidenceValidationError
from .model import AnalysisResult, QueryParameters

__all__ = [
    "AnalysisResult",
    "EvidenceSnapshot",
    "EvidenceValidationError",
    "GrIrAnalysisError",
    "GrIrAnalyzer",
    "QueryParameters",
]
