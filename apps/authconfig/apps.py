import logging

import requests
from django.apps import AppConfig

logger = logging.getLogger(__name__)


class AuthConfigConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.authconfig"
    label = "authconfig"

    def ready(self):
        """Load auth backends and connect auth-source tracking signals."""
        try:
            from .backend_loader import load_auth_backends_from_db
            load_auth_backends_from_db()
        except Exception:
            # Database may not exist yet (first run / pre-migration).
            pass

        self._connect_signals()

    def _connect_signals(self):
        # LDAP: mark auth_source='ldap' whenever django-auth-ldap populates a user.
        try:
            from django_auth_ldap.backend import populate_user

            def _ldap_auth_source(sender, user, ldap_user, **kwargs):
                from apps.core.models import UserProfile
                profile, _ = UserProfile.objects.get_or_create(user=user)
                if profile.auth_source != UserProfile.AUTH_SOURCE_LDAP:
                    profile.auth_source = UserProfile.AUTH_SOURCE_LDAP
                    profile.save(update_fields=["auth_source"])

            populate_user.connect(_ldap_auth_source, weak=False)
        except Exception:
            pass

        # Entra ID: pre-login hook — check group membership, enforce require_group_id,
        # and set is_staff/is_superuser based on admin_group_id.
        try:
            from allauth.exceptions import ImmediateHttpResponse
            from allauth.socialaccount.signals import pre_social_login
            from django.contrib import messages as django_messages
            from django.shortcuts import redirect as _redirect

            def _entra_pre_login(sender, request, sociallogin, **kwargs):
                if sociallogin.account.provider != "microsoft":
                    return
                try:
                    from apps.authconfig.models import EntraIDConfig
                    entra_cfg = EntraIDConfig.objects.first()
                    if not entra_cfg or not entra_cfg.is_enabled:
                        return

                    require_group_id = (entra_cfg.require_group_id or "").strip()
                    admin_group_id = (entra_cfg.admin_group_id or "").strip()

                    # Apply same logic as LDAP: if admin_group_id is set but
                    # require_group_id is blank, restrict login to admin group only.
                    effective_require = require_group_id or admin_group_id

                    if not effective_require and not admin_group_id:
                        return

                    token = sociallogin.token.token if sociallogin.token else None
                    if not token:
                        logger.warning("_entra_pre_login: no access token on sociallogin")
                        if effective_require:
                            django_messages.error(request, "Could not verify your Microsoft group membership. Please try again.")
                            raise ImmediateHttpResponse(_redirect("/login/"))
                        return

                    # Fetch group memberships from Microsoft Graph API.
                    # Follows @odata.nextLink for paginated results.
                    group_ids = set()
                    url = "https://graph.microsoft.com/v1.0/me/memberOf?$select=id"
                    while url:
                        try:
                            resp = requests.get(
                                url,
                                headers={"Authorization": f"Bearer {token}"},
                                timeout=10,
                            )
                        except Exception as exc:
                            logger.warning("_entra_pre_login: Graph API request failed: %s", exc)
                            if effective_require:
                                django_messages.error(request, "Could not verify your Microsoft group membership. Please try again.")
                                raise ImmediateHttpResponse(_redirect("/login/"))
                            return

                        if resp.status_code != 200:
                            logger.warning(
                                "_entra_pre_login: Graph API returned %s: %s",
                                resp.status_code, resp.text[:200],
                            )
                            if effective_require:
                                django_messages.error(request, "Could not verify your Microsoft group membership. Please try again.")
                                raise ImmediateHttpResponse(_redirect("/login/"))
                            return

                        data = resp.json()
                        for item in data.get("value", []):
                            gid = item.get("id", "")
                            if gid:
                                group_ids.add(gid)
                        url = data.get("@odata.nextLink")

                    # Enforce require group (or admin_group if no require_group set).
                    if effective_require and effective_require not in group_ids:
                        django_messages.error(request, "Your Microsoft account is not authorized to access ProxOrchestrator.")
                        raise ImmediateHttpResponse(_redirect("/login/"))

                    # Set admin flags based on admin_group_id membership.
                    if admin_group_id:
                        user = sociallogin.user
                        in_admin_group = admin_group_id in group_ids
                        user.is_staff = in_admin_group
                        user.is_superuser = in_admin_group
                        # Save immediately if this is a returning user.
                        if user.pk:
                            user.save(update_fields=["is_staff", "is_superuser"])

                except ImmediateHttpResponse:
                    raise
                except Exception:
                    logger.exception("_entra_pre_login: unexpected error")

            pre_social_login.connect(_entra_pre_login, weak=False)
        except Exception:
            pass

        # Entra ID: mark auth_source='entra' on first and subsequent Microsoft logins.
        try:
            from allauth.socialaccount.signals import social_account_added, social_account_updated

            def _entra_auth_source(sender, request, sociallogin, **kwargs):
                try:
                    if sociallogin.account.provider == "microsoft":
                        from apps.core.models import UserProfile
                        profile, _ = UserProfile.objects.get_or_create(user=sociallogin.user)
                        if profile.auth_source != UserProfile.AUTH_SOURCE_ENTRA:
                            profile.auth_source = UserProfile.AUTH_SOURCE_ENTRA
                            profile.save(update_fields=["auth_source"])
                except Exception:
                    logger.exception("_entra_auth_source signal failed")

            social_account_added.connect(_entra_auth_source, weak=False)
            social_account_updated.connect(_entra_auth_source, weak=False)
        except Exception:
            pass
