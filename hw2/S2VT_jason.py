import matplotlib.pyplot as plt
import numpy as np
import tensorflow as tf
import argparse
import os
import sys
import copy
import json
import tensorflow.contrib.seq2seq as seq2seq
from tensorflow.contrib.layers import safe_embedding_lookup_sparse as embedding_lookup_unique
from tensorflow.contrib.rnn import LSTMCell, LSTMStateTuple, GRUCell

default_rnn_cell_type         = 1    # 0: BsicRNN, 1: BasicLSTM, 2: FullLSTM, 3: GRU
default_video_dimension       = 4096 # dimension of each frame
default_video_frame_num       = 80   # each video has fixed 80 frames
default_vocab_size            = 6089
default_max_caption_length    = 20
default_embedding_dimension   = 500  # embedding dimension for video and vocab
default_hidden_units          = 1000 # according to paper
default_batch_size            = 145
default_layer_number          = 1
default_max_gradient_norm     = 10
default_dropout_keep_prob     = 0.5    # for dropout layer
default_init_scale            = 0.005  # for tensorflow initializer
default_max_epoch             = 10000
default_info_epoch            = 1
default_testing_video_num     = 50     # number of testing videos
default_learning_rate         = 0.0000001
default_learning_rate_decay_factor = 1


default_optimizer_type = 4
default_optimizers = [tf.train.GradientDescentOptimizer, # 0
                      tf.train.AdadeltaOptimizer,        # 1
                      tf.train.AdagradOptimizer,         # 2
                      tf.train.MomentumOptimizer,        # 3
                      tf.train.AdamOptimizer,            # 4
                      tf.train.RMSPropOptimizer]         # 5


# default value for special vocabs
PAD = 0
BOS = 1
EOS = 2
UNK = 3

# define mode parameters
default_training_mode   = 0
default_validating_mode = 1
default_testing_mode    = 2


class S2VT(object):

  def __init__(self, para):

    self._para = para

    def single_cell():
      if para.rnn_cell_type == 0:
        return tf.contrib.rnn.BasicRNNCell(para.hidden_units, activation=tf.tanh)
      elif para.rnn_cell_type == 1:
        return tf.contrib.rnn.BasicLSTMCell(para.hidden_units, state_is_tuple=True)
      elif para.rnn_cell_type == 2:
        return tf.contrib.rnn.LSTMCell(para.hidden_units, use_peepholes=True, state_is_tuple=True)
      elif para.rnn_cell_type == 3:
        return tf.contrib.rnn.GRUCell(para.hidden_units)

    # dropout layer
    if self.is_train() and para.dropout_keep_prob < 1:
      def rnn_cell():
        return tf.contrib.rnn.DropoutWrapper(
          single_cell(), output_keep_prob=para.dropout_keep_prob)
    else:
      def rnn_cell():
        return single_cell()

    # multi-layer within a layer
    if para.layer_number > 1:
      layer_1_cell = tf.contrib.rnn.MultiRNNCell(
        [rnn_cell() for _ in range(para.layer_number)], state_is_tuple=True)
      layer_2_cell = tf.contrib.rnn.MultiRNNCell(
        [rnn_cell() for _ in range(para.layer_number)], state_is_tuple=True)
    else:
      layer_1_cell = rnn_cell()
      layer_2_cell = rnn_cell()

    # get data in batches
    if self.is_train():
      video, caption, video_len, caption_len = self.get_single_example(para)
      videos, captions, video_lens, caption_lens = tf.train.batch([video, caption, video_len, caption_len],
        batch_size=para.batch_size, dynamic_pad=True)
      # sparse tensor cannot be sliced
      caption_lens_reshape = tf.reshape(caption_lens, [-1]) # reshape to 1D
      caption_mask = tf.sequence_mask(caption_lens_reshape, para.max_caption_length, dtype=tf.float32)
      target_captions = tf.sparse_tensor_to_dense(captions)
      target_captions_input  = target_captions[:,  :-1] # start from <BOS>
      target_captions_output = target_captions[:, 1:  ] # end by <EOS>
    else:
      video, video_len = self.get_single_example(para)
      videos, video_lens = tf.train.batch([video, video_len],
        batch_size=para.batch_size, dynamic_pad=True)
    self._val = videos # why?

    # video and word embeddings as well as word decoding
    with tf.variable_scope('word_embedding'):
      word_embedding_w = tf.get_variable('word_embed',
        [para.vocab_size, para.embedding_dimension])

    with tf.variable_scope('video_embedding'):
      video_embedding_w = tf.get_variable('video_embed',
        [para.video_dimension, para.embedding_dimension])

    with tf.variable_scope('word_decoding'):
      word_decoding_w = tf.get_variable('word_decode',
        [para.hidden_units, para.vocab_size])

    # embed videos and captions
    video_flat = tf.reshape(videos, [-1, para.video_dimension])
    embed_video_inputs = tf.matmul(video_flat, video_embedding_w)
    embed_video_inputs = tf.reshape(embed_video_inputs, [para.batch_size, para.video_frame_num, para.embedding_dimension])
    if self.is_train():
      embed_targets    = tf.nn.embedding_lookup(word_embedding_w, target_captions)

    # apply dropout to inputs
    if self.is_train() and para.dropout_keep_prob < 1:
      embed_video_inputs = tf.nn.dropout(embed_video_inputs, para.dropout_keep_prob)

    # Initial state of the LSTM memory.
    #with tf.variable_scope('building_model') as scope:
    #  with tf.variable_scope("layer_1"):
    #    state_1 = layer_1_cell.zero_state(para.batch_size, dtype=tf.float32)
    #  with tf.variable_scope("layer_2"):
    #    state_2 = layer_2_cell.zero_state(para.batch_size, dtype=tf.float32)

    # initialize cost
    cost = tf.constant(0.0)

    # paddings for 1st and 2nd layers
    layer_1_padding = tf.zeros([para.batch_size, para.max_caption_length, para.embedding_dimension])
    layer_2_padding = tf.zeros([para.batch_size, para.video_frame_num, para.embedding_dimension])
    # preparing sequence length
    video_frame_num = tf.constant(para.video_frame_num, dtype=tf.int64,
                                  shape=[para.batch_size, 1])
    max_caption_length = tf.constant(para.max_caption_length - 1, dtype=tf.int64,
                                     shape=[para.batch_size, 1])
    sequence_length = tf.add(video_frame_num, caption_lens)
    total_length = tf.add(video_frame_num, max_caption_length)
    # preparing inputs
    #layer_1_inputs = tf.placeholder(shape=(para.batch_size,
    #                                       para.video_frame_num+para.max_caption_length,
    #                                       para.embedding_dimension),
    #                                dtype=tf.float32)
    #layer_2_inputs = tf.placeholder(shape=(para.batch_size,
    #                                       para.video_frame_num+para.max_caption_length,
    #                                       para.embedding_dimension+para.hidden_units),
    #                                dtype=tf.float32)
    #sequence_length = tf.placeholder(shape=(para.batch_size,), dtype=tf.int64)


    # =================== layer 1 ===================
    layer_1_inputs = tf.concat([embed_video_inputs, layer_1_padding], 1)
    layer_1_inputs_ta = tf.TensorArray(dtype=tf.float32,
                                       size=para.video_frame_num+para.max_caption_length)
    layer_1_inputs_ta = layer_1_inputs_ta.unstack(layer_1_inputs, axis=1)

    def layer_1_loop_fn(time, cell_output, cell_state, loop_state):
      emit_output = cell_output
      if cell_output is None: # time == 0
        next_cell_state = layer_1_cell.zero_state(para.batch_size, dtype=tf.float32)
      else:
        next_cell_state = cell_state
      all_finished = (time >= total_length)
      is_finished = (time >= sequence_length)
      finished = tf.reduce_all(is_finished)
      next_input = tf.cond(
        finished,
        lambda: tf.zeors([para.batch_size, para.embedding_dimension], dtype=tf.float32)
        lambda: layer_1_inputs_ta.read(time))
      next_loop_state = None
      return (all_finished, next_input, next_cell_state,
              emit_output, next_loop_state)
    
    layer_1_outputs_ta, layer_1_final_state, _ = raw_rnn(layer_1_cell, layer_1_loop_fn)
    #layer_1_outputs = layer_1_outputs_ta.stack() # may not be time-major here
    #layer_1_outputs = tf.reshape(layer_1_outputs,
    #                    [para.batch_size,para.video_frame_num+para.max_caption_length, para.hidden_units])


    # =================== layer 2 ===================
    caption_embed = tf.nn.embedding_lookup(word_embedding_w, target_captions_input)
    layer_2_pad_and_embed = tf.concat([layer_2_padding, caption_embed], 1)
    layer_2_inputs = tf.concat([layer_2_pad_and_embed, layer_1_output], 2)
    layer_2_inputs_ta = tf.TensorArray(dtype=tf.float32,
                                       size=para.para.video_frame_num+para.max_caption_length)
    layer_2_inputs_ta = layer_2_inputs_ta.unstack(layer_2_inputs, axis=1)
    if self.is_train():
      def layer_2_loop_fn(time, cell_output, cell_state, loop_state):
        emit_output = cell_output
        if cell_output is None: # time == 0
          next_cell_state = layer_2_cell.zero_state(para.batch_size, dtype=tf.float32)
        else:
          next_cell_state = cell_state
        all_finished = (time >= total_length)
        is_finished = (time >= sequence_length)
        finished = tf.reduce_all(is_finished)
        next_input = tf.cond(
          finished,
          lambda: tf.zeors([para.batch_size, para.embedding_dimension+para.hidden_units], dtype=tf.float32)
          lambda: layer_2_inputs_ta.read(time))
        next_loop_state = None
        return (all_finished, next_input, next_cell_state,
                emit_output, next_loop_state)
    else:
      def layer_2_loop_fn(time, cell_output, cell_state, loop_state):

        def get_next_input():
          output_logit = tf.matmul(layer_2_output, word_decoding_w)
          prediction = tf.argmax(output_logits, axis=1)
          prediction_embed = tf.nn.embedding_lookup(word_embedding_w, prediction)
          next_input = tf.concat(prediction_embed, layer_1_outputs_ta.read(time))
          return next_input

        emit_output = cell_output
        if cell_output is None: # time == 0
          next_cell_state = layer_2_cell.zero_state(para.batch_size, dtype=tf.float32)
        else:
          next_cell_state = cell_state
        all_finished = (time >= total_length)
        start_decoding = (time >= video_frame_num)
        finished = tf.reduce_all(start_decoding)
        next_input = tf.cond(
          start_decoding,
          lambda: get_next_input
          lambda: layer_2_inputs_ta.read(time))
        next_loop_state = None
        return (all_finished, next_input, next_cell_state,
                emit_output, next_loop_state)

    layer_2_outputs_ta, layer_2_final_state, _ = raw_rnn(layer_2_cell, layer_2_loop_fn)
    layer_2_outputs = tf.stack(layer_2_outputs_ta) # may not be time-major here

    if self.is_train():
      layer_2_output_logit = tf.matmul(layer_2_outputs, word_decoding_w)
      layer_2_output_logit = tf.reshape(layer_2_output_logit,
                               [para.batch_size, para.video_frame_num+para.max_caption_length, para.vocab_size])
      layer_2_output_logit = layer_2_output_logit[:, para.video_frame_num:, :]
      self._prob = tf.nn.softmax(layer_2_output_logit)

      loss = sequence_loss(layer_2_output_logit, target_captions_output, caption_mask)
      self._cost = cost = tf.reduce_mean(loss)

      # clip gradient norm
      tvars = tf.trainable_variables()
      grads, _ = tf.clip_by_global_norm(tf.gradients(cost, tvars),
                   para.max_gradient_norm)
      optimizer  = default_optimizers[para.optimizer_type](para.learning_rate)
      self._eval = optimizer.apply_gradients(zip(grads, tvars),
                     global_step=tf.contrib.framework.get_or_create_global_step())
    else:
      layer_2_output_logit = tf.matmul(layer_2_outputs, word_decoding_w)
      max_prob_index = tf.argmax(layer_2_output_logit, 1)[0]
      self._result = max_prob_index

  # ======================== end of __init__ ======================== #

  def is_train(self): return self._para.mode == 0
  def is_valid(self): return self._para.mode == 1
  def  is_test(self): return self._para.mode == 2

  @property
  def cost(self): return self._cost
  @property
  def eval(self): return self._eval
  @property
  def prob(self): return self._prob
  @property
  def val(self):  return self._val

  def get_single_example(self, para):
    if self.is_train():
      file_list_path = 'MLDS_hw2_data/training_data/Training_Data_TFR/training_list.txt'
      filenames = open(file_list_path).read().splitlines()
      files = ['MLDS_hw2_data/training_data/Training_Data_TFR/'+filename for filename in filenames]
      file_queue = tf.train.string_input_producer(files, shuffle=True)
    else:
      file_list_path = 'MLDS_hw2_data/testing_data/Testing_Data_TFR/testing_list.txt'
      filenames = open(file_list_path).read().splitlines()
      files = ['MLDS_hw2_data/testing_data/Testing_Data_TFR/'+filename for filename in filenames]
      file_queue = tf.train.string_input_producer(files, shuffle=False)

    reader = tf.TFRecordReader()
    _, serialized_example = reader.read(file_queue)

    if self.is_train():
      features = tf.parse_single_example(
        serialized_example,
        features={
          'video': tf.FixedLenFeature([para.video_frame_num*para.video_dimension], tf.float32),
          'caption': tf.VarLenFeature(tf.int64),
          'caption_length': tf.FixedLenFeature([1], tf.int64)
        })
      video = tf.reshape(features['video'], [para.video_frame_num, para.video_dimension])
      caption = features['caption']
      caption_length = features['caption_length']
      return video, caption, tf.shape(video)[0], caption_length
    else:
      features = tf.parse_single_example(
        serialized_example,
        features={
          'video': tf.FixedLenFeature([para.video_frame_num*para.video_dimension], tf.float32)
        })
      video = tf.reshape(features['video'], [para.video_frame_num, para.video_dimension])
      return video, tf.shape(video)[0]

def run_epoch(sess, model, args):
  fetches = {}
  if not model.is_test():
    fetches['cost'] = model.cost
    if model.is_train():
      fetches['eval'] = model.eval
    vals = sess.run(fetches)
    return np.exp(vals['cost'])
  else:
    fetches['prob'] = model.prob
    vals = sess.run(fetches)
    prob = vals['prob']
    bests = []
    for i in range(prob.shape[0]):
      ans = []
      for j in range(prob.shape[1]):
        max_id = np.argmax(prob[i, j, :])
        if max_id == EOS:
          break
        ans.append(dct[max_id])
      bests.append(ans)
    return bests

if __name__ == '__main__':
  argparser = argparse.ArgumentParser(description='S2VT encoder and decoder')
  argparser.add_argument('-type', '--rnn_cell_type',
    type=int, default=default_rnn_cell_type,
    help='rnn cell type: 0->BasicRNN, 1->BasicLSTM, 2->FullLSTM, 3->GRU')
  argparser.add_argument('-vd', '--video_dimension',
    type=int, default=default_video_dimension,
    help='video dimension (default:%d)' %default_video_dimension)
  argparser.add_argument('-vfn', '--video_frame_num',
    type=int, default=default_video_frame_num,
    help='video frame numbers (default:%d)' %default_video_frame_num)
  #argparser.add_argument('-vs', '--vocab_size',
  #  type=int, default=default_vocab_size,
  #  help='vocab size (default:%d)' %default_vocab_size)
  argparser.add_argument('-mcl', '--max_caption_length',
    type=int, default=default_max_caption_length,
    help='maximum output caption length (default:%d)' %default_max_caption_length)
  argparser.add_argument('-ed', '--embedding_dimension',
    type=int, default=default_embedding_dimension,
    help='embedding dimension of video and caption (default:%d)' %default_embedding_dimension)
  argparser.add_argument('-hu', '--hidden_units',
    type=int, default=default_hidden_units,
    help='hidden units of rnn cell (default:%d)' %default_hidden_units)
  argparser.add_argument('-bs', '--batch_size',
    type=int, default=default_batch_size,
    help='batch size (default:%d)' %default_batch_size)
  argparser.add_argument('-ln', '--layer_number',
    type=int, default=default_layer_number,
    help='layer number within a layer (default:%d)' %default_layer_number)
  argparser.add_argument('-gn', '--max_gradient_norm',
    type=int, default=default_max_gradient_norm,
    help='maximum gradient norm (default:%d' %default_max_gradient_norm)
  argparser.add_argument('-kp', '--dropout_keep_prob',
    type=int, default=default_dropout_keep_prob,
    help='keep probability of dropout layer (default:%d)' %default_dropout_keep_prob)
  argparser.add_argument('-lr', '--learning_rate',
    type=int, default=default_learning_rate,
    help='learning rate (default:%d' %default_learning_rate)
  argparser.add_argument('-lrdf', '--learning_rate_decay_factor',
    type=int, default=default_learning_rate_decay_factor,
    help='learning rate decay factor (default:%d)' %default_learning_rate_decay_factor)
  argparser.add_argument('-ot', '--optimizer_type',
    type=int, default=default_optimizer_type,
    help='type of optimizer (default:%d)' %default_optimizer_type)
  argparser.add_argument('-is', '--init_scale',
    type=int, default=default_init_scale,
    help='initialization scale for tensorflow initializer (default:%d)' %default_init_scale)
  argparser.add_argument('-me', '--max_epoch',
    type=int, default=default_max_epoch,
    help='maximum training epoch (default:%d' %default_max_epoch)
  argparser.add_argument('-ie', '--info_epoch',
    type=int, default=default_info_epoch,
    help='show training information for each (default:%d) epochs' %default_info_epoch)
  args = argparser.parse_args()


  print('S2VT start...\n')

  print('Loading vocab dictionary...\n')
  vocab_dictionary_path = 'MLDS_hw2_data/training_data/jason_vocab.json'
  with open(vocab_dictionary_path) as vocab_dictionary_json:
    vocab_dictionary = json.load(vocab_dictionary_json)
  args.vocab_size = len(vocab_dictionary)
  print('vocab_size = %d' %args.vocab_size)

  with tf.Graph().as_default():
    initializer = tf.random_uniform_initializer(-args.init_scale, args.init_scale)

    # training model
    with tf.name_scope('train'):
      train_args = copy.deepcopy(args)
      with tf.variable_scope('model', reuse=None, initializer=initializer):
        train_args.mode = default_training_mode
        train_model = S2VT(para=train_args)

    # testing model
    with tf.name_scope('test'):
      test_args = copy.deepcopy(args)
      with tf.variable_scope('model', reuse=True, initializer=initializer):
        test_args.mode = default_testing_mode
        test_args.batch_size = 1
        test_model = S2VT(para=test_args)

    config = tf.ConfigProto()
    config.gpu_options.per_process_gpu_memory_fraction = 1.0
    sv = tf.train.Supervisor(logdir='jason/logs/')
    with sv.managed_session(config=config) as sess:
      # training
      for i in range(1, args.max_epoch + 1):
        train_perplexity = run_epoch(sess, train_model, train_args)
        if i % args.info_epoch == 0:
          print('Epoch #%d  Train Perplexity: %.4f' %(i, train_perplexity))

      # testing
      results = []
      for i in range(default_testing_video_num):
        results.extend(run_epoch(sess, test_model, test_args))
      print(results)

    # compute BLEU score
    filenames = open('MLDS_hw2_data/testing_id.txt', 'r').read().splitlines()
    output = [{"caption": result, "id": filename}
              for result, filename in zip(results, filenames)]
    with open('jason/output.json', 'w') as f:
      json.dump(output, f)
    os.system('python3 bleu_eval.py jason/output.json MLDS_hw2_data/testing_public_label.json')
