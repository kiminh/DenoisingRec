import os
import time
import argparse
import numpy as np
import random

import torch
import torch.nn as nn
import torch.optim as optim
import torch.utils.data as data
import torch.backends.cudnn as cudnn
# from tensorboardX import SummaryWriter

import model
import config
import evaluate
import data_utils
from loss import lossFunction

parser = argparse.ArgumentParser()
# added for co-teaching

parser.add_argument('--alpha', 
	type = int, 
	default = 0.45, 
	help='how many epochs for linear drop rate {5, 10, 15}')
parser.add_argument('--exponent', 
	type = float, 
	default = 1, 
	help='exponent of the forget rate {0.5, 1, 2}')
parser.add_argument("--lr", 
	type=float, 
	default=0.001, 
	help="learning rate")
parser.add_argument("--dropout", 
	type=float,
	default=0.0,  
	help="dropout rate")
parser.add_argument("--batch_size", 
	type=int, 
	default=1024, 
	help="batch size for training")
parser.add_argument("--epochs", 
	type=int,
	default=10,
	help="training epoches")
parser.add_argument("--eval_freq", 
	type=int,
	default=2000,
	help="the freq of eval")
parser.add_argument("--top_k", 
	type=list, 
	default=[3, 20, 50, 100],
	help="compute metrics@top_k")
parser.add_argument("--factor_num", 
	type=int,
	default=32, 
	help="predictive factors numbers in the model")
parser.add_argument("--num_layers", 
	type=int,
	default=3, 
	help="number of layers in MLP model")
parser.add_argument("--num_ng", 
	type=int,
	default=1, 
	help="sample negative items for training")
parser.add_argument("--out", 
	default=True,
	help="save model or not")
parser.add_argument("--gpu", 
	type=str,
	default="1",
	help="gpu card ID")
args = parser.parse_args()

os.environ["CUDA_VISIBLE_DEVICES"] = args.gpu
cudnn.benchmark = True

torch.manual_seed(2019) # cpu
torch.cuda.manual_seed(2019) #gpu
np.random.seed(2019) #numpy
random.seed(2019) #random and transforms
torch.backends.cudnn.deterministic=True # cudnn

def worker_init_fn(worker_id):
    np.random.seed(2019 + worker_id)


print("arguments: %s " %(args))
print("config model", config.model)
print("config path", config.main_path)
print("config dataset", config.dataset)

############################## PREPARE DATASET ##########################
train_data, valid_data, test_data_pos, user_pos, user_num ,item_num, train_mat, train_data_noisy = data_utils.load_all(config.dataset)

# construct the train and test datasets
train_dataset = data_utils.NCFData(
		train_data, item_num, train_mat, args.num_ng, 0, train_data_noisy)
valid_dataset = data_utils.NCFData(
		valid_data, item_num, train_mat, args.num_ng, 1)

train_loader = data.DataLoader(train_dataset,
		batch_size=args.batch_size, shuffle=True, num_workers=4, pin_memory=True, worker_init_fn=worker_init_fn)
valid_loader = data.DataLoader(valid_dataset,
		batch_size=args.batch_size, shuffle=True, num_workers=4, pin_memory=True, worker_init_fn=worker_init_fn)

print("data loaded! user_num:{}, item_num:{} train_data_len:{} test_user_num:{}".format(user_num, item_num, len(train_data), len(test_data_pos)))

########################### CREATE MODEL #################################
if config.model == 'NeuMF-pre':
	assert os.path.exists(config.GMF_model_path), 'lack of GMF model'
	assert os.path.exists(config.MLP_model_path), 'lack of MLP model'
	GMF_model = torch.load(config.GMF_model_path)
	MLP_model = torch.load(config.MLP_model_path)
else:
	GMF_model = None
	MLP_model = None

model = model.NCF(user_num, item_num, args.factor_num, args.num_layers, 
						args.dropout, config.model, GMF_model, MLP_model)

model.cuda()
loss_function = nn.BCEWithLogitsLoss()

if config.model == 'NeuMF-pre':
	optimizer = optim.SGD(model.parameters(), lr=args.lr)
else:
	optimizer = optim.Adam(model.parameters(), lr=args.lr)

# writer = SummaryWriter() # for visualization

# define drop rate schedule
# def select_rate_schedule(iteration):

# 	forget_rate = np.linspace(0, args.forget_rate**args.exponent, args.num_gradual)
# 	if iteration < args.num_gradual:
# 		return forget_rate[iteration]
# 	else:
# 		return args.forget_rate

# # define flip rate schedule
# def flip_rate_schedule(iteration):

# 	flip_rate = np.linspace(0, args.flip_rate**args.exponent, args.flip_num_gradual)
# 	if iteration < args.flip_num_gradual:
# 		return flip_rate[iteration]
# 	else:
# 		return args.flip_rate

# print("rate_schedule", rate_schedule)

########################### Eval #####################################

def eval(model, valid_loader, best_loss, count):
	
	model.eval()
	epoch_loss = 0
	valid_loader.dataset.ng_sample()
	for user, item, label, noisy_or_not in valid_loader:
		user = user.cuda()
		item = item.cuda()
		label = label.float().cuda()

		prediction = model(user, item)
		loss = lossFunction(prediction, label, args.alpha)
		epoch_loss += loss.detach()
	print("################### EVAL ######################")
	print("Eval loss:{}".format(epoch_loss))
	if epoch_loss < best_loss:
		best_loss = epoch_loss
		if args.out:
			if not os.path.exists(config.model_path):
				os.mkdir(config.model_path)
			torch.save(model, '{}{}_{}.pth'.format(config.model_path, config.model, args.alpha))
		print("### Saved model... Best validation loss:{}".format(epoch_loss))
	return best_loss

########################### Test #####################################

def test(model, test_data_pos, user_pos):
	top_k = args.top_k
	model.eval()
	precision, recall, NDCG, MRR = evaluate.test_all_users(model, 2048, item_num, test_data_pos, user_pos, top_k)

	print("################### TEST ######################")
	print("Precision {:.4f}-{:.4f}-{:.4f}-{:.4f}".format(precision[0], precision[1], precision[2], precision[3]))
	print("recall {:.4f}-{:.4f}-{:.4f}-{:.4f}".format(recall[0], recall[1], recall[2], recall[3]))
	print("NDCG {:.4f}-{:.4f}-{:.4f}-{:.4f}".format(NDCG[0], NDCG[1], NDCG[2], NDCG[3]))
	print("MRR {:.4f}-{:.4f}-{:.4f}-{:.4f}".format(MRR[0], MRR[1], MRR[2], MRR[3]))

########################### TRAINING #####################################
count, best_hr = 0, 0
best_loss = 1e9

for epoch in range(args.epochs):
	model.train() # Enable dropout (if have).

	start_time = time.time()
	train_loader.dataset.ng_sample()

	for user, item, label, noisy_or_not in train_loader:
		user = user.cuda()
		item = item.cuda()
		label = label.float().cuda()

		model.zero_grad()
		prediction = model(user, item)
		loss = lossFunction(prediction, label, args.alpha)
		loss.backward()
		optimizer.step()

		if count % args.eval_freq == 0 and count != 0:

			print("epoch: {}, iter: {}, loss:{}".format(epoch, count, loss))
			# if count % 10000==0:
			best_loss = eval(model, valid_loader, best_loss, count)
			# test(model, test_data_pos, user_pos)
			model.train()

		count += 1
# print("############################## Training End. ##############################")
# test(model, test_data_pos, user_pos)
print("############################## Training End. ##############################")
test_model = torch.load('{}{}_{}.pth'.format(config.model_path, config.model, args.alpha))
test_model.cuda()
test(test_model, test_data_pos, user_pos)
