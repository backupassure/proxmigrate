"""
DNS provider API integrations for automated ACME DNS-01 challenges.

Each provider implements create_txt_record() and delete_txt_record().
No Django imports — this module is testable in isolation.
"""

import json
import logging
import time

import requests

logger = logging.getLogger(__name__)

DNS_PROPAGATION_DELAY = 10  # seconds to wait after record creation


class DnsProviderError(Exception):
    """Raised when a DNS provider API call fails."""
    pass


# ---------------------------------------------------------------------------
# Cloudflare
# ---------------------------------------------------------------------------

def _cloudflare_headers(api_token):
    return {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }


def cloudflare_create_txt(domain, value, api_token, zone_id=""):
    """Create a TXT record via Cloudflare API.

    If zone_id is empty, auto-discovers it from the domain.
    Returns the record ID for later deletion.
    """
    headers = _cloudflare_headers(api_token)

    if not zone_id:
        zone_id = _cloudflare_find_zone(domain, headers)

    record_name = f"_acme-challenge.{domain}"

    # Delete any existing ACME challenge records first
    _cloudflare_delete_existing(zone_id, record_name, headers)

    resp = requests.post(
        f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records",
        headers=headers,
        json={
            "type": "TXT",
            "name": record_name,
            "content": value,
            "ttl": 120,
        },
        timeout=30,
    )
    data = resp.json()
    if not data.get("success"):
        errors = data.get("errors", [])
        raise DnsProviderError(f"Cloudflare create TXT failed: {errors}")

    record_id = data["result"]["id"]
    logger.info("Cloudflare TXT record created: %s (ID: %s)", record_name, record_id)
    return record_id


def cloudflare_delete_txt(domain, api_token, zone_id="", record_id=""):
    """Delete the ACME challenge TXT record from Cloudflare."""
    headers = _cloudflare_headers(api_token)

    if not zone_id:
        zone_id = _cloudflare_find_zone(domain, headers)

    if record_id:
        resp = requests.delete(
            f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record_id}",
            headers=headers,
            timeout=30,
        )
        if resp.status_code not in (200, 404):
            logger.warning("Cloudflare delete record %s returned %d", record_id, resp.status_code)
    else:
        _cloudflare_delete_existing(zone_id, f"_acme-challenge.{domain}", headers)


def _cloudflare_find_zone(domain, headers):
    """Auto-discover the Cloudflare zone ID from the domain."""
    # Try progressively shorter domain parts (e.g. sub.example.com → example.com)
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        zone_name = ".".join(parts[i:])
        resp = requests.get(
            "https://api.cloudflare.com/client/v4/zones",
            headers=headers,
            params={"name": zone_name, "status": "active"},
            timeout=30,
        )
        data = resp.json()
        if data.get("success") and data.get("result"):
            return data["result"][0]["id"]

    raise DnsProviderError(
        f"Could not find Cloudflare zone for {domain}. "
        f"Provide the Zone ID manually."
    )


def _cloudflare_delete_existing(zone_id, record_name, headers):
    """Delete any existing TXT records with the given name."""
    resp = requests.get(
        f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records",
        headers=headers,
        params={"type": "TXT", "name": record_name},
        timeout=30,
    )
    data = resp.json()
    if data.get("success"):
        for record in data.get("result", []):
            requests.delete(
                f"https://api.cloudflare.com/client/v4/zones/{zone_id}/dns_records/{record['id']}",
                headers=headers,
                timeout=30,
            )


# ---------------------------------------------------------------------------
# AWS Route 53
# ---------------------------------------------------------------------------

def route53_create_txt(domain, value, api_token, api_secret="", zone_id=""):
    """Create a TXT record via AWS Route 53.

    api_token = AWS Access Key ID
    api_secret = AWS Secret Access Key
    zone_id = Hosted Zone ID (e.g. Z1234567890)
    """
    try:
        import boto3
    except ImportError:
        raise DnsProviderError(
            "AWS Route 53 requires the boto3 package. "
            "Install it with: pip install boto3"
        )

    client = boto3.client(
        "route53",
        aws_access_key_id=api_token,
        aws_secret_access_key=api_secret,
    )

    record_name = f"_acme-challenge.{domain}"

    client.change_resource_record_sets(
        HostedZoneId=zone_id,
        ChangeBatch={
            "Changes": [{
                "Action": "UPSERT",
                "ResourceRecordSet": {
                    "Name": record_name,
                    "Type": "TXT",
                    "TTL": 120,
                    "ResourceRecords": [{"Value": f'"{value}"'}],
                },
            }],
        },
    )
    logger.info("Route 53 TXT record upserted: %s", record_name)
    return ""


def route53_delete_txt(domain, api_token, api_secret="", zone_id=""):
    """Delete the ACME challenge TXT record from Route 53."""
    try:
        import boto3
    except ImportError:
        return

    client = boto3.client(
        "route53",
        aws_access_key_id=api_token,
        aws_secret_access_key=api_secret,
    )

    record_name = f"_acme-challenge.{domain}"

    # Get current record value to delete it
    try:
        resp = client.list_resource_record_sets(
            HostedZoneId=zone_id,
            StartRecordName=record_name,
            StartRecordType="TXT",
            MaxItems="1",
        )
        for rrs in resp.get("ResourceRecordSets", []):
            if rrs["Name"].rstrip(".") == record_name and rrs["Type"] == "TXT":
                client.change_resource_record_sets(
                    HostedZoneId=zone_id,
                    ChangeBatch={
                        "Changes": [{
                            "Action": "DELETE",
                            "ResourceRecordSet": rrs,
                        }],
                    },
                )
                logger.info("Route 53 TXT record deleted: %s", record_name)
    except Exception as exc:
        logger.warning("Route 53 delete failed: %s", exc)


# ---------------------------------------------------------------------------
# Azure DNS
# ---------------------------------------------------------------------------

def azure_create_txt(domain, value, api_token, zone_id=""):
    """Create a TXT record via Azure DNS REST API.

    api_token = Azure Bearer token (from service principal or managed identity)
    zone_id = Full resource ID: /subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.Network/dnsZones/{zone}
    """
    record_name = "_acme-challenge"
    url = (
        f"https://management.azure.com{zone_id}"
        f"/TXT/{record_name}?api-version=2018-05-01"
    )

    resp = requests.put(
        url,
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        },
        json={
            "properties": {
                "TTL": 120,
                "TXTRecords": [{"value": [value]}],
            },
        },
        timeout=30,
    )

    if resp.status_code not in (200, 201):
        raise DnsProviderError(f"Azure DNS create TXT failed: {resp.status_code} {resp.text}")

    logger.info("Azure DNS TXT record created: _acme-challenge.%s", domain)
    return ""


def azure_delete_txt(domain, api_token, zone_id=""):
    """Delete the ACME challenge TXT record from Azure DNS."""
    record_name = "_acme-challenge"
    url = (
        f"https://management.azure.com{zone_id}"
        f"/TXT/{record_name}?api-version=2018-05-01"
    )

    resp = requests.delete(
        url,
        headers={"Authorization": f"Bearer {api_token}"},
        timeout=30,
    )
    if resp.status_code not in (200, 204, 404):
        logger.warning("Azure DNS delete returned %d", resp.status_code)


# ---------------------------------------------------------------------------
# GoDaddy
# ---------------------------------------------------------------------------

def godaddy_create_txt(domain, value, api_token, api_secret="", zone_id=""):
    """Create a TXT record via GoDaddy API.

    api_token = GoDaddy API Key
    api_secret = GoDaddy API Secret
    zone_id = not used (domain is the zone)
    """
    # Find the base domain (GoDaddy uses the registerable domain as the zone)
    parts = domain.split(".")
    base_domain = ".".join(parts[-2:])
    record_name = "_acme-challenge"
    if len(parts) > 2:
        record_name = f"_acme-challenge.{'.'.join(parts[:-2])}"

    resp = requests.put(
        f"https://api.godaddy.com/v1/domains/{base_domain}/records/TXT/{record_name}",
        headers={
            "Authorization": f"sso-key {api_token}:{api_secret}",
            "Content-Type": "application/json",
        },
        json=[{"data": value, "ttl": 600}],
        timeout=30,
    )

    if resp.status_code not in (200, 204):
        raise DnsProviderError(f"GoDaddy create TXT failed: {resp.status_code} {resp.text}")

    logger.info("GoDaddy TXT record created: %s.%s", record_name, base_domain)
    return ""


def godaddy_delete_txt(domain, api_token, api_secret="", zone_id=""):
    """Delete the ACME challenge TXT record from GoDaddy."""
    parts = domain.split(".")
    base_domain = ".".join(parts[-2:])
    record_name = "_acme-challenge"
    if len(parts) > 2:
        record_name = f"_acme-challenge.{'.'.join(parts[:-2])}"

    # GoDaddy doesn't have a delete endpoint — overwrite with empty
    resp = requests.put(
        f"https://api.godaddy.com/v1/domains/{base_domain}/records/TXT/{record_name}",
        headers={
            "Authorization": f"sso-key {api_token}:{api_secret}",
            "Content-Type": "application/json",
        },
        json=[{"data": "removed", "ttl": 600}],
        timeout=30,
    )
    if resp.status_code not in (200, 204):
        logger.warning("GoDaddy delete returned %d", resp.status_code)


# ---------------------------------------------------------------------------
# DigitalOcean
# ---------------------------------------------------------------------------

def digitalocean_create_txt(domain, value, api_token, zone_id=""):
    """Create a TXT record via DigitalOcean API.

    api_token = DigitalOcean personal access token
    zone_id = not used (domain is the zone)
    """
    parts = domain.split(".")
    base_domain = ".".join(parts[-2:])
    record_name = "_acme-challenge"
    if len(parts) > 2:
        record_name = f"_acme-challenge.{'.'.join(parts[:-2])}"

    # Delete existing first
    _digitalocean_delete_existing(base_domain, record_name, api_token)

    resp = requests.post(
        f"https://api.digitalocean.com/v2/domains/{base_domain}/records",
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        },
        json={
            "type": "TXT",
            "name": record_name,
            "data": value,
            "ttl": 120,
        },
        timeout=30,
    )

    if resp.status_code not in (200, 201):
        raise DnsProviderError(f"DigitalOcean create TXT failed: {resp.status_code} {resp.text}")

    record_id = resp.json().get("domain_record", {}).get("id", "")
    logger.info("DigitalOcean TXT record created: %s.%s", record_name, base_domain)
    return str(record_id)


def digitalocean_delete_txt(domain, api_token, zone_id=""):
    """Delete the ACME challenge TXT record from DigitalOcean."""
    parts = domain.split(".")
    base_domain = ".".join(parts[-2:])
    record_name = "_acme-challenge"
    if len(parts) > 2:
        record_name = f"_acme-challenge.{'.'.join(parts[:-2])}"

    _digitalocean_delete_existing(base_domain, record_name, api_token)


def _digitalocean_delete_existing(base_domain, record_name, api_token):
    """Delete existing TXT records matching the name."""
    headers = {"Authorization": f"Bearer {api_token}"}
    resp = requests.get(
        f"https://api.digitalocean.com/v2/domains/{base_domain}/records",
        headers=headers,
        params={"type": "TXT", "name": f"{record_name}.{base_domain}"},
        timeout=30,
    )
    if resp.status_code == 200:
        for record in resp.json().get("domain_records", []):
            if record.get("name") == record_name:
                requests.delete(
                    f"https://api.digitalocean.com/v2/domains/{base_domain}/records/{record['id']}",
                    headers=headers,
                    timeout=30,
                )


# ---------------------------------------------------------------------------
# Dispatcher — unified interface
# ---------------------------------------------------------------------------

def create_txt_record(dns_provider, domain, value, api_token, api_secret="", zone_id=""):
    """Create a DNS TXT record using the configured provider.

    Returns a record_id string (may be empty for some providers).
    Raises DnsProviderError on failure.
    """
    providers = {
        "cloudflare": lambda: cloudflare_create_txt(domain, value, api_token, zone_id),
        "route53": lambda: route53_create_txt(domain, value, api_token, api_secret, zone_id),
        "azure": lambda: azure_create_txt(domain, value, api_token, zone_id),
        "godaddy": lambda: godaddy_create_txt(domain, value, api_token, api_secret, zone_id),
        "digitalocean": lambda: digitalocean_create_txt(domain, value, api_token, zone_id),
    }

    if dns_provider not in providers:
        raise DnsProviderError(f"Unsupported DNS provider: {dns_provider}")

    record_id = providers[dns_provider]()

    # Wait for propagation
    logger.info("Waiting %ds for DNS propagation...", DNS_PROPAGATION_DELAY)
    time.sleep(DNS_PROPAGATION_DELAY)

    return record_id


def delete_txt_record(dns_provider, domain, api_token, api_secret="", zone_id="", record_id=""):
    """Delete a DNS TXT record using the configured provider.

    Best-effort — logs warnings but does not raise on failure.
    """
    try:
        if dns_provider == "cloudflare":
            cloudflare_delete_txt(domain, api_token, zone_id, record_id)
        elif dns_provider == "route53":
            route53_delete_txt(domain, api_token, api_secret, zone_id)
        elif dns_provider == "azure":
            azure_delete_txt(domain, api_token, zone_id)
        elif dns_provider == "godaddy":
            godaddy_delete_txt(domain, api_token, api_secret, zone_id)
        elif dns_provider == "digitalocean":
            digitalocean_delete_txt(domain, api_token, zone_id)
    except Exception as exc:
        logger.warning("Failed to delete TXT record via %s: %s", dns_provider, exc)
