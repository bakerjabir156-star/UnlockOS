"""
activation_hijack.py — mitmproxy Activation Intercept Script
Run with: mitmdump -s activation_hijack.py -p 8080

This addon intercepts Apple activation server requests and patches
the response to report the device as "Activated", bypassing the
iCloud / MDM lock screen on qualifying devices.
"""

from mitmproxy import http
import json
import re


APPLE_ACTIVATION_HOSTS = [
    "albert.apple.com",
    "captive.apple.com",
    "gs.apple.com",
    "static.ips.apple.com",
    "init.ips.apple.com",
]

ACTIVATION_PATH_PATTERNS = [
    r"/deviceservices/",
    r"/WebObjects/MZFinance",
    r"/deviceservices/deviceActivation",
]


def _is_activation_request(flow: http.HTTPFlow) -> bool:
    host = flow.request.pretty_host
    path = flow.request.path
    if not any(h in host for h in APPLE_ACTIVATION_HOSTS):
        return False
    return any(re.search(p, path) for p in ACTIVATION_PATH_PATTERNS) or True


def request(flow: http.HTTPFlow) -> None:
    """Log intercepted activation requests."""
    if _is_activation_request(flow):
        print(f"[UnlockOS Proxy] ⬆  Request intercepted → {flow.request.host}{flow.request.path}")


def response(flow: http.HTTPFlow) -> None:
    """Patch activation responses to report 'Activated' status."""
    if not _is_activation_request(flow):
        return
    if not flow.response or not flow.response.content:
        return

    print(f"[UnlockOS Proxy] ⬇  Response intercepted from {flow.request.host}")

    content_type = flow.response.headers.get("Content-Type", "")

    # ----------------------------------------------------------------
    # Strategy 1: Plist / XML response (most common from albert.apple.com)
    # ----------------------------------------------------------------
    if "xml" in content_type or "plist" in content_type:
        text = flow.response.text
        replacements = [
            ("Unactivated", "Activated"),
            ("<false/>", "<true/>"),
            ("ActivationRequired", "Activated"),
            ("activation-required", "activated"),
            ("MDMRequired", "Bypassed"),
        ]
        modified = text
        for old, new in replacements:
            modified = modified.replace(old, new)
        if modified != text:
            flow.response.text = modified
            print("[UnlockOS Proxy] ✅ Plist activation response PATCHED")

    # ----------------------------------------------------------------
    # Strategy 2: JSON response (newer iOS versions)
    # ----------------------------------------------------------------
    elif "json" in content_type:
        try:
            data = json.loads(flow.response.text)
            patched = False
            for key in ("activation_status", "status", "activationState"):
                if key in data:
                    data[key] = "Activated"
                    patched = True
            for key in ("mdm_required", "activation_required", "requires_activation"):
                if key in data:
                    data[key] = False
                    patched = True
            if patched:
                flow.response.text = json.dumps(data)
                print("[UnlockOS Proxy] ✅ JSON activation response PATCHED")
        except (json.JSONDecodeError, Exception) as e:
            print(f"[UnlockOS Proxy] ⚠  JSON parse failed: {e}")

    else:
        # Fallback: raw text replacement
        text = flow.response.text
        if "Unactivated" in text or "ActivationRequired" in text:
            flow.response.text = (
                text.replace("Unactivated", "Activated")
                    .replace("ActivationRequired", "Activated")
                    .replace("false", "true")
            )
            print("[UnlockOS Proxy] ✅ Raw response PATCHED (fallback)")
