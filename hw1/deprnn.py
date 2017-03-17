#!/usr/bin/python3
import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
import argparse
import sys
import numpy as np
import tensorflow as tf
from collections import Counter
import copy
import csv

#default values
default_wordvec_src = 2
default_hidden_size = 256
default_layer_num = 4
default_rnn_type = 1
default_use_dep = False
default_learning_rate = 0.001
default_init_scale = 0.001
default_max_grad_norm = 25
default_max_epoch = 2
default_keep_prob = 0.3
default_batch_size = 300
default_data_dir = './Training_Data'+str(default_wordvec_src)+'/'
default_train_num = 522
default_epoch_size = 100
default_optimizer = 4
optimizers = [tf.train.GradientDescentOptimizer, tf.train.AdadeltaOptimizer,\
    tf.train.AdagradOptimizer, tf.train.MomentumOptimizer,\
    tf.train.AdamOptimizer, tf.train.RMSPropOptimizer]

#functions for arguments of unsupported types
def t_or_f(arg):
  ua = str(arg).upper()
  if 'TRUE'.startswith(ua):
    return True
  elif 'FALSE'.startswith(ua):
    return False
  else:
    raise argparse.ArgumentTypeError('--use_dep can only be True or False!')
def restricted_float(x):
    x = float(x)
    if x < 0.0 or x > 1.0:
        raise argparse.ArgumentTypeError('%r not in range [0.0, 1.0]'%x)
    return x

#argument parser
parser = argparse.ArgumentParser(description=\
    'Dependency-tree-based rnn for MSR sentence completion challenge.')
parser.add_argument('--wordvec_src', type=int, default=default_wordvec_src, nargs='?',\
    choices=range(0, 7),\
    help='Decide the source of wordvec --> [0:debug-mode], [1:glove.6B.50d],\
    [2:glove.6B.100d], [3:glove.6B.200d], [4:glove.6B.300d], [5:glove.42B],\
    [6:glove.840B]. (default:%d)'%default_wordvec_src)
parser.add_argument('--layer_num', type=int, default=default_layer_num, nargs='?',\
    help='Number of rnn layer. (default:%d)'%default_layer_num)
parser.add_argument('--optimizer', type=int, default=default_layer_num, nargs='?',\
    help='Optimzers --> [0: GradientDescent], [1:Adadelta], [2:Adagrad],\
    [3:Momentum], [4:Adam], [5:RMSProp]. (default:%d)'%default_optimizer)
parser.add_argument('--rnn_type', type=int, default=default_rnn_type, nargs='?',\
    choices=range(0, 4),\
    help='Type of rnn cell --> [0:Basic], [1:basic LSTM], [2:full LSTM], [3:GRU].\
        (default:%d)'%default_rnn_type)
parser.add_argument('--learning_rate', type=float, default=default_learning_rate,\
    nargs='?', help='Value of initial learning rate. (default:%r)'\
    %default_learning_rate)
parser.add_argument('--max_grad_norm', type=float, default=default_max_grad_norm,\
    nargs='?', help='Maximum gradient norm allowed. (default:%r)'\
    %default_max_grad_norm)
parser.add_argument('--init_scale', type=float, default=default_init_scale,\
    nargs='?', help='initialize scale. (default:%r)'%default_init_scale)
parser.add_argument('--use_dep', type=t_or_f, default=default_use_dep, nargs='?',\
    choices=[False, True],\
    help='Use dependency tree or not. (default:%r)'%default_use_dep)
parser.add_argument('--max_epoch', type=int, default=default_max_epoch, nargs='?',\
    help='Maximum epoch to be trained. (default:%d)'%default_max_epoch)
parser.add_argument('--epoch_size', type=int, default=default_epoch_size, nargs='?',\
    help='Iterations in a epoch before resetting. (default:%d)'%default_epoch_size)
parser.add_argument('--train_num', type=int, default=default_train_num, nargs='?',\
    help='Number of files out of the total 522 files to be trained. (default:%d)'\
    %default_train_num)
parser.add_argument('--keep_prob', type=restricted_float,\
    default=default_keep_prob,\
    nargs='?', help='Keeping-Probability for dropout layer. (default:%r)'\
    %default_keep_prob)
parser.add_argument('--batch_size', type=int, default=default_batch_size, nargs='?',\
    help='Mini-batch size while training. (default:%d)'%default_batch_size)
parser.add_argument('--hidden_size', type=int, default=default_hidden_size, nargs='?',\
    help='Dimension of hidden layer. (default:%d)'%default_hidden_size)
parser.add_argument('--data_dir', type=str, default=default_data_dir, nargs='?',\
    help='Directory where the data are placed. (default:%s)'%default_data_dir)

args = parser.parse_args()

#load in pre-trained word embedding and vocabulary list
wordvec = np.load(args.data_dir+'wordvec.npy')
vocab = open(args.data_dir+'vocab.txt', 'r').read().splitlines()
assert( len(vocab) == wordvec.shape[0])

#decide vocab_size
args.vocab_size = wordvec.shape[0]

#load in file list for training and validation
filenames = open(args.data_dir+'file_list.txt', 'r').read().splitlines()
filenames = [ args.data_dir+ff for ff in filenames ]
filenames = [filenames[:default_train_num], filenames[default_train_num:],\
    ['testing_data.tfr']]

#decide embedding dimension
args.embed_dim = [50, 50, 100, 200, 300, 300, 300][args.wordvec_src]


def is_train(mode): return mode == 0
def is_valid(mode): return mode == 1
def is_test(mode): return mode == 2

def get_single_example(para):
  '''get one example from TFRecorder file using tensorflow default queue runner'''
  #f_queue = tf.train.string_input_producer(filenames, num_epochs=None)
  f_queue = tf.train.string_input_producer(filenames[para.mode], num_epochs=None)
  reader = tf.TFRecordReader()

  _, serialized_example = reader.read(f_queue)

  feature = tf.parse_single_example(serialized_example,\
    features={\
      'content': tf.VarLenFeature(tf.int64),\
      'len': tf.FixedLenFeature([1], tf.int64)})
  #feature['len'] = tf.clip_by_value(feature['len'], 0, 100)
  #feature['content'] = tf.sparse_tensor_to_dense(feature['content'])[:100]
  return feature['content'], feature['len'][0]

class DepRNN(object):
  '''dependency-tree based rnn'''

  def __init__(self, para):
    '''build multi-layer rnn graph'''
    if para.rnn_type == 0:#basic rnn
      def unit_cell():
        return tf.contrib.rnn.BasicRNNCell(para.hidden_size, activation=tf.tanh)
    elif para.rnn_type == 1:#basic LSTM
      def unit_cell():
        return tf.contrib.rnn.BasicLSTMCell(para.hidden_size, forget_bias=0.0,\
            state_is_tuple=True)
    elif para.rnn_type == 2:#full LSTM
      def unit_cell():
        return tf.contrib.rnn.LSTMCell(para.hidden_size, forget_bias=0.0,\
            state_is_tuple=True)
    elif para.rnn_type == 3:#GRU
      def unit_cell():
        return tf.contrib.rnn.GRUCell(para.hidden_size, state_is_tuple=True)

    rnn_cell = unit_cell

		#dropout layer
    if is_train(para.mode) and para.keep_prob < 1:
      def rnn_cell():
        return tf.contrib.rnn.DropoutWrapper(\
            unit_cell(), output_keep_prob=para.keep_prob)

    #multi-layer rnn
    cell = tf.contrib.rnn.MultiRNNCell([rnn_cell()] * para.layer_num,\
        state_is_tuple=True)

    #initialize rnn_cell state to zero
    self._initial_state = cell.zero_state(para.batch_size, tf.float32)

    #using pre-trained word embedding
    W_E = tf.Variable(tf.constant(0.0,\
        shape=[para.vocab_size, para.embed_dim]), trainable=False, name='W_E')
    self._embedding = tf.placeholder(tf.float32, [para.vocab_size, para.embed_dim])
    self._embed_init = W_E.assign(self._embedding)

    #feed in data in batches
    one_sent, sq_len = get_single_example(para)
    batch, seq_len = tf.train.batch([one_sent, sq_len],\
        batch_size=para.batch_size, dynamic_pad=True)
    #sparse tensor cannot be sliced
    batch = tf.sparse_tensor_to_dense(batch)

    #seq_len is for dynamic_rnn
    seq_len = tf.to_int32(seq_len)

    #x and y differ by one position
    batch_x = batch[:, :-1]
    batch_y = batch[:, 1:]

    #if testing, need to know the word ids
    if is_test(para.mode): self._target = batch_y

    #word_id to vector
    inputs = tf.nn.embedding_lookup(W_E, batch_x)

    if is_train(para.mode) and para.keep_prob < 1:
      inputs = tf.nn.dropout(inputs, para.keep_prob)

    #use dynamic_rnn to build dynamic-time-step rnn
    outputs, state = tf.nn.dynamic_rnn(cell, inputs,\
        sequence_length=seq_len, dtype=tf.float32)
    output = tf.reshape(tf.concat(outputs, 1), [-1, para.hidden_size])
    with tf.variable_scope('softmax'):
      softmax_w = tf.get_variable('w', [para.hidden_size, para.vocab_size],\
          dtype=tf.float32)
      softmax_b = tf.get_variable('b', [para.vocab_size], dtype=tf.float32)

    logits = tf.matmul(output, softmax_w)+softmax_b

    if is_test(para.mode): self._prob = tf.nn.softmax(logits)

    loss = tf.contrib.legacy_seq2seq.sequence_loss_by_example([logits],\
        [tf.reshape(batch_y, [-1])],\
        [tf.ones([(tf.reduce_max(seq_len)-1)*para.batch_size], dtype=tf.float32)])

    self._cost = cost = tf.reduce_mean(loss)

    #if validation or testing, exit here
    if not is_train(para.mode): return

    #clip global gradient norm
    tvars = tf.trainable_variables()
    grads, _ = tf.clip_by_global_norm(tf.gradients(cost, tvars), para.max_grad_norm)
    optimizer = optimizers[para.optimizer](para.learning_rate)
    self._eval = optimizer.apply_gradients(zip(grads, tvars),\
        global_step=tf.contrib.framework.get_or_create_global_step())

  @property
  def initial_state(self): return self._initial_state
  @property
  def cost(self): return self._cost
  @property
  def eval(self): return self._eval
  @property
  def prob(self): return self._prob
  @property
  def target(self): return self._target

def run_epoch(sess, model, args):
  '''Runs the model on the given data.'''
  costs = 0.0
  iters = 0
  state = sess.run(model.initial_state)

  fetches = {\
      'cost': model.cost,\
  }

  if not is_test(args.mode):
    if is_train(args.mode):
      fetches['eval'] = model.eval
    for i in range(args.epoch_size):
      fd_dct = {}
      for i, (c, h) in enumerate(model.initial_state):
        fd_dct[c] = state[i].c
        fd_dct[h] = state[i].h

      vals = sess.run(fetches, feed_dict=fd_dct)
      cost = vals['cost']
      costs += cost
      iters += 1
    return np.exp(costs/iters)
  else:
    fetches['prob'] = model.prob
    fetches['target'] = model.target
    for i in range(args.epoch_size):
      fd_dct = {}
      for i, (c, h) in enumerate(model.initial_state):
        fd_dct[c] = state[i].c
        fd_dct[h] = state[i].h

      vals = sess.run(fetches, feed_dict=fd_dct)
      #cost = vals['cost']
      prob = vals['prob']
      target = vals['target']

      #shape of choices = 5 x (len(sentence)-1)
      choices = np.array([[prob[j*5, target[k, j]]\
          for j in range(target.shape[1])] for k in range(5)])

    return chr(ord('a')+np.argmax(np.prod(choices, axis=1)))

with tf.Graph().as_default():
  initializer = tf.random_uniform_initializer(-args.init_scale, args.init_scale)

  #mode: 0->train, 1->valid, 2->test
  with tf.name_scope('train'):
    train_args = copy.deepcopy(args)
    with tf.variable_scope('model', reuse=None, initializer=initializer):
      train_args.mode = 0
      train_model = DepRNN(para=train_args)
  '''
  with tf.name_scope('valid'):
    valid_args = copy.deepcopy(args)
    with tf.variable_scope('model', reuse=True, initializer=initializer):
      valid_args.mode = 1
      valid_model = DepRNN(para=valid_args)
  '''
  with tf.name_scope('test'):
    test_args = copy.deepcopy(args)
    with tf.variable_scope('model', reuse=True, initializer=initializer):
      test_args.mode = 2
      test_args.batch_size = 5
      test_args.epoch_size = 1
      test_model = DepRNN(para=test_args)

  sv = tf.train.Supervisor(logdir='./logs/')
  with sv.managed_session() as sess:

    #load in pre-trained word-embedding
    sess.run(train_model._embed_init, feed_dict={train_model._embedding: wordvec})

    for i in range(args.max_epoch):
      train_perplexity = run_epoch(sess, train_model, train_args)
      print('Epoch: %d Train Perplexity: %.4f' % (i + 1, train_perplexity))
      #valid_perplexity = run_epoch(sess, valid_model, valid_args)
      #print('Epoch: %d Valid Perplexity: %.3f' % (i + 1, valid_perplexity))
    with open('submission/basic_lstm2.csv', 'w') as f:
      wrtr = csv.writer(f)
      wrtr.writerow(['id', 'answer'])
      for i in range(1040):
        result = run_epoch(sess, test_model, test_args)
        wrtr.writerow([i+1, result[0]])
