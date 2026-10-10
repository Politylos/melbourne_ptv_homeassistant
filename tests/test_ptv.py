"""Tests for the PTV Transport setup screens, route publishing and YAML import."""
import json
from unittest.mock import patch

import pytest

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

D = "ptv_transport"
CF = "custom_components.ptv_transport.config_flow._validate_credentials"
JOURNEY = {"mode": "train", "route_type": 0, "route_id": 2, "line": "Belgrave", "number": "",
    "run_ref": "1", "towards": "Belgrave", "express_stops": 0,
    "origin": {"id": 1162, "name": "Richmond", "walk_m": 16},
    "destination": {"id": 1229, "name": "Union", "walk_m": 274}, "platform": "9",
    "scheduled_ts": 2000000000, "estimated_ts": None, "departure_ts": 2000000000,
    "departure": "1:33pm", "minutes": 5, "delay_min": 0, "arrival_ts": 2000001000,
    "arrival": "1:50pm", "stops": []}
ROUTE_INPUT = {"name": "Work", "from_location": {"latitude": -37.824, "longitude": 144.99},
    "to_location": {"latitude": -37.8255, "longitude": 145.0995}, "to_name": "Surrey Hills",
    "modes": ["train", "tram"],
    "walking": {"train": 1500, "tram": 800, "bus": 600, "vline": 3000, "night_bus": 600},
    "advanced": {"only_relevant_stops": True, "max_journeys": 5, "stops_per_mode": 4}}

async def test_user_flow(hass):
    r = await hass.config_entries.flow.async_init(D, context={"source": config_entries.SOURCE_USER})
    assert r["type"] is FlowResultType.FORM
    with patch(CF, return_value="invalid_auth"):
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"devid": "1", "api_key": "k"})
    assert r["errors"] == {"base": "invalid_auth"}
    with patch(CF, return_value=None), patch("custom_components.ptv_transport.async_setup_entry", return_value=True):
        r = await hass.config_entries.flow.async_configure(r["flow_id"], {"devid": "1", "api_key": "k"})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["data"] == {"devid": "1", "api_key": "k"}
    assert r["options"] == {"topic_prefix": "transport", "scan_interval": 120}

@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_route_subentry_and_publish(hass, mqtt_mock):
    entry = MockConfigEntry(domain=D, data={"devid": "1", "api_key": "k"},
                            options={"topic_prefix": "transport", "scan_interval": 120}, unique_id="1")
    entry.add_to_hass(hass)
    with patch("custom_components.ptv_transport.plan", return_value=[JOURNEY]) as p:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        r = await hass.config_entries.subentries.async_init((entry.entry_id, "route"), context={"source": "user"})
        assert r["type"] is FlowResultType.FORM, r
        r = await hass.config_entries.subentries.async_configure(r["flow_id"], ROUTE_INPUT)
        assert r["type"] is FlowResultType.CREATE_ENTRY, r
        await hass.async_block_till_done(wait_background_tasks=True)
        sub = next(iter(entry.subentries.values()))
        assert sub.data["to"] == {"latitude": -37.8255, "longitude": 145.0995, "name": "Surrey Hills"}
        assert sub.data["max_walk"] == {"train": 1500, "tram": 800}
        # duplicate name rejected
        r = await hass.config_entries.subentries.async_init((entry.entry_id, "route"), context={"source": "user"})
        r = await hass.config_entries.subentries.async_configure(r["flow_id"], ROUTE_INPUT)
        assert r["errors"] == {"name": "duplicate_name"}
        # reconfigure: follow an entity instead
        hass.states.async_set("person.me", "home", {"latitude": -37.82, "longitude": 144.99})
        r = await entry.start_subentry_reconfigure_flow(hass, sub.subentry_id)
        assert r["type"] is FlowResultType.FORM, r
        r = await hass.config_entries.subentries.async_configure(r["flow_id"], {**ROUTE_INPUT, "from_entity": "person.me"})
        assert r["type"] is FlowResultType.ABORT and r["reason"] == "reconfigure_successful", r
        await hass.async_block_till_done(wait_background_tasks=True)
        assert entry.subentries[sub.subentry_id].data["from"] == "person.me"
        await hass.async_block_till_done(wait_background_tasks=True)
    topics = [c.args[0] for c in mqtt_mock.async_publish.call_args_list]
    assert "transport/work" in topics and "transport/routes" in topics, topics
    payload = json.loads([c.args[1] for c in mqtt_mock.async_publish.call_args_list if c.args[0] == "transport/work"][-1])
    assert payload["journeys"][0]["line"] == "Belgrave" and payload["from"]["name"] == "me"
    states = [s for s in hass.states.async_all("sensor")]
    assert len(states) == 1, states
    assert states[0].state.startswith("2033-")

async def test_options(hass):
    entry = MockConfigEntry(domain=D, data={"devid": "1", "api_key": "k"},
                            options={"topic_prefix": "transport", "scan_interval": 120})
    entry.add_to_hass(hass)
    with patch("custom_components.ptv_transport.async_setup_entry", return_value=True):
        r = await hass.config_entries.options.async_init(entry.entry_id)
        r = await hass.config_entries.options.async_configure(r["flow_id"], {"topic_prefix": "/ptv/", "scan_interval": 300})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"topic_prefix": "ptv", "scan_interval": 300}

@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_yaml_import(hass, mqtt_mock):
    conf = {D: {"devid": "9", "api_key": "k", "scan_interval": 90, "routes": [
        {"name": "Work", "from": {"latitude": -37.8, "longitude": 144.9}, "to": "zone.home"},
        {"name": "Beach", "from": {"latitude": -37.8, "longitude": 144.9},
         "to": {"latitude": -37.86, "longitude": 144.97}, "modes": ["tram"]}]}}
    with patch("custom_components.ptv_transport.plan", return_value=[]):
        assert await async_setup_component(hass, D, conf)
        await hass.async_block_till_done(wait_background_tasks=True)
    entries = hass.config_entries.async_entries(D)
    assert len(entries) == 1
    e = entries[0]
    assert e.options == {"topic_prefix": "transport", "scan_interval": 90}
    assert sorted(s.title for s in e.subentries.values()) == ["Beach", "Work"]
    assert e.state is config_entries.ConfigEntryState.LOADED
