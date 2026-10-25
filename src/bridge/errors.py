"""Typed error taxonomy (docs/04 §Error taxonomy).

Tool results carry `{"error": {"code": ..., "message": ...}}` where the message
is FIXED wording per code — never exception text, provider messages, paths or
payloads (docs/12 §6). A provider's own error message may interpolate a
personal file name, so it is never forwarded; the code is mapped to Bridge
semantics first.
"""

from __future__ import annotations

from dataclasses import dataclass


class BridgeError(Exception):
    """Raised internally; the tool layer converts it to a typed result."""

    def __init__(self, code: str, *, provider_code: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.provider_code = provider_code  # recorded for logs as a code only


# local-search error envelope codes -> Bridge taxonomy (docs/04, docs/12).
# Anything unmapped is an incompatibility, never silently retried or ignored.
PROVIDER_CODE_MAP = {
    "not_found": "asset_unavailable",
    "file_unavailable": "asset_unavailable",
    "index_mismatch": "index_incompatible",
    "rebuild_running": "asset_unavailable",
    "model_loading": "dependency_unavailable",
    "forbidden_host": "index_incompatible",
    "search_expired": "invalid_request",
    # B-10 copy-path codes (docs/04 register_asset contract): an unmapped code
    # would say "the provider API is incompatible", which is wrong — these are
    # transient refusals of THIS copy operation.
    "path_conflict": "path_conflict",
    "storage_full": "storage_full",
}


def provider_error_to_bridge_code(provider_code: str) -> str:
    return PROVIDER_CODE_MAP.get(provider_code, "index_incompatible")


@dataclass(frozen=True)
class ErrorSpec:
    code: str
    message: str


ERRORS: dict[str, ErrorSpec] = {
    spec.code: spec
    for spec in (
        ErrorSpec("invalid_request", "The request is malformed or contains a rejected field."),
        ErrorSpec("unauthorized_scope", "The scope is not usable under the configured trusted-local policy."),
        ErrorSpec("asset_not_found", "No such asset in this scope."),
        ErrorSpec("asset_unavailable", "The asset cannot be reached right now (missing, stale or offline)."),
        ErrorSpec("unsupported_media", "The media type is not supported by this tool."),
        ErrorSpec("media_too_large", "The media exceeds the configured size or pixel bounds."),
        ErrorSpec("dependency_unavailable", "The asset provider cannot be reached."),
        ErrorSpec("index_incompatible", "The provider index or API is incompatible."),
        ErrorSpec("storage_error", "The Bridge storage operation failed."),
        # B-10 managed import (docs/04 register_asset, docs/03 §5): failures of
        # the COPY path, kept distinct from asset availability so an operator
        # can tell "this import did not land" from "this asset is offline".
        ErrorSpec("storage_full", "The managed storage cannot accept the copy."),
        ErrorSpec("path_conflict", "A managed asset already occupies that content path."),
        ErrorSpec("source_unreadable", "The provider could not serve the source bytes for the copy."),
    )
}


def error_result(code: str) -> dict:
    """The single response shape for every failure, including asset_not_found
    (identical for unknown ids and other-scope ids; anti-enumeration, T-02)."""
    spec = ERRORS[code]
    return {"error": {"code": spec.code, "message": spec.message}}
