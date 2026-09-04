from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from .aging import build_aging, days_overdue
from .drafting import build_communication_draft
from .intent import detect_intent, resolve_customer
from .matching import match_receipts
from .models import MatchStatus, PaymentHistory, QueryIntent
from .ports import ARDataGateway
from .scoring import assess_risk


class ARCollectionAssistant:
    """Natural-language orchestration over read-only AR data."""

    def __init__(self, gateway: ARDataGateway) -> None:
        self.gateway = gateway

    def query(self, query: str, as_of: date | None = None) -> dict[str, object]:
        as_of = as_of or date.today()
        snapshot = self.gateway.load_snapshot(as_of)
        intent = detect_intent(query)
        target_customer_id = resolve_customer(query, snapshot.accounts)
        matches = match_receipts(
            snapshot.unmatched_receipts,
            snapshot.open_items,
            snapshot.accounts,
            as_of,
        )
        receipts_by_id = {receipt.receipt_id: receipt for receipt in snapshot.unmatched_receipts}
        history_by_customer = {history.customer_id: history for history in snapshot.payment_history}
        items_by_customer = {
            account.customer_id: [item for item in snapshot.open_items if item.customer_id == account.customer_id]
            for account in snapshot.accounts
        }

        week_end = as_of + timedelta(days=6 - as_of.weekday())
        customer_results: list[dict[str, object]] = []
        for account in snapshot.accounts:
            if target_customer_id and account.customer_id != target_customer_id:
                continue
            items = items_by_customer[account.customer_id]
            history = history_by_customer.get(
                account.customer_id,
                PaymentHistory(account.customer_id, Decimal("0"), Decimal("1"), 0),
            )
            customer_matches = [match for match in matches if match.candidate_customer_id == account.customer_id]
            assessment = assess_risk(
                account,
                items,
                history,
                receipts_by_id,
                customer_matches,
                as_of,
            )
            overdue_items = [item for item in items if days_overdue(item, as_of) > 0]
            covered_documents = {
                match.candidate_document_id
                for match in customer_matches
                if match.status in {MatchStatus.EXACT, MatchStatus.LIKELY}
                and match.candidate_document_id
            }
            result = {
                "customer": account,
                "aging": build_aging(items, as_of),
                "risk": assessment,
                "collection_priority": {
                    "priority": assessment.priority,
                    "next_action_date": assessment.next_action_date,
                    "included_this_week": assessment.next_action_date <= week_end,
                },
                "payment_match_suggestions": customer_matches,
                "communication_draft": build_communication_draft(
                    account,
                    overdue_items,
                    assessment,
                    covered_documents,
                ),
            }

            if intent == QueryIntent.LIST_WEEKLY_COLLECTIONS:
                if assessment.net_collection_amount <= 0 or assessment.next_action_date > week_end:
                    continue
            elif intent == QueryIntent.LIST_UNMATCHED_RECEIPTS:
                continue
            customer_results.append(result)

        priority_order = {"P1": 0, "P2": 1, "P3": 2, "P4": 3, "HOLD_REVIEW": 4}
        customer_results.sort(
            key=lambda result: (
                priority_order[result["risk"].priority],  # type: ignore[union-attr]
                -result["risk"].score,  # type: ignore[union-attr]
                result["customer"].customer_id,  # type: ignore[union-attr]
            )
        )

        totals_by_currency: dict[str, Decimal] = {}
        actionable_totals_by_currency: dict[str, Decimal] = {}
        for result in customer_results:
            currency = result["customer"].currency  # type: ignore[union-attr]
            totals_by_currency[currency] = (
                totals_by_currency.get(currency, Decimal("0"))
                + result["risk"].net_collection_amount  # type: ignore[union-attr]
            )
            if result["risk"].priority != "HOLD_REVIEW":  # type: ignore[union-attr]
                actionable_totals_by_currency[currency] = (
                    actionable_totals_by_currency.get(currency, Decimal("0"))
                    + result["risk"].net_collection_amount  # type: ignore[union-attr]
                )

        unmatched_receipts = []
        for match in matches:
            if match.status == MatchStatus.UNMATCHED:
                unmatched_receipts.append(
                    {
                        "receipt": receipts_by_id[match.receipt_id],
                        "match_suggestion": match,
                        "recommended_action": "manual_research_in_feban",
                    }
                )

        request_id = str(uuid5(NAMESPACE_URL, f"ar-collection:{as_of.isoformat()}:{query}"))
        return {
            "schema_version": "1.0",
            "request_id": request_id,
            "query": query,
            "intent": intent,
            "as_of": as_of,
            "generated_at": datetime.now(timezone.utc),
            "summary": {
                "customer_count": len(customer_results),
                "totals_by_currency": totals_by_currency,
                "actionable_totals_by_currency": actionable_totals_by_currency,
                "high_risk_customer_count": sum(
                    1 for result in customer_results if result["risk"].risk_level == "high"  # type: ignore[union-attr]
                ),
                "unmatched_receipt_count": len(unmatched_receipts),
            },
            "customers": customer_results,
            "unmatched_bank_receipts": unmatched_receipts,
            "controls": {
                "sap_access": "read_only",
                "payment_clearing_posted": False,
                "communication_auto_send": False,
                "human_review_required": True,
            },
            "data_lineage": {
                "source_system": snapshot.source_system,
                "extracted_at": snapshot.extracted_at,
                "sap_scope": ["FI-AR", "SD-Billing", "Bank Accounting"],
            },
        }
