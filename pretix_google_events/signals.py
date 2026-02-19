import json
import logging
from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.dispatch import receiver
from django.urls import resolve, reverse
from django.utils.html import strip_tags
from django.utils.translation import gettext_lazy as _
from pretix.control.signals import nav_event_settings
from pretix.helpers.json import CustomJSONEncoder
from pretix.presale.signals import html_head

logger = logging.getLogger(__name__)


def _json_date(value, show_times: bool) -> str:
    if value is None:
        return ""
    if show_times:
        return value.isoformat()
    return value.date().isoformat()


def _format_price(value: Decimal) -> str:
    try:
        return f"{Decimal(value):.2f}"
    except (InvalidOperation, TypeError):
        return ""


def _is_valid_url(url: str | None) -> bool:
    """Validate that a URL is a proper HTTP(S) URL."""
    if not url:
        return False
    validator = URLValidator(schemes=("http", "https"))
    try:
        validator(url)
        return True
    except ValidationError:
        return False


def _override_enabled(event, key: str) -> bool:
    return event.settings.get(key, False, as_type=bool)


def _resolve_i18n(event, key: str, default, use_override: bool):
    """Resolve an i18n field to a localized string in the event's default locale."""
    from i18nfield.strings import LazyI18nString

    value = event.settings.get(key) if use_override else default
    if not value:
        return None

    # Ensure it's a LazyI18nString
    if isinstance(value, str):
        try:
            # Try to parse as JSON dict (stored i18n field)
            data = json.loads(value)
            if isinstance(data, dict):
                value = LazyI18nString(data)
        except (json.JSONDecodeError, TypeError):
            # It's a plain string, that's fine
            return value

    # Localize to event's default locale, not request locale
    if hasattr(value, "localize"):
        return value.localize(event.settings.locale)
    return str(value)


def _resolve_setting_override(
    event, value_key: str, override_key: str, default, as_type=None
):
    if _override_enabled(event, override_key):
        return event.settings.get(value_key, default, as_type=as_type)
    return default


def _event_defaults(event):
    from pretix.multidomain.urlreverse import build_absolute_uri

    defaults = {
        "name": event.name,
        "description": event.settings.frontpage_text,
        "image": event.social_image,
        "location_name": event.location,
        "organizer_name": event.organizer.name,
        "organizer_url": build_absolute_uri(event.organizer, "presale:organizer.index"),
        "offer_currency": event.currency,
        "offer_url": build_absolute_uri(event, "presale:event.index"),
        "offer_valid_from": event.effective_presale_start,
    }

    if event.is_remote:
        defaults["attendance_mode"] = "https://schema.org/OnlineEventAttendanceMode"
    else:
        defaults["attendance_mode"] = "https://schema.org/OfflineEventAttendanceMode"

    if event.date_to and event.date_to < event.date_from:
        defaults["event_status"] = "https://schema.org/EventCompleted"
    else:
        defaults["event_status"] = "https://schema.org/EventScheduled"

    if event.presale_is_running:
        defaults["offer_availability"] = "https://schema.org/InStock"
    elif event.presale_has_ended:
        defaults["offer_availability"] = "https://schema.org/SoldOut"
    else:
        defaults["offer_availability"] = "https://schema.org/PreOrder"

    return defaults


def _parse_german_address(street_field: str | None) -> dict:
    """Parse German format address from multi-line string.

    German format typically:
    Line 1: Street and number
    Line 2: Postal code and city
    Line 3 (optional): Country
    """
    if not street_field:
        return {}

    lines = [line.strip() for line in street_field.split("\n") if line.strip()]
    if not lines:
        return {}

    parsed = {}

    # First line: street address
    if len(lines) >= 1:
        parsed["streetAddress"] = lines[0]

    # Second line: try to parse postal code and city
    if len(lines) >= 2:
        parts = lines[1].split(None, 1)  # Split on first whitespace
        if len(parts) == 2:
            # Assume first part is postal code, second is city
            parsed["postalCode"] = parts[0]
            parsed["addressLocality"] = parts[1]
        else:
            parsed["addressLocality"] = lines[1]

    # Third line: country
    if len(lines) >= 3:
        parsed["addressCountry"] = lines[2]

    return parsed


def _build_location(settings) -> dict | list | None:
    """Build location data for structured data. Returns dict for single location, list for mixed mode, or None."""
    location_name = settings.get("location_name")
    attendance_mode = settings.get("attendance_mode")

    if attendance_mode == "https://schema.org/OnlineEventAttendanceMode":
        return {
            "@type": "VirtualLocation",
            "url": settings.get("offer_url"),
        }

    # Try to parse German format address first if street field contains newlines
    street_field = settings.get("location_street")
    if street_field and "\n" in street_field:
        parsed_address = _parse_german_address(street_field)
        address_fields = {
            "streetAddress": parsed_address.get("streetAddress") or street_field,
            "addressLocality": parsed_address.get("addressLocality")
            or settings.get("location_locality"),
            "addressRegion": parsed_address.get("addressRegion")
            or settings.get("location_region"),
            "postalCode": parsed_address.get("postalCode")
            or settings.get("location_postal"),
            "addressCountry": parsed_address.get("addressCountry")
            or settings.get("location_country"),
        }
    else:
        address_fields = {
            "streetAddress": settings.get("location_street"),
            "addressLocality": settings.get("location_locality"),
            "addressRegion": settings.get("location_region"),
            "postalCode": settings.get("location_postal"),
            "addressCountry": settings.get("location_country"),
        }

    address_payload = {k: v for k, v in address_fields.items() if v is not None}

    if attendance_mode == "https://schema.org/MixedEventAttendanceMode":
        locations = []
        if location_name or address_payload:
            place = {"@type": "Place"}
            if location_name:
                place["name"] = location_name
            if address_payload:
                place["address"] = {"@type": "PostalAddress", **address_payload}
            locations.append(place)
        locations.append({"@type": "VirtualLocation", "url": settings.get("offer_url")})
        return locations if locations else None

    # Offline or default mode: build single Place
    place = {"@type": "Place"}
    if location_name:
        place["name"] = location_name
    if address_payload:
        place["address"] = {"@type": "PostalAddress", **address_payload}
    return place if len(place) > 1 else None


def _build_location_for_name(settings, location_name: str | None) -> dict | list | None:
    """Build location data for a specific location name. Returns dict for single location, list for mixed mode, or None."""
    if (
        settings.get("attendance_mode")
        == "https://schema.org/OnlineEventAttendanceMode"
    ):
        return {
            "@type": "VirtualLocation",
            "url": settings.get("offer_url"),
        }

    if settings.get("attendance_mode") == "https://schema.org/MixedEventAttendanceMode":
        locations = []
        if location_name:
            locations.append({"@type": "Place", "name": location_name})
        locations.append({"@type": "VirtualLocation", "url": settings.get("offer_url")})
        return locations if locations else None

    if not location_name:
        return None
    return {"@type": "Place", "name": location_name}


def _build_subevents(event, settings, show_times: bool):
    if not event.has_subevents:
        return []

    subevents = (
        event.subevents.filter(active=True, is_public=True)
        .order_by("date_from")
        .values(
            "name",
            "date_from",
            "date_to",
            "location",
        )
    )
    data = []
    for subevent in subevents:
        entry = {
            "@type": "Event",
            "name": str(subevent["name"]),
            "startDate": _json_date(subevent["date_from"], show_times),
        }
        if subevent["date_to"]:
            entry["endDate"] = _json_date(subevent["date_to"], show_times)

        location_name = subevent["location"] or settings.get("location_name")
        location = _build_location_for_name(
            settings, str(location_name) if location_name else None
        )
        if location:
            entry["location"] = location

        data.append(entry)

    return data


def _iter_offer_items(event):
    """Get active items/variations for building offers."""
    items = event.items.filter(active=True, admission=True)
    if not items.exists():
        items = event.items.filter(active=True)
    return items.prefetch_related("variations")


def _is_within_availability_window(item_or_variation) -> bool:
    """Check if item/variation is within its availability window."""
    from pretix.base.timemachine import time_machine_now

    now_dt = time_machine_now()

    # Check available_from
    if (
        hasattr(item_or_variation, "available_from")
        and item_or_variation.available_from
    ):
        if item_or_variation.available_from > now_dt:
            return False

    # Check available_until
    if (
        hasattr(item_or_variation, "available_until")
        and item_or_variation.available_until
    ):
        if item_or_variation.available_until < now_dt:
            return False

    return True


def _get_item_availability(item_or_variation, event, subevent=None) -> str:
    """Check quota availability for an item or variation and return schema.org availability URL."""
    from pretix.base.models.items import Quota

    # Check if item/variation has quotas
    try:
        if event.has_subevents and not subevent:
            # For events with subevents but no specific subevent, we can't check quotas properly
            # Fall back to global presale status
            if event.presale_is_running:
                return "https://schema.org/InStock"
            elif event.presale_has_ended:
                return "https://schema.org/SoldOut"
            else:
                return "https://schema.org/PreOrder"

        availability, available_number = item_or_variation.check_quotas(
            count_waitinglist=True,
            subevent=subevent,
            trust_parameters=subevent is None,
            fail_on_no_quotas=False,
        )
    except (TypeError, ValueError) as e:
        logger.debug(
            "Could not check quotas for item %s: %s",
            item_or_variation.pk,
            str(e),
        )
        # Fall back to global presale status
        if event.presale_is_running:
            return "https://schema.org/InStock"
        elif event.presale_has_ended:
            return "https://schema.org/SoldOut"
        else:
            return "https://schema.org/PreOrder"

    # Map quota availability to schema.org values
    if availability == Quota.AVAILABILITY_OK:
        return "https://schema.org/InStock"
    elif availability in (Quota.AVAILABILITY_RESERVED, Quota.AVAILABILITY_ORDERED):
        # Items are reserved but not yet sold out
        return "https://schema.org/LimitedAvailability"
    else:  # AVAILABILITY_GONE
        return "https://schema.org/SoldOut"


def _build_offers(event, settings: dict) -> list:
    """Build offer data for structured data with validation."""
    offers = []
    has_overrides = (
        _override_enabled(event, "google_events_sd_override_offer_price")
        or _override_enabled(event, "google_events_sd_override_offer_currency")
        or _override_enabled(event, "google_events_sd_override_offer_availability")
        or _override_enabled(event, "google_events_sd_override_offer_url")
        or _override_enabled(event, "google_events_sd_override_offer_valid_from")
    )

    override_price = (
        settings.get("offer_price")
        if _override_enabled(event, "google_events_sd_override_offer_price")
        else None
    )
    override_currency = (
        settings.get("offer_currency")
        if _override_enabled(event, "google_events_sd_override_offer_currency")
        else None
    )
    override_availability = (
        settings.get("offer_availability")
        if _override_enabled(event, "google_events_sd_override_offer_availability")
        else None
    )
    override_url = (
        settings.get("offer_url")
        if _override_enabled(event, "google_events_sd_override_offer_url")
        else None
    )
    override_valid_from = (
        settings.get("offer_valid_from")
        if _override_enabled(event, "google_events_sd_override_offer_valid_from")
        else None
    )

    # Load per-item overrides
    item_overrides_json = event.settings.get("google_events_sd_item_overrides", "{}")
    try:
        item_overrides = json.loads(item_overrides_json) if item_overrides_json else {}
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(
            "Failed to parse item overrides JSON for event %s: %s", event.pk, e
        )
        item_overrides = {}

    for item in _iter_offer_items(event):
        # Skip if item itself is unavailable (e.g., outside availability window)
        if not _is_within_availability_window(item):
            continue

        variations = [v for v in item.variations.all() if v.active]
        if variations:
            for variation in variations:
                # Skip if variation is unavailable (e.g., outside availability window)
                if not _is_within_availability_window(variation):
                    continue

                item_key = f"{item.id}-{variation.id}"
                item_override = item_overrides.get(item_key, {})

                # Determine if item should be ignored
                # Default: ignore if not admission ticket OR requires voucher
                ignore_default = (not item.admission) or item.require_voucher

                # Check if there's an explicit override
                if "ignore" in item_override:
                    should_ignore = item_override.get("ignore") == "true"
                else:
                    should_ignore = ignore_default

                if should_ignore:
                    continue

                price = (
                    variation.default_price
                    if variation.default_price is not None
                    else item.default_price
                )

                # Validate item override URL
                item_url = item_override.get("url")
                if item_url and not _is_valid_url(item_url):
                    logger.warning(
                        "Invalid URL in item override for event %s item %s: %s",
                        event.pk,
                        item_key,
                        item_url,
                    )
                    item_url = None

                # Determine availability: per-item override > actual quota > global override > default
                if "availability" in item_override and item_override.get(
                    "availability"
                ):
                    availability = item_override.get("availability")
                elif override_availability:
                    availability = override_availability
                else:
                    # Check actual quota availability for this variation
                    availability = _get_item_availability(
                        variation, event, subevent=None
                    )

                offer = {
                    "@type": "Offer",
                    "url": item_url or override_url or settings.get("offer_url"),
                    "price": _format_price(
                        item_override.get("price") or override_price or price
                    ),
                    "priceCurrency": item_override.get("currency")
                    or override_currency
                    or settings.get("offer_currency"),
                    "availability": availability,
                    "validFrom": (
                        _json_date(override_valid_from, True)
                        if override_valid_from
                        else None
                    ),
                }
                offers.append(offer)
        else:
            item_key = str(item.id)
            item_override = item_overrides.get(item_key, {})

            # Determine if item should be ignored
            # Default: ignore if not admission ticket OR requires voucher
            ignore_default = (not item.admission) or item.require_voucher

            # Check if there's an explicit override
            if "ignore" in item_override:
                should_ignore = item_override.get("ignore") == "true"
            else:
                should_ignore = ignore_default

            if should_ignore:
                continue

            # Also skip items outside their availability window
            if not _is_within_availability_window(item):
                continue

            # Validate item override URL
            item_url = item_override.get("url")
            if item_url and not _is_valid_url(item_url):
                logger.warning(
                    "Invalid URL in item override for event %s item %s: %s",
                    event.pk,
                    item_key,
                    item_url,
                )
                item_url = None

            # Determine availability: per-item override > actual quota > global override > default
            if "availability" in item_override and item_override.get("availability"):
                availability = item_override.get("availability")
            elif override_availability:
                availability = override_availability
            else:
                # Check actual quota availability for this item
                availability = _get_item_availability(item, event, subevent=None)

            offer = {
                "@type": "Offer",
                "url": item_url or override_url or settings.get("offer_url"),
                "price": _format_price(
                    item_override.get("price") or override_price or item.default_price
                ),
                "priceCurrency": item_override.get("currency")
                or override_currency
                or settings.get("offer_currency"),
                "availability": availability,
                "validFrom": (
                    _json_date(override_valid_from, True)
                    if override_valid_from
                    else None
                ),
            }
            offers.append(offer)

    if not offers and has_overrides:
        offers.append(
            {
                "@type": "Offer",
                "url": override_url or settings.get("offer_url"),
                "price": (
                    _format_price(override_price)
                    if override_price is not None
                    else None
                ),
                "priceCurrency": override_currency or settings.get("offer_currency"),
                "availability": override_availability
                or settings.get("offer_availability"),
                "validFrom": (
                    _json_date(override_valid_from, True)
                    if override_valid_from
                    else None
                ),
            }
        )

    # Remove None values and return
    cleaned = []
    for offer in offers:
        cleaned_offer = {k: v for k, v in offer.items() if v is not None}
        cleaned.append(cleaned_offer)
    return cleaned


def _build_structured_data(event, request) -> dict:
    """Build structured data for an event."""
    defaults = _event_defaults(event)
    settings = {
        "name": _resolve_i18n(
            event,
            "google_events_sd_name",
            defaults["name"],
            _override_enabled(event, "google_events_sd_override_name"),
        ),
        "description": _resolve_i18n(
            event,
            "google_events_sd_description",
            defaults["description"],
            _override_enabled(event, "google_events_sd_override_description"),
        ),
        "image": _resolve_setting_override(
            event,
            "google_events_sd_image",
            "google_events_sd_override_image",
            defaults["image"],
        ),
        "location_name": _resolve_i18n(
            event,
            "google_events_sd_location_name",
            defaults["location_name"],
            _override_enabled(event, "google_events_sd_override_location_name"),
        ),
        "location_street": _resolve_setting_override(
            event,
            "google_events_sd_location_street",
            "google_events_sd_override_location_address",
            "",
        ),
        "location_locality": _resolve_setting_override(
            event,
            "google_events_sd_location_locality",
            "google_events_sd_override_location_address",
            "",
        ),
        "location_region": _resolve_setting_override(
            event,
            "google_events_sd_location_region",
            "google_events_sd_override_location_address",
            "",
        ),
        "location_postal": _resolve_setting_override(
            event,
            "google_events_sd_location_postal",
            "google_events_sd_override_location_address",
            "",
        ),
        "location_country": _resolve_setting_override(
            event,
            "google_events_sd_location_country",
            "google_events_sd_override_location_address",
            "",
        ),
        "performer_name": _resolve_i18n(
            event,
            "google_events_sd_performer_name",
            None,
            _override_enabled(event, "google_events_sd_override_performer_name"),
        ),
        "organizer_name": _resolve_i18n(
            event,
            "google_events_sd_organizer_name",
            defaults["organizer_name"],
            _override_enabled(event, "google_events_sd_override_organizer_name"),
        ),
        "organizer_url": _resolve_setting_override(
            event,
            "google_events_sd_organizer_url",
            "google_events_sd_override_organizer_url",
            defaults["organizer_url"],
        ),
        "event_status": _resolve_setting_override(
            event,
            "google_events_sd_event_status",
            "google_events_sd_override_event_status",
            defaults["event_status"],
        ),
        "attendance_mode": _resolve_setting_override(
            event,
            "google_events_sd_attendance_mode",
            "google_events_sd_override_attendance_mode",
            defaults["attendance_mode"],
        ),
        "offer_price": _resolve_setting_override(
            event,
            "google_events_sd_offer_price",
            "google_events_sd_override_offer_price",
            None,
            as_type=Decimal,
        ),
        "offer_currency": _resolve_setting_override(
            event,
            "google_events_sd_offer_currency",
            "google_events_sd_override_offer_currency",
            defaults["offer_currency"],
        ),
        "offer_availability": _resolve_setting_override(
            event,
            "google_events_sd_offer_availability",
            "google_events_sd_override_offer_availability",
            defaults["offer_availability"],
        ),
        "offer_url": _resolve_setting_override(
            event,
            "google_events_sd_offer_url",
            "google_events_sd_override_offer_url",
            defaults["offer_url"],
        ),
        "offer_valid_from": _resolve_setting_override(
            event,
            "google_events_sd_offer_valid_from",
            "google_events_sd_override_offer_valid_from",
            defaults["offer_valid_from"],
        ),
    }

    show_times = event.settings.show_times

    data = {
        "@context": "https://schema.org",
        "@type": "Event",
        "name": settings.get("name"),
        "startDate": _json_date(event.date_from, show_times),
    }

    if event.date_to:
        data["endDate"] = _json_date(event.date_to, show_times)

    if settings.get("event_status"):
        data["eventStatus"] = settings.get("event_status")

    if settings.get("attendance_mode"):
        data["eventAttendanceMode"] = settings.get("attendance_mode")

    description = settings.get("description")
    if description:
        # Strip HTML tags and remove any remaining HTML entities
        data["description"] = strip_tags(description)

    image = settings.get("image")
    if image:
        data["image"] = [image] if isinstance(image, str) else image

    location = _build_location(settings)
    if location:
        data["location"] = location

    organizer_name = settings.get("organizer_name")
    if organizer_name:
        organizer = {"@type": "Organization", "name": organizer_name}
        if settings.get("organizer_url"):
            organizer["url"] = settings.get("organizer_url")
        data["organizer"] = organizer

    performer_name = settings.get("performer_name")
    if performer_name:
        data["performer"] = {"@type": "PerformingGroup", "name": performer_name}

    offers = _build_offers(event, settings)
    if offers:
        data["offers"] = offers

    subevents = _build_subevents(event, settings, show_times)
    if subevents:
        data["subEvent"] = subevents

    return data


def _validate_structured_data(data: dict) -> list[str]:
    """Validate structured data and return list of validation warnings."""
    errors = []

    if not data.get("name"):
        errors.append("Missing event name")
    if not data.get("startDate"):
        errors.append("Missing startDate")

    attendance_mode = data.get("eventAttendanceMode")
    if attendance_mode in (
        "https://schema.org/OfflineEventAttendanceMode",
        "https://schema.org/MixedEventAttendanceMode",
    ) and not data.get("location"):
        errors.append("Missing location for offline or mixed events")

    offers = data.get("offers")
    if isinstance(offers, dict):
        offers = [offers]
    if offers:
        for offer in offers:
            if offer.get("price") and not offer.get("priceCurrency"):
                errors.append("Offer price requires priceCurrency")
            if offer.get("validFrom") and not offer.get("url"):
                errors.append("Offer validFrom should include a URL")

    return errors


def get_structured_data_cache_key(language_code: str) -> str:
    return f"google_events_sd_jsonld:{language_code}"


def invalidate_structured_data_cache(event, request):
    language_code = getattr(request, "LANGUAGE_CODE", "en")
    event.cache.delete(get_structured_data_cache_key(language_code))


def _build_payload_cached(event, request):
    language_code = getattr(request, "LANGUAGE_CODE", "en")
    cache_key = get_structured_data_cache_key(language_code)
    cached = event.cache.get(cache_key)
    if cached is not None:
        return cached

    data = _build_structured_data(event, request)
    errors = _validate_structured_data(data)
    if errors:
        logger.warning(
            "Structured data validation warnings for event %s: %s",
            event.pk,
            "; ".join(errors),
        )
    payload = json.dumps(data, cls=CustomJSONEncoder, ensure_ascii=True)
    event.cache.set(cache_key, payload, 600)
    return payload


@receiver(nav_event_settings, dispatch_uid="google_events_nav_event_settings")
def navbar_event_settings(sender, request, **kwargs):
    url = resolve(request.path_info)
    return [
        {
            "label": _("Google Events structured data"),
            "url": reverse(
                "plugins:pretix_google_events:settings",
                kwargs={
                    "event": request.event.slug,
                    "organizer": request.organizer.slug,
                },
            ),
            "active": url.namespace == "plugins:pretix_google_events"
            and url.url_name.startswith("settings"),
        }
    ]


@receiver(html_head, dispatch_uid="google_events_html_head")
def html_head_presale(sender, request=None, **kwargs) -> str:
    """Inject JSON-LD structured data into page head."""
    if not request or not sender:
        return ""
    if getattr(request, "pci_dss_payment_page", False):
        return ""
    if not sender.settings.get("google_events_sd_enabled", True, as_type=bool):
        return ""

    # Suppress pretix's default event_microdata to avoid duplicate structured data
    # Set to a space (truthy but effectively empty) to prevent default generation
    if (
        not sender.settings.event_microdata
        or sender.settings.event_microdata.strip() == ""
    ):
        sender.settings.event_microdata = " "

    try:
        payload = _build_payload_cached(sender, request)
        if not payload:
            return ""

        # Return JSON-LD structured data in a script tag
        # Only escape </script> sequences to prevent breaking out of the script tag
        # JSON-LD should NOT be HTML-escaped (no &quot; etc)
        safe_payload = payload.replace("</", "<\\/")
        return f'<script type="application/ld+json">{safe_payload}</script>\n'
    except Exception as e:
        logger.exception(
            "Failed to build Google Events structured data for event %s: %s",
            sender.pk,
            e,
        )
        return ""


def build_structured_data_preview(event, request):
    data = _build_structured_data(event, request)
    errors = _validate_structured_data(data)
    return data, errors
