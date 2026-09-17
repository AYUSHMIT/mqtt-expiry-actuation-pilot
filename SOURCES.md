# Primary sources checked — 16 September 2026

These support protocol/implementation statements, not the novelty or success of the proposed experiment. The stack's exact runtime is captured separately; web documentation can change.

- **S1 — OASIS MQTT 5.0, §3.3.2.3.3, Message Expiry Interval.**
  https://docs.oasis-open.org/mqtt/mqtt/v5.0/os/mqtt-v5.0-os.html
  Delivery-copy expiry and forwarding of remaining expiry time. Distinguish this from our chosen execution-time application requirement.
- **S2 — Home Assistant, Automation modes.**
  https://www.home-assistant.io/docs/automation/modes/
  Native queued and parallel modes; conditions for queued runs are evaluated on triggering/admission.
- **S3 — Eclipse Mosquitto, Version 2.0.22 release.**
  https://mosquitto.org/blog/2025/07/version-2-0-22-released/
  Establishes the selected broker release, not that it is the latest available release.
- **S4 — Home Assistant September 2026 release notes, patch 2026.9.2.**
  https://www.home-assistant.io/blog/2026/09/02/release-20269/
  The 2026.9.2 patch is dated September 11.
- **S5 — Home Assistant, Input button.**
  https://www.home-assistant.io/integrations/input_button/
  Native virtual endpoint, press action, last-pressed state. Not physical-actuator feedback.
- **S6 — Home Assistant, Script syntax.**
  https://www.home-assistant.io/docs/scripts/
  Native action, condition, delay, stop, and event syntax.
- **S7 — Home Assistant developer WebSocket API.**
  https://developers.home-assistant.io/docs/api/websocket/
  Authenticated event subscription and state/context observations.
- **S8 — Home Assistant MQTT documentation and publish API announcement.**
  https://www.home-assistant.io/integrations/mqtt/
  https://developers.home-assistant.io/blog/2026/05/11/mqtt-publish-api-message-expiry-interval/
  MQTT integration configuration and publication expiry support. Outgoing publish support is not proof of incoming end-to-end actuator expiry.
- **S9 — Eclipse Paho Python client documentation.**
  https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html
  Callback version 2, MQTT 5 connection/publish properties, publish acknowledgements.

No current claims about a vendor vulnerability, publication novelty, or empirical failure are made from these sources. The earlier poster discussion's literature review remains a separate task; this bundle freezes the feasibility experiment rather than asserting research priority.
