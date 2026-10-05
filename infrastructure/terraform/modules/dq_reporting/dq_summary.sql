-- dq_summary: every data quality check of the latest run, one row per table and check, for the
-- data quality slide. Each report keeps its own latest run per table, so a run with --tables only
-- replaces the rows of the tables it built.
--   failing_rows / total_rows / failing_pct: rows that broke (or were changed by) the check
--   status: ok, flagged, imputed, removed, excluded, warn or fail, depending on the check
--   detail: context the numbers don't carry (orphan note, Pandera's error, lag percentiles...)
WITH all_reports AS (
    -- Flags (<rule>), imputations (imputed:<column>), empty columns (all_null:<column>) and
    -- contract checks (<kind>:<column>). A contract kind's own flag (e.g. required_missing, true
    -- when any column fails it) is left out: its per-column rows say the same, in more detail.
    SELECT
        'silver' AS layer, 'rules' AS report, "table" AS table_name,
        CASE
            WHEN rule LIKE 'imputed:%' THEN 'imputation'
            WHEN rule LIKE 'all_null:%' THEN 'empty_column'
            WHEN rule LIKE '%:%' THEN 'contract'
            ELSE 'flag'
        END AS check_type,
        rule AS check_name, failing_rows, "rows" AS total_rows, failing_pct,
        CASE
            WHEN failing_rows = 0 THEN 'ok'
            WHEN rule LIKE 'imputed:%' THEN 'imputed'
            ELSE 'flagged'
        END AS status,
        CAST(NULL AS varchar) AS detail, run_at
    FROM ${silver}.dq_rule_report
    WHERE rule NOT IN (
        'required_missing', 'not_allowed', 'out_of_range', 'outside_dataset', 'date_order', 'not_unique'
    )

    UNION ALL
    SELECT 'silver', 'foreign_keys', child_table, 'foreign_key', child_column || ' -> ' || parent_table,
        orphan_rows, checked_rows, orphan_pct, status, note, run_at
    FROM ${silver}.dq_fk_report

    UNION ALL
    SELECT 'silver', 'duplicates', "table", 'duplicates', 'exact_duplicates', exact_duplicates, rows_in,
        100.0 * exact_duplicates / NULLIF(rows_in, 0),
        CASE WHEN exact_duplicates > 0 THEN 'removed' ELSE 'ok' END, NULL, run_at
    FROM ${silver}.dq_dedup_report

    UNION ALL
    SELECT 'silver', 'duplicates', "table", 'duplicates', 'conflicting_duplicates', conflicting_duplicates, rows_in,
        100.0 * conflicting_duplicates / NULLIF(rows_in, 0),
        CASE WHEN conflicting_duplicates > 0 THEN 'removed' ELSE 'ok' END,
        format('%s keys had more than one version', conflicting_keys), run_at
    FROM ${silver}.dq_dedup_report

    UNION ALL
    -- Pandera's findings: which contract checks failed, without row counts.
    SELECT 'silver', 'contracts', "table", 'pandera', "column" || ': ' || "check", NULL, NULL, NULL, 'fail',
        error, run_at
    FROM ${silver}.dq_contract_report

    UNION ALL
    -- The report's status also counts rows processed before their event; those are a flag above.
    SELECT 'silver', 'arrivals', "table", 'late_arrival', 'late_rows', late_rows, rows_with_lag, late_pct,
        CASE WHEN late_rows > 0 THEN 'warn' ELSE 'ok' END,
        format('lag p50 %s, p90 %s, p99 %s, max %s days', lag_p50, lag_p90, lag_p99, lag_max), run_at
    FROM ${silver}.dq_arrival_report

    UNION ALL
    SELECT 'silver', 'volumes', "table", 'volume', 'volume', NULL, raw_rows, NULL, status,
        format(
            '%s raw rows vs %s in the dictionary (%s%%); %s days without a file%s; %s unusual days%s; '
            || '%s rows filed under another day',
            raw_rows, expected_rows, vs_expected_pct, missing_days,
            CASE WHEN missing_days > 0 THEN ' (' || array_join(slice(missing_day_list, 1, 10), ', ') || ')' ELSE '' END,
            cardinality(unusual_days),
            CASE
                WHEN cardinality(unusual_days) > 0 THEN ' (' || array_join(slice(unusual_days, 1, 10), ', ') || ')'
                ELSE ''
            END,
            COALESCE(rows_filed_on_other_day, 0)
        ),
        run_at
    FROM ${silver}.dq_volume_report

    UNION ALL
    SELECT 'gold', 'exclusions', "table", 'exclusion', 'rows_excluded', rows_excluded, rows_in,
        100.0 * rows_excluded / NULLIF(rows_in, 0),
        CASE WHEN rows_excluded > 0 THEN 'excluded' ELSE 'ok' END,
        json_format(CAST(excluded_by_reason AS JSON)), run_at
    FROM ${gold}.dq_exclusions
),

latest AS (
    -- run_at is ISO-8601 text from Spark, so the largest string is the latest run.
    SELECT *, max(run_at) OVER (PARTITION BY report, table_name) AS latest_run
    FROM all_reports
)

SELECT
    CAST(layer AS varchar) AS layer,
    CAST(report AS varchar) AS report,
    CAST(table_name AS varchar) AS table_name,
    CAST(check_type AS varchar) AS check_type,
    CAST(check_name AS varchar) AS check_name,
    CAST(failing_rows AS bigint) AS failing_rows,
    CAST(total_rows AS bigint) AS total_rows,
    CAST(round(failing_pct, 3) AS double) AS failing_pct,
    CAST(status AS varchar) AS status,
    CAST(detail AS varchar) AS detail,
    CAST(from_iso8601_timestamp(run_at) AS timestamp(3)) AS run_at
FROM latest
WHERE run_at = latest_run
