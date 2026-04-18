import logging

logger = logging.getLogger(__name__)

SESSION_KEY = "active_cluster_id"


class ActiveClusterMiddleware:
    """Resolve the session's selected cluster into request.active_cluster.

    Falls back to the default cluster (slug='default') when no selection
    exists or when the selected cluster has been deleted. If no clusters
    exist yet (fresh install, wizard not complete), request.active_cluster
    is None — callers must handle that case.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.active_cluster = self._resolve(request)
        return self.get_response(request)

    def _resolve(self, request):
        try:
            from apps.clusters.models import Cluster
        except Exception:
            return None

        cluster_id = None
        try:
            cluster_id = request.session.get(SESSION_KEY)
        except Exception:
            pass

        if cluster_id:
            cluster = Cluster.objects.filter(pk=cluster_id).first()
            if cluster:
                return cluster
            try:
                del request.session[SESSION_KEY]
            except Exception:
                pass

        default = Cluster.get_default()
        if default:
            return default
        return Cluster.objects.first()


def active_cluster(request):
    """Template context processor — exposes active_cluster and the cluster list."""
    try:
        from apps.clusters.models import Cluster
    except Exception:
        return {}
    return {
        "active_cluster": getattr(request, "active_cluster", None),
        "all_clusters": list(Cluster.objects.order_by("name")),
    }
