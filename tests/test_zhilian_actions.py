from unittest import TestCase

from bosshunter.collection.capabilities import platform_supports


class CollectionOnlyPlatformCapabilityTests(TestCase):
    def test_new_platforms_are_collection_only(self):
        for platform in ("zhilian", "51job"):
            for capability in ("collect", "score", "greet"):
                self.assertTrue(platform_supports(platform, capability))
            self.assertEqual(platform_supports(platform, "deliver"), platform in {"51job", "liepin"})
            self.assertFalse(platform_supports(platform, "monitor"))
