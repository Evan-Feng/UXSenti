import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from utils.module import GradReverse
from utils.bdi import compute_nn_accuracy


class CrossLingualLanguageModelTrainer(object):

    def __init__(self, src_lm, trg_lm, discriminator, lm_optimizer, dis_optimizer, criterion, bptt, alpha, beta, lambd, lm_clip, dis_clip, lexicon, lexicon_size):
        self.src_lm = src_lm
        self.trg_lm = trg_lm
        self.discriminator = discriminator
        self.lm_optimizer = lm_optimizer
        self.dis_optimizer = dis_optimizer
        self.criterion = criterion
        self.bptt = bptt
        self.alpha = alpha
        self.beta = beta
        self.lambd = lambd
        self.lm_clip = lm_clip
        self.dis_clip = dis_clip
        self.lexicon = lexicon
        self.lexicon_size = lexicon_size
        self.rev_grad = GradReverse(self.lambd)
        self.is_cuda = next(self.src_lm.parameters()).is_cuda

    def step(self, src_x, src_y, trg_x, trg_y):
        """
        src_x: torch.tensor of shape (batch_size, src_bptt)
        src_y: torch.tensor of shape (batch_size, trg_bptt)
        trg_x: torch.tensor of shape (batch_size, src_bptt)
        trg_x: torch.tensor of shape (batch_size, trg_bptt)
        """
        self.train()
        bs, bptt = src_x.size()

        self.lm_optimizer.zero_grad()
        self.dis_optimizer.zero_grad()

        lr0 = self.lm_optimizer.param_groups[0]['lr']
        self.lm_optimizer.param_groups[0]['lr'] = lr0 * bptt / self.bptt

        src_out, src_h, src_dropped_h = self.src_lm(src_x)
        trg_out, trg_h, trg_dropped_h = self.trg_lm(trg_x)
        src_loss = src_raw_loss = self.criterion(F.log_softmax(src_out.view(-1, src_out.size(-1)), -1), src_y)
        trg_loss = trg_raw_loss = self.criterion(F.log_softmax(trg_out.view(-1, trg_out.size(-1)), -1), trg_y)
        src_loss = src_loss + sum(self.alpha * h.pow(2).mean() for h in src_dropped_h[-1:])
        src_loss = src_loss + sum(self.beta * (h[:, 1:] - h[:, :-1]).pow(2).mean() for h in src_h[-1:])
        trg_loss = trg_loss + sum(self.alpha * h.pow(2).mean() for h in trg_dropped_h[-1:])
        trg_loss = trg_loss + sum(self.beta * (h[:, 1:] - h[:, :-1]).pow(2).mean() for h in trg_h[-1:])

        src_pooled = torch.cat([h.mean(1) for h in src_h], -1)
        trg_pooled = torch.cat([h.mean(1) for h in trg_h], -1)
        dis_x = torch.cat([src_pooled, trg_pooled], 0)
        dix_x = self.rev_grad(dis_x)
        dis_y = torch.cat((torch.zeros(bs, dtype=torch.int64), torch.ones(bs, dtype=torch.int64)), -1)
        if self.is_cuda:
            dis_y = dis_y.cuda()
        dis_out = self.discriminator(dis_x)
        dis_loss = self.criterion(F.log_softmax(dis_out, -1), dis_y)

        loss = src_loss + trg_loss + dis_loss
        loss.backward()

        torch.nn.utils.clip_grad_norm_(self.src_lm.parameters(), self.lm_clip)
        torch.nn.utils.clip_grad_norm_(self.trg_lm.parameters(), self.lm_clip)

        self.lm_optimizer.step()
        self.dis_optimizer.step()

        for x in self.discriminator.parameters():
            x.data.clamp_(-self.dis_clip, self.dis_clip)

        self.lm_optimizer.param_groups[0]['lr'] = lr0

        return loss, src_raw_loss, trg_raw_loss, dis_loss

    def evaluate(self, src_val, trg_val):
        """
        src_val: torch.tensor of shape (src_val_len, batch_size)
        trg_val: torch.tensor of shape (trg_val_len, batch_size)
        """
        src_l, bs = src_val.size()
        trg_l, bs = trg_val.size()
        length = min(src_l, trg_l)
        bptt = self.bptt
        length = (length // bptt) * bptt

        self.eval()
        self.reset()

        total_losses = np.zeros(4)

        for i in range(0, length, bptt):
            sx = src_val[i:i + bptt].t()
            sy = src_val[i + 1:i + 1 + bptt].t().contiguous().view(-1)
            tx = trg_val[i:i + bptt].t()
            ty = trg_val[i + 1:i + 1 + bptt].t().contiguous().view(-1)
            src_out, src_h, src_dropped_h = self.src_lm(sx)
            trg_out, trg_h, trg_dropped_h = self.trg_lm(tx)
            src_loss = src_raw_loss = self.criterion(F.log_softmax(src_out.view(-1, src_out.size(-1)), -1), sy)
            trg_loss = trg_raw_loss = self.criterion(F.log_softmax(trg_out.view(-1, trg_out.size(-1)), -1), ty)
            src_loss = src_loss + sum(self.alpha * h.pow(2).mean() for h in src_dropped_h[-1:])
            src_loss = src_loss + sum(self.beta * (h[:, 1:] - h[:, :-1]).pow(2).mean() for h in src_h[-1:])
            trg_loss = trg_loss + sum(self.alpha * h.pow(2).mean() for h in trg_dropped_h[-1:])
            trg_loss = trg_loss + sum(self.beta * (h[:, 1:] - h[:, :-1]).pow(2).mean() for h in trg_h[-1:])

            src_pooled = torch.cat([h.mean(1) for h in src_h], -1)
            trg_pooled = torch.cat([h.mean(1) for h in trg_h], -1)

            dx = torch.cat([src_pooled, trg_pooled], 0)
            dx = self.rev_grad(dx)
            dy = torch.cat((torch.zeros(bs, dtype=torch.int64), torch.ones(bs, dtype=torch.int64)), -1)
            if self.is_cuda:
                dy = dy.cuda()

            dis_out = self.discriminator(dx)
            dis_loss = self.criterion(F.log_softmax(dis_out, -1), dy)
            loss = src_loss + trg_loss + dis_loss

            total_losses += np.array([loss, src_raw_loss, trg_raw_loss, dis_loss])

        return total_losses / (length / bptt)

    def evaluate_bdi(self, batch_size=5000):
        x_src = list(self.src_lm.children())[0].encoder.weight.data.cpu().numpy()
        x_trg = list(self.trg_lm.children())[0].encoder.weight.data.cpu().numpy()
        acc = compute_nn_accuracy(x_src, x_trg, self.lexicon,
                                  batch_size=5000, lexicon_size=self.lexicon_size)
        return acc

    def reset(self):
        self.src_lm.reset()
        self.trg_lm.reset()
        return self

    def reset_src(self):
        self.src_lm.reset()
        return self

    def reset_trg(self):
        self.trg_lm.reset()
        return self

    def train(self):
        self.src_lm.train()
        self.trg_lm.train()
        self.discriminator.train()
        return self

    def eval(self):
        self.src_lm.eval()
        self.trg_lm.eval()
        self.discriminator.eval()
        return self

    def cuda(self):
        self.src_lm.cuda()
        self.trg_lm.cuda()
        self.discriminator.cuda()
        self.is_cuda = True
        return self

    def cpu(self):
        self.src_lm.cpu()
        self.trg_lm.cpu()
        self.discriminator.cpu()
        self.is_cuda = False
        return self