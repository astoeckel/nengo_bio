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

import nengo
import nengo_bio as bio
import numpy as np

PROBE_SYNAPSE = 0.1
T = 10.0
T_SKIP = 1.0

def run_and_compute_relative_rmse(model, probe, expected_fns):
    with nengo.Simulator(model, progress_bar=None) as sim:
        sim.run(T)

    # Fetch the time and the probe data
    ts = sim.trange()
    expected = np.array([f(ts - PROBE_SYNAPSE) for f in expected_fns]).T
    actual = sim.data[probe]

    # Compute the slice over which to compute the error
    slice_ = slice(int(T_SKIP / sim.dt), int(T / sim.dt))

    # Compute the RMSE and the RMSE
    rms = np.sqrt(np.mean(np.square(expected)))
    rmse = np.sqrt(np.mean(np.square(expected[slice_] - actual[slice_])))

    return rmse / rms

def test_communication_channel():
    f1, f2 = lambda t: np.sin(t), lambda t: np.cos(t)
    with nengo.Network() as model:
        inp_a = nengo.Node(f1)
        inp_b = nengo.Node(f2)

        ens_a = bio.Ensemble(n_neurons=101, dimensions=1, p_exc=0.8)
        ens_b = bio.Ensemble(n_neurons=102, dimensions=1, p_exc=0.8)
        ens_c = bio.Ensemble(n_neurons=103, dimensions=2)

        nengo.Connection(inp_a, ens_a)
        nengo.Connection(inp_b, ens_b)

        bio.Connection((ens_a, ens_b), ens_c)

        prb_output = nengo.Probe(ens_c, synapse=PROBE_SYNAPSE)

    assert run_and_compute_relative_rmse(model, prb_output, (f1, f2)) < 0.1


def test_communication_channel_with_radius():
    f1, f2 = lambda t: np.sin(t), lambda t: np.cos(t)
    with nengo.Network() as model:
        inp_a = nengo.Node(f1)
        inp_b = nengo.Node(f2)

        ens_a = bio.Ensemble(n_neurons=101, dimensions=1, p_exc=0.8)
        ens_b = bio.Ensemble(n_neurons=102, dimensions=1, p_exc=0.8)
        ens_c = bio.Ensemble(n_neurons=103, dimensions=2, radius=2)

        nengo.Connection(inp_a, ens_a)
        nengo.Connection(inp_b, ens_b)

        bio.Connection((ens_a, ens_b), ens_c)

        prb_output = nengo.Probe(ens_c, synapse=PROBE_SYNAPSE)

    assert run_and_compute_relative_rmse(model, prb_output, (f1, f2)) < 0.1
