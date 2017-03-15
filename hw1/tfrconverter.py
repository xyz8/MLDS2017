import argparse
import numpy as np
import tensorflow as tf
from tqdm import tqdm

argparser = argparse.ArgumentParser(description='Parsing given datas into tfr format')
argparser.add_argument('-i', '--file_list',
  type=str, default='training_list',
  help='FILE_LIST is the file storing the file names of training datas.'
       ' (default: %(default)s)')
argparser.add_argument('-o', '--output_dir',
  type=str, default='Training_Data_tfr',
  help='OUTPUT_DIR is the directory where the output files will be '
       'stored in. (default: %(default)s)',)
argparser.add_argument('-v,' '--vocab',
  type=str,
  help='VOCAB is a file which give each word a distinct id')
argparser.set_defaults(comma_split=False)
args = argparser.parse_args()

vocab_table = dict()
vocab_table_idx = 0
with open(args.vocab,'r') as vocab:
  words = [v.strip('\n') for v in vocab.readlines()]
  for w in words:
    vocab_table[w] = vocab_table_idx
    vocab_table_idx = vocab_table_idx + 1

with open(args.file_list,'r') as file_list:
  for file_name in file_list:
    with open(file_name[:-1],'r',encoding="utf-8",errors='ignore') as f:
      sys.stderr.write('start converting file ' + file_name[:-1] + '\n')
      writer = tf.python_io.TFRecordWriter(args.output_dir+"/"+file_name[:-5])
      content = [x.strip('\n') for x in f.readlines()]
      for idx in tqdm(len(content)):
        words = content.split()
        words_id = []
        for w in words:
          if w in vocab_table:
            words_id.append(vocab_table[w])
          else:
            words_id.append(0)
        example = tf.train.Example(
          features=tf.train.Features(
            feature={
              content: tf.train.Feature(
                int64_list=tf.train.Int64List(value=words_id)),
              'len': tf.train.Feature(
                int64_list=tf.train.Int64List(value=[len(words_id)]))
        }))
        serialized = example.SerializeToString()
        writer.write(serialized)



      with open(args.output_dir+"/"+file_name[:-5]+".txt",'w') as of:
        sys.stderr.write('finished parsing file ' + file_name[:-1] + '\n')
