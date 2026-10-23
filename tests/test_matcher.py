import unittest

from songmatch.core import SongMatcher, detect_themes, estimate_mood


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = SongMatcher()

    def top(self, text):
        return self.m.match(text)["results"][0]

    def test_dataset_unique(self):
        keys = [(s["title"], s["artist"]) for s in self.m.songs]
        self.assertEqual(len(keys), len(set(keys)))

    def test_themes(self):
        self.assertIn("cheating", detect_themes("he cheated on me"))
        self.assertIn("long_distance", detect_themes("we are long distance"))
        self.assertIn("situationship", detect_themes("I don't know what we are, mixed signals"))

    def test_negation(self):
        v_pos, _ = estimate_mood("I am happy")
        v_neg, _ = estimate_mood("I am not happy anymore")
        self.assertGreater(v_pos, v_neg)

    def test_cheating_match_has_theme(self):
        r = self.top("my boyfriend cheated on me and I'm furious")
        self.assertTrue({"cheating", "betrayal"} & set(self.m.analyze("cheated")["themes"]))
        self.assertIn("cheating", " ".join(r["reasons"]).lower())

    def test_engaged_is_upbeat(self):
        r = self.top("we just got engaged!! so happy")
        song = next(s for s in self.m.songs if s["title"] == r["title"])
        self.assertIn("commitment", song["themes"])
        self.assertGreater(song["valence"], 0.5)

    def test_deterministic(self):
        t = "I miss my ex so much"
        self.assertEqual(self.m.match(t)["results"], self.m.match(t)["results"])

    def test_short_input(self):
        with self.assertRaises(ValueError):
            self.m.match("  ")

    def test_safety_note(self):
        self.assertIsNotNone(self.m.match("he hits me and threatens me")["safety_note"])
        self.assertIsNone(self.m.match("we got engaged")["safety_note"])


if __name__ == "__main__":
    unittest.main()
