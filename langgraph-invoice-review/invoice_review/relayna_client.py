"""
relayna_client.py — thin httpx wrapper around the Relayna REST API.

All API methods raise RelaynaError on non-2xx responses so nodes can handle
failures cleanly without parsing raw HTTP errors.

Relayna API reference (relevant endpoints):
  POST /api/assets/upload              — multipart upload, returns asset record
  POST /api/checkpoints                — create review checkpoint + magic link
  GET  /api/checkpoints/:id/status     — polling-friendly status endpoint
  POST /api/checkpoints/:id/cancel     — cancel a pending checkpoint
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx


class RelaynaError(Exception):
    """Raised when the Relayna API returns a non-2xx response."""

    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"Relayna API error {status_code}: {body}")


@dataclass
class CheckpointStatus:
    status: str                    # pending | approved | rejected | needs_changes | expired | cancelled
    decision_comment: Optional[str]


class RelaynaClient:
    """
    Synchronous Relayna API client.

    Usage:
        client = RelaynaClient.from_env()
        asset_id = client.upload_asset("invoice.pdf")
        checkpoint_id, review_url = client.create_checkpoint(...)
        status = client.get_status(checkpoint_id)
    """

    def __init__(self, base_url: str, api_key: str):
        # Strip trailing slash so we can always write f"{self.base_url}/api/..."
        self.base_url = base_url.rstrip("/")
        # trust_env=False prevents httpx from picking up HTTP_PROXY / HTTPS_PROXY
        # env vars, which would route localhost traffic through an external proxy
        # and cause connection timeouts.
        self._http = httpx.Client(
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            trust_env=False,
            timeout=120.0,
        )

    @classmethod
    def from_env(cls) -> "RelaynaClient":
        base_url = os.environ["RELAYNA_BASE_URL"]
        api_key = os.environ["RELAYNA_API_KEY"]
        return cls(base_url=base_url, api_key=api_key)

    # Keep the client alive for the lifetime of the workflow
    def __del__(self) -> None:
        self._http.close()

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code >= 400:
            raise RelaynaError(
                status_code=response.status_code,
                body=response.text,
            )

    # ── Assets ────────────────────────────────────────────────────────────────

    def upload_asset(
        self,
        file_path: str | Path,
        purpose: str = "invoice",
        ttl_seconds: int = 86400,
    ) -> str:
        """
        Upload a file to Relayna and return the asset UUID.

        Relayna stores the file in Cloudflare R2. The asset UUID is used when
        attaching the file to a review checkpoint.
        """
        file_path = Path(file_path)
        url = f"{self.base_url}/api/assets/upload"

        with open(file_path, "rb") as f:
            response = self._http.post(
                url,
                files={"file": (file_path.name, f, "application/pdf")},
                data={"purpose": purpose, "ttl_seconds": str(ttl_seconds)},
            )

        self._raise_for_status(response)
        data = response.json()
        return data["asset"]["id"]

    # ── Checkpoints ───────────────────────────────────────────────────────────

    def create_checkpoint(
        self,
        title: str,
        instructions: str,
        summary: str,
        items: list[dict],
        callback_url: Optional[str] = None,
        ttl_seconds: int = 86400,
        external_ref: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> tuple[str, str]:
        """
        Create a review checkpoint and return (checkpoint_id, review_url).

        The review_url is a magic link the human reviewer opens in their browser.
        No login is required — the token in the URL IS the auth credential.

        `items` format:
            [
                # Attach a previously-uploaded asset (e.g. the PDF)
                {"item_type": "asset", "asset_id": "...", "label": "Invoice PDF", "position": 0},

                # Embed structured JSON data for the reviewer to inspect
                {"item_type": "json", "label": "Extracted Data", "content_json": {...}, "position": 1},

                # Plain text note
                {"item_type": "text", "label": "Note", "content_text": "...", "position": 2},
            ]
        """
        url = f"{self.base_url}/api/checkpoints"
        payload: dict = {
            "title": title,
            "instructions": instructions,
            "summary": summary,
            "ttl_seconds": ttl_seconds,
            "items": items,
        }
        if callback_url:
            payload["callback_url"] = callback_url
        if external_ref:
            payload["external_ref"] = external_ref
        if metadata:
            payload["metadata"] = metadata

        response = self._http.post(url, json=payload)
        self._raise_for_status(response)

        data = response.json()
        checkpoint_id = data["checkpoint"]["id"]
        review_url = data["review_url"]
        return checkpoint_id, review_url

    def get_status(self, checkpoint_id: str) -> CheckpointStatus:
        """
        Fetch the current status of a checkpoint.

        This is the polling endpoint — lightweight and designed for repeated calls.
        Returns a CheckpointStatus with `status` and `decision_comment`.
        """
        url = f"{self.base_url}/api/checkpoints/{checkpoint_id}/status"
        response = self._http.get(url)
        self._raise_for_status(response)

        data = response.json()
        return CheckpointStatus(
            status=data["status"],
            decision_comment=data.get("decision_comment"),
        )

    def cancel_checkpoint(self, checkpoint_id: str) -> None:
        """Cancel a pending checkpoint (no-op if already decided/expired)."""
        url = f"{self.base_url}/api/checkpoints/{checkpoint_id}/cancel"
        response = self._http.post(url)
        # 422 is acceptable if the checkpoint can't be cancelled in its current state
        if response.status_code not in (200, 201, 204, 422):
            self._raise_for_status(response)
