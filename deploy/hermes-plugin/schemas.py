"""Tool schemas for the hermes-overseer bridge plugin."""

OVERSEER_STATUS_SCHEMA = {
    "name": "overseer_status",
    "description": (
        "Check the current status of the overseer monitoring system. "
        "Returns poll state, uptime, and any recent alerts. "
        "Use this before risky operations to verify the overseer is healthy."
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}
