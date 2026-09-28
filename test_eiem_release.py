import io
import json
import unittest

import eiem_release


class ReleaseCheckTests(unittest.TestCase):
    def test_new_latest_and_ignored(self):
        payload = {"tag_name": "v0.35.0", "html_url":
                   "https://github.com/ssice-a/EFF-blender/releases/tag/v0.35.0"}

        def opener(request, timeout):
            self.assertEqual(timeout, 5)
            return io.BytesIO(json.dumps(payload).encode())

        self.assertEqual(eiem_release.check_release((0, 34, 0), opener=opener)["status"],
                         "available")
        self.assertEqual(eiem_release.check_release((0, 34, 0), "v0.35.0",
                                                    opener=opener)["status"], "ignored")
        self.assertEqual(eiem_release.check_release((0, 34, 0), "v0.35.0",
                                                    force=True, opener=opener)["status"],
                         "available")
        self.assertEqual(eiem_release.check_release((0, 35, 0), opener=opener)["status"],
                         "latest")

    def test_invalid_release_is_rejected(self):
        with self.assertRaises(ValueError):
            eiem_release.parse_version("v0.35.0-beta")
        payload = {"tag_name": "v0.35.0", "html_url": "https://example.com/other"}
        with self.assertRaises(ValueError):
            eiem_release.check_release((0, 34, 0), opener=lambda *_args, **_kwargs:
                                       io.BytesIO(json.dumps(payload).encode()))


if __name__ == "__main__":
    unittest.main()
