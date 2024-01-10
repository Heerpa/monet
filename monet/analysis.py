#!/usr/bin/env python
"""
    monet/analysis.py
    ~~~~~~~~~~~~~~~~~

    Analysis of attenuation curves.

    :authors: Heinrich Grabmayr, 2022
    :copyright: Copyright (c) 2022 Jungmann Lab, MPI of Biochemistry
"""
import logging
from icecream import ic
import abc
from collections.abc import Iterable
import time

import lmfit
import numpy as np
from numpy.polynomial import Polynomial as _Polynomial
import matplotlib.pyplot as plt


logger = logging.getLogger(__name__)
ic.configureOutput(outputFunction=logger.debug)

class AbstractAttenuationCurveAnalyzer(abc.ABC):
    """An abstract class for analyzing attenuation curves.

    Attenuation curves (Power vs control parameter) are expected to have
    specific shapes (e.g. sinusoidal for Polarization-Rotation attenuation)
    or can be fit with a spline.
    An AttenuationCurveAnalyzer fits a model to calibration data, and then
    uses that to return a control parameter for a desired power output.
    """
    def __init__(self, analysis_parameters):
        """
        Args:
            analysis_parameters : dict
                min : float
                    minimum control parameter (angle, ..)
                max : float
                    maximum control parameter (angle, ..)
        """
        self.model = lmfit.Model(self._model_function)
        self.analysis_parameters = analysis_parameters

    @abc.abstractmethod
    def _model_function(x, **pars):
        """A fitting model function
        """
        pass

    @abc.abstractmethod
    def _model_function_inv(y, **pars):
        """The inverse of the model function
        """
        pass

    @abc.abstractmethod
    def _model_function_estinit(x, y):
        """estimate inital parameters for the model function
        """
        pass

    @abc.abstractmethod
    def output_range(self):
        pass

    def fit(self, x, y):
        """Fit a model from calibration data

        Args:
            y : scalar or 1d array
                desired power output
            x : numeric, same shape as y
                the control parameters (e.g. angle) corresponding to y
                using the current model
        """
        init_pars = self._model_function_estinit(y, x)
        self.fit_result = self.model.fit(y, x=x, verbose=False, **init_pars)
        self.curr_params = self.get_model()

    def estimate(self, y):
        """Estimate control parameter needed to reach a given power.

        Args:
            y : scalar or 1d array
                desired power output

        Returns:
            x : numeric, same shape as y
                the control parameters (e.g. angle) corresponding to y
                using the current model
        """
        pars = self.curr_params
        minimax = {
            'mini': self.analysis_parameters['min'],
            'maxi': self.analysis_parameters['max']}
        return self._model_function_inv(y, **pars, **minimax)

    def estimate_power(self, x):
        """Estimate power for a given control parameter.

        Args:
            x : scalar or 1d array
                angular value

        Returns:
            y : numeric, same shape as y
                estimated power output
        """
        if isinstance(self.curr_params, dict):
            return self.model.eval(**self.curr_params, x=x)
        else:
            return self.model.eval(self.curr_params, x=x)

    def get_model(self):
        """Return current model parameters

        Returns:
            model_parameters : dict
                the model parameters
        """
        if hasattr(self, 'fit_result'):
            return self.fit_result.params.valuesdict()
        else:
            return self.curr_params

    def load_model(self, model_parameters):
        """Load a model from parameters

        Args:
            parameters : dict
                the model parameters
        """
        self.model.make_params(**model_parameters)
        self.curr_params = model_parameters

    def plot(self, fname, xlabel=None, ylabel=None, title=None):
        """Plot the outcome of the analysis

        Args:
            fname : string
                the file name to save the plot at.

        """
        # there was a QT error on voyager (220726) - avoid it by using tkagg
        import matplotlib
        matplotlib.use('tkagg')
        # fig, ax = plt.subplots()
        # print('abstract plotting with', xlabel, ylabel, title)
        # print('filename', fname)
        fig = self.fit_result.plot(
            show_init=False, xlabel=xlabel, ylabel=ylabel, title=title)
        fig.savefig(fname)
        plt.close(fig)


class SinusAttenuationCurveAnalyzer(AbstractAttenuationCurveAnalyzer):
    """Model is sinusoidal, with polarization angle twice the wave plate anlge:
    P(alpha) = bkg + amp * sin(2*(alpha+phi))**2

    In- and Output
    P : power
    alpha : angle of half wave plate

    Model Parameters:
    bkg : background power
    amp : amplitude
    phi : offset rotation of half wave plate
    """
    def __init__(self, analysis_parameters):
        """
        Args:
            analysis_parameters : dict
                min : float
                    minimum control parameter (angle, ..)
                max : float
                    maximum control parameter (angle, ..)
        """
        super().__init__(analysis_parameters)

    def _model_function(self, x, bkg, amp, phi):
        """Squared sinus function with twice the angle, background and offset

        sin**2(alpha) = (1+sin(2*alpha))/2

        P(alpha) = bkg + amp * sin(2*(alpha+phi))**2
                 = bkg + amp * (1 + sin(4*pi/180(alpha+phi)))/2
        Args:
            alpha : float or array
                input angle, in deg
            bkg : float
                background
            amp : float
                amplitude
            phi : float
                angular offset in deg
        Returns:
            result : float or array
                the squared sinus etc.
        """
        return bkg + amp * (1+np.sin(4*np.pi/180*(x+phi)))/2

    def _model_function_inv(self, y, bkg, amp, phi, mini, maxi):
        """calculate the inverse of the squared sinus
        alpha = np.arcsin((out-bkg)/amp*2 - 1)/2*180/np.pi-phi
        """
        logger.debug('inverting squared sinus model function')
        logger.debug(f'y={str(y)}, bkg={str(bkg)}, amp={str(amp)}, phi={str(phi)}')
        if np.any(y < bkg) or np.any(y > bkg + amp):
            raise ValueError(
                'Desired value y={:s} out of range. '.format(str(y)) +
                'Should be between bkg={:s} and amp+bkg={:s}'.format(
                    str(bkg), str(bkg+amp)))
        alpha = np.arcsin((y-bkg)/amp*2 - 1)/4*180/np.pi-phi
        if isinstance(alpha, np.ndarray):
            alpha[alpha<mini] = alpha[alpha<mini]+90
            alpha[alpha>maxi] = alpha[alpha>maxi]-90
        else:
            for i in range(5):
                if alpha < mini:
                    alpha += 90
                elif alpha > maxi:
                    alpha -= 90
                else:
                    break
        return alpha

    def _model_function_estinit(self, y, x):
        """Estimate initial parameters of data given to a squared sinusoidal

        Args:
            y : array (N)
                the result data
            alpha : array (N)
                the angular data
        Returns:
            pars : dict
                keys: bkg, amp, phi
        """
        pars = {
            'bkg': np.min(y),
            'amp': np.max(y)-np.min(y),
            'phi': x[np.argmax(y)]+90/4,
        }
        self.model.make_params(pars)
        self.model.set_param_hint('bkg', min=0)
        self.model.set_param_hint('amp', min=0)
        self.model.set_param_hint('phi', min=0)
        return pars

    def output_range(self):
        """calculate the power output range within the polarizer angle range

        Returns:
            output_range : list, len 2
                [min power, max power]
        """
        params = self.get_model()
        phi_max = 180/8  # =22,5°; period 90°
        phi_min = 3/8*180
        phi_period = 90
        phi_range = [self.analysis_parameters['min'], self.analysis_parameters['max']]
        # check whether maximum is between the angle range
        next_maxphi_from_min = (
            (((phi_range[0]-phi_max)//phi_period)+1) *
             phi_period+phi_max)
        next_minphi_from_min = (
            (((phi_range[0]-phi_min)//phi_period)+1) *
             phi_period+phi_min)
        output_range = [0, 0]
        if next_maxphi_from_min < phi_range[1]:
            output_range[1] = params['bkg']+params['amp']
        else:
            output_range[1] = max([self.estimate_power(phi_range[0]),
                                   self.estimate_power(phi_range[1])])
        if next_minphi_from_min < phi_range[1]:
            output_range[0] = params['bkg']
        else:
            output_range[0] = min([self.estimate_power(phi_range[0]),
                                   self.estimate_power(phi_range[1])])
        return output_range

    def plot(self, fname, xlabel=None, ylabel=None, title=None):
        """Plot the outcome of the analysis

        Args:
            fname : string
                the file name to save the plot at.

        """
        if xlabel is None:
            xlabel = 'angle [deg]'
        # print('plotting with', xlabel, ylabel, title)
        super().plot(fname, xlabel, ylabel, title)


class LinearCurveAnalyzer(AbstractAttenuationCurveAnalyzer):
    """Model is linear:
    P(x) = bkg + amp * x

    In- and Output
    P : power
    x: set point

    Model Parameters:
    bkg : background power
    amp : amplitude
    """
    def __init__(self, analysis_parameters):
        """
        Args:
            analysis_parameters : dict
                min : float
                    minimum control parameter
                max : float
                    maximum control parameter
        """
        super().__init__(analysis_parameters)

    def _model_function(self, x, bkg, amp):
        """linear function with background and offset

        P(x) = bkg + amp * x

        Args:
            x : float or array
                input value
            bkg : float
                background
            amp : float
                amplitude
        Returns:
            result : float or array
                the output value.
        """
        return bkg + amp * x

    def _model_function_inv(self, y, bkg, amp, mini, maxi):
        """calculate the inverse
        """
        if np.any(y < bkg + amp * mini) or np.any(y > bkg + amp * maxi):
            raise ValueError(
                'Desired value y={:s} out of range. '.format(str(y)) +
                'Should be between {:s} and {:s}'.format(
                    str(bkg + amp * mini), str(bkg + amp * maxi)))
        x = (y-bkg)/amp
        return x

    def _model_function_estinit(self, y, x):
        """Estimate initial parameters of data given to a squared sinusoidal

        Args:
            y : array (N)
                the result data
            x : array (N)
                the input data
        Returns:
            pars : dict
                keys: bkg, amp
        """
        pars = {
            'bkg': np.min(y),
            'amp': np.max(y)-np.min(y),
        }
        self.model.make_params(pars)
        self.model.set_param_hint('bkg', min=0)
        self.model.set_param_hint('amp', min=0)
        return pars

    def output_range(self):
        raise NotImplementedError()

    def plot(self, fname, xlabel=None, ylabel=None, title=None):
        """Plot the outcome of the analysis

        Args:
            fname : string
                the file name to save the plot at.

        """
        if xlabel is None:
            xlabel = 'x'
        print('plotting with', xlabel, ylabel, title)
        super().plot(fname, xlabel, ylabel, title)


class PointCurveAnalyzer(AbstractAttenuationCurveAnalyzer):
    """If not attenuator is connected, use this point analyzer, which
    will always return the one calibrated value.

    model: P(x) = amp
    """
    def __init__(self, analysis_parameters):
        analysis_parameters['min'] = np.nan
        analysis_parameters['min'] = np.nan
        super().__init__(analysis_parameters)

    def fit(self, x, y):
        """Fit a model from calibration data

        Args:
            y : scalar or 1d array
                desired power output
            x : numeric, same shape as y
                For a PointCurveAnalyzer, the x value does not make sense
                and is ignored. It is only kept for consistency of use
                of different analyzers.
        """
        if isinstance(y, Iterable):
            y = np.mean(y)
        self.curr_params = {'amp': y}

    def output_range(self):
        return [self.curr_params['amp'], self.curr_params['amp']]

    def estimate(self, y):
        """Estimate control parameter needed to reach a given power.
        For the PointCurveAnalyzer, there is no relevant control
        parameter, so return zeros.

        Args:
            y : scalar or 1d array
                desired power output

        Returns:
            x : numeric, same shape as y
                the control parameters (e.g. angle) corresponding to y
                using the current model
        """
        if isinstance(y, Iterable):
            x = np.zeros_like(y)
        else:
            x = 0
        return x

    def estimate_power(self, x):
        """Estimate power for a given control parameter.

        Args:
            x : scalar or 1d array
                angular value

        Returns:
            y : numeric, same shape as y
                estimated power output
        """
        if isinstance(x, Iterable):
            return self.curr_params['amp'] * np.ones_like(x)
        else:
            return self.curr_params['amp']

    def plot(self, fname, xlabel=None, ylabel=None, title=None):
        logger.debug('PointCurveAnalyzer does not plot.')

    def _model_function(self, x, bkg, amp):
        """constant function

        P(x) = amp

        Args:
            x : float or array
                input value
            amp : float
                amplitude
        Returns:
            result : float or array
                the output value.
        """
        if isinstance(x, Iterable):
            return amp + np.ones_like(x)
        else:
            return amp

    def _model_function_inv(self, y, amp):
        """calculate the inverse
        """
        return y

    def _model_function_estinit(self, y, x):
        """Estimate initial parameters of data given to a squared sinusoidal

        Args:
            y : array (N)
                the result data
            x : array (N)
                the input data
        Returns:
            pars : dict
                keys: bkg, amp
        """
        pars = {
            'amp': np.mean(y),
        }
        return pars


class PolynomAttenuationCurveAnalyzer(AbstractAttenuationCurveAnalyzer):
    """Model is a polynomial fit.
    P(alpha) = pn[0] * ap**deg + pn[1] * ap**(deg-1) + ... + pn[deg]

    In- and Output
    P : power
    ap / x : power setting of the AOTF (input value)

    Model Parameters:
    pn : polynomial fit parameters. Array, len deg+1
    deg : degree of polynomial fitting
    """
    def __init__(self, analysis_parameters):
        """
        Args:
            analysis_parameters : dict
                min : float
                    minimum control parameter (angle, ..)
                max : float
                    maximum control parameter (angle, ..)
                deg : int
                    the degree of polynomial fitting
        """
        self.poly = None
        self.polinv = None
        if 'polydegree' not in analysis_parameters:
            analysis_parameters['polydegree'] = 10
        super().__init__(analysis_parameters)

    def _model_function(self, x, pn):
        """Squared sinus function with twice the angle, background and offset

        Args:
            x : float or array
                input variable, AOTF power [dB]
            pn : array of float
                polynomial parameters
        Returns:
            result : float or array
                the evaluation of the polynomial at x
        """
        if self.poly is None:
            self.poly = _Polynomial(pn)
        y = self.poly(x)
        return y

    def _model_function_inv(self, y, pn, mini, maxi):
        """calculate the inverse
        """
        if self.polinv is not None:
            x = self.polinv(y)
        else:
            raise ValueError('At this point the inverse Polznomial should be defined.')
            x = _Polynomial(pn)(y)
        if x < mini:
            x = mini
        elif x > maxi:
            x = maxi
        return x

    def _model_function_estinit(self, y, x):
        """Estimate initial parameters of data given to a squared sinusoidal

        Args:
            y : array (N)
                the result data
            alpha : array (N)
                the angular data
        Returns:
            pars : dict
                keys: bkg, amp, phi
        """
        raise NotImplementedError()

    def output_range(self):
        """calculate the power output range within input parameter range

        Returns:
            output_range : list, len 2
                [min power, max power]
        """
        end_vals = [
            np.real(self.poly(self.analysis_parameters['min'])),
            np.real(self.poly(self.analysis_parameters['max'])),
            ]
        extremes = self.poly.deriv().roots()
        extremes = [
            e for e in extremes
            if (np.isreal(e) and
                e > self.analysis_parameters['min'] and
                e < self.analysis_parameters['max'])]
        extreme_vals = [np.real(self.poly(e)) for e in extremes]
        output_range = [
            min(end_vals+extreme_vals),
            max(end_vals+extreme_vals),
            ]
        return output_range

    def fit(self, x, y):
        """Fit a model from calibration data. crop to x-range and
        between maxima to make the relationship bijective.

        Args:
            y : scalar or 1d array
                desired power output
            x : numeric, same shape as y
                the control parameters (e.g. angle) corresponding to y
                using the current model
        """
        inxrange = np.argwhere(
            (x >= self.analysis_parameters['min']) &
            (x <= self.analysis_parameters['max']))
        x = x[inxrange].flatten()
        y = y[inxrange].flatten()
        self.fitvals_forward = {
            'x': x,
            'y': y}
        win_x = [min(x), max(x)]
        self.poly = _Polynomial.fit(
                x, y, self.analysis_parameters['polydegree'],
                window=win_x, domain=win_x)
        idxyextremepos = np.argmin(y), np.argmax(y)
        if idxyextremepos[0] < idxyextremepos[1]:
            idx = range(idxyextremepos[0], idxyextremepos[1])
        else:
            idx = range(idxyextremepos[1], idxyextremepos[0])
        x = x[idx]
        y = y[idx]

        self.fitvals_backward = {
            'x': x,
            'y': y}
        win_y = [min(y), max(y)]
        self.polinv = _Polynomial.fit(
            y, x, int(1.5 * self.analysis_parameters['polydegree']),
            window=win_y, domain=win_y)
        self.curr_params = self.coef2params(self.poly.coef, self.polinv.coef)

    def estimate(self, y):
        """Estimate control parameter needed to reach a given power.

        Args:
            y : scalar or 1d array
                desired power output

        Returns:
            x : numeric, same shape as y
                the control parameters (e.g. angle) corresponding to y
                using the current model
        """
        coef_fw, coef_bw = self.params2coef(self.curr_params)
        minimax = {
            'mini': self.analysis_parameters['min'],
            'maxi': self.analysis_parameters['max']}
        return self._model_function_inv(y, coef_bw, **minimax)

    def estimate_power(self, x):
        """Estimate power for a given control parameter.

        Args:
            x : scalar or 1d array
                angular value

        Returns:
            y : numeric, same shape as y
                estimated power output
        """
        if self.poly is not None and x is not None:
            return self.poly(x)
        else:
            return 0

    def get_model(self):
        """Return current model parameters

        Returns:
            model_parameters : dict
                the model parameters
        """
        if self.poly is not None:
            return self.coef2params(self.poly.coef, self.polinv.coef)
        else:
            return self.curr_params

    def coef2params(self, coef, coef_inv):
        """Convert the Polynomial coefficients to a parameter set
        Args:
            coef : np array
                the polynomial coefficients
            coef_inv : np array
                the coefficients of the inverse polynomial
        Returns:
            params : dict
                the coefficients as a dict, with keys p0, p1, ..
        """
        params = {'p{:d}'.format(i): c for i, c in enumerate(coef)}
        for i, c in enumerate(coef_inv):
            params['i{:d}'.format(i)] = c
        return params

    def params2coef(self, params):
        """Convert a parameter set to the Polynomial coefficients
        Args:
            params : dict
                the coefficients as a dict, with keys p0, p1, ..
        Returns:
            coef_fw : np array
                the polynomial coefficients
            coef_bw : np array
                the coefficients of the inverse polynomial
        """
        n_fw = len([1 for k in list(params.keys()) if 'p' in k])
        n_bw = len([1 for k in list(params.keys()) if 'i' in k])
        coef_fw = np.array([
            params['p{:d}'.format(i)]
            for i in range(n_fw)])
        coef_fw[np.isnan(coef_fw)] = 0
        coef_bw = np.array([
            params['i{:d}'.format(i)]
            for i in range(n_bw)])
        coef_bw[np.isnan(coef_bw)] = 0
        return coef_fw, coef_bw

    def load_model(self, model_parameters):
        """Load a model from parameters

        Args:
            parameters : dict
                the model parameters
                keys: 'p0', 'p1', ...
        """
        coef_fw, coef_bw = self.params2coef(model_parameters)
        self.poly = _Polynomial(coef_fw)
        self.polinv = _Polynomial(coef_bw)
        self.curr_params = model_parameters

    def plot(self, fname, xlabel=None, ylabel=None, title=None):
        """Plot the outcome of the analysis

        Args:
            fname : string
                the file name to save the plot at.

        """
        if xlabel is None:
            xlabel = 'input variable (e.g. power[dBm])'
        if ylabel is None:
            ylabel = 'Beam Power [mW]'
        # print('plotting with', xlabel, ylabel, title)
        import matplotlib
        matplotlib.use('tkagg')
        # fig, ax = plt.subplots()
        # print('abstract plotting with', xlabel, ylabel, title)
        # print('filename', fname)
        fig, ax = plt.subplots()
        xmock = np.linspace(
            self.analysis_parameters['min'],
            self.analysis_parameters['max'],
            num=50)
        if (hasattr(self, 'fitvals_forward')):
            ax.plot(self.fitvals_forward['x'], self.fitvals_forward['y'],
                color='b', linestyle='None', marker='+', label='data used forward')
            ax.plot(self.fitvals_backward['x'], self.fitvals_backward['y'],
                color='r', linestyle='None', marker='+', label='data used inv')
            ymock = np.linspace(
                np.min(self.fitvals_backward['y']),
                np.max(self.fitvals_backward['y']), num=50)
        else:
            ymock = np.linspace(
                self.poly(np.min(self.analysis_parameters['min'])),
                self.poly(np.max(self.analysis_parameters['min'])), num=50)
        ax.plot(
            xmock, self.poly(xmock), color='b', linestyle='-',
            label='fit forward')
        ax.plot(self.polinv(ymock), ymock,
            color='r', linestyle='-', label='fit inverse')
        ax.legend()
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        fig.savefig(fname)
        plt.close(fig)


def test_PolynomAttenuationCurveAnalyzer():
    x = np.arange(21)
    y = np.array([
        1, 1.5, 2, 3, 4, 5, 5.6, 5.8, 6, 6.3,
        6.4, 6.7, 8, 10, 13, 14, 14.5, 14.6, 14.4, 14,
        13])

    pars = {
      'min': 0.0,
      'max': 22.5,
      'step': .1,
      'polydegree': 6,
    }
    paca = PolynomAttenuationCurveAnalyzer(pars)
    paca.fit(x, y)
    paca.plot('testplot.png')
    print('estimating outcomes')
    val = 2
    print(val, paca.estimate_power(val))
    val = 4
    print(val, paca.estimate_power(val))
    val = 10
    print(val, paca.estimate_power(val))
    print('estimating inverse')
    val = 5
    print(val, paca.estimate(val))
    val = 10
    print(val, paca.estimate(val))
    val = 13
    print(val, paca.estimate(val))

if __name__ == '__main__':
    test_PolynomAttenuationCurveAnalyzer()