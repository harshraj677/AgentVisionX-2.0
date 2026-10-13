from typing import Literal, get_args

Action = Literal["allow", "warn", "redact", "block"]
ACTIONS: tuple[str, ...] = get_args(Action)

# Higher = stricter. The decision for a prompt is its strictest finding.
SEVERITY: dict[str, int] = {"allow": 0, "warn": 1, "redact": 2, "block": 3}

DEFAULT_POLICY: dict[str, Action] = {
    # Secrets and government/financial identifiers: never leave the backend.
    "API_KEY": "block",
    "PRIVATE_KEY": "block",
    "PASSWORD_OR_SECRET": "block",
    "CREDIT_CARD": "block",
    "US_SSN": "block",
    "US_PASSPORT": "block",
    "US_ITIN": "block",
    "IN_AADHAAR": "block",
    "US_BANK_NUMBER": "block",
    # Contact and account details: must be redacted (or the prompt edited) before sending.
    "EMAIL_ADDRESS": "redact",
    "PHONE_NUMBER": "redact",
    "IBAN_CODE": "redact",
    "IN_PAN": "redact",
    "MEDICAL_LICENSE": "redact",
    # Context-dependent: the user decides.
    "PERSON": "warn",
    "IP_ADDRESS": "warn",
    "CRYPTO": "warn",
    "US_DRIVER_LICENSE": "warn",
}


class PolicyEngine:
    """Maps a detected category to allow / warn / redact / block.

    - allow:  ignored.
    - warn:   the user must confirm before sending.
    - redact: the user must apply redaction (or edit the prompt) before sending.
    - block:  cannot be sent as-is; the user must redact or edit, and the result is re-scanned.
    """

    def __init__(self, overrides: dict[str, str] | None = None, default_action: str = "warn"):
        if default_action not in ACTIONS:
            raise ValueError(f"Invalid default action: {default_action}")
        self._policy: dict[str, str] = dict(DEFAULT_POLICY)
        for category, action in (overrides or {}).items():
            if action not in ACTIONS:
                raise ValueError(f"Invalid action '{action}' for category '{category}'")
            self._policy[category.upper()] = action
        self._default = default_action

    def action_for(self, category: str) -> Action:
        return self._policy.get(category, self._default)  # type: ignore[return-value]

    @staticmethod
    def strictest(actions: list[str]) -> Action:
        return max(actions, key=lambda a: SEVERITY[a], default="allow")  # type: ignore[return-value]
