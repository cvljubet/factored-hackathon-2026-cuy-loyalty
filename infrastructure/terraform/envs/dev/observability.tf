# Chat observability in the team account. The backend writes one JSON line per chat turn
# (event "chat_turn", backend/app/observability.py) to its ECS log group; the metric filters
# below turn those lines and two warning messages into metrics, and the dashboard shows them
# next to Logs Insights breakdowns. Bedrock's own metrics (throttles, quota) are on the model
# account's dashboard (envs/dev-app output bedrock_dashboard_url).
locals {
  chat_log_group   = module.backend_service.log_group_name
  metric_namespace = "${local.name_prefix}/chat"
  turn             = "$.event = \"chat_turn\""

  # name => [filter pattern, metric value, unit]
  chat_metrics = {
    Turns           = ["{ ${local.turn} }", "1", "Count"]
    FailedTurns     = ["{ (${local.turn}) && ($.failed IS TRUE) }", "1", "Count"]
    Escalations     = ["{ (${local.turn}) && ($.escalated IS TRUE) }", "1", "Count"]
    BlockedTurns    = ["{ (${local.turn}) && ($.status = \"blocked\") }", "1", "Count"]
    FallbackTurns   = ["{ (${local.turn}) && ($.fallback_used IS TRUE) }", "1", "Count"]
    TurnLatency     = ["{ ${local.turn} }", "$.latency_ms", "Milliseconds"]
    ModelRequests   = ["{ ${local.turn} }", "$.model_requests", "Count"]
    InputTokens     = ["{ ${local.turn} }", "$.input_tokens", "Count"]
    OutputTokens    = ["{ ${local.turn} }", "$.output_tokens", "Count"]
    RouterFallbacks = ["\"Model router failed\"", "1", "Count"]
    InquiryFailures = ["\"Inquiry turn failed\"", "1", "Count"]
  }
}

resource "aws_cloudwatch_log_metric_filter" "chat" {
  for_each       = local.chat_metrics
  name           = "${local.name_prefix}-chat-${each.key}"
  log_group_name = local.chat_log_group
  pattern        = each.value[0]

  metric_transformation {
    name      = each.key
    namespace = local.metric_namespace
    value     = each.value[1]
    unit      = each.value[2]
  }
}

locals {
  # [title, [[metric, stat, label], ...]] for the metric graphs, two per row.
  chat_graphs = [
    ["Turns, failures and escalations (5 min)", [["Turns", "Sum", "turns"], ["FailedTurns", "Sum", "failed"], ["Escalations", "Sum", "escalated"]]],
    ["Blocked turns and fallbacks (5 min)", [["BlockedTurns", "Sum", "blocked"], ["FallbackTurns", "Sum", "Sonnet fallback answered"], ["RouterFallbacks", "Sum", "router fell back to rules"], ["InquiryFailures", "Sum", "inquiry failed"]]],
    ["Turn latency (ms)", [["TurnLatency", "p50", "p50"], ["TurnLatency", "p95", "p95"], ["TurnLatency", "Maximum", "max"]]],
    ["Model requests and tokens per turn (average)", [["ModelRequests", "Average", "requests"], ["InputTokens", "Average", "input tokens"], ["OutputTokens", "Average", "output tokens"]]],
  ]

  turn_query = "SOURCE '${local.chat_log_group}' | filter event = \"chat_turn\""
  # [title, query] for the Logs Insights tables, full width.
  chat_queries = [
    ["Turns by engine and status", "${local.turn_query} | stats count(*) as turns, avg(latency_ms) as avg_ms, avg(model_requests) as requests, sum(input_tokens + output_tokens) as tokens by engine, status | sort turns desc"],
    ["Blocked and escalated turns", "${local.turn_query} | filter status in [\"blocked\", \"escalated\"] | stats count(*) as turns by engine, blocked_reason, route_engine, credit_decision | sort turns desc"],
    ["Slowest turns (stage timings in ms)", "${local.turn_query} | sort latency_ms desc | limit 20 | fields @timestamp, engine, status, latency_ms, stage_ms.guardrail_input, stage_ms.router, stage_ms.engine, stage_ms.guardrail_output, model_requests, fallback_used"],
    ["Warnings and errors", "SOURCE '${local.chat_log_group}' | filter @message like /^(WARNING|ERROR|CRITICAL) / | sort @timestamp desc | limit 50 | fields @timestamp, @message"],
  ]
}

resource "aws_cloudwatch_dashboard" "chat" {
  dashboard_name = "${local.name_prefix}-chat"
  dashboard_body = jsonencode({
    widgets = concat(
      [
        for i, graph in local.chat_graphs : {
          type   = "metric"
          x      = (i % 2) * 12
          y      = floor(i / 2) * 6
          width  = 12
          height = 6
          properties = {
            title   = graph[0]
            region  = var.region
            view    = "timeSeries"
            stacked = false
            period  = 300
            metrics = [for m in graph[1] : [local.metric_namespace, m[0], { stat = m[1], label = m[2] }]]
          }
        }
      ],
      [
        for i, query in local.chat_queries : {
          type   = "log"
          x      = 0
          y      = 12 + i * 6
          width  = 24
          height = 6
          properties = {
            title  = query[0]
            region = var.region
            view   = "table"
            query  = query[1]
          }
        }
      ],
    )
  })
}

output "chat_dashboard_url" {
  description = "CloudWatch dashboard of chat turns, failures, fallbacks, latency and tokens (team account)."
  value       = "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards/dashboard/${aws_cloudwatch_dashboard.chat.dashboard_name}"
}
