"""
Configuration - loads settings from environment variables.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# Azure AD
AZURE_CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "")
AZURE_TENANT_ID = os.getenv("AZURE_TENANT_ID", "")
AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:8501")
AUTHORITY = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}"

# Encryption
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "")

# AI (Claude API)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Access code (shared login code)
ACCESS_CODE = os.getenv("ACCESS_CODE", "")

# Database
DATABASE_PATH = os.getenv("DATABASE_PATH", "watchdog.db")

# -----------------------------------------------------------
# SYSTEM REGISTRY
# -----------------------------------------------------------
# Define each system/license that needs token rotation here.
# This is the single place you add new systems.
#
#   "system_key": {
#       "name":        Display name shown in the UI,
#       "description": Short explanation of what the token is for,
#       "owner_group": Azure AD group Object ID that can manage it,
#                      (set to None to allow any authenticated user)
#       "fields":      List of fields the owner needs to provide,
#   }
# -----------------------------------------------------------
SYSTEMS = {
    "pagerduty": {
        "name": "PagerDuty",
        "description": "API token for PagerDuty license monitoring",
        "owner_group": None,
        "fields": [
            {"key": "api_token", "label": "API Token", "type": "password"},
        ],
    },
    "gitlab_xolv": {
        "name": "GitLab [Xolv]",
        "description": "Access token for Xolv GitLab instance",
        "owner_group": None,
        "fields": [
            {"key": "api_token", "label": "Personal Access Token", "type": "password"},
            {"key": "base_url", "label": "GitLab Instance URL", "type": "text"},
        ],
    },
    "gitlab_catalight": {
        "name": "GitLab [Catalight]",
        "description": "Access token for Catalight GitLab instance",
        "owner_group": None,
        "fields": [
            {"key": "api_token", "label": "Personal Access Token", "type": "password"},
            {"key": "base_url", "label": "GitLab Instance URL", "type": "text"},
        ],
    },
    "atlassian_xolv": {
        "name": "Atlassian [Xolv]",
        "description": "API token for Xolv Atlassian (Jira/Confluence) instance",
        "owner_group": None,
        "fields": [
            {"key": "api_token", "label": "API Token", "type": "password"},
            {"key": "email", "label": "Service Account Email", "type": "text"},
            {"key": "base_url", "label": "Atlassian Instance URL", "type": "text"},
        ],
    },
    "atlassian_catalight": {
        "name": "Atlassian [Catalight]",
        "description": "API token for Catalight Atlassian (Jira/Confluence) instance",
        "owner_group": None,
        "fields": [
            {"key": "api_token", "label": "API Token", "type": "password"},
            {"key": "email", "label": "Service Account Email", "type": "text"},
            {"key": "base_url", "label": "Atlassian Instance URL", "type": "text"},
        ],
    },
}
