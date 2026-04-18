import logging
from functools import wraps

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from apps.clusters.middleware import SESSION_KEY
from apps.clusters.models import Cluster
from apps.proxmox.api import ProxmoxAPI

logger = logging.getLogger(__name__)


def _staff_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={request.path}")
        if not request.user.is_staff:
            return HttpResponse("Forbidden — staff access required.", status=403)
        return view_func(request, *args, **kwargs)

    return _wrapped


@_staff_required
def cluster_list(request):
    clusters = Cluster.objects.order_by("name")
    return render(request, "clusters/list.html", {
        "clusters": clusters,
        "help_slug": "clusters-list",
    })


@_staff_required
def cluster_add(request):
    errors = {}
    if request.method == "POST":
        errors = _save_cluster_from_post(request, cluster=None)
        if not errors:
            return redirect("cluster_list")
    values = _form_values(cluster=None, post=request.POST if request.method == "POST" else None)
    return render(request, "clusters/add.html", {
        "errors": errors,
        "values": values,
        "help_slug": "clusters-add",
    })


@_staff_required
def cluster_edit(request, cluster_id):
    cluster = get_object_or_404(Cluster, pk=cluster_id)
    errors = {}
    if request.method == "POST":
        errors = _save_cluster_from_post(request, cluster=cluster)
        if not errors:
            return redirect("cluster_list")
    values = _form_values(cluster=cluster, post=request.POST if request.method == "POST" else None)
    return render(request, "clusters/edit.html", {
        "cluster": cluster,
        "errors": errors,
        "values": values,
        "help_slug": "clusters-edit",
    })


def _form_values(cluster, post):
    """Return a dict with every form field populated from POST (if present) or the cluster."""
    fields = ("name", "host", "api_port", "ssh_port", "api_token_id")
    values = {f: "" for f in fields}
    if cluster:
        values["name"] = cluster.name
        values["host"] = cluster.host
        values["api_port"] = str(cluster.api_port)
        values["ssh_port"] = str(cluster.ssh_port)
        values["api_token_id"] = cluster.api_token_id
    if post:
        for f in fields:
            if f in post:
                values[f] = post.get(f, "")
    if not values["api_port"]:
        values["api_port"] = "8006"
    if not values["ssh_port"]:
        values["ssh_port"] = "22"
    return values


@_staff_required
@require_POST
def cluster_delete(request, cluster_id):
    cluster = get_object_or_404(Cluster, pk=cluster_id)
    if cluster.slug == "default":
        messages.error(
            request,
            "The default cluster cannot be deleted from this page — "
            "it is managed by the Proxmox settings wizard.",
        )
        return redirect("cluster_list")

    name = cluster.name
    cluster.delete()
    if request.session.get(SESSION_KEY) == cluster_id:
        request.session.pop(SESSION_KEY, None)
    messages.success(request, f"Cluster '{name}' deleted.")
    logger.info("Cluster %s (id=%s) deleted by %s", name, cluster_id, request.user)
    return redirect("cluster_list")


@_staff_required
@require_POST
def cluster_switch(request):
    """Set the active cluster for this session."""
    cluster_id = request.POST.get("cluster_id")
    next_url = request.POST.get("next") or reverse("dashboard")
    try:
        cluster_id = int(cluster_id)
    except (TypeError, ValueError):
        messages.error(request, "Invalid cluster.")
        return redirect(next_url)

    cluster = Cluster.objects.filter(pk=cluster_id).first()
    if not cluster:
        messages.error(request, "Cluster not found.")
        return redirect(next_url)

    request.session[SESSION_KEY] = cluster.pk
    messages.success(request, f"Switched to cluster '{cluster.name}'.")
    return redirect(next_url)


@_staff_required
@require_POST
def cluster_test(request):
    """Test connection to a cluster using posted or stored credentials.

    HTMX endpoint — returns a small notification snippet.
    """
    host = request.POST.get("host", "").strip()
    api_port = request.POST.get("api_port", "8006").strip() or "8006"
    token_id = request.POST.get("api_token_id", "").strip()
    token_secret = request.POST.get("api_token_secret", "").strip()
    cluster_id = request.POST.get("cluster_id", "").strip()

    if cluster_id and not token_secret:
        cluster = Cluster.objects.filter(pk=cluster_id).first()
        if cluster:
            token_secret = cluster.api_token_secret

    if not host or not token_id or not token_secret:
        return _notification("warning", "Host, token ID, and token secret are required.")

    try:
        api = ProxmoxAPI(host=host, port=int(api_port), token_id=token_id, token_secret=token_secret)
        nodes = api.get_nodes()
    except Exception as exc:
        logger.warning("Cluster test failed for %s: %s", host, exc)
        return _notification("danger", f"Connection failed: {exc}")

    return _notification(
        "success",
        f"Connected successfully — discovered {len(nodes)} node(s): "
        f"{', '.join(n.get('node', '?') for n in nodes)}",
    )


def _save_cluster_from_post(request, cluster):
    """Validate POST data and save. Returns dict of field errors (empty on success)."""
    errors = {}
    data = request.POST

    name = data.get("name", "").strip()
    host = data.get("host", "").strip()
    api_port_raw = data.get("api_port", "8006").strip() or "8006"
    ssh_port_raw = data.get("ssh_port", "22").strip() or "22"
    token_id = data.get("api_token_id", "").strip()
    token_secret = data.get("api_token_secret", "").strip()

    if not name:
        errors["name"] = "Display name is required."
    if not host:
        errors["host"] = "Host or IP is required."
    try:
        api_port = int(api_port_raw)
    except ValueError:
        errors["api_port"] = "API port must be a number."
        api_port = 8006
    try:
        ssh_port = int(ssh_port_raw)
    except ValueError:
        errors["ssh_port"] = "SSH port must be a number."
        ssh_port = 22
    if not token_id:
        errors["api_token_id"] = "API token ID is required."
    if cluster is None and not token_secret:
        errors["api_token_secret"] = "API token secret is required for new clusters."

    if errors:
        return errors

    if cluster is None:
        slug_base = slugify(name) or "cluster"
        slug = slug_base
        n = 1
        while Cluster.objects.filter(slug=slug).exists():
            n += 1
            slug = f"{slug_base}-{n}"
        cluster = Cluster(slug=slug, is_configured=True, wizard_step=5)

    cluster.name = name
    cluster.host = host
    cluster.api_port = api_port
    cluster.ssh_port = ssh_port
    cluster.api_token_id = token_id
    if token_secret:
        cluster.api_token_secret = token_secret
    cluster.save()
    messages.success(request, f"Cluster '{name}' saved.")
    logger.info("Cluster %s saved by %s", cluster.slug, request.user)
    return {}


def _notification(level, text):
    return HttpResponse(
        f'<div class="notification is-{level} is-light" '
        f'style="font-size:0.875rem;padding:0.6rem 0.85rem;margin:0;">'
        f"{text}</div>"
    )
