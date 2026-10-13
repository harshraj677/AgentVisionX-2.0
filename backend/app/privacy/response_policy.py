"""Policy for scanning what the AI says back. Deliberately separate from the input policy (app/privacy/policy.py):
a model reply naming a person or quoting an example e-mail is normal, whereas a reply containing a live-looking API
key is not, so the sensible defaults differ.
"""
from typing import Literal, get_args

ResponseAction = Literal["ignore", "allow", "warn", "redact", "block"]
RESPONSE_ACTIONS: tuple[str, ...] = get_args(ResponseAction)
ResponseMode = Literal["policy", "allow", "warn", "redact", "block"]

# ignore: not reported, no handling (noisy in prose).   allow: reported, returned unchanged.
# warn: returned unchanged with a warning in metadata.  redact: spans replaced by [CATEGORY].  block: reply withheld.
DEFAULT_RESPONSE_POLICY: dict[str, str] = {
    # Recognisable secrets and identifiers must never be returned unmodified by default.
    "API_KEY": "redact",
    "PRIVATE_KEY": "redact",
    "PASSWORD_OR_SECRET": "redact",
    "CREDIT_CARD": "redact",
    "US_SSN": "redact",
    "US_PASSPORT": "redact",
    "US_ITIN": "redact",
    "IN_AADHAAR": "redact",
    "US_BANK_NUMBER": "redact",
    # Common in examples and documentation, so flag rather than alter.
    "EMAIL_ADDRESS": "warn",
    "PHONE_NUMBER": "warn",
    "IBAN_CODE": "warn",
    "IN_PAN": "warn",
    "MEDICAL_LICENSE": "warn",
    # Too noisy in ordinary prose (names, example IPs): not reported unless you list them in RESPONSE_PRIVACY_POLICY.
    "PERSON": "ignore",
    "IP_ADDRESS": "ignore",
    "CRYPTO": "ignore",
    "US_DRIVER_LICENSE": "ignore",
}


class ResponsePolicy:
    """Decides, per detected category, what happens to a reply.

    Precedence: explicit RESPONSE_PRIVACY_POLICY entry  >  uniform RESPONSE_PRIVACY_MODE (when not "policy")  >
    built-in table  >  RESPONSE_PRIVACY_DEFAULT_ACTION. Categories the built-in table marks "ignore" stay ignored
    under a uniform mode unless listed explicitly.
    """

    def __init__(self, mode: str = "policy", overrides: dict[str, str] | None = None, default_action: str = "warn"):
        if mode != "policy" and mode not in RESPONSE_ACTIONS:
            raise ValueError(f"Invalid response privacy mode: {mode}")
        if default_action not in RESPONSE_ACTIONS:
            raise ValueError(f"Invalid default action: {default_action}")
        self._overrides: dict[str, str] = {}
        for category, action in (overrides or {}).items():
            if action not in RESPONSE_ACTIONS:
                raise ValueError(f"Invalid action '{action}' for category '{category}'")
            self._overrides[category.upper()] = action
        self.mode = mode
        self._default = default_action

    def action_for(self, category: str) -> str:
        if category in self._overrides:
            return self._overrides[category]
        base = DEFAULT_RESPONSE_POLICY.get(category, self._default)
        if self.mode != "policy" and base != "ignore":
            return self.mode
        return base
