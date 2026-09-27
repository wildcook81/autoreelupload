"""CLI entry point for automated Instagram Reel publishing."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from config import get_settings
from storage import create_storage
from uploader import InstagramUploader


logger = logging.getLogger(__name__)


def configure_logging(level: str) -> None:
    """Configure application-wide logging."""
    logging.basicConfig(
        level=getattr(logging, level),
        format=(
            "%(asctime)s | %(levelname)s | "
            "%(name)s | %(message)s"
        ),
    )


def upload_file(
    uploader: InstagramUploader,
    video_path: Path,
    caption: str,
) -> None:
    """Upload one MP4 and log the result."""
    try:
        media_id = uploader.upload_reel(
            video_path=video_path,
            caption=caption,
        )

        logger.info(
            "Published %s successfully. Media ID: %s",
            video_path,
            media_id,
        )

    except Exception:
        logger.exception(
            "Failed to publish %s",
            video_path,
        )


class MP4EventHandler(FileSystemEventHandler):
    """Watchdog handler for newly created MP4 files."""

    def __init__(
        self,
        uploader: InstagramUploader,
        caption: str,
    ) -> None:
        self.uploader = uploader
        self.caption = caption

    def on_created(self, event) -> None:
        """Process newly created MP4 files."""
        if event.is_directory:
            return

        path = Path(event.src_path)

        if path.suffix.lower() != ".mp4":
            return

        logger.info("Detected new video: %s", path)

        # Wait until the producing process has finished writing the file.
        if not wait_for_file_ready(path):
            logger.error("File never became ready: %s", path)
            return

        upload_file(
            uploader=self.uploader,
            video_path=path,
            caption=self.caption,
        )


def wait_for_file_ready(
    path: Path,
    checks: int = 3,
    interval: float = 2.0,
) -> bool:
    """Wait until a file exists and its size stops changing."""
    previous_size = -1

    for _ in range(checks):
        if not path.exists():
            time.sleep(interval)
            continue

        current_size = path.stat().st_size

        if current_size > 0 and current_size == previous_size:
            return True

        previous_size = current_size
        time.sleep(interval)

    return path.exists() and path.stat().st_size > 0


def watch_directory(
    directory: Path,
    uploader: InstagramUploader,
    caption: str,
) -> None:
    """Watch a directory for newly created MP4 files."""
    directory.mkdir(parents=True, exist_ok=True)

    handler = MP4EventHandler(
        uploader=uploader,
        caption=caption,
    )

    observer = Observer()
    observer.schedule(
        handler,
        str(directory),
        recursive=False,
    )
    observer.start()

    logger.info("Watching directory: %s", directory.resolve())

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping directory watcher.")
    finally:
        observer.stop()
        observer.join()


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        description="Automated Instagram Reels uploader.",
    )

    mode = parser.add_mutually_exclusive_group(required=True)

    mode.add_argument(
        "--video",
        type=Path,
        help="Path to an MP4 file to upload.",
    )

    mode.add_argument(
        "--watch",
        type=Path,
        help="Directory to watch for new MP4 files.",
    )

    parser.add_argument(
        "--caption",
        default="",
        help="Instagram Reel caption.",
    )

    return parser


def main() -> int:
    """Run the CLI application."""
    parser = build_parser()
    args = parser.parse_args()

    try:
        settings = get_settings()
        configure_logging(settings.log_level)

        storage = create_storage(settings)

        uploader = InstagramUploader(
            settings=settings,
            storage=storage,
        )

        if args.video:
            if not args.video.exists():
                logger.error("Video does not exist: %s", args.video)
                return 1

            upload_file(
                uploader=uploader,
                video_path=args.video,
                caption=args.caption,
            )

        elif args.watch:
            watch_directory(
                directory=args.watch,
                uploader=uploader,
                caption=args.caption,
            )

        return 0

    except Exception:
        logger.exception("Application failed.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
