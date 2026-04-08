import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, name="vmcreator.refresh_vm_community_catalog")
def refresh_vm_community_catalog(self):
    """Rebuild the VM community scripts catalog from GitHub."""
    from apps.vmcreator.vm_catalog import rebuild_catalog

    logger.info("Starting VM community catalog refresh...")
    result = rebuild_catalog()

    if result["success"]:
        logger.info("VM catalog refresh complete: %d scripts, %d categories",
                     result["script_count"], result["category_count"])
    else:
        logger.error("VM catalog refresh failed: %s", result["error"])

    return result
