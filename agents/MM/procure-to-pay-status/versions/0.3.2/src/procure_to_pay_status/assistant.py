"""Application service for the P2P status assistant."""

from __future__ import annotations

from datetime import date

from .analyzer import P2PAnalyzer
from .extractor import extract_query_parameters
from .model import P2PReport, QueryParameters
from .port import P2PDataSource


class P2PStatusAssistant:
    """Natural-language entry point with a replaceable SAP data source."""

    def __init__(self, data_source: P2PDataSource, analyzer: P2PAnalyzer | None = None):
        self.data_source = data_source
        self.analyzer = analyzer or P2PAnalyzer()

    def ask(self, question: str, *, as_of: date | None = None) -> P2PReport:
        parameters = extract_query_parameters(question)
        return self.query(parameters, as_of=as_of)

    def query(self, parameters: QueryParameters, *, as_of: date | None = None) -> P2PReport:
        tables = self.data_source.load_purchase_order(parameters.po_number)
        return self.analyzer.analyze(tables, parameters, as_of=as_of or date.today())

