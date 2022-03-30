#!/usr/bin/env python
# -*- coding: utf-8 -*-
# ==============================================================================
#          \file   losses.py
#        \author   chenghuige
#          \date   2018-11-01 17:09:04.464856
#   \Description
# ==============================================================================

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import sys
import os
from turtle import forward

import torch
from torch import nn
import torch.nn.functional as F


class BiLMCriterion(object):

  def __init__(self):
    self.loss_fn = torch.nn.CrossEntropyLoss(reduction='sum')

  def forward(self, model, x, y, training=False):
    fw_y = torch.zeros_like(y)
    bw_y = torch.zeros_like(y)
    # Notice tf not support item assignment, even eager
    fw_y[:, 0:-1] = y[:, 1:]
    bw_y[:, 1:] = y[:, 0:-1]

    # print(fw_y)
    # print(bw_y)

    num_targets = torch.sum((fw_y > 0).long())

    fw_mask = fw_y > 0
    bw_mask = bw_y > 0

    # -1 to ignore padding index 0
    fw_y = fw_y.masked_select(fw_mask) - 1
    bw_y = bw_y.masked_select(bw_mask) - 1

    y_ = model.encode(x, training=training)
    fw_y_, bw_y_ = y_.chunk(2, -1)

    fw_y_ = fw_y_.masked_select(fw_mask.unsqueeze(-1)).view(-1, model.num_units)
    bw_y_ = bw_y_.masked_select(bw_mask.unsqueeze(-1)).view(-1, model.num_units)

    fw_y_ = model.encode.hidden2tag(fw_y_)
    bw_y_ = model.encode.hidden2tag(bw_y_)

    if num_targets > 0:
      fw_loss = self.loss_fn(fw_y_, fw_y)
      bw_loss = self.loss_fn(bw_y_, bw_y)
      loss = (fw_loss + bw_loss) / 2.
      #print(y.shape, num_targets, fw_loss, bw_loss, loss)
      loss = loss / num_targets.float()
    else:
      loss = torch.tensor(0.0).cuda()

    return loss


def compute_kl_loss(p, q, pad_mask=None):

  p_loss = F.kl_div(F.log_softmax(p, dim=-1),
                    F.softmax(q, dim=-1),
                    reduction='none')
  q_loss = F.kl_div(F.log_softmax(q, dim=-1),
                    F.softmax(p, dim=-1),
                    reduction='none')

  # pad_mask is for seq-level tasks
  if pad_mask is not None:
    p_loss.masked_fill_(pad_mask, 0.)
    q_loss.masked_fill_(pad_mask, 0.)

  # You can choose whether to use function "sum" and "mean" depending on your task
  p_loss = p_loss.sum()
  q_loss = q_loss.sum()

  loss = (p_loss + q_loss) / 2
  return loss
calc_kl_loss = compute_kl_loss


def dice_coef(y_pred, y_true, smooth=1, ignore_background=True):
  loss = None
  #ic(y_pred, y_true, y_pred.shape, y_true.shape)
  start = 0 if not ignore_background else 1
  for i in range(start, y_pred.shape[-1]):
    intersection = (y_true[:, :, i] * y_pred[:, :, i]).sum(1)
    union = y_true[:, :, i].sum(1) + y_pred[:, :, i].sum(1) 
    loss_ = (2. * intersection + smooth) / (union + smooth)
    if i == start:
      loss = loss_
    else:
      loss += loss_
  # (bs)
  loss /= y_pred.shape[-1]
  return loss

def dice_loss(y_pred, y_true, smooth=1, ignore_background=True):
  return 1 - dice_coef(y_pred, y_true, smooth=smooth, ignore_background=ignore_background)
calc_dice_loss = dice_loss

def dice_coef2(y_pred, y_true, smooth=1, ignore_background=True):
  loss = None
  #ic(y_pred, y_true, y_pred.shape, y_true.shape)
  start = 0 if not ignore_background else 1
  y_true = y_true.view(-1, y_true.shape[-1])
  y_pred = y_pred.view(-1, y_true.shape[-1])
  for i in range(start, y_pred.shape[-1]):
    intersection = (y_true[:, i] * y_pred[:, i]).sum(0)
    union = y_true[:, i].sum(0) + y_pred[:, i].sum(0) 
    loss_ = (2. * intersection + smooth) / (union + smooth)
    if i == start:
      loss = loss_
    else:
      loss += loss_
  loss /= y_pred.shape[-1]
  return loss

def dice_loss2(y_pred, y_true, smooth=1, ignore_background=True):
  return 1 - dice_coef2(y_pred, y_true, smooth=smooth, ignore_background=ignore_background)
calc_dice_loss2 = dice_loss2

class DiceLoss(nn.Module):
    
  def __init__(self, smooth=1, ignore_background=True, reduction='mean'):
    self.smooth = smooth
    self.ignore_backgound = True
    self.reduction = reduction

  def forward(self, y_pred, y_true):
    dice = 1 - dice_coef(y_pred,
                         y_true,
                         smooth=self.smooth,
                         ignore_background=self.ignore_backgound)
    return dice

# https://blog.csdn.net/weixin_44179676/article/details/109704502
# for multi label
def focal_loss(y_pred, y_true, weight=None, alpha=0.25, gamma=2):
  sigmoid_p = nn.Sigmoid()(y_pred)
  zeros = torch.zeros_like(sigmoid_p)
  pos_p_sub = torch.where(y_true > zeros, y_true - sigmoid_p, zeros)
  neg_p_sub = torch.where(y_true > zeros, zeros, sigmoid_p)
  per_entry_cross_ent = -alpha * (pos_p_sub ** gamma) * torch.log(torch.clamp(sigmoid_p, 1e-8, 1.0)) - (1 - alpha) * (neg_p_sub ** gamma) * torch.log(torch.clamp(1.0 - sigmoid_p, 1e-8, 1.0))
  return per_entry_cross_ent

class MultiLabelFocalLoss(nn.Module):
  
  def __init__(self, reduction='mean', gamma=2, weight=None, alpha=0.25):
    self.reduction = reduction
    self.gamma = gamma
    self.weight = weight
    self.alpha = alpha
  
  def forward(self, y_pred, y_true):
    floss = focal_loss(y_pred, y_true, weight=self.weight, alpha=self.alpha, gamma=self.gamma)
    # ic(floss.shape)
    if self.reduction == 'mean':
      return floss.mean()
    return floss

class FocalLoss(nn.Module):
  """Multi-class Focal loss implementation"""

  def __init__(self, gamma=2, weight=None, reduction='mean', ignore_index=-100):
    super(FocalLoss, self).__init__()
    self.gamma = gamma
    self.weight = weight
    self.ignore_index = ignore_index
    self.reduction = reduction

  def forward(self, y_pred, y_true):
    """
        y_pred: [N, C]
        y_true: [N, ]
    """
    log_pt = torch.log_softmax(y_pred, dim=1)
    pt = torch.exp(log_pt)
    log_pt = (1 - pt)**self.gamma * log_pt
    loss = nn.functional.nll_loss(log_pt,
                                  y_true,
                                  self.weight,
                                  reduction=self.reduction,
                                  ignore_index=self.ignore_index)
    return loss


class MultiLabelCircleLoss(nn.Module):

  def __init__(self, reduction="mean", inf=1e12):
    """CircleLoss of MultiLabel, 多个目标类的多标签分类场景，希望“每个目标类得分都不小于每个非目标类的得分”
        多标签分类的交叉熵(softmax+crossentropy推广, N选K问题), LSE函数的梯度恰好是softmax函数
        让同类相似度与非同类相似度之间拉开一定的margin。
          - 使同类相似度比最大的非同类相似度更大。
          - 使最小的同类相似度比最大的非同类相似度更大。
          - 所有同类相似度都比所有非同类相似度更大。
        urls: [将“softmax+交叉熵”推广到多标签分类问题](https://spaces.ac.cn/archives/7359)
        args:
            reduction: str, Specifies the reduction to apply to the output, 输出形式. 
                            eg.``'none'`` | ``'mean'`` | ``'sum'``. ``'none'``
            inf: float, Minimum of maths, 无穷大.  eg. 1e12
        returns:
            Tensor of loss.
        examples:
            >>> label, logits = [[1, 1, 1, 1], [0, 0, 0, 1]], [[0, 1, 1, 0], [1, 0, 0, 1],]
            >>> label, logits = torch.tensor(label).float(), torch.tensor(logits).float()
            >>> loss = MultiLabelCircleLoss()(logits, label)
        """
    super(MultiLabelCircleLoss, self).__init__()
    self.reduction = reduction
    self.inf = inf  # 无穷大

  def forward(self, logits, labels):
    logits = (1 - 2 * labels) * logits  # <3, 4>
    logits_neg = logits - labels * self.inf  # <3, 4>
    logits_pos = logits - (1 - labels) * self.inf  # <3, 4>
    zeros = torch.zeros_like(logits[..., :1])  # <3, 1>
    logits_neg = torch.cat([logits_neg, zeros], dim=-1)  # <3, 5>
    logits_pos = torch.cat([logits_pos, zeros], dim=-1)  # <3, 5>
    neg_loss = torch.logsumexp(logits_neg, dim=-1)  # <3, >
    pos_loss = torch.logsumexp(logits_pos, dim=-1)  # <3, >
    loss = neg_loss + pos_loss
    if "mean" == self.reduction:
      loss = loss.mean()
    else:
      loss = loss.mean(1)
    return loss

