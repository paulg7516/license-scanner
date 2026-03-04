# License Watchdog — Demo Script

**Duration:** 5–7 minutes
**Audience:** IT leaders, finance, platform owners

---

## Opening (30 seconds)

> "License Watchdog gives you instant visibility into unused SaaS licenses across your organization — and lets you act on them in one click. Right now we're scanning PagerDuty, Atlassian, and GitLab across both Xolv and Catalight."

---

## 1. Scan Overview (2 minutes)

**Navigate to:** Scan Overview tab

> "This is the dashboard. Three numbers tell the story:"
>
> - **113 inactive users** detected across 5 platform instances
> - **$4,661/month** in recoverable spend
> - **$55,942/year** in potential savings
>
> "The scanner flags anyone inactive for 60+ days — no login, no API activity, nothing. Let me drill into Xolv's Atlassian as an example."

**Expand the Atlassian [Xolv] section**

> "62 users, costing us $3,900 a month, haven't touched Atlassian in two months. For each user you can see their name, email, last active date, and monthly cost."

**Show status controls**

> "From here, the system owner can mark each user as **Deactivated** to reclaim the license, or **Kept** if there's a business reason to retain access. You can also use bulk actions to handle them all at once."

**Key benefit:** *"No more spreadsheets, no more guessing. Real data, real dollar amounts, actionable in seconds."*

---

## 2. Token Health & Rotation (2 minutes)

**Navigate to:** Token Health & Rotation tab

> "This page solves a different pain point — credential management. Every integration runs on API tokens, and those tokens need to be rotated regularly for security."
>
> "Each card shows a platform's token health: green means healthy, orange means rotation is due soon, red means overdue. We enforce a 90-day rotation policy."

**Show the rotation form**

> "Tokens are never stored in the platform. When it's time to rotate, the system owner generates a new token in the source system, logs the rotation here, and we track *who* rotated *what* and *when*. The platform only records rotation metadata — not the credentials themselves."

**Key benefit:** *"No more Slack DMs asking 'who has the PagerDuty token?' — rotation is self-service, tracked, and no secrets live in the platform."*

---

## 3. Audit Log (1 minute)

**Navigate to:** Audit Log tab

> "Every action in the system is logged: scans, status changes, token rotations, bulk operations. Filter by event type or system. This is your compliance trail."

**Key benefit:** *"Full accountability — you always know who did what and when."*

---

## Closing (30 seconds)

> "To recap — License Watchdog gives you three things:"
>
> 1. **Visibility** — Know exactly which licenses are going unused
> 2. **Savings** — We've identified nearly **$56K/year** in recoverable spend today
> 3. **Security** — Centralized rotation tracking with a full audit trail — no tokens stored in the platform
>
> "It's live right now across Xolv and Catalight, and adding a new platform takes minutes. Questions?"

---

## Talking Points for Q&A

- **"How does it detect inactive users?"** — Connects to each platform's API and checks login/activity history. Anyone inactive 60+ days gets flagged.
- **"Does it actually deactivate users?"** — Not today. It flags and tracks decisions. Deactivation is still done in each platform by the owner — we surface the data and track the action.
- **"What platforms can you add?"** — Anything with an API. Adding a new system is a config change, not a code change.
- **"Who can access this?"** — Secured with Azure AD SSO. Platform access can be restricted to specific AD groups.
- **"Where is it hosted?"** — Can run on Azure App Service, internal VM, or Render.com — wherever your team prefers.
