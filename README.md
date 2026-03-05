# 🔑 License Watchdog - Token Rotation Portal

A self-service web UI that lets system owners rotate their own API tokens — so you're not the bottleneck.

Built with **Streamlit** (Python), secured with **Azure AD** login and **AES encryption** for stored tokens.

---

## Quick Start (Local Development)

### 1. Prerequisites

- Python 3.10 or newer
- An Azure AD App Registration (see below)

### 2. Install dependencies

```bash
cd license-watchdog-ui
pip install -r requirements.txt
```

### 3. Configure environment

```bash
# Copy the template
cp .env.example .env

# Generate an encryption key
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Paste the key into .env as ENCRYPTION_KEY
# Set ACCESS_CODE to a shared login code (e.g. ACCESS_CODE=mycode123)
# Fill in your Azure AD values (see next section)
```

### 4. Run locally

```bash
streamlit run app.py
```

The app opens at **http://localhost:8501**. Log in using the access code set in your `.env` file.

---

## Azure AD Setup (One-Time)

1. Go to **Azure Portal → Azure Active Directory → App Registrations → New Registration**
2. Set:
   - Name: `License Watchdog`
   - Redirect URI: `http://localhost:8501` (add your production URL later)
   - Supported account types: Single tenant
3. After creation, note the **Application (client) ID** and **Directory (tenant) ID**
4. Go to **Certificates & Secrets → New client secret** — copy the secret value
5. Put all three values in your `.env` file

### Optional: Restrict access by Azure AD group

1. Go to **Token Configuration → Add groups claim → Security groups**
2. Create groups in Azure AD like `Watchdog-Jira-Owners`
3. Copy each group's **Object ID** into the `owner_group` field in `config.py`

---

## Adding a New System

Edit `config.py` and add an entry to the `SYSTEMS` dict:

```python
SYSTEMS = {
    "jira": {
        "name": "Jira Software",
        "description": "Service account token for Jira license auditing",
        "owner_group": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",  # or None for any user
        "fields": [
            {"key": "api_token", "label": "API Token", "type": "password"},
            {"key": "email",     "label": "Service Account Email", "type": "text"},
            {"key": "base_url",  "label": "Jira Base URL", "type": "text"},
        ],
    },
}
```

That's it — the UI picks it up automatically.

---

## Deploy to Production

### Option A: Azure App Service (Recommended)

```bash
# Install Azure CLI if you haven't
# az login

# Create an App Service
az webapp up \
  --name license-watchdog \
  --resource-group your-rg \
  --runtime "PYTHON:3.11" \
  --sku B1

# Set environment variables
az webapp config appsettings set \
  --name license-watchdog \
  --resource-group your-rg \
  --settings \
    AZURE_CLIENT_ID="your-client-id" \
    AZURE_TENANT_ID="your-tenant-id" \
    AZURE_CLIENT_SECRET="your-secret" \
    ENCRYPTION_KEY="your-fernet-key" \
    REDIRECT_URI="https://license-watchdog.azurewebsites.net"
```

Create a `startup.txt` file:

```
streamlit run app.py --server.port 8000 --server.headless true
```

Then set the startup command:

```bash
az webapp config set \
  --name license-watchdog \
  --resource-group your-rg \
  --startup-file startup.txt
```

### Option B: Internal VM

```bash
# On your VM
git clone <your-repo>
cd license-watchdog-ui
pip install -r requirements.txt
cp .env.example .env
# Edit .env with production values

# Run with nohup or systemd
nohup streamlit run app.py --server.port 8501 --server.headless true &
```

---

## Production Checklist

- [ ] Set a strong `ACCESS_CODE` in your environment
- [ ] Set `REDIRECT_URI` to your production URL
- [ ] Add production URL to Azure AD App Registration redirect URIs
- [ ] Store `.env` values as App Service settings (not in files)
- [ ] Use a persistent disk or Azure SQL for the database in production
- [ ] Set up HTTPS (App Service does this automatically)
- [ ] Test Azure AD login end-to-end
- [ ] Add your real systems to `config.py`

---

## Project Structure

```
license-watchdog-ui/
├── app.py              # Main Streamlit UI
├── auth.py             # Azure AD authentication
├── storage.py          # Encrypted token storage + audit log
├── config.py           # System registry + settings
├── requirements.txt    # Python dependencies
├── .env.example        # Environment variable template
└── README.md           # This file
```

---

## Security Notes

- Tokens are **encrypted at rest** using Fernet (AES-128-CBC) — even if someone gets the database file, they can't read the tokens without the encryption key
- All token changes are **audit-logged** with timestamp, user email, and action
- Azure AD groups can **restrict access** so only designated owners can rotate specific tokens
- Access code authentication provides a shared login for internal users
- Microsoft SSO via Azure AD is available as an alternative login method
