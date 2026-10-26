I want an agent that forecasts the weather from Google Weather. Here are my answers to your seven questions
so you do not need to ask again; I am seal, the owner.

1. Purpose: answer a weather forecast question for one location and a number of days (1 to 10) using the
   Google Weather capability pack and return the forecast as structured JSON. Name it `google-weather-forecaster`,
   domain `weather`, version 0.1.0, license Apache-2.0, owner seal.
2. Target: Atlas. Because the pack needs network access, use the tier and target the spec-author rules require.
   Routing role `google-weather-forecaster`, tags `weather`, `forecast`.
3. Input `request` (prompt_json): object with `location` (string) and `days` (integer 1..10), no other fields.
   Output `forecast` (assistant_json): object with `location`, `days` and a `forecast` array of
   `{date, summary, temperature_c: {min, max}}`.
4. Capability: pack `google-weather`, tool `google-weather__forecast`. If that pack does not exist yet, do not
   invent its server or tools; declare it as I named it and tell me plainly what the build report says.
5. Permissions: shell none, network allowlist (Google Weather API only), the tier you must use for that;
   env name `GOOGLE_WEATHER_API_KEY` (the value is mine, never ask for it).
6. Refuse when the request is missing or malformed, when the location cannot be resolved or days exceed ten,
   and when the request is not a weather forecast. Refusal response: a JSON refusal with the reason.
7. Model pinned `gpt-5.4`. Golden cases: normal (Bangkok, 3 days), missing input (empty envelope),
   refusal (Bangkok, 30 days), malformed (an extra field).

I confirm the permissions, env and capabilities blocks exactly as described above; record `confirmed_by` as `seal`
for all three. Write `work/google-weather-forecaster/spec.yaml`, run build.py with
`--out out/google-weather-forecaster`, and report the build.py summary lines verbatim.
