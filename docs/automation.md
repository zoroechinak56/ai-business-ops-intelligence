# Pipeline Automation with n8n

## Pipeline command

Run a complete source-data bundle through validation, cleaning, database
rebuild, analytics, root-cause analysis, AI reasoning, alert evaluation, and
Markdown reporting:

```powershell
python -m src.automation.run_pipeline --file data/raw
```

The `--file` value may be the source directory or a CSV path inside a complete
bundle containing the ten expected source CSVs. A lone CSV is rejected because
the existing database loader rebuilds all ten related tables; rebuilding from
one table would discard the others or violate foreign keys. Raw input files
are not modified. For each run the pipeline writes
`data/processed/reports/pipeline_<run-id>.md` and prints a final JSON object
containing `status`, `steps`, `duration`, and `alerts`.

## Alert rules

- Latest order month fulfilment rate **below 90%**.
- Any supplier late-delivery rate more than **10 percentage points** above
  the unweighted average supplier rate from SQL query 05.
- Any SKU-group stock-out rate **above 20%** in the latest order month, using
  inventory snapshots grouped as in SQL query 06.

Alert comparisons are strict (`<`, `>`, `>` respectively); values exactly at
the threshold do not alert. Each alert includes the measured values, relevant
period/group, threshold, and a readable message.

## Database audit

The pipeline creates `audit_log` if absent and records each run step with
`run_id`, `step`, `status`, `started_at`, `ended_at`, `rows_processed`, and
`message`. Completed, failed, and skipped steps all receive records. The
database rebuild drops/recreates the operational tables but leaves this audit
table intact. The AI reasoner continues to write prompt/response validation
events to its separate `ai_audit_log`.

## Import the n8n workflow

1. Run n8n self-hosted on the same host/container network that can access the
   repository, source bundle, Python 3.11 environment, and database. The
   Execute Command node is not available on n8n Cloud and may be disabled by
   instance policy.
2. Make the project root the process working directory for n8n's Execute
   Command node (or configure the n8n task runner so the command executes from
   the repository root). Ensure `python` resolves to the project environment,
   or edit the two Execute Command nodes to use the absolute Python executable.
3. In n8n, select **Workflows → Import from File** and choose
   `n8n/workflow.json`.
4. Configure the SMTP credentials on the three email nodes and set n8n
   environment variables `PIPELINE_ALERT_FROM`, `PIPELINE_ALERT_TO`,
   `PIPELINE_SUMMARY_FROM`, and `PIPELINE_SUMMARY_TO`.
5. Configure the Postgres credentials on the three database nodes for the
   same database used by the pipeline. The `audit_log` table is created by the
   pipeline. The n8n database nodes insert workflow-level completion/retry
   records into that table. For SQLite deployments, replace those Postgres
   nodes with an installed SQLite node and map the same insert fields to the
   configured SQLite database.
6. Save the workflow, test it with a complete bundle path, and activate it.

The workflow exposes a POST webhook at the path
`/webhook/business-ops-pipeline`. Send a JSON body with an absolute path visible
to the n8n host:

```json
{
  "filePath": "C:\\data\\business-ops\\raw"
}
```

The first Execute Command failure waits five seconds and retries once. A second
command failure is recorded and sends a failure email. A successful command's
JSON stdout is parsed; non-empty `alerts` routes an alert email, then the
workflow adds its audit record and sends a final summary. A run with no alerts
skips the alert email but still audits and sends the summary. The webhook
returns an immediate acknowledgement; inspect the n8n execution and the
Markdown report for completion details.

## Operational notes

- The CLI stops at the first failed pipeline stage. Later stages are marked
  skipped in `audit_log`; the Markdown report captures failure details.
- Configure `DATABASE_URL` and, if desired, `LLM_API_KEY` / `LLM_MODEL` in the
  execution environment or `.env`. Do not put secrets into the workflow JSON.
- Protect the webhook with n8n authentication or a reverse-proxy access policy
  before exposing it outside a trusted network. Keep the workflow inactive
  until credentials, host paths, and the webhook access policy are verified.
- The command rebuilds the operational database from a complete bundle. Use
  an isolated database or maintenance window if the database serves live
  workloads.
