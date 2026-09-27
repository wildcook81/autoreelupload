"""Instagram Graph API Reels uploader."""

from __future__ import annotations

import logging
import random
import time
from pathlib import Path
from typing import Any

import requests
from requests import Response
from requests.exceptions import ConnectionError, Timeout

from config import Settings
from storage import HostedVideo, VideoStorage

logger = logging.getLogger(__name__)


class InstagramAPIError(RuntimeError):
    """Raised when the Instagram Graph API returns an error."""


class InstagramUploader:
    """Uploads Reels using the Instagram Graph API."""

    TRANSIENT_HTTP_STATUS_CODES = {
        429,
        500,
        502,
        503,
        504,
    }

    TERMINAL_STATUSES = {
        "FINISHED",
        "EXPIRED",
        "ERROR",
    }

    def __init__(
        self,
        settings: Settings,
        storage: VideoStorage,
        session: requests.Session | None = None,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.session = session or requests.Session()

        self.base_url = (
            f"https://graph.facebook.com/"
            f"{settings.graph_api_version}"
        )

    def upload_reel(
        self,
        video_path: Path,
        caption: str = "",
    ) -> str:
        """
        Upload and publish a Reel.

        Returns:
            The Instagram media ID returned by media_publish.
        """
        if not video_path.is_file():
            raise FileNotFoundError(video_path)

        if video_path.suffix.lower() != ".mp4":
            raise ValueError("Instagram Reel input must be an MP4 file.")

        hosted_video: HostedVideo | None = None
        published = False

        try:
            logger.info("Hosting video: %s", video_path)
            hosted_video = self.storage.upload(video_path)

            creation_id = self.create_container(
                video_url=hosted_video.url,
                caption=caption,
            )

            self.wait_for_container(creation_id)

            media_id = self.publish_container(creation_id)
            published = True

            logger.info(
                "Successfully published %s as Instagram media %s",
                video_path,
                media_id,
            )

            return media_id

        finally:
            # The URL must remain reachable until Meta has successfully
            # consumed the container. Cleanup therefore happens only after
            # successful publication.
            if published and hosted_video is not None:
                self.storage.cleanup(hosted_video)

    def create_container(
        self,
        video_url: str,
        caption: str,
    ) -> str:
        """Create an Instagram Reel media container."""
        endpoint = (
            f"{self.base_url}/"
            f"{self.settings.instagram_user_id}/media"
        )

        payload = {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": "true",
            "access_token": self.settings.instagram_access_token,
        }

        response = self._request_with_retry(
            method="POST",
            url=endpoint,
            data=payload,
        )

        body = self._parse_json(response)
        creation_id = body.get("id")

        if not creation_id:
            raise InstagramAPIError(
                f"Container creation response lacked id: {body}"
            )

        logger.info("Created Instagram container %s", creation_id)

        return str(creation_id)

    def wait_for_container(self, container_id: str) -> None:
        """
        Poll the container until processing succeeds or reaches a terminal
        failure state.
        """
        endpoint = f"{self.base_url}/{container_id}"

        deadline = time.monotonic() + self.settings.max_polling_seconds

        logger.info(
            "Polling container %s for up to %.0f seconds.",
            container_id,
            self.settings.max_polling_seconds,
        )

        while time.monotonic() < deadline:
            response = self._request_with_retry(
                method="GET",
                url=endpoint,
                params={
                    "fields": "status_code",
                    "access_token": self.settings.instagram_access_token,
                },
            )

            body = self._parse_json(response)
            status = str(body.get("status_code", "")).upper()

            logger.info(
                "Container %s status: %s",
                container_id,
                status or "<missing>",
            )

            if status == "FINISHED":
                return

            if status == "ERROR":
                raise InstagramAPIError(
                    f"Instagram container {container_id} entered ERROR: "
                    f"{body}"
                )

            if status == "EXPIRED":
                raise InstagramAPIError(
                    f"Instagram container {container_id} expired: {body}"
                )

            if status != "IN_PROGRESS":
                raise InstagramAPIError(
                    f"Unexpected status for container "
                    f"{container_id}: {body}"
                )

            remaining = deadline - time.monotonic()
            sleep_for = min(
                self.settings.polling_interval_seconds,
                max(0.0, remaining),
            )

            if sleep_for <= 0:
                break

            time.sleep(sleep_for)

        raise TimeoutError(
            f"Instagram container {container_id} did not finish within "
            f"{self.settings.max_polling_seconds:.0f} seconds."
        )

    def publish_container(self, creation_id: str) -> str:
        """Publish a successfully processed Instagram media container."""
        endpoint = (
            f"{self.base_url}/"
            f"{self.settings.instagram_user_id}/media_publish"
        )

        payload = {
            "creation_id": creation_id,
            "access_token": self.settings.instagram_access_token,
        }

        response = self._request_with_retry(
            method="POST",
            url=endpoint,
            data=payload,
        )

        body = self._parse_json(response)
        media_id = body.get("id")

        if not media_id:
            raise InstagramAPIError(
                f"Publish response lacked media id: {body}"
            )

        return str(media_id)

    def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Response:
        """Perform an HTTP request with exponential retry for transient errors."""
        last_error: Exception | None = None

        for attempt in range(self.settings.network_max_retries + 1):
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    data=data,
                    params=params,
                    timeout=self.settings.graph_api_timeout_seconds,
                )

                if (
                    response.status_code not in self.TRANSIENT_HTTP_STATUS_CODES
                    and response.status_code < 400
                ):
                    return response

                if (
                    response.status_code not in self.TRANSIENT_HTTP_STATUS_CODES
                    and response.status_code >= 400
                ):
                    self._raise_api_error(response)

                logger.warning(
                    "Transient HTTP %s from %s.",
                    response.status_code,
                    url,
                )

            except (ConnectionError, Timeout) as exc:
                last_error = exc
                logger.warning(
                    "Transient network error on attempt %d/%d: %s",
                    attempt + 1,
                    self.settings.network_max_retries + 1,
                    exc,
                )

            if attempt >= self.settings.network_max_retries:
                break

            delay = min(
                self.settings.network_backoff_max_seconds,
                self.settings.network_backoff_base_seconds
                * (2**attempt),
            )

            # Small jitter prevents synchronized retries.
            delay *= random.uniform(0.8, 1.2)

            logger.info("Retrying request in %.2f seconds.", delay)
            time.sleep(delay)

        if last_error:
            raise InstagramAPIError(
                f"Request failed after retries: {url}"
            ) from last_error

        raise InstagramAPIError(
            f"Request failed after retries: {url}"
        )

    @staticmethod
    def _parse_json(response: Response) -> dict[str, Any]:
        """Decode a Graph API JSON response."""
        try:
            body = response.json()
        except ValueError as exc:
            raise InstagramAPIError(
                f"Instagram returned non-JSON response: "
                f"{response.text[:500]}"
            ) from exc

        if not isinstance(body, dict):
            raise InstagramAPIError(
                f"Unexpected Instagram response: {body!r}"
            )

        return body

    def _raise_api_error(self, response: Response) -> None:
        """Raise an informative exception for a non-success API response."""
        try:
            body = response.json()
        except ValueError:
            body = response.text

        raise InstagramAPIError(
            f"Instagram Graph API HTTP {response.status_code}: {body}"
        )
