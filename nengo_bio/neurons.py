#   nengo_bio -- Extensions to Nengo for more biological plausibility
#   Copyright (C) 2019  Andreas Stöckel
#
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation, either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program.  If not, see <https://www.gnu.org/licenses/>.

import numpy as np

from nengo.exceptions import ValidationError
from nengo.neurons import NeuronType
from nengo.params import IntParam, NumberParam

from nengo_bio.common import Excitatory, Inhibitory
from nengo_bio.internal import (lif_utils, multi_compartment_lif_parameters)


class MultiChannelNeuronType(NeuronType):
    inputs = ()

    def step_math(self, dt, J, output):
        """
        The step_math function is not used by MultiInputNeuron instances. This
        is mainly to prevent mixing of MultiInputNeuron instances with standard
        Nengo ensembles.
        """
        raise RuntimeError(
            "Neurons of type can only be used in conjunction with"
            "nengo_bio.Connection")

    @property
    def n_inputs(self):
        return len(self.inputs)

    def tune(self, dt, model, ens):
        return None

    def compile(self, dt, n_neurons, tuning):
        """
        Generates an efficient simulator for this neuron type.

        Returns a callable "step_math" function. The "step_math" function
        receives the current input and returns the output (whether a neuron
        spiked or not), as well as the current state.

        Parameters
        ----------
        dt : float
            The timestep that is going to be used in the simulation. Since this
            parameter is known ahead of time it can be treated as a constant
            expression, potentially increasing performance.
        n_neurons : int
            The number of neurons for which the function should be compiled.
        """
        raise NotImplementedError(
            "MultiInputNeuronType must implement the \"compile\" function.")


class LIF(MultiChannelNeuronType):
    """
    A standard single-compartment LIF neuron with separate exciatory and
    inhibitory inputs.
    """

    inputs = (Excitatory, Inhibitory)

    C_som = NumberParam('C', low=0, low_open=True)
    g_leak_som = NumberParam('g_leak', low=0, low_open=True),

    tau_spike = NumberParam('tau_spike', low=0)
    tau_ref = NumberParam('tau_ref', low=0)

    v_spike = NumberParam('v_spike')
    v_reset = NumberParam('v_reset')
    v_th = NumberParam('v_reset')

    E_rev_leak = NumberParam('E_leak')

    subsample = IntParam('subsample', low=1)

    def __init__(self,
                 C_som=1e-9,
                 g_leak_som=50e-9,
                 E_rev_leak=-65e-3,
                 tau_ref=2e-3,
                 tau_spike=1e-3,
                 v_th=-50e-3,
                 v_reset=-65e-3,
                 v_spike=20e-3,
                 subsample=10):

        super(LIF, self).__init__()

        self.C_som = C_som
        self.g_leak_som = g_leak_som
        self.E_rev_leak = E_rev_leak
        self.tau_ref = tau_ref
        self.tau_spike = tau_spike
        self.v_th = v_th
        self.v_reset = v_reset
        self.v_spike = v_spike
        self.subsample = subsample

    def threshold_current(self):
        """
        Returns the input current at which the neuron is supposed to start
        spiking.
        """
        return (self.v_th - self.E_rev_leak) * self.g_leak_som

    def _lif_parameters(self):
        """
        Returns the LIF parameters of the somatic compartments. These parameters
        are used in the gain/bias computations.
        """
        tau_ref = self.tau_spike + self.tau_ref
        tau_rc = self.C_som / self.g_leak_som
        i_th = self.threshold_current()
        return tau_ref, tau_rc, i_th

    def _lif_rate(self, J):
        """
        Returns the LIF rate for a given input current.
        """
        tau_ref, tau_rc, i_th = self._lif_parameters()
        return lif_utils.lif_rate(J / i_th, tau_ref, tau_rc)

    def _lif_rate_inv(self, a):
        """
        Returns the input current resulting in the given rate.
        """
        tau_ref, tau_rc, i_th = self._lif_parameters()
        return lif_utils.lif_rate_inv(a, tau_ref, tau_rc) * i_th

    def gain_bias(self, max_rates, intercepts):
        # Make sure the input is a 1D array
        max_rates = np.array(max_rates, dtype=float, copy=False, ndmin=1)
        intercepts = np.array(intercepts, dtype=float, copy=False, ndmin=1)

        # Make sure the maximum rates are not surpassing the maximally
        # attainable rate
        tau_ref, _, i_th = self._lif_parameters()
        inv_tau_ref = 1. / tau_ref if tau_ref > 0. else np.inf
        if np.any(max_rates > inv_tau_ref):
            raise ValidationError(
                "Max rates must be below the inverse "
                "of the sum of the refractory and spike "
                "period ({:0.3f})".format(inv_tau_ref),
                attr='max_rates',
                obj=self)

        # Solve the following linear system for gain, bias
        #   i_th  = gain * intercepts + bias
        #   i_max = gain              + bias
        i_max = self._lif_rate_inv(max_rates)
        gain = (i_max - i_th) / (1. - intercepts)
        bias = i_max - gain

        return gain, bias

    def max_rates_intercepts(self, gain, bias):
        # The max rate is defined as the rate for the input current gain + bias
        max_rates = self._lif_rate(gain + bias)

        # Solve i_th = gain * intercept + bias for the intercept; warn about
        # invalid values
        intercepts = (self.threshold_current() - bias) / gain
        if not np.all(np.isfinite(intercepts)):
            warnings.warn("Non-finite values detected in `intercepts`; this "
                          "probably means that `gain` was too small.")

        return max_rates, intercepts

    def rates(self, x, gain, bias):
        return self._lif_rate(gain * x + bias)

    def _params_som(self):
        return multi_compartment_lif_parameters.SomaticParameters(
            tau_ref=self.tau_ref,
            tau_spike=self.tau_spike,
            v_th=self.v_th,
            v_reset=self.v_reset,
            v_spike=self.v_spike,
        )

    def _compile(self, dt, n_neurons, params_den, force_python_sim):
        # Either instantiate the C++ simulator or the reference simulator
        import nengo_bio.internal.multi_compartment_lif_cpp as mcl_cpp
        import nengo_bio.internal.multi_compartment_lif_python as mcl_python
        params_som = self._params_som()
        if force_python_sim or not mcl_cpp.supports_cpp():
            sim_class = mcl_python.compile_simulator_python(
                params_som, params_den, dt=dt, ss=self.subsample)
        else:
            sim_class = mcl_cpp.compile_simulator_cpp(
                params_som, params_den, dt=dt, ss=self.subsample)

        # Create a new simulator instance and wrap it in a function
        return sim_class(n_neurons).step_math

    def compile(self, dt, n_neurons, tuning=None, force_python_sim=False):
        params_den = multi_compartment_lif_parameters.DendriticParameters.\
            make_lif(
            C_som=self.C_som,
            g_leak_som=self.g_leak_som,
            E_rev_leak=self.E_rev_leak)

        return self._compile(dt, n_neurons, params_den, force_python_sim)


class TwoCompLIF(LIF):
    """
    A two-compartment LIF neuron with conductance based synapses.

    A TwoCompLIF neuron consists of a somatic as well as a dendritic
    compartment.
    """

    inputs = (Excitatory, Inhibitory)

    C_den = NumberParam('c_den', low=0, low_open=True)

    g_leak_den = NumberParam('g_leak_den', low=0, low_open=True)
    g_couple = NumberParam('g_couple', low=0, low_open=True)

    E_rev_exc = NumberParam('E_exc')
    E_rev_inh = NumberParam('E_inh')

    subsample = IntParam('subsample', low=1)

    def __init__(self,
                 C_som=1e-9,
                 C_den=1e-9,
                 g_leak_som=50e-9,
                 g_leak_den=50e-9,
                 g_couple=50e-9,
                 E_rev_leak=-65e-3,
                 E_rev_exc=20e-3,
                 E_rev_inh=-75e-3,
                 tau_ref=2e-3,
                 tau_spike=1e-3,
                 v_th=-50e-3,
                 v_reset=-65e-3,
                 v_spike=20e-3,
                 subsample=10):

        super(TwoCompLIF, self).__init__(
            C_som=C_som,
            g_leak_som=g_leak_som,
            E_rev_leak=E_rev_leak,
            tau_ref=tau_ref,
            tau_spike=tau_spike,
            v_th=v_th,
            v_reset=v_reset,
            v_spike=v_spike,
            subsample=subsample)

        self.C_den = C_den
        self.g_leak_den = g_leak_den
        self.g_couple = g_couple
        self.E_rev_exc = E_rev_exc
        self.E_rev_inh = E_rev_inh

    def compile(self, dt, n_neurons, tuning=None, force_python_sim=False):
        # Create the parameter arrays describing this particular multi
        # compartment LIF neuron
        params_den = multi_compartment_lif_parameters.DendriticParameters.\
            make_two_comp_lif(
            C_som=self.C_som,
            C_den=self.C_den,
            g_leak_som=self.g_leak_som,
            g_leak_den=self.g_leak_den,
            g_couple=self.g_couple,
            E_rev_leak=self.E_rev_leak,
            E_rev_exc=self.E_rev_exc,
            E_rev_inh=self.E_rev_inh
        )

        return self._compile(dt, n_neurons, params_den, force_python_sim)

