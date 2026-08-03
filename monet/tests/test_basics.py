"""
monet/tests/test_basics.py
~~~~~~~~~~~~~~~~~~~~~~~~~~

Test the basic functionality of monet.

:authors: Heinrich Grabmayr, 2022
:copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""

import unittest


class TestBasics(unittest.TestCase):

    def setUp(self):
        pass

    def tearDown(self):
        pass

    def test_basics_01_environment(self):
        assert True


class TestWavelengthColor(unittest.TestCase):

    @staticmethod
    def _rgb(hexstr):
        h = hexstr.lstrip("#")
        return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))

    def test_valid_hex_format(self):
        from monet.util import wavelength_to_rgb

        for nm in (405, 488, 532, 561, 640, 785):
            c = wavelength_to_rgb(nm)
            self.assertTrue(c.startswith("#") and len(c) == 7)
            r, g, b = self._rgb(c)
            for v in (r, g, b):
                self.assertTrue(0 <= v <= 255)

    def test_dominant_channel_matches_spectrum(self):
        from monet.util import wavelength_to_rgb

        # blue dominant in the blue, green in the green, red in the red
        r, g, b = self._rgb(wavelength_to_rgb(450))
        self.assertGreater(b, r)
        self.assertGreater(b, g)
        r, g, b = self._rgb(wavelength_to_rgb(520))
        self.assertGreater(g, r)
        self.assertGreater(g, b)
        r, g, b = self._rgb(wavelength_to_rgb(660))
        self.assertGreater(r, g)
        self.assertGreater(r, b)

    def test_out_of_range_and_invalid(self):
        from monet.util import wavelength_to_rgb

        # UV / IR clamp to violet / deep red without error
        self.assertTrue(wavelength_to_rgb(200).startswith("#"))
        self.assertTrue(wavelength_to_rgb(1200).startswith("#"))
        # non-numeric -> neutral grey
        self.assertEqual(wavelength_to_rgb("nope"), "#808080")
