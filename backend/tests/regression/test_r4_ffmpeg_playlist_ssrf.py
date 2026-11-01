"""Regression — security review R4, SSRF through ffmpeg playlists (PR #37).

Originally: the video summary handed ffprobe/ffmpeg the presigned URL with no
input restrictions. An upload named `x.mp4` but stored as
`application/vnd.apple.mpegurl` holding an `#EXTM3U` playlist was opened by
ffmpeg's HLS demuxer, which then fetched every segment URL in it — any host
the backend container could reach. An image piped on stdin could do the same.

Every ffmpeg/ffprobe call now pins its input to single-file demuxers, and the
image path to the pipe. tests/integration/test_ffmpeg_inputs.py proves the
same against a real ffmpeg.
"""

import subprocess

from app import summary

PLAYLISTS = {"hls", "concat", "ffconcat", "image2", "image2pipe", "tee", "data"}


def _record(monkeypatch, returncode=0):
    calls = []

    def run(command, **_):
        calls.append(command)
        if returncode and command[0] == "ffprobe":
            raise subprocess.CalledProcessError(returncode, command)
        return subprocess.CompletedProcess(command, returncode, stdout=b"10.0")

    monkeypatch.setattr(summary.subprocess, "run", run)
    monkeypatch.setattr(summary, "_video_url", lambda key: "http://s3/b/k?sig")
    monkeypatch.setattr(summary, "_read", lambda key, limit: b"bytes")
    return calls


def _input_option(argv: list[str], name: str) -> set[str]:
    end = argv.index("-i") if "-i" in argv else len(argv)  # ffprobe has no -i
    return set(argv[argv.index(name, 0, end) + 1].split(","))


def test_every_video_read_allows_no_playlist_demuxer(monkeypatch):
    calls = _record(monkeypatch)
    summary.material("x.mp4", "application/vnd.apple.mpegurl", "u/k", 10)

    assert [c[0] for c in calls] == ["ffprobe"] + ["ffmpeg"] * len(summary.FRAMES)
    for argv in calls:
        assert not _input_option(argv, "-format_whitelist") & PLAYLISTS
        assert "file" not in _input_option(argv, "-protocol_whitelist")


def test_an_image_reads_the_pipe_and_nothing_else(monkeypatch):
    calls = _record(monkeypatch)
    summary.material("x.png", "image/png", "u/k", 10)

    [argv] = calls
    assert _input_option(argv, "-protocol_whitelist") == {"pipe"}
    assert not _input_option(argv, "-format_whitelist") & PLAYLISTS


def test_a_refused_video_is_no_summary_rather_than_an_error(monkeypatch):
    _record(monkeypatch, returncode=1)
    assert summary.material("x.mp4", "video/mp4", "u/k", 10) is None
