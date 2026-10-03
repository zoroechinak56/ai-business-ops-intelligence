# Evidence-Grounded AI Reasoner

Run an explanation for the latest saved root-cause report:

```powershell
python -m src.ai_layer.reasoner --question "Why did fulfilment fall this month?"
```

The reasoner reads `data/processed/root_cause_latest.json` (or the configured
`DATA_PROCESSED_DIR`) and queries the configured database for supplier late
rates using SQL query 05 and SKU-group stock-outs during `period_b` using the
same definition as SQL query 06. It selects the highest negative
volume-weighted contribution for warehouse and SKU group. The supplier in the
Evidence bullet is selected independently from SQL query 05: it is the supplier
with the largest positive difference from the all-supplier late-rate average.
The bullet states its late rate, the average, and the gap in percentage points.
If no supplier is above average, the reasoner says so and does not name one.
The SKU group and recommendation are selected independently from the period-B
results of SQL query 06: the group with the highest stock-out rate. Its bullet
states the rate, stock-out snapshots and total snapshots for that group, and
its share of all period-B stock-out snapshots. The recommendation is validated
to reference that same selected group.

The response contains one Finding, three ordered Evidence bullets, and one or
two Recommendation bullets. Its only evidence is the computed root-cause
fields, supplier late rates and all-supplier average, and period-specific
stock-out metrics. A deterministic renderer is used when `LLM_API_KEY` is
unset. Set `LLM_MODEL` in `.env` to override the default model.

When the Anthropic API is used, the prompt prohibits ungrounded numbers. The
reasoner extracts numeric tokens from the generated response and checks them
against the supplied fact values. Invalid output is rejected and retried once;
if the retry remains invalid, the reasoner raises an error rather than
returning unverified text.

Every prompt/response attempt, validation result, attempt number, and model is
written to the database table `ai_audit_log`, including deterministic fallback
responses and failed/retried responses. API exceptions are audited and then
raised. API keys remain in `.env` and are never written to the audit table.

This layer reports associations in computed evidence; recommendations are
operational suggestions and do not establish causality.
