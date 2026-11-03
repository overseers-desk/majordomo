---
name: weather
description: Someone asks about the weather, rain, temperature, wind or a coming storm, today or in the next days.
---

Get the forecast from Open-Meteo, which is public and needs no key. Use WebFetch.

Place: use the location from your system prompt unless the person names another. For a named place, geocode first:
`https://geocoding-api.open-meteo.com/v1/search?name=<place>&count=1`
It returns latitude and longitude. For the default location, use its coordinates directly, or geocode its name.

Forecast:
`https://api.open-meteo.com/v1/forecast?latitude=..&longitude=..&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code,wind_speed_10m_max&timezone=auto`
Add hourly fields (for example `hourly=temperature_2m,precipitation_probability,wind_speed_10m`) when the question is about particular hours, such as "will it rain this afternoon". `weather_code` is the WMO code; turn it into plain words.

Answer briefly with the figures that answer the question, for the day or days asked, in the person's language. A question about rain gets the chance of rain, not the whole forecast. Name the place if it was not the default. If the geocoder finds nothing, say so and ask which place is meant.

Send the answer with `reply`.
