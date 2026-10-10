# PTV Transport for Home Assistant

A custom integration for Home Assistant. It does the work of querying the
[PTV Timetable API](https://timetableapi.ptv.vic.gov.au/swagger/ui/index) and
builds the departure data for each route you define. Each route is published
as retained JSON to its own MQTT topic, `transport/<route>`, and gets a sensor
in Home Assistant.

Your ESP board (or anything else) only has to subscribe to a topic. It doesn't
need to make secure web requests, sign API calls or parse large responses.

## How a route is planned

1. Find the nearest train, tram and bus stops to the start and to the end.
2. Pair the stops that share a line, keeping the pair with the least walking.
3. Get the next departures on that line, skipping any you couldn't walk to in
   time.
4. Load each service's stopping pattern and keep it only if it stops at the
   destination after your stop. This drops express services that skip the
   destination and services going the wrong way.
5. Publish up to `max_journeys` options, sorted by when you'd arrive at the
   destination, including the walk from the stop.

It only plans trips on one train, tram or bus, with no changes.

## Install

You need the MQTT integration set up first, for example with the Mosquitto
broker add-on. You also need a PTV Timetable API user ID and key, which PTV
issues on request (see the
[API documentation](https://timetableapi.ptv.vic.gov.au/swagger/ui/index)).

### With HACS (recommended)

[![Open your Home Assistant instance and add this repository to HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Politylos&repository=melbourne_ptv_homeassistant&category=integration)

1. In Home Assistant, open **HACS**, then **⋮ → Custom repositories**.
2. Add `https://github.com/Politylos/melbourne_ptv_homeassistant` with type **Integration**.
3. Search for **PTV Transport**, open it, and select **Download**.
4. Restart Home Assistant.

### Manually

Copy `custom_components/ptv_transport` into your Home Assistant
`config/custom_components/` folder, then restart Home Assistant.

## Set up

1. Go to **Settings → Devices & services → Add integration** and choose
   **PTV Transport**.
2. Enter your PTV **user ID (devid)** and **API key**. They're checked against
   the API before they're saved.
3. On the PTV Transport integration page, click **Add route** for each trip:
   - **Route name**: also used for the MQTT topic, so `Work` publishes to
     `transport/work`.
   - **Start** and **Destination**: pick a point on the map, or choose an
     entity to follow instead. A `person.*` or `device_tracker.*` uses your
     phone's live GPS through the Companion app; a `zone.*` uses the zone.
   - **Transport modes**: train, tram, bus, V/Line and night bus.
   - **Walking distances** (collapsed): how far you'll walk to each mode.
     The defaults are train 1500 m, tram 800 m and bus 600 m.
   - **Advanced** (collapsed): only list the stops between boarding and
     getting off, how many journeys to keep (1 to 20), and how many nearby
     stops to check per mode.

You can add as many routes as you like. To edit or delete a route, use the
⋮ menu next to it on the integration page.

**Configure** on the integration page sets the MQTT topic prefix (default
`transport`) and how often to refresh (default 120 seconds). To change your
user ID or key, choose **Reconfigure**.

### Moving from configuration.yaml

Version 1.0 was configured in YAML. If a `ptv_transport:` block is still in
`configuration.yaml`, it's imported into the setup above the first time 1.1
starts, with each route becoming a route entry. After that the YAML is
ignored, and you can delete the block.

## What gets published

| Topic | Payload |
|---|---|
| `transport/routes` | JSON list of the route slugs, e.g. `["work","beach"]` (retained) |
| `transport/<route>` | The route's journeys, as below (retained, updated every refresh) |

```json
{
  "route": "work",
  "name": "work",
  "updated": "2026-10-10T16:41:00+11:00",
  "updated_ts": 1791610860,
  "from": {"lat": -37.824, "lon": 144.99, "name": "Me"},
  "to": {"lat": -37.8255, "lon": 145.0995, "name": "Surrey Hills"},
  "count": 2,
  "journeys": [
    {
      "mode": "train", "route_type": 0, "route_id": 2,
      "line": "Belgrave", "number": "", "run_ref": "951853",
      "towards": "Belgrave", "express_stops": 0,
      "origin": {"id": 1162, "name": "Richmond", "walk_m": 16},
      "destination": {"id": 1229, "name": "Union", "walk_m": 274},
      "platform": "9",
      "scheduled_ts": 1791611100, "estimated_ts": 1791611160,
      "departure_ts": 1791611160, "departure": "4:46pm",
      "minutes": 5, "delay_min": 1,
      "arrival_ts": 1791612300, "arrival": "5:05pm",
      "stops": [
        {"id": 1162, "name": "Richmond", "ts": 1791611160, "time": "4:46pm", "platform": "9"},
        {"id": 1030, "name": "Burnley", "ts": 1791611340, "time": "4:49pm", "platform": "1"}
      ]
    }
  ]
}
```

- `journeys` is sorted best first.
- Every `*_ts` value is a UTC epoch timestamp, in seconds.
- `departure`, `arrival` and `time` are local times, ready to display.
- `minutes` is worked out at the moment of publishing. A display should count
  down from `departure_ts` using its own clock.
- When a service is running late but its stopping pattern has no live times,
  the delay at your stop is added to every later stop.

## Sensor

Each route gets its own device with a sensor,
`sensor.ptv_<route>_next_departure`, whose value is the departure time of the
best journey. Its attributes list every journey (without the stop lists) and
the route's MQTT topic.

## Refresh now

Call the `ptv_transport.refresh` action to re-plan straight away. Add
`route: work` to refresh only that route; leave it out to refresh them all.

## API usage

Each refresh makes about 10 to 25 API calls per route. Nearby stop lookups are
cached for a day, so after the first refresh it's mostly departure and
stopping-pattern calls. With many routes, raise the refresh interval under **Configure**.

## Development

The tests drive the setup screens, route publishing and YAML import inside a
real Home Assistant:

```bash
pip install -r requirements_test.txt
pytest
```

GitHub Actions runs them, hassfest and HACS validation on every push.
