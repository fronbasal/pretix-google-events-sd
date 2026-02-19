from django.utils.translation import gettext_lazy

from . import __version__

try:
    from pretix.base.plugins import PluginConfig
except ImportError:
    raise RuntimeError("Please use pretix 2.7 or above to run this plugin!")


class PluginApp(PluginConfig):
    default = True
    name = "pretix_google_events"
    verbose_name = "Pretix Google Events (StructuredData)"

    class PretixPluginMeta:
        name = gettext_lazy("Pretix Google Events (StructuredData)")
        author = "Daniel Malik <mail@fronbasal.de>"
        description = gettext_lazy(
            "Inject Google structured data for better search listings"
        )
        visible = True
        version = __version__
        category = "FEATURE"
        compatibility = "pretix>=2.7.0"
        settings_links = [
            (
                (
                    gettext_lazy("General"),
                    gettext_lazy("Google Events structured data"),
                ),
                "plugins:pretix_google_events:settings",
                {},
            ),
        ]
        navigation_links = []

    def ready(self):
        from . import signals  # NOQA

    def installed(self, event_or_organizer):
        """Called when the plugin is activated for an event."""
        # Suppress pretix's default event_microdata to avoid duplicate structured data
        # Set to a space (truthy but effectively empty) to prevent default generation
        if hasattr(event_or_organizer, "settings"):
            # Only suppress if the plugin is enabled
            if event_or_organizer.settings.get(
                "google_events_sd_enabled", True, as_type=bool
            ):
                event_or_organizer.settings.event_microdata = " "
