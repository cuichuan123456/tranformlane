# Copyright (c) Facebook, Inc. and its affiliates. All Rights Reserved
"""
Modules to compute the matching cost and solve the corresponding LSAP.
"""
import torch
from scipy.optimize import linear_sum_assignment
from torch import nn

import pdb

class HungarianMatcher(nn.Module):
    """This class computes an assignment between the targets and the predictions of the network

    For efficiency reasons, the targets don't include the no_object. Because of this, in general,
    there are more predictions than targets. In this case, we do a 1-to-1 matching of the best predictions,
    while the others are un-matched (and thus treated as non-objects).
    """
    def __init__(self, cost_class: float = 1,
                 curves_weight: float = 1, lower_weight: float = 1, upper_weight: float = 1):
        # Creates the matcher  #3，5，2，2

        super().__init__()
        self.cost_class = cost_class
        threshold = 15 / 720.
        self.threshold = nn.Threshold(threshold**2, 0.)

        self.curves_weight = curves_weight
        self.lower_weight = lower_weight
        self.upper_weight = upper_weight

    @torch.no_grad()
    def forward(self, outputs, targets):  #targets[0].shape   [4,115]  len(outputs)  2
        """ Performs the matching
        """
        bs, num_queries = outputs["pred_logits"].shape[:2]  #16,7    3

        # We flatten to compute the cost matrices in a batch
        out_prob = outputs["pred_logits"].flatten(0, 1).softmax(-1)   #112,3
        tgt_ids  = torch.cat([tgt[:, 0] for tgt in targets]).long() #63

        # Compute the classification cost. Contrary to the loss, we don't use the NLL,
        # but approximate it in 1 - proba[target class].
        # The 1 is a constant that doesn't change the matching, it can be ommitted.
        cost_class = -out_prob[:, tgt_ids]

        out_bbox = outputs["pred_curves"]
        tgt_uppers = torch.cat([tgt[:, 2] for tgt in targets])
        tgt_lowers = torch.cat([tgt[:, 1] for tgt in targets])

        # # Compute the L1 cost between lowers and uppers
        cost_lower = torch.cdist(out_bbox[:, :, 0].view((-1, 1)), tgt_lowers.unsqueeze(-1), p=1)
        cost_upper = torch.cdist(out_bbox[:, :, 1].view((-1, 1)), tgt_uppers.unsqueeze(-1), p=1)

        # # Compute the poly cost
        tgt_points = torch.cat([tgt[:, 3:] for tgt in targets]) # 0~20 112
        tgt_xs = tgt_points[:, :tgt_points.shape[1] // 2]
        valid_xs = tgt_xs >= 0
        weights = (torch.sum(valid_xs, dtype=torch.float32) / torch.sum(valid_xs, dim=1, dtype=torch.float32))**0.5
        weights = weights / torch.max(weights)

        tgt_ys = tgt_points[:, tgt_points.shape[1] // 2:]
        out_polys = out_bbox[:, :, 2:].view((-1, 6))
        tgt_ys = tgt_ys.repeat(out_polys.shape[0], 1, 1)
        tgt_ys = tgt_ys.transpose(0, 2)
        tgt_ys = tgt_ys.transpose(0, 1)

        # Calculate the predicted xs
        out_xs = out_polys[:, 0] / (tgt_ys - out_polys[:, 1]) ** 2 + out_polys[:, 2] / (tgt_ys - out_polys[:, 1]) + \
                 out_polys[:, 3] + out_polys[:, 4] * tgt_ys - out_polys[:, 5]
        tgt_xs = tgt_xs.repeat(out_polys.shape[0], 1, 1)
        tgt_xs = tgt_xs.transpose(0, 2)
        tgt_xs = tgt_xs.transpose(0, 1)

        cost_polys = torch.stack([torch.sum(torch.abs(tgt_x[valid_x] - out_x[valid_x]), dim=0) for tgt_x, out_x, valid_x in zip(tgt_xs, out_xs, valid_xs)], dim=-1)
        cost_polys = cost_polys * weights

        # # Final cost matrix
        C = self.cost_class * cost_class + self.curves_weight * cost_polys + \
            self.lower_weight * cost_lower + self.upper_weight * cost_upper

        C = C.view(bs, num_queries, ).cpu()  #(16,7,62)   origin (16,7,63)

        sizes = [tgt.shape[0] for tgt in targets]  #[4, 4, 4, 3, 4,  4, 4, 4, 4, 4,  3, 4, 5, 4, 4, 3]
        #origin [4, 4, 4, 3, 3,  5, 3, 5, 5, 3,  3, 4, 3, 4, 5, 5]
        # pdb.set_trace()
        indices = [linear_sum_assignment(c[i]) for i, c in enumerate(C.split(sizes, -1))] #len()  16  origin 16
        #[(array([1, 3, 4, 6]), array([1, 3, 2, 0])), (array([1, 2, 3, 4]), array([1, 3, 0, 2])), (array([1, 2, 4, 6]), array([2, 0, 1, 3])), (array([1, 2, 6]), array([2, 0, 1])), (array([1, 2, 4, 6]), array([2, 3, 1, 0])),
        # (array([1, 2, 4, 6]), array([0, 3, 1, 2])), (array([1, 2, 4, 6]), array([0, 1, 2, 3])), (array([1, 2, 3, 6]), array([1, 3, 0, 2])), (array([1, 2, 4, 6]), array([1, 3, 0, 2])), (array([1, 2, 4, 6]),
        # array([2, 0, 1, 3])), (array([1, 4, 6]), array([1, 0, 2])), (array([1, 2, 3, 6]), array([2, 0, 3, 1])), (array([1, 2, 3, 4, 6]), array([2, 1, 0, 3, 4])),
        # (array([1, 3, 4, 6]), array([0, 3, 1, 2])), (array([0, 1, 4, 6]), array([0, 1, 2, 3])), (array([1, 2, 4]), array([1, 2, 0]))]
        #orign :  [(array([0, 2, 4, 5]), array([2, 1, 3, 0])), (array([1, 3, 4, 5]), array([2, 3, 1, 0])), (array([0, 3, 4, 6]), array([0, 1, 3, 2])), (array([0, 2, 5]), array([0, 1, 2])), (array([3, 4, 6]), array([0, 2, 1])), (array([0, 1, 3, 4, 6]),
         #  array([0, 3, 2, 1, 4])), (array([2, 3, 5]), array([0, 2, 1])), (array([0, 1, 2, 4, 6]), array([0, 2, 3, 4, 1])), (array([0, 1, 2, 3, 4]), array([4, 2, 1, 3, 0])), (array([0, 1, 5]), array([1, 2, 0])), (array([2, 3, 4]), array([2, 0, 1])), (array([0, 1, 4, 6]),
         # array([1, 0, 2, 3])), (array([0, 3, 4]), array([2, 0, 1])), (array([1, 2, 4, 5]), array([1, 3, 2, 0])), (array([0, 1, 3, 5, 6]), array([3, 2, 1, 0, 4])), (array([2, 3, 4, 5, 6]), array([3, 4, 0, 1, 2]))]
        return [(torch.as_tensor(i, dtype=torch.int64), torch.as_tensor(j, dtype=torch.int64)) for i, j in indices]


def build_matcher(set_cost_class,
                  curves_weight, lower_weight, upper_weight):  #3，5，2，2
    return HungarianMatcher(cost_class=set_cost_class,
                            curves_weight=curves_weight, lower_weight=lower_weight, upper_weight=upper_weight)
