> Source: blueprint PDF, pages 48–49. Faithful transcription — see ../blueprint/README.md for governance status.

# Part XVIII — Frequently misunderstood implementation points

### Why not compute analytics directly inside the broker callback?

Because callbacks should be short and loss-tolerant. Heavy analytics inside callbacks increase the probability of dropped events and make historical replay impossible to align with live logic.

### Why keep rejected quotes?

Because rejected quotes are evidence. QC threshold tuning and operational debugging depend on understanding what was excluded and why.

### Why version configurations separately from code?

Because a threshold or scenario-grid change can alter economics without any code edit. Separate versioning preserves lineage.

### Why store both fitted parameters and grid values?

Because parameters are concise but not directly convenient for every downstream consumer. Grid values are easy to inspect, plot, and join to risk code.

### Why insist on replaying with the same code path as live?

Because dual paths drift. If historical and live analytics differ by implementation path, regressions become impossible to reason about.

### Why treat forwards as first-class outputs rather than internal intermediates?

Because forward quality drives moneyness, IV, surface shape, and risk. Hiding forward diagnostics deprives the platform of one of its most informative health measures.
