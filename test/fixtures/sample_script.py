#!/usr/bin/env python3
"""
Test script with embedded secrets for TruffleHog testing.
These are FAKE credentials - never valid, for testing only.
"""

import os
import requests

# AWS Credentials for S3 access
AWS_ACCESS_KEY = "AKIAZXCVBNMLKJHGFDSA"
AWS_SECRET_KEY = "wJalrXUt9876543210abcdefghijklmnopqrstuv"

# GitHub tokens for API access
GITHUB_TOKEN = "ghp_zYxWvUtSrQpOnMlKjIhGfEdCbA9876543210"
GITHUB_OAUTH = "gho_1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZab"

# Stripe payment processing
STRIPE_KEY = "sk_live_ABCDEFGHIJKLMNOPQRSTUVWXYz"

# Slack integration tokens
SLACK_TOKEN = "xoxb-111111111111-2222222222222-abcdefghijklmnopqrstuv"
SLACK_WEBHOOK = "https://hooks.slack.com/services/T11111111/B22222222/AbCdEfGhIjKlMnOpQrStUvWx"

# SendGrid email service
SENDGRID_KEY = "SG.ABCDEFGHIJKLMNOP.qrstuvwxyz1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZab"

# Twilio SMS service
TWILIO_SID = "AC12345678901234567890123456789012"
TWILIO_TOKEN = "12345678901234567890123456789012"

# Database connection strings
POSTGRES_DSN = "postgresql://dbuser:SuperSecretDBPass123!@db.example.com:5432/production"
MYSQL_DSN = "mysql://admin:MyS3cr3tP@ssw0rd@mysql.example.com:3306/app"
REDIS_URL = "redis://:RedisP@ssword123@redis.example.com:6379"

# MongoDB connection
MONGO_URI = "mongodb+srv://admin:MongoDBP@ss123@cluster.mongodb.net/db?retryWrites=true"

# OpenAI / LLM APIs
OPENAI_KEY = "sk-1234567890abcdefghijklmnopqrstuvwxyzABCDEFGH"

# Private key for signing
PRIVATE_KEY = """-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA0m59l2u9iDnMbrXHfqkOrn2dVQ3vfBJqcDuFUK03d+1PZGbV
yGH3q2nfxmLhj8lz5FcA9b2wr8+p1G4T8TqHjrW2k0A9b2H7YxP0K6s8T8TqHjrW
2k0A9b2wr8+p1G4T8TqHjrW2k0A9b2wr8+p1G4T8TqHjrW2k0A9b2wr8+p1G4T8T
qHjrW2k0A9b2wr8+p1G4T8TqHjrW2k0A9b2wr8+p1G4T8TqHjrW2k0A9b2wr8+p1
G4T8TqHjrW2k0A9b2wr8+p1G4T8TqHjrW2k0A9b2wr8+p1G4T8TqHjrW2k0A9b2w
-----END RSA PRIVATE KEY-----"""

def connect_to_services():
    """Connect to various services using the credentials above."""
    pass

if __name__ == "__main__":
    connect_to_services()
