terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # 5.82 adds aws_cloudfront_vpc_origin.
      version = ">= 5.82, < 7.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.5, < 4.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Purpose   = "amie-app"
      ManagedBy = "terraform"
      Project   = "sample-agentic-modernisation-integration-engine"
    }
  }
}
