import logging

from django import forms

from apps.authconfig.models import EntraIDConfig
from apps.authconfig.models import LDAPConfig

logger = logging.getLogger(__name__)


class LDAPConfigForm(forms.ModelForm):
    """Form for editing LDAP authentication settings."""

    bind_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=True, attrs={"autocomplete": "off"}),
        label="Bind Password",
        help_text="Leave blank to keep the existing password.",
    )

    class Meta:
        model = LDAPConfig
        fields = [
            "is_enabled",
            "server_uri",
            "bind_dn",
            "bind_password",
            "user_search_base",
            "user_search_filter",
            "require_group",
            "admin_group",
            "use_tls",
            "skip_cert_verify",
        ]

    def save(self, commit=True):
        instance = super().save(commit=False)
        pw = self.cleaned_data.get("bind_password", "").strip()
        if pw:
            instance.bind_password = pw
        if commit:
            instance.save()
        return instance


class EntraIDConfigForm(forms.ModelForm):
    """Form for editing Entra ID (Azure AD) authentication settings."""

    client_secret = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=True, attrs={"autocomplete": "off"}),
        label="Client Secret",
        help_text="Leave blank to keep the existing secret.",
    )

    class Meta:
        model = EntraIDConfig
        fields = [
            "is_enabled",
            "tenant_id",
            "client_id",
            "client_secret",
            "allowed_domains",
            "admin_group_id",
        ]

    def save(self, commit=True):
        instance = super().save(commit=False)
        secret = self.cleaned_data.get("client_secret", "").strip()
        if secret:
            instance.client_secret = secret
        if commit:
            instance.save()
        return instance
