"""Plain-language display labels shared by every web template.

The web workspace serves non-technical leads, so enum values never reach the
screen raw: the board, ticket modal, lists, and history all read from here.
"""

TYPE_LABELS = {
    "STORY": "Initiative",
    "BUG": "Issue",
    "DEMO_REQUEST": "Review request",
    "TASK": "Task",
}

STATUS_LABELS = {
    "BACKLOG": "Not planned",
    "SELECTED": "Ready",
    "IN_PROGRESS": "In progress",
    "DONE": "Done",
}
