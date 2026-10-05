# HTTPS in front of an HTTP API origin (the backend ALB), on the default
# *.cloudfront.net certificate. Nothing is cached: every request, with its method,
# body, query string and all viewer headers (Authorization included), goes to the origin.
#
# With use_vpc_origin, CloudFront reaches the internal ALB vpc_origin_alb_arn through a VPC origin:
# a service-managed network interface inside the VPC, so the request (and its token)
# never crosses the public internet. Without it, the origin is a public HTTP endpoint.

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
  origin_id      = "${var.name_prefix}-api-origin"
  use_vpc_origin = var.use_vpc_origin
}

# Creating or changing a VPC origin takes up to 15 minutes. HTTP is fine here: the hop runs
# inside AWS's network (TLS on it would need a custom domain and certificate on the ALB).
resource "aws_cloudfront_vpc_origin" "this" {
  count = local.use_vpc_origin ? 1 : 0

  vpc_origin_endpoint_config {
    name                   = "${var.name_prefix}-api"
    arn                    = var.vpc_origin_alb_arn
    http_port              = 80
    https_port             = 443
    origin_protocol_policy = "http-only"
    origin_ssl_protocols {
      items    = ["TLSv1.2"]
      quantity = 1
    }
  }
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

    dynamic "custom_origin_config" {
      for_each = local.use_vpc_origin ? [] : [1]
      content {
        http_port                = 80
        https_port               = 443
        origin_protocol_policy   = "http-only" # the ALB has no certificate
        origin_ssl_protocols     = ["TLSv1.2"]
        origin_read_timeout      = var.origin_read_timeout
        origin_keepalive_timeout = 5
      }
    }

    dynamic "vpc_origin_config" {
      for_each = local.use_vpc_origin ? [1] : []
      content {
        vpc_origin_id            = aws_cloudfront_vpc_origin.this[0].id
        origin_read_timeout      = var.origin_read_timeout
        origin_keepalive_timeout = 5
      }
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
