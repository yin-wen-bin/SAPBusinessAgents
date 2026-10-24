# Operational account-item source semantics

Keep CompanyCode, FiscalYear, AccountingDocument and AccountingDocumentItem as the
entry-view key and preserve FinancialAccountType and SpecialGLCode for classification.
Use live metadata, not this document, to establish field availability and types.

The view can include ordinary postings, actual special-GL postings and noted requests.
SpecialGLCode is configuration-dependent: never hardcode F (or another letter) as a
universal inclusion/exclusion rule. A current I_SpecialGLCode configuration observation
is not proof that historical configuration was identical. Missing or contradictory
classification evidence remains unknown. No guessed default may make it business-complete.

At a historical cutoff, a clearing on or before the cutoff is not open; clearing after
the cutoff remains open at that time. Missing clearing fields and contradictory clearing
references are gaps, not an open state. Do not replace this with today's IsCleared flag.

Retain posted balances, noted pending requests and invoice subsets separately, with
Decimal amounts by currency. Already signed amounts must not be signed again. Noted
requests can appear in a historical item list without contributing to posted AP balance.
All original rows and the reasons for their metric membership remain auditable.

The reviewed machine role is in `config/sap-source-semantics.json`. The independent
baseline must follow the same reviewed business definition, not copy the candidate plan
or switch to its source merely to obtain MATCH.
