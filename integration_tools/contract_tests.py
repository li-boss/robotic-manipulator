"""Run only the implemented D interaction contract and policy checks."""
import unittest

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover("emotion_interaction/tests")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
