import http.client
import base64
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs

import httpx

from gemini_web2api.config import CONFIG, DEFAULT_CONFIG, load_config, write_default_config
from gemini_web2api.gemini import _build_headers, _build_payload, _cookie_cache, load_cookie
from gemini_web2api.server import GeminiHandler, ThreadedServer
from gemini_web2api.tools import google_contents_to_prompt, messages_to_prompt


def _decode_payload(payload):
    outer = json.loads(parse_qs(payload)["f.req"][0])
    return json.loads(outer[1])


def _decode_sse(body):
    events = []
    for block in body.strip().split("\n\n"):
        lines = block.splitlines()
        event_type = next(
            (line[len("event: "):] for line in lines if line.startswith("event: ")),
            None,
        )
        data = next(
            (line[len("data: "):] for line in lines if line.startswith("data: ")),
            None,
        )
        if event_type and data:
            events.append((event_type, json.loads(data)))
    return events


class PayloadPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(CONFIG)

    def tearDown(self):
        CONFIG.clear()
        CONFIG.update(self.original_config)

    def test_temporary_chats_default_to_disabled(self):
        self.assertIs(DEFAULT_CONFIG["temporary_chats"], False)

    def test_persistent_chat_payload(self):
        CONFIG["temporary_chats"] = False

        inner = _decode_payload(_build_payload("hello", 1, 4))

        self.assertEqual(inner[41], [2])
        self.assertIsNone(inner[45])

    def test_temporary_chat_payload(self):
        CONFIG["temporary_chats"] = True

        inner = _decode_payload(_build_payload("hello", 1, 4))

        self.assertEqual(inner[41], [1])
        self.assertEqual(inner[45], 1)

    def test_payload_includes_uploaded_image_refs(self):
        inner = _decode_payload(_build_payload("describe", 1, 4, [("/uploaded/image-ref", "image/jpeg")]))

        self.assertEqual(inner[0][0], "describe")
        [[file_info, filename]] = inner[0][3]
        self.assertEqual(file_info[:4], ["/uploaded/image-ref", 1, None, "image/jpeg"])
        self.assertEqual(len(file_info[4]), 36)
        self.assertEqual(filename, "image.jpeg")


def _wrb_line(text):
    inner = [None, None, None, None, [["rc_1", [text]]], "x" * 200]
    return json.dumps([["wrb.fr", None, json.dumps(inner)]])


class CitationCleanupTests(unittest.TestCase):
    def test_clean_text_strips_citation_markers_only_when_requested(self):
        from gemini_web2api.gemini import clean_text

        text = "A[cite: 1]\nB [cite: 1, 2].[cite]"
        self.assertEqual(clean_text(text, strip_citations=True), "A\nB .")
        self.assertEqual(clean_text(text), text)

    def _stream(self, get_client, texts, file_refs):
        from gemini_web2api.gemini import generate_stream

        resp = mock.MagicMock()
        resp.iter_text.return_value = iter([_wrb_line(t) + "\n" for t in texts])
        get_client.return_value.stream.return_value.__enter__.return_value = resp
        return "".join(generate_stream("p", 1, 4, file_refs))

    @mock.patch("gemini_web2api.gemini._get_httpx_client")
    def test_image_stream_strips_marker_split_across_chunks(self, get_client):
        texts = ("Hello[ci", "Hello[cite: 1] wor", "Hello[cite: 1] world[")
        out = self._stream(get_client, texts, [("/ref", "image/png")])

        self.assertEqual(out, "Hello world[")

    @mock.patch("gemini_web2api.gemini._get_httpx_client")
    def test_text_stream_keeps_citation_markers(self, get_client):
        texts = ("Hello[ci", "Hello[cite: 1] wor", "Hello[cite: 1] world[")
        out = self._stream(get_client, texts, None)

        self.assertEqual(out, "Hello[cite: 1] world[")


class UpstreamErrorTests(unittest.TestCase):
    # Real Gemini Web reply for a rejected image request.
    RAW = (
        ")]}'\n\n121\n"
        '[["wrb.fr",null,null,null,null,[3,null,[["type.googleapis.com/assistant.boq.bard.application.BardErrorInfo",[1003]]]]]]\n'
        '56\n[["di",80],["af.httprm",79,"-5383267747995074321",35]]\n25\n[["e",4,null,null,215]]\n'
    )

    def test_extract_response_text_raises_on_bard_error(self):
        from gemini_web2api.gemini import extract_response_text

        with self.assertRaisesRegex(RuntimeError, r"BardErrorInfo \[1003\]"):
            extract_response_text(self.RAW)

    @mock.patch.dict(CONFIG, {"retry_attempts": 1})
    @mock.patch("gemini_web2api.gemini._get_httpx_client")
    def test_stream_raises_on_bard_error(self, get_client):
        from gemini_web2api.gemini import generate_stream

        resp = mock.MagicMock()
        resp.iter_text.return_value = iter([self.RAW])
        get_client.return_value.stream.return_value.__enter__.return_value = resp

        with self.assertRaisesRegex(RuntimeError, r"BardErrorInfo \[1003\]"):
            list(generate_stream("p", 1, 4))


class StaleBlRetryTests(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(CONFIG)
        CONFIG.update({"gemini_bl": "boq_old", "cookie_file": None})

    def tearDown(self):
        CONFIG.clear()
        CONFIG.update(self.original_config)

    @staticmethod
    def _stale_bl_error():
        request = httpx.Request("POST", "https://gemini.google.com/")
        return httpx.HTTPStatusError("405", request=request, response=httpx.Response(405, request=request))

    @mock.patch("gemini_web2api.gemini.fetch_latest_bl", return_value="boq_new")
    @mock.patch("gemini_web2api.gemini._get_httpx_client")
    def test_generate_refreshes_bl_after_405(self, get_client, _fetch):
        from gemini_web2api.gemini import generate

        post = get_client.return_value.post
        post.side_effect = [self._stale_bl_error(), mock.MagicMock(text="")]

        generate("p", 1, 4)

        self.assertIn("bl=boq_old", post.call_args_list[0].args[0])
        self.assertIn("bl=boq_new", post.call_args_list[1].args[0])
        self.assertEqual(CONFIG["gemini_bl"], "boq_new")

    @mock.patch("gemini_web2api.gemini.fetch_latest_bl", return_value="boq_new")
    @mock.patch("gemini_web2api.gemini._get_httpx_client")
    def test_stream_refreshes_bl_after_405(self, get_client, _fetch):
        from gemini_web2api.gemini import generate_stream

        stale = self._stale_bl_error()
        ok = mock.MagicMock()
        ok.__enter__.return_value.iter_text.return_value = iter([])
        stream = get_client.return_value.stream
        stream.side_effect = [stale, ok]

        list(generate_stream("p", 1, 4))

        self.assertIn("bl=boq_old", stream.call_args_list[0].args[1])
        self.assertIn("bl=boq_new", stream.call_args_list[1].args[1])


class MessageParsingTests(unittest.TestCase):
    def test_messages_to_prompt_extracts_openai_image_url_data_url(self):
        image_data = base64.b64encode(b"fake png").decode()

        prompt, images = messages_to_prompt([{
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_data}"}},
            ],
        }])

        self.assertEqual(prompt, "Describe [Image attached]")
        self.assertEqual(images, [(b"fake png", "image/png")])

    def test_messages_to_prompt_extracts_responses_input_image_url(self):
        prompt, images = messages_to_prompt([{
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Describe"},
                {"type": "input_image", "image_url": "https://example.com/image.png"},
            ],
        }])

        self.assertEqual(prompt, "Describe [Image attached]")
        self.assertEqual(images, [("https://example.com/image.png", "image/png")])

    def test_messages_to_prompt_ignores_malformed_image_data_url(self):
        prompt, images = messages_to_prompt([{
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,%%%"}},
            ],
        }])

        self.assertEqual(prompt, "Describe")
        self.assertEqual(images, [])

    def test_google_contents_to_prompt_extracts_inline_image_data(self):
        image_data = base64.b64encode(b"fake png").decode()

        prompt, images = google_contents_to_prompt({
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": "Describe"},
                    {"inlineData": {"mimeType": "image/png", "data": image_data}},
                ],
            }],
        })

        self.assertEqual(prompt, "Describe\n[Image attached]")
        self.assertEqual(images, [(b"fake png", "image/png")])

    def test_google_contents_to_prompt_ignores_malformed_inline_image_data(self):
        prompt, images = google_contents_to_prompt({
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": "Describe"},
                    {"inlineData": {"mimeType": "image/png", "data": "%%%"}},
                ],
            }],
        })

        self.assertEqual(prompt, "Describe")
        self.assertEqual(images, [])


class StreamingEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadedServer(("127.0.0.1", 0), GeminiHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.original_config = dict(CONFIG)
        CONFIG["api_keys"] = []
        CONFIG["log_requests"] = False

    def tearDown(self):
        CONFIG.clear()
        CONFIG.update(self.original_config)

    def post_json(self, path, payload):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(
            "POST",
            path,
            body=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        body = response.read().decode()
        headers = dict(response.getheaders())
        connection.close()
        return response.status, headers, body

    def post_chunked_json(self, path, payload):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(
            "POST",
            path,
            body=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            encode_chunked=True,
        )
        response = connection.getresponse()
        body = response.read().decode()
        headers = dict(response.getheaders())
        connection.close()
        return response.status, headers, body

    @mock.patch("gemini_web2api.server.generate_stream")
    def test_chat_stream_starts_with_assistant_role(self, generate_stream):
        generate_stream.return_value = iter(["hel", "lo"])

        status, headers, body = self.post_json(
            "/v1/chat/completions",
            {
                "model": "gemini-3.6-flash",
                "messages": [{"role": "user", "content": "hello"}],
                "stream": True,
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/event-stream")
        chunks = [
            json.loads(line[len("data: "):])
            for line in body.splitlines()
            if line.startswith("data: {")
        ]
        self.assertEqual(chunks[0]["choices"][0]["delta"], {"role": "assistant"})
        self.assertEqual(chunks[1]["choices"][0]["delta"], {"content": "hel"})
        self.assertEqual(chunks[2]["choices"][0]["delta"], {"content": "lo"})
        self.assertTrue(body.endswith("data: [DONE]\n\n"))

    @mock.patch("gemini_web2api.server.generate", return_value="chunked ok")
    def test_chat_accepts_chunked_body(self, _generate):
        status, _, body = self.post_chunked_json(
            "/v1/chat/completions",
            {
                "model": "gemini-3.6-flash",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["choices"][0]["message"]["content"], "chunked ok")

    @mock.patch("gemini_web2api.server.generate", return_value="ok")
    def test_chat_response_id_tags_request_logs(self, _generate):
        CONFIG["log_requests"] = True

        with self.assertLogs("gemini_web2api", level="INFO") as logs:
            status, _, body = self.post_json(
                "/v1/chat/completions",
                {"model": "gemini-3.6-flash", "messages": [{"role": "user", "content": "hello"}]},
            )

        cid = json.loads(body)["id"]
        self.assertEqual(status, 200)
        self.assertRegex(cid, r"^chatcmpl-[0-9a-f]{12}$")
        # The access line is logged before the body is sent, so it is always captured.
        self.assertTrue(any(f"[{cid}]" in line and "POST /v1/chat/completions" in line
                            for line in logs.output), logs.output)

    @mock.patch("gemini_web2api.server.upload_image", return_value="/uploaded/image-ref")
    @mock.patch("gemini_web2api.server.generate", return_value="looks good")
    def test_chat_accepts_openai_image_url_data_url(self, generate, upload_image):
        image_data = base64.b64encode(b"fake png").decode()

        status, _, body = self.post_json(
            "/v1/chat/completions",
            {
                "model": "gemini-3.6-flash",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe this image"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{image_data}"
                            },
                        },
                    ],
                }],
            },
        )

        self.assertEqual(status, 200)
        upload_image.assert_called_once_with(b"fake png", "image.png", "image/png")
        self.assertEqual(generate.call_args.args[3], [("/uploaded/image-ref", "image/png")])
        self.assertIn("[Image attached]", generate.call_args.args[0])
        self.assertEqual(json.loads(body)["choices"][0]["message"]["content"], "looks good")

    @mock.patch("gemini_web2api.server.fetch_image_bytes", return_value=b"\xff\xd8\xffremote jpeg")
    @mock.patch("gemini_web2api.server.upload_image", return_value="/uploaded/remote-ref")
    @mock.patch("gemini_web2api.server.generate", return_value="remote ok")
    def test_responses_accepts_input_image_url(self, generate, upload_image, fetch_image_bytes):
        status, _, _ = self.post_json(
            "/v1/responses",
            {
                "model": "gemini-3.6-flash",
                "input": [{
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "What is shown?"},
                        {
                            "type": "input_image",
                            "image_url": "https://example.com/image.jpg",
                        },
                    ],
                }],
            },
        )

        self.assertEqual(status, 200)
        fetch_image_bytes.assert_called_once_with("https://example.com/image.jpg")
        upload_image.assert_called_once_with(b"\xff\xd8\xffremote jpeg", "image.png", "image/jpeg")
        self.assertEqual(generate.call_args.args[3], [("/uploaded/remote-ref", "image/jpeg")])
        self.assertIn("[Image attached]", generate.call_args.args[0])

    @mock.patch("gemini_web2api.server.upload_image", return_value="/uploaded/image-ref")
    @mock.patch("gemini_web2api.server.generate", return_value="top-level image ok")
    def test_responses_accepts_top_level_input_image(self, generate, upload_image):
        image_data = base64.b64encode(b"fake png").decode()

        status, _, _ = self.post_json(
            "/v1/responses",
            {
                "model": "gemini-3.6-flash",
                "input": [
                    {"type": "input_text", "text": "What is shown?"},
                    {
                        "type": "input_image",
                        "image_url": f"data:image/png;base64,{image_data}",
                    },
                ],
            },
        )

        self.assertEqual(status, 200)
        upload_image.assert_called_once_with(b"fake png", "image.png", "image/png")
        self.assertEqual(generate.call_args.args[3], [("/uploaded/image-ref", "image/png")])
        self.assertIn("What is shown?", generate.call_args.args[0])
        self.assertIn("[Image attached]", generate.call_args.args[0])

    @mock.patch("gemini_web2api.server.upload_image", side_effect=RuntimeError("upload denied"))
    def test_google_image_upload_failure_returns_502(self, _upload_image):
        image_data = base64.b64encode(b"fake png").decode()

        status, _, body = self.post_json(
            "/v1beta/models/gemini-3.6-flash:generateContent",
            {
                "contents": [{
                    "role": "user",
                    "parts": [{
                        "inlineData": {
                            "mimeType": "image/png",
                            "data": image_data,
                        },
                    }],
                }],
            },
        )

        self.assertEqual(status, 502)
        self.assertIn("image upload failed: upload denied", json.loads(body)["error"]["message"])

    @mock.patch("gemini_web2api.server.generate_stream", return_value=iter(["streamed"]))
    def test_google_stream_generate_content_uses_sse(self, _generate_stream):
        status, headers, body = self.post_json(
            "/v1beta/models/gemini-3.6-flash:streamGenerateContent",
            {
                "contents": [{
                    "role": "user",
                    "parts": [{"text": "Stream this"}],
                }],
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/event-stream")
        self.assertIn('"text": "streamed"', body)

    @mock.patch("gemini_web2api.server.generate", return_value="hello")
    def test_responses_text_stream_has_complete_event_sequence(self, _generate):
        status, headers, body = self.post_json(
            "/v1/responses",
            {
                "model": "gemini-3.6-flash",
                "input": "hello",
                "stream": True,
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/event-stream")
        events = _decode_sse(body)
        self.assertEqual(
            [event_type for event_type, _ in events],
            [
                "response.created",
                "response.in_progress",
                "response.output_item.added",
                "response.content_part.added",
                "response.output_text.delta",
                "response.output_text.done",
                "response.content_part.done",
                "response.output_item.done",
                "response.completed",
            ],
        )
        self.assertEqual(
            [event["sequence_number"] for _, event in events],
            list(range(1, len(events) + 1)),
        )
        self.assertEqual(events[4][1]["delta"], "hello")
        self.assertEqual(events[-1][1]["response"]["status"], "completed")
        self.assertEqual(events[-1][1]["response"]["output"][0]["content"][0]["text"], "hello")

    @mock.patch("gemini_web2api.server.parse_tool_calls")
    @mock.patch("gemini_web2api.server.generate", return_value="tool output")
    def test_responses_function_call_stream_has_complete_event_sequence(
        self, _generate, parse_tool_calls
    ):
        parse_tool_calls.return_value = (
            "",
            [
                {
                    "id": "call_test",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city":"Shanghai"}'},
                }
            ],
        )

        status, _, body = self.post_json(
            "/v1/responses",
            {
                "model": "gemini-3.6-flash",
                "input": "weather",
                "tools": [
                    {
                        "type": "function",
                        "name": "get_weather",
                        "description": "Get weather",
                        "parameters": {"type": "object"},
                    }
                ],
                "stream": True,
            },
        )

        self.assertEqual(status, 200)
        events = _decode_sse(body)
        self.assertEqual(
            [event_type for event_type, _ in events],
            [
                "response.created",
                "response.in_progress",
                "response.output_item.added",
                "response.function_call_arguments.delta",
                "response.function_call_arguments.done",
                "response.output_item.done",
                "response.completed",
            ],
        )
        self.assertEqual(
            [event["sequence_number"] for _, event in events],
            list(range(1, len(events) + 1)),
        )
        self.assertEqual(events[2][1]["output_index"], 0)
        self.assertEqual(events[3][1]["delta"], '{"city":"Shanghai"}')
        self.assertEqual(events[4][1]["arguments"], '{"city":"Shanghai"}')
        self.assertEqual(events[-1][1]["response"]["output"][0]["name"], "get_weather")


class WebModelHeaderTests(unittest.TestCase):
    def test_known_mode_sends_web_model_id(self):
        self.assertIn('"e6fa609c3fa255c0"', _build_headers(3)["x-goog-ext-525001261-jspb"])

    def test_unknown_mode_sends_no_model_header(self):
        self.assertNotIn("x-goog-ext-525001261-jspb", _build_headers(2))
        self.assertNotIn("x-goog-ext-525001261-jspb", _build_headers())


class CookieFileTokenTests(unittest.TestCase):
    def setUp(self):
        self.original_config = dict(CONFIG)
        self.tmpdir = tempfile.TemporaryDirectory()
        self.cookie_path = Path(self.tmpdir.name) / "gemini-auth.json"
        CONFIG.update({"cookie_file": str(self.cookie_path),"xsrf_token": "cfg-at",
                       "gemini_bl": "cfg-bl", "auth_user": 1})
        _cookie_cache.update({"str": "", "sapisid": None, "mtime": 0})

    def tearDown(self):
        CONFIG.clear()
        CONFIG.update(self.original_config)
        _cookie_cache.update({"str": "", "sapisid": None, "mtime": 0})
        self.tmpdir.cleanup()

    def _write(self, data):
        self.cookie_path.write_text(json.dumps(data))

    def test_cookie_file_tokens_override_config(self):
        self._write({"cookie": "SID=x", "sapisid": "s", "auth_user": None,
                     "xsrf_token": "file-at", "gemini_bl": "file-bl"})

        self.assertEqual(load_cookie(), ("SID=x", "s"))
        self.assertEqual(CONFIG["xsrf_token"], "file-at")
        self.assertEqual(CONFIG["gemini_bl"], "cfg-bl")  # always fetched, never from the file
        self.assertIsNone(CONFIG["auth_user"])

    def test_missing_tokens_keep_config_values(self):
        self._write({"cookie": "SID=x", "sapisid": "s"})

        load_cookie()
        self.assertEqual(CONFIG["xsrf_token"], "cfg-at")
        self.assertEqual(CONFIG["gemini_bl"], "cfg-bl")
        self.assertEqual(CONFIG["auth_user"], 1)

    def test_plain_text_cookie_file_is_rejected(self):
        self.cookie_path.write_text("SID=x; SAPISID=s")

        self.assertEqual(load_cookie(), ("", None))

    def test_relative_cookie_file_resolves_against_config_dir(self):
        config_path = Path(self.tmpdir.name) / "config.json"
        config_path.write_text(json.dumps({"cookie_file": "./gemini-auth.json"}))

        load_config(str(config_path))
        self.assertEqual(Path(CONFIG["cookie_file"]), self.cookie_path.resolve())

    def test_default_config_is_written_without_auth_values(self):
        config_path = Path(self.tmpdir.name) / "sub" / "config.json"

        write_default_config(str(config_path))
        data = json.loads(config_path.read_text())
        self.assertEqual(data["port"], 8081)
        self.assertNotIn("gemini_bl", data)
        self.assertNotIn("xsrf_token", data)

    def test_absolute_cookie_file_is_kept(self):
        config_path = Path(self.tmpdir.name) / "config.json"
        config_path.write_text(json.dumps({"cookie_file": "/etc/gemini-auth.json"}))

        load_config(str(config_path))
        self.assertEqual(CONFIG["cookie_file"], "/etc/gemini-auth.json")


if __name__ == "__main__":
    unittest.main()
