import unittest
from io import StringIO
from backend.services.intraday_ingest import base


class TestFormatDetection(unittest.TestCase):
    def test_unknown_header_raises(self):
        f = StringIO("foo,bar,baz\n1,2,3\n")
        with self.assertRaises(base.UnknownFormatError):
            base.detect_format(f)

    def test_registry_starts_empty_or_with_only_known_formats(self):
        # Registry contains only explicitly registered handlers; clean_2023 may be present
        known = {"clean_2023"}
        self.assertTrue(set(base.list_registered_formats()).issubset(known))

    def test_register_and_lookup(self):
        class FakeHandler(base.BaseFormatHandler):
            HEADER_SIGNATURE = "a,b,c"

            def clean(self, source_path):
                raise NotImplementedError

        base.register_handler("fake", FakeHandler)
        try:
            self.assertIn("fake", base.list_registered_formats())
            f = StringIO("a,b,c\n1,2,3\n")
            handler = base.detect_format(f)
            self.assertIsInstance(handler, FakeHandler)
        finally:
            base.unregister_handler("fake")

    def test_register_handler_must_subclass_base(self):
        class NotAHandler:
            HEADER_SIGNATURE = "x"

        with self.assertRaises(TypeError):
            base.register_handler("bad", NotAHandler)


if __name__ == "__main__":
    unittest.main()
