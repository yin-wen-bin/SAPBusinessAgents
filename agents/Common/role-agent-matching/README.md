# Role-to-Agent Matching Assistant

Runtime-driven, read-only platform assistant for matching user-provided role descriptions and user-selected SAP process documents to the local Agent catalog. Users may provide either source type or combine both. User descriptions remain explicitly labeled and are never presented as verified SAP facts or formal policy documents. The assistant is intentionally excluded from deterministic Agent execution and workflow composition.

Agent discovery evaluates the complete active catalog through bounded, valid JSON pages. Only `full` and `partial` coverage is presented as a match; `none` candidates remain available as rejected audit evidence. Capability gaps and workflow suggestions are emitted only after every catalog page and operation-to-Agent pair has been evaluated successfully at one catalog digest.
