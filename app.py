import base64
import json
import os
import time
import logging
import requests
import urllib3
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

REMNA_BASE_URL = os.environ["REMNA_BASE_URL"].rstrip("/")
REMNA_API_URL = f"{REMNA_BASE_URL}/subscription-settings"
REMNA_TOKEN = os.environ["REMNA_TOKEN"]
GITHUB_RAW_URL = os.environ.get(
    "GITHUB_RAW_URL",
    "https://raw.githubusercontent.com/hydraponique/roscomvpn-happ-routing/refs/heads/main/HAPP/DEFAULT.DEEPLINK",
)
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "300"))  # seconds
CRON_SCHEDULE = os.environ.get("CRON_SCHEDULE", "").strip()
SSL_VERIFY = REMNA_BASE_URL.startswith("https://")

REMNA_HEADERS = {
    "Accept": "application/json",
    "Authorization": f"Bearer {REMNA_TOKEN}",
}

if not SSL_VERIFY:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    REMNA_HEADERS["X-Forwarded-Proto"] = "https"
    REMNA_HEADERS["X-Forwarded-For"] = "127.0.0.1"

DEEPLINK_PREFIX = "happ://routing/onadd/"

# Поля happRouting со списками правил; суффикс env: ROUTING_EXTRA_<SUFFIX> / SQUAD_N_EXTRA_<SUFFIX>
ROUTING_LIST_FIELDS = {
    "DIRECT_SITES": "DirectSites",
    "DIRECT_IP": "DirectIp",
    "PROXY_SITES": "ProxySites",
    "PROXY_IP": "ProxyIp",
    "BLOCK_SITES": "BlockSites",
    "BLOCK_IP": "BlockIp",
}


def parse_extra_list(value: str) -> list[str]:
    if not value or not value.strip():
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def load_routing_extras(prefix: str = "ROUTING") -> dict[str, list[str]]:
    extras = {}
    for env_suffix, json_key in ROUTING_LIST_FIELDS.items():
        items = parse_extra_list(os.environ.get(f"{prefix}_EXTRA_{env_suffix}", ""))
        if items:
            extras[json_key] = items
    return extras


def load_routing_overrides(prefix: str = "ROUTING") -> dict[str, str]:
    """Скалярные поля happRouting, заменяемые целиком; суффикс env: <PREFIX>_NAME и т.п."""
    overrides = {}
    name = os.environ.get(f"{prefix}_NAME", "").strip()
    if name:
        overrides["Name"] = name
    return overrides


def parse_deeplink(deeplink: str) -> tuple[str, dict]:
    deeplink = deeplink.strip()
    if not deeplink:
        raise ValueError("Empty happRouting deeplink")

    prefix = DEEPLINK_PREFIX
    payload_b64 = deeplink
    for known_prefix in (
        "happ://routing/onadd/",
        "happ://routing/add/",
        "happ://routing/import/",
    ):
        if deeplink.startswith(known_prefix):
            prefix = known_prefix
            payload_b64 = deeplink[len(known_prefix):]
            break

    if payload_b64 == deeplink and not deeplink.startswith("happ://"):
        payload_b64 = deeplink

    padding = "=" * (-len(payload_b64) % 4)
    payload = base64.b64decode(payload_b64 + padding)
    return prefix, json.loads(payload)


def routing_configs_equal(left: str, right: str) -> bool:
    if left.strip() == right.strip():
        return True
    try:
        return parse_deeplink(left)[1] == parse_deeplink(right)[1]
    except (ValueError, json.JSONDecodeError):
        return False


def build_deeplink(prefix: str, routing: dict) -> str:
    encoded = base64.b64encode(
        json.dumps(routing, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return f"{prefix}{encoded}"


def merge_routing_lists(base: list, extra: list) -> list:
    seen = set(base)
    merged = list(base)
    for item in extra:
        if item not in seen:
            merged.append(item)
            seen.add(item)
    return merged


def apply_routing_extras(
    deeplink: str,
    extras: dict[str, list[str]],
    overrides: dict[str, str] | None = None,
) -> str:
    overrides = overrides or {}
    if not extras and not overrides:
        return deeplink.strip()

    prefix, routing = parse_deeplink(deeplink)
    changed = False
    for json_key, value in overrides.items():
        if routing.get(json_key) != value:
            routing[json_key] = value
            changed = True
            log.info("Routing field %s overridden: %s", json_key, value)
    for json_key, items in extras.items():
        current = routing.get(json_key)
        if current is None:
            routing[json_key] = list(items)
            changed = True
            continue
        if not isinstance(current, list):
            log.warning("Routing field %s is not a list, skipping extras", json_key)
            continue
        merged = merge_routing_lists(current, items)
        added = [item for item in items if item not in current]
        if merged != current:
            routing[json_key] = merged
            changed = True
            log.info("Routing extras added to %s: %s", json_key, ", ".join(added))

    if not changed:
        return deeplink.strip()
    return build_deeplink(prefix, routing)


def load_squad_configs() -> list:
    squads = []
    i = 1
    while True:
        uuid = os.environ.get(f"SQUAD_{i}_UUID", "").strip()
        url = os.environ.get(f"SQUAD_{i}_URL", "").strip()
        if not uuid or not url:
            break
        squads.append({
            "uuid": uuid,
            "url": url,
            "current_routing": None,
            "current_settings": {},
            "routing_extras": load_routing_extras(f"SQUAD_{i}"),
            "routing_overrides": load_routing_overrides(f"SQUAD_{i}"),
        })
        i += 1
    return squads


def get_remna_settings() -> dict:
    resp = requests.get(
        REMNA_API_URL,
        headers=REMNA_HEADERS,
        timeout=30,
        verify=SSL_VERIFY,
    )
    resp.raise_for_status()
    return resp.json()


def patch_remna_settings(payload: dict) -> dict:
    resp = requests.patch(
        REMNA_API_URL,
        headers={**REMNA_HEADERS, "Content-Type": "application/json"},
        json=payload,
        timeout=30,
        verify=SSL_VERIFY,
    )
    resp.raise_for_status()
    return resp.json()


def get_external_squad(squad_uuid: str) -> dict:
    resp = requests.get(
        f"{REMNA_BASE_URL}/external-squads/{squad_uuid}",
        headers=REMNA_HEADERS,
        timeout=30,
        verify=SSL_VERIFY,
    )
    resp.raise_for_status()
    return resp.json()


def patch_external_squad(squad_uuid: str, routing: str, current_settings: dict) -> dict:
    merged = {**current_settings, "happRouting": routing}
    resp = requests.patch(
        f"{REMNA_BASE_URL}/external-squads",
        headers={**REMNA_HEADERS, "Content-Type": "application/json"},
        json={"uuid": squad_uuid, "subscriptionSettings": merged},
        timeout=30,
        verify=SSL_VERIFY,
    )
    resp.raise_for_status()
    return resp.json()


def get_github_deeplink(url: str) -> str:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text.strip()


def run_cycle(settings_uuid: str, state: dict, squads: list) -> None:
    """Одна итерация проверки: сравнить роутинг на GitHub с текущим и обновить при изменении."""
    try:
        github_deeplink = get_github_deeplink(GITHUB_RAW_URL)
        log.info("Fetched GitHub deeplink (%d chars)", len(github_deeplink))
        target_deeplink = apply_routing_extras(
            github_deeplink, state["routing_extras"], state["routing_overrides"]
        )
        if target_deeplink != github_deeplink:
            log.info("Applied routing extras to subscription settings deeplink")

        if not routing_configs_equal(target_deeplink, state["current_routing"]):
            log.info("Routing changed! Updating subscription settings...")
            result = patch_remna_settings({
                "uuid": settings_uuid,
                "happRouting": target_deeplink,
            })
            state["current_routing"] = target_deeplink
            log.info("Successfully updated happRouting in subscription settings")
            log.debug("Patch response: %s", result)
        else:
            log.info("No changes detected in subscription settings")

    except Exception:
        log.exception("Error during subscription settings check cycle")

    for squad in squads:
        try:
            deeplink = get_github_deeplink(squad["url"])
            target_deeplink = apply_routing_extras(
                deeplink, squad["routing_extras"], squad["routing_overrides"]
            )
            if target_deeplink != deeplink:
                log.info("Applied routing extras for squad %s", squad["uuid"])
            if not routing_configs_equal(target_deeplink, squad["current_routing"] or ""):
                log.info("Routing changed for squad %s! Updating...", squad["uuid"])
                patch_external_squad(squad["uuid"], target_deeplink, squad["current_settings"])
                squad["current_settings"] = {**squad["current_settings"], "happRouting": target_deeplink}
                squad["current_routing"] = target_deeplink
                log.info("Successfully updated happRouting for squad %s", squad["uuid"])
            else:
                log.info("No changes detected for squad %s", squad["uuid"])
        except Exception:
            log.exception("Error updating squad %s", squad["uuid"])


def main():
    log.info("Starting routing update monitor")
    log.info("Remna API: %s", REMNA_API_URL)
    log.info("GitHub URL: %s", GITHUB_RAW_URL)
    if CRON_SCHEDULE:
        log.info("Mode: cron schedule '%s' (container local time)", CRON_SCHEDULE)
    else:
        log.info("Mode: interval polling every %ds", CHECK_INTERVAL)

    # Fetch current settings on startup
    settings = get_remna_settings()
    data = settings.get("response", settings)
    settings_uuid = data["uuid"]
    routing_extras = load_routing_extras()
    routing_overrides = load_routing_overrides()
    state = {
        "current_routing": (data.get("happRouting", "") or "").strip(),
        "routing_extras": routing_extras,
        "routing_overrides": routing_overrides,
    }
    log.info("Settings UUID: %s", settings_uuid)
    log.info("Current happRouting loaded (%d chars)", len(state["current_routing"]))
    if routing_extras:
        for field, items in sorted(routing_extras.items()):
            log.info("Routing extras for %s: %s", field, ", ".join(items))
    else:
        log.info("No routing extras configured (ROUTING_EXTRA_*)")
    for field, value in sorted(routing_overrides.items()):
        log.info("Routing override for %s: %s", field, value)

    squads = load_squad_configs()
    log.info("Loaded %d external squad(s)", len(squads))
    for squad in squads:
        try:
            data = get_external_squad(squad["uuid"])
            squad_data = data.get("response", data)
            squad["current_settings"] = squad_data.get("subscriptionSettings", {}) or {}
            squad["current_routing"] = (squad["current_settings"].get("happRouting", "") or "").strip()
            log.info("Squad %s current happRouting loaded (%d chars)", squad["uuid"], len(squad["current_routing"]))
            if squad["routing_extras"]:
                log.info(
                    "Squad %s routing extras configured: %s",
                    squad["uuid"],
                    ", ".join(sorted(squad["routing_extras"])),
                )
        except Exception:
            log.exception("Failed to fetch initial routing for squad %s, will update on first cycle", squad["uuid"])

    if CRON_SCHEDULE:
        try:
            from croniter import croniter
        except ImportError:
            raise SystemExit("CRON_SCHEDULE is set, but the 'croniter' package is not installed")
        if not croniter.is_valid(CRON_SCHEDULE):
            raise SystemExit(f"Invalid CRON_SCHEDULE expression: {CRON_SCHEDULE!r}")

        # Синхронизируемся один раз при запуске, чтобы не ждать первого срабатывания
        # расписания (например, после рестарта или деплоя), затем работаем по cron.
        run_cycle(settings_uuid, state, squads)
        schedule = croniter(CRON_SCHEDULE, datetime.now())
        while True:
            next_run = schedule.get_next(datetime)
            delay = (next_run - datetime.now()).total_seconds()
            if delay > 0:
                log.info(
                    "Next scheduled run at %s (in %ds)",
                    next_run.isoformat(sep=" ", timespec="seconds"),
                    int(delay),
                )
                time.sleep(delay)
            run_cycle(settings_uuid, state, squads)
    else:
        while True:
            run_cycle(settings_uuid, state, squads)
            time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
