output "dq_summary_view" {
  value = "${aws_glue_catalog_table.dq_summary.database_name}.${aws_glue_catalog_table.dq_summary.name}"
}
