import json
import unittest
from unittest.mock import patch, MagicMock
import sys
import os

# Ensure src/ is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

# ── Patch all module-level side effects before importing the handler ──────────
# query_processor.py calls get_gemini_api_key() and genai.Client() at import
# time. We stub those out so tests never need real AWS / Gemini credentials.

_secretsmanager_patcher = patch('boto3.client')
mock_boto3_client = _secretsmanager_patcher.start()

# Fake secretsmanager that returns a dummy Gemini key
mock_sm = MagicMock()
mock_sm.get_secret_value.return_value = {
    'SecretString': json.dumps({'GEMINI_API_KEY': 'test-api-key'})
}
mock_boto3_client.return_value = mock_sm

# Fake genai.Client so no real HTTP is made at import time
_genai_patcher = patch('google.genai.Client')
mock_genai_client_cls = _genai_patcher.start()
mock_genai_client_cls.return_value = MagicMock()

# Environment variables required by module-level code
os.environ.setdefault('DOCUMENTS_BUCKET', 'test-bucket')
os.environ.setdefault('METADATA_TABLE', 'test-table')
os.environ.setdefault('STAGE', 'test')
os.environ.setdefault('DB_SECRET_ARN', 'arn:aws:secretsmanager:us-east-1:123456789:secret:test-db')
os.environ.setdefault('GEMINI_SECRET_ARN', 'arn:aws:secretsmanager:us-east-1:123456789:secret:test-gemini')
os.environ.setdefault('GEMINI_EMBEDDING_MODEL', 'text-embedding-004')
os.environ.setdefault('TEMPERATURE', '0.7')
os.environ.setdefault('MAX_OUTPUT_TOKENS', '1024')
os.environ.setdefault('TOP_K', '40')
os.environ.setdefault('TOP_P', '0.9')
os.environ.setdefault('ENABLE_EVALUATION', 'false')

from query_processor.query_processor import handler  # noqa: E402


class TestQueryProcessor(unittest.TestCase):

    # ── healthcheck ───────────────────────────────────────────────────────────

    def test_handler_healthcheck(self):
        """Health check returns the updated message from the agentic RAG handler."""
        response = handler({"action": "healthcheck"}, {})

        self.assertEqual(response["statusCode"], 200)
        body = json.loads(response["body"])
        self.assertEqual(
            body["message"],
            "Enhanced query processor with stateless agentic RAG is healthy"
        )
        # New fields present in health check response
        self.assertIn("stage", body)
        self.assertIn("client_type", body)
        self.assertEqual(body["client_type"], "stateless_http")

    def test_handler_healthcheck_via_body(self):
        """Health check also works when action is inside the request body."""
        event = {"body": json.dumps({"action": "healthcheck"})}
        response = handler(event, {})
        self.assertEqual(response["statusCode"], 200)

    # ── missing query ─────────────────────────────────────────────────────────

    def test_handler_missing_query(self):
        """Missing query returns 400."""
        event = {"body": json.dumps({"user_id": "user-1"})}
        response = handler(event, {})
        self.assertEqual(response["statusCode"], 400)
        body = json.loads(response["body"])
        self.assertEqual(body["message"], "Query is required")

    # ── successful query ──────────────────────────────────────────────────────

    @patch("query_processor.query_processor.embed_query")
    @patch("query_processor.query_processor.similarity_search")
    @patch("query_processor.query_processor.generate_response")
    def test_handler_query_success(self, mock_generate, mock_search, mock_embed):
        """Successful query returns the new nested response structure."""
        mock_embed.return_value = [0.1, 0.2, 0.3]

        mock_chunks = [
            {
                "chunk_id": "chunk-1",
                "document_id": "doc-1",
                "user_id": "user-1",
                "content": "RAG stands for Retrieval-Augmented Generation",
                "metadata": {"page": 1},
                "file_name": "file1.pdf",
                "similarity_score": 0.95
            }
        ]
        mock_search.return_value = mock_chunks
        mock_generate.return_value = (
            "RAG stands for Retrieval-Augmented Generation. "
            "It combines retrieval and generation techniques."
        )

        event = {
            "body": json.dumps({
                "query": "What is RAG?",
                "user_id": "user-1",
                "model_name": "gemini-2.0-flash"
            })
        }

        response = handler(event, {})
        self.assertEqual(response["statusCode"], 200)

        body = json.loads(response["body"])

        # Top-level fields
        self.assertEqual(body["query"], "What is RAG?")
        self.assertEqual(
            body["response"],
            "RAG stands for Retrieval-Augmented Generation. "
            "It combines retrieval and generation techniques."
        )

        # Traditional RAG sub-object (replaces old flat "results" key)
        self.assertIn("traditional_rag", body)
        trad = body["traditional_rag"]
        self.assertEqual(trad["count"], 1)
        self.assertEqual(len(trad["results"]), 1)
        self.assertEqual(
            trad["results"][0]["content"],
            "RAG stands for Retrieval-Augmented Generation"
        )
        self.assertIn("assessment", trad)

        # MCP web search sub-object
        self.assertIn("mcp_web_search", body)
        self.assertFalse(body["mcp_web_search"]["used"])
        self.assertEqual(body["mcp_web_search"]["client_type"], "stateless_http")

        # Metadata sub-object
        self.assertIn("metadata", body)
        self.assertEqual(body["metadata"]["mcp_client_type"], "stateless_http")

    @patch("query_processor.query_processor.embed_query")
    @patch("query_processor.query_processor.similarity_search")
    @patch("query_processor.query_processor.generate_response")
    def test_handler_query_no_results(self, mock_generate, mock_search, mock_embed):
        """Query with no matching chunks still returns a valid response structure."""
        mock_embed.return_value = [0.0] * 768
        mock_search.return_value = []
        mock_generate.return_value = "I could not find relevant information."

        event = {
            "body": json.dumps({
                "query": "unknown topic",
                "user_id": "user-1"
            })
        }

        response = handler(event, {})
        self.assertEqual(response["statusCode"], 200)

        body = json.loads(response["body"])
        self.assertEqual(body["traditional_rag"]["count"], 0)
        # When no chunks exist, RAG quality assessment triggers web search need
        self.assertTrue(body["traditional_rag"]["assessment"]["needs_web_search"])

    # ── RAG quality assessment ────────────────────────────────────────────────

    def test_assess_rag_quality_no_chunks(self):
        """Empty chunk list always needs web search."""
        from query_processor.query_processor import assess_rag_quality
        result = assess_rag_quality([], "test query")
        self.assertTrue(result["needs_web_search"])
        self.assertEqual(result["confidence"], 0.0)

    def test_assess_rag_quality_high_confidence(self):
        """High-similarity chunks should not trigger web search."""
        from query_processor.query_processor import assess_rag_quality
        chunks = [
            {"content": "A" * 200, "similarity_score": 0.95},
            {"content": "B" * 200, "similarity_score": 0.90},
        ]
        result = assess_rag_quality(chunks, "test query")
        self.assertFalse(result["needs_web_search"])
        self.assertGreater(result["confidence"], 0.7)

    def test_assess_rag_quality_low_confidence(self):
        """Low-similarity chunks should trigger web search."""
        from query_processor.query_processor import assess_rag_quality
        chunks = [
            {"content": "A" * 200, "similarity_score": 0.3},
        ]
        result = assess_rag_quality(chunks, "test query")
        self.assertTrue(result["needs_web_search"])

    def test_assess_rag_quality_short_context(self):
        """Sufficient similarity but tiny context should trigger web search."""
        from query_processor.query_processor import assess_rag_quality
        chunks = [
            {"content": "short", "similarity_score": 0.95},
        ]
        result = assess_rag_quality(chunks, "test query")
        self.assertTrue(result["needs_web_search"])

    # ── error handling ────────────────────────────────────────────────────────

    @patch("query_processor.query_processor.embed_query")
    @patch("query_processor.query_processor.similarity_search")
    def test_handler_db_error(self, mock_search, mock_embed):
        """DB failure returns 500."""
        mock_embed.return_value = [0.1, 0.2, 0.3]
        mock_search.side_effect = Exception("DB connection failed")

        event = {
            "body": json.dumps({"query": "What is RAG?", "user_id": "user-1"})
        }

        response = handler(event, {})
        self.assertEqual(response["statusCode"], 500)
        body = json.loads(response["body"])
        self.assertIn("Internal error", body["message"])

    # ── MCP client unit tests ─────────────────────────────────────────────────

    def test_stateless_mcp_client_init(self):
        """StatelessMCPClient initialises with correct defaults."""
        from query_processor.query_processor import StatelessMCPClient
        c = StatelessMCPClient("http://localhost:8080/mcp", timeout=45.0)
        self.assertEqual(c.mcp_url, "http://localhost:8080/mcp")
        self.assertEqual(c.timeout, 45.0)
        self.assertIn("Content-Type", c.headers)

    def test_stateless_mcp_client_request_id(self):
        """Request IDs are unique and sequential."""
        from query_processor.query_processor import StatelessMCPClient
        c = StatelessMCPClient("http://localhost:8080/mcp")
        id1 = c._generate_request_id()
        id2 = c._generate_request_id()
        self.assertNotEqual(id1, id2)
        self.assertTrue(id1.startswith("req_1_"))
        self.assertTrue(id2.startswith("req_2_"))

    def test_stateless_mcp_extract_tool_result_jsonrpc(self):
        """_extract_tool_result handles standard JSON-RPC result."""
        from query_processor.query_processor import StatelessMCPClient
        c = StatelessMCPClient("http://localhost:8080/mcp")
        response_data = {"result": "search result text"}
        result = c._extract_tool_result(response_data)
        self.assertEqual(result, "search result text")

    def test_stateless_mcp_extract_tool_result_error(self):
        """_extract_tool_result surfaces MCP errors clearly."""
        from query_processor.query_processor import StatelessMCPClient
        c = StatelessMCPClient("http://localhost:8080/mcp")
        response_data = {"error": {"code": -32601, "message": "Method not found"}}
        result = c._extract_tool_result(response_data)
        self.assertIn("MCP Error", result)
        self.assertIn("Method not found", result)


if __name__ == "__main__":
    unittest.main()