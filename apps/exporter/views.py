import logging

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

logger = logging.getLogger(__name__)


@login_required
def export_index(request):
    """Phase 2 stub — VM export feature coming soon."""
    return render(request, "exporter/coming_soon.html", {"feature": "VM Export"})
