"""Public video storage implementations for Instagram publishing."""

from __future__ import annotations

import logging
import mimetypes
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import boto3
import cloudinary
import cloudinary.uploader
from botocore.exceptions import BotoCoreError, ClientError

from config import Settings

logger = logging.getLogger(__name__)


@dataclass
class HostedVideo:
    """Represents a remotely hosted video and its cleanup metadata."""

    url: str
    identifier: str
    provider: str


class VideoStorage(ABC):
    """Interface implemented by supported public video storage providers."""

    @abstractmethod
    def upload(self, video_path: Path) -> HostedVideo:
        """Upload a local video and return its public URL."""

    @abstractmethod
    def cleanup(self, hosted_video: HostedVideo) -> None:
        """Delete a previously uploaded video when possible."""


class S3Storage(VideoStorage):
    """AWS S3-backed public video storage."""

    def __init__(self, settings: Settings) -> None:
        if not settings.aws_s3_bucket:
            raise ValueError("AWS_S3_BUCKET is required.")

        self.bucket = settings.aws_s3_bucket
        self.region = settings.aws_region
        self.prefix = settings.aws_s3_prefix.strip("/")

        self.client = boto3.client(
            "s3",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )

    def upload(self, video_path: Path) -> HostedVideo:
        """Upload an MP4 and return its S3 HTTPS URL."""
        object_name = (
            f"{self.prefix}/{uuid.uuid4().hex}-{video_path.name}"
            if self.prefix
            else f"{uuid.uuid4().hex}-{video_path.name}"
        )

        content_type = mimetypes.guess_type(video_path.name)[0] or "video/mp4"

        try:
            self.client.upload_file(
                str(video_path),
                self.bucket,
                object_name,
                ExtraArgs={
                    "ContentType": content_type,
                },
            )
        except (BotoCoreError, ClientError) as exc:
            logger.exception("S3 upload failed for %s", video_path)
            raise RuntimeError("Failed to upload video to S3.") from exc

        url = (
            f"https://{self.bucket}.s3.{self.region}.amazonaws.com/"
            f"{object_name}"
        )

        logger.info("Uploaded %s to S3: %s", video_path, url)

        return HostedVideo(
            url=url,
            identifier=object_name,
            provider="s3",
        )

    def cleanup(self, hosted_video: HostedVideo) -> None:
        """Delete an uploaded S3 object."""
        try:
            self.client.delete_object(
                Bucket=self.bucket,
                Key=hosted_video.identifier,
            )
            logger.info("Deleted S3 object %s", hosted_video.identifier)
        except (BotoCoreError, ClientError):
            logger.exception(
                "Failed to delete S3 object %s",
                hosted_video.identifier,
            )


class CloudinaryStorage(VideoStorage):
    """Cloudinary-backed video storage."""

    def __init__(self, settings: Settings) -> None:
        if not all(
            [
                settings.cloudinary_cloud_name,
                settings.cloudinary_api_key,
                settings.cloudinary_api_secret,
            ]
        ):
            raise ValueError("Cloudinary credentials are incomplete.")

        cloudinary.config(
            cloud_name=settings.cloudinary_cloud_name,
            api_key=settings.cloudinary_api_key,
            api_secret=settings.cloudinary_api_secret,
            secure=True,
        )

        self.folder = settings.cloudinary_folder

    def upload(self, video_path: Path) -> HostedVideo:
        """Upload a video to Cloudinary and return its secure URL."""
        public_id = f"{uuid.uuid4().hex}-{video_path.stem}"

        try:
            result = cloudinary.uploader.upload(
                str(video_path),
                resource_type="video",
                folder=self.folder,
                public_id=public_id,
                overwrite=False,
            )
        except Exception as exc:
            logger.exception("Cloudinary upload failed for %s", video_path)
            raise RuntimeError(
                "Failed to upload video to Cloudinary."
            ) from exc

        url = result.get("secure_url")
        actual_public_id = result.get("public_id")

        if not url or not actual_public_id:
            raise RuntimeError(
                "Cloudinary did not return a usable public video URL."
            )

        logger.info("Uploaded %s to Cloudinary.", video_path)

        return HostedVideo(
            url=url,
            identifier=actual_public_id,
            provider="cloudinary",
        )

    def cleanup(self, hosted_video: HostedVideo) -> None:
        """Delete the uploaded Cloudinary video."""
        try:
            cloudinary.uploader.destroy(
                hosted_video.identifier,
                resource_type="video",
                invalidate=True,
            )
            logger.info(
                "Deleted Cloudinary asset %s",
                hosted_video.identifier,
            )
        except Exception:
            logger.exception(
                "Failed to delete Cloudinary asset %s",
                hosted_video.identifier,
            )


def create_storage(settings: Settings) -> VideoStorage:
    """Create the configured storage implementation."""
    if settings.storage_provider == "s3":
        return S3Storage(settings)

    if settings.storage_provider == "cloudinary":
        return CloudinaryStorage(settings)

    raise ValueError(
        f"Unsupported storage provider: {settings.storage_provider}"
    )
