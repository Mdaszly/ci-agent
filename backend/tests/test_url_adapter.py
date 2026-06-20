from unittest.mock import MagicMock, patch

import pytest

from app.services.url_adapter import URLAdapter, URLAdapterError


class TestURLAdapter:
    def test_parse_html_extracts_title_and_text(self):
        html = """<!DOCTYPE html>
        <html>
        <head><title>Test Page</title></head>
        <body>
            <h1>Main Title</h1>
            <article>
                <p>This is a paragraph with some text.</p>
                <p>Another paragraph here.</p>
            </article>
        </body>
        </html>"""
        
        adapter = URLAdapter()
        title, text, price = adapter._parse_html(html)
        
        assert title == "Test Page"
        assert "Main Title" in text
        assert "This is a paragraph" in text
        assert price == ""

    def test_parse_html_extracts_price(self):
        html = """<!DOCTYPE html>
        <html>
        <head><title>Pricing Page</title></head>
        <body>
            <article>
                <h2>Pricing</h2>
                <p>Starter: $9.99/month</p>
                <p>Pro: $29.99 USD/month</p>
                <p>Enterprise: Contact us</p>
            </article>
        </body>
        </html>"""
        
        adapter = URLAdapter()
        title, text, price = adapter._parse_html(html)
        
        assert title == "Pricing Page"
        assert "$9.99" in price or "9.99" in price

    def test_extract_price_patterns(self):
        text = "Price: $19.99 USD, 订阅: ¥99, €49.99, £29"
        adapter = URLAdapter()
        price = adapter._extract_price(text)
        
        assert "$19.99" in price or "19.99" in price
        assert "¥99" in price or "99" in price

    def test_clean_text_removes_short_lines(self):
        adapter = URLAdapter()
        text = """Line 1: this is a long line with more than three characters

a

bc

Line 2: another long line"""
        
        cleaned = adapter._clean_text(text)
        
        assert "Line 1" in cleaned
        assert "Line 2" in cleaned
        assert "\na\n" not in cleaned
        assert "\nbc\n" not in cleaned

    @patch("app.services.url_adapter.httpx.Client")
    def test_fetch_follows_redirect(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        
        mock_response = MagicMock()
        mock_response.headers = {"content-type": "text/html"}
        mock_response.text = "<html><title>Redirected Page</title><body>Content</body></html>"
        mock_response.raise_for_status.return_value = None
        mock_client.get.return_value = mock_response
        
        adapter = URLAdapter()
        title, text, price = adapter.fetch("https://example.com")
        
        assert title == "Redirected Page"
        mock_client.get.assert_called_once()

    @patch("app.services.url_adapter.httpx.Client")
    def test_fetch_403_raises_error(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        
        from httpx import HTTPStatusError
        mock_client.get.side_effect = HTTPStatusError(
            "Forbidden",
            request=MagicMock(),
            response=MagicMock(status_code=403)
        )
        
        adapter = URLAdapter(max_retries=1)
        
        with pytest.raises(URLAdapterError, match="HTTP请求失败: 403"):
            adapter.fetch("https://example.com")
        
        assert mock_client.get.call_count == 2

    @patch("app.services.url_adapter.httpx.Client")
    def test_fetch_timeout_retries(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        
        from httpx import TimeoutException
        mock_client.get.side_effect = [
            TimeoutException("timeout", request=MagicMock()),
            TimeoutException("timeout", request=MagicMock()),
            MagicMock(
                headers={"content-type": "text/html"},
                text="<html><title>Success</title></html>",
                raise_for_status=MagicMock(return_value=None)
            )
        ]
        
        adapter = URLAdapter(max_retries=2)
        title, text, price = adapter.fetch("https://example.com")
        
        assert title == "Success"
        assert mock_client.get.call_count == 3

    @patch("app.services.url_adapter.httpx.Client")
    def test_fetch_timeout_exhausts_retries(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        
        from httpx import TimeoutException
        mock_client.get.side_effect = TimeoutException("timeout", request=MagicMock())
        
        adapter = URLAdapter(max_retries=2)
        
        with pytest.raises(URLAdapterError, match="请求超时"):
            adapter.fetch("https://example.com")
        
        assert mock_client.get.call_count == 3

    @patch("app.services.url_adapter.httpx.Client")
    def test_fetch_non_html_content_raises_error(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        
        mock_response = MagicMock()
        mock_response.headers = {"content-type": "image/png"}
        mock_response.text = "binary content"
        mock_response.raise_for_status.return_value = None
        mock_client.get.return_value = mock_response
        
        adapter = URLAdapter()
        
        with pytest.raises(URLAdapterError, match="不支持的内容类型"):
            adapter.fetch("https://example.com/image.png")