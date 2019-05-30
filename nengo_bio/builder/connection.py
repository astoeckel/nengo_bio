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

from nengo_bio.common import Excitatory, Inhibitory
from nengo_bio.solvers import SolverWrapper, ExtendedSolver

import nengo.builder

class BuiltConnection:
    def __init__(self):
        self.weights = {
            Excitatory: None,
            Inhibitory: None
        }
        self.pre_idx_dim_map = []
        self.pre_idx_neurons_map = []

def get_multi_ensemble_eval_points(model, mens, rng, n_eval_points=None):
    """
    This function generates the evaluation points for the given MultiEnsemble.
    """

    def choice(A, n):
        return A[rng.randint(0, A.shape[0], n)]

    if mens.operator == mens.OP_NONE:
        # Recursion base case. The MultiEnsemble encapsulates a single Ensemble
        # instance -- just return the evaluation points associated with this
        # ensemble.
        pnts = model.params[mens.objs[0]].eval_points
        pnts.setflags(write=False)
        if n_eval_points is None:
            return pnts
        return choice(pnts, n_eval_points)
    elif (mens.operator == mens.OP_STACK) or (mens.operator == mens.OP_JOIN):
        # For each MultiEnsemble object in the stack/join, fetch the evaluation
        # points associated with that MultiEnsemble. Track the maximum number
        # of evaluation points.
        pnts_per_obj, n_pnts = [None] * len(mens.objs), [0] * len(mens.objs)
        for i, obj in enumerate(mens.objs):
            pnts = get_multi_ensemble_eval_points(model, mens.objs[i], rng,
                                                  n_eval_points)
            pnts_per_obj[i] = pnts
            n_pnts[i] = pnts.shape[0]
        max_n_pnts = max(n_pnts)

        # Either select n_eval_points or the maximum number of eval points
        # as the number of evaluation points to generate.
        if n_eval_points is None:
            n_eval_points = max_n_pnts

        if mens.operator == mens.OP_STACK:
            # Write the evaluation points to a contiguous array.
            pnts = np.empty((max_n_pnts, mens.dimensions))
            d = 0
            for i, p in enumerate(pnts_per_obj):
                n_pnts, n_dims = p.shape
                if n_eval_points >= max_n_pnts:
                    # Write the points to the resulting array, fill the
                    # remaining space with randomly selected samples
                    pnts[:n_pnts, d:(d+n_dims)] = p
                    pnts[n_pnts:, d:(d+n_dims)] = \
                        choice(p, n_eval_points - n_pnts)
                else:
                    # Ranomly select n_eval_points points and write them to
                    # the target array
                    pnts[:, d:(d+n_dims)] = choice(p, n_eval_points)

                # Increment the dimension counter
                d += n_dims
            return pnts
        elif mens.operator == mens.OP_JOIN:
            # Write the evaluation points to a contiguous array and select
            # max_n_pnts of those
            return choice(np.concatenate(pnts_per_obj, axis=0), n_eval_points)


def remove_bias_current(model, ens):
    sig_post_bias = model.sig[ens.neurons]['bias']
    sig_post_in = model.sig[ens.neurons]['in']
    for i, op in enumerate(model.operators):
        if isinstance(op, nengo.builder.operator.Copy):
            if (op.src is sig_post_bias) and (op.dst is sig_post_in):
                # Delete the copy operator and instead add a reset operator
                del model.operators[i]
                model.add_op((nengo.builder.operator.Reset(sig_post_in)))
                return True
    return False


@nengo.builder.Builder.register(SolverWrapper)
def build_solver(model, solver, _, rng):
    # Fetch the high-level connection
    conn = solver.connection # Note: this is the nengo_bio.Connection object
                             # and NOT the nengo.Connection object

    # If the high-level connection object has not been built, build it
    if not conn in model.params:
        ### TODO: Move to build_connection
        model.params[conn] = built_connection = BuiltConnection()

        # Remove the bias current from the target ensemble
        if conn.decode_bias:
            remove_bias_current(model, conn.post_obj)

        # For each pre-ensemble, fetch the evaluation points and the activities
        d0, d1, n0, n1 = 0, 0, 0, 0

        N = len(conn.pre)
        eval_points_list = [None] * N
        activities_list = [None] * N
        pre_idx_dim_map = [(0, 0)] * N
        pre_idx_neurons_map = [(0, 0)] * N
        synapse_types = {
            Excitatory: [],
            Inhibitory: []
        }

        for pre_idx, pre_ in enumerate(conn.pre):
            d0, d1, n0, n1 = d1, d1 + pre_.size_out, n1, n1 + pre_.neurons.size_out
            if conn.eval_points is None:
                eval_points = model.params[pre_].eval_points.view()
                eval_points.setflags(write=False)
            else:
                eval_points = conn.eval_points[:, d0:d1]

            built_pre_ens = model.params[pre_]
            synapse_types[Excitatory].append(
                built_pre_ens.synapse_types[Excitatory])
            synapse_types[Inhibitory].append(
                built_pre_ens.synapse_types[Inhibitory])

            activities = nengo.builder.ensemble.get_activities(
                model.params[pre_], pre_, eval_points)

            eval_points_list[pre_idx] = eval_points
            activities_list[pre_idx] = activities
            pre_idx_dim_map[pre_idx] = (d0, d1)
            pre_idx_neurons_map[pre_idx] = (n0, n1)

        # Make sure each pre-population has the same number of evaluation points
        if len(set(map(lambda x: x.shape[0], eval_points))) > 1:
            raise nengo.exceptions.BuildError(
                "The number of evaluation points must be the same for all " +
                "pre-objects in connection {}".format(conn))

        # Build the evaluation points and activities encompassing all source
        # ensembles
        eval_points = np.concatenate(eval_points_list, axis=1)
        activities = np.concatenate(activities_list, axis=1)
        synapse_types = np.array((
            np.concatenate(synapse_types[Excitatory]),
            np.concatenate(synapse_types[Inhibitory])), dtype=np.bool)

        # Fetch the target values in representation space
        targets = nengo.builder.connection.get_targets(conn, eval_points)

        # Transform the target values
        if not isinstance(conn.transform, nengo.connection.Dense):
            raise nengo.exceptions.BuildError(
                "Non-compositional solvers only work with Dense transforms")
        transform = conn.transform.sample(rng=rng)
        targets = np.dot(targets, transform.T)

        # For the target population, fetch the gains and biases
        built_post_ens = model.params[conn.post_obj]
        encoders = built_post_ens.encoders
        gain = built_post_ens.gain
        bias = built_post_ens.bias

        # Compute the target currents
        target_currents = (targets @ encoders.T) * gain
        if conn.decode_bias:
            target_currents += bias

        # LIF neuron model parameters
        WE, WI = solver(activities, target_currents, synapse_types, rng)

#        RMS = np.sqrt(np.mean(np.square(target_currents)))
#        RMSE = np.sqrt(np.mean(np.square(target_currents -
#               (activities @ WE - activities @ WI))))
#        print(conn.label, RMS, RMSE / RMS, np.mean(WE + WI))

        built_connection.weights[Excitatory] =  WE
        built_connection.weights[Inhibitory] = -WI
        built_connection.pre_idx_dim_map = pre_idx_dim_map
        built_connection.pre_idx_neurons_map = pre_idx_neurons_map
    else:
        built_connection = model.params[conn]

    n_neurons_pre = conn.pre_obj[solver.pre_idx].neurons.size_out
    n_neurons_post = conn.post_obj.neurons.size_in

    bc = built_connection
    n0, n1 = bc.pre_idx_neurons_map[solver.pre_idx]
    W = np.copy(bc.weights[solver.synapse_type][n0:n1].T)

    return None, W, None
