import json
import pytest
from datetime import timedelta
from decimal import Decimal
from django.test import override_settings
from django.utils.timezone import now
from django_scopes import scopes_disabled
from pretix.base.models import Event, Organizer, SubEvent
from types import SimpleNamespace

from pretix_google_events.signals import (
    build_structured_data_preview,
    get_structured_data_cache_key,
    html_head_presale,
    invalidate_structured_data_cache,
)


def _make_event(slug: str, name: str, **kwargs):
    organizer = Organizer.objects.create(name=f"Org {slug}", slug=f"org-{slug}")
    event = Event.objects.create(
        organizer=organizer,
        name=name,
        slug=slug,
        date_from=now(),
        **kwargs,
    )
    return event


@pytest.mark.django_db
@scopes_disabled()
def test_structured_data_includes_subevents():
    event = _make_event("series", "Series", has_subevents=True, location="Main Hall")
    SubEvent.objects.create(
        event=event,
        name="Date 1",
        date_from=now() + timedelta(days=2),
        date_to=now() + timedelta(days=2, hours=3),
        active=True,
        is_public=True,
    )

    request = SimpleNamespace(LANGUAGE_CODE="en")
    data, errors = build_structured_data_preview(event, request)

    assert not errors
    assert "subEvent" in data
    assert len(data["subEvent"]) == 1
    assert data["subEvent"][0]["name"] == "Date 1"


@pytest.mark.django_db
@scopes_disabled()
def test_html_head_renders_jsonld():
    event = _make_event("single", "Single")

    request = SimpleNamespace(LANGUAGE_CODE="en", pci_dss_payment_page=False)
    html = html_head_presale(event, request=request)

    assert "application/ld+json" in html


@pytest.mark.django_db
@scopes_disabled()
def test_html_head_disabled_or_pci_dss_is_empty():
    event = _make_event("disabled", "Disabled")
    event.settings.set("google_events_sd_enabled", False)

    request = SimpleNamespace(LANGUAGE_CODE="en", pci_dss_payment_page=False)
    assert html_head_presale(event, request=request) == ""

    request = SimpleNamespace(LANGUAGE_CODE="en", pci_dss_payment_page=True)
    event.settings.set("google_events_sd_enabled", True)
    assert html_head_presale(event, request=request) == ""


@pytest.mark.django_db
@scopes_disabled()
def test_cache_invalidation():
    event = _make_event("cache-test", "Cache Test")

    request = SimpleNamespace(LANGUAGE_CODE="en")
    cache_key = get_structured_data_cache_key("en")
    event.cache.set(cache_key, "payload", 60)

    invalidate_structured_data_cache(event, request)

    assert event.cache.get(cache_key) is None


@pytest.mark.django_db
@scopes_disabled()
def test_cached_payload_is_used():
    with override_settings(
        CACHES={
            "default": {
                "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                "LOCATION": "google-events-sd-test",
            }
        }
    ):
        event = _make_event("cached", "Cached")
        request = SimpleNamespace(LANGUAGE_CODE="en", pci_dss_payment_page=False)
        cache_key = get_structured_data_cache_key("en")
        event.cache.set(cache_key, '{"cached": true}', 60)

        html = html_head_presale(event, request=request)
        assert '"cached": true' in html


@pytest.mark.django_db
@scopes_disabled()
def test_validation_missing_location_for_offline_event():
    event = _make_event("offline", "Offline")
    event.settings.set("google_events_sd_override_attendance_mode", True)
    event.settings.set(
        "google_events_sd_attendance_mode",
        "https://schema.org/OfflineEventAttendanceMode",
    )
    event.settings.set("google_events_sd_override_location_name", True)
    event.settings.set("google_events_sd_location_name", "")

    request = SimpleNamespace(LANGUAGE_CODE="en")
    data, errors = build_structured_data_preview(event, request)

    assert "location" not in data
    assert any("location" in err.lower() for err in errors)


@pytest.mark.django_db
@scopes_disabled()
def test_validation_offer_price_requires_currency():
    event = _make_event("offer", "Offer")
    event.settings.set("google_events_sd_override_offer_price", True)
    event.settings.set("google_events_sd_offer_price", Decimal("12.00"))
    event.settings.set("google_events_sd_override_offer_currency", True)
    event.settings.set("google_events_sd_offer_currency", "")

    request = SimpleNamespace(LANGUAGE_CODE="en")
    _, errors = build_structured_data_preview(event, request)

    assert any("pricecurrency" in err.lower() for err in errors)


@pytest.mark.django_db
@scopes_disabled()
def test_description_is_stripped():
    event = _make_event("desc", "Desc")
    event.settings.set("google_events_sd_override_description", True)
    event.settings.set("google_events_sd_description", "<p>Hello <b>World</b></p>")

    request = SimpleNamespace(LANGUAGE_CODE="en")
    data, _ = build_structured_data_preview(event, request)

    assert data["description"] == "Hello World"


@pytest.mark.django_db
@scopes_disabled()
def test_online_event_includes_virtual_location():
    event = _make_event("online", "Online")
    event.settings.set("google_events_sd_override_attendance_mode", True)
    event.settings.set(
        "google_events_sd_attendance_mode",
        "https://schema.org/OnlineEventAttendanceMode",
    )

    request = SimpleNamespace(LANGUAGE_CODE="en")
    data, errors = build_structured_data_preview(event, request)

    assert not errors
    assert data["location"]["@type"] == "VirtualLocation"


@pytest.mark.django_db
@scopes_disabled()
def test_mixed_event_includes_place_and_virtual():
    event = _make_event("mixed", "Mixed", location="Hall A")
    event.settings.set("google_events_sd_override_attendance_mode", True)
    event.settings.set(
        "google_events_sd_attendance_mode",
        "https://schema.org/MixedEventAttendanceMode",
    )

    request = SimpleNamespace(LANGUAGE_CODE="en")
    data, errors = build_structured_data_preview(event, request)

    assert not errors
    assert isinstance(data["location"], list)
    types = {entry["@type"] for entry in data["location"]}
    assert "Place" in types
    assert "VirtualLocation" in types


@pytest.mark.django_db
@scopes_disabled()
def test_missing_name_triggers_validation_warning():
    event = _make_event("noname", "Name")
    event.settings.set("google_events_sd_override_name", True)
    event.settings.set("google_events_sd_name", "")

    request = SimpleNamespace(LANGUAGE_CODE="en")
    _, errors = build_structured_data_preview(event, request)

    assert any("name" in err.lower() for err in errors)


@pytest.mark.django_db
@scopes_disabled()
def test_per_item_offer_overrides():
    """Test per-item offer price overrides."""
    event = _make_event("offers-override", "Event", location="Main Hall")
    item1 = event.items.create(
        name="General", admission=True, active=True, default_price=Decimal("10.00")
    )
    item2 = event.items.create(
        name="VIP", admission=True, active=True, default_price=Decimal("20.00")
    )

    # Set per-item overrides
    overrides = {
        str(item1.id): {"price": "12.50", "currency": "USD"},
        str(item2.id): {"price": "25.00", "currency": "USD"},
    }
    event.settings.set("google_events_sd_item_overrides", json.dumps(overrides))

    request = SimpleNamespace(LANGUAGE_CODE="en")
    data, errors = build_structured_data_preview(event, request)

    assert not errors
    assert "offers" in data
    assert len(data["offers"]) == 2
    assert data["offers"][0]["price"] == "12.50"
    assert data["offers"][1]["price"] == "25.00"


@pytest.mark.django_db
@scopes_disabled()
def test_per_variation_offer_overrides():
    """Test per-variation offer price overrides."""
    from pretix.base.models import ItemVariation

    event = _make_event("var-overrides", "Event", location="Main Hall")
    item = event.items.create(
        name="T-Shirt", admission=True, active=True, default_price=Decimal("15.00")
    )
    var_s = ItemVariation.objects.create(
        item=item, value="Small", active=True, default_price=Decimal("15.00")
    )
    var_l = ItemVariation.objects.create(
        item=item, value="Large", active=True, default_price=Decimal("18.00")
    )

    # Set per-variation overrides
    overrides = {
        f"{item.id}-{var_s.id}": {
            "price": "14.00",
            "availability": "https://schema.org/InStock",
        },
        f"{item.id}-{var_l.id}": {
            "price": "20.00",
            "availability": "https://schema.org/SoldOut",
        },
    }
    event.settings.set("google_events_sd_item_overrides", json.dumps(overrides))

    request = SimpleNamespace(LANGUAGE_CODE="en")
    data, errors = build_structured_data_preview(event, request)

    assert not errors
    assert "offers" in data
    assert len(data["offers"]) == 2
    assert data["offers"][0]["price"] == "14.00"
    assert data["offers"][0]["availability"] == "https://schema.org/InStock"
    assert data["offers"][1]["price"] == "20.00"
    assert data["offers"][1]["availability"] == "https://schema.org/SoldOut"


@pytest.mark.django_db
@scopes_disabled()
def test_item_overrides_fallback_to_global_settings():
    """Test that global settings apply when per-item overrides are not set."""
    event = _make_event("fallback-overrides", "Event", location="Main Hall")
    event.items.create(
        name="Ticket", admission=True, active=True, default_price=Decimal("10.00")
    )

    # Set global override
    event.settings.set("google_events_sd_override_offer_price", True)
    event.settings.set("google_events_sd_offer_price", Decimal("15.00"))

    # No per-item overrides
    event.settings.set("google_events_sd_item_overrides", "{}")

    request = SimpleNamespace(LANGUAGE_CODE="en")
    data, errors = build_structured_data_preview(event, request)

    assert not errors
    assert "offers" in data
    assert data["offers"][0]["price"] == "15.00"


@pytest.mark.django_db
@scopes_disabled()
def test_item_overrides_take_priority_over_global():
    """Test that per-item overrides take priority over global settings."""
    event = _make_event("priority-overrides", "Event", location="Main Hall")
    item = event.items.create(
        name="Ticket", admission=True, active=True, default_price=Decimal("10.00")
    )

    # Set global override
    event.settings.set("google_events_sd_override_offer_price", True)
    event.settings.set("google_events_sd_offer_price", Decimal("15.00"))

    # Set per-item override that takes priority
    overrides = {str(item.id): {"price": "99.99"}}
    event.settings.set("google_events_sd_item_overrides", json.dumps(overrides))

    request = SimpleNamespace(LANGUAGE_CODE="en")
    data, errors = build_structured_data_preview(event, request)

    assert not errors
    assert data["offers"][0]["price"] == "99.99"
