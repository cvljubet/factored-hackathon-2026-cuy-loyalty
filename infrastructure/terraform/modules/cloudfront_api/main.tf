# HTTPS in front of an HTTP API origin (the backend ALB), on the default
# *.cloudfront.net certificate. Nothing is cached: every request, with its method,
# body, query string and all viewer headers (Authorization included), goes to the origin.

# AWS-managed policies, looked up by name rather than hard-coding their IDs.
data "aws_cloudfront_cache_policy" "caching_disabled" {
  name = "Managed-CachingDisabled"
}

# Forwards every viewer header (Authorization, Origin, Content-Type, ...), cookie and
# query string. Safe only because caching is disabled. The viewer Host header is kept,
# so redirects the API issues point back at CloudFront, not at the load balancer.
data "aws_cloudfront_origin_request_policy" "all_viewer" {
  name = "Managed-AllViewer"
}

locals {
  origin_id = "${var.name_prefix}-api-origin"
}

resource "aws_cloudfront_distribution" "this" {
  enabled         = true
  comment         = var.comment
  is_ipv6_enabled = true
  http_version    = "http2and3"
  price_class     = var.price_class

  origin {
    origin_id   = local.origin_id
    domain_name = var.origin_domain_name

    custom_origin_config {
      http_port                = 80
      https_port               = 443
      origin_protocol_policy   = "http-only" # the ALB has no certificate
      origin_ssl_protocols     = ["TLSv1.2"]
      origin_read_timeout      = var.origin_read_timeout
      origin_keepalive_timeout = 5
    }
  }

  default_cache_behavior {
    target_origin_id         = local.origin_id
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = data.aws_cloudfront_cache_policy.caching_disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer.id
    compress                 = true
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}
