"""Tests for app.services.logo_images — normalizing any image into the
128px square PNG we store, and pulling a logo from an admin's pasted URL
(an image, or a page it's on) or uploaded file. No network: httpx.MockTransport."""

import io
import re

import httpx
import pytest
from PIL import Image

from app.services import logo_images as li


def png(width=180, height=180, color=(200, 30, 30, 255)) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", (width, height), color).save(out, format="PNG")
    return out.getvalue()


def ico(sizes) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", (256, 256), (0, 0, 255, 255)).save(out, format="ICO", sizes=sizes)
    return out.getvalue()


def client(routes: dict[str, tuple[int, bytes, str]]) -> httpx.Client:
    """routes: absolute URL -> (status, body, content type). Anything else 404s."""

    def handler(request: httpx.Request) -> httpx.Response:
        status, body, ctype = routes.get(str(request.url), (404, b"", "text/plain"))
        if status in (301, 302):
            return httpx.Response(status, headers={"Location": body.decode()})
        return httpx.Response(status, content=body, headers={"content-type": ctype})

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def page(head: str) -> tuple[int, bytes, str]:
    return 200, f"<html><head>{head}</head><body></body></html>".encode(), "text/html"


class TestCandidates:
    def test_priority(self):
        html = """
          <meta itemprop="image" content="/images/g_128dp.png">
          <script type="application/ld+json">{"@type": "Organization", "logo": {"url": "/brand/logo.png"}}</script>
          <link rel="icon" href="/fav-16.png" sizes="16x16">
          <link rel="icon" href="/fav-192.png" sizes="192x192">
          <link rel="apple-touch-icon" href="/touch-120.png" sizes="120x120">
          <link rel="apple-touch-icon" href="/touch-180.png" sizes="180x180">
          <link rel="icon" href="/logo.svg" type="image/svg+xml">
          <link rel="shortcut icon" href="/unsized.png">
        """
        assert li.candidates(html, "https://www.acme.com/") == [
            "https://www.acme.com/brand/logo.png",
            "https://www.acme.com/touch-180.png",
            "https://www.acme.com/touch-120.png",
            "https://www.acme.com/fav-192.png",
            "https://www.acme.com/unsized.png",
            "https://www.acme.com/images/g_128dp.png",
            "https://www.acme.com/apple-touch-icon.png",
            "https://www.acme.com/favicon.ico",
        ]
        assert li.square_only_candidates(html, "https://www.acme.com/") == {"https://www.acme.com/images/g_128dp.png"}

    def test_malformed_json_ld_is_ignored(self):
        assert li.candidates('<script type="application/ld+json">{nope</script>', "https://acme.com/") == [
            "https://acme.com/apple-touch-icon.png",
            "https://acme.com/favicon.ico",
        ]


class TestNormalize:
    def test_square_png_out(self):
        out = Image.open(io.BytesIO(li.normalize(png(300, 150))))
        assert (out.format, out.size) == ("PNG", (128, 128))

    @pytest.mark.parametrize(("width", "height"), [(32, 32), (400, 60)])
    def test_automatic_rejects_tiny_and_banner_images(self, width, height):
        assert li.normalize(png(width, height)) is None

    def test_ico_uses_its_largest_frame(self):
        assert li.normalize(ico([(16, 16), (32, 32), (64, 64)])) is not None

    @pytest.mark.parametrize(
        ("data", "reason"),
        [
            (b"<html>nope</html>", "isn't an image"),
            (b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "SVG"),
            (png(20, 20), "Too small (20×20)"),
            (png(400, 60), "Too wide"),
            (png(100, 100, (0, 0, 0, 0)), "transparent"),
        ],
    )
    def test_readable_reasons(self, data, reason):
        with pytest.raises(li.LogoRejected, match=re.escape(reason)):
            li.normalize_or_raise(data)


class TestLogoFromUrl:
    def test_an_image_url(self):
        result = li.logo_from_url("https://cdn.acme.com/logo.png", client=client({
            "https://cdn.acme.com/logo.png": (200, png(), "image/png"),
        }))
        assert (result.status, result.source_url) == (li.STATUS_OK, "https://cdn.acme.com/logo.png")

    def test_a_page_url_uses_the_logo_on_it(self):
        result = li.logo_from_url("acme.com", client=client({
            "https://acme.com": _redirect("https://www.acme.com/"),
            "https://www.acme.com/": page('<link rel="apple-touch-icon" href="/t.png">'),
            "https://www.acme.com/t.png": (200, png(), "image/png"),
        }))
        assert result.source_url == "https://www.acme.com/t.png"

    def test_manual_accepts_a_small_but_real_icon(self):
        result = li.logo_from_url("https://acme.com/i.png", client=client({
            "https://acme.com/i.png": (200, png(40, 40), "image/png"),
        }))
        assert result.status == li.STATUS_OK

    @pytest.mark.parametrize(
        ("routes", "reason"),
        [
            ({}, "HTTP 404"),
            ({"https://acme.com/x": (403, b"", "text/html")}, "refused our request"),
            ({"https://acme.com/x": (200, b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml")}, "SVG"),
            ({"https://acme.com/x": page("")}, "No logo found on that page"),
        ],
    )
    def test_rejections(self, routes, reason):
        with pytest.raises(li.LogoRejected, match=reason):
            li.logo_from_url("https://acme.com/x", client=client(routes))

    def test_not_a_web_address(self):
        with pytest.raises(li.LogoRejected, match="web address"):
            li.logo_from_url("ftp://acme.com/x")


class TestUpload:
    def test_accepts_png_jpeg_ico(self):
        jpeg = io.BytesIO()
        Image.new("RGB", (100, 100), "red").save(jpeg, format="JPEG")
        for data in (png(), jpeg.getvalue(), ico([(64, 64)])):
            assert li.logo_from_upload(data)

    def test_rejects_oversize(self):
        with pytest.raises(li.LogoRejected, match="over 2 MB"):
            li.logo_from_upload(b"x" * (li.MAX_UPLOAD_BYTES + 1))


def _redirect(to: str) -> tuple[int, bytes, str]:
    return 301, to.encode(), ""
