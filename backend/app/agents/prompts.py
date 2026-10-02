"""Shared bits for the AI's instructions.

Until Phase 8c this file held the agent's full system prompt. Now the chat
is a scripted flow (D28) and the AI's only job is to interpret off-script
messages; its instructions are in interpreter.py.
"""

# How each store profile field (Phase 7b, D22) is labelled for the AI.
PROFILE_LABELS = {
    "opening_hours": "Opening hours",
    "location": "Location",
    "delivery_info": "Delivery areas and fees",
    "pickup_instructions": "Pickup",
    "payment_instructions": "How to pay",
    "return_policy": "Returns and exchanges",
}
