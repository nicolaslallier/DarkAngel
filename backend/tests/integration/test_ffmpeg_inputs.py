"""summary.material() against a real ffmpeg and the real SeaweedFS: every kind
of video and image it promises still gives stills through the input
whitelists, and an HLS playlist disguised as `.mp4` (the SSRF fixed in PR #37,
pinned at argv level in tests/regression/test_r4_ffmpeg_playlist_ssrf.py)
fetches nothing."""

import http.server
import io
import os
import shutil
import subprocess
import threading
import uuid

import pytest

from app import summary
from app.api.routes import files


@pytest.fixture(scope="module", autouse=True)
def _ffmpeg():
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return
    if os.environ.get("CI") == "true":
        pytest.fail("ffmpeg is not installed", pytrace=False)
    pytest.skip("ffmpeg is not installed")


def _make(tmp_path, ext: str, args: list[str]) -> bytes:
    out = tmp_path / f"in.{ext}"
    subprocess.run(  # noqa: S603
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", *args, str(out)],  # noqa: S607
        check=True,
    )
    return out.read_bytes()


def _store(s3_bucket, data: bytes, content_type: str) -> str:
    key = f"ffmpeg-test/{uuid.uuid4()}"
    files.s3_client().put_object(
        s3_bucket, key, io.BytesIO(data), len(data), content_type=content_type
    )
    return key


CLIP = "testsrc=duration=2:size=160x120:rate=25"


@pytest.mark.parametrize(
    ("ext", "content_type", "args"),
    [
        ("mp4", "video/mp4", [CLIP, "-c:v", "mpeg4"]),
        ("mov", "video/quicktime", [CLIP, "-c:v", "mpeg4"]),
        ("mkv", "video/x-matroska", [CLIP, "-c:v", "mpeg4"]),
        ("avi", "video/x-msvideo", [CLIP, "-c:v", "mjpeg"]),
        ("mpg", "video/mpeg", [CLIP, "-c:v", "mpeg1video", "-f", "mpeg"]),
        ("m1v", "video/mpeg", [CLIP, "-c:v", "mpeg1video", "-f", "mpeg1video"]),
        ("ts", "video/mp2t", [CLIP, "-c:v", "mpeg1video"]),
        ("wmv", "video/x-ms-wmv", [CLIP, "-c:v", "wmv2"]),
        ("flv", "video/x-flv", [CLIP, "-c:v", "flv"]),
    ],
)
def test_videos_still_give_stills(tmp_path, s3_bucket, ext, content_type, args):
    key = _store(s3_bucket, _make(tmp_path, ext, args), content_type)
    _, stills = summary.material(f"clip.{ext}", content_type, key, 1)
    assert len(stills) == len(summary.FRAMES)
    assert all(s.startswith(b"\xff\xd8") for s in stills)  # JPEG


@pytest.mark.parametrize(
    ("ext", "content_type"),
    [("png", "image/png"), ("jpg", "image/jpeg"), ("gif", "image/gif"), ("bmp", "image/bmp")],
)
def test_images_still_give_a_still(tmp_path, s3_bucket, ext, content_type):
    data = _make(tmp_path, ext, ["testsrc=size=1600x1200", "-frames:v", "1"])
    key = _store(s3_bucket, data, content_type)
    _, [still] = summary.material(f"pic.{ext}", content_type, key, len(data))
    assert still.startswith(b"\xff\xd8")


@pytest.fixture
def internal(tmp_path):
    """A stand-in for a service only the backend can reach; counts its hits."""
    hits = []
    segment = _make(tmp_path, "ts", [CLIP, "-c:v", "mpeg1video"])

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "video/mp2t")
            self.end_headers()
            self.wfile.write(segment)

        def log_message(self, *_):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}", hits
    server.shutdown()


@pytest.mark.parametrize(
    ("name", "content_type", "playlist"),
    [
        ("x.mp4", "application/vnd.apple.mpegurl", "#EXTM3U\n#EXT-X-TARGETDURATION:2\n"
         "#EXTINF:2,\n{url}/seg.ts\n#EXT-X-ENDLIST\n"),
        ("x.mp4", "video/mp4", "ffconcat version 1.0\nfile '{url}/seg.ts'\n"),
        ("x.png", "image/png", "#EXTM3U\n#EXT-X-TARGETDURATION:2\n"
         "#EXTINF:2,\n{url}/seg.ts\n#EXT-X-ENDLIST\n"),
    ],
)  # fmt: skip
def test_a_disguised_playlist_fetches_nothing(s3_bucket, internal, name, content_type, playlist):
    url, hits = internal
    data = playlist.format(url=url).encode()
    key = _store(s3_bucket, data, content_type)
    assert summary.material(name, content_type, key, len(data)) is None
    assert hits == []
