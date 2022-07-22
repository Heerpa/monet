"""
    monet/tests/test_analysis.py
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    Test the analysis module of monet.

    :authors: Heinrich Grabmayr, 2022
    :copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""
import unittest
import monet.analysis as man
import numpy as np


class TestAnalysis(unittest.TestCase):

    def setUp(self):
        config = {
            'min': 30,
            'max': 100,
            'step': 5,}
        self.att = man.SinusAttenuationCurveAnalyzer(config)

    def tearDown(self):
        pass

    def test_01_SinusAnalyser(self):
        config = {
            'min': 30,
            'max': 100,
            'step': 5,}
        att = man.SinusAttenuationCurveAnalyzer(config)

    def test_02_Sin_model_fun(self):
        model = {
            'bkg': 0,
            'amp': 50,
            'phi': 30,
            'start': 10,
            'step': 5,
            'stop': 100}
        x = np.arange(model['start'], model['stop'], model['step'])
        print('x in ', x)

        pwr = self.att._model_function(
            x, model['bkg'], model['amp'], model['phi'])
        self.att._model_function(
            90, model['bkg'], model['amp'], model['phi'])

        print('pwr', pwr)

        x_back = self.att._model_function_inv(
            pwr, model['bkg'], model['amp'], model['phi'],
            mini=0, maxi=100)
        self.att._model_function_inv(
            .5, model['bkg'], model['amp'], model['phi'],
            mini=0, maxi=100)
        with self.assertRaises(ValueError) as context:
            self.att._model_function_inv(
                2*(model['amp']+model['bkg']), model['bkg'],
                model['amp'], model['phi'],
                mini=0, maxi=100)
        self.assertTrue('out of range.' in str(context.exception))
        with self.assertRaises(ValueError) as context:
            self.att._model_function_inv(
                2*pwr, model['bkg'],
                model['amp'], model['phi'],
                mini=0, maxi=100)
        self.assertTrue('out of range.' in str(context.exception))


        print('x back', x_back)

        initpars = self.att._model_function_estinit(pwr, x)
        print('estimated init pars')
        print(initpars)
        print('config with init pars', model)
        assert True

    def test_02_PointAnalyzer(self):
        config = {}
        att = man.PointCurveAnalyzer(config)

        power = 8
        att.fit(0, power)

        assert att.estimate_power(0) == power
        assert att.estimate_power(9) == power
        assert att.estimate(9) == 0
