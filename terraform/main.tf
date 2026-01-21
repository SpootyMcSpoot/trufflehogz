# ===================================================================
# TEST DATA - NOT REAL CREDENTIALS
# ===================================================================
# This file contains intentional fake secrets for TruffleHog testing.
# These credentials have never been valid and pose no security risk.
# See TEST_FIXTURES.md for more information.
# ===================================================================

terraform {
  required_version = ">= 1.0"

  backend "s3" {
    bucket         = "my-terraform-state"
    key            = "production/terraform.tfstate"
    region         = "us-east-1"
    access_key     = "AKIAIOSFODNN7EXAMPLE5"
    secret_key     = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY5"
    encrypt        = true
  }
}

provider "aws" {
  region     = "us-east-1"
  access_key = "AKIAIOSFODNN7EXAMPLE6"
  secret_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY6"
}

# Database credentials stored as variables (BAD PRACTICE)
variable "db_password" {
  default = "T3rr@f0rm_DB_P@$$w0rd_2024_V3ry_S3cur3!"
}

variable "api_key" {
  default = "sk_prod_terraform_api_key_1234567890abcdefghijklmnopqrstuvwxyz"
}

resource "aws_db_instance" "production" {
  identifier = "production-database"
  engine     = "postgres"
  username   = "dbadmin"
  password   = var.db_password
}

# GitHub token for CI/CD
variable "github_token" {
  default = "ghp_TerraformGitHubToken1234567890ABCDEFGHIJ"
}
