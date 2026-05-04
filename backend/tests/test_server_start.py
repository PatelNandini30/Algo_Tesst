import unittest


class TestAppCreation(unittest.TestCase):
    def test_app_has_default_orjson_response(self):
        """FastAPI app should use ORJSONResponse by default."""
        from backend.main import app
        from fastapi.responses import ORJSONResponse
        self.assertIs(app.router.default_response_class, ORJSONResponse)
