# License Scanner -Project Guide

## Overview

License Scanner is a self-service web portal for managing SaaS license optimization and API token rotation. It scans platforms like PagerDuty, Atlassian, and GitLab to identify inactive users, surface cost savings, and provide centralized credential management with a full audit trail.

Built by Xolv Technology Solutions.

---

## Table of Contents

1. [Getting Started](#getting-started)
2. [User Guide](#user-guide)
3. [Technical Reference](#technical-reference)
4. [Deployment](#deployment)
5. [Adding New Platforms](#adding-new-platforms)

---

## Getting Started

### Prerequisites

- Python 3.10+
- An Anthropic API key (for AI Insights)
- Azure AD App Registration (for production SSO -optional for local dev)

### Local Setup

```bash
# Clone the repo
git clone https://github.com/paulg7516/license-scanner.git
cd license-scanner

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your values (see Environment Variables below)

# Run
streamlit run app.py --server.port 8501 --server.headless true
```

Open **http://localhost:8501** and log in with the access code set in your `.env` file (no Azure AD required locally).

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `AZURE_CLIENT_ID` | Production | Azure AD app client ID |
| `AZURE_TENANT_ID` | Production | Azure AD tenant ID |
| `AZURE_CLIENT_SECRET` | Production | Azure AD app secret |
| `REDIRECT_URI` | Production | OAuth callback URL (e.g. `https://your-app.onrender.com`) |
| `ENCRYPTION_KEY` | Yes | Fernet key for token encryption. Generate with: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `DATABASE_PATH` | No | SQLite DB path (default: `watchdog.db`) |
| `ACCESS_CODE` | Yes | Shared access code for login authentication |
| `ANTHROPIC_API_KEY` | For AI | Claude API key from [console.anthropic.com](https://console.anthropic.com/) |

---

## User Guide

### Navigation

The portal has four main tabs:

| Tab | Color | Purpose |
|-----|-------|---------|
| **Scan Overview** | Green | View inactive users, track savings, manage license statuses |
| **Token Health** | Teal | Monitor token rotation health, log credential rotations |
| **Audit Log** | Purple | Review all activity history for compliance |
| **AI Insights** | Orange | Generate AI-powered executive summaries of scan data |

---

### Scan Overview

This is the main dashboard for license optimization.

**Headline Metrics**
- Realized savings (from deactivated users)
- Pending action (unresolved inactive users)
- Total identified savings (monthly and annual)

**Running a Scan**
- Click **Run Scan** to start scanning all connected platforms
- A progress bar shows real-time status (current platform, % complete)
- Click **Stop Scan** to cancel a running scan

**Managing Inactive Users**

Each organization section shows inactive users grouped by platform. For each user you can see:
- Name, email, last active date, monthly cost
- Current status (Pending, Deactivated, or Kept)

To take action:
1. Use the **status dropdown** next to each user to change their status
2. Or select multiple users with checkboxes and use **bulk actions**

| Status | Meaning |
|--------|---------|
| **Pending** | Flagged as inactive, no action taken yet |
| **Deactivated** | License reclaimed -counts toward realized savings |
| **Kept** | Intentionally retained (business justification) |

**Filters**
- Filter by platform using the dropdown
- Search by status text

---

### Token Health & Rotation

Centralized credential management with a 90-day rotation policy.

**Health Dashboard**
- A health score ring (0-100) summarizes overall token health
- Token cards show each platform's status:
  - **Healthy** (green) -rotated within 90 days
  - **Due Soon** (orange) -rotation approaching
  - **Overdue** (red) -past 90-day rotation window
  - **Not Configured** -no token on file

**Logging a Rotation**

When you rotate a token in the source platform:
1. Select the **system** from the dropdown
2. Choose **who** performed the rotation
3. Set the **date** the new token was created
4. Click **Save**

This logs the rotation metadata. Tokens are never stored in the platform -only rotation tracking data is recorded.

**Removing an Integration**
- Click the **X** button on a system to stop tracking its rotation
- A confirmation prompt prevents accidental removal

---

### Audit Log

A complete activity history for compliance and accountability.

**What's Tracked**
- Scan started / completed / cancelled
- User status changes (pending, deactivated, kept)
- Bulk status actions
- Token rotation logging
- Integration removals

**Controls**
- Filter by system or view all events
- Paginated at 15 entries per page
- Each entry shows: action, source system, details, user, and timestamp

---

### AI Insights

AI-generated executive summaries powered by Claude.

**Generating a Summary**
1. Navigate to the **AI Insights** tab
2. Click **Generate Summary**
3. The AI analyzes your latest scan data and produces:
   - **Headline** -one-sentence overview
   - **Key Findings** -what the scan uncovered
   - **Opportunities** -where the biggest savings are, with dollar amounts
   - **Recommended Actions** -concrete next steps
   - **Bottom Line** -strategic recommendation

**Caching**
- Summaries are cached and reused until new scan data is available
- Click **Generate Summary** again to force a refresh

---

## Technical Reference

### Architecture

```
license-scanner/
├── app.py              # Main Streamlit app -all UI logic (single-page app)
├── auth.py             # Azure AD OAuth2 via MSAL
├── config.py           # Environment config + SYSTEMS registry
├── storage.py          # Encrypted token storage (SQLite + Fernet)
├── ai_summary.py       # Claude API integration for executive summaries
├── requirements.txt    # Python dependencies
├── render.yaml         # Render.com deployment blueprint
├── .env.example        # Environment variable template
├── assets/             # Logo images
└── Data Files:
    ├── scan_results.json       # Latest scan output (written by external scanner)
    ├── scan_history.json       # Historical scan data for trends
    ├── user_actions.json       # User status decisions
    ├── rotation_metadata.json  # Token rotation tracking
    ├── user_registry.json      # Known users (auto-populated on login)
    ├── audit_log.json          # Activity log (last 500 entries)
    ├── ai_summary.json         # Cached AI summary
    └── watchdog.db             # SQLite database (encrypted tokens + audit)
```

### Data Flow

1. An external scanner (`system_watchdog`) connects to platform APIs and writes `scan_results.json`
2. The UI reads and renders scan results -it never calls platform APIs directly
3. User actions (status changes, rotations) are saved to JSON files and SQLite
4. The audit log captures all state changes with who, what, and when
5. AI summaries are generated on demand from scan data via the Claude API

### Authentication

**Access Code:** A shared access code (set via `ACCESS_CODE` env var) provides simple authentication for internal users. Enter the code on the login page to sign in.

**Microsoft SSO:** Azure AD SSO via MSAL authorization code flow is available as an alternative. User groups from Azure AD control which systems a user can manage (via `owner_group` in the SYSTEMS config).

### Security

- **Encryption at rest** -all tokens stored with Fernet (AES-128-CBC)
- **No tokens in the platform** -only rotation metadata is tracked; credentials are stored encrypted in SQLite and never displayed
- **Group-based access control** -Azure AD groups restrict system management
- **Audit trail** -every action is logged with user identity and timestamp

### Key Constants

| Constant | Value | Location |
|----------|-------|----------|
| Inactivity threshold | 60 days | Scanner config |
| Rotation policy | 90 days | `app.py` (`ROTATION_POLICY_DAYS`) |
| Audit log retention | 500 entries | `app.py` |
| Audit page size | 15 entries | `app.py` (`AUDIT_PER_PAGE`) |
| AI model | claude-sonnet-4-20250514 | `ai_summary.py` |

---

## Deployment

### Render.com

The repo includes a `render.yaml` blueprint for one-click deployment.

1. Push to GitHub
2. On Render: **New > Web Service** > connect repo
3. Render auto-detects the blueprint
4. Set environment variables in the Render dashboard:
   - `ENCRYPTION_KEY` (required)
   - `ANTHROPIC_API_KEY` (for AI Insights)
   - Azure AD vars (for production SSO)
5. Set `REDIRECT_URI` to your Render URL (e.g. `https://license-scanner.onrender.com`)

**Start command:** `streamlit run app.py --server.port $PORT --server.headless true --server.address 0.0.0.0`

**Note:** Render's free tier has an ephemeral filesystem -JSON data files and SQLite reset on redeploy. Use Render's Disk add-on for persistence.

### Azure App Service

1. Create a Python web app in Azure
2. Set environment variables in Application Settings
3. Set startup command: `streamlit run app.py --server.port 8000 --server.headless true`
4. Configure HTTPS and custom domain
5. Add the redirect URI to your Azure AD App Registration

---

## Adding New Platforms

To add a new platform to scan and track:

1. **Register the system** in `config.py`:

```python
SYSTEMS = {
    # ... existing systems ...
    "your_system_key": {
        "name": "Display Name",
        "description": "What this token is used for",
        "owner_group": None,  # Azure AD group ID, or None for all users
        "fields": [
            {"key": "api_token", "label": "API Token", "type": "password"},
            {"key": "base_url", "label": "Instance URL", "type": "text"},
        ],
    },
}
```

2. **Update the scanner** (`system_watchdog`) to connect to the new platform's API and include it in scan results

3. **Deploy** -the UI automatically picks up new systems from the config. No app.py changes needed.
