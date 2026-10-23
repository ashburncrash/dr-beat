import unittest

from songmatch.core import SongMatcher
from songmatch.limits import DailyBudget, RateLimiter
from songmatch.llm import SongPicker


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class LimitTests(unittest.TestCase):
    def test_rate_limiter(self):
        c = Clock()
        rl = RateLimiter(per_minute=2, per_day=3, clock=c)
        self.assertTrue(rl.allow("a") and rl.allow("a"))
        self.assertFalse(rl.allow("a"))      # 3rd within a minute
        self.assertTrue(rl.allow("b"))       # other visitor unaffected
        c.t += 61
        self.assertTrue(rl.allow("a"))
        c.t += 61
        self.assertFalse(rl.allow("a"))      # daily cap of 3
        c.t += 86400
        self.assertTrue(rl.allow("a"))

    def test_daily_budget(self):
        c = Clock()
        b = DailyBudget(2, clock=c)
        self.assertTrue(b.take() and b.take())
        self.assertFalse(b.take())
        self.assertEqual(b.remaining(), 0)
        c.t += 86400
        self.assertEqual(b.remaining(), 2)
        self.assertTrue(b.take())

    def test_picker_skips_llm_when_budget_out(self):
        class Boom:
            def complete(self, *a, **k):
                raise AssertionError("LLM must not be called")

            def describe(self):
                return "x"

        out = SongPicker(SongMatcher(), llm=Boom()).pick("he cheated on me", allow_llm=False)
        self.assertEqual(out["song"]["source"], "catalogue")
        self.assertIn("quota", out["note"])


if __name__ == "__main__":
    unittest.main()
