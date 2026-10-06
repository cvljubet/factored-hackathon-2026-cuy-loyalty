# Bedrock's own metrics in the model account: per-model requests against the per-minute quota,
# throttles, errors, latency and tokens, plus guardrail checks and interventions. SEARCH picks up
# every inference profile the account calls (ModelId is the profile ID, e.g. us.anthropic...).
locals {
  dashboard_region = data.aws_region.current.region
  quota_rpm        = 10 # Haiku 4.5 and Sonnet 4.6 cross-region requests per minute in this account

  bedrock_widgets = [
    {
      title = "Model requests per minute (quota ${local.quota_rpm} per model)"
      search = [
        ["SEARCH('{AWS/Bedrock,ModelId} MetricName=\"Invocations\"', 'Sum', 60)", "requests"],
      ]
      annotation = { label = "quota", value = local.quota_rpm }
    },
    {
      title = "Throttled requests per minute"
      search = [
        ["SEARCH('{AWS/Bedrock,ModelId} MetricName=\"InvocationThrottles\"', 'Sum', 60)", "throttles"],
      ]
    },
    {
      title = "Errors per minute (client, server)"
      search = [
        ["SEARCH('{AWS/Bedrock,ModelId} MetricName=\"InvocationClientErrors\"', 'Sum', 60)", "client"],
        ["SEARCH('{AWS/Bedrock,ModelId} MetricName=\"InvocationServerErrors\"', 'Sum', 60)", "server"],
      ]
    },
    {
      title = "Latency per request (average, ms)"
      search = [
        ["SEARCH('{AWS/Bedrock,ModelId} MetricName=\"InvocationLatency\"', 'Average', 60)", "latency"],
      ]
    },
    {
      title = "Tokens per minute (input, output)"
      search = [
        ["SEARCH('{AWS/Bedrock,ModelId} MetricName=\"InputTokenCount\"', 'Sum', 60)", "input"],
        ["SEARCH('{AWS/Bedrock,ModelId} MetricName=\"OutputTokenCount\"', 'Sum', 60)", "output"],
      ]
    },
    {
      title = "Guardrail checks and interventions (per version, 5 min)"
      search = [
        ["SEARCH('{AWS/Bedrock/Guardrails,GuardrailArn,GuardrailVersion} MetricName=\"Invocations\"', 'Sum', 300)", "checks"],
        ["SEARCH('{AWS/Bedrock/Guardrails,GuardrailArn,GuardrailVersion} MetricName=\"InvocationsIntervened\"', 'Sum', 300)", "intervened"],
      ]
    },
    {
      title = "Guardrail interventions by policy type (5 min)"
      search = [
        ["SEARCH('{AWS/Bedrock/Guardrails,GuardrailPolicyType,Operation} MetricName=\"InvocationsIntervened\"', 'Sum', 300)", "intervened"],
      ]
    },
  ]
}

resource "aws_cloudwatch_dashboard" "bedrock" {
  dashboard_name = "${var.name_prefix}-bedrock"
  dashboard_body = jsonencode({
    widgets = [
      for i, widget in local.bedrock_widgets : {
        type   = "metric"
        x      = (i % 2) * 12
        y      = floor(i / 2) * 6
        width  = 12
        height = 6
        properties = merge(
          {
            title   = widget.title
            region  = local.dashboard_region
            view    = "timeSeries"
            stacked = false
            metrics = [for j, s in widget.search : [{ expression = s[0], id = "e${j}", label = s[1] }]]
          },
          try({ annotations = { horizontal = [widget.annotation] } }, {}),
        )
      }
    ]
  })
}
