// ===================================================================
// TEST DATA - NOT REAL CREDENTIALS
// ===================================================================
// This file contains intentional fake secrets for TruffleHog testing.
// These credentials have never been valid and pose no security risk.
// See TEST_FIXTURES.md for more information.
// ===================================================================

// Production Configuration (TEST FIXTURE)
// WARNING: These are fake credentials for testing secret detection

const config = {
  environment: 'production',

  // AWS Configuration
  aws: {
    accessKeyId: 'AKIAIOSFODNN7EXAMPLE2',
    secretAccessKey: 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY2',
    region: 'us-west-2',
    s3Bucket: 'my-production-uploads'
  },

  // GitHub Integration
  github: {
    token: 'ghp_9876543210ZYXWVUTSRQPONMLKJIHGFEDCBa',
    oauthClientId: '1234567890abcdef1234',
    oauthClientSecret: 'abcdef1234567890abcdef1234567890abcdef12'
  },

  // Stripe Payment Gateway
  stripe: {
    secretKey: 'sk_live_51ZYXwvutsrqponMLKjihgfedcba1234567890ZYXwvutsrqponMLKjihgfedcba',
    publishableKey: 'pk_live_51ZYXwvutsrqponMLKjihgfedcba1234567890',
    webhookSecret: 'whsec_abcdefghijklmnopqrstuvwxyz1234567890'
  },

  // Database
  database: {
    host: 'prod-mysql.example.com',
    port: 3306,
    username: 'app_admin',
    password: 'MyS3cur3MySQLp@ssw0rd!2024',
    database: 'production_db'
  },

  // JWT Authentication
  jwt: {
    secret: 'JWT$ecr3tK3y!Pr0ducti0n_V3ry$3cur3',
    expiresIn: '24h'
  },

  // Slack Notifications
  slack: {
    botToken: 'xoxb-9876543210-9876543210987-zyxwvutsrqponmlkjihgfedcba',
    webhookUrl: 'https://hooks.slack.com/services/T9876543/B9876543/YXZWVUTSRQPONmlkjihgfedcba'
  },

  // SendGrid Email
  sendgrid: {
    apiKey: 'SG.zyxwvutsrqponmlkjih.gfedcbaZYXWVUTSRQPONMLKJIHGFEDCBA1234567890ZYXWVUTSR'
  },

  // Twilio SMS
  twilio: {
    accountSid: 'ACzyxwvutsrqponmlkjihgfedcba9876543',
    authToken: 'zyxwvutsrqponmlkjihgfedcba987654'
  }
};

module.exports = config;
