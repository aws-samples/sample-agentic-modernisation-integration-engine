terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.60, < 7.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Purpose   = "amie-test"
      ManagedBy = "terraform"
      Project   = "sample-agentic-modernisation-integration-engine"
    }
  }
}
