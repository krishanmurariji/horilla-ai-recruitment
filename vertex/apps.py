"""
App configuration for the 'vertex' app.

Ariel Vertex (operations, review and performance portal) rebuilt inside Ariel HR.
ArielVertex is the source of truth: roles, capabilities and rules are ported from it.
"""

from django.apps import AppConfig
from django.conf import settings


class VertexConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "vertex"
    verbose_name = "Ariel Vertex"

    def ready(self) -> None:
        ready = super().ready()
        from django.urls import include, path

        from horilla.urls import urlpatterns
        from vertex import audit, scheduler  # noqa: F401  scheduler starts the background jobs

        audit.register_all()

        settings.APPS.append("vertex")
        urlpatterns.append(path("vertex/", include("vertex.urls")))
        return ready
