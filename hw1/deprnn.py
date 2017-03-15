from __future__ import division, print_function
import os
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
import argparse
import sys
import numpy as np
import tensorflow as tf
from collections import Counter
import time

#default values
default_wordvec_src = -1
default_vocab_size = 11
default_hidden_size = 2
default_layer_num = 1
default_rnn_type = 1
default_use_dep = False
default_learning_rate = 0.1
default_init_scale = 0.001
default_max_grad_norm = 100
default_max_epoch = 100
default_keep_prob = 1.0
default_batch_size = 2
default_data_mode = 2
default_data_dir = './toy_data/'

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
parser.add_argument('--wordvec_src', type=int, default=default_wordvec_src, nargs='?', \
    choices=range(-1, 7), \
    help='Decide the source of wordvec --> [-1:debug-mode], \
    [0:one-hot], [1:glove.6B.50d], [2:glove.6B.100d], \
    [3:glove.6B.200d], [4:glove.6B.300d], [5:glove.42B], \
    [6:glove.840B]. (default:%d)'%default_wordvec_src)
parser.add_argument('--vocab_size', type=int, default=default_vocab_size, nargs='?', \
    help='The vocabulary size to be trained. (default:%d)'%default_vocab_size)
parser.add_argument('--layer_num', type=int, default=default_layer_num, nargs='?', \
    help='Number of rnn layer.. (default:%d)'%default_layer_num)
parser.add_argument('--rnn_type', type=int, default=default_rnn_type, nargs='?', \
    choices=range(0, 3), \
    help='Type of rnn cell --> [0:Basic], [1:basic LSTM], [2:full LSTM], [3:GRU]. \
        (default:%d)'%default_rnn_type)
parser.add_argument('--learning_rate', type=float, default=default_learning_rate, \
    nargs='?', help='Value of initial learning rate. (default:%r)'\
    %default_learning_rate)
parser.add_argument('--max_grad_norm', type=float, default=default_max_grad_norm, \
    nargs='?', help='Maximum gradient norm allowed. (default:%r)'\
    %default_max_grad_norm)
parser.add_argument('--init_scale', type=float, default=default_init_scale, \
    nargs='?', help='initialize scale. (default:%r)'%default_init_scale)
parser.add_argument('--use_dep', type=t_or_f, default=default_use_dep, nargs='?', \
    choices=[False, True], \
    help='Use dependency tree or not. (default:%r)'%default_use_dep)
parser.add_argument('--max_epoch', type=int, default=default_max_epoch, nargs='?', \
    help='Maximum epoch to be trained. (default:%d)'%default_max_epoch)
parser.add_argument('--keep_prob', type=restricted_float, \
    default=default_keep_prob, \
    nargs='?', help='Keeping-Probability for dropout layer. (default:%r)'\
    %default_keep_prob)
parser.add_argument('--batch_size', type=int, default=default_batch_size, nargs='?', \
    help='Mini-batch size while training. (default:%d)'%default_batch_size)
parser.add_argument('--hidden_size', type=int, default=default_hidden_size, nargs='?', \
    help='Dimension of hidden layer. (default:%d)'%default_hidden_size)
parser.add_argument('--data_mode', type=int, default=default_data_mode, nargs='?', \
    choices=range(1, 3), \
    help='Data mode for preprocessed data --> [1:one file], [2:two files].\
    (default:%d)'%default_data_mode)
parser.add_argument('--data_dir', type=str, default=default_data_dir, nargs='?', \
    help='Directory where the data are placed. (default:%s)'%default_data_dir)

args = parser.parse_args()

#decide embedding dimension and vocabulary size
if args.wordvec_src == -1:
  args.embed_dim = 50
  args.vocab_size = 12
elif args.wordvec_src == 0:
  args.embed_dim = args.vocab_size
elif args.wordvec_src == 1:
  args.embed_dim = 50
  args.vocab_size = 400000
elif args.wordvec_src == 2:
  args.embed_dim = 100
  args.vocab_size = 400000
elif args.wordvec_src == 3:
  args.embed_dim = 200
  args.vocab_size = 400000
elif args.wordvec_src == 4:
  args.embed_dim = 300
  args.vocab_size = 400000
elif args.wordvec_src == 5:
  args.embed_dim = 300
  args.vocab_size = 1917494
elif args.wordvec_src == 6:
  args.embed_dim = 300
  args.vocab_size = 2196017
else: assert(False)

def get_single_example(args):
  filename = args.data_dir+'/data.tfr'
  f_queue = tf.train.string_input_producer([filename], num_epochs=None)
  reader = tf.TFRecordReader()

  _, serialized_example = reader.read(f_queue)

  feature = tf.parse_single_example(serialized_example, \
    features={\
      'train': tf.VarLenFeature(tf.int64), \
      'sqlen': tf.FixedLenFeature([1], tf.int64)})
  return feature['train'], feature['sqlen'][0]

class DepRNN(object):
  def __init__(self, is_training, para):

    #build model
    if para.rnn_type == 0:#basic rnn
      def unit_cell():
        return tf.contrib.rnn.BasicRNNCell(para.hidden_size, activation=tf.tanh)
    elif para.rnn_type == 1:
      def unit_cell():
        return tf.contrib.rnn.BasicLSTMCell(para.hidden_size, forget_bias=0.0, \
            state_is_tuple=True)
    else:#TODO (full LSTM and GRU)
      assert(False)

    rnn_cell = unit_cell

    if is_training and para.keep_prob < 1:#TODO (dropout layer)
      assert(False)

    cell = tf.contrib.rnn.MultiRNNCell([rnn_cell()] * para.layer_num, \
        state_is_tuple=True)
    self._initial_state = cell.zero_state(para.batch_size, tf.float32)

    #using pre-trained word embedding
    W_E = tf.Variable(tf.constant(0.0, \
        shape=[para.vocab_size, para.embed_dim]), \
        trainable=False, name='W_E')
    self._embedding = tf.placeholder(tf.float32, \
        [para.vocab_size, para.embed_dim])
    self._embed_init = W_E.assign(self._embedding)

    one_sent, sq_len = get_single_example(para)
    batch, seq_len = tf.train.batch([one_sent, sq_len], \
        batch_size=para.batch_size, dynamic_pad=True)
    batch = tf.sparse_tensor_to_dense(batch)
    self._seq_len = seq_len = tf.to_int32(seq_len)

    batch_x = batch[:, :-1]
    batch_y = batch[:, 1:]

    inputs = tf.nn.embedding_lookup(W_E, batch_x)

    if is_training and para.keep_prob < 1:#TODO (dropout layer on input)
      assert(False)

    #use dynamic_rnn to build dynamic time-step rnn
    outputs, state = tf.nn.dynamic_rnn(cell, inputs, \
        sequence_length=seq_len, dtype=tf.float32)
    output = tf.reshape(tf.concat(outputs, 1), [-1, para.hidden_size])
    with tf.variable_scope('softmax'):
      softmax_w = tf.get_variable('w', [para.hidden_size, para.vocab_size], \
          dtype=tf.float32)
      softmax_b = tf.get_variable('b', [para.vocab_size], dtype=tf.float32)

    logits = tf.matmul(output, softmax_w)+softmax_b
    loss = tf.contrib.legacy_seq2seq.sequence_loss_by_example([logits], \
        [tf.reshape(batch_y, [-1])], \
        [tf.ones([(tf.reduce_max(seq_len)-1)*para.batch_size], dtype=tf.float32)])

    #self._cost = cost = tf.reduce_sum(loss) / tf.to_float(tf.reduce_sum(seq_len)-para.batch_size)
    self._cost = cost = tf.reduce_mean(loss)
    self._final_state = state

    #if validation or testing, exit here
    if not is_training: return

    tvars = tf.trainable_variables()
    grads, _ = tf.clip_by_global_norm(tf.gradients(cost, tvars), \
                                      para.max_grad_norm)
    optimizer = tf.train.GradientDescentOptimizer(para.learning_rate)
    self._train_op = optimizer.apply_gradients(zip(grads, tvars), \
        global_step=tf.contrib.framework.get_or_create_global_step())

  @property
  def seq_len(self): return self._seq_len
  @property
  def initial_state(self): return self._initial_state
  @property
  def cost(self): return self._cost
  @property
  def final_state(self): return self._final_state
  @property
  def train_op(self): return self._train_op

def run_epoch(sess, model, eval_op=None, verbose=False):
  """Runs the model on the given data."""
  start_time = time.time()
  costs = 0.0
  iters = 0
  state = sess.run(model.initial_state)

  fetches = {
      'cost': model.cost,
      'final_state': model.final_state,
      'seq_len': model.seq_len
  }

  if eval_op is not None:
    fetches["eval_op"] = eval_op

  fd_dct = {}
  for i, (c, h) in enumerate(model.initial_state):
    fd_dct[c] = state[i].c
    fd_dct[h] = state[i].h

  vals = sess.run(fetches, feed_dict=fd_dct)

  cost = vals['cost']
  state = vals['final_state']
  seq_len = vals['seq_len']

  costs += cost
  iters += (sum(seq_len)-len(seq_len))
  #print('iters = %r'%iters)

  #if verbose and step % (model.input.epoch_size // 10) == 10:
  #  print("%.3f perplexity: %.3f speed: %.0f wps" %
  #        (step / model.input.epoch_size, np.exp(costs / iters),
  #         iters * model.input.batch_size / (time.time() - start_time)))

  return np.exp(costs / iters)

with tf.Graph().as_default():
  initializer = tf.random_uniform_initializer(-args.init_scale, args.init_scale)

  with tf.name_scope("Train"):
    with tf.variable_scope('model', reuse=None, initializer=initializer):
      deprnn = DepRNN(is_training=True, para=args)

  sv = tf.train.Supervisor(logdir='./logs/', saver=None)
  with sv.managed_session() as sess:

    #load in pre-trained word-embedding
    wordvec = np.load(args.data_dir+'wordvec.npy')
    sess.run(deprnn._embed_init, feed_dict={deprnn._embedding: wordvec})

    for i in range(args.max_epoch):
      #print("Epoch: %d Learning rate: %.3f" % (i + 1, sess.run(deprnn.lr)))
      train_perplexity = run_epoch(sess, deprnn, eval_op=deprnn.train_op, verbose=True)
      print("Epoch: %d Train Perplexity: %.3f" % (i + 1, train_perplexity))
      #valid_perplexity = run_epoch(session, mvalid)
      #print("Epoch: %d Valid Perplexity: %.3f" % (i + 1, valid_perplexity))
