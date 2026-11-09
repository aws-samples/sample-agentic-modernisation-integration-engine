# Internal ALB in the private subnets, reached only by CloudFront through a VPC origin.
# CloudFront terminates HTTPS with its default *.cloudfront.net certificate.

resource "aws_lb" "this" {
  name               = "${var.name}-alb"
  internal           = true
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.private[*].id
  # SSE streams (AI output, ATX logs) stay open for minutes.
  idle_timeout               = 3600
  drop_invalid_header_fields = true
}

resource "aws_lb_target_group" "app" {
  name                 = "${var.name}-web"
  port                 = 3000
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = data.aws_vpc.this.id
  deregistration_delay = 30

  health_check {
    path                = "/health"
    matcher             = "200"
    interval            = 30
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}

resource "aws_cloudfront_vpc_origin" "alb" {
  vpc_origin_endpoint_config {
    name                   = "${var.name}-alb"
    arn                    = aws_lb.this.arn
    http_port              = 80
    https_port             = 443
    origin_protocol_policy = "http-only"
    origin_ssl_protocols {
      items    = ["TLSv1.2"]
      quantity = 1
    }
  }
}

data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_origin_request_policy" "all_viewer" {
  name = "Managed-AllViewer"
}

resource "aws_cloudfront_distribution" "this" {
  enabled         = true
  comment         = "AMIE app"
  is_ipv6_enabled = true
  price_class     = "PriceClass_100"
  http_version    = "http2and3"

  origin {
    origin_id   = "alb"
    domain_name = aws_lb.this.dns_name
    vpc_origin_config {
      vpc_origin_id            = aws_cloudfront_vpc_origin.alb.id
      origin_read_timeout      = var.origin_read_timeout
      origin_keepalive_timeout = 60
    }
  }

  # Everything dynamic: no caching, every header (including Authorization) forwarded.
  default_cache_behavior {
    target_origin_id         = "alb"
    viewer_protocol_policy   = "redirect-to-https"
    allowed_methods          = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods           = ["GET", "HEAD"]
    cache_policy_id          = data.aws_cloudfront_cache_policy.disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer.id
    compress                 = false
  }

  # Vite's content-hashed bundles are safe to cache at the edge.
  ordered_cache_behavior {
    path_pattern           = "/assets/*"
    target_origin_id       = "alb"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    cache_policy_id        = data.aws_cloudfront_cache_policy.optimized.id
    compress               = true
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
