"""Safety rails for every mutating Home Assistant action.

Three independent layers, in the order they run:

  1. `HA_WRITE_ENABLED`  -- a process-wide kill switch.
  2. The deny-list       -- domains an agent may never touch, enforced in code.
  3. Confirmation        -- a human approves a rendered diff before it is applied.

The deny-list is deliberately not configuration. Putting it in `agent.json` would
make "may this agent unlock the front door?" a question answered by a file that an
agent can read and, in a system with a filesystem tool, potentially write. As a
frozenset in code, changing it requires a code change, a test change and a commit:
reviewable and attributable. An `agent.json` grant cannot widen it.

Two bypasses shaped the design, both found while writing the policy rather than
after:

  * A service call is not the only way to reach a blocked domain. Writing an
    automation whose action is `lock.unlock` reaches it too, so automation and
    script payloads are scanned recursively before they are accepted.
  * `script.turn_on` looks like an ordinary reversible call, but a script can
    contain anything. Scripts and `automation.trigger` therefore require
    confirmation rather than auto-applying, because their effects are opaque at
    call time.
"""
import hashlib
import json
import re
import secrets
import time
from typing import Any, Dict, Optional, Tuple

from core.integrations.homeassistant.client import write_enabled

# Domains an agent may never actuate. Physical-security and water/heat risks:
# the cost of a false positive is an agent saying "I can't do that", and the cost
# of a false negative is an unlocked door.
BLOCKED_DOMAINS = frozenset({
    "lock",
    "alarm_control_panel",
    "cover",
    "garage_door",
    "valve",
    "water_heater",
})

# Setpoints outside this range are refused even for an allowed climate entity:
# a plausible-looking unit error (18 -> 81) should not be able to cook the house.
CLIMATE_SAFE_BAND_C = (12.0, 28.0)

# Reversible, low-consequence, and cheap to undo by asking again.
AUTO_APPLY_DOMAINS = frozenset({
    "light", "switch", "fan", "media_player", "scene", "notify",
    "input_boolean", "input_number", "input_select", "input_datetime",
    "climate", "humidifier", "todo", "timer", "button", "select", "number",
})

# Reversible in principle, opaque in practice: the effect is whatever the script
# happens to contain, which may include a blocked domain.
OPAQUE_DOMAINS = frozenset({"script", "automation"})

# Anything that changes stored configuration always needs a human.
CONFIG_WRITE_ACTIONS = frozenset({
    "upsert_automation", "upsert_script", "upsert_scene", "upsert_helper",
    "delete_automation", "delete_script", "delete_scene", "reload",
})

CONFIRMATION_TTL_SECONDS = 300

_ENTITY_REF = re.compile(r"^([a-z_]+)\.[A-Za-z0-9_]+$")


class GuardRejection(Exception):
    """A mutating action was refused. The message is shown to the agent."""


class ConfirmationRequired(Exception):
    """A mutating action needs human approval first.

    Carries the token the agent must echo back, and the payload that token is
    bound to, so the caller can render a diff alongside it.
    """

    def __init__(self, token: str, action: str, payload: Dict[str, Any]):
        self.token = token
        self.action = action
        self.payload = payload
        super().__init__(f"Confirmation required for '{action}'.")


# -- Deny-list ---------------------------------------------------------------


def domain_of(value: str) -> str:
    """'light.porch' -> 'light'; 'light' -> 'light'."""
    return value.split(".", 1)[0] if "." in value else value


def check_domain(value: str) -> None:
    """Refuses a blocked domain. `value` may be an entity_id or a bare domain."""
    domain = domain_of(str(value or ""))
    if domain in BLOCKED_DOMAINS:
        raise GuardRejection(
            f"'{domain}' is on the permanent deny-list (physical safety) and cannot be "
            f"actuated by an agent, with or without confirmation. This is enforced in "
            f"code, not configuration, so it cannot be granted in agent.json."
        )


def check_climate_setpoint(service_data: Optional[Dict[str, Any]]) -> None:
    """Refuses climate setpoints outside the safe band."""
    if not service_data:
        return
    low, high = CLIMATE_SAFE_BAND_C
    for key in ("temperature", "target_temp_high", "target_temp_low"):
        if key not in service_data:
            continue
        try:
            value = float(service_data[key])
        except (TypeError, ValueError):
            raise GuardRejection(f"climate '{key}' must be a number, got {service_data[key]!r}.")
        if not (low <= value <= high):
            raise GuardRejection(
                f"climate '{key}' of {value}degC is outside the safe band "
                f"{low}-{high}degC and was refused."
            )


def scan_payload_for_blocked(payload: Any, _path: str = "") -> None:
    """Walks a config payload refusing any reference to a blocked domain.

    Without this, the deny-list is trivially bypassed: an agent forbidden from
    calling `lock.unlock` could instead write an automation that calls it, and
    Home Assistant would happily run it on a schedule.

    Matches entity references ('lock.front_door') and service references
    ('lock.unlock'), which share a syntax -- so this is deliberately broad. A
    false positive here is an explainable refusal; a miss is an unlocked door.
    """
    if isinstance(payload, dict):
        for key, value in payload.items():
            scan_payload_for_blocked(value, f"{_path}.{key}" if _path else str(key))
        return
    if isinstance(payload, (list, tuple)):
        for index, value in enumerate(payload):
            scan_payload_for_blocked(value, f"{_path}[{index}]")
        return
    if isinstance(payload, str):
        match = _ENTITY_REF.match(payload.strip())
        if match and match.group(1) in BLOCKED_DOMAINS:
            raise GuardRejection(
                f"This payload references '{payload}' at '{_path or 'root'}', which is in "
                f"the blocked '{match.group(1)}' domain. Writing configuration that "
                f"actuates a blocked domain is refused for the same reason calling it "
                f"directly is."
            )


# -- Autonomy policy ---------------------------------------------------------


def confirmation_required(action: str, target: Optional[str] = None) -> bool:
    """Whether `action` needs a human before it is applied.

    Config writes always do. Service calls depend on the target domain:
    reversible domains auto-apply, opaque ones (script, automation) do not, and
    anything unrecognised defaults to requiring confirmation -- a new domain
    should arrive as "ask first", not as "assume it is safe".
    """
    if action in CONFIG_WRITE_ACTIONS:
        return True
    if action == "call_service":
        domain = domain_of(str(target or ""))
        if domain in OPAQUE_DOMAINS:
            return True
        return domain not in AUTO_APPLY_DOMAINS
    return True


# -- Confirmation tokens -----------------------------------------------------
#
# A token is a random handle stored against a hash of the exact payload it was
# issued for. Tamper-evidence comes from comparing that hash on redemption: an
# agent that "improves" the automation between proposing and confirming
# invalidates its own token rather than slipping the change past the human who
# approved something else.
#
# The store is in-process. A restart drops pending confirmations, which fails
# safe -- the agent is told to re-propose. That is a deliberate trade against
# single-use enforcement, which a stateless signed token cannot provide.

_pending: Dict[str, Tuple[float, str, str, Optional[str]]] = {}


def _fingerprint(action: str, payload: Dict[str, Any]) -> str:
    canonical = json.dumps({"action": action, "payload": payload}, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _purge_expired(now: Optional[float] = None) -> None:
    now = now if now is not None else time.time()
    for token in [t for t, (expires, *_rest) in _pending.items() if expires < now]:
        _pending.pop(token, None)


def issue_confirmation(action: str, payload: Dict[str, Any], agent_id: Optional[str] = None) -> str:
    """Registers a pending confirmation and returns the token to echo back."""
    _purge_expired()
    token = secrets.token_urlsafe(12)
    _pending[token] = (
        time.time() + CONFIRMATION_TTL_SECONDS,
        action,
        _fingerprint(action, payload),
        agent_id,
    )
    return token


def redeem_confirmation(
    token: str,
    action: str,
    payload: Dict[str, Any],
    agent_id: Optional[str] = None,
) -> None:
    """Consumes a token, or raises GuardRejection explaining why it is not valid.

    Single-use: the token is removed whether or not the checks below pass, so a
    rejected confirmation cannot be retried by guessing at the payload.
    """
    _purge_expired()
    entry = _pending.pop(token, None)

    if entry is None:
        raise GuardRejection(
            "That confirmation token is unknown or has expired "
            f"(tokens last {CONFIRMATION_TTL_SECONDS // 60} minutes, and each works once). "
            "Propose the change again to get a fresh one."
        )

    _expires, issued_action, fingerprint, issued_agent = entry

    if issued_action != action:
        raise GuardRejection(
            f"That token was issued for '{issued_action}', not '{action}'."
        )
    if issued_agent is not None and agent_id is not None and issued_agent != agent_id:
        raise GuardRejection("That token was issued to a different agent.")
    if fingerprint != _fingerprint(action, payload):
        raise GuardRejection(
            "The payload changed after the confirmation was issued, so the token is "
            "no longer valid. The human approved a specific change; re-propose and "
            "have the new version approved."
        )


def reset_pending() -> None:
    """Clears the pending store. For tests."""
    _pending.clear()


# -- The chain ---------------------------------------------------------------


def authorise(
    action: str,
    target: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
    confirm_token: Optional[str] = None,
    agent_id: Optional[str] = None,
    blanket_grant: bool = False,
) -> None:
    """Runs every guard for one mutating action.

    Returns None when the action may proceed. Raises GuardRejection when it may
    not, or ConfirmationRequired when a human has to approve it first.

    Ordering is deliberate: the cheap, absolute refusals come first, so a blocked
    domain is refused without ever minting a token or prompting a human for
    something that was never going to be allowed.
    """
    payload = payload or {}

    # 1. Kill switch.
    if not write_enabled():
        raise GuardRejection(
            "Writing to Home Assistant is disabled. Set HA_WRITE_ENABLED=true in .env "
            "to enable mutating actions."
        )

    # 2. Blanket grants are refused for writes. `{}` in agent.json means
    #    allow-all in the permission loader, which is harmless for a read tool and
    #    a loaded gun for this one.
    if blanket_grant:
        raise GuardRejection(
            "home_assistant requires explicit per-action grants for writes; a blanket "
            "'{}' grant is refused. List the actions and entity selectors in agent.json."
        )

    # 3. Deny-list, on both the direct target and anything inside the payload.
    if target:
        check_domain(target)
    scan_payload_for_blocked(payload)

    if action == "call_service" and domain_of(str(target or "")) == "climate":
        check_climate_setpoint(payload.get("service_data") or payload.get("data"))

    # 4. Confirmation.
    if confirmation_required(action, target):
        if not confirm_token:
            raise ConfirmationRequired(
                token=issue_confirmation(action, payload, agent_id),
                action=action,
                payload=payload,
            )
        redeem_confirmation(confirm_token, action, payload, agent_id)
