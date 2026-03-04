"""
Azure AD Authentication via MSAL.

Handles the OAuth2 authorization code flow for Streamlit.
User signs in with their Microsoft account; we get their email and group memberships.
"""

import streamlit as st
import msal
import config


def _build_msal_app():
    """Create an MSAL confidential client application."""
    return msal.ConfidentialClientApplication(
        client_id=config.AZURE_CLIENT_ID,
        client_credential=config.AZURE_CLIENT_SECRET,
        authority=config.AUTHORITY,
    )


def get_login_url() -> str:
    """Generate the Azure AD login URL."""
    app = _build_msal_app()
    flow = app.initiate_auth_code_flow(
        scopes=["User.Read"],
        redirect_uri=config.REDIRECT_URI,
    )
    # Store the flow in session so we can complete it after redirect
    st.session_state["auth_flow"] = flow
    return flow["auth_uri"]


def complete_login(auth_response: dict) -> dict | None:
    """
    Exchange the authorization code for tokens.
    Returns user info dict with 'name', 'email', 'groups' or None on failure.
    """
    app = _build_msal_app()
    flow = st.session_state.get("auth_flow", {})

    result = app.acquire_token_by_auth_code_flow(flow, auth_response)

    if "error" in result:
        st.error(f"Login failed: {result.get('error_description', result['error'])}")
        return None

    id_claims = result.get("id_token_claims", {})
    return {
        "name": id_claims.get("name", "Unknown"),
        "email": id_claims.get("preferred_username", "unknown@unknown.com"),
        "groups": id_claims.get("groups", []),  # Requires 'groups' claim in Azure app
    }


def is_authenticated() -> bool:
    """Check if the current session has a logged-in user."""
    return st.session_state.get("user") is not None


def get_current_user() -> dict:
    """Return the current logged-in user dict."""
    return st.session_state.get("user", {})


def can_manage_system(system_key: str) -> bool:
    """
    Check if the current user is allowed to manage a given system.
    If the system has no owner_group set, any authenticated user can manage it.
    """
    from config import SYSTEMS

    system = SYSTEMS.get(system_key)
    if not system:
        return False

    owner_group = system.get("owner_group")
    if owner_group is None:
        return True  # No restriction

    user = get_current_user()
    return owner_group in user.get("groups", [])


def logout():
    """Clear session state."""
    for key in ["user", "auth_flow"]:
        st.session_state.pop(key, None)
