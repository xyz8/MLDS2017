if [ "${1}" == "S2S" ]; then
  git clone https://gitlab.com/Daikon-Sun/MLDS_hw4_S2S_model.git
  mkdir works
  mkdir works/data
  mkdir works/data/train
  mkdir works/data/test
  mkdir works/movie_subtitles
  mkdir works/movie_subtitles/nn_models
  cp MLDS_hw4_S2S_model/* works/movie_subtitles/nn_models

  mv $2 works/movie_subtitles/data/test/test_set.txt
  python3 main.py --mode test --model_name movie_subtitles --size 512
  mv works/movie_subtitles/results/results_4_512_100000.txt $3
else
  mkdir -p Adversial_RL/gen_data/checkpoints
  git clone https://Daikon-Sun@gitlab.com/Daikon-Sun/MLDS_hw4_RL_model.git
  mv MLDS_hw4_RL_model Adversial_RL/gen_data
  python3 Adversial_RL/al_neural_dialogue_train.py ${2} ${3}
fi