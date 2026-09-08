# Material Shortage Procurement Response

Deterministic, GET-only MM Agent. The `shortage-procurement` CLI runs the same rule used by the Schema v2 workflow. Embedded SAP OData is primary; `sap-adt-table-export` is conditionally invoked only for explicit API evidence gaps.

```powershell
shortage-procurement --format markdown
```
