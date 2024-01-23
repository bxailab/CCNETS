
'''
    COPYRIGHT (c) 2022. CCNets. All Rights reserved.
'''

import torch
import numpy as np 
import time

from .utils.loader import get_Dataloader, get_testloader, _load_model, _save_model
from .utils.setting import set_general_args, set_dim_args, set_flag_args
from .utils.log import log_train, log_metric
from .utils.print import print_iter, print_lr, print_metric
from .utils.report import get_roc_auc_score, get_classification_report, get_regression_report
from .utils.func import select_seq_batch, convert_to_one_hot_vector

from .comps.supervised_learning_model import SupervisedLearningModel
from sklearn.neural_network import MLPClassifier

from .train.supervised_trainer import SupervisedTrainer
from torch.utils.data import Dataset


from tqdm.notebook import tqdm_notebook

class SupervisedLearningWithCCNets():
    def __init__(self, args, net):
        super(SupervisedLearningWithCCNets, self).__init__()
        set_dim_args(self, args)
        set_flag_args(self, args)
        set_general_args(self, args)

        self.model_names = ["supervised_learning_model"]
        self.models = [SupervisedLearningModel.create(args, net).to(device=self.device)]

        self.trainer = SupervisedTrainer(args, self.models)

        self.supervised_learning_loss_type = args.supervised_learning_loss_type
        self.optimizers = self.trainer.get_optimizers()
        self.schedulers = self.trainer.get_schedulers()

    def save_models(self, model_path = None):
        model_path = self.model_path if model_path is None else model_path
        for info in zip(self.model_names, self.models, self.optimizers, self.schedulers):
            _names, _model, _optimizers, _schedulers = info
            _save_model(model_path, _names, _model, _optimizers, _schedulers)

    def load_models(self, model_path = None):
        model_path = self.model_path if model_path is None else model_path
        for info in zip(self.model_names, self.models, self.optimizers, self.schedulers):
            _names, _model, _optimizers, _schedulers = info
            _load_model(model_path, _names, _model, _optimizers, _schedulers)
        return self.models
    def init_batch(self, source_batch, target_batch):
        if self.use_one_hot:
            target_batch = convert_to_one_hot_vector(target_batch, self.label_size)

        if self.seq_len > 0:
            source_batch, target_batch = select_seq_batch(source_batch, target_batch, self.seq_len)

        if self.seq_len == 0 and len(target_batch.shape) == 1:
            target_batch.unsqueeze_(-1)                
        elif self.seq_len > 0 and len(target_batch.shape) == 2:
            target_batch.unsqueeze_(-1)                

        source_batch = source_batch.float().to(self.device)
        target_batch = target_batch.float().to(self.device)
        return source_batch, target_batch 

    def test(self, testset):
        self.trainer.set_train(train = False)
        batch_size = int(min(len(testset), self.batch_size))
        dataloader = get_testloader(testset, batch_size)
        array_labels, array_preds = None, None 
        metrics = None
        self.test_iters =0;self.test_cnt_checkpoints=0;self.test_sum_losses=None
        for i, (source_batch, target_batch) in enumerate(dataloader):
                
            source_batch, target_batch = self.init_batch(source_batch, target_batch)
            inferred_y = self.models[0].infer(source_batch)
            
            if self.seq_len > 0:
                inferred_y = inferred_y[:,-1,:]
                target_batch = target_batch[:,-1,:]

            if array_preds is None:
                array_preds = inferred_y.cpu()
                array_labels = target_batch.cpu()
            else:
                array_preds = torch.cat([array_preds, inferred_y.cpu()], dim = 0)
                array_labels = torch.cat([array_labels, target_batch.cpu()], dim = 0)
            losses = self.trainer.test_models(source_batch, target_batch)
            self.test_sum_losses = (self.test_sum_losses + losses) if self.test_sum_losses is not None else losses

        if self.label_type == "UC":
            metrics = get_classification_report(array_preds, array_labels, self.label_size)
        elif self.label_type == "C":
            metrics = get_roc_auc_score(array_preds, array_labels)
        elif self.label_type == "R":
            metrics = get_regression_report(array_preds, array_labels)
        return metrics

    def train(self, trainset:Dataset, testset:Dataset, ccnets, data_recreate_type):
        self.model_name = "supervised_learning_model_" + data_recreate_type
        make_array_x = None; make_array_labels = None
        self.init_training_vars()
        batch_size = int(min(len(trainset), self.batch_size))


        dataloader = get_Dataloader(trainset, batch_size, self.use_shuffle)

        for i, (source_batch, target_batch) in enumerate(dataloader):
            if len(source_batch) != batch_size:
                continue

            x, y = self.init_batch(source_batch, target_batch)
            
            if data_recreate_type != "generation_10":
                if data_recreate_type == "generate":
                    x, y = ccnets.generate(x, y)
                elif data_recreate_type == "generate_with_random_label":
                    x, y = ccnets.generate_with_random_label(x, y)
                elif data_recreate_type == "generate_with_random_explain":
                    x, y = ccnets.generate_with_random_explain(x, y)
                elif data_recreate_type == "generate_random":
                    x, y = ccnets.generate_random(x, y)
                elif data_recreate_type == "reconstruct":
                    x, y = ccnets.reconstruct(x, y)
                elif data_recreate_type == "reverse_label":
                    x, y = ccnets.reverse_label(x, y)
                elif data_recreate_type == "balance_label":
                    x, y = ccnets.balance_label(x, y)
                elif data_recreate_type == "sl":
                    pass
                if self.supervised_learning_loss_type == "CrossEntropy":
                    y = torch.argmax(y, dim=1)
                       
                losses = self.trainer.train_models(x, y)
                self.iters += 1; self.cnt_checkpoints += 1
                self.sum_losses = (self.sum_losses + losses) if self.sum_losses is not None else losses
               # if you want to use our supervised network, using below # part
                #if self.should_checkpoint():
                #    self.process_checkpoint(epoch, i, len(dataloader), testset)
        #return
            #############################################################################
            # This part was added after looking at the corresponding code.
            # if you want to use other network, using this part
                if make_array_x is None:
                    make_array_x = x.cpu()
                    make_array_labels = y.cpu()
                else:
                    make_array_x = torch.cat([make_array_x, x.cpu()], dim = 0)
                    make_array_labels = torch.cat([make_array_labels, y.cpu()], dim = 0)
            ##############################################################################  
            # This part was added after looking at the corresponding code.
            elif data_recreate_type == "generation_10":
                for j in range(10):
                    x,y = ccnets.generate(x, y)
                    if make_array_x is None:
                        make_array_x = x.cpu()
                        make_array_labels = y.cpu()
                    else:
                        make_array_x = torch.cat([make_array_x, x.cpu()], dim = 0)
                        make_array_labels = torch.cat([make_array_labels, y.cpu()], dim = 0)
            ###############################################################################
        return make_array_x, make_array_labels

    def init_training_vars(self):
        self.sum_losses = None
        self.iters, self.cnt_checkpoints, self.cnt_print = 0, 0, 0
        self.pvt_time = time.time()
        return 
    
    def should_checkpoint(self):
        return self.cnt_checkpoints >= self.num_checkpoints

    def time_checkpoint(self):
        cur_time = time.time()
        et = cur_time - self.pvt_time
        self.pvt_time = cur_time
        return et
    
    def process_checkpoint(self, epoch, i, len_dataloader, testset):
        if self.use_image:
            self.image_debugger.update_images(self.models)
            self.image_debugger.display_image()
            
        et = self.time_checkpoint()
        mean_losses = self.sum_losses/self.cnt_checkpoints
        print_iter(epoch, self.num_epoch, i, len_dataloader, et)
        print_lr(self.optimizers)

        if self.use_test:
            matric = self.test(testset) 
            print_metric(self.label_type, matric)
            log_metric(self.log, self.label_type, epoch, matric)
            test_mean_losses = self.test_sum_losses/self.test_cnt_checkpoints
            log_test(self.log,epoch,test_mean_losses)
        log_train(self.log, epoch, mean_losses)
        self.log.flush()
        
        save_path = self.model_path if self.cnt_print %2 == 0 else self.temp_path
        self.save_models(model_path = save_path)
        
        self.sum_losses, self.cnt_checkpoints = None, 0
        self.cnt_print += 1