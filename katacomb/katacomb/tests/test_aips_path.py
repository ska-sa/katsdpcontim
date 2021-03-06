from contextlib import contextmanager
import unittest

from katacomb import obit_context, uv_factory, AIPSPath
from katacomb.aips_path import parse_aips_path, next_seq_nr


@contextmanager
def file_cleaner(paths):
    """
    Delete a list of AIPS files at both the context start and stop
    """

    if not isinstance(paths, (tuple, list)):
        paths = [paths]

    try:
        for path in paths:
            with uv_factory(aips_path=path, mode="w") as f:
                f.Zap()

        yield
    finally:
        for path in paths:
            with uv_factory(aips_path=path, mode="w") as f:
                f.Zap()


class TestAipsPath(unittest.TestCase):
    """
    Test AIPS path functionalityy
    """

    def test_next_seq_nr(self):
        """ Test finding the next highest disk sequence number of an AIPS Path """

        # Create two AIPS paths, one with with sequence number 10 and 11
        p1 = AIPSPath(name='test', disk=1, aclass="klass", seq=10)
        p2 = p1.copy(seq=p1.seq+1)

        with obit_context(), file_cleaner([p1, p2]):
            # Create the first file and test highest sequence number
            with uv_factory(aips_path=p1, mode="w"):
                pass
            self.assertEqual(next_seq_nr(p1), p1.seq+1)

            # Create the second file and test highest sequence number
            with uv_factory(aips_path=p2, mode="w"):
                pass
            self.assertEqual(next_seq_nr(p1), p1.seq+2)
            self.assertEqual(next_seq_nr(p2), p2.seq+1)

    def test_parse_aips_path(self):
        """ Test AIPS path parsing """
        attrs = ["name", "disk", "aclass", "seq", "atype", "label", "dtype"]
        test_values = ["plort", 10, "klass", 2, "UV", "alabel", "AIPS"]
        defaults = ["plort", 1, "aips", 1, "UV", "katuv", "AIPS"]

        def _test_wrapper(elements):
            """ Wrapper for testing multiple instances """
            if len(elements) == 1:
                path_str = "(%s,)" % elements[0]
            else:
                path_str = "(%s)" % ",".join(str(e) for e in elements)

            path = parse_aips_path(path_str)
            expected = elements + defaults[len(elements):]

            self.assertEqual([getattr(path, a) for a in attrs], expected)

        # Iterate through available tuple permutations
        for i in range(1, len(test_values)):
            _test_wrapper(test_values[0:i])

    def test_parse_aips_path_fail(self):
        """ Test for an invalid tuple """
        with self.assertRaises(ValueError) as cm:
            parse_aips_path("([1,2],4)")

        ex_fragment = "AIPS path should be a tuple"
        self.assertTrue(ex_fragment in str(cm.exception))
