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

### Then configure it

1. Add your credentials to `secrets.yaml`:

   ```yaml
   ptv_devid: "your-user-id"
   ptv_api_key: "your-api-key"
   ```

2. Add the configuration below to `configuration.yaml`.
3. Restart Home Assistant again.

## Configuration

```yaml
ptv_transport:
  devid: !secret ptv_devid
  api_key: !secret ptv_api_key
  topic_prefix: transport        # optional, default "transport"
  scan_interval: 120             # optional, seconds between refreshes
  routes:
    # From your phone's live GPS location (Companion app) to a fixed point
    - name: work
      from: person.me
      to:
        latitude: -37.8255
        longitude: 145.0995
        name: Surrey Hills
      modes: [train, tram, bus]  # optional, choose from: train, tram, bus, vline, night_bus
      max_walk:                  # optional, metres (defaults: train 1500, tram 800, bus 600)
        train: 1500
        tram: 800
      only_relevant_stops: true  # optional, list only the stops from boarding to getting off
      max_journeys: 5            # optional, 1 to 20

    # Fixed start, with a zone as the destination
    - name: beach
      from:
        latitude: -37.8183
        longitude: 144.9671
      to: zone.st_kilda
      modes: [tram]
```

`from` and `to` can each be:

- an entity with `latitude` and `longitude` attributes, such as `person.*`,
  `device_tracker.*` or `zone.*`
- fixed coordinates, written as `latitude` and `longitude`, with an optional `name`

You can add as many routes as you like. Each one is published to
`<topic_prefix>/<slug of name>`, so `work` goes to `transport/work`.

## What gets published

| Topic | Payload |
|---|---|
| `transport/routes` | JSON list of the route slugs, e.g. `["work","beach"]` (retained) |
| `transport/<route>` | The route's journeys, as below (retained, updated every `scan_interval`) |

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

Each route gets a sensor, `sensor.ptv_<route>`, whose value is the departure
time of the best journey. Its attributes list every journey (without the
stop lists) and the route's MQTT topic.

## Refresh now

Call the `ptv_transport.refresh` action to re-plan straight away. Add
`route: work` to refresh only that route; leave it out to refresh them all.

## API usage

Each refresh makes about 10 to 25 API calls per route. Nearby stop lookups are
cached for a day, so after the first refresh it's mostly departure and
stopping-pattern calls. With many routes, raise `scan_interval`.
