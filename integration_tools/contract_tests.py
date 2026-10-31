"""Run D recognition policies and the direct JJT client checks."""
import unittest

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover("emotion_interaction/tests")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
