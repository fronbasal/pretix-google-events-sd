import json
from decimal import Decimal
from django import forms
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from i18nfield.forms import I18nFormField, I18nTextarea, I18nTextInput
from pretix.base.forms import SettingsForm
from pretix.base.models import Event
from pretix.control.views.event import EventSettingsFormView, EventSettingsViewMixin

from .signals import build_structured_data_preview, invalidate_structured_data_cache

EVENT_STATUS_CHOICES = (
    ("https://schema.org/EventScheduled", _("Scheduled")),
    ("https://schema.org/EventCancelled", _("Cancelled")),
    ("https://schema.org/EventPostponed", _("Postponed")),
    ("https://schema.org/EventRescheduled", _("Rescheduled")),
    ("https://schema.org/EventMovedOnline", _("Moved online")),
    ("https://schema.org/EventCompleted", _("Completed")),
)

ATTENDANCE_MODE_CHOICES = (
    ("https://schema.org/OfflineEventAttendanceMode", _("Offline")),
    ("https://schema.org/OnlineEventAttendanceMode", _("Online")),
    ("https://schema.org/MixedEventAttendanceMode", _("Mixed")),
)

AVAILABILITY_CHOICES = (
    ("https://schema.org/InStock", _("In stock")),
    ("https://schema.org/SoldOut", _("Sold out")),
    ("https://schema.org/PreOrder", _("Pre-order")),
)


def _i18n_is_empty(value) -> bool:
    if value is None:
        return True
    if hasattr(value, "data"):
        return not any(v for v in value.data.values())
    return value == ""


class GoogleEventsSettingsForm(SettingsForm):
    google_events_sd_enabled = forms.BooleanField(
        label=_("Enable structured data"),
        required=False,
        initial=True,
        help_text=_("Inject Event structured data (JSON-LD) into all public pages."),
    )

    google_events_sd_override_name = forms.BooleanField(
        label=_("Override event name"),
        required=False,
    )
    google_events_sd_name = I18nFormField(
        label=_("Event name"),
        required=False,
        widget=I18nTextInput,
    )

    google_events_sd_override_description = forms.BooleanField(
        label=_("Override event description"),
        required=False,
    )
    google_events_sd_description = I18nFormField(
        label=_("Event description"),
        required=False,
        widget=I18nTextarea,
    )

    google_events_sd_override_image = forms.BooleanField(
        label=_("Override event image URL"),
        required=False,
    )
    google_events_sd_image = forms.URLField(
        label=_("Event image URL"),
        required=False,
    )

    google_events_sd_override_location_name = forms.BooleanField(
        label=_("Override location name"),
        required=False,
    )
    google_events_sd_location_name = I18nFormField(
        label=_("Location name"),
        required=False,
        widget=I18nTextInput,
    )

    google_events_sd_override_location_address = forms.BooleanField(
        label=_("Override location address"),
        required=False,
    )
    google_events_sd_location_street = forms.CharField(
        label=_("Street address"),
        required=False,
    )
    google_events_sd_location_locality = forms.CharField(
        label=_("City"),
        required=False,
    )
    google_events_sd_location_region = forms.CharField(
        label=_("Region"),
        required=False,
    )
    google_events_sd_location_postal = forms.CharField(
        label=_("Postal code"),
        required=False,
    )
    google_events_sd_location_country = forms.CharField(
        label=_("Country"),
        required=False,
    )

    google_events_sd_override_performer_name = forms.BooleanField(
        label=_("Override performer"),
        required=False,
    )
    google_events_sd_performer_name = I18nFormField(
        label=_("Performer"),
        required=False,
        widget=I18nTextInput,
    )

    google_events_sd_override_organizer_name = forms.BooleanField(
        label=_("Override organizer name"),
        required=False,
    )
    google_events_sd_organizer_name = I18nFormField(
        label=_("Organizer name"),
        required=False,
        widget=I18nTextInput,
    )

    google_events_sd_override_organizer_url = forms.BooleanField(
        label=_("Override organizer URL"),
        required=False,
    )
    google_events_sd_organizer_url = forms.URLField(
        label=_("Organizer URL"),
        required=False,
    )

    google_events_sd_override_event_status = forms.BooleanField(
        label=_("Override event status"),
        required=False,
    )
    google_events_sd_event_status = forms.ChoiceField(
        label=_("Event status"),
        required=False,
        choices=EVENT_STATUS_CHOICES,
    )

    google_events_sd_override_attendance_mode = forms.BooleanField(
        label=_("Override attendance mode"),
        required=False,
    )
    google_events_sd_attendance_mode = forms.ChoiceField(
        label=_("Attendance mode"),
        required=False,
        choices=ATTENDANCE_MODE_CHOICES,
    )

    google_events_sd_override_offer_price = forms.BooleanField(
        label=_("Override offer price"),
        required=False,
    )
    google_events_sd_offer_price = forms.DecimalField(
        label=_("Offer price"),
        required=False,
        min_value=Decimal("0.00"),
    )

    google_events_sd_override_offer_currency = forms.BooleanField(
        label=_("Override offer currency"),
        required=False,
    )
    google_events_sd_offer_currency = forms.CharField(
        label=_("Offer currency"),
        required=False,
    )

    google_events_sd_override_offer_availability = forms.BooleanField(
        label=_("Override offer availability"),
        required=False,
    )
    google_events_sd_offer_availability = forms.ChoiceField(
        label=_("Offer availability"),
        required=False,
        choices=AVAILABILITY_CHOICES,
    )

    google_events_sd_override_offer_url = forms.BooleanField(
        label=_("Override offer URL"),
        required=False,
    )
    google_events_sd_offer_url = forms.URLField(
        label=_("Offer URL"),
        required=False,
    )

    google_events_sd_override_offer_valid_from = forms.BooleanField(
        label=_("Override offer valid-from"),
        required=False,
    )
    google_events_sd_offer_valid_from = forms.DateTimeField(
        label=_("Offer valid-from"),
        required=False,
        help_text=_("Defaults to the presale start date if available."),
    )

    google_events_sd_item_overrides = forms.CharField(
        label=_("Per-item offer overrides"),
        required=False,
        widget=forms.HiddenInput,
    )

    override_pairs = (
        ("google_events_sd_override_name", ["google_events_sd_name"]),
        ("google_events_sd_override_description", ["google_events_sd_description"]),
        ("google_events_sd_override_image", ["google_events_sd_image"]),
        ("google_events_sd_override_location_name", ["google_events_sd_location_name"]),
        (
            "google_events_sd_override_location_address",
            [
                "google_events_sd_location_street",
                "google_events_sd_location_locality",
                "google_events_sd_location_region",
                "google_events_sd_location_postal",
                "google_events_sd_location_country",
            ],
        ),
        (
            "google_events_sd_override_performer_name",
            ["google_events_sd_performer_name"],
        ),
        (
            "google_events_sd_override_organizer_name",
            ["google_events_sd_organizer_name"],
        ),
        ("google_events_sd_override_organizer_url", ["google_events_sd_organizer_url"]),
        ("google_events_sd_override_event_status", ["google_events_sd_event_status"]),
        (
            "google_events_sd_override_attendance_mode",
            ["google_events_sd_attendance_mode"],
        ),
        ("google_events_sd_override_offer_price", ["google_events_sd_offer_price"]),
        (
            "google_events_sd_override_offer_currency",
            ["google_events_sd_offer_currency"],
        ),
        (
            "google_events_sd_override_offer_availability",
            ["google_events_sd_offer_availability"],
        ),
        ("google_events_sd_override_offer_url", ["google_events_sd_offer_url"]),
        (
            "google_events_sd_override_offer_valid_from",
            ["google_events_sd_offer_valid_from"],
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        event = self.obj
        if not event:
            return

        defaults = _get_event_defaults(event)

        for key, value in defaults.items():
            if key not in self.initial:
                self.fields[key].initial = value

        for override_key, value_keys in self.override_pairs:
            if override_key in self.initial:
                continue
            base_value = None
            for value_key in value_keys:
                base_value = defaults.get(value_key)
                if not _i18n_is_empty(base_value):
                    break
            self.fields[override_key].initial = _i18n_is_empty(base_value)

    def save(self):
        result = super().save()
        for override_key, value_keys in self.override_pairs:
            if not self.cleaned_data.get(override_key, False):
                for value_key in value_keys:
                    self.obj.settings.delete(value_key)
        return result


class SettingsView(EventSettingsViewMixin, EventSettingsFormView):
    model = Event
    form_class = GoogleEventsSettingsForm
    template_name = "pretix_google_events/settings.html"
    permission = "can_change_event_settings"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        preview = None
        errors = []
        try:
            if self.request.GET.get("preview_refresh"):
                invalidate_structured_data_cache(self.request.event, self.request)
            data, errors = build_structured_data_preview(
                self.request.event, self.request
            )
            preview = json.dumps(data, indent=2, ensure_ascii=True)
        except Exception as e:
            import traceback

            preview = None
            error_msg = f"Preview generation failed: {str(e)}\n{traceback.format_exc()}"
            errors = [error_msg]
        context["structured_data_preview"] = preview
        context["structured_data_errors"] = errors
        context["active_items"] = _get_active_items(self.request.event)
        return context

    def get_success_url(self, **kwargs):
        return reverse(
            "plugins:pretix_google_events:settings",
            kwargs={
                "organizer": self.request.event.organizer.slug,
                "event": self.request.event.slug,
            },
        )


def _get_event_defaults(event) -> dict:
    """Get event defaults for form initialization (with setting key prefixes)."""
    from django.db.models import Min

    from pretix_google_events.signals import _event_defaults as get_core_defaults

    # Get core defaults from signals module (unprefixed keys)
    core_defaults = get_core_defaults(event)

    # Calculate min item price for form display
    def _min_item_price():
        items = event.items.filter(active=True, admission=True)
        if not items.exists():
            items = event.items.filter(active=True)
        if not items.exists():
            return None
        return items.aggregate(Min("default_price"))["default_price__min"]

    min_price = _min_item_price()

    # Map core defaults to form field names (with "google_events_sd_" prefix)
    defaults = {
        "google_events_sd_enabled": True,
        "google_events_sd_name": core_defaults["name"],
        "google_events_sd_description": core_defaults["description"],
        "google_events_sd_image": core_defaults["image"],
        "google_events_sd_location_name": core_defaults["location_name"],
        "google_events_sd_organizer_name": core_defaults["organizer_name"],
        "google_events_sd_organizer_url": core_defaults["organizer_url"],
        "google_events_sd_offer_currency": core_defaults["offer_currency"],
        "google_events_sd_offer_url": core_defaults["offer_url"],
        "google_events_sd_offer_valid_from": core_defaults["offer_valid_from"],
        "google_events_sd_attendance_mode": core_defaults["attendance_mode"],
        "google_events_sd_event_status": core_defaults["event_status"],
        "google_events_sd_offer_availability": core_defaults["offer_availability"],
    }

    if min_price is not None:
        defaults["google_events_sd_offer_price"] = min_price

    return defaults


def _get_active_items(event):
    """Get active items/variations filtered by availability windows."""
    from django.utils import timezone

    try:
        # Get current time
        now = timezone.now()
        items = event.items.filter(active=True)

        result = []
        for item in items:
            # Check if item is available now
            if item.available_from and item.available_from > now:
                continue  # Not yet available
            if item.available_until and item.available_until < now:
                continue  # No longer available

            # Default ignore: not an admission ticket OR requires voucher
            ignore_default = not item.admission or item.require_voucher

            variations = list(item.variations.filter(active=True))
            if not variations:
                # Item without variations
                result.append(
                    {
                        "type": "item",
                        "item_id": item.id,
                        "name": str(item.name),
                        "price": item.default_price,
                        "ignore_default": ignore_default,
                    }
                )
            else:
                # Item with variations
                for var in variations:
                    result.append(
                        {
                            "type": "variation",
                            "item_id": item.id,
                            "variation_id": var.id,
                            "name": f"{item.name} - {var.value}",
                            "price": var.default_price or item.default_price,
                            "ignore_default": ignore_default,
                        }
                    )

        return result
    except Exception:
        import logging

        logger = logging.getLogger(__name__)
        logger.exception("Failed to get active items")
        return []
