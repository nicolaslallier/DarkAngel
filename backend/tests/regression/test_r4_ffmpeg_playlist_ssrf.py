"""Regression — security review R4, SSRF through ffmpeg playlists.

Originally: the video summary handed ffprobe/ffmpeg the presigned URL with no
input restrictions. An upload named `x.mp4` but stored as
`application/vnd.apple.mpegurl` holding an `#EXTM3U` playlist was opened by
ffmpeg's HLS demuxer, which then fetched every segment URL in it — any host
the backend container could reach. An image piped on stdin could do the same.

Every ffmpeg/ffprobe call now pins its input: pipe-only for images, and for
videos HTTP plus container demuxers only (no hls, concat...).
"""

import subprocess

from app import summary


def _argv(monkeypatch, call) -> list[str]:
    seen = []

    def run(command, **_):
        seen.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=b"1.0")

    monkeypatch.setattr(summary.subprocess, "run", run)
    call()
    return seen[0]


def _option(argv: list[str], name: str) -> str:
    # An input option only counts before -i (ffmpeg) or anywhere (ffprobe).
    end = argv.index("-i") if "-i" in argv else len(argv)
    return argv[argv.index(name, 0, end) + 1]


def test_video_reads_are_limited_to_container_demuxers(monkeypatch):
    url = "http://s3/bucket/key?sig"
    for call in (lambda: summary._duration(url), lambda: summary._jpeg(url, at=1.0)):
        argv = _argv(monkeypatch, call)
        formats = _option(argv, "-format_whitelist").split(",")
        assert "hls" not in formats
        assert "concat" not in formats
        assert "mp4" in formats
        assert "file" not in _option(argv, "-protocol_whitelist").split(",")


def test_an_image_on_the_pipe_reads_nothing_else(monkeypatch):
    argv = _argv(monkeypatch, lambda: summary._jpeg("pipe:0", data=b"x"))
    assert _option(argv, "-protocol_whitelist") == "pipe"
