from __future__ import annotations


def neo_aspect_settings_sections() -> list[dict[str, object]]:
    return [
        {
            "id": "body",
            "title": "Body",
            "items": [
                "Live2D model",
                "expressions",
                "motions",
                "chat bubble",
                "voice",
                "desktop window",
            ],
        },
        {
            "id": "brain",
            "title": "Brain",
            "items": [
                "model endpoint",
                "model name",
                "API key storage",
                "persona profile",
                "response style",
                "single-step decision tuning",
            ],
        },
        {
            "id": "human_ops",
            "title": "Human Ops",
            "items": [
                "screen observation",
                "accessibility permission",
                "click preview red dot",
                "action review defaults",
                "allowed action types",
            ],
        },
        {
            "id": "memory",
            "title": "Memory",
            "items": [
                "conversation saving",
                "long-term memory",
                "user preferences",
                "relationship memory",
                "review queue",
            ],
        },
        {
            "id": "skills",
            "title": "Skills",
            "items": [
                "recipe list",
                "proposal queue",
                "enable or disable recipes",
                "edit recipes",
                "review history",
            ],
        },
        {
            "id": "diagnostics",
            "title": "Diagnostics",
            "items": [
                "local health",
                "last observation",
                "last approved action",
                "last failed action",
            ],
        },
    ]
