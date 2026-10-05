# Makes the data quality reports queryable in Athena: one Glue table per report folder the Glue
# jobs append JSON to, and the dq_summary view over all of them (dq_summary.sql).
locals {
  # Columns as [name, Hive type], in the order the jobs write them. run_at stays a string: Spark
  # writes ISO-8601 with a zone, which the view parses.
  reports = {
    dq_dedup_report = {
      layer  = "silver"
      folder = "_dq_report"
      columns = [
        ["table", "string"], ["rows_in", "bigint"], ["rows_out", "bigint"], ["rows_removed", "bigint"],
        ["duplicate_keys", "bigint"], ["exact_duplicates", "bigint"], ["conflicting_keys", "bigint"],
        ["conflicting_duplicates", "bigint"], ["run_at", "string"],
      ]
    }
    dq_rule_report = {
      layer  = "silver"
      folder = "_rule_report"
      columns = [
        ["table", "string"], ["rule", "string"], ["failing_rows", "bigint"], ["rows", "bigint"],
        ["failing_pct", "double"], ["run_at", "string"],
      ]
    }
    dq_fk_report = {
      layer  = "silver"
      folder = "_fk_report"
      columns = [
        ["child_table", "string"], ["child_column", "string"], ["parent_table", "string"],
        ["parent_column", "string"], ["nullable", "boolean"], ["child_rows", "bigint"], ["null_rows", "bigint"],
        ["checked_rows", "bigint"], ["orphan_rows", "bigint"], ["orphan_pct", "double"], ["status", "string"],
        ["note", "string"], ["run_at", "string"],
      ]
    }
    dq_contract_report = {
      layer  = "silver"
      folder = "_contract_report"
      columns = [
        ["table", "string"], ["category", "string"], ["error_type", "string"], ["column", "string"],
        ["check", "string"], ["error", "string"], ["run_at", "string"],
      ]
    }
    dq_arrival_report = {
      layer  = "silver"
      folder = "_arrival_report"
      columns = [
        ["table", "string"], ["rows", "bigint"], ["rows_with_lag", "bigint"], ["late_rows", "bigint"],
        ["late_pct", "double"], ["processed_before_event", "bigint"], ["lag_p50", "int"], ["lag_p90", "int"],
        ["lag_p99", "int"], ["lag_max", "int"], ["same_day", "bigint"], ["one_day", "bigint"],
        ["two_to_seven_days", "bigint"], ["eight_to_thirty_days", "bigint"], ["over_thirty_days", "bigint"],
        ["status", "string"], ["run_at", "string"],
      ]
    }
    dq_volume_report = {
      layer  = "silver"
      folder = "_volume_report"
      columns = [
        ["table", "string"], ["raw_rows", "bigint"], ["silver_rows", "bigint"], ["expected_rows", "bigint"],
        ["vs_expected_pct", "double"], ["file_days", "bigint"], ["missing_days", "bigint"],
        ["missing_day_list", "array<string>"], ["unusual_days", "array<string>"],
        ["rows_filed_on_other_day", "bigint"], ["status", "string"], ["run_at", "string"],
      ]
    }
    dq_exclusions = {
      layer  = "gold"
      folder = "_exclusions"
      columns = [
        ["table", "string"], ["rows_in", "bigint"], ["rows_excluded", "bigint"],
        ["excluded_by_reason", "map<string,bigint>"], ["run_at", "string"],
      ]
    }
  }

  # The view's columns as [name, type in the view's SQL, Hive type for the catalog]. They must match
  # the casts at the end of dq_summary.sql, or Athena calls the view stale.
  summary_columns = [
    ["layer", "varchar", "string"],
    ["report", "varchar", "string"],
    ["table_name", "varchar", "string"],
    ["check_type", "varchar", "string"],
    ["check_name", "varchar", "string"],
    ["failing_rows", "bigint", "bigint"],
    ["total_rows", "bigint", "bigint"],
    ["failing_pct", "double", "double"],
    ["status", "varchar", "string"],
    ["detail", "varchar", "string"],
    ["run_at", "timestamp(3)", "timestamp"],
  ]
  summary_sql = templatefile("${path.module}/dq_summary.sql", {
    silver = var.database_names["silver"]
    gold   = var.database_names["gold"]
  })
}

resource "aws_glue_catalog_table" "report" {
  for_each      = local.reports
  name          = each.key
  database_name = var.database_names[each.value.layer]
  table_type    = "EXTERNAL_TABLE"
  parameters = {
    classification = "json"
    EXTERNAL       = "TRUE"
  }

  storage_descriptor {
    location      = "s3://${var.lake_bucket}/${each.value.layer}/${each.value.folder}/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
      parameters = {
        "ignore.malformed.json" = "true"
      }
    }

    dynamic "columns" {
      for_each = each.value.columns
      content {
        name = columns.value[0]
        type = columns.value[1]
      }
    }
  }
}

# An Athena view stored in the Glue catalog: Athena reads the SQL from the base64 "Presto View"
# comment; the storage descriptor's columns are what other tools see.
resource "aws_glue_catalog_table" "dq_summary" {
  name          = "dq_summary"
  database_name = var.database_names["silver"]
  table_type    = "VIRTUAL_VIEW"
  parameters = {
    presto_view = "true"
    comment     = "Presto View"
  }
  view_original_text = "/* Presto View: ${base64encode(jsonencode({
    originalSql = local.summary_sql
    catalog     = "awsdatacatalog"
    schema      = var.database_names["silver"]
    columns     = [for c in local.summary_columns : { name = c[0], type = c[1] }]
  }))} */"
  view_expanded_text = "/* Presto View */"

  storage_descriptor {
    dynamic "columns" {
      for_each = local.summary_columns
      content {
        name = columns.value[0]
        type = columns.value[2]
      }
    }
  }

  depends_on = [aws_glue_catalog_table.report]
}
