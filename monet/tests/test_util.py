"""
monet/tests/test_util.py
~~~~~~~~~~~~~~~~~~~~~~~~~

Tests for monet.util — dynamic class loading and the MicroManager
acquisition-comment helper.

:authors: Heinrich Grabmayr, 2024
:copyright: Copyright (c) 2024 Jungmann Lab, MPI of Biochemistry
"""

import sys
import types
import unittest

import monet.util as util

# Aliased so pytest doesn't try to collect it as a test case.
from monet.attenuation import TestAttenuator as _TestAttenuator


class TestLoadClass(unittest.TestCase):

    def test_load_class_no_settings(self):
        # init_kwargs is passed as the single positional argument.
        obj = util.load_class('monet.attenuation.TestAttenuator', {})
        self.assertIsInstance(obj, _TestAttenuator)

    def test_load_class_passes_init_kwargs_as_config(self):
        config = {'min': 0, 'max': 360}
        obj = util.load_class('monet.attenuation.TestAttenuator', config)
        self.assertEqual(obj.config, config)

    def test_load_class_with_settings(self):
        # When `settings` is given it is spread as keyword arguments:
        # Met(init_kwargs, **settings). Use a stdlib type that accepts both.
        obj = util.load_class(
            'collections.OrderedDict', {'a': 1}, settings={'b': 2}
        )
        self.assertEqual(obj['a'], 1)
        self.assertEqual(obj['b'], 2)

    def test_load_class_bad_path_raises(self):
        with self.assertRaises(ModuleNotFoundError):
            util.load_class('monet.does_not_exist.Nope', {})

    def test_load_class_missing_attribute_raises(self):
        with self.assertRaises(AttributeError):
            util.load_class('monet.attenuation.NoSuchClass', {})


class _Recorder:
    """Records which teardown methods were called, optionally raising."""

    def __init__(self, methods=('close',), inner=None, raise_on=()):
        self.calls = []
        self._raise_on = set(raise_on)
        if inner is not None:
            # expose the wrapped handle under a name release_hardware knows
            self.device = inner
        for name in methods:
            setattr(self, name, self._make(name))

    def _make(self, name):
        def _fn():
            self.calls.append(name)
            if name in self._raise_on:
                raise RuntimeError('boom in ' + name)

        return _fn


class TestReleaseHardware(unittest.TestCase):

    def test_none_is_noop(self):
        util.release_hardware(None)  # must not raise

    def test_calls_close_and_disconnect(self):
        dev = _Recorder(methods=('close', 'disconnect'))
        util.release_hardware(dev)
        self.assertIn('close', dev.calls)
        self.assertIn('disconnect', dev.calls)

    def test_recurses_into_wrapped_handle(self):
        inner = _Recorder(methods=('close',))
        wrapper = _Recorder(methods=(), inner=inner)
        util.release_hardware(wrapper)
        self.assertEqual(inner.calls, ['close'])

    def test_swallows_errors(self):
        dev = _Recorder(methods=('close',), raise_on=('close',))
        util.release_hardware(dev)  # error swallowed
        self.assertEqual(dev.calls, ['close'])

    def test_no_close_methods_is_noop(self):
        # An object without any teardown methods (e.g. a Test* device).
        util.release_hardware(_TestAttenuator({}))


# ---------------------------------------------------------------------------
# A minimal in-memory stand-in for the pycromanager API surface that
# update_mm_acquisition_comment touches. Lets us exercise the
# replace-or-append logic without a running MicroManager.
# ---------------------------------------------------------------------------
_UNSET = object()


class _FakeSettings:
    def __init__(self, comment=''):
        self._comment = comment

    def comment(self, new=_UNSET):
        if new is _UNSET:
            return self._comment
        self._comment = new
        return self

    def copy_builder(self):
        return _FakeSettings(self._comment)

    def build(self):
        return self


class _FakeAcqMgr:
    def __init__(self):
        self.settings = _FakeSettings('')

    def get_acquisition_settings(self):
        return self.settings

    def set_acquisition_settings(self, settings):
        self.settings = settings


class TestUpdateMMComment(unittest.TestCase):

    def setUp(self):
        # Install a fake `pycromanager` whose Studio routes to a shared
        # manager we can inspect afterwards.
        self.mgr = _FakeAcqMgr()
        mgr = self.mgr

        class _Studio:
            def acquisitions(self):
                return mgr

        fake = types.ModuleType('pycromanager')
        fake.Studio = _Studio
        self._saved = sys.modules.get('pycromanager')
        sys.modules['pycromanager'] = fake
        self.addCleanup(self._restore)

    def _restore(self):
        if self._saved is None:
            sys.modules.pop('pycromanager', None)
        else:
            sys.modules['pycromanager'] = self._saved

    def test_appends_line(self):
        err = util.update_mm_acquisition_comment(488, 12.345, 'mW')
        self.assertIsNone(err)
        self.assertEqual(self.mgr.settings.comment(), 'Power 488nm: 12.345 mW')

    def test_replaces_existing_line_for_same_laser(self):
        util.update_mm_acquisition_comment(488, 10.0, 'mW')
        util.update_mm_acquisition_comment(488, 20.0, 'mW')
        comment = self.mgr.settings.comment()
        self.assertEqual(comment, 'Power 488nm: 20.000 mW')
        # Exactly one line for this laser — no duplicate appended.
        self.assertEqual(comment.count('Power 488nm:'), 1)

    def test_keeps_lines_for_other_lasers(self):
        util.update_mm_acquisition_comment(488, 10.0, 'mW')
        util.update_mm_acquisition_comment(561, 5.0, 'mW')
        comment = self.mgr.settings.comment()
        self.assertIn('Power 488nm: 10.000 mW', comment)
        self.assertIn('Power 561nm: 5.000 mW', comment)

    def test_optional_fields_in_line(self):
        util.update_mm_acquisition_comment(
            640, 3.5, 'mW', att_pos=12.3456, laser_pwr=100.0
        )
        comment = self.mgr.settings.comment()
        self.assertIn('@ att=12.3456', comment)
        self.assertIn('lp=100.0mW', comment)


class TestUpdateMMCommentNoPycromanager(unittest.TestCase):

    def setUp(self):
        # Ensure importing pycromanager fails so we hit the no-op path.
        self._saved = sys.modules.get('pycromanager')
        sys.modules['pycromanager'] = None  # forces ImportError on import
        self.addCleanup(self._restore)

    def _restore(self):
        if self._saved is None:
            sys.modules.pop('pycromanager', None)
        else:
            sys.modules['pycromanager'] = self._saved

    def test_noop_returns_none(self):
        # No MicroManager / pycromanager: graceful no-op.
        self.assertIsNone(util.update_mm_acquisition_comment(488, 1.0, 'mW'))


if __name__ == '__main__':
    unittest.main()
