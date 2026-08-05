"""JSON Merge Patch (RFC 7386) — the PATCH contract for fire_config.custom_assumptions.

Semantics: dicts merge recursively, any non-dict value (scalars AND arrays)
replaces wholesale, and an explicit null DELETES the key. Clients clear a key
by sending null, never by omitting it — omission means "leave untouched"
(fire-master#9: omission used to mean "delete everything you didn't resend").

Deliberately NOT shared with FireProjectionsEngine._apply_overrides: scenario
overrides merge exactly one level deep by design and never delete, and
changing that engine's semantics would silently alter what existing saved
scenarios resolve to. Two contracts, both intentional.
"""

from copy import deepcopy


def json_merge_patch(base: dict | None, patch: dict | None) -> dict | None:
    """Apply an RFC 7386 merge patch to ``base`` and return the result.

    A non-dict ``patch`` (including None) replaces ``base`` entirely, per the
    RFC — so PATCHing ``custom_assumptions: null`` is an explicit full clear.
    Neither input is mutated.
    """
    if not isinstance(patch, dict):
        return deepcopy(patch)
    result = deepcopy(base) if isinstance(base, dict) else {}
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict):
            result[key] = json_merge_patch(result.get(key), value)
        else:
            result[key] = deepcopy(value)
    return result
