#!/usr/bin/env python3

import nengo
import nengo_bio as bio
import numpy as np

with nengo.Network() as model:
    inp_a = nengo.Node(0)
    inp_b = nengo.Node(0)

    ens_a1 = bio.Ensemble(n_neurons=101, dimensions=1, p_exc=0.0)
    ens_a2 = bio.Ensemble(n_neurons=102, dimensions=1, p_inh=1.0)
    ens_b = bio.Ensemble(n_neurons=103, dimensions=1, p_exc=0.8)
    ens_c = bio.Ensemble(n_neurons=104, dimensions=2)

    nengo.Connection(inp_a, ens_a1)
    nengo.Connection(inp_a, ens_a2)
    nengo.Connection(inp_b, ens_b)

    bio.Connection(({ens_a1, ens_a2}, ens_b), ens_c,
                    function=lambda x: (x[0] ** 2, np.mean(x)))

with nengo.Simulator(model) as sim:
    sim.run(1.0)
